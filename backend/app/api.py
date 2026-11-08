"""HTTP routes for the ClueStack REST API.

Routes are intentionally thin and delegate orchestration to services stored on
FastAPI application state.
"""

from fastapi import APIRouter, HTTPException, Query, Request, status
from prometheus_client import Counter, Histogram
from pydantic import ValidationError

from app.schemas import (
    AlertsResponse,
    AuditLogEntry,
    ContainerBuildCreateRequest,
    ContainerBuildEligibilityRequest,
    ContainerBuildEligibilityResult,
    ContainerBuildLogsResponse,
    ContainerBuildRecord,
    DeploymentRecord,
    DeploymentReportRecord,
    DeploymentRequest,
    DeploymentResponse,
    KubernetesClusterOverview,
    KubernetesContextList,
    LocalRepositoryAnalysisRequest,
    LogsResponse,
    ProjectCreate,
    ProjectRecord,
    RepositoryAnalysisRequest,
    RepositoryAnalysisResult,
    RepositoryReadinessRequest,
    RepositoryReadinessResult,
    ServiceMetrics,
    SourceSnapshotCreateRequest,
    SourceSnapshotRecord,
    SourceSnapshotVerification,
    WorkloadHealth,
)
from app.services.container_builds import ContainerBuildError
from app.services.deployments import DeploymentExecutionError
from app.services.kubernetes_clusters import KubernetesIntegrationError
from app.services.local_repository_analysis import LocalRepositoryAnalysisError
from app.services.projects import ProjectConflictError, ProjectModeError, ProjectNotFoundError
from app.services.repository_analysis import RepositoryAnalysisError
from app.services.repository_readiness import RepositoryReadinessError
from app.services.source_snapshots import SourceSnapshotError

DEPLOYMENT_COUNTER = Counter("infrawatch_deployments_total", "Deployment actions accepted by ClueStack")
REQUEST_TIMER = Histogram("infrawatch_api_request_seconds", "ClueStack API route execution time", ["route"])


