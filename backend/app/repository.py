"""Small persistence layer for deployment records.

The repository uses a JSON file so the app works out of the box in local
development, while keeping the API boundary easy to replace with PostgreSQL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol
from urllib.parse import unquote, urlparse
from uuid import uuid4

import psycopg2
from psycopg2.extras import Json
from pydantic import ValidationError

from app.report_sanitizer import sanitize_report
from app.schemas import (
    AuditLogEntry,
    DeploymentRecord,
    DeploymentReportCreate,
    DeploymentReportRecord,
    ProjectCreate,
    ProjectMode,
    ProjectRecord,
    ProjectSourceType,
)


class DeploymentRepository(Protocol):
    """Persistence contract used by deployment orchestration."""

    def list(self) -> list[DeploymentRecord]:
        """Return all known deployments."""

    def list_by_project(self, project_id: str) -> list[DeploymentRecord]:
        """Return deployments explicitly associated with a project."""

    def get(self, name: str) -> DeploymentRecord | None:
        """Return one deployment record by name."""

    def upsert(self, record: DeploymentRecord) -> DeploymentRecord:
        """Insert or replace a deployment record."""

    def delete(self, name: str) -> DeploymentRecord | None:
        """Delete one deployment record if present."""


class AuditLogRepository(Protocol):
    """Persistence contract used by audit logging."""

    def append(
        self,
        *,
        action: str,
        status: str,
        message: str,
        service: str | None = None,
        actor: str = "system",
        metadata: dict | None = None,
    ) -> AuditLogEntry:
        """Append one immutable audit event."""

    def list(self, limit: int = 100) -> list[AuditLogEntry]:
        """Return newest audit entries first."""


class ProjectRepository(Protocol):
    """Persistence contract for ClueStack project metadata."""

    def list(self) -> list[ProjectRecord]:
        """Return all known projects."""

    def get(self, project_id: str) -> ProjectRecord | None:
        """Return one project by stable ID."""

    def create(self, project: ProjectCreate) -> ProjectRecord:
        """Create and persist a new project."""

    def get_or_create_default_local_project(self) -> ProjectRecord:
        """Return the default local project, creating it once if needed."""


class ReportRepository(Protocol):
    """Persistence contract for historical deployment reports."""

    def create(self, report: DeploymentReportCreate) -> DeploymentReportRecord:
        """Create and persist one report."""

    def list(
        self,
        *,
        project_id: str | None = None,
        deployment_id: str | None = None,
    ) -> list[DeploymentReportRecord]:
        """Return reports, optionally filtered by project or deployment."""

    def get(self, report_id: str) -> DeploymentReportRecord | None:
        """Return one report by stable ID."""


class ProjectStateError(RuntimeError):
    """Raised when project state cannot be safely loaded."""


class ReportStateError(RuntimeError):
    """Raised when report state cannot be safely loaded."""


class FileDeploymentRepository:
    """Thread-safe JSON-backed repository for deployment records."""

    def __init__(self, state_file: str) -> None:
        self._path = Path(state_file)
        self._lock = Lock()

    def list(self) -> list[DeploymentRecord]:
        """Return all known deployments sorted by most recent update."""

        with self._lock:
            records = self._read()
        return sorted(records.values(), key=lambda item: item.updated_at, reverse=True)

    def list_by_project(self, project_id: str) -> list[DeploymentRecord]:
        """Return deployments explicitly associated with a project."""

        return [record for record in self.list() if record.project_id == project_id]

    def get(self, name: str) -> DeploymentRecord | None:
        """Return one deployment record by name."""

        with self._lock:
            return self._read().get(name)

    def upsert(self, record: DeploymentRecord) -> DeploymentRecord:
        """Insert or replace a deployment record."""

        with self._lock:
            records = self._read()
            records[record.name] = record
            self._write(records)
        return record

    def delete(self, name: str) -> DeploymentRecord | None:
        """Delete one deployment record if present."""

        with self._lock:
            records = self._read()
            removed = records.pop(name, None)
            self._write(records)
        return removed

    def _read(self) -> dict[str, DeploymentRecord]:
        """Read the state file and hydrate Pydantic records."""

        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return {name: DeploymentRecord.model_validate(item) for name, item in raw.items()}

    def _write(self, records: dict[str, DeploymentRecord]) -> None:
        """Persist records with an atomic replace to avoid partial writes."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            name: record.model_dump(mode="json")
            for name, record in records.items()
        }
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)


