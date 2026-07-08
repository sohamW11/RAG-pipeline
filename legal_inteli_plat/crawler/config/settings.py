"""Typed application configuration.

Configuration is layered:

1. Defaults declared on the Pydantic models below.
2. A YAML file (``config/settings.yaml`` by default, overridable with the
   ``CRAWLER_CONFIG_PATH`` environment variable).
3. A small set of environment-variable overrides for the values that change
   between deployments (secrets, connection strings, feature toggles).

Nothing in the business logic reads environment variables or files directly --
everything flows through the :class:`CrawlerSettings` object, which keeps the
rest of the code honest about *where* configuration comes from and makes the
service trivially testable (build a settings object, inject it).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "settings.yaml"


class AppConfig(BaseModel):
    """Top-level identity / observability settings."""

    name: str = "legal-crawler"
    environment: str = "local"
    log_level: str = "INFO"
    json_logs: bool = True


class DatabaseConfig(BaseModel):
    """SQLAlchemy connection configuration.

    The default is an async SQLite database so the service runs with zero
    infrastructure. In production this is overridden with an
    ``postgresql+asyncpg://`` URL via the ``DATABASE_URL`` environment variable.
    """

    url: str = "sqlite+aiosqlite:///./crawler.db"
    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10


class S3Config(BaseModel):
    """Configuration shared by the S3 and MinIO storage backends."""

    bucket: str = "legal-documents"
    region: str = "us-east-1"
    endpoint_url: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    prefix: str = ""
    secure: bool = True


class StorageConfig(BaseModel):
    """Selects and configures the object-storage backend."""

    backend: str = "local"  # local | s3 | minio
    local_path: str = "./storage-data"
    s3: S3Config = Field(default_factory=S3Config)
    minio: S3Config = Field(default_factory=S3Config)


class KafkaTopics(BaseModel):
    """Logical Kafka topic names used across the pipeline."""

    discovery: str = "crawler.discovery"
    metadata: str = "crawler.metadata"
    downloads: str = "crawler.downloads"
    errors: str = "crawler.errors"
    ready_for_parse: str = "crawler.ready_for_parse"
    dead_letter: str = "crawler.dead_letter"


class KafkaConfig(BaseModel):
    """Event-bus configuration. Disabled by default so the service runs without
    a broker; when disabled a no-op publisher is used and events are logged."""

    enabled: bool = False
    bootstrap_servers: str = "localhost:9092"
    client_id: str = "legal-crawler"
    topics: KafkaTopics = Field(default_factory=KafkaTopics)


class RedisConfig(BaseModel):
    """Cache / distributed rate-limiter backend. Optional."""

    enabled: bool = False
    url: str = "redis://localhost:6379/0"
    namespace: str = "crawler"


class DownloadConfig(BaseModel):
    """Tunables for the download manager."""

    timeout: int = 30
    max_parallel: int = 4
    retry_attempts: int = 3
    retry_backoff_seconds: float = 1.0
    rate_limit_per_second: float = 1.0
    user_agent: str = "LegalIntelligenceCrawler/1.0 (+compliance@example.com)"
    chunk_size: int = 65_536


class SelectorConfig(BaseModel):
    """CSS selectors that teach the generic extractor how to read a listing page.

    Keeping selectors in configuration (rather than code) is what lets us add
    RBI / MCA / IRDAI / PFRDA without touching the crawler logic.
    """

    row: str = "table tr"
    title: str | None = None
    link: str = "a"
    pdf_link: str | None = "a[href$='.pdf']"
    document_number: str | None = None
    publication_date: str | None = None
    department: str | None = None
    date_format: str = "%d %b %Y"


class CategoryConfig(BaseModel):
    """A single crawlable legal category within a source."""

    name: str
    path: str = ""
    document_type: str | None = None
    language: str = "en"
    enabled: bool = True


class SourceConfig(BaseModel):
    """A regulator (SEBI, RBI, ...). Fully declarative."""

    name: str
    base_url: str
    category_path: str = ""
    crawl_frequency: str = "daily"
    enabled: bool = True
    fetcher: str = "httpx"  # httpx | playwright
    selectors: SelectorConfig = Field(default_factory=SelectorConfig)
    categories: list[CategoryConfig] = Field(default_factory=list)
    # Text labels that identify a category link on the discovery landing page.
    discovery_keywords: list[str] = Field(default_factory=list)


class DiscoveryConfig(BaseModel):
    """Controls the discovery stage."""

    enabled: bool = True
    sources: list[SourceConfig] = Field(default_factory=list)


class SchedulerJobConfig(BaseModel):
    """A declarative scheduled job."""

    name: str
    schedule: str = "0 * * * *"  # cron expression
    job_type: str = "crawl"
    priority: int = 5
    enabled: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class SchedulerConfig(BaseModel):
    """Scheduler configuration."""

    enabled: bool = True
    backend: str = "internal"  # internal | temporal (future)
    max_retries: int = 3
    jobs: list[SchedulerJobConfig] = Field(default_factory=list)


class CrawlerSettings(BaseModel):
    """Root settings object injected throughout the service."""

    app: AppConfig = Field(default_factory=AppConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    kafka: KafkaConfig = Field(default_factory=KafkaConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    download: DownloadConfig = Field(default_factory=DownloadConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)

    # ------------------------------------------------------------------ #
    # Construction helpers
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "CrawlerSettings":
        """Build settings from YAML then apply environment overrides."""
        path = Path(config_path or os.getenv("CRAWLER_CONFIG_PATH", DEFAULT_CONFIG_PATH))
        data: dict[str, Any] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        settings = cls.model_validate(data)
        return settings._apply_env_overrides()

    # Backwards-compatible alias used by existing call sites/tests.
    @classmethod
    def from_env(cls, config_path: str | Path | None = None) -> "CrawlerSettings":
        """Alias for :meth:`load`."""
        return cls.load(config_path)

    def _apply_env_overrides(self) -> "CrawlerSettings":
        """Overlay a curated set of environment variables onto the settings.

        Only deployment-varying values are overridable via env so that the YAML
        stays the single source of truth for structural configuration.
        """
        env = os.environ

        if v := env.get("CRAWLER_ENV"):
            self.app.environment = v
        if v := env.get("CRAWLER_LOG_LEVEL"):
            self.app.log_level = v
        if v := env.get("DATABASE_URL"):
            self.database.url = v
        if v := env.get("CRAWLER_STORAGE_BACKEND"):
            self.storage.backend = v
        if v := env.get("CRAWLER_STORAGE_LOCAL_PATH"):
            self.storage.local_path = v

        # Storage secrets (applied to whichever object backend is active).
        obj = self.storage.s3 if self.storage.backend == "s3" else self.storage.minio
        if v := env.get("STORAGE_BUCKET"):
            obj.bucket = v
        if v := env.get("STORAGE_ENDPOINT_URL"):
            obj.endpoint_url = v
        if v := env.get("STORAGE_ACCESS_KEY"):
            obj.access_key = v
        if v := env.get("STORAGE_SECRET_KEY"):
            obj.secret_key = v

        if v := env.get("KAFKA_ENABLED"):
            self.kafka.enabled = v.lower() in {"1", "true", "yes"}
        if v := env.get("KAFKA_BOOTSTRAP_SERVERS"):
            self.kafka.bootstrap_servers = v

        if v := env.get("REDIS_ENABLED"):
            self.redis.enabled = v.lower() in {"1", "true", "yes"}
        if v := env.get("REDIS_URL"):
            self.redis.url = v

        return self


_SETTINGS: CrawlerSettings | None = None


def get_settings() -> CrawlerSettings:
    """Return a process-wide cached settings instance (dependency-injection root)."""
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = CrawlerSettings.load()
    return _SETTINGS
