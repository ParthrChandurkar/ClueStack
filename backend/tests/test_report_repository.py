"""Deployment report schema and JSON persistence tests."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.report_sanitizer import REDACTION_MARKER
from app.repository import (
    FileReportRepository,
    PostgresReportRepository,
    ReportStateError,
    build_report_repository,
)
from app.schemas import (
    DeploymentReportCreate,
    DeploymentReportOperation,
    DeploymentReportStatus,
)


def unwrap_json(value):
    """Return raw payloads from psycopg2 Json wrappers used by repository code."""

    return getattr(value, "adapted", value)


class FakeReportPostgresCursor:
    """Small SQL-aware fake cursor for report repository tests."""

    def __init__(self, database: "FakeReportPostgresConnection") -> None:
        self.database = database
        self._one: tuple[dict] | None = None
        self._many: list[tuple[dict]] = []

    def __enter__(self) -> "FakeReportPostgresCursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, query: str, params: tuple | None = None) -> None:
        normalized = " ".join(query.lower().split())
        self.database.statements.append(query)
        params = params or ()
        self._one = None
        self._many = []

        if normalized.startswith("create table") or normalized.startswith("create index"):
            return
        if "insert into infrawatch_deployment_reports" in normalized:
            report_id, project_id, deployment_id, payload, created_at = params
            if report_id in self.database.reports:
                self._one = None
                return
            self.database.reports[report_id] = {
                "project_id": project_id,
                "deployment_id": deployment_id,
                "payload": unwrap_json(payload),
                "created_at": created_at,
            }
            self._one = (unwrap_json(payload),)
            return
        if normalized == "select payload from infrawatch_deployment_reports where id = %s":
            report = self.database.reports.get(params[0])
            self._one = (report["payload"],) if report else None
            return
        if normalized.startswith("select payload from infrawatch_deployment_reports"):
            reports = list(self.database.reports.values())
            if "where project_id = %s and deployment_id = %s" in normalized:
                project_id, deployment_id = params
                reports = [
                    report
                    for report in reports
                    if report["project_id"] == project_id and report["deployment_id"] == deployment_id
                ]
            elif "where project_id = %s" in normalized:
                reports = [report for report in reports if report["project_id"] == params[0]]
            elif "where deployment_id = %s" in normalized:
                reports = [report for report in reports if report["deployment_id"] == params[0]]
            reports = sorted(reports, key=lambda item: item["created_at"], reverse=True)
            self._many = [(report["payload"],) for report in reports]
            return
        raise AssertionError(f"Unexpected SQL: {query}")

    def fetchone(self):
        """Return the last single-row result."""

        return self._one

    def fetchall(self):
        """Return the last multi-row result."""

        return self._many


class FakeReportPostgresConnection:
    """Small context-manager connection used instead of a real PostgreSQL server."""

    def __init__(self) -> None:
        self.reports: dict[str, dict] = {}
        self.statements: list[str] = []

    def __enter__(self) -> "FakeReportPostgresConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> FakeReportPostgresCursor:
        """Return a fake cursor bound to this in-memory store."""

        return FakeReportPostgresCursor(self)


def sample_report(**overrides) -> DeploymentReportCreate:
    """Build a valid report payload for repository tests."""

    started_at = overrides.pop("started_at", datetime(2026, 10, 10, 10, 0, tzinfo=UTC))
    payload = {
        "operation": DeploymentReportOperation.deploy,
        "status": DeploymentReportStatus.success,
        "summary": "Deployment completed.",
        "target": "local_kubernetes",
        "started_at": started_at,
        "completed_at": started_at + timedelta(minutes=1),
        "duration_ms": 60_000,
        "evidence": {
            "namespace": "infrawatch",
            "metrics_source": "prometheus",
        },
        "errors": [],
    }
    payload.update(overrides)
    return DeploymentReportCreate(**payload)


def test_report_schema_validates_uuid_links() -> None:
    """Optional project and deployment IDs must be stable UUID values."""

    with pytest.raises(ValidationError):
        sample_report(project_id="not-a-uuid")

    with pytest.raises(ValidationError):
        sample_report(deployment_id="not-a-uuid")


def test_report_schema_allows_sensitive_evidence_for_repository_sanitization() -> None:
    """Repository boundaries sanitize evidence before persistence."""

    report = sample_report(evidence={"authorization_header": "Bearer synthetic-token"})

    assert report.evidence == {"authorization_header": "Bearer synthetic-token"}


def test_report_schema_rejects_unbounded_errors_and_bad_timestamps() -> None:
    """Reports should keep error details bounded and timestamps coherent."""

    with pytest.raises(ValidationError):
        sample_report(errors=[{"message": f"error {index}"} for index in range(21)])

    with pytest.raises(ValidationError, match="completed_at cannot be earlier"):
        sample_report(
            started_at=datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
            completed_at=datetime(2026, 10, 10, 9, 59, tzinfo=UTC),
        )


def test_file_report_repository_creates_lists_and_gets_reports(tmp_path) -> None:
    """Reports should be persisted with generated UUID identifiers."""

    repository = FileReportRepository(str(tmp_path / "reports.json"))
    created = repository.create(sample_report())

    UUID(created.id)
    assert created.operation is DeploymentReportOperation.deploy
    assert created.status is DeploymentReportStatus.success
    assert repository.get(created.id) == created
    assert repository.list() == [created]

    reloaded = FileReportRepository(str(tmp_path / "reports.json"))
    assert reloaded.get(created.id) == created


def test_file_report_repository_sanitizes_before_persisting(tmp_path) -> None:
    """File reports should sanitize evidence and error messages at create time."""

    repository = FileReportRepository(str(tmp_path / "reports.json"))
    created = repository.create(
        sample_report(
            evidence={
                "headers": {"Authorization": "Bearer synthetic-token"},
                "message": "password=synthetic-password",
            },
            errors=[{"message": "failed with token=synthetic-token"}],
        )
    )

    assert created.evidence["headers"]["Authorization"] == REDACTION_MARKER
    assert "synthetic-password" not in created.evidence["message"]
    assert "synthetic-token" not in created.errors[0].message
    persisted = repository.get(created.id)
    assert persisted == created


def test_file_report_repository_filters_by_project_and_deployment(tmp_path) -> None:
    """Report listing should support project and deployment filters."""

    repository = FileReportRepository(str(tmp_path / "reports.json"))
    project_id = "00000000-0000-0000-0000-000000000001"
    deployment_id = "00000000-0000-0000-0000-000000000002"
    matching = repository.create(sample_report(project_id=project_id, deployment_id=deployment_id))
    repository.create(
        sample_report(
            project_id="00000000-0000-0000-0000-000000000003",
            deployment_id=deployment_id,
            started_at=datetime(2026, 10, 10, 10, 2, tzinfo=UTC),
        )
    )
    repository.create(
        sample_report(
            project_id=project_id,
            deployment_id="00000000-0000-0000-0000-000000000004",
            started_at=datetime(2026, 10, 10, 10, 3, tzinfo=UTC),
        )
    )

    assert repository.list(project_id=project_id, deployment_id=deployment_id) == [matching]


def test_file_report_repository_sorts_newest_first(tmp_path) -> None:
    """Reports should list newest created records first."""

    repository = FileReportRepository(str(tmp_path / "reports.json"))
    older = repository.create(sample_report(started_at=datetime(2026, 10, 10, 10, 0, tzinfo=UTC)))
    newer = repository.create(sample_report(started_at=datetime(2026, 10, 10, 10, 5, tzinfo=UTC)))

    assert repository.list() == [newer, older]


def test_file_report_repository_state_files_are_isolated_by_path(tmp_path) -> None:
    """Independent report paths should not share state."""

    first_repository = FileReportRepository(str(tmp_path / "first" / "reports.json"))
    second_repository = FileReportRepository(str(tmp_path / "second" / "reports.json"))

    first_repository.create(sample_report(summary="First report."))
    second_repository.create(sample_report(summary="Second report."))

    assert [report.summary for report in first_repository.list()] == ["First report."]
    assert [report.summary for report in second_repository.list()] == ["Second report."]


def test_malformed_report_state_raises_actionable_error(tmp_path) -> None:
    """Malformed report state should not be silently overwritten."""

    state_file = tmp_path / "reports.json"
    state_file.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ReportStateError, match="not valid JSON"):
        FileReportRepository(str(state_file)).list()


def test_report_state_id_mismatch_raises_actionable_error(tmp_path) -> None:
    """A state key that disagrees with report ID should be reported."""

    state_file = tmp_path / "reports.json"
    report = FileReportRepository(str(state_file)).create(sample_report())
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    state_file.write_text(json.dumps({"wrong-id": payload[report.id]}), encoding="utf-8")

    with pytest.raises(ReportStateError, match="key does not match report id"):
        FileReportRepository(str(state_file)).list()


def test_duplicate_report_ids_do_not_overwrite_existing_reports(monkeypatch, tmp_path) -> None:
    """A generated duplicate report ID should fail instead of overwriting state."""

    repository = FileReportRepository(str(tmp_path / "reports.json"))
    duplicate_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr("app.repository.uuid4", lambda: duplicate_id)
    first = repository.create(sample_report(summary="Original report."))

    with pytest.raises(ValueError, match="report id already exists"):
        repository.create(sample_report(summary="Conflicting report."))

    assert repository.get(first.id).summary == "Original report."


def test_report_state_is_written_atomically_without_tmp_file_left(tmp_path) -> None:
    """Successful writes should replace the target file and leave no tmp file behind."""

    state_file = tmp_path / "reports.json"
    repository = FileReportRepository(str(state_file))

    repository.create(sample_report(completed_at=datetime.now(UTC) + timedelta(seconds=1)))

    assert state_file.exists()
    assert not state_file.with_suffix(".tmp").exists()


def test_build_report_repository_selects_file_or_postgres(monkeypatch, tmp_path) -> None:
    """Report storage should follow the database-url selection convention."""

    fake_database = FakeReportPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)

    file_repository = build_report_repository(None, str(tmp_path / "reports.json"))
    postgres_repository = build_report_repository("postgresql://example", str(tmp_path / "ignored.json"))

    assert isinstance(file_repository, FileReportRepository)
    assert isinstance(postgres_repository, PostgresReportRepository)


def test_postgres_report_repository_initializes_schema(monkeypatch) -> None:
    """PostgreSQL report schema creation should be idempotent and non-destructive."""

    fake_database = FakeReportPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)

    PostgresReportRepository("postgresql://example")

    schema_sql = "\n".join(fake_database.statements)
    assert "CREATE TABLE IF NOT EXISTS infrawatch_deployment_reports" in schema_sql
    assert "id TEXT PRIMARY KEY" in schema_sql
    assert "project_id TEXT" in schema_sql
    assert "deployment_id TEXT" in schema_sql
    assert "payload JSONB NOT NULL" in schema_sql
    assert "CREATE INDEX IF NOT EXISTS infrawatch_deployment_reports_created_at_idx" in schema_sql
    assert "CREATE INDEX IF NOT EXISTS infrawatch_deployment_reports_project_id_created_at_idx" in schema_sql
    assert "CREATE INDEX IF NOT EXISTS infrawatch_deployment_reports_deployment_id_created_at_idx" in schema_sql
    assert "CREATE INDEX IF NOT EXISTS infrawatch_deployment_reports_project_deployment_created_at_idx" in schema_sql


def test_postgres_report_repository_creates_lists_and_gets_reports(monkeypatch) -> None:
    """The PostgreSQL report repository should match the file repository contract."""

    fake_database = FakeReportPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresReportRepository("postgresql://example")

    created = repository.create(sample_report())

    UUID(created.id)
    assert repository.get(created.id) == created
    assert repository.list() == [created]


def test_postgres_report_repository_sanitizes_before_persisting(monkeypatch) -> None:
    """PostgreSQL reports should sanitize evidence and error messages at create time."""

    fake_database = FakeReportPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresReportRepository("postgresql://example")

    created = repository.create(
        sample_report(
            evidence={
                "headers": {"Authorization": "Bearer synthetic-token"},
                "message": "password=synthetic-password",
            },
            errors=[{"message": "failed with token=synthetic-token"}],
        )
    )

    assert created.evidence["headers"]["Authorization"] == REDACTION_MARKER
    assert "synthetic-password" not in created.evidence["message"]
    assert "synthetic-token" not in created.errors[0].message
    persisted_payload = fake_database.reports[created.id]["payload"]
    assert persisted_payload["evidence"] == created.model_dump(mode="json")["evidence"]


def test_postgres_report_repository_filters_by_project_and_deployment(monkeypatch) -> None:
    """PostgreSQL report listing should support combined filters."""

    fake_database = FakeReportPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresReportRepository("postgresql://example")
    project_id = "00000000-0000-0000-0000-000000000001"
    deployment_id = "00000000-0000-0000-0000-000000000002"
    matching = repository.create(sample_report(project_id=project_id, deployment_id=deployment_id))
    repository.create(
        sample_report(
            project_id="00000000-0000-0000-0000-000000000003",
            deployment_id=deployment_id,
            started_at=datetime(2026, 10, 10, 10, 2, tzinfo=UTC),
        )
    )
    repository.create(
        sample_report(
            project_id=project_id,
            deployment_id="00000000-0000-0000-0000-000000000004",
            started_at=datetime(2026, 10, 10, 10, 3, tzinfo=UTC),
        )
    )

    assert repository.list(project_id=project_id, deployment_id=deployment_id) == [matching]
    assert len(repository.list(project_id=project_id)) == 2
    assert len(repository.list(deployment_id=deployment_id)) == 2


def test_postgres_report_repository_rejects_duplicate_ids(monkeypatch) -> None:
    """Duplicate report IDs should fail without overwriting the existing row."""

    fake_database = FakeReportPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    duplicate_id = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setattr("app.repository.uuid4", lambda: duplicate_id)
    repository = PostgresReportRepository("postgresql://example")
    first = repository.create(sample_report(summary="Original report."))

    with pytest.raises(ValueError, match="report id already exists"):
        repository.create(sample_report(summary="Conflicting report."))

    assert repository.get(first.id).summary == "Original report."


def test_postgres_invalid_report_payload_raises_actionable_error(monkeypatch) -> None:
    """Invalid PostgreSQL JSON payloads should be reported instead of ignored."""

    fake_database = FakeReportPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresReportRepository("postgresql://example")
    created = repository.create(sample_report())
    fake_database.reports[created.id]["payload"] = {"id": "not-a-uuid"}

    with pytest.raises(ReportStateError, match="invalid report records"):
        repository.get(created.id)