class FileAuditLogRepository:
    """Thread-safe JSON-backed append-only audit log repository."""

    def __init__(self, audit_file: str, max_entries: int = 500) -> None:
        self._path = Path(audit_file)
        self._max_entries = max_entries
        self._lock = Lock()

    def append(
        self,
        *,
        action: str,
        status: str,
        message: str,
        service: str | None = None,
        actor: str = "system",
        metadata: dict | None = None,
    ) -> AuditLogEntry:
        """Append one immutable event and keep the newest records."""

        entry = AuditLogEntry(
            id=str(uuid4()),
            action=action,
            service=service,
            actor=actor,
            status=status,
            message=message,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        with self._lock:
            entries = self._read()
            entries.append(entry)
            entries = entries[-self._max_entries :]
            self._write(entries)
        return entry

    def list(self, limit: int = 100) -> list[AuditLogEntry]:
        """Return newest audit entries first."""

        with self._lock:
            entries = self._read()
        return list(reversed(entries))[:limit]

    def _read(self) -> list[AuditLogEntry]:
        """Read and hydrate persisted audit log entries."""

        if not self._path.exists():
            return []
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return [AuditLogEntry.model_validate(item) for item in raw]

    def _write(self, entries: list[AuditLogEntry]) -> None:
        """Persist audit entries with an atomic replace."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [entry.model_dump(mode="json") for entry in entries]
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)


class FileProjectRepository:
    """Thread-safe JSON-backed repository for ClueStack projects."""

    DEFAULT_LOCAL_SLUG = "default-local"

    def __init__(self, projects_file: str) -> None:
        self._path = Path(projects_file)
        self._lock = Lock()

    def list(self) -> list[ProjectRecord]:
        """Return all known projects sorted by creation time."""

        with self._lock:
            projects = self._read()
        return sorted(projects.values(), key=lambda item: item.created_at)

    def get(self, project_id: str) -> ProjectRecord | None:
        """Return one project by stable ID."""

        with self._lock:
            return self._read().get(project_id)

    def create(self, project: ProjectCreate) -> ProjectRecord:
        """Create a new project with a generated UUID and unique slug."""

        with self._lock:
            projects = self._read()
            slug = project.slug or slugify_project_name(project.name)
            if any(existing.slug == slug for existing in projects.values()):
                raise ValueError(f"project slug already exists: {slug}")
            now = utc_now()
            record = ProjectRecord(
                **project.model_dump(exclude={"slug"}),
                id=str(uuid4()),
                slug=slug,
                created_at=now,
                updated_at=now,
            )
            projects[record.id] = record
            self._write(projects)
        return record

    def get_or_create_default_local_project(self) -> ProjectRecord:
        """Return the default local project, creating it idempotently."""

        with self._lock:
            projects = self._read()
            for project in projects.values():
                if (
                    project.slug == self.DEFAULT_LOCAL_SLUG
                    and project.mode is ProjectMode.local
                    and project.source_type is ProjectSourceType.manual
                ):
                    return project

            record = build_default_local_project()
            projects[record.id] = record
            self._write(projects)
        return record

    def _read(self) -> dict[str, ProjectRecord]:
        """Read and validate persisted project metadata."""

        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ProjectStateError("project state must be a JSON object keyed by project ID")
            projects = {project_id: ProjectRecord.model_validate(item) for project_id, item in raw.items()}
        except json.JSONDecodeError as exc:
            raise ProjectStateError(f"Project state file is not valid JSON: {self._path}") from exc
        except (TypeError, ValidationError, ValueError) as exc:
            raise ProjectStateError(f"Project state file contains invalid project records: {self._path}") from exc

        for project_id, project in projects.items():
            if project.id != project_id:
                raise ProjectStateError(
                    f"Project state key does not match project id for {project_id}: {self._path}"
                )
        return projects

    def _write(self, projects: dict[str, ProjectRecord]) -> None:
        """Persist projects with an atomic replace to avoid partial writes."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            project_id: project.model_dump(mode="json")
            for project_id, project in projects.items()
        }
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)


