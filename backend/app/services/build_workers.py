"""Internal contracts for a future isolated container-build worker.

This module deliberately contains no subprocess, shell, Docker-client, or host
mount implementation. Production execution remains unavailable until a runtime
can prove the capabilities expressed by :class:`IsolationCapabilities`.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.report_sanitizer import sanitize_text
from app.services.repository_analysis import is_safe_repository_path

IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")
IMAGE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
HOST_PATH_PATTERN = re.compile(r"(?i)(?:[a-z]:\\[^\r\n\t ]+|/(?:tmp|var|home|root|mnt)/[^\r\n\t ]+)")


class WorkerJobStatus(str, Enum):
    queued = "queued"
    starting = "starting"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"
    rejected = "rejected"
    cleanup_failed = "cleanup_failed"


class WorkerCleanupStatus(str, Enum):
    not_started = "not_started"
    succeeded = "succeeded"
    failed = "failed"


class WorkerFailureCategory(str, Enum):
    unavailable = "unavailable"
    invalid_request = "invalid_request"
    snapshot_rejected = "snapshot_rejected"
    startup_failed = "startup_failed"
    worker_crashed = "worker_crashed"
    timed_out = "timed_out"
    cancelled = "cancelled"
    invalid_result = "invalid_result"
    cleanup_failed = "cleanup_failed"


class WorkerResourcePolicy(BaseModel):
    """Conservative, bounded resources approved by the coordinator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_limit: float = Field(..., ge=0.1, le=8.0)
    memory_megabytes: int = Field(..., ge=128, le=8_192)
    storage_megabytes: int = Field(..., ge=256, le=20_480)
    process_limit: int = Field(..., ge=16, le=1_024)
    timeout_seconds: int = Field(..., ge=30, le=3_600)
    max_log_bytes: int = Field(..., ge=500, le=2_000)


class WorkerRequest(BaseModel):
    """Validated request passed to a worker provider, never directly to a shell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    build_id: str
    project_id: str
    snapshot_id: str
    snapshot_integrity_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    snapshot_format_version: int = Field(..., ge=1, le=1)
    context_path: str = Field(..., min_length=1, max_length=512)
    dockerfile_path: str = Field(..., min_length=1, max_length=512)
    image_name: str = Field(..., min_length=1, max_length=255)
    image_tag: str = Field(..., min_length=1, max_length=128)
    resources: WorkerResourcePolicy
    network_policy: str = Field(default="disabled", pattern=r"^disabled$")
    execution_timeout_seconds: int = Field(..., ge=30, le=3_600)
    correlation_id: str

    @field_validator("build_id", "project_id", "snapshot_id", "correlation_id")
    @classmethod
    def validate_uuid(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("context_path", "dockerfile_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        if value == ".":
            return value
        if not is_safe_repository_path(value):
            raise ValueError("path must be a safe relative repository path")
        return value

    @field_validator("image_name")
    @classmethod
    def validate_image_name(cls, value: str) -> str:
        if not IMAGE_NAME_PATTERN.fullmatch(value):
            raise ValueError("invalid image name")
        return value

    @field_validator("image_tag")
    @classmethod
    def validate_image_tag(cls, value: str) -> str:
        if not IMAGE_TAG_PATTERN.fullmatch(value):
            raise ValueError("invalid image tag")
        return value

    @model_validator(mode="after")
    def validate_paths_and_timeout(self) -> WorkerRequest:
        if self.execution_timeout_seconds != self.resources.timeout_seconds:
            raise ValueError("execution timeout must match the approved resource policy")
        if self.context_path != ".":
            context = PurePosixPath(self.context_path)
            dockerfile = PurePosixPath(self.dockerfile_path)
            if dockerfile != context and context not in dockerfile.parents:
                raise ValueError("Dockerfile must be inside the selected build context")
        return self


class WorkerResourceUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cpu_seconds: float | None = Field(default=None, ge=0)
    peak_memory_megabytes: float | None = Field(default=None, ge=0)
    storage_megabytes: float | None = Field(default=None, ge=0)


class WorkerResult(BaseModel):
    """Structured output returned by an isolation runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    build_id: str
    correlation_id: str
    status: WorkerJobStatus
    exit_code: int | None = None
    image_digest: str | None = None
    logs: list[str] = Field(default_factory=list, max_length=1_000)
    started_at: datetime | None = None
    completed_at: datetime
    resource_usage: WorkerResourceUsage | None = None
    failure_category: WorkerFailureCategory | None = None
    failure_message: str | None = Field(default=None, max_length=500)
    cleanup_status: WorkerCleanupStatus = WorkerCleanupStatus.not_started

    @field_validator("job_id", "build_id", "correlation_id")
    @classmethod
    def validate_uuid(cls, value: str) -> str:
        return str(UUID(value))


