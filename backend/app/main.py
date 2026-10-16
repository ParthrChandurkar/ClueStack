"""FastAPI application factory for ClueStack.

The factory pattern keeps tests isolated and makes production runtime wiring
explicit for deployment platforms.
"""

import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from app import __version__
from app.api import build_router
from app.config import Settings, get_settings
from app.repository import (
    AuditLogRepository,
    DeploymentRepository,
    FileAuditLogRepository,
    FileDeploymentRepository,
    PostgresAuditLogRepository,
    PostgresDeploymentRepository,
    build_project_repository,
    build_report_repository,
)
from app.schemas import DeploymentRequest
from app.services.deployments import DeploymentService
from app.services.observability import AlertmanagerClient, LokiClient, PrometheusClient
from app.services.projects import ProjectService
from app.services.reports import ReportService

HTTP_REQUEST_COUNTER = Counter(
    "http_requests_total",
    "HTTP requests served by a ClueStack-compatible service.",
    ["service", "method", "path", "status"],
)
HTTP_REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency for a ClueStack-compatible service.",
    ["service", "method", "path"],
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""

    active_settings = settings or get_settings()
    app = FastAPI(
        title=active_settings.app_name,
        version=__version__,
        description="Local Application Deployment & Observability Platform.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    repository, audit_repository = _build_repositories(active_settings)
    project_repository = build_project_repository(active_settings.database_url, active_settings.projects_file)
    report_repository = build_report_repository(active_settings.database_url, active_settings.reports_file)
    project_service = ProjectService(project_repository)
    report_service = ReportService(report_repository)
    app.state.settings = active_settings
    app.state.project_service = project_service
    app.state.report_service = report_service
    app.state.deployment_service = DeploymentService(
        active_settings,
        repository,
        audit_repository,
        project_service,
        report_service,
    )
    app.state.prometheus_client = PrometheusClient(active_settings)
    app.state.loki_client = LokiClient(active_settings)
    app.state.alertmanager_client = AlertmanagerClient(active_settings)

    if active_settings.seed_demo_data and not repository.list():
        _seed_demo_deployments(app.state.deployment_service)

    app.include_router(build_router())

    @app.middleware("http")
    async def observe_http_requests(request, call_next):
        """Record request count and latency for Prometheus dashboards."""

        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        path = request.url.path
        status_code = str(response.status_code)
        HTTP_REQUEST_COUNTER.labels(
            service=active_settings.service_name,
            method=request.method,
            path=path,
            status=status_code,
        ).inc()
        HTTP_REQUEST_LATENCY.labels(
            service=active_settings.service_name,
            method=request.method,
            path=path,
        ).observe(elapsed)
        return response

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        """Return a friendly API discovery message."""

        return {"service": "ClueStack API", "docs": "/docs", "health": "/healthz"}

    @app.get("/metrics", include_in_schema=False)
    @app.get("/internal/metrics", include_in_schema=False)
    async def internal_metrics() -> Response:
        """Expose Prometheus metrics for the ClueStack API itself."""

        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


def _build_repositories(settings: Settings) -> tuple[DeploymentRepository, AuditLogRepository]:
    """Use PostgreSQL when configured, otherwise keep the safe JSON fallback."""

    if settings.database_url:
        return (
            PostgresDeploymentRepository(settings.database_url),
            PostgresAuditLogRepository(settings.database_url),
        )
    return (
        FileDeploymentRepository(settings.state_file),
        FileAuditLogRepository(settings.audit_file),
    )


def _seed_demo_deployments(service: DeploymentService) -> None:
    """Populate demo runtimes with useful, non-sensitive sample services."""

    for payload in (
        DeploymentRequest(
            name="checkout-api",
            image="ghcr.io/infrawatch/checkout-api:2.4.1",
            replicas=3,
            port=8080,
            environment={"ENVIRONMENT": "demo"},
        ),
        DeploymentRequest(
            name="catalog-api",
            image="ghcr.io/infrawatch/catalog-api:1.9.0",
            replicas=2,
            port=8080,
            environment={"ENVIRONMENT": "demo"},
        ),
        DeploymentRequest(
            name="payments-worker",
            image="ghcr.io/infrawatch/payments-worker:3.2.0",
            replicas=2,
            port=9090,
            environment={"ENVIRONMENT": "demo", "QUEUE": "payments"},
        ),
    ):
        service.deploy(payload)


app = create_app()
