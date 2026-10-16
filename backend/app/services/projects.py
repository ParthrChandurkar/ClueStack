"""Project management service for ClueStack."""

from app.repository import ProjectRepository
from app.schemas import ProjectCreate, ProjectMode, ProjectRecord


class ProjectConflictError(RuntimeError):
    """Raised when a project conflicts with an existing project."""


class ProjectNotFoundError(RuntimeError):
    """Raised when a requested project does not exist."""


class ProjectModeError(RuntimeError):
    """Raised when a project cannot be used for the requested operation."""


class ProjectService:
    """Coordinates project validation and persistence."""

    def __init__(self, repository: ProjectRepository) -> None:
        self._repository = repository

    def create(self, project: ProjectCreate) -> ProjectRecord:
        """Create a new project."""

        try:
            return self._repository.create(project)
        except ValueError as exc:
            raise ProjectConflictError(str(exc)) from exc

    def list_projects(self) -> list[ProjectRecord]:
        """List all known projects."""

        return self._repository.list()

    def get(self, project_id: str) -> ProjectRecord | None:
        """Return one project by ID."""

        return self._repository.get(project_id)

    def get_or_create_default_local_project(self) -> ProjectRecord:
        """Return the default local project, creating it once if needed."""

        return self._repository.get_or_create_default_local_project()

    def resolve_local_deployment_project(self, project_id: str | None) -> ProjectRecord:
        """Resolve the project a local deployment should belong to."""

        project = self.get(project_id) if project_id else self.get_or_create_default_local_project()
        if project is None:
            raise ProjectNotFoundError("Project not found")
        if project.mode is not ProjectMode.local:
            raise ProjectModeError("Local deployments require a local project")
        return project