@dataclass(frozen=True)
class VerifiedSnapshotDescriptor:
    """Metadata-only snapshot lease; it intentionally contains no host path."""

    snapshot_id: str
    project_id: str
    integrity_hash: str
    format_version: int
    submission_complete: bool


@dataclass(frozen=True)
class IsolationCapabilities:
    runtime_name: str
    available: bool
    verified_security_boundary: bool
    unprivileged: bool = False
    read_only_base: bool = False
    dedicated_writable_storage: bool = False
    snapshot_only_access: bool = False
    process_limits: bool = False
    resource_limits: bool = False
    network_disabled: bool = False
    reliable_termination: bool = False
    reliable_cleanup: bool = False
    reason: str = ""

    @property
    def satisfies_required_boundary(self) -> bool:
        return all(
            (
                self.available,
                self.verified_security_boundary,
                self.unprivileged,
                self.read_only_base,
                self.dedicated_writable_storage,
                self.snapshot_only_access,
                self.process_limits,
                self.resource_limits,
                self.network_disabled,
                self.reliable_termination,
                self.reliable_cleanup,
            )
        )


class SnapshotVerifier(Protocol):
    def verify_for_worker(self, request: WorkerRequest) -> VerifiedSnapshotDescriptor: ...


class IsolationRuntime(Protocol):
    @property
    def capabilities(self) -> IsolationCapabilities: ...

    def execute(self, request: WorkerRequest, snapshot: VerifiedSnapshotDescriptor) -> WorkerResult: ...

    def terminate(self, job_id: str) -> bool: ...

    def cleanup(self, job_id: str) -> bool: ...


@dataclass
class WorkerJob:
    request: WorkerRequest
    status: WorkerJobStatus
    job_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    result: WorkerResult | None = None


class WorkerLifecycleManager:
    """Thread-safe lifecycle tracking and duplicate/capacity protection."""

    _transitions = {
        WorkerJobStatus.queued: {
            WorkerJobStatus.starting,
            WorkerJobStatus.cancelled,
            WorkerJobStatus.rejected,
            WorkerJobStatus.cleanup_failed,
        },
        WorkerJobStatus.starting: {
            WorkerJobStatus.running,
            WorkerJobStatus.failed,
            WorkerJobStatus.cancelled,
            WorkerJobStatus.timed_out,
            WorkerJobStatus.cleanup_failed,
        },
        WorkerJobStatus.running: {
            WorkerJobStatus.succeeded,
            WorkerJobStatus.failed,
            WorkerJobStatus.cancelled,
            WorkerJobStatus.timed_out,
            WorkerJobStatus.cleanup_failed,
        },
    }

    def __init__(self, max_concurrent_jobs: int, max_queued_jobs: int) -> None:
        if not 1 <= max_concurrent_jobs <= 4 or not 1 <= max_queued_jobs <= 20:
            raise ValueError("worker concurrency and queue limits are outside the supported range")
        self.max_concurrent_jobs = max_concurrent_jobs
        self.max_queued_jobs = max_queued_jobs
        self._jobs: dict[str, WorkerJob] = {}
        self._correlations: dict[str, str] = {}
        self._lock = threading.Lock()

    def enqueue(self, request: WorkerRequest, job_id: str) -> WorkerJob:
        with self._lock:
            existing_id = self._correlations.get(request.correlation_id)
            if existing_id:
                existing = self._jobs[existing_id]
                if existing.request != request:
                    raise ValueError("correlation identifier was already used for a different request")
                return existing
            queued = sum(job.status is WorkerJobStatus.queued for job in self._jobs.values())
            if queued >= self.max_queued_jobs:
                raise RuntimeError("worker queue capacity has been reached")
            job = WorkerJob(request=request, status=WorkerJobStatus.queued, job_id=str(UUID(job_id)))
            self._jobs[job.job_id] = job
            self._correlations[request.correlation_id] = job.job_id
            return job

    def transition(self, job_id: str, status: WorkerJobStatus, result: WorkerResult | None = None) -> WorkerJob:
        with self._lock:
            job = self._jobs[job_id]
            if status not in self._transitions.get(job.status, set()):
                raise ValueError(f"invalid worker transition from {job.status.value} to {status.value}")
            if status is WorkerJobStatus.starting:
                active = sum(
                    item.status in {WorkerJobStatus.starting, WorkerJobStatus.running}
                    for item in self._jobs.values()
                    if item.job_id != job_id
                )
                if active >= self.max_concurrent_jobs:
                    raise RuntimeError("worker concurrency capacity has been reached")
            job.status = status
            job.updated_at = datetime.now(UTC)
            job.result = result
            return job

    def get(self, job_id: str) -> WorkerJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recover_interrupted(self) -> int:
        """Mark only in-memory interrupted work failed after provider recovery."""

        count = 0
        with self._lock:
            for job in self._jobs.values():
                if job.status in {WorkerJobStatus.queued, WorkerJobStatus.starting, WorkerJobStatus.running}:
                    job.status = WorkerJobStatus.failed
                    job.updated_at = datetime.now(UTC)
                    count += 1
        return count


