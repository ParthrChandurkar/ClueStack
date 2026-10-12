"""Project model and JSON persistence tests."""

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.repository import (
    FileDeploymentRepository,
    FileProjectRepository,
    PostgresDeploymentRepository,
    PostgresProjectRepository,
    ProjectStateError,
    build_project_repository,
)
from app.schemas import (
    DeploymentRecord,
    DeploymentStatus,
    ProjectCreate,
    ProjectMode,
    ProjectSourceType,
)


def unwrap_json(value):
    """Return raw payloads from psycopg2 Json wrappers used by repository code."""

    return getattr(value, "adapted", value)


class FakePostgresCursor:
    """Small SQL-aware fake cursor for project repository tests."""

    def __init__(self, database: "FakePostgresConnection") -> None:
        self.database = database
        self._one: tuple[dict] | None = None
        self._many: list[tuple[dict]] = []

    def __enter__(self) -> "FakePostgresCursor":
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
        if normalized == "select payload from infrawatch_projects order by created_at asc":
            projects = sorted(self.database.projects.values(), key=lambda item: item["created_at"])
            self._many = [(project["payload"],) for project in projects]
            return
        if normalized == "select payload from infrawatch_projects where id = %s":
            project = self.database.projects.get(params[0])
            self._one = (project["payload"],) if project else None
            return
        if "from infrawatch_deployments" in normalized and "payload ->> 'project_id' = %s" in normalized:
            self._many = []
            return
        if "insert into infrawatch_projects" in normalized and "on conflict (slug) do nothing" in normalized:
            project_id, slug, payload, created_at, updated_at = params
            if slug in self.database.slug_index:
                self._one = None
                return
            self.database.projects[project_id] = {
                "slug": slug,
                "payload": unwrap_json(payload),
                "created_at": created_at,
                "updated_at": updated_at,
            }
            self.database.slug_index[slug] = project_id
            self._one = (unwrap_json(payload),)
            return
        if "insert into infrawatch_projects" in normalized and "on conflict (slug)" in normalized:
            project_id, slug, payload, created_at, updated_at = params
            existing_id = self.database.slug_index.get(slug)
            if existing_id:
                self._one = (self.database.projects[existing_id]["payload"],)
                return
            self.database.projects[project_id] = {
                "slug": slug,
                "payload": unwrap_json(payload),
                "created_at": created_at,
                "updated_at": updated_at,
            }
            self.database.slug_index[slug] = project_id
            self._one = (unwrap_json(payload),)
            return
        raise AssertionError(f"Unexpected SQL: {query}")

    def fetchone(self):
        """Return the last single-row result."""

        return self._one

    def fetchall(self):
        """Return the last multi-row result."""

        return self._many


class FakePostgresConnection:
    """Small context-manager connection used instead of a real PostgreSQL server."""

    def __init__(self) -> None:
        self.projects: dict[str, dict] = {}
        self.slug_index: dict[str, str] = {}
        self.statements: list[str] = []

    def __enter__(self) -> "FakePostgresConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> FakePostgresCursor:
        """Return a fake cursor bound to this in-memory store."""

        return FakePostgresCursor(self)


