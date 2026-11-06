"""Bounded, read-only analysis of public GitHub repositories."""

from __future__ import annotations

import base64
import json
import re
import time
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.config import Settings
from app.report_sanitizer import sanitize_text
from app.schemas import (
    DeploymentReportCreate,
    DeploymentReportError,
    DeploymentReportOperation,
    DeploymentReportStatus,
    ProjectMode,
    ProjectSourceType,
    RepositoryAnalysisFinding,
    RepositoryAnalysisResult,
    RepositoryAnalysisSignal,
    RepositoryAnalysisSource,
    RepositoryFindingStatus,
    RepositoryRetrievalStatus,
)
from app.services.projects import ProjectModeError, ProjectNotFoundError, ProjectService
from app.services.reports import ReportService

GITHUB_API_ORIGIN = "https://api.github.com"
SENSITIVE_PATH_PARTS = {
    ".env",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "private-key",
    "private_key",
    "id_rsa",
    "id_ed25519",
}
FRAMEWORK_MARKERS = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "react": "React",
    "vite": "Vite",
    "next": "Next.js",
    "next.js": "Next.js",
    "express": "Express",
    "nestjs": "NestJS",
    "@nestjs/core": "NestJS",
    "opentelemetry": "OpenTelemetry",
}
OBSERVABILITY_MARKERS = {
    "prometheus": "Prometheus",
    "prometheus_client": "Prometheus",
    "opentelemetry": "OpenTelemetry",
    "grafana": "Grafana",
    "loki": "Loki",
    "alloy": "Grafana Alloy",
    "alertmanager": "Alertmanager",
}


class RepositoryAnalysisError(RuntimeError):
    """Safe error raised when GitHub retrieval cannot complete."""

    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class RepositorySourceSnapshot:
    """Normalized bounded evidence supplied by one read-only source adapter."""

    source: RepositoryAnalysisSource
    source_label: str
    target: str
    files: dict[str, str]
    content_bytes_retrieved: int
    tree_entries_seen: int
    partial: bool
    warnings: list[str]
    repository_url: str | None = None
    owner: str | None = None
    repository: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    tree_truncated: bool = False


