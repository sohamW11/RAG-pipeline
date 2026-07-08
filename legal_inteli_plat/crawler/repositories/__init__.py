"""Repository layer: typed async persistence, one class per aggregate."""

from crawler.repositories.base import AsyncRepository
from crawler.repositories.category_repository import CategoryRepository
from crawler.repositories.document_repository import DocumentRepository
from crawler.repositories.history_repository import (
    CrawlHistoryRepository,
    DownloadHistoryRepository,
)
from crawler.repositories.job_repository import (
    CrawlerJobRepository,
    SchedulerJobRepository,
)
from crawler.repositories.version_repository import DocumentVersionRepository

__all__ = [
    "AsyncRepository",
    "CategoryRepository",
    "DocumentRepository",
    "DocumentVersionRepository",
    "CrawlHistoryRepository",
    "DownloadHistoryRepository",
    "CrawlerJobRepository",
    "SchedulerJobRepository",
]