class FakeDeploymentPostgresCursor:
    """Small SQL-aware fake cursor for deployment repository tests."""

    def __init__(self, database: "FakeDeploymentPostgresConnection") -> None:
        self.database = database
        self._one: tuple[dict] | None = None
        self._many: list[tuple[dict]] = []

    def __enter__(self) -> "FakeDeploymentPostgresCursor":
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
        if normalized == "select payload from infrawatch_deployments order by updated_at desc":
            deployments = sorted(self.database.deployments.values(), key=lambda item: item["updated_at"], reverse=True)
            self._many = [(deployment["payload"],) for deployment in deployments]
            return
        if "from infrawatch_deployments" in normalized and "payload ->> 'project_id' = %s" in normalized:
            deployments = [
                deployment
                for deployment in self.database.deployments.values()
                if deployment["payload"].get("project_id") == params[0]
            ]
            deployments = sorted(deployments, key=lambda item: item["updated_at"], reverse=True)
            self._many = [(deployment["payload"],) for deployment in deployments]
            return
        if normalized == "select payload from infrawatch_deployments where name = %s":
            deployment = self.database.deployments.get(params[0])
            self._one = (deployment["payload"],) if deployment else None
            return
        if "insert into infrawatch_deployments" in normalized:
            name, payload, created_at, updated_at = params
            self.database.deployments[name] = {
                "payload": unwrap_json(payload),
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return
        if normalized == "delete from infrawatch_deployments where name = %s":
            self.database.deployments.pop(params[0], None)
            return
        raise AssertionError(f"Unexpected SQL: {query}")

    def fetchone(self):
        """Return the last single-row result."""

        return self._one

    def fetchall(self):
        """Return the last multi-row result."""

        return self._many


class FakeDeploymentPostgresConnection:
    """Small context-manager connection used for deployment repository tests."""

    def __init__(self) -> None:
        self.deployments: dict[str, dict] = {}
        self.statements: list[str] = []

    def __enter__(self) -> "FakeDeploymentPostgresConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> FakeDeploymentPostgresCursor:
        """Return a fake cursor bound to this in-memory store."""

        return FakeDeploymentPostgresCursor(self)


def sample_deployment(**overrides) -> DeploymentRecord:
    """Build a valid deployment record for repository tests."""

    now = overrides.pop("now", datetime(2026, 10, 10, 10, 0, tzinfo=UTC))
    payload = {
        "id": "00000000-0000-0000-0000-000000000010",
        "name": "catalog-api",
        "project_id": "00000000-0000-0000-0000-000000000001",
        "image": "docker.io/example/catalog-api:latest",
        "namespace": "infrawatch",
        "replicas": 1,
        "port": 8080,
        "status": DeploymentStatus.running,
        "ready_replicas": 1,
        "available_replicas": 1,
        "message": "Deployment running.",
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    return DeploymentRecord(**payload)


def test_project_create_validates_local_defaults() -> None:
    """Local projects should default to manual source metadata."""

    project = ProjectCreate(name="Catalog Local")

    assert project.name == "Catalog Local"
    assert project.mode is ProjectMode.local
    assert project.source_type is ProjectSourceType.manual
    assert project.repository_url is None
    assert project.default_namespace == "infrawatch"


def test_project_create_validates_github_repository_url() -> None:
    """GitHub repository projects require public HTTPS GitHub repository URLs."""

    project = ProjectCreate(
        name="Catalog Repository",
        mode=ProjectMode.github_repository,
        source_type=ProjectSourceType.github_public_readonly,
        repository_url="https://github.com/example/catalog-api.git",
    )

    assert project.repository_url == "https://github.com/example/catalog-api"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Catalog", "slug": "Bad Slug"},
        {"name": "Catalog", "repository_url": "https://github.com/example/catalog-api"},
        {
            "name": "Catalog",
            "mode": "github_repository",
            "source_type": "manual",
            "repository_url": "https://github.com/example/catalog-api",
        },
        {
            "name": "Catalog",
            "mode": "github_repository",
            "source_type": "github_public_readonly",
            "repository_url": "https://gitlab.com/example/catalog-api",
        },
    ],
)
def test_project_create_rejects_invalid_combinations(payload: dict) -> None:
    """Project input should reject unsafe slugs and inconsistent source settings."""

    with pytest.raises(ValidationError):
        ProjectCreate(**payload)


def test_file_project_repository_creates_lists_and_gets_projects(tmp_path) -> None:
    """Projects should be persisted with stable UUID identifiers."""

    repository = FileProjectRepository(str(tmp_path / "projects.json"))
    created = repository.create(ProjectCreate(name="Catalog Local", slug="catalog-local"))

    UUID(created.id)
    assert created.slug == "catalog-local"
    assert repository.get(created.id) == created
    assert repository.list() == [created]

    reloaded = FileProjectRepository(str(tmp_path / "projects.json"))
    assert reloaded.get(created.id) == created


def test_file_project_repository_generates_slug_from_name(tmp_path) -> None:
    """A missing slug should be generated from the project name."""

    repository = FileProjectRepository(str(tmp_path / "projects.json"))
    created = repository.create(ProjectCreate(name="Catalog API Local!"))

    assert created.slug == "catalog-api-local"


def test_file_project_repository_rejects_duplicate_slugs(tmp_path) -> None:
    """Project slugs should remain unique within one state file."""

    repository = FileProjectRepository(str(tmp_path / "projects.json"))
    repository.create(ProjectCreate(name="Catalog Local", slug="catalog-local"))

    with pytest.raises(ValueError, match="project slug already exists"):
        repository.create(ProjectCreate(name="Another Catalog", slug="catalog-local"))


