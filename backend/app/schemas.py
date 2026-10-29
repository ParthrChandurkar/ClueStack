"""Pydantic schemas shared by API routes and service classes.

The models define ClueStack's public REST contract for deployments, metrics,
and log responses.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class DeploymentStatus(str, Enum):
    """Supported lifecycle states for a deployment."""

    running = "Running"
    simulated = "Simulated"
    failed = "Failed"
    pending = "Pending"
    deploying = "Deploying"
    deleting = "Deleting"
    deleted = "Deleted"


class ProjectMode(str, Enum):
    """Supported ClueStack project operating modes."""

    local = "local"
    github_repository = "github_repository"


class ProjectSourceType(str, Enum):
    """Supported origins for project metadata."""

    manual = "manual"
    github_public_readonly = "github_public_readonly"


class ProjectCreate(BaseModel):
    """Validated input for creating a ClueStack project."""

    name: str = Field(..., min_length=2, max_length=80)
    slug: str | None = Field(
        default=None,
        min_length=2,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    mode: ProjectMode = ProjectMode.local
    source_type: ProjectSourceType = ProjectSourceType.manual
    repository_url: str | None = Field(default=None, max_length=255)
    default_namespace: str = Field(
        default="infrawatch",
        min_length=2,
        max_length=63,
        pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
    )
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Keep project names readable and safe for local display."""

        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("project name cannot be blank")
        return normalized

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: str | None) -> str | None:
        """Accept only public GitHub HTTPS repository URLs for this first slice."""

        if value is None:
            return None
        normalized = value.strip().rstrip("/").removesuffix(".git")
        if not normalized:
            return None
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.port is not None:
            raise ValueError("repository_url must use the official https://github.com host")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("repository_url cannot include credentials, a query, or a fragment")
        parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
        if len(parts) != 2 or not all(parts) or any(part in {".", ".."} for part in parts):
            raise ValueError("repository_url must include GitHub owner and repository name")
        if any("/" in part or "\\" in part for part in parts):
            raise ValueError("repository_url contains an unsafe path")
        return normalized

    @model_validator(mode="after")
    def validate_mode_and_source(self) -> "ProjectCreate":
        """Ensure mode, source type, and repository URL are consistent."""

        if self.mode is ProjectMode.local:
            if self.source_type is not ProjectSourceType.manual:
                raise ValueError("local projects must use manual source_type")
            if self.repository_url is not None:
                raise ValueError("local projects cannot include repository_url")
        if self.mode is ProjectMode.github_repository:
            if self.source_type is not ProjectSourceType.github_public_readonly:
                raise ValueError("github_repository projects must use github_public_readonly source_type")
            if self.repository_url is None:
                raise ValueError("github_repository projects require repository_url")
        return self


class ProjectRecord(ProjectCreate):
    """Persisted ClueStack project metadata."""

    id: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def validate_uuid_id(cls, value: str) -> str:
        """Require stable UUID identifiers for persisted projects."""

        UUID(value)
        return value


class DeploymentReportOperation(str, Enum):
    """Supported historical report operation types."""

    deploy = "deploy"
    delete = "delete"
    rollback = "rollback"
    status_check = "status_check"
    repository_analysis = "repository_analysis"
    repository_readiness = "repository_readiness"
    container_build = "container_build"


class DeploymentReportStatus(str, Enum):
    """Supported historical report outcome states."""

    success = "success"
    failure = "failure"
    partial = "partial"
    skipped = "skipped"


class DeploymentReportError(BaseModel):
    """Bounded sanitized error detail for a deployment report."""

    code: str | None = Field(default=None, max_length=80)
    message: str = Field(..., min_length=1, max_length=500)


