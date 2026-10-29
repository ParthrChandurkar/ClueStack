"""Worker-contract tests use fakes and do not establish real runtime isolation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.build_workers import (
    BoundedWorkerLogCollector,
    BuildWorkerProvider,
    IsolationCapabilities,
    UnavailableIsolationRuntime,
    VerifiedSnapshotDescriptor,
    WorkerCleanupStatus,
    WorkerFailureCategory,
    WorkerJobStatus,
    WorkerLifecycleManager,
    WorkerRequest,
    WorkerResourcePolicy,
    WorkerResult,
    WorkerResultValidator,
)

DIGEST = f"sha256:{'a' * 64}"
INTEGRITY = "b" * 64


def policy(**updates) -> WorkerResourcePolicy:
    values = {
        "cpu_limit": 1.0,
        "memory_megabytes": 512,
        "storage_megabytes": 1_024,
        "process_limit": 64,
        "timeout_seconds": 300,
        "max_log_bytes": 1_000,
    }
    values.update(updates)
    return WorkerResourcePolicy(**values)


def request(**updates) -> WorkerRequest:
    values = {
        "build_id": str(uuid4()),
        "project_id": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "snapshot_integrity_hash": INTEGRITY,
        "snapshot_format_version": 1,
        "context_path": ".",
        "dockerfile_path": "Dockerfile",
        "image_name": "example/service",
        "image_tag": "test-1",
        "resources": policy(),
        "network_policy": "disabled",
        "execution_timeout_seconds": 300,
        "correlation_id": str(uuid4()),
    }
    values.update(updates)
    return WorkerRequest(**values)


class FakeVerifier:
    def __init__(self, descriptor: VerifiedSnapshotDescriptor | None = None, error: Exception | None = None) -> None:
        self.descriptor = descriptor
        self.error = error

    def verify_for_worker(self, worker_request: WorkerRequest) -> VerifiedSnapshotDescriptor:
        if self.error:
            raise self.error
        return self.descriptor or VerifiedSnapshotDescriptor(
            snapshot_id=worker_request.snapshot_id,
            project_id=worker_request.project_id,
            integrity_hash=worker_request.snapshot_integrity_hash,
            format_version=worker_request.snapshot_format_version,
            submission_complete=True,
        )


class FakeRuntime:
    def __init__(
        self,
        *,
        result: WorkerResult | None = None,
        error: Exception | None = None,
        cleanup_ok: bool = True,
        reliable_termination: bool = True,
    ) -> None:
        self.result = result
        self.error = error
        self.cleanup_ok = cleanup_ok
        self.terminated: list[str] = []
        self.cleaned: list[str] = []
        self._capabilities = IsolationCapabilities(
            runtime_name="test-fake",
            available=True,
            verified_security_boundary=True,
            unprivileged=True,
            read_only_base=True,
            dedicated_writable_storage=True,
            snapshot_only_access=True,
            process_limits=True,
            resource_limits=True,
            network_disabled=True,
            reliable_termination=reliable_termination,
            reliable_cleanup=True,
            reason="Synthetic test runtime only.",
        )

    @property
    def capabilities(self) -> IsolationCapabilities:
        return self._capabilities

    def execute(self, worker_request, snapshot):
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result

    def terminate(self, job_id):
        self.terminated.append(job_id)
        return True

    def cleanup(self, job_id):
        self.cleaned.append(job_id)
        return self.cleanup_ok


def result(worker_request: WorkerRequest, job_id: str, **updates) -> WorkerResult:
    now = datetime.now(UTC)
    values = {
        "job_id": job_id,
        "build_id": worker_request.build_id,
        "correlation_id": worker_request.correlation_id,
        "status": WorkerJobStatus.succeeded,
        "exit_code": 0,
        "image_digest": DIGEST,
        "logs": ["Build complete"],
        "started_at": now,
        "completed_at": now + timedelta(seconds=1),
        "cleanup_status": WorkerCleanupStatus.succeeded,
    }
    values.update(updates)
    return WorkerResult(**values)


def provider(verifier, runtime):
    return BuildWorkerProvider(
        verifier,
        runtime,
        WorkerLifecycleManager(max_concurrent_jobs=1, max_queued_jobs=2),
        BoundedWorkerLogCollector(1_000),
    )


def test_worker_request_validates_ids_paths_images_and_timeout() -> None:
    assert request().network_policy == "disabled"
    for field, value in (
        ("dockerfile_path", "../Dockerfile"),
        ("context_path", "C:/source"),
        ("image_name", "UPPER/name"),
        ("image_tag", "bad tag"),
    ):
        with pytest.raises(ValidationError):
            request(**{field: value})
    with pytest.raises(ValidationError):
        request(execution_timeout_seconds=60)
    with pytest.raises(ValidationError):
        WorkerRequest(**{**request().model_dump(), "environment": {"TOKEN": "synthetic"}})


def test_resource_policy_rejects_missing_and_excessive_limits() -> None:
    with pytest.raises(ValidationError):
        WorkerResourcePolicy(cpu_limit=1, memory_megabytes=512)
    with pytest.raises(ValidationError):
        policy(cpu_limit=32)
    with pytest.raises(ValidationError):
        policy(storage_megabytes=100_000)


def test_snapshot_mismatch_and_incomplete_submission_are_rejected() -> None:
    worker_request = request()
    bad_project = VerifiedSnapshotDescriptor(
        snapshot_id=worker_request.snapshot_id,
        project_id=str(uuid4()),
        integrity_hash=INTEGRITY,
        format_version=1,
        submission_complete=True,
    )
    for descriptor in (
        bad_project,
        VerifiedSnapshotDescriptor(
            snapshot_id=worker_request.snapshot_id,
            project_id=worker_request.project_id,
            integrity_hash=INTEGRITY,
            format_version=1,
            submission_complete=False,
        ),
    ):
        job_id = str(uuid4())
        runtime = FakeRuntime(result=result(worker_request, job_id))
        worker = provider(FakeVerifier(descriptor), runtime)
        with pytest.raises(ValueError):
            worker.submit(worker_request, job_id)
        assert runtime.cleaned == []


def test_unavailable_runtime_rejects_without_verifying_or_executing() -> None:
    worker = provider(
        FakeVerifier(error=AssertionError("verifier must not be reached")),
        UnavailableIsolationRuntime("No verified runtime."),
    )
    assert worker.available is False
    with pytest.raises(RuntimeError, match="No verified runtime"):
        worker.submit(request(), str(uuid4()))


def test_success_requires_valid_digest_matching_result_and_cleanup() -> None:
    worker_request = request()
    job_id = str(uuid4())
    runtime = FakeRuntime(result=result(worker_request, job_id))
    worker = provider(FakeVerifier(), runtime)
    output, logs, truncated = worker.submit(worker_request, job_id)
    assert output.status is WorkerJobStatus.succeeded
    assert output.image_digest == DIGEST
    assert logs == "Build complete"
    assert truncated is False
    assert runtime.cleaned == [job_id]


def test_zero_exit_without_digest_and_stale_result_are_rejected() -> None:
    worker_request = request()
    validator = WorkerResultValidator()
    with pytest.raises(ValueError, match="digest"):
        validator.validate(worker_request, result(worker_request, str(uuid4()), image_digest=None))
    with pytest.raises(ValueError, match="match"):
        validator.validate(
            worker_request,
            result(worker_request, str(uuid4()), correlation_id=str(uuid4())),
        )


def test_startup_crash_and_timeout_fail_without_success() -> None:
    for error in (RuntimeError("worker crashed"), TimeoutError("deadline")):
        worker_request = request(correlation_id=str(uuid4()))
        job_id = str(uuid4())
        runtime = FakeRuntime(error=error)
        worker = provider(FakeVerifier(), runtime)
        with pytest.raises(type(error)):
            worker.submit(worker_request, job_id)
        assert runtime.cleaned == [job_id]
        if isinstance(error, TimeoutError):
            assert runtime.terminated == [job_id]


def test_cleanup_failure_prevents_success() -> None:
    worker_request = request()
    job_id = str(uuid4())
    runtime = FakeRuntime(result=result(worker_request, job_id), cleanup_ok=False)
    with pytest.raises(RuntimeError, match="cleanup"):
        provider(FakeVerifier(), runtime).submit(worker_request, job_id)


def test_duplicate_correlation_returns_same_completed_result() -> None:
    worker_request = request()
    job_id = str(uuid4())
    runtime = FakeRuntime(result=result(worker_request, job_id))
    worker = provider(FakeVerifier(), runtime)
    first = worker.submit(worker_request, job_id)
    second = worker.submit(worker_request, str(uuid4()))
    assert first == second
    assert runtime.cleaned == [job_id]

    with pytest.raises(ValueError, match="different request"):
        worker.submit(
            request(correlation_id=worker_request.correlation_id, build_id=str(uuid4())),
            str(uuid4()),
        )


def test_cancellation_requires_reliable_complete_runtime_termination() -> None:
    worker_request = request()
    job_id = str(uuid4())
    lifecycle = WorkerLifecycleManager(1, 2)
    lifecycle.enqueue(worker_request, job_id)
    runtime = FakeRuntime(reliable_termination=True)
    worker = BuildWorkerProvider(FakeVerifier(), runtime, lifecycle, BoundedWorkerLogCollector(1_000))
    assert worker.cancel(job_id) is True
    assert runtime.terminated == [job_id]
    assert runtime.cleaned == [job_id]


def test_log_collection_redacts_secrets_host_paths_and_bounds_bytes() -> None:
    collector = BoundedWorkerLogCollector(500)
    logs, truncated = collector.collect(
        [
            "Authorization: Bearer synthetic-secret",
            "read C:\\Users\\person\\source\\Dockerfile and /tmp/private/build/context",
            "x" * 600,
        ]
    )
    assert "synthetic-secret" not in logs
    assert "C:\\Users" not in logs
    assert "/tmp/private" not in logs
    assert "[HOST_PATH]" in logs
    assert truncated is True
    assert len(logs.encode()) <= 500


def test_lifecycle_enforces_queue_concurrency_transitions_and_recovery() -> None:
    lifecycle = WorkerLifecycleManager(1, 2)
    first = lifecycle.enqueue(request(), str(uuid4()))
    second = lifecycle.enqueue(request(), str(uuid4()))
    lifecycle.transition(first.job_id, WorkerJobStatus.starting)
    with pytest.raises(RuntimeError, match="concurrency"):
        lifecycle.transition(second.job_id, WorkerJobStatus.starting)
    lifecycle.transition(first.job_id, WorkerJobStatus.running)
    with pytest.raises(ValueError, match="invalid"):
        lifecycle.transition(first.job_id, WorkerJobStatus.queued)
    assert lifecycle.recover_interrupted() == 2


def test_provider_recreation_does_not_resume_untrusted_in_process_work() -> None:
    runtime = UnavailableIsolationRuntime("No verified runtime.")
    original = provider(FakeVerifier(), runtime)
    with pytest.raises(RuntimeError):
        original.submit(request(), str(uuid4()))
    recreated = provider(FakeVerifier(), runtime)
    assert recreated.available is False
    assert recreated._lifecycle.recover_interrupted() == 0


def test_malformed_worker_result_is_rejected_by_schema() -> None:
    with pytest.raises(ValidationError):
        WorkerResult(
            job_id="not-a-uuid",
            build_id="bad",
            correlation_id="bad",
            status="invented",
            completed_at=datetime.now(UTC),
        )
    assert WorkerFailureCategory.invalid_result.value == "invalid_result"

