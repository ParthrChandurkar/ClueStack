"""Immutable source snapshot tests; no repository code or containers are executed."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import (
    DeploymentReportCreate,
    DeploymentReportOperation,
    DeploymentReportStatus,
    RepositoryAnalysisSource,
    SourceSnapshotIdentity,
    SourceSnapshotRecord,
    SourceSnapshotStatus,
)
from app.services.source_snapshots import GitHubSnapshotAcquirer, canonical_manifest, normalize_snapshot_path


def build_client(tmp_path, transport=None, **overrides) -> TestClient:
    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
        source_snapshots_file=str(tmp_path / "snapshots.json"),
        source_snapshots_root=str(tmp_path / "snapshot-content"),
        **overrides,
    )
    app = create_app(settings)
    if transport is not None:
        app.state.source_snapshot_service._github = GitHubSnapshotAcquirer(settings, transport=transport)
    return TestClient(app)


def create_local(client: TestClient, name="Local Snapshot") -> dict:
    return client.post(
        "/projects",
        json={"name": name, "mode": "local", "source_type": "manual"},
    ).json()


def test_local_snapshot_is_deterministic_verified_and_restart_safe(tmp_path) -> None:
    client = build_client(tmp_path)
    project = create_local(client)
    response = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={
            "source_label": "Selected folder",
            "submission_complete": True,
            "files": [
                {"path": "src/main.py", "content": "print('safe')\n"},
                {"path": "Dockerfile", "content": "FROM python:3.13\n"},
                {"path": ".env", "content": "TOKEN=synthetic\n"},
                {"path": "config.txt", "content": "api_key=syntheticcredential12345\n"},
            ],
        },
    )
    assert response.status_code == 201
    snapshot = response.json()
    assert snapshot["status"] == "available"
    assert snapshot["file_count"] == 2
    assert snapshot["integrity_verified"] is True
    assert snapshot["source_identity"]["submission_id"]
    assert "repository_url" not in snapshot["source_identity"] or snapshot["source_identity"]["repository_url"] is None
    assert snapshot["exclusions"] == [
        {"reason": "sensitive_content", "count": 1},
        {"reason": "sensitive_path", "count": 1},
    ]
    assert [item["path"] for item in snapshot["included_files"]] == ["Dockerfile", "src/main.py"]

    identity = client.app.state.source_snapshot_service.get(project["id"], snapshot["id"]).source_identity
    manifest = client.app.state.source_snapshot_service.get(project["id"], snapshot["id"]).included_files
    expected = hashlib.sha256(canonical_manifest(identity, manifest).encode()).hexdigest()
    assert snapshot["integrity_hash"] == expected
    assert client.post(f"/projects/{project['id']}/source-snapshots/{snapshot['id']}/verify").json()["valid"]

    restarted = build_client(tmp_path)
    recovered = restarted.get(f"/projects/{project['id']}/source-snapshots/{snapshot['id']}")
    assert recovered.status_code == 200
    assert recovered.json()["integrity_hash"] == expected


def test_path_duplicates_limits_and_project_isolation(tmp_path) -> None:
    client = build_client(tmp_path, source_snapshot_max_files=2, source_snapshot_max_file_bytes=1_024)
    project = create_local(client)
    other = create_local(client, "Other Local")
    for path in ("../escape.py", "/absolute.py", "C:/windows.py", "bad\\path.py"):
        response = client.post(
            f"/projects/{project['id']}/source-snapshots",
            json={"files": [{"path": path, "content": "safe"}]},
        )
        assert response.status_code == 422
    duplicate = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={"files": [{"path": "a.py", "content": "a"}, {"path": "a.py", "content": "b"}]},
    )
    assert duplicate.status_code == 422
    oversized = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={"files": [{"path": "a.py", "content": "x" * 1_025}]},
    )
    assert oversized.status_code == 413
    too_many = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={"files": [{"path": f"{index}.py", "content": "safe"} for index in range(3)]},
    )
    assert too_many.status_code == 413
    binary = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={"files": [{"path": "binary.txt", "content": "before\u0000after"}]},
    )
    assert binary.status_code == 422
    valid = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={"files": [{"path": "a.py", "content": "safe"}]},
    ).json()
    assert client.get(f"/projects/{other['id']}/source-snapshots/{valid['id']}").status_code == 404
    duplicate_snapshot = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={"files": [{"path": "a.py", "content": "safe"}]},
    )
    assert duplicate_snapshot.status_code == 409


def test_aggregate_content_limit(tmp_path) -> None:
    client = build_client(
        tmp_path,
        source_snapshot_max_file_bytes=10_000,
        source_snapshot_max_total_bytes=10_000,
    )
    project = create_local(client)
    response = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={
            "files": [
                {"path": "one.py", "content": "a" * 6_000},
                {"path": "two.py", "content": "b" * 6_000},
            ]
        },
    )
    assert response.status_code == 413


def test_tampering_fails_verification_and_snapshot_can_be_deleted(tmp_path) -> None:
    client = build_client(tmp_path)
    project = create_local(client)
    snapshot = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={"files": [{"path": "app.py", "content": "original"}]},
    ).json()
    stored = Path(client.app.state.settings.source_snapshots_root) / snapshot["id"] / "app.py"
    stored.chmod(0o600)
    stored.write_text("tampered", encoding="utf-8")
    verification = client.post(f"/projects/{project['id']}/source-snapshots/{snapshot['id']}/verify")
    assert verification.json()["valid"] is False
    assert verification.json()["status"] == "failed"
    deleted = client.delete(f"/projects/{project['id']}/source-snapshots/{snapshot['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert not stored.parent.exists()


def test_github_snapshot_resolves_branch_to_exact_commit_and_excludes_binary(tmp_path) -> None:
    commit_sha = "a" * 40
    blobs = {
        "docker": b"FROM python:3.13\n",
        "source": b"print('hello')\n",
        "image": b"\x89PNG\x00data",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/acme/sample":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/acme/sample/commits/main":
            return httpx.Response(200, json={"sha": commit_sha})
        if path == f"/repos/acme/sample/git/commits/{commit_sha}":
            return httpx.Response(200, json={"tree": {"sha": "tree"}})
        if path == "/repos/acme/sample/git/trees/tree":
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {"type": "blob", "path": "Dockerfile", "sha": "docker", "size": len(blobs["docker"])},
                        {"type": "blob", "path": "src/app.py", "sha": "source", "size": len(blobs["source"])},
                        {"type": "blob", "path": "logo.png", "sha": "image", "size": len(blobs["image"])},
                    ],
                },
            )
        if "/git/blobs/" in path:
            name = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"encoding": "base64", "content": base64.encodebytes(blobs[name]).decode()})
        return httpx.Response(404)

    client = build_client(tmp_path, httpx.MockTransport(handler))
    project = client.post(
        "/projects",
        json={
            "name": "GitHub Snapshot",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://github.com/acme/sample",
        },
    ).json()
    response = client.post(f"/projects/{project['id']}/source-snapshots", json={"reference": "main"})
    assert response.status_code == 201
    snapshot = response.json()
    assert snapshot["source_identity"]["commit_sha"] == commit_sha
    assert snapshot["source_identity"]["resolved_reference"] == "main"
    assert snapshot["file_count"] == 2
    assert snapshot["exclusions"] == [{"reason": "unsupported_binary", "count": 1}]
    available, _ = client.app.state.source_snapshot_service.build_availability(
        project["id"], RepositoryAnalysisSource.github_public, snapshot["id"], commit_sha, None
    )
    mismatch, reason = client.app.state.source_snapshot_service.build_availability(
        project["id"], RepositoryAnalysisSource.github_public, snapshot["id"], "b" * 40, None
    )
    assert available is True
    assert mismatch is False
    assert "commit" in reason


def test_truncated_github_tree_fails_without_publishing(tmp_path) -> None:
    commit_sha = "a" * 40

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/sample"):
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/commits/main"):
            return httpx.Response(200, json={"sha": commit_sha})
        if "/git/commits/" in path:
            return httpx.Response(200, json={"tree": {"sha": "tree"}})
        if "/git/trees/" in path:
            return httpx.Response(200, json={"truncated": True, "tree": []})
        return httpx.Response(404)

    client = build_client(tmp_path, httpx.MockTransport(handler))
    project = client.post(
        "/projects",
        json={
            "name": "Truncated",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://github.com/acme/sample",
        },
    ).json()
    response = client.post(f"/projects/{project['id']}/source-snapshots", json={})
    assert response.status_code == 422
    records = client.get(f"/projects/{project['id']}/source-snapshots").json()
    assert records[0]["status"] == "failed"
    assert not any(Path(client.app.state.settings.source_snapshots_root).iterdir())


def test_github_rate_limit_is_reported_without_publishing(tmp_path) -> None:
    client = build_client(tmp_path, httpx.MockTransport(lambda request: httpx.Response(429)))
    project = client.post(
        "/projects",
        json={
            "name": "Rate limited",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://github.com/acme/sample",
        },
    ).json()
    response = client.post(f"/projects/{project['id']}/source-snapshots", json={})
    assert response.status_code == 429
    assert "rate limits" in response.json()["detail"]
    assert client.get(f"/projects/{project['id']}/source-snapshots").json()[0]["status"] == "failed"


def test_restart_marks_interrupted_creation_failed_and_removes_only_staging(tmp_path) -> None:
    client = build_client(tmp_path)
    project = create_local(client)
    service = client.app.state.source_snapshot_service
    interrupted = SourceSnapshotRecord(
        id="11111111-1111-4111-8111-111111111111",
        project_id=project["id"],
        source=RepositoryAnalysisSource.local_upload,
        source_identity=SourceSnapshotIdentity(submission_id="submission"),
        created_at=datetime.now(UTC),
        file_count=0,
        total_content_size=0,
        status=SourceSnapshotStatus.creating,
    )
    service._metadata.create(interrupted)
    root = Path(client.app.state.settings.source_snapshots_root)
    staging = root / f".{interrupted.id}.test.creating"
    staging.mkdir(parents=True)
    (staging / "partial").write_text("incomplete", encoding="utf-8")
    unrelated = root / "unrelated"
    unrelated.mkdir()

    restarted = build_client(tmp_path)
    recovered = restarted.get(f"/projects/{project['id']}/source-snapshots/{interrupted.id}").json()
    assert recovered["status"] == "failed"
    assert "interrupted" in recovered["error"]
    assert not staging.exists()
    assert unrelated.exists()


def test_expiration_and_build_reference_prevent_unsafe_consumption_or_deletion(tmp_path) -> None:
    client = build_client(tmp_path)
    project = create_local(client)
    snapshot = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={"files": [{"path": "Dockerfile", "content": "FROM scratch\n"}]},
    ).json()
    service = client.app.state.source_snapshot_service
    record = service.get(project["id"], snapshot["id"])
    service._metadata.update(record.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}))
    listed = client.get(f"/projects/{project['id']}/source-snapshots").json()
    assert listed[0]["status"] == "expired"
    available, reason = service.build_availability(
        project["id"], RepositoryAnalysisSource.local_upload, snapshot["id"], None, None
    )
    assert available is False
    assert "expired" in reason

    service._metadata.update(record.model_copy(update={"expires_at": datetime.now(UTC) + timedelta(days=1)}))
    client.app.state.report_service.create(
        DeploymentReportCreate(
            project_id=project["id"],
            operation=DeploymentReportOperation.container_build,
            status=DeploymentReportStatus.failure,
            summary="Synthetic build history reference.",
            target="isolated-container-build",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            evidence={"source_snapshot_id": snapshot["id"]},
        )
    )
    response = client.delete(f"/projects/{project['id']}/source-snapshots/{snapshot['id']}")
    assert response.status_code == 409
    assert "referenced" in response.json()["detail"]


def test_incomplete_local_snapshot_is_not_a_complete_build_context_and_executor_stays_disabled(tmp_path) -> None:
    client = build_client(tmp_path)
    project = create_local(client)
    reports = client.app.state.report_service
    now = datetime.now(UTC)
    analysis = reports.create(
        DeploymentReportCreate(
            project_id=project["id"],
            operation=DeploymentReportOperation.repository_analysis,
            status=DeploymentReportStatus.success,
            summary="Local analysis complete.",
            target="local_user_submission",
            started_at=now,
            completed_at=now,
            evidence={
                "source": "local_upload",
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
            summary="Containerization ready.",
            target="local_user_submission",
            started_at=now,
            completed_at=now,
            evidence={
                "analysis_report_id": analysis.id,
                "analysis_source": "local_upload",
                "dimensions": [{"id": "containerization", "status": "ready"}],
            },
        )
    )
    snapshot = client.post(
        f"/projects/{project['id']}/source-snapshots",
        json={
            "analysis_report_id": analysis.id,
            "submission_complete": False,
            "files": [{"path": "Dockerfile", "content": "FROM scratch\n"}],
        },
    ).json()
    response = client.post(
        f"/projects/{project['id']}/container-build-eligibility",
        json={
            "analysis_report_id": analysis.id,
            "readiness_report_id": readiness.id,
            "source_snapshot_id": snapshot["id"],
            "dockerfile_path": "Dockerfile",
            "context_path": ".",
            "image_name": "example/local",
            "image_tag": "snapshot-test",
        },
    )
    assert response.status_code == 200
    findings = {item["code"]: item["status"] for item in response.json()["findings"]}
    assert findings["snapshot.unavailable"] == "blocker"
    assert findings["executor.unavailable"] == "blocker"
    assert response.json()["eligible"] is False


def test_normalized_manifest_order() -> None:
    assert normalize_snapshot_path("src/app.py") == "src/app.py"