def build_router() -> APIRouter:
    """Build the API router with all public ClueStack endpoints."""

    router = APIRouter()

    @router.get("/healthz", tags=["system"])
    async def healthz(request: Request) -> dict[str, str | bool]:
        """Return health and an honest summary of active platform capabilities."""

        settings = request.app.state.settings
        return {
            "status": "ok",
            "environment": settings.environment,
            "fastapi": True,
            "kubernetes_execution": settings.execute_kubectl,
            "deployment_mode": "kubernetes" if settings.execute_kubectl else "manifest-simulation",
            "observability_mode": "prometheus-loki" if not settings.allow_mock_observability else "fallback-enabled",
            "persistence_mode": "postgresql" if settings.database_url else "json-file",
        }

    @router.get("/kubernetes/contexts", response_model=KubernetesContextList, tags=["kubernetes"])
    async def kubernetes_contexts(request: Request) -> KubernetesContextList:
        """List safe context names without changing the user's active kubectl context."""

        with REQUEST_TIMER.labels(route="/kubernetes/contexts").time():
            try:
                return request.app.state.kubernetes_cluster_service.list_contexts()
            except KubernetesIntegrationError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail={"code": exc.code, "message": exc.public_message},
                ) from exc

    @router.get("/kubernetes/overview", response_model=KubernetesClusterOverview, tags=["kubernetes"])
    async def kubernetes_overview(
        request: Request,
        context: str = Query(..., min_length=1, max_length=253, pattern=r"^[^\x00-\x1f\x7f]+$"),
    ) -> KubernetesClusterOverview:
        """Return one consolidated read-only overview for an explicitly selected context."""

        with REQUEST_TIMER.labels(route="/kubernetes/overview").time():
            try:
                return request.app.state.kubernetes_cluster_service.overview(context)
            except KubernetesIntegrationError as exc:
                raise HTTPException(
                    status_code=exc.status_code,
                    detail={"code": exc.code, "message": exc.public_message},
                ) from exc

    @router.post(
        "/deploy",
        response_model=DeploymentResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["deployments"],
    )
    async def deploy(payload: DeploymentRequest, request: Request) -> DeploymentResponse:
        """Trigger or update a service deployment."""

        with REQUEST_TIMER.labels(route="/deploy").time():
            try:
                response = request.app.state.deployment_service.deploy(payload)
                DEPLOYMENT_COUNTER.inc()
                return response
            except ProjectNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc
            except ProjectModeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Local deployments require a local project",
                ) from exc
            except DeploymentExecutionError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @router.get("/deployments", tags=["deployments"])
    async def deployments(request: Request):
        """List all deployments known to ClueStack."""

        with REQUEST_TIMER.labels(route="/deployments").time():
            return request.app.state.deployment_service.list_deployments()

    @router.get("/audit-logs", response_model=list[AuditLogEntry], tags=["audit"])
    async def audit_logs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[AuditLogEntry]:
        """List recent operational actions recorded by ClueStack."""

        with REQUEST_TIMER.labels(route="/audit-logs").time():
            return request.app.state.deployment_service.list_audit_logs(limit=limit)

    @router.get("/reports", response_model=list[DeploymentReportRecord], tags=["reports"])
    async def list_reports(
        request: Request,
        project_id: str | None = Query(default=None),
        deployment_id: str | None = Query(default=None),
    ) -> list[DeploymentReportRecord]:
        """List historical deployment reports."""

        with REQUEST_TIMER.labels(route="/reports").time():
            return request.app.state.report_service.list_reports(
                project_id=project_id,
                deployment_id=deployment_id,
            )

    @router.get("/reports/{report_id}", response_model=DeploymentReportRecord, tags=["reports"])
    async def get_report(report_id: str, request: Request) -> DeploymentReportRecord:
        """Return one historical deployment report by ID."""

        with REQUEST_TIMER.labels(route="/reports/{report_id}").time():
            report = request.app.state.report_service.get(report_id)
            if report is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
            return report

    @router.post(
        "/projects",
        response_model=ProjectRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["projects"],
    )
    async def create_project(payload: ProjectCreate, request: Request) -> ProjectRecord:
        """Create a ClueStack project."""

        with REQUEST_TIMER.labels(route="/projects").time():
            try:
                return request.app.state.project_service.create(payload)
            except ProjectConflictError as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Project slug already exists") from exc

    @router.get("/projects", response_model=list[ProjectRecord], tags=["projects"])
    async def list_projects(request: Request) -> list[ProjectRecord]:
        """List ClueStack projects."""

        with REQUEST_TIMER.labels(route="/projects").time():
            return request.app.state.project_service.list_projects()

    @router.get("/projects/default-local", response_model=ProjectRecord, tags=["projects"])
    async def default_local_project(request: Request) -> ProjectRecord:
        """Create or retrieve the default local project."""

        with REQUEST_TIMER.labels(route="/projects/default-local").time():
            return request.app.state.project_service.get_or_create_default_local_project()

    @router.post(
        "/projects/{project_id}/source-snapshots",
        response_model=SourceSnapshotRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["source-snapshots"],
    )
    async def create_source_snapshot(project_id: str, request: Request) -> SourceSnapshotRecord:
        """Create an exact, bounded source snapshot without executing repository code."""

        try:
            payload = await _read_source_snapshot_payload(request)
            return await request.app.state.source_snapshot_service.create(project_id, payload)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc
        except SourceSnapshotError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get(
        "/projects/{project_id}/source-snapshots",
        response_model=list[SourceSnapshotRecord],
        tags=["source-snapshots"],
    )
    async def list_source_snapshots(project_id: str, request: Request) -> list[SourceSnapshotRecord]:
        try:
            return request.app.state.source_snapshot_service.list(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc

    @router.post(
        "/projects/{project_id}/source-snapshots/{snapshot_id}/verify",
        response_model=SourceSnapshotVerification,
        tags=["source-snapshots"],
    )
    async def verify_source_snapshot(project_id: str, snapshot_id: str, request: Request) -> SourceSnapshotVerification:
        try:
            return request.app.state.source_snapshot_service.verify(project_id, snapshot_id)
        except (ProjectNotFoundError, SourceSnapshotError) as exc:
            code = status.HTTP_404_NOT_FOUND if isinstance(exc, ProjectNotFoundError) else exc.status_code
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.delete(
        "/projects/{project_id}/source-snapshots/{snapshot_id}",
        response_model=SourceSnapshotRecord,
        tags=["source-snapshots"],
    )
    async def delete_source_snapshot(project_id: str, snapshot_id: str, request: Request) -> SourceSnapshotRecord:
        try:
            return request.app.state.source_snapshot_service.delete(project_id, snapshot_id)
        except (ProjectNotFoundError, SourceSnapshotError) as exc:
            code = status.HTTP_404_NOT_FOUND if isinstance(exc, ProjectNotFoundError) else exc.status_code
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.get(
        "/projects/{project_id}/source-snapshots/{snapshot_id}",
        response_model=SourceSnapshotRecord,
        tags=["source-snapshots"],
    )
    async def get_source_snapshot(project_id: str, snapshot_id: str, request: Request) -> SourceSnapshotRecord:
        try:
            return request.app.state.source_snapshot_service.get(project_id, snapshot_id)
        except (ProjectNotFoundError, SourceSnapshotError) as exc:
            code = status.HTTP_404_NOT_FOUND if isinstance(exc, ProjectNotFoundError) else exc.status_code
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.post(
        "/projects/{project_id}/repository-analysis",
        response_model=RepositoryAnalysisResult,
        tags=["projects"],
    )
    async def analyze_repository(
        project_id: str,
        payload: RepositoryAnalysisRequest,
        request: Request,
    ) -> RepositoryAnalysisResult:
        """Analyze recognized configuration in a public GitHub repository."""

        with REQUEST_TIMER.labels(route="/projects/{project_id}/repository-analysis").time():
            try:
                return await request.app.state.repository_analysis_service.analyze(project_id, payload.branch)
            except ProjectNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc
            except ProjectModeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Repository analysis requires a public GitHub repository project",
                ) from exc
            except RepositoryAnalysisError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post(
        "/projects/{project_id}/repository-readiness",
        response_model=RepositoryReadinessResult,
        tags=["projects"],
    )
    async def assess_repository_readiness(
        project_id: str,
        payload: RepositoryReadinessRequest,
        request: Request,
    ) -> RepositoryReadinessResult:
        """Assess readiness from one persisted public repository analysis."""

        with REQUEST_TIMER.labels(route="/projects/{project_id}/repository-readiness").time():
            try:
                return request.app.state.repository_readiness_service.assess(
                    project_id,
                    payload.analysis_report_id,
                )
            except ProjectNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc
            except ProjectModeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Repository readiness requires a supported analyzed repository project",
                ) from exc
            except RepositoryReadinessError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post(
        "/projects/{project_id}/local-repository-analysis",
        response_model=RepositoryAnalysisResult,
        tags=["projects"],
    )
    async def analyze_local_repository(project_id: str, request: Request) -> RepositoryAnalysisResult:
        """Analyze bounded text configuration selected by a browser folder picker."""

        with REQUEST_TIMER.labels(route="/projects/{project_id}/local-repository-analysis").time():
            payload = await _read_local_analysis_payload(request)
            try:
                return request.app.state.local_repository_analysis_service.analyze(project_id, payload)
            except ProjectNotFoundError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc
            except ProjectModeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Local repository analysis requires a local project",
                ) from exc
            except LocalRepositoryAnalysisError as exc:
                raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post(
        "/projects/{project_id}/container-build-eligibility",
        response_model=ContainerBuildEligibilityResult,
        tags=["builds"],
    )
    async def container_build_eligibility(
        project_id: str,
        payload: ContainerBuildEligibilityRequest,
        request: Request,
    ) -> ContainerBuildEligibilityResult:
        """Validate evidence and isolation prerequisites without executing a build."""

        try:
            return request.app.state.container_build_service.eligibility(project_id, payload)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc
        except ContainerBuildError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.post(
        "/projects/{project_id}/container-builds",
        response_model=ContainerBuildRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["builds"],
    )
    async def create_container_build(
        project_id: str,
        payload: ContainerBuildCreateRequest,
        request: Request,
    ) -> ContainerBuildRecord:
        """Create an explicitly approved build attempt through the configured isolated executor."""

        try:
            return request.app.state.container_build_service.create(project_id, payload)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc
        except ContainerBuildError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get(
        "/projects/{project_id}/container-builds",
        response_model=list[ContainerBuildRecord],
        tags=["builds"],
    )
    async def list_container_builds(project_id: str, request: Request) -> list[ContainerBuildRecord]:
        try:
            return request.app.state.container_build_service.list_builds(project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc

    @router.get(
        "/projects/{project_id}/container-builds/{build_id}/logs",
        response_model=ContainerBuildLogsResponse,
        tags=["builds"],
    )
    async def container_build_logs(project_id: str, build_id: str, request: Request) -> ContainerBuildLogsResponse:
        result = request.app.state.container_build_service.logs(project_id, build_id)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Container build not found")
        return result

    @router.post(
        "/projects/{project_id}/container-builds/{build_id}/cancel",
        response_model=ContainerBuildRecord,
        tags=["builds"],
    )
    async def cancel_container_build(project_id: str, build_id: str, request: Request) -> ContainerBuildRecord:
        try:
            return request.app.state.container_build_service.cancel(project_id, build_id)
        except ContainerBuildError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get(
        "/projects/{project_id}/container-builds/{build_id}",
        response_model=ContainerBuildRecord,
        tags=["builds"],
    )
    async def get_container_build(project_id: str, build_id: str, request: Request) -> ContainerBuildRecord:
        result = request.app.state.container_build_service.get(project_id, build_id)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Container build not found")
        return result

    @router.get("/projects/{project_id}", response_model=ProjectRecord, tags=["projects"])
    async def get_project(project_id: str, request: Request) -> ProjectRecord:
        """Return one ClueStack project by ID."""

        with REQUEST_TIMER.labels(route="/projects/{project_id}").time():
            project = request.app.state.project_service.get(project_id)
            if project is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            return project

    @router.get("/projects/{project_id}/deployments", response_model=list[DeploymentRecord], tags=["projects"])
    async def list_project_deployments(project_id: str, request: Request) -> list[DeploymentRecord]:
        """List deployments explicitly associated with one project."""

        with REQUEST_TIMER.labels(route="/projects/{project_id}/deployments").time():
            project = request.app.state.project_service.get(project_id)
            if project is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            return request.app.state.deployment_service.list_deployments_for_project(project_id)

    @router.get("/projects/{project_id}/reports", response_model=list[DeploymentReportRecord], tags=["projects"])
    async def list_project_reports(project_id: str, request: Request) -> list[DeploymentReportRecord]:
        """List reports explicitly associated with one project."""

        with REQUEST_TIMER.labels(route="/projects/{project_id}/reports").time():
            project = request.app.state.project_service.get(project_id)
            if project is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            return request.app.state.report_service.list_reports(project_id=project_id)

    @router.get("/metrics/{service}", response_model=ServiceMetrics, tags=["observability"])
    async def metrics(service: str, request: Request) -> ServiceMetrics:
        """Return service-level Prometheus metrics for dashboard charts."""

        with REQUEST_TIMER.labels(route="/metrics/{service}").time():
            try:
                return await request.app.state.prometheus_client.service_metrics(service)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to read metrics from Prometheus",
                ) from exc

    @router.get("/workloads/{service}", response_model=WorkloadHealth, tags=["observability"])
    async def workload(service: str, request: Request) -> WorkloadHealth:
        """Return Kubernetes Deployment and Pod health for dashboard workload cards."""

        with REQUEST_TIMER.labels(route="/workloads/{service}").time():
            try:
                workload_health = request.app.state.deployment_service.workload_health(service)
            except DeploymentExecutionError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            if workload_health is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workload not found")
            return workload_health

    @router.get("/logs/{service}", response_model=LogsResponse, tags=["observability"])
    async def logs(
        service: str,
        request: Request,
        search: str | None = Query(default=None, max_length=200),
        pod: str | None = Query(default=None, min_length=1, max_length=253),
        container: str | None = Query(default=None, min_length=1, max_length=253),
        minutes: int = Query(default=15, ge=1, le=60),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> LogsResponse:
        """Return recent Loki log lines for a service."""

        with REQUEST_TIMER.labels(route="/logs/{service}").time():
            try:
                return await request.app.state.loki_client.logs(
                    service,
                    limit=limit,
                    minutes=minutes,
                    search=search,
                    pod=pod,
                    container=container,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to read logs from Loki",
                ) from exc

    @router.get("/alerts", response_model=AlertsResponse, tags=["observability"])
    async def alerts(request: Request) -> AlertsResponse:
        """Return normalized Alertmanager alert state for the dashboard."""

        with REQUEST_TIMER.labels(route="/alerts").time():
            try:
                return await request.app.state.alertmanager_client.alerts()
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Unable to read alerts from Alertmanager",
                ) from exc

    @router.delete("/deployment/{name}", status_code=status.HTTP_200_OK, tags=["deployments"])
    async def delete_deployment(name: str, request: Request) -> dict[str, str]:
        """Tear down a deployment and remove its ClueStack state."""

        with REQUEST_TIMER.labels(route="/deployment/{name}").time():
            try:
                deleted = request.app.state.deployment_service.delete(name)
            except DeploymentExecutionError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            if deleted is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
            return {"status": "deleted", "name": name}

    @router.post("/deployment/{name}/rollback", response_model=DeploymentRecord, tags=["deployments"])
    async def rollback_deployment(name: str, request: Request) -> DeploymentRecord:
        """Rollback a deployment with Kubernetes rollout undo."""

        with REQUEST_TIMER.labels(route="/deployment/{name}/rollback").time():
            try:
                rolled_back = request.app.state.deployment_service.rollback(name)
            except DeploymentExecutionError as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            if rolled_back is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
            return rolled_back

    return router


async def _read_local_analysis_payload(request: Request) -> LocalRepositoryAnalysisRequest:
    """Read JSON incrementally so oversized local submissions are rejected early."""

    limit = request.app.state.settings.local_analysis_max_request_bytes
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Request body is too large",
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length header",
            ) from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Request body is too large",
            )
    try:
        return LocalRepositoryAnalysisRequest.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid local analysis payload",
        ) from exc


async def _read_source_snapshot_payload(request: Request) -> SourceSnapshotCreateRequest:
    """Bound JSON buffering before validating a potentially multi-file local submission."""

    limit = request.app.state.settings.source_snapshot_max_request_bytes
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                raise HTTPException(status_code=413, detail="Snapshot request body is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header") from exc
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="Snapshot request body is too large")
    try:
        return SourceSnapshotCreateRequest.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid source snapshot payload") from exc