class DeploymentReportCreate(BaseModel):
    """Validated input for creating a historical deployment report."""

    project_id: str | None = None
    deployment_id: str | None = None
    operation: DeploymentReportOperation
    status: DeploymentReportStatus
    summary: str = Field(..., min_length=1, max_length=500)
    target: str = Field(..., min_length=1, max_length=120)
    commit_sha: str | None = Field(default=None, max_length=64)
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    evidence: dict[str, Any] = Field(default_factory=dict)
    errors: list[DeploymentReportError] = Field(default_factory=list, max_length=20)

    @field_validator("project_id", "deployment_id")
    @classmethod
    def validate_optional_uuid(cls, value: str | None) -> str | None:
        """Validate optional stable identifiers when supplied."""

        if value is not None:
            UUID(value)
        return value

    @field_validator("evidence")
    @classmethod
    def validate_sanitized_evidence(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Keep evidence top-level shape bounded before repository sanitization."""

        if len(value) > 50:
            raise ValueError("evidence cannot contain more than 50 top-level keys")
        return value

    @model_validator(mode="after")
    def validate_timestamps(self) -> "DeploymentReportCreate":
        """Ensure completed_at does not precede started_at."""

        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot be earlier than started_at")
        return self


class DeploymentReportRecord(DeploymentReportCreate):
    """Persisted historical deployment report."""

    id: str
    created_at: datetime

    @field_validator("id")
    @classmethod
    def validate_uuid_id(cls, value: str) -> str:
        """Require stable UUID report identifiers."""

        UUID(value)
        return value


class RepositoryFindingStatus(str, Enum):
    """Evidence state for a repository-analysis capability."""

    detected = "detected"
    missing = "missing"
    unknown = "unknown"
    unsupported = "unsupported"


class RepositoryRetrievalStatus(str, Enum):
    """Completeness of a public repository retrieval."""

    complete = "complete"
    partial = "partial"
    failed = "failed"


class RepositoryAnalysisSource(str, Enum):
    """Supported read-only sources for repository evidence."""

    github_public = "github_public"
    local_upload = "local_upload"


class SourceSnapshotStatus(str, Enum):
    """Lifecycle state for an immutable build-source snapshot."""

    creating = "creating"
    available = "available"
    failed = "failed"
    expired = "expired"
    deleted = "deleted"


class SourceSnapshotIdentity(BaseModel):
    """Stable, non-secret identity of the captured source."""

    repository_url: str | None = None
    owner: str | None = None
    repository: str | None = None
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    resolved_reference: str | None = Field(default=None, max_length=255)
    submission_id: str | None = None
    submission_complete: bool | None = None


class SourceSnapshotFileManifest(BaseModel):
    path: str = Field(..., min_length=1, max_length=512)
    size: int = Field(..., ge=0)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class SourceSnapshotExclusion(BaseModel):
    reason: str = Field(..., min_length=1, max_length=80)
    count: int = Field(..., ge=1)


class SourceSnapshotCreateRequest(BaseModel):
    """Create from the project's GitHub source or submitted local files."""

    reference: str | None = Field(default=None, min_length=1, max_length=255)
    analysis_report_id: str | None = None
    source_label: str | None = Field(default=None, min_length=1, max_length=80)
    submission_complete: bool = True
    files: list["LocalRepositoryFile"] = Field(default_factory=list, max_length=500)

    @field_validator("reference")
    @classmethod
    def validate_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            not normalized
            or any(ord(character) < 32 for character in normalized)
            or normalized.startswith(("-", "/"))
            or normalized.endswith(("/", "."))
            or ".." in normalized
        ):
            raise ValueError("reference contains an unsafe sequence")
        return normalized

    @field_validator("analysis_report_id")
    @classmethod
    def validate_analysis_report_id(cls, value: str | None) -> str | None:
        if value is not None:
            UUID(value)
        return value


