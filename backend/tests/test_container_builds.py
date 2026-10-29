"""Controlled container-build orchestration tests; no real container runtime is used."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import (
    DeploymentReportCreate,
    DeploymentReportOperation,
    DeploymentReportStatus,
)
from app.services.container_builds import (
    BuildExecutionResult,
    ContainerBuildService,
    PreparedBuildSource,
)

COMMIT = "a" * 40


class FakeSourceProvider:
    def __init__(self) -> None:
        self.cleaned = False

    def availability(self, project_id, source, snapshot_id, expected_identity, analysis_report_id):
        if expected_identity and snapshot_id == "b" * 40:
            return False, "The snapshot commit does not match the analyzed commit."
        return True, "available"

    def prepare(self, project_id, source, snapshot_id, expected_identity, analysis_report_id):
        return PreparedBuildSource(
            snapshot_id=snapshot_id,
            project_id=project_id,
            integrity_hash="f" * 64,
            format_version=1,
            submission_complete=True,
        )

    def cleanup(self, prepared):
        self.cleaned = True


class FakeExecutor:
    available = True

    def __init__(self, result: BuildExecutionResult) -> None:
        self.result = result
        self.request = None

    def execute(self, request):
        self.request = request
        return self.result

    def cancel(self, build_id):
        return False


def build_client(tmp_path, *, execution_enabled=False) -> TestClient:
    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
        container_build_execution_enabled=execution_enabled,
    )
    return TestClient(create_app(settings))


def prepare_evidence(client: TestClient) -> tuple[dict, dict, dict]:
    project = client.post(
        "/projects",
        json={
            "name": "Build Sample",
            "slug": "build-sample",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://github.com/acme/sample",
        },
    ).json()
    reports = client.app.state.report_service
    now = datetime.now(UTC)
    analysis = reports.create(
        DeploymentReportCreate(
            project_id=project["id"],
            operation=DeploymentReportOperation.repository_analysis,
            status=DeploymentReportStatus.success,
            summary="Analysis complete.",
            target="github_public_readonly",
            commit_sha=COMMIT,
            started_at=now,
            completed_at=now,
            evidence={
                "source": "github_public",
                "retrieval_status": "complete",
                "readiness_signals": [
                    {"id": "container.dockerfile", "evidence_paths": ["Dockerfile"], "detail": "Detected."}
                ],
            },
        )
    )
    readiness = reports.create(
        DeploymentReportCreate(
            project_id=project["id"],
            operation=DeploymentReportOperation.repository_readiness,
            status=DeploymentReportStatus.success,
            summary="Ready.",
            target="github_public_readonly",
            commit_sha=COMMIT,
            started_at=now,
            completed_at=now,
            evidence={
                "analysis_report_id": analysis.id,
                "analysis_source": "github_public",
                "dimensions": [{"id": "containerization", "status": "ready"}],
            },
        )
    )
    payload = {
        "analysis_report_id": analysis.id,
        "readiness_report_id": readiness.id,
        "source_snapshot_id": COMMIT,
        "dockerfile_path": "Dockerfile",
        "context_path": ".",
        "image_name": "example/build-sample",
        "image_tag": "test-1",
    }
    return project, payload, {"analysis": analysis, "readiness": readiness}


def install_fakes(client: TestClient, result: BuildExecutionResult):
    provider = FakeSourceProvider()
    executor = FakeExecutor(result)
    client.app.state.container_build_service = ContainerBuildService(
        client.app.state.settings,
        client.app.state.project_service,
        client.app.state.report_service,
        provider,
        executor,
    )
    return provider, executor


def test_production_default_rejects_unavailable_snapshot_and_executor(tmp_path) -> None:
    client = build_client(tmp_path)
    project, payload, _ = prepare_evidence(client)

    eligibility = client.post(
        f"/projects/{project['id']}/container-build-eligibility",
        json=payload,
    )
    assert eligibility.status_code == 200
    assert eligibility.json()["eligible"] is False
    assert eligibility.json()["worker_availability"] == "unavailable"
    assert eligibility.json()["network_policy"] == "disabled"
    blockers = {item["code"] for item in eligibility.json()["findings"] if item["status"] == "blocker"}
    assert {"snapshot.unavailable", "executor.unavailable"} <= blockers

    attempt = client.post(
        f"/projects/{project['id']}/container-builds",
        json={**payload, "approved": True},
    )
    assert attempt.status_code == 201
    assert attempt.json()["status"] == "rejected"
    assert "deployment" not in attempt.json()["failure_reason"].lower()


def test_missing_analysis_and_readiness_are_actionable_blockers(tmp_path) -> None:
    client = build_client(tmp_path)
    project = client.post(
        "/projects",
        json={"name": "Empty Local", "slug": "empty-local", "mode": "local", "source_type": "manual"},
    ).json()
    response = client.post(
        f"/projects/{project['id']}/container-build-eligibility",
        json={
            "analysis_report_id": str(uuid4()),
            "readiness_report_id": str(uuid4()),
            "source_snapshot_id": None,
            "dockerfile_path": "Dockerfile",
            "context_path": ".",
            "image_name": "example/empty",
            "image_tag": "test",
        },
    )
    blockers = {item["code"] for item in response.json()["findings"] if item["status"] == "blocker"}
    assert {"analysis.missing", "readiness.missing", "snapshot.required"} <= blockers


def test_explicit_approval_is_required_and_persisted_as_rejected(tmp_path) -> None:
    client = build_client(tmp_path, execution_enabled=True)
    project, payload, _ = prepare_evidence(client)
    install_fakes(client, BuildExecutionResult(exit_status=0, logs="ok", image_digest=f"sha256:{'b' * 64}"))

    response = client.post(f"/projects/{project['id']}/container-builds", json={**payload, "approved": False})

    assert response.status_code == 201
    assert response.json()["status"] == "rejected"
    assert "approval" in response.json()["failure_reason"].lower()


def test_mocked_isolated_success_requires_exit_zero_and_verified_digest(tmp_path) -> None:
    client = build_client(tmp_path, execution_enabled=True)
    project, payload, _ = prepare_evidence(client)
    provider, executor = install_fakes(
        client,
        BuildExecutionResult(
            exit_status=0,
            logs="built image",
            image_digest=f"sha256:{'b' * 64}",
        ),
    )

    response = client.post(f"/projects/{project['id']}/container-builds", json={**payload, "approved": True})

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "succeeded"
    assert result["image_digest"] == f"sha256:{'b' * 64}"
    assert provider.cleaned is True
    assert executor.request.policy.network_disabled is True
    assert executor.request.policy.privileged is False
    assert executor.request.policy.host_docker_socket is False
    assert executor.request.policy.host_filesystem_mounts is False
    assert executor.request.policy.credentials_available is False
    assert executor.request.policy.process_limit == 64
    assert executor.request.source.project_id == project["id"]
    assert executor.request.source.integrity_hash == "f" * 64
    report = client.get(f"/reports/{result['report_id']}").json()
    assert report["operation"] == "container_build"
    assert "no deployment" in report["summary"].lower()


def test_failed_process_invalid_digest_and_sanitized_bounded_logs(tmp_path) -> None:
    client = build_client(tmp_path, execution_enabled=True)
    project, payload, _ = prepare_evidence(client)
    install_fakes(
        client,
        BuildExecutionResult(
            exit_status=1,
            logs="Authorization: Bearer synthetic-token\n" + ("x" * 5_000),
            image_digest="not-a-digest",
            failure_reason="builder exited with status 1",
        ),
    )

    response = client.post(f"/projects/{project['id']}/container-builds", json={**payload, "approved": True})
    assert response.json()["status"] == "failed"
    build_id = response.json()["id"]
    logs = client.get(f"/projects/{project['id']}/container-builds/{build_id}/logs").json()
    assert "synthetic-token" not in logs["logs"]
    assert logs["truncated"] is True
    assert len(logs["logs"].encode()) <= 2_000


def test_mismatched_revision_missing_dockerfile_invalid_context_and_cancellation(tmp_path) -> None:
    client = build_client(tmp_path, execution_enabled=True)
    project, payload, _ = prepare_evidence(client)
    install_fakes(client, BuildExecutionResult(exit_status=0, logs="ok", image_digest=f"sha256:{'c' * 64}"))
    invalid = {
        **payload,
        "source_snapshot_id": "b" * 40,
        "dockerfile_path": "nested/Dockerfile",
        "context_path": "nested",
    }

    eligibility = client.post(f"/projects/{project['id']}/container-build-eligibility", json=invalid).json()
    blockers = {item["code"] for item in eligibility["findings"] if item["status"] == "blocker"}
    assert {"snapshot.unavailable", "dockerfile.missing", "context.unsupported"} <= blockers

    rejected = client.post(
        f"/projects/{project['id']}/container-builds",
        json={**invalid, "approved": True},
    ).json()
    cancel = client.post(f"/projects/{project['id']}/container-builds/{rejected['id']}/cancel")
    assert cancel.status_code == 409
    assert "cancellation" in cancel.json()["detail"].lower()


def test_schema_rejects_unsafe_paths_and_invalid_image_tags(tmp_path) -> None:
    client = build_client(tmp_path)
    project, payload, _ = prepare_evidence(client)
    unsafe = client.post(
        f"/projects/{project['id']}/container-build-eligibility",
        json={**payload, "dockerfile_path": "../Dockerfile"},
    )
    assert unsafe.status_code == 200
    assert any(item["code"] == "dockerfile.path" for item in unsafe.json()["findings"])

    invalid_tag = client.post(
        f"/projects/{project['id']}/container-build-eligibility",
        json={**payload, "image_tag": "bad tag"},
    )
    assert invalid_tag.status_code == 422


def test_mocked_timeout_is_failure_and_never_success(tmp_path) -> None:
    client = build_client(tmp_path, execution_enabled=True)
    project, payload, _ = prepare_evidence(client)
    install_fakes(
        client,
        BuildExecutionResult(exit_status=0, logs="timed out", image_digest=f"sha256:{'d' * 64}", timed_out=True),
    )

    response = client.post(
        f"/projects/{project['id']}/container-builds",
        json={**payload, "approved": True},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["image_digest"] is None
    assert "timeout" in response.json()["failure_reason"].lower()
