"""Bounded adapter for browser-selected local repository evidence."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import PurePosixPath

from app.config import Settings
from app.schemas import (
    DeploymentReportCreate,
    DeploymentReportError,
    DeploymentReportOperation,
    DeploymentReportStatus,
    LocalRepositoryAnalysisRequest,
    ProjectMode,
    ProjectSourceType,
    RepositoryAnalysisResult,
    RepositoryAnalysisSource,
)
from app.services.projects import ProjectModeError, ProjectNotFoundError, ProjectService
from app.services.reports import ReportService
from app.services.repository_analysis import (
    RepositoryAnalysisService,
    RepositorySourceSnapshot,
    is_recognized_repository_path,
    is_safe_repository_path,
    is_sensitive_repository_path,
)

EXCLUDED_DIRECTORY_PARTS = {
    ".git",
    ".cache",
    ".mypy_cache",
    ".next",
    ".nuxt",
    ".pytest_cache",
    ".terraform",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "env",
    "node_modules",
    "out",
    "target",
    "vendor",
    "venv",
}


class LocalRepositoryAnalysisError(RuntimeError):
    """Safe validation or limit error for a local evidence submission."""

    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class LocalRepositoryAnalysisService:
    """Validate local text evidence and pass it to the shared analyzer."""

    def __init__(
        self,
        settings: Settings,
        projects: ProjectService,
        reports: ReportService,
        analyzer: RepositoryAnalysisService,
    ) -> None:
        self._settings = settings
        self._projects = projects
        self._reports = reports
        self._analyzer = analyzer

    def analyze(self, project_id: str, payload: LocalRepositoryAnalysisRequest) -> RepositoryAnalysisResult:
        """Analyze a bounded browser submission without accessing the server filesystem."""

        project = self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError("Project not found")
        if project.mode is not ProjectMode.local or project.source_type is not ProjectSourceType.manual:
            raise ProjectModeError("Local repository analysis requires a local project")

        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        try:
            snapshot = self._build_snapshot(payload, started_clock)
            return self._analyzer.analyze_snapshot(project.id, snapshot, started_at, started_clock)
        except LocalRepositoryAnalysisError as exc:
            completed_at = datetime.now(UTC)
            self._reports.create(
                DeploymentReportCreate(
                    project_id=project.id,
                    operation=DeploymentReportOperation.repository_analysis,
                    status=DeploymentReportStatus.failure,
                    summary="Local repository analysis failed before evidence could be assessed.",
                    target="local_user_submission",
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=_duration_ms(started_clock),
                    evidence={"source": RepositoryAnalysisSource.local_upload.value},
                    errors=[DeploymentReportError(code=exc.code, message=str(exc))],
                )
            )
            raise

    def _build_snapshot(
        self,
        payload: LocalRepositoryAnalysisRequest,
        started_clock: float,
    ) -> RepositorySourceSnapshot:
        if len(payload.files) > self._settings.github_max_files:
            raise LocalRepositoryAnalysisError(
                "Local repository submission exceeded the configured file-count limit.",
                status_code=413,
                code="local_file_count_limit",
            )

        accepted: dict[str, str] = {}
        submitted_paths: set[str] = set()
        total_bytes = 0
        excluded_count = 0
        unsupported_count = 0
        binary_count = 0
        for item in payload.files:
            self._check_deadline(started_clock)
            path = item.path
            if not is_safe_repository_path(path) or len(path.encode("utf-8")) > 512:
                raise LocalRepositoryAnalysisError(
                    "Local repository submission contains an unsafe relative path.",
                    status_code=422,
                    code="unsafe_local_path",
                )
            if path in submitted_paths:
                raise LocalRepositoryAnalysisError(
                    "Local repository submission contains duplicate relative paths.",
                    status_code=422,
                    code="duplicate_local_path",
                )
            submitted_paths.add(path)
            parts = {part.lower() for part in PurePosixPath(path).parts}
            if parts & EXCLUDED_DIRECTORY_PARTS or is_sensitive_repository_path(path):
                excluded_count += 1
                continue
            if not is_recognized_repository_path(path):
                unsupported_count += 1
                continue
            content_bytes = item.content.encode("utf-8")
            if b"\x00" in content_bytes:
                binary_count += 1
                continue
            if len(content_bytes) > self._settings.github_max_file_bytes:
                raise LocalRepositoryAnalysisError(
                    "A submitted file exceeded the configured per-file analysis limit.",
                    status_code=413,
                    code="local_file_size_limit",
                )
            if total_bytes + len(content_bytes) > self._settings.github_max_total_bytes:
                raise LocalRepositoryAnalysisError(
                    "Local repository content exceeded the configured total analysis limit.",
                    status_code=413,
                    code="local_total_size_limit",
                )
            accepted[path] = item.content
            total_bytes += len(content_bytes)

        warnings: list[str] = []
        if excluded_count:
            warnings.append(f"Excluded {excluded_count} sensitive or generated path(s) from analysis.")
        if unsupported_count:
            warnings.append(
                f"Ignored {unsupported_count} unsupported file(s); only recognized configuration is analyzed."
            )
        if binary_count:
            warnings.append(f"Skipped {binary_count} binary or non-text file(s).")
        if not payload.submission_complete:
            warnings.append("The browser marked this folder submission as incomplete.")
        self._check_deadline(started_clock)
        return RepositorySourceSnapshot(
            source=RepositoryAnalysisSource.local_upload,
            source_label=payload.source_label,
            target="local_user_submission",
            files=accepted,
            content_bytes_retrieved=total_bytes,
            tree_entries_seen=len(payload.files),
            partial=not payload.submission_complete or binary_count > 0,
            warnings=warnings,
        )

    def _check_deadline(self, started_clock: float) -> None:
        if time.perf_counter() - started_clock > self._settings.local_analysis_timeout_seconds:
            raise LocalRepositoryAnalysisError(
                "Local repository analysis exceeded its processing time limit.",
                status_code=504,
                code="local_analysis_timeout",
            )


def _duration_ms(started_clock: float) -> int:
    return max(0, min(round((time.perf_counter() - started_clock) * 1000), 86_400_000))