class SourceSnapshotRecord(BaseModel):
    id: str
    project_id: str
    source: RepositoryAnalysisSource
    source_identity: SourceSnapshotIdentity
    analysis_report_id: str | None = None
    created_at: datetime
    expires_at: datetime | None = None
    format_version: int = Field(default=1, ge=1)
    included_files: list[SourceSnapshotFileManifest] = Field(default_factory=list)
    exclusions: list[SourceSnapshotExclusion] = Field(default_factory=list)
    file_count: int = Field(..., ge=0)
    total_content_size: int = Field(..., ge=0)
    integrity_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: SourceSnapshotStatus
    integrity_verified: bool = False
    storage_kind: str = "filesystem"
    read_only_requested: bool = False
    error: str | None = Field(default=None, max_length=500)


class SourceSnapshotVerification(BaseModel):
    snapshot_id: str
    valid: bool
    status: SourceSnapshotStatus
    integrity_hash: str | None = None
    verified_at: datetime


class RepositoryAnalysisRequest(BaseModel):
    """Optional branch selection for a read-only repository analysis."""

    branch: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("branch")
    @classmethod
    def validate_branch(cls, value: str | None) -> str | None:
        """Reject control characters and traversal-like branch names."""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("branch must be a non-empty printable value")
        if normalized.startswith(("-", "/")) or normalized.endswith(("/", ".")) or ".." in normalized:
            raise ValueError("branch contains an unsafe sequence")
        return normalized


class LocalRepositoryFile(BaseModel):
    """One bounded UTF-8 text file selected through the browser."""

    path: str = Field(..., min_length=1, max_length=512)
    content: str = Field(..., max_length=1_000_000)


class LocalRepositoryAnalysisRequest(BaseModel):
    """Browser-selected local repository evidence; never a server filesystem path."""

    source_label: str = Field(default="Selected local folder", min_length=1, max_length=80)
    submission_complete: bool = True
    files: list[LocalRepositoryFile] = Field(default_factory=list, max_length=100)

    @field_validator("source_label")
    @classmethod
    def normalize_source_label(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if (
            not normalized
            or any(ord(character) < 32 for character in normalized)
            or any(character in normalized for character in ("/", "\\"))
        ):
            raise ValueError("source_label must be a printable value")
        return normalized


class RepositoryAnalysisFinding(BaseModel):
    """One evidence-backed repository capability finding."""

    category: str = Field(..., min_length=1, max_length=80)
    status: RepositoryFindingStatus
    values: list[str] = Field(default_factory=list, max_length=100)
    evidence_paths: list[str] = Field(default_factory=list, max_length=100)
    detail: str = Field(..., min_length=1, max_length=500)


class RepositoryAnalysisSignal(BaseModel):
    """One deterministic readiness signal derived from retrieved configuration."""

    id: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-z0-9_.-]+$")
    evidence_paths: list[str] = Field(default_factory=list, max_length=100)
    detail: str = Field(..., min_length=1, max_length=300)


class RepositoryAnalysisResult(BaseModel):
    """Bounded result returned from a supported read-only source."""

    project_id: str
    source: RepositoryAnalysisSource
    source_label: str
    repository_url: str | None = None
    owner: str | None = None
    repository: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    retrieval_status: RepositoryRetrievalStatus
    tree_truncated: bool
    tree_entries_seen: int = Field(..., ge=0)
    files_retrieved: int = Field(..., ge=0)
    content_bytes_retrieved: int = Field(..., ge=0)
    findings: list[RepositoryAnalysisFinding]
    readiness_signals: list[RepositoryAnalysisSignal]
    missing_prerequisites: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    report_id: str
    analyzed_at: datetime


class RepositoryReadinessStatus(str, Enum):
    """Deterministic readiness outcome for one dimension or the overall result."""

    ready = "ready"
    partially_ready = "partially_ready"
    not_ready = "not_ready"
    unknown = "unknown"


class RepositoryReadinessSeverity(str, Enum):
    """Severity assigned to an evidence-backed readiness finding."""

    info = "info"
    warning = "warning"
    error = "error"


class RepositoryReadinessRequest(BaseModel):
    """Select the persisted repository analysis used for assessment."""

    analysis_report_id: str | None = None

    @field_validator("analysis_report_id")
    @classmethod
    def validate_analysis_report_id(cls, value: str | None) -> str | None:
        if value is not None:
            UUID(value)
        return value