def test_default_local_project_is_idempotent(tmp_path) -> None:
    """The default local project should be created once and then reused."""

    repository = FileProjectRepository(str(tmp_path / "projects.json"))

    first = repository.get_or_create_default_local_project()
    second = repository.get_or_create_default_local_project()

    assert first == second
    assert first.slug == "default-local"
    assert first.mode is ProjectMode.local
    assert first.source_type is ProjectSourceType.manual
    assert repository.list() == [first]


def test_project_state_files_are_isolated_by_path(tmp_path) -> None:
    """Tests and local runs can use independent project state files."""

    first_repository = FileProjectRepository(str(tmp_path / "first" / "projects.json"))
    second_repository = FileProjectRepository(str(tmp_path / "second" / "projects.json"))

    first_repository.create(ProjectCreate(name="First Project", slug="first-project"))
    second_repository.create(ProjectCreate(name="Second Project", slug="second-project"))

    assert [project.slug for project in first_repository.list()] == ["first-project"]
    assert [project.slug for project in second_repository.list()] == ["second-project"]


def test_malformed_project_state_raises_actionable_error(tmp_path) -> None:
    """Malformed project state should not be silently overwritten."""

    state_file = tmp_path / "projects.json"
    state_file.write_text("{not-json", encoding="utf-8")
    repository = FileProjectRepository(str(state_file))

    with pytest.raises(ProjectStateError, match="not valid JSON"):
        repository.list()


def test_project_state_id_mismatch_raises_actionable_error(tmp_path) -> None:
    """A state key that disagrees with the project ID should be reported."""

    state_file = tmp_path / "projects.json"
    project = FileProjectRepository(str(state_file)).create(ProjectCreate(name="Catalog Local"))
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    state_file.write_text(json.dumps({"wrong-id": payload[project.id]}), encoding="utf-8")

    with pytest.raises(ProjectStateError, match="key does not match project id"):
        FileProjectRepository(str(state_file)).list()


