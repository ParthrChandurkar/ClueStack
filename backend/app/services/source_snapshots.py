"""Bounded, integrity-checked source snapshots for future isolated builds."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas import (
    ProjectMode,
    RepositoryAnalysisSource,
    SourceSnapshotCreateRequest,
    SourceSnapshotExclusion,
    SourceSnapshotFileManifest,
    SourceSnapshotIdentity,
    SourceSnapshotRecord,
    SourceSnapshotStatus,
    SourceSnapshotVerification,
)
from app.services.build_workers import VerifiedSnapshotDescriptor, WorkerRequest
from app.services.container_builds import ContainerBuildError, PreparedBuildSource
from app.services.projects import ProjectNotFoundError, ProjectService
from app.services.reports import ReportService
from app.services.repository_analysis import GITHUB_API_ORIGIN, parse_github_repository_url

FORMAT_VERSION = 1
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
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
SENSITIVE_PARTS = {
    ".env",
    "credential",
    "credentials",
    "secret",
    "secrets",
    "private-key",
    "private_key",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
    "kubeconfig",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".der"}
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
SENSITIVE_CONTENT = re.compile(
    rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    rb"^\s*(?:password|api[_-]?key|secret|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+\-=]{16,}",
    re.IGNORECASE | re.MULTILINE,
)


class SourceSnapshotError(RuntimeError):
    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SnapshotMetadataRepository(Protocol):
    def create(self, record: SourceSnapshotRecord) -> SourceSnapshotRecord: ...
    def update(self, record: SourceSnapshotRecord) -> SourceSnapshotRecord: ...
    def get(self, snapshot_id: str) -> SourceSnapshotRecord | None: ...
    def list(self, project_id: str) -> list[SourceSnapshotRecord]: ...


class FileSnapshotMetadataRepository:
    """Atomic JSON metadata persistence; source bytes live in the snapshot root."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def create(self, record: SourceSnapshotRecord) -> SourceSnapshotRecord:
        with self._lock:
            records = self._read()
            if record.id in records:
                raise SourceSnapshotError("Snapshot ID already exists.", status_code=409, code="snapshot_exists")
            records[record.id] = record
            self._write(records)
        return record

    def update(self, record: SourceSnapshotRecord) -> SourceSnapshotRecord:
        with self._lock:
            records = self._read()
            if record.id not in records:
                raise SourceSnapshotError("Snapshot not found.", status_code=404, code="snapshot_not_found")
            records[record.id] = record
            self._write(records)
        return record

    def get(self, snapshot_id: str) -> SourceSnapshotRecord | None:
        with self._lock:
            return self._read().get(snapshot_id)

    def list(self, project_id: str) -> list[SourceSnapshotRecord]:
        with self._lock:
            records = self._read().values()
        return sorted(
            (item for item in records if item.project_id == project_id), key=lambda item: item.created_at, reverse=True
        )

    def _read(self) -> dict[str, SourceSnapshotRecord]:
        if not self._path.exists():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("metadata root must be an object")
            records = {key: SourceSnapshotRecord.model_validate(item) for key, item in value.items()}
        except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise SourceSnapshotError(
                "Snapshot metadata is unavailable or malformed; no data was overwritten.",
                status_code=500,
                code="snapshot_metadata_invalid",
            ) from exc
        if any(key != record.id for key, record in records.items()):
            raise SourceSnapshotError(
                "Snapshot metadata identity mismatch.", status_code=500, code="snapshot_metadata_invalid"
            )
        return records

    def _write(self, records: dict[str, SourceSnapshotRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps({key: value.model_dump(mode="json") for key, value in records.items()}, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)


class GitHubSnapshotAcquirer:
    """Acquire exact public GitHub commit contents through api.github.com only."""

    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def acquire(
        self, repository_url: str, reference: str | None
    ) -> tuple[SourceSnapshotIdentity, list[tuple[str, bytes]], Counter[str]]:
        owner, repository = parse_github_repository_url(repository_url)
        deadline = time.monotonic() + self._settings.source_snapshot_timeout_seconds
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ClueStack-source-snapshot",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(
            base_url=GITHUB_API_ORIGIN,
            headers=headers,
            timeout=httpx.Timeout(self._settings.github_api_timeout_seconds),
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            metadata = await self._json(client, f"/repos/{quote(owner)}/{quote(repository)}", deadline)
            resolved_reference = reference or _string(metadata, "default_branch")
            commit = await self._json(
                client,
                f"/repos/{quote(owner)}/{quote(repository)}/commits/{quote(resolved_reference, safe='')}",
                deadline,
            )
            commit_sha = _string(commit, "sha").lower()
            if not COMMIT_PATTERN.fullmatch(commit_sha):
                raise SourceSnapshotError(
                    "GitHub returned an invalid commit identity.", status_code=502, code="invalid_github_response"
                )
            git_commit = await self._json(
                client, f"/repos/{quote(owner)}/{quote(repository)}/git/commits/{commit_sha}", deadline
            )
            tree_ref = git_commit.get("tree")
            if not isinstance(tree_ref, dict):
                raise SourceSnapshotError(
                    "GitHub commit omitted its tree.", status_code=502, code="invalid_github_response"
                )
            tree_sha = _string(tree_ref, "sha")
            tree = await self._json(
                client,
                f"/repos/{quote(owner)}/{quote(repository)}/git/trees/{quote(tree_sha)}",
                deadline,
                params={"recursive": "1"},
            )
            entries = tree.get("tree")
            if not isinstance(entries, list):
                raise SourceSnapshotError(
                    "GitHub returned an invalid tree.", status_code=502, code="invalid_github_response"
                )
            if tree.get("truncated") is True:
                raise SourceSnapshotError(
                    "GitHub truncated the repository tree; an exact snapshot cannot be created.",
                    status_code=422,
                    code="snapshot_tree_truncated",
                )
            if len(entries) > self._settings.source_snapshot_max_files * 20:
                raise SourceSnapshotError(
                    "Repository tree exceeds the snapshot entry limit.", status_code=413, code="snapshot_tree_limit"
                )
            candidates: list[tuple[str, str, int]] = []
            excluded: Counter[str] = Counter()
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("type") != "blob":
                    continue
                path = entry.get("path")
                sha = entry.get("sha")
                size = entry.get("size")
                if not isinstance(path, str) or not isinstance(sha, str) or not isinstance(size, int):
                    raise SourceSnapshotError(
                        "GitHub tree contained invalid file metadata.", status_code=502, code="invalid_github_response"
                    )
                normalized = normalize_snapshot_path(path)
                if entry.get("mode") == "120000":
                    excluded["symbolic_link"] += 1
                    continue
                reason = exclusion_reason(normalized)
                if reason:
                    excluded[reason] += 1
                    continue
                if size > self._settings.source_snapshot_max_file_bytes:
                    excluded["file_too_large"] += 1
                    continue
                candidates.append((normalized, sha, size))
            _check_counts(self._settings, candidates)
            if len({path.casefold() for path, _, _ in candidates}) != len(candidates):
                raise SourceSnapshotError(
                    "Repository contains paths that collide on case-insensitive storage.",
                    status_code=422,
                    code="duplicate_path",
                )
            files: list[tuple[str, bytes]] = []
            total = 0
            for path, blob_sha, declared_size in sorted(candidates):
                blob = await self._json(
                    client, f"/repos/{quote(owner)}/{quote(repository)}/git/blobs/{quote(blob_sha)}", deadline
                )
                if blob.get("encoding") != "base64" or not isinstance(blob.get("content"), str):
                    raise SourceSnapshotError(
                        "GitHub returned an unsupported blob encoding.", status_code=502, code="unsupported_blob"
                    )
                try:
                    encoded_content = "".join(blob["content"].split())
                    content = base64.b64decode(encoded_content, validate=True)
                except (ValueError, TypeError) as exc:
                    raise SourceSnapshotError(
                        "GitHub returned invalid blob content.", status_code=502, code="invalid_github_response"
                    ) from exc
                if len(content) != declared_size or len(content) > self._settings.source_snapshot_max_file_bytes:
                    raise SourceSnapshotError(
                        "GitHub blob size did not match the exact tree.", status_code=502, code="blob_size_mismatch"
                    )
                if _is_binary(content):
                    excluded["unsupported_binary"] += 1
                    continue
                if _sensitive_content(content):
                    excluded["sensitive_content"] += 1
                    continue
                total += len(content)
                if total > self._settings.source_snapshot_max_total_bytes:
                    raise SourceSnapshotError(
                        "Snapshot content exceeds the aggregate limit.", status_code=413, code="snapshot_size_limit"
                    )
                files.append((path, content))
        identity = SourceSnapshotIdentity(
            repository_url=f"https://github.com/{owner}/{repository}",
            owner=owner,
            repository=repository,
            commit_sha=commit_sha,
            resolved_reference=resolved_reference,
        )
        return identity, files, excluded

    async def _json(
        self, client: httpx.AsyncClient, path: str, deadline: float, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceSnapshotError("Snapshot acquisition timed out.", status_code=504, code="snapshot_timeout")
        try:
            response = await client.get(
                path, params=params, timeout=min(remaining, self._settings.github_api_timeout_seconds)
            )
        except httpx.TimeoutException as exc:
            raise SourceSnapshotError(
                "GitHub snapshot acquisition timed out.", status_code=504, code="github_timeout"
            ) from exc
        except httpx.RequestError as exc:
            raise SourceSnapshotError(
                "GitHub could not be reached for snapshot acquisition.", status_code=502, code="github_unavailable"
            ) from exc
        if response.status_code == 404:
            raise SourceSnapshotError(
                "Repository or requested reference was not found.", status_code=404, code="source_not_found"
            )
        if response.status_code in {403, 429}:
            raise SourceSnapshotError(
                "GitHub rate limits prevented snapshot acquisition.", status_code=429, code="github_rate_limited"
            )
        if not 200 <= response.status_code < 300:
            raise SourceSnapshotError(
                f"GitHub snapshot acquisition failed with status {response.status_code}.",
                status_code=502,
                code="github_api_error",
            )
        if len(response.content) > self._settings.github_max_api_response_bytes:
            raise SourceSnapshotError(
                "GitHub response exceeded the configured limit.", status_code=502, code="github_response_too_large"
            )
        try:
            value = response.json()
        except ValueError as exc:
            raise SourceSnapshotError(
                "GitHub returned invalid JSON.", status_code=502, code="invalid_github_response"
            ) from exc
        if not isinstance(value, dict):
            raise SourceSnapshotError(
                "GitHub returned an invalid response shape.", status_code=502, code="invalid_github_response"
            )
        return value


class SourceSnapshotService:
    def __init__(
        self,
        settings: Settings,
        projects: ProjectService,
        reports: ReportService,
        metadata: SnapshotMetadataRepository,
        github: GitHubSnapshotAcquirer,
    ) -> None:
        self._settings = settings
        self._projects = projects
        self._reports = reports
        self._metadata = metadata
        self._github = github
        self._root = Path(settings.source_snapshots_root).resolve()
        checkout_root = Path(__file__).resolve().parents[3]
        if self._root == checkout_root or checkout_root in self._root.parents:
            raise SourceSnapshotError(
                "Snapshot storage must be outside the application source checkout.",
                status_code=500,
                code="unsafe_snapshot_root",
            )
        self._recover_incomplete_publications()
        self.cleanup_expired()

    async def create(self, project_id: str, payload: SourceSnapshotCreateRequest) -> SourceSnapshotRecord:
        project = self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError("Project not found")
        snapshot_id = str(uuid4())
        self._root.mkdir(parents=True, exist_ok=True)
        source = (
            RepositoryAnalysisSource.github_public
            if project.mode is ProjectMode.github_repository
            else RepositoryAnalysisSource.local_upload
        )
        created = datetime.now(UTC)
        record = SourceSnapshotRecord(
            id=snapshot_id,
            project_id=project_id,
            source=source,
            source_identity=SourceSnapshotIdentity(
                submission_id=str(uuid4()) if source is RepositoryAnalysisSource.local_upload else None
            ),
            analysis_report_id=payload.analysis_report_id,
            created_at=created,
            expires_at=created + timedelta(seconds=self._settings.source_snapshot_retention_seconds),
            file_count=0,
            total_content_size=0,
            status=SourceSnapshotStatus.creating,
        )
        self._metadata.create(record)
        staging: Path | None = None
        final: Path | None = None
        try:
            analysis = self._validate_analysis(project_id, source, payload.analysis_report_id)
            if source is RepositoryAnalysisSource.github_public:
                if payload.files:
                    raise SourceSnapshotError(
                        "GitHub snapshots do not accept client-submitted files.",
                        status_code=422,
                        code="unexpected_files",
                    )
                identity, files, excluded = await self._github.acquire(project.repository_url or "", payload.reference)
                if analysis and analysis.commit_sha and analysis.commit_sha != identity.commit_sha:
                    raise SourceSnapshotError(
                        "Snapshot commit does not match the selected analysis.",
                        status_code=409,
                        code="snapshot_analysis_mismatch",
                    )
            else:
                if payload.reference:
                    raise SourceSnapshotError(
                        "Local snapshots do not accept a Git reference.", status_code=422, code="unexpected_reference"
                    )
                identity, files, excluded = self._local_files(payload, record.source_identity.submission_id or "")
            manifest = _manifest(files)
            integrity = integrity_hash(identity, manifest)
            if any(
                existing.id != snapshot_id
                and existing.status is SourceSnapshotStatus.available
                and (
                    existing.integrity_hash == integrity
                    or (
                        source is RepositoryAnalysisSource.local_upload
                        and existing.included_files == manifest
                    )
                )
                for existing in self._metadata.list(project_id)
            ):
                raise SourceSnapshotError(
                    "An identical available source snapshot already exists for this project.",
                    status_code=409,
                    code="duplicate_snapshot",
                )
            staging = self._root / f".{snapshot_id}.{uuid4().hex}.creating"
            final = self._snapshot_path(snapshot_id)
            staging.mkdir(parents=False, exist_ok=False)
            for entry, (_, content) in zip(manifest, sorted(files), strict=True):
                destination = staging / Path(*PurePosixPath(entry.path).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            (staging / "manifest.json").write_text(canonical_manifest(identity, manifest), encoding="utf-8")
            os.replace(staging, final)
            _request_read_only(final)
            available = record.model_copy(
                update={
                    "source_identity": identity,
                    "included_files": manifest,
                    "exclusions": [
                        SourceSnapshotExclusion(reason=key, count=value) for key, value in sorted(excluded.items())
                    ],
                    "file_count": len(manifest),
                    "total_content_size": sum(item.size for item in manifest),
                    "integrity_hash": integrity,
                    "status": SourceSnapshotStatus.available,
                    "integrity_verified": False,
                    "read_only_requested": True,
                }
            )
            if not self._verify_record(available):
                raise SourceSnapshotError(
                    "Published snapshot failed integrity verification.",
                    status_code=500,
                    code="snapshot_integrity_failed",
                )
            return self._metadata.update(available.model_copy(update={"integrity_verified": True}))
        except Exception as exc:
            if staging and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if final and final.exists():
                _make_writable(final)
                shutil.rmtree(final, ignore_errors=True)
            failed = record.model_copy(update={"status": SourceSnapshotStatus.failed, "error": _safe_error(exc)})
            self._metadata.update(failed)
            raise

    def list(self, project_id: str) -> list[SourceSnapshotRecord]:
        self._require_project(project_id)
        records = self._metadata.list(project_id)
        now = datetime.now(UTC)
        result = []
        for record in records:
            if record.status is SourceSnapshotStatus.available and record.expires_at and record.expires_at <= now:
                record = self._metadata.update(
                    record.model_copy(update={"status": SourceSnapshotStatus.expired, "integrity_verified": False})
                )
            result.append(record)
        return result

    def get(self, project_id: str, snapshot_id: str) -> SourceSnapshotRecord:
        self._require_project(project_id)
        record = self._metadata.get(snapshot_id)
        if record is None or record.project_id != project_id:
            raise SourceSnapshotError("Snapshot not found.", status_code=404, code="snapshot_not_found")
        return record

    def verify(self, project_id: str, snapshot_id: str) -> SourceSnapshotVerification:
        record = self.get(project_id, snapshot_id)
        valid = record.status is SourceSnapshotStatus.available and self._verify_record(record)
        updated = record.model_copy(update={"integrity_verified": valid})
        if not valid and record.status is SourceSnapshotStatus.available:
            updated = updated.model_copy(
                update={"status": SourceSnapshotStatus.failed, "error": "Snapshot integrity verification failed."}
            )
        self._metadata.update(updated)
        return SourceSnapshotVerification(
            snapshot_id=snapshot_id,
            valid=valid,
            status=updated.status,
            integrity_hash=updated.integrity_hash,
            verified_at=datetime.now(UTC),
        )

    def delete(self, project_id: str, snapshot_id: str) -> SourceSnapshotRecord:
        record = self.get(project_id, snapshot_id)
        if record.status in {SourceSnapshotStatus.creating, SourceSnapshotStatus.deleted}:
            raise SourceSnapshotError(
                "Snapshot cannot be deleted in its current state.", status_code=409, code="snapshot_not_deletable"
            )
        if any(
            report.evidence.get("source_snapshot_id") == snapshot_id
            for report in self._reports.list_reports(project_id=project_id)
        ):
            raise SourceSnapshotError(
                "Snapshot is referenced by build history and cannot be deleted.",
                status_code=409,
                code="snapshot_referenced",
            )
        path = self._snapshot_path(snapshot_id)
        if path.exists():
            _make_writable(path)
            shutil.rmtree(path)
        return self._metadata.update(
            record.model_copy(update={"status": SourceSnapshotStatus.deleted, "integrity_verified": False})
        )

    def cleanup_expired(self) -> int:
        count = 0
        for project in self._projects.list_projects():
            for record in self.list(project.id):
                if record.status is SourceSnapshotStatus.expired:
                    try:
                        self.delete(project.id, record.id)
                    except SourceSnapshotError:
                        continue
                    count += 1
        return count

    def build_availability(
        self,
        project_id: str,
        source: RepositoryAnalysisSource,
        snapshot_id: str | None,
        expected_identity: str | None,
        analysis_report_id: str | None,
    ) -> tuple[bool, str]:
        if not snapshot_id:
            return False, "An immutable source snapshot identifier is required."
        record = self._metadata.get(snapshot_id)
        if record is None or record.project_id != project_id or record.source is not source:
            return False, "The snapshot does not belong to this project and source."
        if record.status is not SourceSnapshotStatus.available:
            return False, f"The snapshot is {record.status.value}, not available."
        if record.expires_at and record.expires_at <= datetime.now(UTC):
            return False, "The snapshot has expired."
        if analysis_report_id and record.analysis_report_id != analysis_report_id:
            return False, "The snapshot does not match the selected repository analysis."
        if expected_identity and record.source_identity.commit_sha != expected_identity:
            return False, "The snapshot commit does not match the analyzed commit."
        if source is RepositoryAnalysisSource.local_upload and record.source_identity.submission_complete is not True:
            return False, "The local snapshot is incomplete and cannot be used as a complete build context."
        if not self._verify_record(record):
            return False, "Snapshot integrity verification failed."
        return True, "The available snapshot passed integrity verification."

    def prepare_build(
        self,
        project_id: str,
        source: RepositoryAnalysisSource,
        snapshot_id: str,
        expected_identity: str | None,
        analysis_report_id: str | None,
    ) -> PreparedBuildSource:
        available, reason = self.build_availability(
            project_id, source, snapshot_id, expected_identity, analysis_report_id
        )
        if not available:
            raise ContainerBuildError(reason, status_code=409, code="source_snapshot_unavailable")
        record = self.get(project_id, snapshot_id)
        assert record.integrity_hash is not None
        return PreparedBuildSource(
            snapshot_id=snapshot_id,
            project_id=project_id,
            integrity_hash=record.integrity_hash,
            format_version=record.format_version,
            submission_complete=record.source_identity.submission_complete is not False,
        )

    def verify_for_worker(self, request: WorkerRequest) -> VerifiedSnapshotDescriptor:
        """Re-verify immutable bytes and return metadata without exposing a host path."""

        record = self.get(request.project_id, request.snapshot_id)
        if record.status is not SourceSnapshotStatus.available or not self._verify_record(record):
            raise ValueError("worker snapshot is unavailable or failed integrity verification")
        if record.expires_at and record.expires_at <= datetime.now(UTC):
            raise ValueError("worker snapshot has expired")
        if (
            record.integrity_hash != request.snapshot_integrity_hash
            or record.format_version != request.snapshot_format_version
        ):
            raise ValueError("worker snapshot metadata does not match the request")
        return VerifiedSnapshotDescriptor(
            snapshot_id=record.id,
            project_id=record.project_id,
            integrity_hash=record.integrity_hash,
            format_version=record.format_version,
            submission_complete=record.source_identity.submission_complete is not False,
        )

    def _local_files(
        self, payload: SourceSnapshotCreateRequest, submission_id: str
    ) -> tuple[SourceSnapshotIdentity, list[tuple[str, bytes]], Counter[str]]:
        if not payload.files:
            raise SourceSnapshotError("Select at least one local source file.", status_code=422, code="snapshot_empty")
        if len(payload.files) > self._settings.source_snapshot_max_files:
            raise SourceSnapshotError(
                "Local submission exceeds the file-count limit.", status_code=413, code="snapshot_file_limit"
            )
        files: list[tuple[str, bytes]] = []
        seen: set[str] = set()
        excluded: Counter[str] = Counter()
        total = 0
        for item in payload.files:
            path = normalize_snapshot_path(item.path)
            collision_key = path.casefold()
            if collision_key in seen:
                raise SourceSnapshotError(
                    "Local submission contains duplicate normalized paths.", status_code=422, code="duplicate_path"
                )
            seen.add(collision_key)
            reason = exclusion_reason(path)
            if reason:
                excluded[reason] += 1
                continue
            content = item.content.encode("utf-8")
            if _is_binary(content):
                raise SourceSnapshotError(
                    "Local submission contains unsupported binary content.", status_code=422, code="unsupported_binary"
                )
            if _sensitive_content(content):
                excluded["sensitive_content"] += 1
                continue
            if len(content) > self._settings.source_snapshot_max_file_bytes:
                raise SourceSnapshotError(
                    "A local file exceeds the per-file limit.", status_code=413, code="snapshot_file_size_limit"
                )
            total += len(content)
            if total > self._settings.source_snapshot_max_total_bytes:
                raise SourceSnapshotError(
                    "Local submission exceeds the aggregate limit.", status_code=413, code="snapshot_size_limit"
                )
            files.append((path, content))
        if not files:
            raise SourceSnapshotError(
                "No eligible local source files remained after exclusions.", status_code=422, code="snapshot_empty"
            )
        identity = SourceSnapshotIdentity(submission_id=submission_id, submission_complete=payload.submission_complete)
        return identity, files, excluded

    def _validate_analysis(self, project_id: str, source: RepositoryAnalysisSource, report_id: str | None):
        if not report_id:
            return None
        report = self._reports.get(report_id)
        if report is None or report.project_id != project_id or report.operation.value != "repository_analysis":
            raise SourceSnapshotError(
                "Analysis report does not belong to this project.", status_code=409, code="snapshot_analysis_mismatch"
            )
        if report.evidence.get("source") != source.value:
            raise SourceSnapshotError(
                "Analysis source does not match the snapshot source.",
                status_code=409,
                code="snapshot_analysis_mismatch",
            )
        return report

    def _verify_record(self, record: SourceSnapshotRecord) -> bool:
        if record.integrity_hash is None or record.format_version != FORMAT_VERSION:
            return False
        root = self._snapshot_path(record.id)
        if not root.is_dir():
            return False
        actual: list[SourceSnapshotFileManifest] = []
        try:
            for expected in record.included_files:
                path = root / Path(*PurePosixPath(expected.path).parts)
                if not path.is_file():
                    return False
                content = path.read_bytes()
                actual.append(
                    SourceSnapshotFileManifest(
                        path=expected.path, size=len(content), sha256=hashlib.sha256(content).hexdigest()
                    )
                )
            manifest_text = (root / "manifest.json").read_text(encoding="utf-8")
        except OSError:
            return False
        return (
            actual == record.included_files
            and manifest_text == canonical_manifest(record.source_identity, record.included_files)
            and integrity_hash(record.source_identity, actual) == record.integrity_hash
        )

    def _snapshot_path(self, snapshot_id: str) -> Path:
        try:
            normalized = str(UUID(snapshot_id))
        except ValueError as exc:
            raise SourceSnapshotError(
                "Invalid snapshot identifier.", status_code=422, code="invalid_snapshot_id"
            ) from exc
        path = (self._root / normalized).resolve()
        if path.parent != self._root:
            raise SourceSnapshotError("Invalid snapshot path.", status_code=422, code="invalid_snapshot_id")
        return path

    def _recover_incomplete_publications(self) -> None:
        """Mark interrupted metadata as failed and remove only generated staging directories."""

        for project in self._projects.list_projects():
            for record in self._metadata.list(project.id):
                if record.status is SourceSnapshotStatus.creating:
                    self._metadata.update(
                        record.model_copy(
                            update={
                                "status": SourceSnapshotStatus.failed,
                                "error": "Snapshot creation was interrupted before atomic publication.",
                            }
                        )
                    )
        if not self._root.exists():
            return
        for candidate in self._root.glob(".*.creating"):
            if candidate.is_dir() and candidate.parent == self._root:
                _make_writable(candidate)
                shutil.rmtree(candidate, ignore_errors=True)

    def _require_project(self, project_id: str) -> None:
        if self._projects.get(project_id) is None:
            raise ProjectNotFoundError("Project not found")


class SnapshotBuildSourceProvider:
    def __init__(self, snapshots: SourceSnapshotService) -> None:
        self._snapshots = snapshots

    def availability(
        self,
        project_id: str,
        source: RepositoryAnalysisSource,
        snapshot_id: str | None,
        expected_identity: str | None,
        analysis_report_id: str | None,
    ) -> tuple[bool, str]:
        return self._snapshots.build_availability(
            project_id, source, snapshot_id, expected_identity, analysis_report_id
        )

    def prepare(
        self,
        project_id: str,
        source: RepositoryAnalysisSource,
        snapshot_id: str,
        expected_identity: str | None,
        analysis_report_id: str | None,
    ) -> PreparedBuildSource:
        return self._snapshots.prepare_build(project_id, source, snapshot_id, expected_identity, analysis_report_id)

    def cleanup(self, prepared: PreparedBuildSource) -> None:
        return None


def normalize_snapshot_path(value: str) -> str:
    if (
        not value
        or "\\" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
        or any(ord(char) < 32 for char in value)
    ):
        raise SourceSnapshotError(
            "Snapshot path must be a safe relative POSIX path.", status_code=422, code="unsafe_path"
        )
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise SourceSnapshotError(
            "Snapshot path contains traversal or unsafe components.", status_code=422, code="unsafe_path"
        )
    if any(
        ":" in part or part.endswith((" ", ".")) or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
        for part in raw_parts
    ):
        raise SourceSnapshotError(
            "Snapshot path contains an unsafe platform-specific component.",
            status_code=422,
            code="unsafe_path",
        )
    normalized = PurePosixPath(*raw_parts).as_posix()
    if len(normalized) > 512:
        raise SourceSnapshotError("Snapshot path exceeds the configured limit.", status_code=422, code="unsafe_path")
    return normalized


def exclusion_reason(path: str) -> str | None:
    parts = [part.lower() for part in PurePosixPath(path).parts]
    for part in parts:
        stem = part.rsplit(".", 1)[0] if "." in part else part
        if part in EXCLUDED_DIRECTORY_PARTS:
            return "generated_or_dependency_path"
        if (
            part in SENSITIVE_PARTS
            or stem in SENSITIVE_PARTS
            or part.startswith(".env")
            or any(part.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)
        ):
            return "sensitive_path"
    return None


def _manifest(files: list[tuple[str, bytes]]) -> list[SourceSnapshotFileManifest]:
    return [
        SourceSnapshotFileManifest(path=path, size=len(content), sha256=hashlib.sha256(content).hexdigest())
        for path, content in sorted(files)
    ]


def canonical_manifest(identity: SourceSnapshotIdentity, manifest: list[SourceSnapshotFileManifest]) -> str:
    value = {
        "format_version": FORMAT_VERSION,
        "source_identity": identity.model_dump(mode="json", exclude_none=True),
        "files": [item.model_dump(mode="json") for item in manifest],
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def integrity_hash(identity: SourceSnapshotIdentity, manifest: list[SourceSnapshotFileManifest]) -> str:
    return hashlib.sha256(canonical_manifest(identity, manifest).encode("utf-8")).hexdigest()


def _check_counts(settings: Settings, candidates: list[tuple[str, str, int]]) -> None:
    if len(candidates) > settings.source_snapshot_max_files:
        raise SourceSnapshotError(
            "Repository exceeds the snapshot file-count limit.", status_code=413, code="snapshot_file_limit"
        )
    if sum(size for _, _, size in candidates) > settings.source_snapshot_max_total_bytes:
        raise SourceSnapshotError(
            "Repository exceeds the aggregate snapshot limit.", status_code=413, code="snapshot_size_limit"
        )


def _is_binary(content: bytes) -> bool:
    if b"\x00" in content:
        return True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _sensitive_content(content: bytes) -> bool:
    return SENSITIVE_CONTENT.search(content) is not None


def _string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise SourceSnapshotError(
            "GitHub response omitted required source identity.", status_code=502, code="invalid_github_response"
        )
    return item


def _request_read_only(root: Path) -> None:
    for path in [*root.rglob("*"), root]:
        try:
            path.chmod(stat.S_IREAD | (stat.S_IEXEC if path.is_dir() else 0))
        except OSError:
            pass


def _make_writable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            path.chmod(stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if path.is_dir() else 0))
        except OSError:
            pass


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, SourceSnapshotError):
        return str(exc)[:500]
    return "Snapshot creation failed before atomic publication."
