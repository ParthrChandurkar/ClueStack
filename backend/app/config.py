"""Runtime configuration for the ClueStack API.

All operational settings are read from environment variables so secrets and
cluster-specific values never need to be hardcoded in source control.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    app_name: str = "ClueStack API"
    environment: str = "development"
    log_level: str = "INFO"
    service_name: str = "infrawatch-backend"

    cors_origins: Annotated[
        list[str],
        Field(
            default_factory=lambda: [
                "http://localhost:3000",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ]
        ),
    ]

    state_file: str = "/tmp/infrawatch/deployments.json"
    audit_file: str = "/tmp/infrawatch/audit-log.json"
    projects_file: str = "/tmp/infrawatch/projects.json"
    reports_file: str = "/tmp/infrawatch/reports.json"
    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("INFRAWATCH_DATABASE_URL", "DATABASE_URL"),
    )
    execute_kubectl: bool = False
    seed_demo_data: bool = False
    kubectl_binary: str = "kubectl"
    kubectl_namespace: str = "infrawatch"
    rollout_timeout_seconds: int = Field(default=180, ge=30, le=900)

    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    alertmanager_url: str = "http://alertmanager:9093"
    observability_timeout_seconds: float = 10.0
    allow_mock_observability: bool = True
    redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("INFRAWATCH_REDIS_URL", "REDIS_URL"),
    )
    redis_cache_ttl: int = Field(
        default=5,
        ge=1,
        le=60,
        validation_alias=AliasChoices("INFRAWATCH_REDIS_CACHE_TTL", "REDIS_CACHE_TTL"),
    )

    github_api_timeout_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    github_analysis_timeout_seconds: float = Field(default=20.0, ge=2.0, le=60.0)
    github_max_tree_entries: int = Field(default=2_000, ge=100, le=10_000)
    github_max_files: int = Field(default=40, ge=1, le=100)
    github_max_file_bytes: int = Field(default=256_000, ge=1_024, le=1_000_000)
    github_max_total_bytes: int = Field(default=1_000_000, ge=10_000, le=5_000_000)
    github_max_api_response_bytes: int = Field(default=5_000_000, ge=10_000, le=20_000_000)
    local_analysis_max_request_bytes: int = Field(default=1_500_000, ge=10_000, le=6_000_000)
    local_analysis_timeout_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    container_build_execution_enabled: bool = False
    container_build_timeout_seconds: int = Field(default=600, ge=30, le=3_600)
    container_build_memory_megabytes: int = Field(default=512, ge=128, le=8_192)
    container_build_cpu_limit: float = Field(default=0.5, ge=0.1, le=8.0)
    container_build_storage_megabytes: int = Field(default=1_024, ge=256, le=20_480)
    container_build_process_limit: int = Field(default=64, ge=16, le=1_024)
    container_build_max_log_bytes: int = Field(default=2_000, ge=500, le=2_000)
    container_build_max_concurrent_jobs: int = Field(default=1, ge=1, le=4)
    container_build_max_queued_jobs: int = Field(default=4, ge=1, le=20)
    container_build_worker_runtime: str = Field(default="disabled", pattern=r"^disabled$")
    container_build_network_policy: str = Field(default="disabled", pattern=r"^disabled$")
    repository_analysis_max_age_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    source_snapshots_file: str = "/tmp/infrawatch/source-snapshots.json"
    source_snapshots_root: str = "/tmp/infrawatch/source-snapshots"
    source_snapshot_max_files: int = Field(default=250, ge=1, le=2_000)
    source_snapshot_max_file_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    source_snapshot_max_total_bytes: int = Field(default=25_000_000, ge=10_000, le=250_000_000)
    source_snapshot_max_request_bytes: int = Field(default=30_000_000, ge=10_000, le=300_000_000)
    source_snapshot_timeout_seconds: float = Field(default=30.0, ge=2.0, le=120.0)
    source_snapshot_retention_seconds: int = Field(default=604_800, ge=300, le=31_536_000)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="INFRAWATCH_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings object for dependency injection."""

    return Settings()