def parse_github_repository_url(value: str) -> tuple[str, str]:
    """Return owner and repository for an official GitHub HTTPS URL."""

    parsed = urlsplit(value.strip().rstrip("/").removesuffix(".git"))
    try:
        port = parsed.port
    except ValueError as exc:
        raise RepositoryAnalysisError(
            "Repository URL contains an invalid port.", status_code=422, code="invalid_repository_url"
        ) from exc
    if parsed.scheme != "https" or parsed.hostname != "github.com" or port is not None:
        raise RepositoryAnalysisError(
            "Repository URL must use the official https://github.com host.",
            status_code=422,
            code="invalid_repository_url",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RepositoryAnalysisError(
            "Repository URL cannot include credentials, a query, or a fragment.",
            status_code=422,
            code="invalid_repository_url",
        )
    parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
    if len(parts) != 2 or not all(parts) or any(part in {".", ".."} or "/" in part or "\\" in part for part in parts):
        raise RepositoryAnalysisError(
            "Repository URL must identify one GitHub owner and repository.",
            status_code=422,
            code="invalid_repository_url",
        )
    return parts[0], parts[1]


class GitHubRepositoryClient:
    """Retrieve bounded public repository data from the official GitHub API."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def retrieve(self, repository_url: str, requested_branch: str | None) -> dict[str, Any]:
        """Retrieve metadata, a bounded tree, and recognized text files."""

        owner, repository = parse_github_repository_url(repository_url)
        deadline = time.monotonic() + self._settings.github_analysis_timeout_seconds
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ClueStack-public-repository-analysis",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        timeout = httpx.Timeout(self._settings.github_api_timeout_seconds)
        async with httpx.AsyncClient(
            base_url=GITHUB_API_ORIGIN,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            metadata = await self._get_json(client, f"/repos/{quote(owner)}/{quote(repository)}", deadline)
            branch = requested_branch or _required_string(
                metadata,
                "default_branch",
                "Repository metadata omitted its default branch.",
            )
            branch_data = await self._get_json(
                client,
                f"/repos/{quote(owner)}/{quote(repository)}/branches/{quote(branch, safe='')}",
                deadline,
                missing_message="The requested branch was not found.",
                missing_code="branch_not_found",
            )
            commit = branch_data.get("commit")
            if not isinstance(commit, dict) or not isinstance(commit.get("sha"), str):
                raise RepositoryAnalysisError(
                    "GitHub returned incomplete branch metadata.", status_code=502, code="invalid_github_response"
                )
            commit_sha = commit["sha"]
            commit_data = await self._get_json(
                client,
                f"/repos/{quote(owner)}/{quote(repository)}/git/commits/{quote(commit_sha)}",
                deadline,
            )
            tree_ref = commit_data.get("tree")
            if not isinstance(tree_ref, dict) or not isinstance(tree_ref.get("sha"), str):
                raise RepositoryAnalysisError(
                    "GitHub returned incomplete commit metadata.", status_code=502, code="invalid_github_response"
                )
            tree_data = await self._get_json(
                client,
                f"/repos/{quote(owner)}/{quote(repository)}/git/trees/{quote(tree_ref['sha'])}",
                deadline,
                params={"recursive": "1"},
            )
            raw_tree = tree_data.get("tree")
            if not isinstance(raw_tree, list):
                raise RepositoryAnalysisError(
                    "GitHub returned an invalid repository tree.", status_code=502, code="invalid_github_response"
                )

            api_truncated = tree_data.get("truncated") is True
            limit_truncated = len(raw_tree) > self._settings.github_max_tree_entries
            bounded_tree = raw_tree[: self._settings.github_max_tree_entries]
            candidates: list[dict[str, Any]] = []
            warnings: list[str] = []
            for entry in bounded_tree:
                if not isinstance(entry, dict) or entry.get("type") != "blob":
                    continue
                path = entry.get("path")
                sha = entry.get("sha")
                size = entry.get("size", 0)
                if not isinstance(path, str) or not isinstance(sha, str) or not _safe_repository_path(path):
                    continue
                if _sensitive_path(path) or not _recognized_path(path):
                    continue
                if not isinstance(size, int) or size < 0 or size > self._settings.github_max_file_bytes:
                    warnings.append(f"Skipped {path}: file exceeds the per-file retrieval limit.")
                    continue
                candidates.append({"path": path, "sha": sha, "size": size})

            if len(candidates) > self._settings.github_max_files:
                warnings.append("Recognized file count exceeded the configured retrieval limit.")
            files: dict[str, str] = {}
            total_bytes = 0
            for candidate in candidates[: self._settings.github_max_files]:
                if total_bytes + candidate["size"] > self._settings.github_max_total_bytes:
                    warnings.append(
                        "Total repository content limit reached before all recognized files were retrieved."
                    )
                    break
                blob_data = await self._get_json(
                    client,
                    f"/repos/{quote(owner)}/{quote(repository)}/git/blobs/{quote(candidate['sha'])}",
                    deadline,
                )
                if blob_data.get("encoding") != "base64" or not isinstance(blob_data.get("content"), str):
                    warnings.append(f"Skipped {candidate['path']}: unsupported GitHub blob encoding.")
                    continue
                try:
                    encoded_content = "".join(blob_data["content"].split())
                    decoded = base64.b64decode(encoded_content, validate=True)
                except (ValueError, TypeError):
                    warnings.append(f"Skipped {candidate['path']}: invalid encoded content.")
                    continue
                if len(decoded) > self._settings.github_max_file_bytes:
                    warnings.append(f"Skipped {candidate['path']}: decoded file exceeds the per-file limit.")
                    continue
                if total_bytes + len(decoded) > self._settings.github_max_total_bytes:
                    warnings.append(
                        "Total repository content limit reached before all recognized files were retrieved."
                    )
                    break
                try:
                    text = decoded.decode("utf-8")
                except UnicodeDecodeError:
                    warnings.append(f"Skipped {candidate['path']}: only UTF-8 text configuration is supported.")
                    continue
                files[candidate["path"]] = text
                total_bytes += len(decoded)

        tree_truncated = api_truncated or limit_truncated
        if api_truncated:
            warnings.append("GitHub marked the recursive repository tree as truncated.")
        if limit_truncated:
            warnings.append("Repository tree exceeded the configured entry limit.")
        incomplete_files = len(files) < min(len(candidates), self._settings.github_max_files)
        return {
            "owner": owner,
            "repository": repository,
            "repository_url": f"https://github.com/{owner}/{repository}",
            "branch": branch,
            "commit_sha": commit_sha,
            "tree_truncated": tree_truncated,
            "tree_entries_seen": len(bounded_tree),
            "files": files,
            "content_bytes_retrieved": total_bytes,
            "warnings": _unique(warnings),
            "partial": tree_truncated or incomplete_files or bool(warnings),
        }

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        deadline: float,
        *,
        params: dict[str, str] | None = None,
        missing_message: str = "The public GitHub repository was not found or is not accessible.",
        missing_code: str = "repository_not_found",
    ) -> dict[str, Any]:
        """Perform one deadline-bounded request and normalize safe failures."""

        if time.monotonic() >= deadline:
            raise RepositoryAnalysisError(
                "Repository analysis exceeded its total request duration.", status_code=504, code="analysis_timeout"
            )
        remaining = deadline - time.monotonic()
        try:
            response = await client.get(
                path,
                params=params,
                timeout=min(self._settings.github_api_timeout_seconds, remaining),
            )
        except httpx.TimeoutException as exc:
            raise RepositoryAnalysisError(
                "GitHub did not respond before the request timeout.", status_code=504, code="github_timeout"
            ) from exc
        except httpx.RequestError as exc:
            raise RepositoryAnalysisError(
                "GitHub could not be reached for repository analysis.", status_code=502, code="github_unavailable"
            ) from exc
        if response.status_code == 404:
            raise RepositoryAnalysisError(missing_message, status_code=404, code=missing_code)
        if response.status_code in {403, 429}:
            raise RepositoryAnalysisError(
                "GitHub API rate limit or access policy prevented repository analysis.",
                status_code=429,
                code="github_rate_limited",
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise RepositoryAnalysisError(
                f"GitHub repository analysis failed with upstream status {response.status_code}.",
                status_code=502,
                code="github_api_error",
            )
        if len(response.content) > self._settings.github_max_api_response_bytes:
            raise RepositoryAnalysisError(
                "GitHub response exceeded the configured analysis limit.",
                status_code=502,
                code="github_response_too_large",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RepositoryAnalysisError(
                "GitHub returned an invalid JSON response.", status_code=502, code="invalid_github_response"
            ) from exc
        if not isinstance(payload, dict):
            raise RepositoryAnalysisError(
                "GitHub returned an unexpected response shape.", status_code=502, code="invalid_github_response"
            )
        return payload


class RepositoryAnalysisService:
    """Coordinate project validation, GitHub retrieval, analysis, and reporting."""

    def __init__(
        self,
        projects: ProjectService,
        reports: ReportService,
        github: GitHubRepositoryClient,
    ) -> None:
        self._projects = projects
        self._reports = reports
        self._github = github

    async def analyze(self, project_id: str, branch: str | None) -> RepositoryAnalysisResult:
        """Analyze an existing public GitHub project and persist a sanitized report."""

        project = self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError("Project not found")
        if (
            project.mode is not ProjectMode.github_repository
            or project.source_type is not ProjectSourceType.github_public_readonly
            or not project.repository_url
        ):
            raise ProjectModeError("Repository analysis requires a public GitHub repository project")

        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        try:
            retrieved = await self._github.retrieve(project.repository_url, branch)
        except RepositoryAnalysisError as exc:
            completed_at = datetime.now(UTC)
            self._reports.create(
                DeploymentReportCreate(
                    project_id=project.id,
                    operation=DeploymentReportOperation.repository_analysis,
                    status=DeploymentReportStatus.failure,
                    summary="Public GitHub repository analysis failed.",
                    target="github_public_readonly",
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=_duration_ms(started_clock),
                    evidence={"repository_url": project.repository_url, "requested_branch": branch},
                    errors=[DeploymentReportError(code=exc.code, message=str(exc))],
                )
            )
            raise

        snapshot = RepositorySourceSnapshot(
            source=RepositoryAnalysisSource.github_public,
            source_label=f"{retrieved['owner']}/{retrieved['repository']}",
            target="github_public_readonly",
            repository_url=retrieved["repository_url"],
            owner=retrieved["owner"],
            repository=retrieved["repository"],
            branch=retrieved["branch"],
            commit_sha=retrieved["commit_sha"],
            tree_truncated=retrieved["tree_truncated"],
            tree_entries_seen=retrieved["tree_entries_seen"],
            files=retrieved["files"],
            content_bytes_retrieved=retrieved["content_bytes_retrieved"],
            warnings=retrieved["warnings"],
            partial=retrieved["partial"],
        )
        return self.analyze_snapshot(project.id, snapshot, started_at, started_clock)

    def analyze_snapshot(
        self,
        project_id: str,
        snapshot: RepositorySourceSnapshot,
        started_at: datetime,
        started_clock: float,
    ) -> RepositoryAnalysisResult:
        """Run the shared analyzer and persist only sanitized derived evidence."""

        findings, missing_prerequisites, analysis_warnings = analyze_repository_files(
            snapshot.files, partial=snapshot.partial
        )
        readiness_signals = collect_readiness_signals(snapshot.files)
        warnings = _unique([*snapshot.warnings, *analysis_warnings])
        retrieval_status = (
            RepositoryRetrievalStatus.partial
            if snapshot.partial or analysis_warnings
            else RepositoryRetrievalStatus.complete
        )
        completed_at = datetime.now(UTC)
        evidence = {
            "source": snapshot.source.value,
            "source_label": snapshot.source_label,
            "repository_url": snapshot.repository_url,
            "owner": snapshot.owner,
            "repository": snapshot.repository,
            "branch": snapshot.branch,
            "commit_sha": snapshot.commit_sha,
            "retrieval_status": retrieval_status.value,
            "tree_truncated": snapshot.tree_truncated,
            "tree_entries_seen": snapshot.tree_entries_seen,
            "files_retrieved": len(snapshot.files),
            "content_bytes_retrieved": snapshot.content_bytes_retrieved,
            "findings": [finding.model_dump(mode="json") for finding in findings],
            "readiness_signals": [signal.model_dump(mode="json") for signal in readiness_signals],
            "missing_prerequisites": missing_prerequisites,
            "warnings": warnings,
        }
        report = self._reports.create(
            DeploymentReportCreate(
                project_id=project_id,
                operation=DeploymentReportOperation.repository_analysis,
                status=(
                    DeploymentReportStatus.partial
                    if retrieval_status is RepositoryRetrievalStatus.partial
                    else DeploymentReportStatus.success
                ),
                summary=(
                    f"{_source_summary(snapshot.source)} completed with incomplete evidence."
                    if retrieval_status is RepositoryRetrievalStatus.partial
                    else f"{_source_summary(snapshot.source)} completed."
                ),
                target=snapshot.target,
                commit_sha=snapshot.commit_sha,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=_duration_ms(started_clock),
                evidence=evidence,
            )
        )
        return RepositoryAnalysisResult(
            project_id=project_id,
            source=snapshot.source,
            source_label=snapshot.source_label,
            repository_url=snapshot.repository_url,
            owner=snapshot.owner,
            repository=snapshot.repository,
            branch=snapshot.branch,
            commit_sha=snapshot.commit_sha,
            retrieval_status=retrieval_status,
            tree_truncated=snapshot.tree_truncated,
            tree_entries_seen=snapshot.tree_entries_seen,
            files_retrieved=len(snapshot.files),
            content_bytes_retrieved=snapshot.content_bytes_retrieved,
            findings=findings,
            readiness_signals=readiness_signals,
            missing_prerequisites=missing_prerequisites,
            warnings=warnings,
            errors=[],
            report_id=report.id,
            analyzed_at=completed_at,
        )


def analyze_repository_files(
    files: dict[str, str], *, partial: bool
) -> tuple[list[RepositoryAnalysisFinding], list[str], list[str]]:
    """Return bounded, evidence-backed capability findings for retrieved files."""

    detected: dict[str, dict[str, set[str]]] = defaultdict(lambda: {"values": set(), "paths": set()})
    unsupported_paths: set[str] = set()
    warnings: list[str] = []

    def add(category: str, value: str, path: str) -> None:
        detected[category]["values"].add(sanitize_text(value)[:200])
        detected[category]["paths"].add(path)

    for path, content in files.items():
        lower_path = path.lower()
        basename = PurePosixPath(path).name.lower()
        if basename == "package.json":
            add("dependency_manifests", "package.json", path)
            add("languages", "JavaScript/TypeScript", path)
            try:
                payload = json.loads(content)
                if not isinstance(payload, dict):
                    raise ValueError
                dependencies = {
                    **(payload.get("dependencies") if isinstance(payload.get("dependencies"), dict) else {}),
                    **(payload.get("devDependencies") if isinstance(payload.get("devDependencies"), dict) else {}),
                }
                for dependency in dependencies:
                    for marker, label in FRAMEWORK_MARKERS.items():
                        if marker in str(dependency).lower():
                            add("frameworks", label, path)
                    observability = _marker_match(str(dependency), OBSERVABILITY_MARKERS)
                    if observability:
                        add("observability", observability, path)
                scripts = payload.get("scripts")
                if isinstance(scripts, dict):
                    for name in ("build", "start", "dev"):
                        command = scripts.get(name)
                        if isinstance(command, str):
                            add("build_commands", f"npm run {name}: {command}", path)
                for key in ("main", "module"):
                    if isinstance(payload.get(key), str):
                        add("entry_points", payload[key], path)
            except (ValueError, json.JSONDecodeError):
                warnings.append(f"Could not parse {path} as JSON.")
                unsupported_paths.add(path)
        elif basename == "pyproject.toml":
            add("dependency_manifests", "pyproject.toml", path)
            add("languages", "Python", path)
            try:
                payload = tomllib.loads(content)
                project = payload.get("project", {}) if isinstance(payload, dict) else {}
                dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
                for dependency in dependencies if isinstance(dependencies, list) else []:
                    _add_dependency_markers(str(dependency), path, add)
                scripts = project.get("scripts", {}) if isinstance(project, dict) else {}
                if isinstance(scripts, dict):
                    for name, entry in scripts.items():
                        if isinstance(entry, str):
                            add("entry_points", f"{name} = {entry}", path)
                build_system = payload.get("build-system", {}) if isinstance(payload, dict) else {}
                backend = build_system.get("build-backend") if isinstance(build_system, dict) else None
                if isinstance(backend, str):
                    add("build_commands", f"Python build backend: {backend}", path)
            except (tomllib.TOMLDecodeError, TypeError):
                warnings.append(f"Could not parse {path} as TOML.")
                unsupported_paths.add(path)
        elif basename.startswith("requirements") and basename.endswith(".txt"):
            add("dependency_manifests", basename, path)
            add("languages", "Python", path)
            for line in content.splitlines():
                if line.strip() and not line.lstrip().startswith(("#", "-")):
                    _add_dependency_markers(line, path, add)
        elif basename == "dockerfile" or basename.startswith("dockerfile."):
            add("container_configuration", "Dockerfile", path)
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.upper().startswith("FROM "):
                    add("container_configuration", stripped[:200], path)
                if stripped.upper().startswith(("CMD ", "ENTRYPOINT ")):
                    add("entry_points", stripped[:200], path)
        elif _is_compose_path(lower_path):
            add("container_configuration", "Docker Compose", path)
        elif lower_path.endswith(".tf"):
            add("terraform", "Terraform configuration", path)
            for provider in re.findall(r'\bprovider\s+"([a-zA-Z0-9_-]+)"', content):
                add("terraform", f"Provider: {provider}", path)
        elif lower_path.endswith((".yaml", ".yml")):
            kinds = set(re.findall(r"(?im)^\s*kind\s*:\s*['\"]?([a-z][a-z0-9.-]*)", content))
            for kind in sorted(kinds, key=str.lower):
                add("kubernetes", kind, path)
            lowered = content.lower()
            for marker, label in OBSERVABILITY_MARKERS.items():
                if marker in lowered:
                    add("observability", label, path)

    categories = (
        "languages",
        "frameworks",
        "dependency_manifests",
        "entry_points",
        "build_commands",
        "container_configuration",
        "kubernetes",
        "terraform",
        "observability",
    )
    findings: list[RepositoryAnalysisFinding] = []
    for category in categories:
        item = detected.get(category)
        if item:
            findings.append(
                RepositoryAnalysisFinding(
                    category=category,
                    status=RepositoryFindingStatus.detected,
                    values=sorted(item["values"]),
                    evidence_paths=sorted(item["paths"]),
                    detail="Detected from retrieved repository configuration.",
                )
            )
        else:
            findings.append(
                RepositoryAnalysisFinding(
                    category=category,
                    status=RepositoryFindingStatus.unknown if partial else RepositoryFindingStatus.missing,
                    detail=(
                        "The incomplete retrieval cannot establish whether this capability exists."
                        if partial
                        else "No evidence was found in the retrieved recognized files."
                    ),
                )
            )

    if unsupported_paths:
        findings.append(
            RepositoryAnalysisFinding(
                category="unparsed_configuration",
                status=RepositoryFindingStatus.unsupported,
                evidence_paths=sorted(unsupported_paths),
                detail="Recognized configuration was retrieved but could not be parsed safely.",
            )
        )

    missing_prerequisites: list[str] = []
    if "container_configuration" not in detected and "kubernetes" not in detected:
        missing_prerequisites.append("No Docker or Kubernetes deployment configuration was detected.")
    if "entry_points" not in detected:
        missing_prerequisites.append("No application entry point was identifiable from recognized files.")
    if "build_commands" not in detected:
        missing_prerequisites.append("No explicit build command was identifiable from recognized files.")
    return findings, missing_prerequisites, _unique(warnings)


def collect_readiness_signals(files: dict[str, str]) -> list[RepositoryAnalysisSignal]:
    """Derive bounded boolean readiness evidence without executing repository content."""

    paths_by_signal: dict[str, set[str]] = defaultdict(set)
    details: dict[str, str] = {}

    def detect(signal_id: str, path: str, detail: str) -> None:
        paths_by_signal[signal_id].add(path)
        details.setdefault(signal_id, detail)

    for path, content in files.items():
        lower_path = path.lower()
        basename = PurePosixPath(lower_path).name
        if basename == "dockerfile" or basename.startswith("dockerfile."):
            detect("container.dockerfile", path, "A Dockerfile was retrieved.")
            instructions = _docker_instructions(content)
            if "FROM" in instructions:
                detect("container.base_image", path, "The Dockerfile declares a base image.")
            if instructions & {"RUN", "COPY", "ADD", "WORKDIR"}:
                detect("container.build_instruction", path, "The Dockerfile contains build-stage instructions.")
            if instructions & {"CMD", "ENTRYPOINT"}:
                detect("container.startup_command", path, "The Dockerfile declares CMD or ENTRYPOINT.")
            if re.search(r"(?im)^\s*RUN\s+.*\b(?:pip|uv|poetry|npm|pnpm|yarn)\b", content):
                detect(
                    "container.dependency_install",
                    path,
                    "The Dockerfile contains a recognizable dependency installation command.",
                )
            if "EXPOSE" in instructions:
                detect("container.port", path, "The Dockerfile declares an exposed application port.")
            if instructions & {"ARG", "ENV"}:
                detect(
                    "infrastructure.environment_variables",
                    path,
                    "Build or runtime environment inputs are declared.",
                )
        elif basename == ".dockerignore":
            detect("container.dockerignore", path, "A .dockerignore file was retrieved.")
        elif _is_compose_path(lower_path):
            detect("container.compose", path, "Docker Compose configuration was retrieved.")
            if re.search(r"(?im)^\s*environment\s*:", content):
                detect("infrastructure.environment_variables", path, "Compose environment inputs are declared.")
        elif lower_path.endswith(".tf"):
            detect("infrastructure.terraform", path, "Terraform configuration was retrieved.")
        elif _is_github_workflow_path(lower_path):
            detect("infrastructure.ci_cd", path, "A GitHub Actions workflow was retrieved.")
        elif lower_path.endswith((".yaml", ".yml")):
            kinds = {kind.lower() for kind in re.findall(r"(?im)^\s*kind\s*:\s*['\"]?([a-z][a-z0-9.-]*)", content)}
            if kinds:
                detect("infrastructure.kubernetes", path, "Kubernetes configuration was retrieved.")
            if "deployment" in kinds:
                detect("kubernetes.deployment", path, "A Kubernetes Deployment manifest was detected.")
            if "service" in kinds:
                detect("kubernetes.service", path, "A Kubernetes Service manifest was detected.")
            if "ingress" in kinds:
                detect("kubernetes.ingress", path, "A Kubernetes Ingress manifest was detected.")
            if "horizontalpodautoscaler" in kinds:
                detect("kubernetes.hpa", path, "A HorizontalPodAutoscaler manifest was detected.")
            if "persistentvolumeclaim" in kinds or re.search(r"(?i)\bclaimName\s*:", content):
                detect("kubernetes.persistent_storage", path, "Persistent storage configuration was detected.")
            kubernetes_patterns = {
                "kubernetes.image": r"(?im)^\s*(?:-\s*)?image\s*:\s*\S+",
                "kubernetes.container_port": r"(?im)^\s*(?:-\s*)?containerPort\s*:\s*\d+",
                "kubernetes.replicas": r"(?im)^\s*replicas\s*:\s*\d+",
                "kubernetes.resources_requests": r"(?im)^\s*requests\s*:",
                "kubernetes.resources_limits": r"(?im)^\s*limits\s*:",
                "kubernetes.readiness_probe": r"(?im)^\s*readinessProbe\s*:",
                "kubernetes.liveness_probe": r"(?im)^\s*livenessProbe\s*:",
                "kubernetes.configmap_reference": r"(?i)\b(?:configMapKeyRef|configMapRef|configMap)\s*:",
                "kubernetes.secret_reference": r"(?i)\b(?:secretKeyRef|secretRef|secretName)\s*:",
            }
            signal_details = {
                "kubernetes.image": "A workload container image reference was detected.",
                "kubernetes.container_port": "A workload container port was detected.",
                "kubernetes.replicas": "A replica count was detected.",
                "kubernetes.resources_requests": "Resource requests were detected.",
                "kubernetes.resources_limits": "Resource limits were detected.",
                "kubernetes.readiness_probe": "A readiness probe was detected.",
                "kubernetes.liveness_probe": "A liveness probe was detected.",
                "kubernetes.configmap_reference": "A ConfigMap reference was detected.",
                "kubernetes.secret_reference": "A Secret reference was detected without retrieving secret values.",
            }
            for signal_id, pattern in kubernetes_patterns.items():
                if re.search(pattern, content):
                    detect(signal_id, path, signal_details[signal_id])
            if re.search(r"(?im)^\s*env(?:From)?\s*:", content):
                detect("infrastructure.environment_variables", path, "Kubernetes environment inputs are declared.")

        lowered_content = content.lower()
        if any(marker in lowered_content for marker in ("postgres", "mysql", "mongodb", "sqlite", "psycopg")):
            detect("infrastructure.database", path, "A database dependency indicator was detected.")
        if "redis" in lowered_content:
            detect("infrastructure.cache", path, "A cache dependency indicator was detected.")
        if any(marker in lowered_content for marker in OBSERVABILITY_MARKERS):
            detect("infrastructure.observability", path, "Observability configuration or a dependency was detected.")
        if re.search(r"(?i)\b(?:configMap|secretKeyRef|secretRef|secretName)\s*:", content):
            detect(
                "infrastructure.configuration_dependencies",
                path,
                "External configuration references were detected.",
            )

    return [
        RepositoryAnalysisSignal(
            id=signal_id,
            evidence_paths=sorted(paths),
            detail=details[signal_id],
        )
        for signal_id, paths in sorted(paths_by_signal.items())
    ]


def _recognized_path(path: str) -> bool:
    lower = path.lower()
    basename = PurePosixPath(lower).name
    return (
        basename == "package.json"
        or basename == "pyproject.toml"
        or (basename.startswith("requirements") and basename.endswith(".txt"))
        or basename == "dockerfile"
        or basename.startswith("dockerfile.")
        or basename == ".dockerignore"
        or _is_compose_path(lower)
        or lower.endswith(".tf")
        or _is_github_workflow_path(lower)
        or (_looks_like_kubernetes_path(lower) and lower.endswith((".yaml", ".yml")))
    )


def is_recognized_repository_path(path: str) -> bool:
    """Return whether a safe text path is useful to the shared analyzer."""

    return _recognized_path(path)


def _is_compose_path(path: str) -> bool:
    return PurePosixPath(path).name in {
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
    }


def _looks_like_kubernetes_path(path: str) -> bool:
    parts = {part.lower() for part in PurePosixPath(path).parts}
    basename = PurePosixPath(path).stem.lower()
    return bool(parts & {"k8s", "kubernetes", "manifests", "deploy", "deployments"}) or basename in {
        "deployment",
        "service",
        "statefulset",
        "daemonset",
        "kustomization",
    }


def _is_github_workflow_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return len(pure.parts) >= 3 and pure.parts[0:2] == (".github", "workflows") and pure.suffix in {".yml", ".yaml"}


def _docker_instructions(content: str) -> set[str]:
    return {
        match.group(1).upper()
        for match in re.finditer(
            r"(?im)^\s*(FROM|RUN|COPY|ADD|WORKDIR|CMD|ENTRYPOINT|EXPOSE|ARG|ENV)\b",
            content,
        )
    }


def _safe_repository_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        bool(path)
        and not path.startswith(("/", "\\"))
        and re.match(r"^[A-Za-z]:/", path) is None
        and "\\" not in path
        and not any(ord(character) < 32 for character in path)
        and all(part not in {"", ".", ".."} for part in pure.parts)
    )


def is_safe_repository_path(path: str) -> bool:
    """Return whether a path is relative, normalized, and traversal-free."""

    return _safe_repository_path(path)


def _sensitive_path(path: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(path).parts]
    return sanitize_text(path) != path or any(
        part in SENSITIVE_PATH_PARTS
        or PurePosixPath(part).stem in SENSITIVE_PATH_PARTS
        or part.endswith((".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".der"))
        or part.startswith(".env")
        for part in parts
    )


def is_sensitive_repository_path(path: str) -> bool:
    """Return whether a path could identify credential or key material."""

    return _sensitive_path(path)


def _add_dependency_markers(dependency: str, path: str, add) -> None:
    lowered = dependency.lower()
    for marker, label in FRAMEWORK_MARKERS.items():
        if marker in lowered:
            add("frameworks", label, path)
    observability = _marker_match(lowered, OBSERVABILITY_MARKERS)
    if observability:
        add("observability", observability, path)


def _marker_match(value: str, markers: dict[str, str]) -> str | None:
    lowered = value.lower()
    return next((label for marker, label in markers.items() if marker in lowered), None)


def _required_string(payload: dict[str, Any], key: str, message: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RepositoryAnalysisError(message, status_code=502, code="invalid_github_response")
    return value


def _duration_ms(started_clock: float) -> int:
    return max(0, min(round((time.perf_counter() - started_clock) * 1000), 86_400_000))


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _source_summary(source: RepositoryAnalysisSource) -> str:
    return (
        "Public GitHub repository analysis"
        if source is RepositoryAnalysisSource.github_public
        else "Local repository analysis"
    )
