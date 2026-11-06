"""Public GitHub repository analysis tests with deterministic HTTP responses."""

import base64
import json
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.repository_analysis import (
    GitHubRepositoryClient,
    RepositoryAnalysisError,
    RepositoryAnalysisService,
    analyze_repository_files,
    parse_github_repository_url,
)


def _encoded(value: str) -> str:
    # GitHub blob responses wrap Base64 content across multiple lines.
    return base64.encodebytes(value.encode()).decode()


def github_transport(
    files: dict[str, str],
    *,
    truncated: bool = False,
    override: Callable[[httpx.Request], httpx.Response | None] | None = None,
) -> httpx.MockTransport:
    """Build a complete deterministic GitHub API transport."""

    tree = [
        {"path": path, "type": "blob", "sha": f"blob-{index}", "size": len(content.encode())}
        for index, (path, content) in enumerate(files.items())
    ]
    content_by_sha = {f"blob-{index}": content for index, content in enumerate(files.values())}

    def handler(request: httpx.Request) -> httpx.Response:
        if override:
            response = override(request)
            if response is not None:
                return response
        path = request.url.path
        if path == "/repos/acme/widgets":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/acme/widgets/branches/main":
            return httpx.Response(200, json={"commit": {"sha": "commit-123"}})
        if path == "/repos/acme/widgets/git/commits/commit-123":
            return httpx.Response(200, json={"tree": {"sha": "tree-123"}})
        if path == "/repos/acme/widgets/git/trees/tree-123":
            return httpx.Response(200, json={"tree": tree, "truncated": truncated})
        if "/git/blobs/" in path:
            sha = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={"encoding": "base64", "content": _encoded(content_by_sha[sha])})
        return httpx.Response(404, json={"message": "not found"})

    return httpx.MockTransport(handler)


def build_client(tmp_path, transport: httpx.MockTransport) -> TestClient:
    """Create an isolated API with a mocked GitHub transport."""

    settings = Settings(
        environment="test",
        state_file=str(tmp_path / "deployments.json"),
        audit_file=str(tmp_path / "audit.json"),
        projects_file=str(tmp_path / "projects.json"),
        reports_file=str(tmp_path / "reports.json"),
    )
    app = create_app(settings)
    app.state.repository_analysis_service = RepositoryAnalysisService(
        app.state.project_service,
        app.state.report_service,
        GitHubRepositoryClient(settings, transport=transport),
    )
    return TestClient(app)


def create_repository_project(client: TestClient) -> dict:
    """Create one valid public repository project through the API."""

    response = client.post(
        "/projects",
        json={
            "name": "Acme Widgets",
            "slug": "acme-widgets",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://github.com/acme/widgets",
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/acme/widgets",
        "https://github.com.evil.example/acme/widgets",
        "https://user:password@github.com/acme/widgets",
        "https://github.com/acme/widgets/extra",
        "https://github.com/acme/%2Fwidgets",
    ],
)
def test_repository_url_validation_rejects_unsafe_urls(value: str) -> None:
    """Only one repository on the official GitHub HTTPS host is accepted."""

    with pytest.raises(RepositoryAnalysisError):
        parse_github_repository_url(value)


def test_repository_analysis_retrieves_metadata_tree_files_and_persists_report(tmp_path) -> None:
    """A complete analysis returns evidence and stores a sanitized historical report."""

    files = {
        "package.json": '{"dependencies":{"react":"1","@opentelemetry/api":"1"},'
        '"scripts":{"build":"token=synthetic-secret vite build","start":"vite"},"main":"src/main.tsx"}',
        "Dockerfile": 'FROM node:22-alpine\nCMD ["npm", "start"]\n',
        "k8s/deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: widgets\n",
        "terraform/main.tf": 'provider "kubernetes" {}\n',
        "k8s/secret.yaml": "apiVersion: v1\nkind: Secret\ndata:\n  password: c2VjcmV0\n",
    }
    client = build_client(tmp_path, github_transport(files))
    project = create_repository_project(client)

    response = client.post(f"/projects/{project['id']}/repository-analysis", json={})

    assert response.status_code == 200
    result = response.json()
    assert result["source"] == "github_public"
    assert result["source_label"] == "acme/widgets"
    assert result["branch"] == "main"
    assert result["commit_sha"] == "commit-123"
    assert result["retrieval_status"] == "complete"
    assert result["files_retrieved"] == 4
    findings = {finding["category"]: finding for finding in result["findings"]}
    assert findings["frameworks"]["values"] == ["OpenTelemetry", "React"]
    assert findings["container_configuration"]["status"] == "detected"
    assert findings["kubernetes"]["evidence_paths"] == ["k8s/deployment.yaml"]
    assert findings["terraform"]["status"] == "detected"
    assert "synthetic-secret" not in str(findings)

    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert len(reports) == 1
    assert reports[0]["operation"] == "repository_analysis"
    assert reports[0]["status"] == "success"
    assert reports[0]["commit_sha"] == "commit-123"
    serialized = str(reports[0]["evidence"])
    assert "c2VjcmV0" not in serialized
    assert "secret.yaml" not in serialized
    assert "synthetic-secret" not in serialized