class FileReportRepository:
    """Thread-safe JSON-backed repository for historical deployment reports."""

    def __init__(self, reports_file: str) -> None:
        self._path = Path(reports_file)
        self._lock = Lock()

    def create(self, report: DeploymentReportCreate) -> DeploymentReportRecord:
        """Create a new report with a generated UUID."""

        sanitized_report = sanitize_report(report)
        with self._lock:
            reports = self._read()
            record = DeploymentReportRecord(
                **sanitized_report.model_dump(),
                id=str(uuid4()),
                created_at=utc_now(),
            )
            if record.id in reports:
                raise ValueError(f"report id already exists: {record.id}")
            reports[record.id] = record
            self._write(reports)
        return record

    def list(
        self,
        *,
        project_id: str | None = None,
        deployment_id: str | None = None,
    ) -> list[DeploymentReportRecord]:
        """Return reports sorted newest first, optionally filtered."""

        with self._lock:
            reports = self._read()
        records = list(reports.values())
        if project_id is not None:
            records = [record for record in records if record.project_id == project_id]
        if deployment_id is not None:
            records = [record for record in records if record.deployment_id == deployment_id]
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get(self, report_id: str) -> DeploymentReportRecord | None:
        """Return one report by stable ID."""

        with self._lock:
            return self._read().get(report_id)

    def _read(self) -> dict[str, DeploymentReportRecord]:
        """Read and validate persisted report metadata."""

        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ReportStateError("report state must be a JSON object keyed by report ID")
            reports = {
                report_id: DeploymentReportRecord.model_validate(item)
                for report_id, item in raw.items()
            }
        except json.JSONDecodeError as exc:
            raise ReportStateError(f"Report state file is not valid JSON: {self._path}") from exc
        except (TypeError, ValidationError, ValueError) as exc:
            raise ReportStateError(f"Report state file contains invalid report records: {self._path}") from exc

        for report_id, report in reports.items():
            if report.id != report_id:
                raise ReportStateError(f"Report state key does not match report id for {report_id}: {self._path}")
        return reports

    def _write(self, reports: dict[str, DeploymentReportRecord]) -> None:
        """Persist reports with an atomic replace to avoid partial writes."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            report_id: report.model_dump(mode="json")
            for report_id, report in reports.items()
        }
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)


def slugify_project_name(name: str) -> str:
    """Convert a project name to a conservative URL-safe slug."""

    slug = []
    last_was_dash = False
    for character in name.strip().lower():
        if character.isalnum():
            slug.append(character)
            last_was_dash = False
        elif not last_was_dash:
            slug.append("-")
            last_was_dash = True
    normalized = "".join(slug).strip("-")
    if not normalized:
        raise ValueError("project name must contain at least one alphanumeric character")
    return normalized[:63].strip("-")


def build_default_local_project() -> ProjectRecord:
    """Build the default local project record used by all project repositories."""

    now = utc_now()
    return ProjectRecord(
        id=str(uuid4()),
        name="Default Local Project",
        slug=FileProjectRepository.DEFAULT_LOCAL_SLUG,
        mode=ProjectMode.local,
        source_type=ProjectSourceType.manual,
        repository_url=None,
        default_namespace="infrawatch",
        description="Default project for local ClueStack deployments.",
        created_at=now,
        updated_at=now,
    )


def utc_now() -> datetime:
    """Return timezone-aware UTC timestamps for consistent API output."""

    return datetime.now(UTC)


def connect_postgres(database_url: str):
    """Open PostgreSQL connections from URL settings with explicit libpq parameters."""

    parsed = urlparse(database_url)
    if parsed.scheme in {"postgresql", "postgres"}:
        return psycopg2.connect(
            dbname=parsed.path.lstrip("/") or None,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            host=parsed.hostname,
            port=parsed.port or 5432,
        )
    return psycopg2.connect(database_url)


def build_project_repository(database_url: str | None, projects_file: str) -> ProjectRepository:
    """Select PostgreSQL project storage when configured, otherwise JSON-file storage."""

    if database_url:
        return PostgresProjectRepository(database_url)
    return FileProjectRepository(projects_file)


def build_report_repository(database_url: str | None, reports_file: str) -> ReportRepository:
    """Select PostgreSQL report storage when configured, otherwise JSON-file storage."""

    if database_url:
        return PostgresReportRepository(database_url)
    return FileReportRepository(reports_file)


class PostgresProjectRepository:
    """PostgreSQL-backed repository for ClueStack project metadata."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._ensure_schema()

    def list(self) -> list[ProjectRecord]:
        """Return all known projects sorted by creation time."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_projects
                ORDER BY created_at ASC
                """
            )
            return [self._project_from_payload(row[0]) for row in cursor.fetchall()]

    def get(self, project_id: str) -> ProjectRecord | None:
        """Return one project by stable ID."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_projects
                WHERE id = %s
                """,
                (project_id,),
            )
            row = cursor.fetchone()
            return self._project_from_payload(row[0]) if row else None

    def create(self, project: ProjectCreate) -> ProjectRecord:
        """Create a new project while enforcing unique slugs in PostgreSQL."""

        slug = project.slug or slugify_project_name(project.name)
        now = utc_now()
        record = ProjectRecord(
            **project.model_dump(exclude={"slug"}),
            id=str(uuid4()),
            slug=slug,
            created_at=now,
            updated_at=now,
        )
        payload = record.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO infrawatch_projects (id, slug, payload, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (slug) DO NOTHING
                RETURNING payload
                """,
                (record.id, record.slug, Json(payload), record.created_at, record.updated_at),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError(f"project slug already exists: {slug}")
        return self._project_from_payload(row[0])

    def get_or_create_default_local_project(self) -> ProjectRecord:
        """Return the default local project with concurrency-safe slug conflict handling."""

        record = build_default_local_project()
        payload = record.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO infrawatch_projects (id, slug, payload, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (slug)
                DO UPDATE SET payload = infrawatch_projects.payload
                RETURNING payload
                """,
                (record.id, record.slug, Json(payload), record.created_at, record.updated_at),
            )
            row = cursor.fetchone()
        if row is None:
            raise ProjectStateError("Unable to create or retrieve the default local project")
        return self._project_from_payload(row[0])

    def _ensure_schema(self) -> None:
        """Create project persistence tables and indexes idempotently."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS infrawatch_projects (
                    id TEXT PRIMARY KEY,
                    slug TEXT UNIQUE NOT NULL,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_projects_created_at_idx
                ON infrawatch_projects (created_at ASC)
                """
            )

    def _project_from_payload(self, payload: dict) -> ProjectRecord:
        """Hydrate and validate a project payload read from PostgreSQL."""

        try:
            return ProjectRecord.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as exc:
            raise ProjectStateError("PostgreSQL project state contains invalid project records") from exc

    def _connect(self):
        """Open a short-lived database connection."""

        return connect_postgres(self._database_url)