def test_legacy_deployment_records_without_project_id_still_load(tmp_path) -> None:
    """Existing deployment JSON without project_id should remain valid and unassigned."""

    state_file = tmp_path / "deployments.json"
    state_file.write_text(
        json.dumps(
            {
                "legacy-api": {
                    "name": "legacy-api",
                    "image": "docker.io/example/legacy-api:latest",
                    "namespace": "infrawatch",
                    "replicas": 1,
                    "port": 8080,
                    "status": "Running",
                    "ready_replicas": 1,
                    "available_replicas": 1,
                    "message": "Legacy deployment record.",
                    "created_at": "2026-10-10T10:00:00Z",
                    "updated_at": "2026-10-10T10:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )

    repository = FileDeploymentRepository(str(state_file))

    deployments = repository.list()
    assert deployments[0].name == "legacy-api"
    assert deployments[0].id is None
    assert deployments[0].project_id is None
    assert repository.list_by_project("00000000-0000-0000-0000-000000000000") == []


def test_file_deployment_repository_round_trips_stable_ids(tmp_path) -> None:
    """Deployment UUIDs should persist alongside name-based lookup semantics."""

    repository = FileDeploymentRepository(str(tmp_path / "deployments.json"))
    deployment = sample_deployment()

    stored = repository.upsert(deployment)

    UUID(stored.id)
    assert repository.get("catalog-api") == deployment
    assert repository.list_by_project(deployment.project_id) == [deployment]
    assert FileDeploymentRepository(str(tmp_path / "deployments.json")).get("catalog-api") == deployment


def test_file_deployment_repository_rejects_invalid_deployment_ids(tmp_path) -> None:
    """Malformed persisted deployment IDs should not be treated as stable references."""

    state_file = tmp_path / "deployments.json"
    state_file.write_text(
        json.dumps(
            {
                "catalog-api": {
                    **sample_deployment().model_dump(mode="json"),
                    "id": "catalog-api",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        FileDeploymentRepository(str(state_file)).list()


def test_postgres_deployment_repository_round_trips_stable_ids(monkeypatch) -> None:
    """PostgreSQL deployment payloads should preserve stable UUIDs without schema changes."""

    fake_database = FakeDeploymentPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresDeploymentRepository("postgresql://example")
    deployment = sample_deployment()

    stored = repository.upsert(deployment)

    UUID(stored.id)
    assert repository.get("catalog-api") == deployment
    assert repository.list() == [deployment]
    assert repository.list_by_project(deployment.project_id) == [deployment]


def test_postgres_deployment_repository_keeps_legacy_records_without_ids(monkeypatch) -> None:
    """Legacy PostgreSQL payloads without IDs should remain readable and unmodified on read."""

    fake_database = FakeDeploymentPostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresDeploymentRepository("postgresql://example")
    legacy_payload = sample_deployment(id=None).model_dump(mode="json")
    fake_database.deployments["legacy-api"] = {
        "payload": {
            **legacy_payload,
            "name": "legacy-api",
        },
        "created_at": legacy_payload["created_at"],
        "updated_at": legacy_payload["updated_at"],
    }

    legacy = repository.get("legacy-api")

    assert legacy is not None
    assert legacy.id is None
    assert fake_database.deployments["legacy-api"]["payload"]["id"] is None


def test_build_project_repository_selects_file_or_postgres(monkeypatch, tmp_path) -> None:
    """Project storage should follow the existing database-url selection convention."""

    fake_database = FakePostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)

    file_repository = build_project_repository(None, str(tmp_path / "projects.json"))
    postgres_repository = build_project_repository("postgresql://example", str(tmp_path / "ignored.json"))

    assert isinstance(file_repository, FileProjectRepository)
    assert isinstance(postgres_repository, PostgresProjectRepository)


def test_postgres_project_repository_initializes_schema(monkeypatch) -> None:
    """PostgreSQL project schema creation should be idempotent and non-destructive."""

    fake_database = FakePostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)

    PostgresProjectRepository("postgresql://example")

    schema_sql = "\n".join(fake_database.statements)
    assert "CREATE TABLE IF NOT EXISTS infrawatch_projects" in schema_sql
    assert "id TEXT PRIMARY KEY" in schema_sql
    assert "slug TEXT UNIQUE NOT NULL" in schema_sql
    assert "payload JSONB NOT NULL" in schema_sql
    assert "CREATE INDEX IF NOT EXISTS infrawatch_projects_created_at_idx" in schema_sql


def test_postgres_project_repository_creates_lists_and_gets_projects(monkeypatch) -> None:
    """The PostgreSQL repository should match the file repository contract."""

    fake_database = FakePostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresProjectRepository("postgresql://example")

    created = repository.create(ProjectCreate(name="Catalog Local", slug="catalog-local"))

    UUID(created.id)
    assert created.slug == "catalog-local"
    assert repository.get(created.id) == created
    assert repository.list() == [created]


def test_postgres_project_repository_generates_slug_from_name(monkeypatch) -> None:
    """A missing slug should be generated consistently for PostgreSQL-backed projects."""

    fake_database = FakePostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresProjectRepository("postgresql://example")

    created = repository.create(ProjectCreate(name="Catalog API Local!"))

    assert created.slug == "catalog-api-local"


def test_postgres_project_repository_rejects_duplicate_slugs(monkeypatch) -> None:
    """Duplicate slugs should be rejected without overwriting existing records."""

    fake_database = FakePostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresProjectRepository("postgresql://example")
    original = repository.create(ProjectCreate(name="Catalog Local", slug="catalog-local"))

    with pytest.raises(ValueError, match="project slug already exists"):
        repository.create(ProjectCreate(name="Another Catalog", slug="catalog-local"))

    assert repository.get(original.id) == original
    assert repository.list() == [original]


def test_postgres_default_local_project_is_idempotent_and_concurrency_safe(monkeypatch) -> None:
    """The default local project should use slug conflict handling to avoid duplicates."""

    fake_database = FakePostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresProjectRepository("postgresql://example")

    first = repository.get_or_create_default_local_project()
    second = repository.get_or_create_default_local_project()

    assert first == second
    assert first.slug == "default-local"
    assert len(repository.list()) == 1
    default_insert_sql = "\n".join(fake_database.statements)
    assert "ON CONFLICT (slug)" in default_insert_sql
    assert "DO UPDATE SET payload = infrawatch_projects.payload" in default_insert_sql


def test_postgres_invalid_project_payload_raises_actionable_error(monkeypatch) -> None:
    """Invalid PostgreSQL JSON payloads should be reported instead of ignored."""

    fake_database = FakePostgresConnection()
    monkeypatch.setattr("app.repository.connect_postgres", lambda _url: fake_database)
    repository = PostgresProjectRepository("postgresql://example")
    created = repository.create(ProjectCreate(name="Catalog Local"))
    fake_database.projects[created.id]["payload"] = {"id": "not-a-uuid"}

    with pytest.raises(ProjectStateError, match="invalid project records"):
        repository.get(created.id)