def test_repository_analysis_marks_truncated_tree_partial(tmp_path) -> None:
    """GitHub tree truncation is visible in both the response and report."""

    client = build_client(tmp_path, github_transport({"package.json": "{}"}, truncated=True))
    project = create_repository_project(client)
    response = client.post(f"/projects/{project['id']}/repository-analysis", json={})

    assert response.status_code == 200
    assert response.json()["retrieval_status"] == "partial"
    assert response.json()["tree_truncated"] is True
    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert reports[0]["status"] == "partial"


def test_repository_analysis_reports_an_invalid_requested_branch(tmp_path) -> None:
    """An inaccessible requested branch is not replaced with the default branch."""

    def override(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/repos/acme/widgets/branches/missing":
            return httpx.Response(404, json={"message": "not found"})
        return None

    client = build_client(tmp_path, github_transport({}, override=override))
    project = create_repository_project(client)
    response = client.post(
        f"/projects/{project['id']}/repository-analysis",
        json={"branch": "missing"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "The requested branch was not found."
    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert reports[0]["errors"][0]["code"] == "branch_not_found"


@pytest.mark.parametrize(
    ("status_code", "expected_status", "message"),
    [
        (404, 404, "not found"),
        (403, 429, "rate limit"),
    ],
)
def test_repository_analysis_handles_missing_and_rate_limited_repositories(
    tmp_path,
    status_code: int,
    expected_status: int,
    message: str,
) -> None:
    """Upstream access failures are safe and still leave a failure report."""

    def override(request: httpx.Request) -> httpx.Response | None:
        if request.url.path == "/repos/acme/widgets":
            return httpx.Response(status_code, json={"message": "synthetic upstream detail"})
        return None

    client = build_client(tmp_path, github_transport({}, override=override))
    project = create_repository_project(client)
    response = client.post(f"/projects/{project['id']}/repository-analysis", json={})

    assert response.status_code == expected_status
    assert message in response.json()["detail"].lower()
    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert reports[0]["status"] == "failure"
    assert reports[0]["errors"][0]["code"] in {"repository_not_found", "github_rate_limited"}
    assert "synthetic upstream detail" not in str(reports)


def test_repository_analysis_honors_file_count_and_size_limits(tmp_path) -> None:
    """Only bounded recognized content is retrieved and oversize files are reported."""

    settings = Settings(
        github_max_files=1,
        github_max_file_bytes=1024,
        github_max_total_bytes=10_000,
    )
    files = {
        "package.json": "{}",
        "Dockerfile": "FROM python:3.13",
        "requirements.txt": "x" * 2_000,
    }
    transport = github_transport(files)

    import asyncio

    result = asyncio.run(
        GitHubRepositoryClient(settings, transport=transport).retrieve("https://github.com/acme/widgets", None)
    )
    assert len(result["files"]) == 1
    assert result["partial"] is True
    assert any("file exceeds" in warning for warning in result["warnings"])
    assert any("file count" in warning.lower() for warning in result["warnings"])


def test_repository_analysis_handles_network_timeout() -> None:
    """Network timeouts become bounded, credential-free gateway errors."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic token=do-not-expose", request=request)

    import asyncio

    with pytest.raises(RepositoryAnalysisError, match="request timeout") as raised:
        asyncio.run(
            GitHubRepositoryClient(Settings(), transport=httpx.MockTransport(handler)).retrieve(
                "https://github.com/acme/widgets", None
            )
        )
    assert raised.value.status_code == 504
    assert "do-not-expose" not in str(raised.value)


def test_capability_detection_distinguishes_missing_and_unknown() -> None:
    """Complete absence is missing while incomplete retrieval remains unknown."""

    complete, missing, _ = analyze_repository_files({"requirements.txt": "fastapi==1"}, partial=False)
    partial, _, _ = analyze_repository_files({}, partial=True)
    complete_by_category = {finding.category: finding for finding in complete}
    partial_by_category = {finding.category: finding for finding in partial}
    assert complete_by_category["frameworks"].values == ["FastAPI"]
    assert complete_by_category["container_configuration"].status.value == "missing"
    assert partial_by_category["container_configuration"].status.value == "unknown"
    assert missing


def analyze_and_assess(client: TestClient, project: dict) -> tuple[dict, dict]:
    """Run the two persisted analytical stages for one fixture repository."""

    analysis_response = client.post(f"/projects/{project['id']}/repository-analysis", json={})
    assert analysis_response.status_code == 200
    analysis = analysis_response.json()
    readiness_response = client.post(
        f"/projects/{project['id']}/repository-readiness",
        json={"analysis_report_id": analysis["report_id"]},
    )
    assert readiness_response.status_code == 200
    return analysis, readiness_response.json()


def test_complete_repository_readiness_is_evidence_based_and_persisted(tmp_path) -> None:
    """Complete static evidence produces ready dimensions without runtime claims."""

    files = {
        "Dockerfile": (
            "FROM python:3.13-slim\nWORKDIR /app\nCOPY requirements.txt .\n"
            'RUN pip install -r requirements.txt\nCOPY . .\nEXPOSE 8000\nCMD ["python", "app.py"]\n'
        ),
        ".dockerignore": ".git\n.env\n",
        "docker-compose.yml": "services:\n  app:\n    build: .\n    environment:\n      MODE: test\n",
        "requirements.txt": "fastapi==1\nprometheus-client==1\nredis==1\npsycopg==1\n",
        "k8s/deployment.yaml": (
            "apiVersion: apps/v1\nkind: Deployment\nspec:\n  replicas: 2\n  template:\n    spec:\n"
            "      containers:\n        - image: ghcr.io/acme/widgets:1\n          ports:\n"
            "            - containerPort: 8000\n          resources:\n            requests:\n"
            "              cpu: 100m\n            limits:\n              cpu: 500m\n"
            "          readinessProbe:\n            httpGet:\n              path: /health\n"
            "          livenessProbe:\n            httpGet:\n              path: /health\n"
            "          env:\n            - valueFrom:\n                configMapKeyRef:\n"
            "                  name: app-config\n                  key: mode\n"
            "            - valueFrom:\n                secretKeyRef:\n                  name: app-secret\n"
            "                  key: password\n      volumes:\n        - persistentVolumeClaim:\n"
            "            claimName: app-data\n---\napiVersion: v1\nkind: Service\nspec:\n"
            "  ports:\n    - port: 80\n---\napiVersion: networking.k8s.io/v1\nkind: Ingress\n"
            "---\napiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\n"
        ),
        "terraform/main.tf": 'provider "kubernetes" {}\n',
        ".github/workflows/ci.yml": "name: CI\non: [push]\njobs: {}\n",
    }
    client = build_client(tmp_path, github_transport(files))
    project = create_repository_project(client)

    analysis, readiness = analyze_and_assess(client, project)

    signal_ids = {signal["id"] for signal in analysis["readiness_signals"]}
    assert {
        "container.base_image",
        "container.startup_command",
        "kubernetes.service",
        "kubernetes.resources_requests",
        "kubernetes.readiness_probe",
        "kubernetes.configmap_reference",
        "kubernetes.secret_reference",
        "infrastructure.terraform",
        "infrastructure.ci_cd",
        "infrastructure.observability",
    } <= signal_ids
    assert readiness["overall_status"] == "ready"
    assert {dimension["status"] for dimension in readiness["dimensions"]} == {"ready"}
    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert reports[0]["operation"] == "repository_readiness"
    assert reports[0]["status"] == "success"
    assert reports[0]["evidence"]["analysis_report_id"] == analysis["report_id"]
    assert "password" not in str(reports[0]["evidence"]).lower()


@pytest.mark.parametrize(
    ("dockerfile", "expected_finding"),
    [
        (None, "container.dockerfile.missing"),
        ("FROM python:3.13\nRUN pip install -r requirements.txt\n", "container.startup_command.missing"),
    ],
)
def test_container_readiness_rejects_missing_or_incomplete_dockerfile(
    tmp_path,
    dockerfile: str | None,
    expected_finding: str,
) -> None:
    """Dockerfile presence alone never proves container readiness."""

    files = {
        "k8s/deployment.yaml": (
            "kind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n"
            "        - image: example/app:1\n          ports:\n            - containerPort: 8000\n"
            "---\nkind: Service\n"
        ),
        "terraform/main.tf": 'provider "kubernetes" {}',
    }
    if dockerfile is not None:
        files["Dockerfile"] = dockerfile
    client = build_client(tmp_path, github_transport(files))
    project = create_repository_project(client)

    _, readiness = analyze_and_assess(client, project)
    container = next(item for item in readiness["dimensions"] if item["id"] == "containerization")
    assert container["status"] == "not_ready"
    assert expected_finding in {finding["id"] for finding in container["findings"]}
    assert all("build success" not in finding["description"].lower() for finding in container["findings"])


def test_kubernetes_readiness_is_partial_without_service_and_reports_recommendations(tmp_path) -> None:
    """Missing Service is distinct from optional resources and health probes."""

    files = {
        "Dockerfile": 'FROM busybox\nCMD ["sh"]\n',
        "k8s/deployment.yaml": (
            "kind: Deployment\nspec:\n  template:\n    spec:\n      containers:\n"
            "        - image: example/app:1\n          ports:\n            - containerPort: 8000\n"
        ),
        "terraform/main.tf": 'provider "kubernetes" {}',
    }
    client = build_client(tmp_path, github_transport(files))
    project = create_repository_project(client)

    _, readiness = analyze_and_assess(client, project)
    kubernetes = next(item for item in readiness["dimensions"] if item["id"] == "kubernetes")
    assert kubernetes["status"] == "partially_ready"
    assert "A Kubernetes Service was not detected." in kubernetes["missing_prerequisites"]
    assert "Resource requests were not detected." in kubernetes["warnings"]
    assert "Resource limits were not detected." in kubernetes["warnings"]
    assert "A readiness probe was not detected." in kubernetes["warnings"]
    assert "A liveness probe was not detected." in kubernetes["warnings"]
    assert readiness["overall_status"] == "partially_ready"


def test_partial_repository_analysis_forces_unknown_readiness(tmp_path) -> None:
    """Positive evidence cannot hide a truncated repository tree."""

    files = {
        "Dockerfile": 'FROM busybox\nCMD ["sh"]\n',
        "k8s/deployment.yaml": "kind: Deployment\nimage: example/app:1\ncontainerPort: 8000\n---\nkind: Service\n",
        "terraform/main.tf": 'provider "kubernetes" {}',
    }
    client = build_client(tmp_path, github_transport(files, truncated=True))
    project = create_repository_project(client)

    _, readiness = analyze_and_assess(client, project)

    assert readiness["overall_status"] == "unknown"
    assert {dimension["status"] for dimension in readiness["dimensions"]} == {"unknown"}
    assert all(dimension["unknown_conditions"] for dimension in readiness["dimensions"])


def test_terraform_without_kubernetes_is_only_partial_platform_integration(tmp_path) -> None:
    """Generic Terraform presence does not prove ClueStack Kubernetes integration readiness."""

    files = {
        "Dockerfile": 'FROM busybox\nCMD ["sh"]\n',
        "terraform/main.tf": 'provider "aws" {}\n',
    }
    client = build_client(tmp_path, github_transport(files))
    project = create_repository_project(client)

    _, readiness = analyze_and_assess(client, project)

    infrastructure = next(item for item in readiness["dimensions"] if item["id"] == "infrastructure")
    assert infrastructure["status"] == "partially_ready"
    assert "Kubernetes integration configuration was not detected" in infrastructure["missing_prerequisites"][0]


def test_readiness_requires_a_compatible_persisted_analysis(tmp_path) -> None:
    """Missing analysis is explicit and leaves a sanitized failure report."""

    client = build_client(tmp_path, github_transport({}))
    project = create_repository_project(client)

    response = client.post(f"/projects/{project['id']}/repository-readiness", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "Run public repository analysis before assessing readiness."
    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert reports[0]["operation"] == "repository_readiness"
    assert reports[0]["status"] == "failure"
    assert reports[0]["errors"][0]["code"] == "analysis_required"


def test_readiness_rejects_analysis_from_another_project(tmp_path) -> None:
    """A persisted analysis cannot be reused across project boundaries."""

    client = build_client(tmp_path, github_transport({"Dockerfile": 'FROM busybox\nCMD ["sh"]'}))
    first = create_repository_project(client)
    second_response = client.post(
        "/projects",
        json={
            "name": "Other Widgets",
            "slug": "other-widgets",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://github.com/acme/widgets",
        },
    )
    second = second_response.json()
    analysis = client.post(f"/projects/{first['id']}/repository-analysis", json={}).json()

    response = client.post(
        f"/projects/{second['id']}/repository-readiness",
        json={"analysis_report_id": analysis["report_id"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "The requested repository analysis report was not found for this project."


def test_readiness_rejects_outdated_analysis_and_records_failure(tmp_path) -> None:
    """A stale persisted snapshot must be refreshed instead of assessed as current."""

    client = build_client(tmp_path, github_transport({"Dockerfile": 'FROM busybox\nCMD ["sh"]'}))
    project = create_repository_project(client)
    analysis = client.post(f"/projects/{project['id']}/repository-analysis", json={}).json()
    reports_file = tmp_path / "reports.json"
    persisted = json.loads(reports_file.read_text(encoding="utf-8"))
    persisted[analysis["report_id"]]["created_at"] = "2020-01-01T00:00:00Z"
    reports_file.write_text(json.dumps(persisted), encoding="utf-8")

    response = client.post(
        f"/projects/{project['id']}/repository-readiness",
        json={"analysis_report_id": analysis["report_id"]},
    )

    assert response.status_code == 409
    assert "outdated" in response.json()["detail"]
    reports = client.get(f"/projects/{project['id']}/reports").json()
    assert reports[0]["errors"][0]["code"] == "analysis_outdated"
