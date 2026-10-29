"""Safe container-build orchestration with no host-Docker execution path."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from app.config import Settings
from app.report_sanitizer import sanitize_text
from app.schemas import (
    ContainerBuildCreateRequest,
    ContainerBuildEligibilityRequest,
    ContainerBuildEligibilityResult,
    ContainerBuildFinding,
    ContainerBuildFindingStatus,
    ContainerBuildLogsResponse,
    ContainerBuildRecord,
    ContainerBuildStatus,
    DeploymentReportCreate,
    DeploymentReportError,
    DeploymentReportOperation,
    DeploymentReportRecord,
    DeploymentReportStatus,
    ProjectMode,
    RepositoryAnalysisSource,
)
from app.services.build_workers import (
    BuildWorkerProvider,
    WorkerJobStatus,
    WorkerRequest,
    WorkerResourcePolicy,
)
from app.services.projects import ProjectNotFoundError, ProjectService
from app.services.reports import ReportService
from app.services.repository_analysis import is_safe_repository_path

DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
FINAL_STATUSES = {
    ContainerBuildStatus.succeeded,
    ContainerBuildStatus.failed,
    ContainerBuildStatus.cancelled,
    ContainerBuildStatus.rejected,
}


class ContainerBuildError(RuntimeError):
    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class BuildExecutionPolicy:
    network_disabled: bool
    privileged: bool
    host_docker_socket: bool
    host_filesystem_mounts: bool
    credentials_available: bool
    cpu_limit: float
    memory_megabytes: int
    storage_megabytes: int
    process_limit: int
    timeout_seconds: int
    max_log_bytes: int


@dataclass(frozen=True)
class PreparedBuildSource:
    """Verified metadata only; host snapshot paths never cross the worker contract."""

    snapshot_id: str
    project_id: str
    integrity_hash: str
    format_version: int
    submission_complete: bool


@dataclass(frozen=True)
class BuildExecutionRequest:
    build_id: str
    project_id: str
    correlation_id: str
    source: PreparedBuildSource
    dockerfile_path: str
    context_path: str
    image_name: str
    image_tag: str
    policy: BuildExecutionPolicy


@dataclass(frozen=True)
class BuildExecutionResult:
    exit_status: int
    logs: str
    image_digest: str | None = None
    failure_reason: str | None = None
    timed_out: bool = False
    worker_job_id: str | None = None
    cleanup_status: str | None = None
    logs_truncated: bool = False


class BuildSourceProvider(Protocol):
    def availability(
        self,
        project_id: str,
        source: RepositoryAnalysisSource,
        snapshot_id: str | None,
        expected_identity: str | None,
        analysis_report_id: str | None,
    ) -> tuple[bool, str]: ...

    def prepare(
        self,
        project_id: str,
        source: RepositoryAnalysisSource,
        snapshot_id: str,
        expected_identity: str | None,
        analysis_report_id: str | None,
    ) -> PreparedBuildSource: ...

    def cleanup(self, prepared: PreparedBuildSource) -> None: ...


class BuildExecutor(Protocol):
    @property
    def available(self) -> bool: ...

    def execute(self, request: BuildExecutionRequest) -> BuildExecutionResult: ...

    def cancel(self, build_id: str) -> bool: ...


class UnavailableBuildSourceProvider:
    """Production-safe default until immutable source snapshots are implemented."""

    def availability(self, project_id, source, snapshot_id, expected_identity, analysis_report_id) -> tuple[bool, str]:
        if source is RepositoryAnalysisSource.local_upload:
            return False, "Local analysis retains configuration evidence, not a buildable immutable source snapshot."
        return False, "GitHub analysis retains bounded configuration evidence, not a complete verified source snapshot."

    def prepare(self, project_id, source, snapshot_id, expected_identity, analysis_report_id) -> PreparedBuildSource:
        raise ContainerBuildError(
            "A secure immutable source snapshot provider is not configured.",
            status_code=409,
            code="source_snapshot_unavailable",
        )

    def cleanup(self, prepared: PreparedBuildSource) -> None:
        return None


class DisabledBuildExecutor:
    """Production-safe default; deliberately never invokes Docker or a shell."""

    @property
    def available(self) -> bool:
        return False

    def execute(self, request: BuildExecutionRequest) -> BuildExecutionResult:
        raise ContainerBuildError(
            "No isolated container build worker is configured.",
            status_code=409,
            code="isolated_builder_unavailable",
        )

    def cancel(self, build_id: str) -> bool:
        return False

    @property
    def availability_reason(self) -> str:
        return "No verified isolated container build worker is configured."


class WorkerBuildExecutor:
    """Translate coordinator requests to the internal worker-provider contract."""

    def __init__(self, provider: BuildWorkerProvider) -> None:
        self._provider = provider

    @property
    def available(self) -> bool:
        return self._provider.available

    @property
    def availability_reason(self) -> str:
        return self._provider.availability_reason

    def execute(self, request: BuildExecutionRequest) -> BuildExecutionResult:
        if not self.available:
            raise ContainerBuildError(
                self.availability_reason,
                status_code=409,
                code="isolated_builder_unavailable",
            )
        job_id = str(uuid4())
        worker_request = WorkerRequest(
            build_id=request.build_id,
            project_id=request.project_id,
            snapshot_id=request.source.snapshot_id,
            snapshot_integrity_hash=request.source.integrity_hash,
            snapshot_format_version=request.source.format_version,
            context_path=request.context_path,
            dockerfile_path=request.dockerfile_path,
            image_name=request.image_name,
            image_tag=request.image_tag,
            resources=WorkerResourcePolicy(
                cpu_limit=request.policy.cpu_limit,
                memory_megabytes=request.policy.memory_megabytes,
                storage_megabytes=request.policy.storage_megabytes,
                process_limit=request.policy.process_limit,
                timeout_seconds=request.policy.timeout_seconds,
                max_log_bytes=request.policy.max_log_bytes,
            ),
            network_policy="disabled",
            execution_timeout_seconds=request.policy.timeout_seconds,
            correlation_id=request.correlation_id,
        )
        result, logs, truncated = self._provider.submit(worker_request, job_id)
        return BuildExecutionResult(
            exit_status=result.exit_code if result.exit_code is not None else 1,
            logs=logs,
            image_digest=result.image_digest,
            failure_reason=result.failure_message,
            timed_out=result.status is WorkerJobStatus.timed_out,
            worker_job_id=result.job_id,
            cleanup_status=result.cleanup_status.value,
            logs_truncated=truncated,
        )

    def cancel(self, build_id: str) -> bool:
        return self._provider.cancel(build_id)


class ContainerBuildService:
    """Validate, execute through injected isolation, and persist final build evidence."""

    def __init__(
        self,
        settings: Settings,
        projects: ProjectService,
        reports: ReportService,
        source_provider: BuildSourceProvider,
        executor: BuildExecutor,
    ) -> None:
        self._settings = settings
        self._projects = projects
        self._reports = reports
        self._source_provider = source_provider
        self._executor = executor
        self._active_keys: set[tuple[str, str, str, str]] = set()
        self._active_lock = threading.Lock()

    def eligibility(
        self,
        project_id: str,
        payload: ContainerBuildEligibilityRequest,
    ) -> ContainerBuildEligibilityResult:
        project = self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError("Project not found")

        findings: list[ContainerBuildFinding] = []
        analysis = self._compatible_report(
            project_id, payload.analysis_report_id, DeploymentReportOperation.repository_analysis
        )
        readiness = self._compatible_report(
            project_id,
            payload.readiness_report_id,
            DeploymentReportOperation.repository_readiness,
        )
        source = self._analysis_source(analysis, project.mode) if analysis else None

        if analysis is None:
            findings.append(_blocker("analysis.missing", "A compatible repository analysis report is required."))
        else:
            age = (datetime.now(UTC) - analysis.created_at).total_seconds()
            if age > self._settings.repository_analysis_max_age_seconds:
                findings.append(_blocker("analysis.stale", "The selected repository analysis is stale."))
            else:
                findings.append(_passed("analysis.compatible", "The selected repository analysis is current."))

        if readiness is None:
            findings.append(_blocker("readiness.missing", "A compatible readiness report is required."))
        elif readiness.evidence.get("analysis_report_id") != payload.analysis_report_id:
            findings.append(_blocker("readiness.mismatch", "Readiness does not correspond to the selected analysis."))
        elif (
            datetime.now(UTC) - readiness.created_at
        ).total_seconds() > self._settings.repository_analysis_max_age_seconds:
            findings.append(_blocker("readiness.stale", "The selected readiness assessment is stale."))
        elif analysis and readiness.created_at < analysis.created_at:
            findings.append(_blocker("readiness.stale", "Readiness predates the selected repository analysis."))
        elif not _container_dimension_ready(readiness):
            findings.append(_blocker("readiness.container", "Containerization readiness is not ready."))
        else:
            findings.append(_passed("readiness.container", "Containerization readiness is ready."))

        dockerfile_paths = _signal_paths(analysis, "container.dockerfile") if analysis else []
        if not is_safe_repository_path(payload.dockerfile_path):
            findings.append(_blocker("dockerfile.path", "Dockerfile path must be a safe relative repository path."))
        elif payload.dockerfile_path not in dockerfile_paths:
            findings.append(
                _blocker(
                    "dockerfile.missing",
                    "The selected Dockerfile was not present in repository analysis evidence.",
                    dockerfile_paths,
                )
            )
        else:
            findings.append(_passed("dockerfile.detected", "The selected Dockerfile is supported.", dockerfile_paths))

        if payload.context_path != ".":
            findings.append(
                _blocker("context.unsupported", "The initial build engine supports only repository-root context '.'.")
            )
        else:
            findings.append(_passed("context.supported", "Repository-root build context is selected."))

        expected_identity = (
            analysis.commit_sha if analysis and source is RepositoryAnalysisSource.github_public else None
        )
        if not payload.source_snapshot_id:
            findings.append(_blocker("snapshot.required", "An immutable source snapshot identifier is required."))
        elif source:
            available, reason = self._source_provider.availability(
                project_id, source, payload.source_snapshot_id, expected_identity, payload.analysis_report_id
            )
            findings.append(
                _passed("snapshot.available", "The selected immutable source snapshot is available.")
                if available
                else _blocker("snapshot.unavailable", reason)
            )

        execution_available = self._settings.container_build_execution_enabled and self._executor.available
        worker_reason = getattr(self._executor, "availability_reason", None)
        if execution_available:
            findings.append(_passed("executor.available", "An isolated build worker is configured."))
        else:
            findings.append(
                _blocker(
                    "executor.unavailable",
                    worker_reason
                    or "Container execution is disabled because no verified isolated build worker is configured.",
                )
            )
        return ContainerBuildEligibilityResult(
            project_id=project_id,
            eligible=not any(item.status is ContainerBuildFindingStatus.blocker for item in findings),
            source=source,
            source_snapshot_id=payload.source_snapshot_id,
            dockerfile_path=payload.dockerfile_path,
            context_path=payload.context_path,
            image_name=payload.image_name,
            image_tag=payload.image_tag,
            findings=findings,
            execution_available=execution_available,
            worker_availability="available" if execution_available else "unavailable",
            network_policy="disabled",
        )

    def create(self, project_id: str, payload: ContainerBuildCreateRequest) -> ContainerBuildRecord:
        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        eligibility = self.eligibility(
            project_id, ContainerBuildEligibilityRequest(**payload.model_dump(exclude={"approved"}))
        )
        if not payload.approved:
            eligibility.findings.append(_blocker("approval.required", "Explicit user approval is required."))
        blockers = [item for item in eligibility.findings if item.status is ContainerBuildFindingStatus.blocker]
        if blockers:
            return self._persist(
                project_id,
                payload,
                eligibility.source,
                ContainerBuildStatus.rejected,
                started_at,
                started_clock,
                logs="",
                failure_reason="; ".join(item.message for item in blockers),
                exit_status=None,
                image_digest=None,
                findings=eligibility.findings,
            )

        assert eligibility.source is not None and payload.source_snapshot_id is not None
        key = (project_id, payload.source_snapshot_id, payload.image_name, payload.image_tag)
        with self._active_lock:
            if key in self._active_keys:
                raise ContainerBuildError(
                    "An equivalent container build is already active.",
                    status_code=409,
                    code="duplicate_build",
                )
            self._active_keys.add(key)
        prepared: PreparedBuildSource | None = None
        build_id = str(uuid4())
        correlation_id = str(uuid4())
        try:
            analysis_identity = self._compatible_report(
                project_id, payload.analysis_report_id, DeploymentReportOperation.repository_analysis
            )
            prepared = self._source_provider.prepare(
                project_id,
                eligibility.source,
                payload.source_snapshot_id,
                analysis_identity.commit_sha
                if analysis_identity and eligibility.source is RepositoryAnalysisSource.github_public
                else None,
                payload.analysis_report_id,
            )
            result = self._executor.execute(
                BuildExecutionRequest(
                    build_id=build_id,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    source=prepared,
                    dockerfile_path=payload.dockerfile_path,
                    context_path=payload.context_path,
                    image_name=payload.image_name,
                    image_tag=payload.image_tag,
                    policy=self._policy(),
                )
            )
            succeeded = (
                result.exit_status == 0
                and not result.timed_out
                and result.image_digest is not None
                and DIGEST_PATTERN.fullmatch(result.image_digest)
            )
            status = ContainerBuildStatus.succeeded if succeeded else ContainerBuildStatus.failed
            failure = (
                None
                if succeeded
                else (
                    "Build execution exceeded its configured timeout."
                    if result.timed_out
                    else result.failure_reason or "Build process or image identity verification failed."
                )
            )
            return self._persist(
                project_id,
                payload,
                eligibility.source,
                status,
                started_at,
                started_clock,
                logs=result.logs,
                failure_reason=failure,
                exit_status=result.exit_status,
                image_digest=result.image_digest if succeeded else None,
                findings=eligibility.findings,
                worker_job_id=result.worker_job_id,
                cleanup_status=result.cleanup_status,
                result_validation="passed" if succeeded else "failed",
                logs_already_truncated=result.logs_truncated,
            )
        except ContainerBuildError as exc:
            return self._persist(
                project_id,
                payload,
                eligibility.source,
                ContainerBuildStatus.failed,
                started_at,
                started_clock,
                logs="",
                failure_reason=str(exc),
                exit_status=None,
                image_digest=None,
                findings=eligibility.findings,
            )
        except Exception:
            return self._persist(
                project_id,
                payload,
                eligibility.source,
                ContainerBuildStatus.failed,
                started_at,
                started_clock,
                logs="",
                failure_reason="The isolated build worker failed unexpectedly.",
                exit_status=None,
                image_digest=None,
                findings=eligibility.findings,
            )
        finally:
            if prepared is not None:
                self._source_provider.cleanup(prepared)
            with self._active_lock:
                self._active_keys.discard(key)

    def list_builds(self, project_id: str) -> list[ContainerBuildRecord]:
        if self._projects.get(project_id) is None:
            raise ProjectNotFoundError("Project not found")
        return [
            self._record(report)
            for report in self._reports.list_reports(project_id=project_id)
            if report.operation is DeploymentReportOperation.container_build
        ]

    def get(self, project_id: str, build_id: str) -> ContainerBuildRecord | None:
        report = self._reports.get(build_id)
        if (
            report is None
            or report.project_id != project_id
            or report.operation is not DeploymentReportOperation.container_build
        ):
            return None
        return self._record(report)

    def logs(self, project_id: str, build_id: str) -> ContainerBuildLogsResponse | None:
        report = self._reports.get(build_id)
        if (
            report is None
            or report.project_id != project_id
            or report.operation is not DeploymentReportOperation.container_build
        ):
            return None
        raw = report.evidence.get("logs", "")
        logs = raw if isinstance(raw, str) else ""
        return ContainerBuildLogsResponse(
            build_id=build_id,
            status=ContainerBuildStatus(str(report.evidence.get("build_status"))),
            logs=logs,
            truncated=report.evidence.get("logs_truncated") is True,
        )

    def cancel(self, project_id: str, build_id: str) -> ContainerBuildRecord:
        record = self.get(project_id, build_id)
        if record is None:
            raise ContainerBuildError("Container build not found.", status_code=404, code="build_not_found")
        raise ContainerBuildError(
            "Reliable cancellation is unavailable for the configured synchronous build executor.",
            status_code=409,
            code="cancellation_unavailable",
        )

    def _compatible_report(
        self, project_id: str, report_id: str, operation: DeploymentReportOperation
    ) -> DeploymentReportRecord | None:
        report = self._reports.get(report_id)
        return report if report and report.project_id == project_id and report.operation is operation else None

    def _analysis_source(self, report: DeploymentReportRecord, project_mode: ProjectMode) -> RepositoryAnalysisSource:
        raw = report.evidence.get("source")
        if raw is None:
            return (
                RepositoryAnalysisSource.github_public
                if project_mode is ProjectMode.github_repository
                else RepositoryAnalysisSource.local_upload
            )
        try:
            return RepositoryAnalysisSource(raw)
        except ValueError as exc:
            raise ContainerBuildError(
                "Analysis source is incompatible.", status_code=409, code="analysis_incompatible"
            ) from exc

    def _policy(self) -> BuildExecutionPolicy:
        return BuildExecutionPolicy(
            network_disabled=True,
            privileged=False,
            host_docker_socket=False,
            host_filesystem_mounts=False,
            credentials_available=False,
            cpu_limit=self._settings.container_build_cpu_limit,
            memory_megabytes=self._settings.container_build_memory_megabytes,
            storage_megabytes=self._settings.container_build_storage_megabytes,
            process_limit=self._settings.container_build_process_limit,
            timeout_seconds=self._settings.container_build_timeout_seconds,
            max_log_bytes=self._settings.container_build_max_log_bytes,
        )

    def _persist(
        self,
        project_id: str,
        payload: ContainerBuildCreateRequest,
        source: RepositoryAnalysisSource | None,
        status: ContainerBuildStatus,
        started_at: datetime,
        started_clock: float,
        *,
        logs: str,
        failure_reason: str | None,
        exit_status: int | None,
        image_digest: str | None,
        findings: list[ContainerBuildFinding],
        worker_job_id: str | None = None,
        cleanup_status: str | None = None,
        result_validation: str | None = None,
        logs_already_truncated: bool = False,
    ) -> ContainerBuildRecord:
        completed_at = datetime.now(UTC)
        raw_log_bytes = len(logs.encode("utf-8"))
        sanitized_logs = sanitize_text(logs)
        encoded = sanitized_logs.encode("utf-8")
        truncated = logs_already_truncated or raw_log_bytes > self._settings.container_build_max_log_bytes
        if truncated:
            encoded = encoded[: self._settings.container_build_max_log_bytes]
            sanitized_logs = encoded.decode("utf-8", errors="ignore")
        report_status = {
            ContainerBuildStatus.succeeded: DeploymentReportStatus.success,
            ContainerBuildStatus.cancelled: DeploymentReportStatus.skipped,
            ContainerBuildStatus.rejected: DeploymentReportStatus.failure,
            ContainerBuildStatus.failed: DeploymentReportStatus.failure,
        }[status]
        evidence = {
            "build_status": status.value,
            "source": source.value if source else None,
            "source_snapshot_id": payload.source_snapshot_id,
            "analysis_report_id": payload.analysis_report_id,
            "readiness_report_id": payload.readiness_report_id,
            "dockerfile_path": payload.dockerfile_path,
            "context_path": payload.context_path,
            "image_name": payload.image_name,
            "image_tag": payload.image_tag,
            "image_digest": image_digest,
            "exit_status": exit_status,
            "failure_reason": failure_reason,
            "logs": sanitized_logs,
            "logs_truncated": truncated,
            "worker_job_id": worker_job_id,
            "result_validation": result_validation,
            "cleanup_status": cleanup_status,
            "eligibility_findings": [item.model_dump(mode="json") for item in findings],
            "isolation_policy": self._policy().__dict__,
        }
        report = self._reports.create(
            DeploymentReportCreate(
                project_id=project_id,
                operation=DeploymentReportOperation.container_build,
                status=report_status,
                summary=f"Container build {status.value}; no deployment was performed.",
                target="isolated_container_build",
                commit_sha=payload.source_snapshot_id if source is RepositoryAnalysisSource.github_public else None,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=_duration_ms(started_clock),
                evidence=evidence,
                errors=(
                    [DeploymentReportError(code=f"build_{status.value}", message=failure_reason)]
                    if failure_reason
                    else []
                ),
            )
        )
        return self._record(report)

    @staticmethod
    def _record(report: DeploymentReportRecord) -> ContainerBuildRecord:
        evidence = report.evidence
        return ContainerBuildRecord(
            id=report.id,
            project_id=report.project_id or "",
            status=ContainerBuildStatus(str(evidence["build_status"])),
            source=RepositoryAnalysisSource(evidence["source"]) if evidence.get("source") else None,
            source_snapshot_id=evidence.get("source_snapshot_id"),
            analysis_report_id=str(evidence["analysis_report_id"]),
            readiness_report_id=str(evidence["readiness_report_id"]),
            dockerfile_path=str(evidence["dockerfile_path"]),
            context_path=str(evidence["context_path"]),
            image_name=str(evidence["image_name"]),
            image_tag=str(evidence["image_tag"]),
            image_digest=evidence.get("image_digest"),
            started_at=report.started_at,
            completed_at=report.completed_at,
            duration_ms=report.duration_ms,
            exit_status=evidence.get("exit_status"),
            failure_reason=evidence.get("failure_reason"),
            worker_job_id=evidence.get("worker_job_id"),
            result_validation=evidence.get("result_validation"),
            cleanup_status=evidence.get("cleanup_status"),
            report_id=report.id,
        )


def _signal_paths(report: DeploymentReportRecord, signal_id: str) -> list[str]:
    signals = report.evidence.get("readiness_signals")
    if not isinstance(signals, list):
        return []
    for signal in signals:
        if (
            isinstance(signal, dict)
            and signal.get("id") == signal_id
            and isinstance(signal.get("evidence_paths"), list)
        ):
            return [str(path) for path in signal["evidence_paths"]]
    return []


def _container_dimension_ready(report: DeploymentReportRecord) -> bool:
    dimensions = report.evidence.get("dimensions")
    if not isinstance(dimensions, list):
        return False
    return any(
        isinstance(item, dict) and item.get("id") == "containerization" and item.get("status") == "ready"
        for item in dimensions
    )


def _passed(code: str, message: str, paths: list[str] | None = None) -> ContainerBuildFinding:
    return ContainerBuildFinding(
        code=code, status=ContainerBuildFindingStatus.passed, message=message, evidence_paths=paths or []
    )


def _blocker(code: str, message: str, paths: list[str] | None = None) -> ContainerBuildFinding:
    return ContainerBuildFinding(
        code=code, status=ContainerBuildFindingStatus.blocker, message=message, evidence_paths=paths or []
    )


def _duration_ms(started_clock: float) -> int:
    return max(0, min(round((time.perf_counter() - started_clock) * 1000), 86_400_000))