class BoundedWorkerLogCollector:
    def __init__(self, maximum_bytes: int) -> None:
        if not 500 <= maximum_bytes <= 2_000:
            raise ValueError("worker log limit is outside the supported range")
        self.maximum_bytes = maximum_bytes

    def collect(self, lines: list[str]) -> tuple[str, bool]:
        sanitized = "\n".join(self._sanitize(line) for line in lines)
        encoded = sanitized.encode("utf-8")
        truncated = len(encoded) > self.maximum_bytes
        if truncated:
            encoded = encoded[: self.maximum_bytes]
        return encoded.decode("utf-8", errors="ignore"), truncated

    @staticmethod
    def _sanitize(value: str) -> str:
        return HOST_PATH_PATTERN.sub("[HOST_PATH]", sanitize_text(value))


class WorkerResultValidator:
    """Reject stale, malformed, or unverified success results."""

    def validate(self, request: WorkerRequest, result: WorkerResult) -> None:
        if result.build_id != request.build_id or result.correlation_id != request.correlation_id:
            raise ValueError("worker result does not match the submitted request")
        if result.status not in {
            WorkerJobStatus.succeeded,
            WorkerJobStatus.failed,
            WorkerJobStatus.cancelled,
            WorkerJobStatus.timed_out,
        }:
            raise ValueError("worker returned a non-final execution status")
        if result.started_at is None:
            raise ValueError("worker result is missing its start timestamp")
        if result.started_at.tzinfo is None or result.completed_at.tzinfo is None:
            raise ValueError("worker timestamps must be timezone-aware")
        if result.started_at and result.completed_at < result.started_at:
            raise ValueError("worker completion time precedes its start time")
        if result.status is WorkerJobStatus.succeeded:
            valid_digest = result.image_digest and IMAGE_DIGEST_PATTERN.fullmatch(result.image_digest)
            if result.exit_code != 0 or not valid_digest:
                raise ValueError("successful worker result lacks a verified immutable image digest")
            if result.failure_category is not None:
                raise ValueError("successful worker result cannot contain a failure category")
        else:
            if result.image_digest is not None:
                raise ValueError("non-successful worker result must not publish an image digest")
            if result.failure_category is None:
                raise ValueError("failed worker result must include a failure category")
        if result.resource_usage:
            if (
                result.resource_usage.peak_memory_megabytes is not None
                and result.resource_usage.peak_memory_megabytes > request.resources.memory_megabytes
            ):
                raise ValueError("worker result reports memory use above the approved limit")
            if (
                result.resource_usage.storage_megabytes is not None
                and result.resource_usage.storage_megabytes > request.resources.storage_megabytes
            ):
                raise ValueError("worker result reports storage use above the approved limit")


class UnavailableIsolationRuntime:
    """Safe production default when no verified isolation boundary exists."""

    def __init__(self, reason: str) -> None:
        self._capabilities = IsolationCapabilities(
            runtime_name="disabled",
            available=False,
            verified_security_boundary=False,
            reason=reason,
        )

    @property
    def capabilities(self) -> IsolationCapabilities:
        return self._capabilities

    def execute(self, request: WorkerRequest, snapshot: VerifiedSnapshotDescriptor) -> WorkerResult:
        raise RuntimeError(self._capabilities.reason)

    def terminate(self, job_id: str) -> bool:
        return False

    def cleanup(self, job_id: str) -> bool:
        return True