class PostgresReportRepository:
    """PostgreSQL-backed repository for historical deployment reports."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._ensure_schema()

    def create(self, report: DeploymentReportCreate) -> DeploymentReportRecord:
        """Create a new report while enforcing unique report IDs in PostgreSQL."""

        sanitized_report = sanitize_report(report)
        record = DeploymentReportRecord(
            **sanitized_report.model_dump(),
            id=str(uuid4()),
            created_at=utc_now(),
        )
        payload = record.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO infrawatch_deployment_reports (
                    id, project_id, deployment_id, payload, created_at
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                RETURNING payload
                """,
                (
                    record.id,
                    record.project_id,
                    record.deployment_id,
                    Json(payload),
                    record.created_at,
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError(f"report id already exists: {record.id}")
        return self._report_from_payload(row[0])

    def list(
        self,
        *,
        project_id: str | None = None,
        deployment_id: str | None = None,
    ) -> list[DeploymentReportRecord]:
        """Return reports sorted newest first, optionally filtered."""

        if project_id is not None and deployment_id is not None:
            query = """
                SELECT payload
                FROM infrawatch_deployment_reports
                WHERE project_id = %s AND deployment_id = %s
                ORDER BY created_at DESC
                """
            params = (project_id, deployment_id)
        elif project_id is not None:
            query = """
                SELECT payload
                FROM infrawatch_deployment_reports
                WHERE project_id = %s
                ORDER BY created_at DESC
                """
            params = (project_id,)
        elif deployment_id is not None:
            query = """
                SELECT payload
                FROM infrawatch_deployment_reports
                WHERE deployment_id = %s
                ORDER BY created_at DESC
                """
            params = (deployment_id,)
        else:
            query = """
                SELECT payload
                FROM infrawatch_deployment_reports
                ORDER BY created_at DESC
                """
            params = ()

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, params)
            return [self._report_from_payload(row[0]) for row in cursor.fetchall()]

    def get(self, report_id: str) -> DeploymentReportRecord | None:
        """Return one report by stable ID."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_deployment_reports
                WHERE id = %s
                """,
                (report_id,),
            )
            row = cursor.fetchone()
            return self._report_from_payload(row[0]) if row else None

    def _ensure_schema(self) -> None:
        """Create report persistence tables and indexes idempotently."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS infrawatch_deployment_reports (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    deployment_id TEXT,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_deployment_reports_created_at_idx
                ON infrawatch_deployment_reports (created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_deployment_reports_project_id_created_at_idx
                ON infrawatch_deployment_reports (project_id, created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_deployment_reports_deployment_id_created_at_idx
                ON infrawatch_deployment_reports (deployment_id, created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_deployment_reports_project_deployment_created_at_idx
                ON infrawatch_deployment_reports (project_id, deployment_id, created_at DESC)
                """
            )

    def _report_from_payload(self, payload: dict) -> DeploymentReportRecord:
        """Hydrate and validate a report payload read from PostgreSQL."""

        try:
            return DeploymentReportRecord.model_validate(payload)
        except (TypeError, ValidationError, ValueError) as exc:
            raise ReportStateError("PostgreSQL report state contains invalid report records") from exc

    def _connect(self):
        """Open a short-lived database connection."""

        return connect_postgres(self._database_url)


