"""Pydantic schemas shared by API routes and service classes.

The models define InfraWatch's public REST contract for deployments, metrics,
and log responses.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class DeploymentStatus(str, Enum):
    """Supported lifecycle states for a deployment."""

    running = "Running"
    failed = "Failed"
    pending = "Pending"
    deploying = "Deploying"
    deleting = "Deleting"
    deleted = "Deleted"


class ProjectMode(str, Enum):
    """Supported InfraWatch project operating modes."""

    local = "local"
    github_repository = "github_repository"


class ProjectSourceType(str, Enum):
    """Supported origins for project metadata."""

    manual = "manual"
    github_public_readonly = "github_public_readonly"


class ProjectCreate(BaseModel):
    """Validated input for creating an InfraWatch project."""

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
        normalized = value.strip().removesuffix(".git")
        if not normalized:
            return None
        if not normalized.startswith("https://github.com/"):
            raise ValueError("repository_url must be an https://github.com/... URL")
        parts = normalized.removeprefix("https://github.com/").split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("repository_url must include GitHub owner and repository name")
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
    """Persisted InfraWatch project metadata."""

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


class DeploymentRequest(BaseModel):
    """Payload used to deploy a service through InfraWatch."""

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
    ready_replicas: int = 0
    available_replicas: int = 0
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
    """Immutable operational event recorded by InfraWatch."""

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
    updated_replicas: int
    ready_replicas: int
    available_replicas: int
    unavailable_replicas: int
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