class BuildWorkerProvider:
    """Coordinator-side provider; runtime execution is injected and capability-gated."""

    def __init__(
        self,
        verifier: SnapshotVerifier,
        runtime: IsolationRuntime,
        lifecycle: WorkerLifecycleManager,
        log_collector: BoundedWorkerLogCollector,
    ) -> None:
        self._verifier = verifier
        self._runtime = runtime
        self._lifecycle = lifecycle
        self._logs = log_collector
        self._validator = WorkerResultValidator()

    @property
    def available(self) -> bool:
        return self._runtime.capabilities.satisfies_required_boundary

    @property
    def availability_reason(self) -> str:
        return self._runtime.capabilities.reason or "The isolation runtime does not satisfy the required boundary."

    def submit(self, request: WorkerRequest, job_id: str) -> tuple[WorkerResult, str, bool]:
        job = self._lifecycle.enqueue(request, job_id)
        if job.result is not None:
            logs, truncated = self._logs.collect(job.result.logs)
            return job.result, logs, truncated
        if not self.available:
            self._lifecycle.transition(job.job_id, WorkerJobStatus.rejected)
            raise RuntimeError(self.availability_reason)
        try:
            snapshot = self._verifier.verify_for_worker(request)
            self._validate_snapshot(request, snapshot)
        except Exception:
            self._lifecycle.transition(job.job_id, WorkerJobStatus.rejected)
            raise
        self._lifecycle.transition(job.job_id, WorkerJobStatus.starting)
        try:
            self._lifecycle.transition(job.job_id, WorkerJobStatus.running)
            result = self._runtime.execute(request, snapshot)
            self._validator.validate(request, result)
            if result.job_id != job.job_id:
                raise ValueError("worker returned a stale or unrelated job identifier")
            cleanup_ok = self._runtime.cleanup(job.job_id)
            if not cleanup_ok or result.cleanup_status is not WorkerCleanupStatus.succeeded:
                self._lifecycle.transition(job.job_id, WorkerJobStatus.cleanup_failed, result)
                raise RuntimeError("isolated worker cleanup failed")
            self._lifecycle.transition(job.job_id, result.status, result)
            logs, truncated = self._logs.collect(result.logs)
            return result, logs, truncated
        except TimeoutError:
            self._runtime.terminate(job.job_id)
            cleanup_ok = self._runtime.cleanup(job.job_id)
            self._lifecycle.transition(
                job.job_id,
                WorkerJobStatus.timed_out if cleanup_ok else WorkerJobStatus.cleanup_failed,
            )
            raise
        except Exception:
            current = self._lifecycle.get(job.job_id)
            if current and current.status in {WorkerJobStatus.starting, WorkerJobStatus.running}:
                cleanup_ok = self._runtime.cleanup(job.job_id)
                self._lifecycle.transition(
                    job.job_id,
                    WorkerJobStatus.failed if cleanup_ok else WorkerJobStatus.cleanup_failed,
                )
            raise

    def cancel(self, job_id: str) -> bool:
        capabilities = self._runtime.capabilities
        job = self._lifecycle.get(job_id)
        if not job or not capabilities.reliable_termination:
            return False
        if job.status not in {WorkerJobStatus.queued, WorkerJobStatus.starting, WorkerJobStatus.running}:
            return False
        if not self._runtime.terminate(job_id):
            return False
        if not self._runtime.cleanup(job_id):
            self._lifecycle.transition(job_id, WorkerJobStatus.cleanup_failed)
            return False
        self._lifecycle.transition(job_id, WorkerJobStatus.cancelled)
        return True

    @staticmethod
    def _validate_snapshot(request: WorkerRequest, snapshot: VerifiedSnapshotDescriptor) -> None:
        if (
            snapshot.snapshot_id != request.snapshot_id
            or snapshot.project_id != request.project_id
            or snapshot.integrity_hash != request.snapshot_integrity_hash
            or snapshot.format_version != request.snapshot_format_version
        ):
            raise ValueError("worker snapshot verification did not match the submitted request")
        if not snapshot.submission_complete:
            raise ValueError("an incomplete local snapshot cannot be used as a complete build context")

