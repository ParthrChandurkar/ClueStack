"""Deterministic tests for browser-submitted local repository analysis."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.local_repository_analysis import LocalRepositoryAnalysisError, LocalRepositoryAnalysisService


def build_client(tmp_path, **overrides) -> TestClient:
    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
        **overrides,
    )
    return TestClient(create_app(settings))


def create_local_project(client: TestClient) -> dict:
    response = client.post(
        "/projects",
        json={"name": "Local Sample", "slug": "local-sample", "mode": "local", "source_type": "manual"},
    )
    assert response.status_code == 201
    return response.json()


def test_local_analysis_uses_shared_findings_reports_and_readiness(tmp_path) -> None:
    client = build_client(tmp_path)
    project = create_local_project(client)
    response = client.post(
        f"/projects/{project['id']}/local-repository-analysis",
        json={
            "source_label": "sample-app",
            "submission_complete": True,
            "files": [
                {
                    "path": "package.json",
                    "content": '{"dependencies":{"react":"1"},"scripts":{"build":"vite build"},"main":"src.ts"}',
                },
                {"path": "Dockerfile", "content": 'FROM node:22\nRUN npm ci\nEXPOSE 3000\nCMD ["npm","start"]'},
                {
                    "path": "k8s/deployment.yaml",
                    "content": (
                        "kind: Deployment\nspec:\n  replicas: 1\n  template:\n    spec:\n      containers:\n"
                        "        - image: sample:latest\n          readinessProbe:\n            httpGet:\n"
                        "              path: /\n"
                    ),
                },
            ],
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["source"] == "local_upload"
    assert result["source_label"] == "sample-app"
    assert result["repository_url"] is None
    assert result["branch"] is None
    assert result["retrieval_status"] == "complete"
    assert result["files_retrieved"] == 3
    assert {item["category"] for item in result["findings"] if item["status"] == "detected"} >= {
        "languages",
        "frameworks",
        "container_configuration",
        "kubernetes",
    }

    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert len(reports) == 1
    assert reports[0]["target"] == "local_user_submission"
    assert reports[0]["evidence"]["source"] == "local_upload"
    assert "files" not in reports[0]["evidence"]
    assert "content" not in reports[0]["evidence"]
    assert "RUN npm ci" not in str(reports[0])

    readiness = client.post(
        f"/projects/{project['id']}/repository-readiness",
        json={"analysis_report_id": result["report_id"]},
    )
    assert readiness.status_code == 200
    assert readiness.json()["analysis_source"] == "local_upload"


def test_local_analysis_excludes_sensitive_generated_binary_and_unsupported_files(tmp_path) -> None:
    client = build_client(tmp_path)
    project = create_local_project(client)
    response = client.post(
        f"/projects/{project['id']}/local-repository-analysis",
        json={
            "source_label": "safe-sample",
            "files": [
                {"path": ".env", "content": "TOKEN=synthetic-secret"},
                {"path": "node_modules/package.json", "content": '{"name":"ignored"}'},
                {"path": "README.md", "content": "unsupported"},
                {"path": "Dockerfile", "content": "FROM python:3.13\u0000binary"},
                {"path": "requirements.txt", "content": "fastapi==1"},
            ],
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["retrieval_status"] == "partial"
    assert result["files_retrieved"] == 1
    assert "synthetic-secret" not in str(result)
    assert any("Excluded 2" in warning for warning in result["warnings"])
    assert any("unsupported" in warning for warning in result["warnings"])
    assert any("binary" in warning for warning in result["warnings"])
    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert "synthetic-secret" not in str(reports)


def test_local_analysis_rejects_traversal_duplicate_and_wrong_project_mode(tmp_path) -> None:
    client = build_client(tmp_path)
    local_project = create_local_project(client)
    unsafe = client.post(
        f"/projects/{local_project['id']}/local-repository-analysis",
        json={"source_label": "sample", "files": [{"path": "../package.json", "content": "{}"}]},
    )
    assert unsafe.status_code == 422

    absolute = client.post(
        f"/projects/{local_project['id']}/local-repository-analysis",
        json={"source_label": "sample", "files": [{"path": "C:/package.json", "content": "{}"}]},
    )
    assert absolute.status_code == 422

    duplicate = client.post(
        f"/projects/{local_project['id']}/local-repository-analysis",
        json={
            "source_label": "sample",
            "files": [
                {"path": "package.json", "content": "{}"},
                {"path": "package.json", "content": "{}"},
            ],
        },
    )
    assert duplicate.status_code == 422

    repository_project = client.post(
        "/projects",
        json={
            "name": "Remote",
            "slug": "remote",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://github.com/acme/sample",
        },
    ).json()
    wrong_mode = client.post(
        f"/projects/{repository_project['id']}/local-repository-analysis",
        json={"source_label": "sample", "files": []},
    )
    assert wrong_mode.status_code == 422


def test_local_analysis_enforces_file_count_and_request_body_limits(tmp_path) -> None:
    limited = build_client(tmp_path / "count", github_max_files=1)
    project = create_local_project(limited)
    response = limited.post(
        f"/projects/{project['id']}/local-repository-analysis",
        json={
            "source_label": "sample",
            "files": [
                {"path": "Dockerfile", "content": "FROM python"},
                {"path": "requirements.txt", "content": "fastapi"},
            ],
        },
    )
    assert response.status_code == 413

    body_limited = build_client(tmp_path / "body", local_analysis_max_request_bytes=10_000)
    body_project = create_local_project(body_limited)
    oversized = body_limited.post(
        f"/projects/{body_project['id']}/local-repository-analysis",
        json={
            "source_label": "sample",
            "files": [{"path": "requirements.txt", "content": "a" * 11_000}],
        },
    )
    assert oversized.status_code == 413


def test_empty_and_explicitly_partial_local_submissions_are_honest(tmp_path) -> None:
    client = build_client(tmp_path)
    project = create_local_project(client)
    empty = client.post(
        f"/projects/{project['id']}/local-repository-analysis",
        json={"source_label": "empty-folder", "files": []},
    )
    assert empty.status_code == 200
    assert empty.json()["retrieval_status"] == "complete"
    assert all(finding["status"] == "missing" for finding in empty.json()["findings"])

    partial = client.post(
        f"/projects/{project['id']}/local-repository-analysis",
        json={"source_label": "partial-folder", "submission_complete": False, "files": []},
    )
    assert partial.status_code == 200
    assert partial.json()["retrieval_status"] == "partial"
    assert all(finding["status"] == "unknown" for finding in partial.json()["findings"])


def test_local_analysis_timeout_is_bounded_and_reported(tmp_path, monkeypatch) -> None:
    client = build_client(tmp_path, local_analysis_timeout_seconds=1)
    project = create_local_project(client)

    def force_timeout(_service, _started_clock):
        raise LocalRepositoryAnalysisError(
            "Local repository analysis exceeded its processing time limit.",
            status_code=504,
            code="local_analysis_timeout",
        )

    monkeypatch.setattr(LocalRepositoryAnalysisService, "_check_deadline", force_timeout)

    response = client.post(
        f"/projects/{project['id']}/local-repository-analysis",
        json={"source_label": "sample", "files": [{"path": "Dockerfile", "content": "FROM python"}]},
    )

    assert response.status_code == 504
    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert reports[-1]["status"] == "failure"
    assert reports[-1]["errors"][0]["code"] == "local_analysis_timeout"
