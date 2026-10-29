"""Deterministic readiness assessment for persisted repository analysis evidence."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from pydantic import ValidationError

from app.config import Settings
from app.schemas import (
    DeploymentReportCreate,
    DeploymentReportError,
    DeploymentReportOperation,
    DeploymentReportRecord,
    DeploymentReportStatus,
    ProjectMode,
    ProjectSourceType,
    RepositoryAnalysisSignal,
    RepositoryAnalysisSource,
    RepositoryReadinessDimension,
    RepositoryReadinessFinding,
    RepositoryReadinessResult,
    RepositoryReadinessSeverity,
    RepositoryReadinessStatus,
)
from app.services.projects import ProjectModeError, ProjectNotFoundError, ProjectService
from app.services.reports import ReportService


class RepositoryReadinessError(RuntimeError):
    """Safe readiness error with an HTTP-compatible status and stable code."""

    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class RepositoryReadinessService:
    """Assess persisted analysis evidence without repeating GitHub retrieval."""

    def __init__(self, settings: Settings, projects: ProjectService, reports: ReportService) -> None:
        self._settings = settings
        self._projects = projects
        self._reports = reports

    def assess(self, project_id: str, analysis_report_id: str | None) -> RepositoryReadinessResult:
        """Assess the selected or latest compatible repository-analysis report."""

        project = self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError("Project not found")
        supported_project = (
            project.mode is ProjectMode.github_repository
            and project.source_type is ProjectSourceType.github_public_readonly
        ) or (project.mode is ProjectMode.local and project.source_type is ProjectSourceType.manual)
        if not supported_project:
            raise ProjectModeError("Repository readiness requires a supported analyzed repository project")

        started_at = datetime.now(UTC)
        started_clock = time.perf_counter()
        try:
            analysis_report = self._resolve_analysis_report(
                project.id,
                analysis_report_id,
                started_at,
                project.mode,
            )
            signals = self._decode_signals(analysis_report)
            analysis_source = self._analysis_source(analysis_report, project.mode)
        except RepositoryReadinessError as exc:
            self._persist_failure(project.id, analysis_report_id, started_at, started_clock, exc)
            raise

        partial_analysis = (
            analysis_report.status is DeploymentReportStatus.partial
            or analysis_report.evidence.get("retrieval_status") == "partial"
            or analysis_report.evidence.get("tree_truncated") is True
        )
        signal_paths = {signal.id: signal.evidence_paths for signal in signals}
        dimensions = [
            _assess_containerization(signal_paths, partial_analysis),
            _assess_kubernetes(signal_paths, partial_analysis),
            _assess_infrastructure(signal_paths, partial_analysis),
        ]
        overall_status = _overall_status(dimensions)
        completed_at = datetime.now(UTC)
        evidence = {
            "analysis_report_id": analysis_report.id,
            "analysis_source": analysis_source.value,
            "analysis_commit_sha": analysis_report.commit_sha,
            "overall_status": overall_status.value,
            "dimensions": [dimension.model_dump(mode="json") for dimension in dimensions],
        }
        report = self._reports.create(
            DeploymentReportCreate(
                project_id=project.id,
                operation=DeploymentReportOperation.repository_readiness,
                status=(
                    DeploymentReportStatus.partial
                    if overall_status is RepositoryReadinessStatus.unknown
                    else DeploymentReportStatus.success
                ),
                summary=f"Repository readiness assessment completed: {overall_status.value}.",
                target=(
                    "github_public_readonly"
                    if analysis_source is RepositoryAnalysisSource.github_public
                    else "local_user_submission"
                ),
                commit_sha=analysis_report.commit_sha,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=_duration_ms(started_clock),
                evidence=evidence,
            )
        )
        return RepositoryReadinessResult(
            project_id=project.id,
            analysis_source=analysis_source,
            analysis_report_id=analysis_report.id,
            analysis_commit_sha=analysis_report.commit_sha,
            overall_status=overall_status,
            dimensions=dimensions,
            report_id=report.id,
            assessed_at=completed_at,
        )

    def _resolve_analysis_report(
        self,
        project_id: str,
        analysis_report_id: str | None,
        now: datetime,
        project_mode: ProjectMode,
    ) -> DeploymentReportRecord:
        if analysis_report_id:
            report = self._reports.get(analysis_report_id)
            if (
                report is None
                or report.project_id != project_id
                or report.operation is not DeploymentReportOperation.repository_analysis
            ):
                raise RepositoryReadinessError(
                    "The requested repository analysis report was not found for this project.",
                    status_code=409,
                    code="analysis_not_found",
                )
        else:
            report = next(
                (
                    candidate
                    for candidate in self._reports.list_reports(project_id=project_id)
                    if candidate.operation is DeploymentReportOperation.repository_analysis
                    and candidate.status in {DeploymentReportStatus.success, DeploymentReportStatus.partial}
                ),
                None,
            )
            if report is None:
                raise RepositoryReadinessError(
                    (
                        "Run public repository analysis before assessing readiness."
                        if project_mode is ProjectMode.github_repository
                        else "Run local repository analysis before assessing readiness."
                    ),
                    status_code=409,
                    code="analysis_required",
                )
        age_seconds = max(0.0, (now - report.created_at).total_seconds())
        if age_seconds > self._settings.repository_analysis_max_age_seconds:
            raise RepositoryReadinessError(
                "The repository analysis is outdated; run analysis again before assessing readiness.",
                status_code=409,
                code="analysis_outdated",
            )
        return report

    def _analysis_source(
        self,
        report: DeploymentReportRecord,
        project_mode: ProjectMode,
    ) -> RepositoryAnalysisSource:
        raw_source = report.evidence.get("source")
        if raw_source is None:
            return (
                RepositoryAnalysisSource.github_public
                if project_mode is ProjectMode.github_repository
                else RepositoryAnalysisSource.local_upload
            )
        try:
            return RepositoryAnalysisSource(raw_source)
        except (TypeError, ValueError) as exc:
            raise RepositoryReadinessError(
                "The repository analysis source is incompatible; run analysis again.",
                status_code=409,
                code="analysis_incompatible",
            ) from exc

    def _decode_signals(self, report: DeploymentReportRecord) -> list[RepositoryAnalysisSignal]:
        raw_signals = report.evidence.get("readiness_signals")
        if not isinstance(raw_signals, list):
            raise RepositoryReadinessError(
                "The repository analysis predates readiness evidence; run analysis again.",
                status_code=409,
                code="analysis_incompatible",
            )
        try:
            return [RepositoryAnalysisSignal.model_validate(signal) for signal in raw_signals]
        except ValidationError as exc:
            raise RepositoryReadinessError(
                "The persisted repository analysis evidence is incomplete; run analysis again.",
                status_code=409,
                code="analysis_incompatible",
            ) from exc

    def _persist_failure(
        self,
        project_id: str,
        analysis_report_id: str | None,
        started_at: datetime,
        started_clock: float,
        error: RepositoryReadinessError,
    ) -> None:
        completed_at = datetime.now(UTC)
        self._reports.create(
            DeploymentReportCreate(
                project_id=project_id,
                operation=DeploymentReportOperation.repository_readiness,
                status=DeploymentReportStatus.failure,
                summary="Repository readiness assessment could not be completed.",
                target="repository_analysis",
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=_duration_ms(started_clock),
                evidence={"analysis_report_id": analysis_report_id},
                errors=[DeploymentReportError(code=error.code, message=str(error))],
            )
        )


def _assess_containerization(signals: dict[str, list[str]], partial: bool) -> RepositoryReadinessDimension:
    findings = _detected_findings(signals, "container.", "containerization")
    missing: list[str] = []
    warnings: list[str] = []
    blocking = False

    if "container.dockerfile" not in signals:
        missing.append("A Dockerfile is required for the current container-image workflow.")
        findings.append(
            _missing("container.dockerfile.missing", "containerization", "No Dockerfile was detected.", True)
        )
        blocking = True
    else:
        for signal_id, description in (
            ("container.base_image", "The Dockerfile does not contain a recognizable FROM declaration."),
            ("container.startup_command", "The Dockerfile does not contain a recognizable CMD or ENTRYPOINT."),
        ):
            if signal_id not in signals:
                missing.append(description)
                findings.append(_missing(f"{signal_id}.missing", "containerization", description, True))
                blocking = True
        for signal_id, description in (
            ("container.build_instruction", "No explicit build-stage instruction was detected."),
            ("container.dependency_install", "No recognizable dependency installation command was detected."),
            ("container.port", "No EXPOSE instruction was detected; confirm the application port manually."),
            ("container.dockerignore", "No .dockerignore file was detected."),
        ):
            if signal_id not in signals:
                warnings.append(description)
                findings.append(_missing(f"{signal_id}.recommended", "containerization", description, False))

    status = RepositoryReadinessStatus.not_ready if blocking else RepositoryReadinessStatus.ready
    if partial:
        status = RepositoryReadinessStatus.unknown
    explanation = {
        RepositoryReadinessStatus.ready: (
            "Required static container configuration was detected; build success remains unverified."
        ),
        RepositoryReadinessStatus.not_ready: "Required container configuration is missing or incomplete.",
        RepositoryReadinessStatus.unknown: (
            "Repository retrieval was incomplete, so container readiness cannot be established."
        ),
    }[status]
    return _dimension("containerization", status, explanation, findings, missing, warnings, partial)


def _assess_kubernetes(signals: dict[str, list[str]], partial: bool) -> RepositoryReadinessDimension:
    findings = _detected_findings(signals, "kubernetes.", "kubernetes")
    missing: list[str] = []
    warnings: list[str] = []
    blocking = False
    incomplete = False

    if "kubernetes.deployment" not in signals:
        missing.append("A Kubernetes Deployment manifest was not detected.")
        findings.append(_missing("kubernetes.deployment.missing", "kubernetes", missing[-1], True))
        blocking = True
    if "kubernetes.image" not in signals:
        missing.append("A workload container image reference was not detected.")
        findings.append(_missing("kubernetes.image.missing", "kubernetes", missing[-1], True))
        blocking = True
    for signal_id, description in (
        ("kubernetes.service", "A Kubernetes Service was not detected."),
        ("kubernetes.container_port", "A workload container port was not detected."),
    ):
        if signal_id not in signals:
            missing.append(description)
            findings.append(_missing(f"{signal_id}.missing", "kubernetes", description, True))
            incomplete = True
    for signal_id, description in (
        ("kubernetes.resources_requests", "Resource requests were not detected."),
        ("kubernetes.resources_limits", "Resource limits were not detected."),
        ("kubernetes.readiness_probe", "A readiness probe was not detected."),
        ("kubernetes.liveness_probe", "A liveness probe was not detected."),
    ):
        if signal_id not in signals:
            warnings.append(description)
            findings.append(_missing(f"{signal_id}.recommended", "kubernetes", description, False))

    status = (
        RepositoryReadinessStatus.not_ready
        if blocking
        else RepositoryReadinessStatus.partially_ready
        if incomplete
        else RepositoryReadinessStatus.ready
    )
    if partial:
        status = RepositoryReadinessStatus.unknown
    explanation = {
        RepositoryReadinessStatus.ready: (
            "Core Kubernetes workload and service evidence was detected; cluster validation remains unverified."
        ),
        RepositoryReadinessStatus.partially_ready: (
            "A workload is described, but required service or port evidence is incomplete."
        ),
        RepositoryReadinessStatus.not_ready: "Core Kubernetes workload configuration is missing.",
        RepositoryReadinessStatus.unknown: (
            "Repository retrieval was incomplete, so Kubernetes readiness cannot be established."
        ),
    }[status]
    return _dimension("kubernetes", status, explanation, findings, missing, warnings, partial)


def _assess_infrastructure(signals: dict[str, list[str]], partial: bool) -> RepositoryReadinessDimension:
    findings = _detected_findings(signals, "infrastructure.", "infrastructure")
    missing: list[str] = []
    warnings: list[str] = []
    has_kubernetes = "infrastructure.kubernetes" in signals
    has_terraform = "infrastructure.terraform" in signals
    if not has_kubernetes and not has_terraform:
        missing.append("Neither Terraform nor Kubernetes integration configuration was detected.")
        findings.append(_missing("infrastructure.integration.missing", "infrastructure", missing[-1], True))
    elif has_terraform and not has_kubernetes:
        missing.append("Terraform exists, but Kubernetes integration configuration was not detected.")
        findings.append(_missing("infrastructure.kubernetes.missing", "infrastructure", missing[-1], True))
    for signal_id, description in (
        ("infrastructure.ci_cd", "No supported CI/CD workflow evidence was detected."),
        ("infrastructure.observability", "No observability integration evidence was detected."),
    ):
        if signal_id not in signals:
            warnings.append(description)
            findings.append(_missing(f"{signal_id}.recommended", "infrastructure", description, False))
    unknown = []
    if "infrastructure.environment_variables" not in signals:
        unknown.append("Environment-variable requirements were not identifiable from recognized configuration.")
    status = (
        RepositoryReadinessStatus.ready
        if has_kubernetes
        else RepositoryReadinessStatus.partially_ready
        if has_terraform
        else RepositoryReadinessStatus.not_ready
    )
    if partial:
        status = RepositoryReadinessStatus.unknown
    explanation = {
        RepositoryReadinessStatus.ready: (
            "Static platform-integration configuration was detected; external services remain unverified."
        ),
        RepositoryReadinessStatus.partially_ready: (
            "Infrastructure-as-code exists, but Kubernetes platform integration remains incomplete."
        ),
        RepositoryReadinessStatus.not_ready: "No supported platform-integration configuration was detected.",
        RepositoryReadinessStatus.unknown: (
            "Repository retrieval was incomplete, so infrastructure readiness cannot be established."
        ),
    }[status]
    return _dimension("infrastructure", status, explanation, findings, missing, warnings, partial, unknown)


def _detected_findings(signals: dict[str, list[str]], prefix: str, category: str) -> list[RepositoryReadinessFinding]:
    return [
        RepositoryReadinessFinding(
            id=f"{signal_id}.detected",
            category=category,
            severity=RepositoryReadinessSeverity.info,
            description=f"Detected {signal_id.removeprefix(prefix).replace('_', ' ')} evidence.",
            evidence_paths=paths,
            required=False,
        )
        for signal_id, paths in sorted(signals.items())
        if signal_id.startswith(prefix)
    ]


def _missing(
    finding_id: str,
    category: str,
    description: str,
    required: bool,
) -> RepositoryReadinessFinding:
    return RepositoryReadinessFinding(
        id=finding_id,
        category=category,
        severity=RepositoryReadinessSeverity.error if required else RepositoryReadinessSeverity.warning,
        description=description,
        required=required,
    )


def _dimension(
    dimension_id: str,
    status: RepositoryReadinessStatus,
    explanation: str,
    findings: list[RepositoryReadinessFinding],
    missing: list[str],
    warnings: list[str],
    partial: bool,
    unknown: list[str] | None = None,
) -> RepositoryReadinessDimension:
    evidence_paths = sorted({path for finding in findings for path in finding.evidence_paths})
    unknown_conditions = list(unknown or [])
    if partial:
        unknown_conditions.insert(0, "The source analysis was partial or used a truncated repository tree.")
    return RepositoryReadinessDimension(
        id=dimension_id,
        status=status,
        explanation=explanation,
        findings=findings,
        evidence_paths=evidence_paths,
        missing_prerequisites=missing,
        warnings=warnings,
        unknown_conditions=unknown_conditions,
    )


def _overall_status(dimensions: list[RepositoryReadinessDimension]) -> RepositoryReadinessStatus:
    statuses = {dimension.status for dimension in dimensions}
    if RepositoryReadinessStatus.unknown in statuses:
        return RepositoryReadinessStatus.unknown
    if RepositoryReadinessStatus.not_ready in statuses:
        return RepositoryReadinessStatus.not_ready
    if RepositoryReadinessStatus.partially_ready in statuses:
        return RepositoryReadinessStatus.partially_ready
    return RepositoryReadinessStatus.ready


def _duration_ms(started_clock: float) -> int:
    return max(0, min(round((time.perf_counter() - started_clock) * 1000), 86_400_000))