class RepositoryReadinessFinding(BaseModel):
    """One stable, deterministic readiness rule result."""

    id: str = Field(..., min_length=3, max_length=120, pattern=r"^[a-z0-9_.-]+$")
    category: str = Field(..., min_length=1, max_length=80)
    severity: RepositoryReadinessSeverity
    description: str = Field(..., min_length=1, max_length=500)
    evidence_paths: list[str] = Field(default_factory=list, max_length=100)
    required: bool


class RepositoryReadinessDimension(BaseModel):
    """Assessment for one independent repository-readiness dimension."""

    id: str = Field(..., pattern=r"^(containerization|kubernetes|infrastructure)$")
    status: RepositoryReadinessStatus
    explanation: str = Field(..., min_length=1, max_length=500)
    findings: list[RepositoryReadinessFinding]
    evidence_paths: list[str] = Field(default_factory=list, max_length=100)
    missing_prerequisites: list[str] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    unknown_conditions: list[str] = Field(default_factory=list, max_length=50)


class RepositoryReadinessResult(BaseModel):
    """Persisted evidence-based readiness assessment for one analysis report."""

    project_id: str
    analysis_source: RepositoryAnalysisSource
    analysis_report_id: str
    analysis_commit_sha: str | None
    overall_status: RepositoryReadinessStatus
    dimensions: list[RepositoryReadinessDimension]
    report_id: str
    assessed_at: datetime


class ContainerBuildStatus(str, Enum):
    """Explicit lifecycle states for controlled container builds."""

    pending = "pending"
    validating = "validating"
    queued = "queued"
    building = "building"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    rejected = "rejected"


class ContainerBuildFindingStatus(str, Enum):
    passed = "passed"
    warning = "warning"
    blocker = "blocker"


class ContainerBuildFinding(BaseModel):
    code: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-z0-9_.-]+$")
    status: ContainerBuildFindingStatus
    message: str = Field(..., min_length=1, max_length=500)
    evidence_paths: list[str] = Field(default_factory=list, max_length=50)


class ContainerBuildEligibilityRequest(BaseModel):
    analysis_report_id: str
    readiness_report_id: str
    source_snapshot_id: str | None = Field(default=None, min_length=1, max_length=128)
    dockerfile_path: str = Field(default="Dockerfile", min_length=1, max_length=512)
    context_path: str = Field(default=".", min_length=1, max_length=512)
    image_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$",
    )
    image_tag: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$",
    )

    @field_validator("analysis_report_id", "readiness_report_id")
    @classmethod
    def validate_report_id(cls, value: str) -> str:
        UUID(value)
        return value


class ContainerBuildCreateRequest(ContainerBuildEligibilityRequest):
    approved: bool = False


class ContainerBuildEligibilityResult(BaseModel):
    project_id: str
    eligible: bool
    source: RepositoryAnalysisSource | None
    source_snapshot_id: str | None
    dockerfile_path: str
    context_path: str
    image_name: str
    image_tag: str
    findings: list[ContainerBuildFinding]
    execution_available: bool
    worker_availability: Literal["available", "unavailable"] = "unavailable"
    network_policy: Literal["disabled"] = "disabled"


class ContainerBuildRecord(BaseModel):
    id: str
    project_id: str
    status: ContainerBuildStatus
    source: RepositoryAnalysisSource | None
    source_snapshot_id: str | None
    analysis_report_id: str
    readiness_report_id: str
    dockerfile_path: str
    context_path: str
    image_name: str
    image_tag: str
    image_digest: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    exit_status: int | None = None
    failure_reason: str | None = None
    worker_job_id: str | None = None
    result_validation: str | None = None
    cleanup_status: str | None = None
    report_id: str


class ContainerBuildLogsResponse(BaseModel):
    build_id: str
    status: ContainerBuildStatus
    logs: str
    truncated: bool


