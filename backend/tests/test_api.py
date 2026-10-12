"""API regression tests for the InfraWatch backend."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.report_sanitizer import REDACTION_MARKER
from app.repository import connect_postgres
from app.schemas import AlertsResponse, AlertSummary, DeploymentReportCreate, MetricPoint, ServiceMetrics
from app.services.observability import AlertmanagerClient, LokiClient, PrometheusClient


class FakeRedis:
    """Small async Redis fake used by cache tests."""

    def __init__(self, initial: str | None = None, *, fail_get: bool = False, fail_set: bool = False) -> None:
        self.initial = initial
        self.fail_get = fail_get
        self.fail_set = fail_set
        self.values: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def get(self, key: str) -> str | None:
        """Return a cached value or simulate Redis being unavailable."""

        if self.fail_get:
            raise ConnectionError("redis unavailable")
        return self.values.get(key, self.initial)

    async def setex(self, key: str, ttl: int, value: str) -> bool:
        """Store a value with the requested TTL or simulate a write failure."""

        if self.fail_set:
            raise TimeoutError("redis write timeout")
        self.values[key] = value
        self.setex_calls.append((key, ttl, value))
        return True


class FakeHttpResponse:
    """Minimal httpx-like response for Loki tests."""

    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        """Raise an HTTP error for non-success status codes."""

        if self.status_code >= 400:
            raise httpx.HTTPStatusError("loki error", request=httpx.Request("GET", "http://loki"), response=None)

    def json(self) -> dict:
        """Return the fake JSON payload."""

        return self.payload


class FakeAsyncClient:
    """Small async client fake used by Loki tests."""

    payload: dict = {}
    status_code = 200
    raised: Exception | None = None
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def get(self, url: str, params: dict | None = None):
        """Capture the request and return a fake response."""

        self.calls.append({"url": url, "params": params or {}})
        if self.raised:
            raise self.raised
        return FakeHttpResponse(self.payload, status_code=self.status_code)


def build_client(tmp_path) -> TestClient:
    """Create a test client with isolated JSON state."""

    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit-log.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
        execute_kubectl=False,
        allow_mock_observability=True,
    )
    return TestClient(create_app(settings))


def sample_report(**overrides) -> DeploymentReportCreate:
    """Build a valid deployment report for API tests."""

    started_at = overrides.pop("started_at", datetime(2026, 10, 10, 10, 0, tzinfo=UTC))
    payload = {
        "operation": "deploy",
        "status": "success",
        "summary": "Deployment completed.",
        "target": "local_kubernetes",
        "started_at": started_at,
        "completed_at": started_at + timedelta(minutes=1),
        "duration_ms": 60_000,
        "evidence": {"metrics_source": "prometheus"},
        "errors": [],
    }
    payload.update(overrides)
    return DeploymentReportCreate(**payload)


def test_deployment_lifecycle(tmp_path) -> None:
    """A service can be deployed, listed, observed, and deleted."""

    client = build_client(tmp_path)
    payload = {
        "name": "catalog-api",
        "image": "docker.io/example/catalog-api:latest",
        "replicas": 2,
        "port": 8080,
        "environment": {"ENVIRONMENT": "test"},
    }

    deploy_response = client.post("/deploy", json=payload)
    assert deploy_response.status_code == 202
    assert deploy_response.json()["deployment"]["status"] == "Running"
    deployment_id = deploy_response.json()["deployment"]["id"]
    UUID(deployment_id)

    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["fastapi"] is True
    assert health.json()["deployment_mode"] == "manifest-simulation"

    deployments = client.get("/deployments")
    assert deployments.status_code == 200
    assert deployments.json()[0]["name"] == "catalog-api"
    assert deployments.json()[0]["id"] == deployment_id

    metrics = client.get("/metrics/catalog-api")
    assert metrics.status_code == 200
    assert metrics.json()["source"] == "mock"
    assert len(metrics.json()["cpu_cores"]) == 15
    assert all(point["value"] >= 0 for point in metrics.json()["error_rate"])

    logs = client.get("/logs/catalog-api")
    assert logs.status_code == 200
    assert logs.json()["lines"]
    assert len({entry["line"] for entry in logs.json()["lines"]}) >= 5
    assert any("[warn]" in entry["line"] for entry in logs.json()["lines"])

    deleted = client.delete("/deployment/catalog-api")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"

    audit_logs = client.get("/audit-logs")
    assert audit_logs.status_code == 200
    events = audit_logs.json()
    assert events[0]["action"] == "deployment.deleted"
    assert events[0]["service"] == "catalog-api"
    assert events[1]["action"] == "deployment.simulated"


def test_deployment_id_is_stable_across_redeployments(tmp_path) -> None:
    """A service keeps its stable deployment UUID when the name-based deployment is updated."""

    client = build_client(tmp_path)
    payload = {
        "name": "catalog-api",
        "image": "docker.io/example/catalog-api:latest",
        "replicas": 1,
        "port": 8080,
    }

    first = client.post("/deploy", json=payload)
    assert first.status_code == 202
    deployment_id = first.json()["deployment"]["id"]
    UUID(deployment_id)

    second = client.post(
        "/deploy",
        json={
            **payload,
            "image": "docker.io/example/catalog-api:v2",
            "replicas": 2,
        },
    )

    assert second.status_code == 202
    assert second.json()["deployment"]["id"] == deployment_id
    assert second.json()["deployment"]["image"] == "docker.io/example/catalog-api:v2"
    assert second.json()["deployment"]["replicas"] == 2


def test_successful_deployment_creates_sanitized_report_with_stable_ids(tmp_path) -> None:
    """A successful deployment attempt should create one historical report."""

    client = build_client(tmp_path)
    project = client.get("/projects/default-local").json()
    response = client.post(
        "/deploy",
        json={
            "name": "catalog-api",
            "project_id": project["id"],
            "image": "redis://user:synthetic-secret@registry.example.com/catalog-api:latest",
            "replicas": 2,
            "port": 8080,
            "environment": {"PASSWORD": "must-not-be-persisted"},
        },
    )

    assert response.status_code == 202
    deployment = response.json()["deployment"]
    UUID(deployment["id"])

    reports = client.get("/reports")
    assert reports.status_code == 200
    body = reports.json()
    assert len(body) == 1
    report = body[0]
    UUID(report["id"])
    assert report["operation"] == "deploy"
    assert report["status"] == "success"
    assert report["project_id"] == project["id"]
    assert report["deployment_id"] == deployment["id"]
    assert report["target"] == "manifest_simulation"
    assert report["duration_ms"] >= 0
    assert report["evidence"]["deployment_name"] == "catalog-api"
    assert report["evidence"]["replicas"] == 2
    assert "synthetic-secret" not in json.dumps(report["evidence"])
    assert REDACTION_MARKER in report["evidence"]["image"]
    assert "PASSWORD" not in json.dumps(report["evidence"])


def test_failed_deployment_creates_failure_report_without_masking_result(monkeypatch, tmp_path) -> None:
    """A Kubernetes rollout failure should preserve response behavior and persist a failure report."""

    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit-log.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
        execute_kubectl=True,
        allow_mock_observability=True,
    )

    def fail_rollout_status(_self, _name: str, _namespace: str) -> None:
        raise RuntimeError("should not be used")

    def fake_read_deployment_state(_self, _name: str, _namespace: str) -> dict:
        return {"ready_replicas": 0, "available_replicas": 0, "observed_generation": None}

    def fake_pod_failure_summary(_self, _name: str, _namespace: str) -> None:
        return None

    def raise_deployment_error(_self, _manifest: dict) -> None:
        from app.services.deployments import DeploymentExecutionError

        raise DeploymentExecutionError("kubectl failed token=synthetic-token")

    monkeypatch.setattr("app.services.deployments.DeploymentService._kubectl_apply", raise_deployment_error)
    monkeypatch.setattr("app.services.deployments.DeploymentService._kubectl_rollout_status", fail_rollout_status)
    monkeypatch.setattr("app.services.deployments.DeploymentService._read_deployment_state", fake_read_deployment_state)
    monkeypatch.setattr("app.services.deployments.DeploymentService._pod_failure_summary", fake_pod_failure_summary)
    client = TestClient(create_app(settings))

    response = client.post(
        "/deploy",
        json={
            "name": "catalog-api",
            "image": "docker.io/example/catalog-api:latest",
            "replicas": 1,
            "port": 8080,
        },
    )

    assert response.status_code == 202
    assert response.json()["deployment"]["status"] == "Failed"
    assert "kubectl failed" in response.json()["deployment"]["message"]
    reports = client.get("/reports").json()
    assert len(reports) == 1
    report = reports[0]
    assert report["status"] == "failure"
    assert report["operation"] == "deploy"
    UUID(report["deployment_id"])
    assert "synthetic-token" not in report["errors"][0]["message"]
    assert REDACTION_MARKER in report["errors"][0]["message"]


def test_project_validation_failure_does_not_create_deployment_report(tmp_path) -> None:
    """Project validation happens before deployment execution and should not create reports."""

    client = build_client(tmp_path)

    response = client.post(
        "/deploy",
        json={
            "name": "catalog-api",
            "project_id": "00000000-0000-0000-0000-000000000404",
            "image": "docker.io/example/catalog-api:latest",
        },
    )

    assert response.status_code == 404
    assert client.get("/reports").json() == []


def test_report_persistence_failure_does_not_mask_success(monkeypatch, tmp_path) -> None:
    """Report persistence failures should not turn a successful deployment into API failure."""

    client = build_client(tmp_path)

    def fail_create(_report):
        raise RuntimeError("postgresql://user:secret@example/infrawatch")

    monkeypatch.setattr(client.app.state.report_service, "create", fail_create)

    response = client.post(
        "/deploy",
        json={
            "name": "catalog-api",
            "image": "docker.io/example/catalog-api:latest",
        },
    )

    assert response.status_code == 202
    assert response.json()["deployment"]["name"] == "catalog-api"


def test_unknown_deployment_delete_returns_404(tmp_path) -> None:
    """Deleting a missing deployment returns a correct HTTP error."""

    client = build_client(tmp_path)
    response = client.delete("/deployment/missing-api")
    assert response.status_code == 404

    audit_logs = client.get("/audit-logs")
    assert audit_logs.status_code == 200
    assert audit_logs.json()[0]["action"] == "deployment.delete_missing"


def test_workload_health_uses_stored_state_when_kubectl_disabled(tmp_path) -> None:
    """The workload endpoint should expose replica health without requiring kubectl in demo mode."""

    client = build_client(tmp_path)
    payload = {
        "name": "catalog-api",
        "image": "docker.io/example/catalog-api:latest",
        "replicas": 2,
        "port": 8080,
    }

    assert client.post("/deploy", json=payload).status_code == 202
    response = client.get("/workloads/catalog-api")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "catalog-api"
    assert body["desired_replicas"] == 2
    assert body["ready_replicas"] == 2
    assert body["available_replicas"] == 2
    assert body["unavailable_replicas"] == 0
    assert body["pods"] == []
    assert body["source"] == "stored"


def test_unknown_workload_returns_404(tmp_path) -> None:
    """A missing workload should return a clear 404."""

    client = build_client(tmp_path)
    response = client.get("/workloads/missing-api")

    assert response.status_code == 404


def test_workload_health_reads_live_kubernetes_payload(monkeypatch, tmp_path) -> None:
    """The workload endpoint should expose live Deployment and Pod health when kubectl is enabled."""

    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit-log.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
        execute_kubectl=True,
        allow_mock_observability=True,
    )

    def fake_kubectl(_self, args: list[str], stdin: str | None = None, timeout: int = 45) -> str:
        if "deployment/catalog-api" in args:
            return json.dumps(
                {
                    "spec": {"replicas": 2},
                    "status": {
                        "updatedReplicas": 2,
                        "readyReplicas": 1,
                        "availableReplicas": 1,
                        "unavailableReplicas": 1,
                        "observedGeneration": 7,
                    },
                }
            )
        if "pods" in args:
            return json.dumps(
                {
                    "items": [
                        {
                            "metadata": {"name": "catalog-api-healthy"},
                            "status": {
                                "phase": "Running",
                                "containerStatuses": [{"ready": True, "restartCount": 0}],
                            },
                        },
                        {
                            "metadata": {"name": "catalog-api-waiting"},
                            "status": {
                                "phase": "Pending",
                                "containerStatuses": [
                                    {
                                        "ready": False,
                                        "restartCount": 2,
                                        "state": {
                                            "waiting": {
                                                "reason": "ImagePullBackOff",
                                                "message": "image pull failed",
                                            }
                                        },
                                    }
                                ],
                            },
                        },
                    ]
                }
            )
        raise AssertionError(f"unexpected kubectl args: {args}")

    monkeypatch.setattr("app.services.deployments.DeploymentService._run_kubectl", fake_kubectl)
    client = TestClient(create_app(settings))

    response = client.get("/workloads/catalog-api")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "kubernetes"
    assert body["desired_replicas"] == 2
    assert body["ready_replicas"] == 1
    assert body["available_replicas"] == 1
    assert body["unavailable_replicas"] == 1
    assert body["observed_generation"] == 7
    assert body["pods"][0]["ready"] is True
    assert body["pods"][1]["reason"] == "ImagePullBackOff"


def test_postgres_url_is_parsed_into_connection_kwargs(monkeypatch) -> None:
    """PostgreSQL URLs should be passed to psycopg2 as explicit connection fields."""

    captured: dict[str, object] = {}

    def fake_connect(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("app.repository.psycopg2.connect", fake_connect)

    connect_postgres("postgresql://infrawatch:abc-123@infrawatch-postgres:5432/infrawatch")

    assert captured["args"] == ()
    assert captured["kwargs"] == {
        "dbname": "infrawatch",
        "user": "infrawatch",
        "password": "abc-123",
        "host": "infrawatch-postgres",
        "port": 5432,
    }


def test_demo_rollback_is_audited_without_kubectl(tmp_path) -> None:
    """Rollback is safe in demo mode and records that no cluster action happened."""

    client = build_client(tmp_path)
    payload = {
        "name": "rollback-api",
        "image": "docker.io/example/rollback-api:latest",
        "replicas": 1,
        "port": 8080,
    }

    assert client.post("/deploy", json=payload).status_code == 202
    rollback = client.post("/deployment/rollback-api/rollback")
    assert rollback.status_code == 200
    assert rollback.json()["name"] == "rollback-api"
    assert "Demo Mode" in rollback.json()["message"]

    audit_logs = client.get("/audit-logs")
    assert audit_logs.status_code == 200
    assert audit_logs.json()[0]["action"] == "deployment.rollback_simulated"


def test_demo_runtime_can_seed_sample_services(tmp_path) -> None:
    """Demo runtimes start with useful sample data when explicitly enabled."""

    settings = Settings(
        environment="demo",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit-log.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
        execute_kubectl=False,
        seed_demo_data=True,
        allow_mock_observability=True,
    )
    client = TestClient(create_app(settings))

    deployments = client.get("/deployments")
    assert deployments.status_code == 200
    assert {item["name"] for item in deployments.json()} == {
        "catalog-api",
        "checkout-api",
        "payments-worker",
    }


def test_project_api_creates_lists_and_gets_project(tmp_path) -> None:
    """Project endpoints should expose file-backed project persistence."""

    client = build_client(tmp_path)
    payload = {
        "name": "Catalog Local",
        "slug": "catalog-local",
        "mode": "local",
        "source_type": "manual",
        "default_namespace": "infrawatch",
    }

    created = client.post("/projects", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Catalog Local"
    assert body["slug"] == "catalog-local"
    assert body["mode"] == "local"
    assert body["source_type"] == "manual"
    assert body["repository_url"] is None
    assert body["id"]

    listed = client.get("/projects")
    assert listed.status_code == 200
    assert listed.json() == [body]

    fetched = client.get(f"/projects/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_project_api_default_local_project_is_idempotent(tmp_path) -> None:
    """The default-local endpoint should create then return the same project."""

    client = build_client(tmp_path)

    first = client.get("/projects/default-local")
    second = client.get("/projects/default-local")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["slug"] == "default-local"
    assert client.get("/projects").json() == [first.json()]


def test_project_api_missing_project_returns_404(tmp_path) -> None:
    """Unknown project IDs should return a clean 404 response."""

    client = build_client(tmp_path)

    response = client.get("/projects/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


def test_project_api_duplicate_slug_returns_409(tmp_path) -> None:
    """Duplicate project slugs should become HTTP 409 conflicts."""

    client = build_client(tmp_path)
    payload = {"name": "Catalog Local", "slug": "catalog-local"}

    assert client.post("/projects", json=payload).status_code == 201
    duplicate = client.post("/projects", json={"name": "Another Catalog", "slug": "catalog-local"})

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Project slug already exists"


def test_project_api_invalid_payload_returns_422(tmp_path) -> None:
    """FastAPI validation should reject inconsistent project payloads."""

    client = build_client(tmp_path)
    payload = {
        "name": "Catalog",
        "mode": "github_repository",
        "source_type": "manual",
        "repository_url": "https://github.com/example/catalog",
    }

    response = client.post("/projects", json=payload)

    assert response.status_code == 422


def test_deploy_without_project_uses_default_local_project(tmp_path) -> None:
    """New deployments without project_id should be associated with default-local."""

    client = build_client(tmp_path)
    response = client.post(
        "/deploy",
        json={
            "name": "defaulted-api",
            "image": "docker.io/example/defaulted-api:latest",
            "replicas": 1,
            "port": 8080,
        },
    )

    assert response.status_code == 202
    deployment = response.json()["deployment"]
    default_project = client.get("/projects/default-local").json()
    assert deployment["project_id"] == default_project["id"]

    project_deployments = client.get(f"/projects/{default_project['id']}/deployments")
    assert project_deployments.status_code == 200
    assert [item["name"] for item in project_deployments.json()] == ["defaulted-api"]


def test_deploy_with_explicit_local_project_id(tmp_path) -> None:
    """A deployment can be explicitly associated with a local project."""

    client = build_client(tmp_path)
    project = client.post("/projects", json={"name": "Catalog Local", "slug": "catalog-local"}).json()

    response = client.post(
        "/deploy",
        json={
            "name": "catalog-api",
            "project_id": project["id"],
            "image": "docker.io/example/catalog-api:latest",
            "replicas": 2,
            "port": 8080,
        },
    )

    assert response.status_code == 202
    assert response.json()["deployment"]["project_id"] == project["id"]
    project_deployments = client.get(f"/projects/{project['id']}/deployments")
    assert project_deployments.status_code == 200
    assert [item["name"] for item in project_deployments.json()] == ["catalog-api"]


def test_deploy_with_unknown_project_id_returns_404(tmp_path) -> None:
    """Explicit unknown project IDs should be rejected without creating deployments."""

    client = build_client(tmp_path)

    response = client.post(
        "/deploy",
        json={
            "name": "missing-project-api",
            "project_id": "00000000-0000-0000-0000-000000000000",
            "image": "docker.io/example/missing-project-api:latest",
            "replicas": 1,
            "port": 8080,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"
    assert client.get("/deployments").json() == []


def test_deploy_with_github_repository_project_returns_422(tmp_path) -> None:
    """Local deployments should not be associated with repository-mode projects."""

    client = build_client(tmp_path)
    project = client.post(
        "/projects",
        json={
            "name": "Catalog Repository",
            "slug": "catalog-repository",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://github.com/example/catalog-api",
        },
    ).json()

    response = client.post(
        "/deploy",
        json={
            "name": "repo-mode-api",
            "project_id": project["id"],
            "image": "docker.io/example/repo-mode-api:latest",
            "replicas": 1,
            "port": 8080,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Local deployments require a local project"


def test_project_deployments_missing_project_returns_404(tmp_path) -> None:
    """Project-scoped deployment listing should validate the project first."""

    client = build_client(tmp_path)

    response = client.get("/projects/00000000-0000-0000-0000-000000000000/deployments")

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


def test_report_api_empty_history(tmp_path) -> None:
    """Report history should be empty before reports are created internally."""

    client = build_client(tmp_path)

    response = client.get("/reports")

    assert response.status_code == 200
    assert response.json() == []


def test_report_api_lists_filters_and_gets_reports(tmp_path) -> None:
    """Report endpoints should expose internally persisted report history."""

    client = build_client(tmp_path)
    project = client.post("/projects", json={"name": "Catalog Local", "slug": "catalog-local"}).json()
    deployment_id = "00000000-0000-0000-0000-000000000002"
    matching = client.app.state.report_service._repository.create(
        sample_report(project_id=project["id"], deployment_id=deployment_id)
    )
    client.app.state.report_service._repository.create(
        sample_report(
            project_id="00000000-0000-0000-0000-000000000003",
            deployment_id=deployment_id,
            started_at=datetime(2026, 10, 10, 10, 2, tzinfo=UTC),
        )
    )

    listed = client.get("/reports")
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    filtered = client.get(f"/reports?project_id={project['id']}&deployment_id={deployment_id}")
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()] == [matching.id]

    fetched = client.get(f"/reports/{matching.id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == matching.id
    assert fetched.json()["project_id"] == project["id"]


def test_project_report_api_validates_project_and_lists_reports(tmp_path) -> None:
    """Project-scoped report history should validate the project first."""

    client = build_client(tmp_path)
    project = client.post("/projects", json={"name": "Catalog Local", "slug": "catalog-local"}).json()
    report = client.app.state.report_service._repository.create(sample_report(project_id=project["id"]))

    response = client.get(f"/projects/{project['id']}/reports")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [report.id]


def test_report_api_missing_report_returns_404(tmp_path) -> None:
    """Unknown report IDs should return a clean 404."""

    client = build_client(tmp_path)

    response = client.get("/reports/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["detail"] == "Report not found"


def test_project_report_api_missing_project_returns_404(tmp_path) -> None:
    """Project-scoped reports should reject unknown projects."""

    client = build_client(tmp_path)

    response = client.get("/projects/00000000-0000-0000-0000-000000000000/reports")

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


def test_report_api_returns_sanitized_report_data(tmp_path) -> None:
    """Report data returned through the API should be sanitized by the repository boundary."""

    client = build_client(tmp_path)
    report = client.app.state.report_service._repository.create(
        sample_report(
            evidence={"headers": {"Authorization": "Bearer synthetic-token"}},
            errors=[{"message": "failed with password=synthetic-password"}],
        )
    )

    response = client.get(f"/reports/{report.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["evidence"]["headers"]["Authorization"] == "[REDACTED]"
    assert "synthetic-password" not in body["errors"][0]["message"]


def loki_payload(*streams: dict) -> dict:
    """Build a Loki query_range payload for tests."""

    return {"status": "success", "data": {"result": list(streams)}}


def test_loki_logs_are_normalized_from_real_stream_labels(monkeypatch) -> None:
    """Loki streams should become frontend-friendly log entries with pod metadata."""

    FakeAsyncClient.payload = loki_payload(
        {
            "stream": {
                "namespace": "infrawatch",
                "app": "infrawatch-backend",
                "pod": "infrawatch-backend-abc",
                "container": "backend",
            },
            "values": [["1700000000000000000", "GET /healthz 200"]],
        }
    )
    FakeAsyncClient.raised = None
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="test", allow_mock_observability=False))
    logs = asyncio.run(client.logs("infrawatch-backend", limit=25, search="healthz", pod="infrawatch-backend-abc"))

    assert logs.source == "loki"
    assert logs.lines[0].line == "GET /healthz 200"
    assert logs.lines[0].namespace == "infrawatch"
    assert logs.lines[0].pod == "infrawatch-backend-abc"
    assert logs.lines[0].container == "backend"
    assert logs.lines[0].labels["app"] == "infrawatch-backend"
    params = FakeAsyncClient.calls[0]["params"]
    assert params["limit"] == 25
    assert 'app="infrawatch-backend"' in params["query"]
    assert 'pod="infrawatch-backend-abc"' in params["query"]
    assert '|= "healthz"' in params["query"]
    assert params["end"] > params["start"]


def test_loki_empty_response_stays_empty(monkeypatch) -> None:
    """An empty Loki response should not be replaced with fake logs."""

    FakeAsyncClient.payload = loki_payload()
    FakeAsyncClient.raised = None
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="test", allow_mock_observability=True))
    logs = asyncio.run(client.logs("infrawatch-backend"))

    assert logs.source == "loki"
    assert logs.lines == []


def test_loki_http_errors_surface_when_not_in_demo(monkeypatch) -> None:
    """Loki failures should be reported instead of silently becoming fake logs."""

    FakeAsyncClient.payload = {}
    FakeAsyncClient.raised = httpx.ConnectTimeout("timeout")
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="development", allow_mock_observability=True))

    try:
        asyncio.run(client.logs("infrawatch-backend"))
    except httpx.ConnectTimeout:
        pass
    else:
        raise AssertionError("Loki timeout should be surfaced outside explicit demo/test contexts")


def test_loki_malformed_response_surfaces_when_not_in_demo(monkeypatch) -> None:
    """Malformed Loki payloads should not be hidden as real logs."""

    FakeAsyncClient.payload = {"unexpected": "shape"}
    FakeAsyncClient.raised = None
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="development", allow_mock_observability=True))

    try:
        asyncio.run(client.logs("infrawatch-backend"))
    except KeyError:
        pass
    else:
        raise AssertionError("Malformed Loki responses should be surfaced outside demo/test contexts")


def test_loki_demo_fallback_is_explicitly_marked_mock(monkeypatch) -> None:
    """Demo fallback logs must never claim to come from Loki."""

    FakeAsyncClient.payload = {}
    FakeAsyncClient.raised = httpx.ConnectError("loki unavailable")
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = LokiClient(Settings(environment="demo", allow_mock_observability=True))
    logs = asyncio.run(client.logs("catalog-api"))

    assert logs.source == "mock"
    assert logs.lines


def test_loki_rejects_unsafe_label_filters() -> None:
    """Unsafe service/pod labels should be rejected before a LogQL query is built."""

    client = LokiClient(Settings(environment="test", allow_mock_observability=False))

    try:
        asyncio.run(client.logs("bad/service"))
    except ValueError:
        pass
    else:
        raise AssertionError("Unsafe Loki label values must be rejected")


def test_alertmanager_alerts_are_normalized(monkeypatch) -> None:
    """Alertmanager payloads should become a stable dashboard contract."""

    FakeAsyncClient.payload = [
        {
            "fingerprint": "abc123",
            "status": {"state": "active"},
            "labels": {
                "alertname": "DeploymentUnavailable",
                "severity": "critical",
                "namespace": "infrawatch",
                "pod": "infrawatch-backend-abc",
                "service": "infrawatch-backend",
                "instance": "minikube",
            },
            "annotations": {
                "summary": "Deployment has unavailable replicas",
                "description": "infrawatch-backend has unavailable pods.",
            },
            "startsAt": "2026-10-08T10:00:00Z",
        }
    ]
    FakeAsyncClient.raised = None
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = AlertmanagerClient(Settings(environment="development", allow_mock_observability=False))
    alerts = asyncio.run(client.alerts())

    assert alerts.source == "alertmanager"
    assert alerts.alerts[0].fingerprint == "abc123"
    assert alerts.alerts[0].status == "firing"
    assert alerts.alerts[0].alertname == "DeploymentUnavailable"
    assert alerts.alerts[0].severity == "critical"
    assert alerts.alerts[0].service == "infrawatch-backend"
    assert alerts.alerts[0].namespace == "infrawatch"
    assert alerts.alerts[0].pod == "infrawatch-backend-abc"
    assert alerts.alerts[0].summary == "Deployment has unavailable replicas"
    assert FakeAsyncClient.calls[0]["url"].endswith("/api/v2/alerts")


def test_alertmanager_empty_response_is_not_mocked(monkeypatch) -> None:
    """A reachable Alertmanager with no active alerts should stay empty."""

    FakeAsyncClient.payload = []
    FakeAsyncClient.raised = None
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = AlertmanagerClient(Settings(environment="test", allow_mock_observability=True))
    alerts = asyncio.run(client.alerts())

    assert alerts.source == "alertmanager"
    assert alerts.alerts == []


def test_alertmanager_failures_surface_outside_demo(monkeypatch) -> None:
    """Alertmanager outages should not become fake alerts in real/local mode."""

    FakeAsyncClient.payload = {}
    FakeAsyncClient.raised = httpx.ConnectError("alertmanager unavailable")
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = AlertmanagerClient(Settings(environment="development", allow_mock_observability=True))

    try:
        asyncio.run(client.alerts())
    except httpx.ConnectError:
        pass
    else:
        raise AssertionError("Alertmanager outage should be surfaced outside demo/test contexts")


def test_alertmanager_demo_fallback_is_explicitly_marked_mock(monkeypatch) -> None:
    """Demo fallback alerts must never claim to come from Alertmanager."""

    FakeAsyncClient.payload = {}
    FakeAsyncClient.raised = httpx.ConnectError("alertmanager unavailable")
    FakeAsyncClient.status_code = 200
    FakeAsyncClient.calls = []
    monkeypatch.setattr("app.services.observability.httpx.AsyncClient", FakeAsyncClient)

    client = AlertmanagerClient(Settings(environment="demo", allow_mock_observability=True))
    alerts = asyncio.run(client.alerts())

    assert alerts.source == "mock"
    assert alerts.alerts
    assert alerts.alerts[0].description == "This simulated alert is not from Alertmanager."


def test_alerts_endpoint_uses_alertmanager_client(tmp_path) -> None:
    """The public alerts endpoint should return the Alertmanager client response."""

    class FakeAlertmanager:
        async def alerts(self) -> AlertsResponse:
            return AlertsResponse(
                source="alertmanager",
                alerts=[
                    AlertSummary(
                        fingerprint="route-alert",
                        status="firing",
                        alertname="BackendUnavailable",
                        severity="critical",
                    )
                ],
            )

    client = build_client(tmp_path)
    client.app.state.alertmanager_client = FakeAlertmanager()

    response = client.get("/alerts")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "alertmanager"
    assert body["alerts"][0]["fingerprint"] == "route-alert"


def test_empty_prometheus_series_falls_back_to_mock_metrics() -> None:
    """A reachable but empty Prometheus should not leave demo dashboards blank."""

    settings = Settings(environment="test", allow_mock_observability=True)
    client = PrometheusClient(settings)

    async def empty_query(_query: str, end: int | None = None):
        return []

    client._query_range = empty_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("catalog-api"))
    assert metrics.source == "mock"
    assert len(metrics.cpu_cores) == 15


def test_empty_prometheus_error_series_means_zero_errors_not_mock_metrics() -> None:
    """No 5xx samples is a healthy zero-error state, not missing telemetry."""

    settings = Settings(environment="test", allow_mock_observability=True)
    client = PrometheusClient(settings)
    points = [MetricPoint(timestamp=100 + index, value=float(index + 1)) for index in range(3)]

    async def mostly_live_query(query: str, end: int | None = None):
        if 'status=~"5.."' in query:
            return []
        return points

    client._query_range = mostly_live_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("infrawatch-backend"))
    assert metrics.source == "prometheus"
    assert [point.value for point in metrics.error_rate] == [0.0, 0.0, 0.0]
    assert [point.timestamp for point in metrics.error_rate] == [100, 101, 102]


def test_prometheus_metrics_cache_miss_queries_prometheus_and_stores_response() -> None:
    """A cache miss should query Prometheus and store only the real Prometheus response."""

    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=5)
    redis = FakeRedis()
    client = PrometheusClient(settings, redis_client=redis)
    points = [MetricPoint(timestamp=100 + index, value=float(index + 1)) for index in range(3)]
    calls: list[str] = []

    async def live_query(query: str, end: int | None = None):
        calls.append(query)
        return [] if 'status=~"5.."' in query else points

    client._query_range = live_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("catalog-api"))

    assert metrics.source == "prometheus"
    assert len(calls) == 4
    assert len(redis.setex_calls) == 1
    assert redis.setex_calls[0][1] == 5
    assert ServiceMetrics.model_validate_json(redis.setex_calls[0][2]).source == "prometheus"


def test_prometheus_metrics_cache_hit_skips_prometheus() -> None:
    """A cache hit should return the cached response without calling Prometheus."""

    cached = ServiceMetrics(
        service="catalog-api",
        cpu_cores=[MetricPoint(timestamp=100, value=0.2)],
        memory_megabytes=[MetricPoint(timestamp=100, value=128)],
        request_rate=[MetricPoint(timestamp=100, value=12)],
        error_rate=[MetricPoint(timestamp=100, value=0)],
        source="prometheus",
    )
    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=5)
    client = PrometheusClient(settings, redis_client=FakeRedis(initial=cached.model_dump_json()))

    async def should_not_query(_query: str, end: int | None = None):
        raise AssertionError("Prometheus should not be queried on cache hit")

    client._query_range = should_not_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("catalog-api"))

    assert metrics == cached


def test_prometheus_metrics_cache_uses_configured_ttl() -> None:
    """Cached metrics should use the short configured TTL."""

    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=9)
    redis = FakeRedis()
    client = PrometheusClient(settings, redis_client=redis)
    points = [MetricPoint(timestamp=100 + index, value=float(index + 1)) for index in range(3)]

    async def live_query(query: str, end: int | None = None):
        return [] if 'status=~"5.."' in query else points

    client._query_range = live_query  # type: ignore[method-assign]

    asyncio.run(client.service_metrics("payments-api"))

    assert redis.setex_calls
    assert redis.setex_calls[0][1] == 9


def test_prometheus_metrics_continue_when_redis_is_unavailable() -> None:
    """Redis errors are bypassed so Prometheus remains available as the source of truth."""

    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=5)
    client = PrometheusClient(settings, redis_client=FakeRedis(fail_get=True, fail_set=True))
    points = [MetricPoint(timestamp=100 + index, value=float(index + 1)) for index in range(3)]
    calls: list[str] = []

    async def live_query(query: str, end: int | None = None):
        calls.append(query)
        return [] if 'status=~"5.."' in query else points

    client._query_range = live_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("checkout-api"))

    assert metrics.source == "prometheus"
    assert len(calls) == 4


def test_mock_metrics_are_not_cached() -> None:
    """Mock fallback data should not be cached as if it were Prometheus truth."""

    settings = Settings(environment="test", allow_mock_observability=True, redis_url="redis://test", redis_cache_ttl=5)
    redis = FakeRedis()
    client = PrometheusClient(settings, redis_client=redis)

    async def empty_query(_query: str, end: int | None = None):
        return []

    client._query_range = empty_query  # type: ignore[method-assign]

    metrics = asyncio.run(client.service_metrics("catalog-api"))

    assert metrics.source == "mock"
    assert redis.setex_calls == []
