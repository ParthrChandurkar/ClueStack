"""Deployment report history service for ClueStack."""

from app.repository import ReportRepository
from app.schemas import DeploymentReportCreate, DeploymentReportRecord


class ReportService:
    """Coordinates read-only deployment report history access."""

    def __init__(self, repository: ReportRepository) -> None:
        self._repository = repository

    def list_reports(
        self,
        *,
        project_id: str | None = None,
        deployment_id: str | None = None,
    ) -> list[DeploymentReportRecord]:
        """List historical reports, optionally filtered by project or deployment."""

        return self._repository.list(project_id=project_id, deployment_id=deployment_id)

    def create(self, report: DeploymentReportCreate) -> DeploymentReportRecord:
        """Persist one sanitized historical report through the repository boundary."""

        return self._repository.create(report)

    def get(self, report_id: str) -> DeploymentReportRecord | None:
        """Return one report by ID."""

        return self._repository.get(report_id)