class PostgresDeploymentRepository:
    """PostgreSQL-backed deployment repository for real local Kubernetes mode."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._ensure_schema()

    def list(self) -> list[DeploymentRecord]:
        """Return all known deployments sorted by most recent update."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_deployments
                ORDER BY updated_at DESC
                """
            )
            return [DeploymentRecord.model_validate(row[0]) for row in cursor.fetchall()]

    def list_by_project(self, project_id: str) -> list[DeploymentRecord]:
        """Return deployments explicitly associated with a project."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_deployments
                WHERE payload ->> 'project_id' = %s
                ORDER BY updated_at DESC
                """,
                (project_id,),
            )
            return [DeploymentRecord.model_validate(row[0]) for row in cursor.fetchall()]

    def get(self, name: str) -> DeploymentRecord | None:
        """Return one deployment record by name."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_deployments
                WHERE name = %s
                """,
                (name,),
            )
            row = cursor.fetchone()
            return DeploymentRecord.model_validate(row[0]) if row else None

    def upsert(self, record: DeploymentRecord) -> DeploymentRecord:
        """Insert or replace a deployment record."""

        payload = record.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO infrawatch_deployments (name, payload, created_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name)
                DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = EXCLUDED.updated_at
                """,
                (record.name, Json(payload), record.created_at, record.updated_at),
            )
        return record

    def delete(self, name: str) -> DeploymentRecord | None:
        """Delete one deployment record if present."""

        existing = self.get(name)
        if existing is None:
            return None
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM infrawatch_deployments WHERE name = %s", (name,))
        return existing

    def _ensure_schema(self) -> None:
        """Create the small persistence schema used by ClueStack."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS infrawatch_deployments (
                    name TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_deployments_updated_at_idx
                ON infrawatch_deployments (updated_at DESC)
                """
            )

    def _connect(self):
        """Open a short-lived database connection."""

        return connect_postgres(self._database_url)


class PostgresAuditLogRepository:
    """PostgreSQL-backed append-only audit log repository."""

    def __init__(self, database_url: str, max_entries: int = 1000) -> None:
        self._database_url = database_url
        self._max_entries = max_entries
        self._ensure_schema()

    def append(
        self,
        *,
        action: str,
        status: str,
        message: str,
        service: str | None = None,
        actor: str = "system",
        metadata: dict | None = None,
    ) -> AuditLogEntry:
        """Append one immutable event and keep the newest records."""

        entry = AuditLogEntry(
            id=str(uuid4()),
            action=action,
            service=service,
            actor=actor,
            status=status,
            message=message,
            metadata=metadata or {},
            created_at=utc_now(),
        )
        payload = entry.model_dump(mode="json")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO infrawatch_audit_logs (id, payload, created_at)
                VALUES (%s, %s, %s)
                """,
                (entry.id, Json(payload), entry.created_at),
            )
            cursor.execute(
                """
                DELETE FROM infrawatch_audit_logs
                WHERE id IN (
                    SELECT id
                    FROM infrawatch_audit_logs
                    ORDER BY created_at DESC
                    OFFSET %s
                )
                """,
                (self._max_entries,),
            )
        return entry

    def list(self, limit: int = 100) -> list[AuditLogEntry]:
        """Return newest audit entries first."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT payload
                FROM infrawatch_audit_logs
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [AuditLogEntry.model_validate(row[0]) for row in cursor.fetchall()]

    def _ensure_schema(self) -> None:
        """Create the small persistence schema used by audit logging."""

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS infrawatch_audit_logs (
                    id TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS infrawatch_audit_logs_created_at_idx
                ON infrawatch_audit_logs (created_at DESC)
                """
            )

    def _connect(self):
        """Open a short-lived database connection."""

        return connect_postgres(self._database_url)