class DeploymentRequest(BaseModel):
    """Payload used to deploy a service through ClueStack."""

    name: str = Field(..., min_length=2, max_length=63, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    project_id: str | None = None
    image: str = Field(..., min_length=3, max_length=255)
    namespace: str | None = Field(default=None, min_length=2, max_length=63)
    replicas: int = Field(default=1, ge=1, le=10)
    port: int = Field(default=8000, ge=1, le=65535)
    environment: dict[str, str] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    commit_sha: str | None = Field(default=None, max_length=64)

    @field_validator("environment", "labels")
    @classmethod
    def validate_flat_string_map(cls, value: dict[str, str]) -> dict[str, str]:
        """Keep Kubernetes metadata and environment values simple and safe."""

        for key, item in value.items():
            if not key or len(key) > 128:
                raise ValueError("map keys must be between 1 and 128 characters")
            if not isinstance(item, str):
                raise ValueError("map values must be strings")
        return value


class DeploymentRecord(BaseModel):
    """Deployment state returned by the API and persisted locally."""

    id: str | None = None
    name: str
    project_id: str | None = None
    image: str
    namespace: str
    replicas: int
    port: int
    status: DeploymentStatus
    url: str | None = None
    commit_sha: str | None = None
    ready_replicas: int | None = None
    available_replicas: int | None = None
    observed_generation: int | None = None
    last_failure: str | None = None
    message: str
    created_at: datetime
    updated_at: datetime

    @field_validator("id")
    @classmethod
    def validate_optional_uuid_id(cls, value: str | None) -> str | None:
        """Deployment IDs are optional for legacy records but UUIDs when present."""

        if value is not None:
            UUID(value)
        return value


class DeploymentResponse(BaseModel):
    """Response returned after a deployment action is accepted."""

    deployment: DeploymentRecord
    kubernetes_manifest: dict[str, Any]


class AuditLogEntry(BaseModel):
    """Immutable operational event recorded by ClueStack."""

    id: str
    action: str
    service: str | None = None
    actor: str = "system"
    status: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MetricPoint(BaseModel):
    """Single time-series point returned to the React dashboard."""

    timestamp: int
    value: float


class ServiceMetrics(BaseModel):
    """Prometheus-derived metrics for one service."""

    service: str
    cpu_cores: list[MetricPoint]
    memory_megabytes: list[MetricPoint]
    request_rate: list[MetricPoint]
    error_rate: list[MetricPoint]
    source: str


class PodHealth(BaseModel):
    """One Kubernetes pod health summary for a service workload."""

    name: str
    phase: str
    ready: bool
    restart_count: int = 0
    reason: str | None = None
    message: str | None = None


class WorkloadHealth(BaseModel):
    """Kubernetes Deployment and Pod health for one service."""

    service: str
    namespace: str
    desired_replicas: int
    updated_replicas: int | None
    ready_replicas: int | None
    available_replicas: int | None
    unavailable_replicas: int | None
    observed_generation: int | None = None
    pods: list[PodHealth] = Field(default_factory=list)
    source: str


class LogLine(BaseModel):
    """Single log event returned from Loki or demo fallback data."""

    timestamp: str
    line: str
    namespace: str | None = None
    pod: str | None = None
    container: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class LogsResponse(BaseModel):
    """Log response for a selected service."""

    service: str
    lines: list[LogLine]
    source: str


class AlertSummary(BaseModel):
    """Normalized Alertmanager alert returned to the dashboard."""

    fingerprint: str
    status: str
    alertname: str
    severity: str | None = None
    instance: str | None = None
    service: str | None = None
    namespace: str | None = None
    pod: str | None = None
    starts_at: str | None = None
    summary: str | None = None
    description: str | None = None


class AlertsResponse(BaseModel):
    """Alertmanager alert state for the dashboard."""

    source: str
    alerts: list[AlertSummary] = Field(default_factory=list)
