"""Metadata extraction from listing pages.

The extractor reads *metadata only* from a listing page's HTML -- titles,
document numbers, dates, links -- and never downloads or parses the underlying
document. Extraction is driven by CSS selectors declared in configuration
(:class:`~crawler.config.settings.SelectorConfig`), which is what makes the
crawler retargetable to new regulators without code changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag
from pydantic import BaseModel

from crawler.config.settings import CategoryConfig, SelectorConfig
from crawler.utils.logging import get_logger

logger = get_logger("crawler.extractor")


class DocumentMetadata(BaseModel):
    """Normalised metadata for a single discovered document.

    This is the crawler's public output type; it maps 1:1 onto the columns of
    the ``documents`` table but is decoupled from the ORM so it can travel over
    Kafka and through the service layer as a plain value object.
    """

    title: str
    document_number: Optional[str] = None
    publication_date: Optional[datetime] = None
    effective_date: Optional[datetime] = None
    department: Optional[str] = None
    category_name: Optional[str] = None
    pdf_url: Optional[str] = None
    html_url: Optional[str] = None
    source_url: Optional[str] = None
    language: str = "en"
    document_type: Optional[str] = None
    version: str = "1"


def _parse_date(text: Optional[str], fmt: str) -> Optional[datetime]:
    """Best-effort parse of a date string, trying the configured format first."""
    if not text:
        return None
    text = text.strip()
    for candidate in (fmt, "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%b %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, candidate)
        except ValueError:
            continue
    logger.info("date_unparsed", value=text)
    return None


class ListingExtractor:
    """Base class for listing extractors (Strategy pattern)."""

    def extract(
        self,
        html: str,
        *,
        base_url: str,
        selectors: SelectorConfig,
        category: Optional[CategoryConfig] = None,
    ) -> list[DocumentMetadata]:
        """Return metadata for every document row on the page."""
        raise NotImplementedError


class ConfigurableListingExtractor(ListingExtractor):
    """Generic, selector-driven extractor usable for most regulator sites."""

    def _select_text(self, row: Tag, selector: Optional[str]) -> Optional[str]:
        if not selector:
            return None
        node = row.select_one(selector)
        return node.get_text(" ", strip=True) if node else None

    def _select_href(self, row: Tag, selector: Optional[str], base_url: str) -> Optional[str]:
        if not selector:
            return None
        node = row.select_one(selector)
        if node and node.has_attr("href"):
            return urljoin(base_url, node["href"])
        return None

    def extract(
        self,
        html: str,
        *,
        base_url: str,
        selectors: SelectorConfig,
        category: Optional[CategoryConfig] = None,
    ) -> list[DocumentMetadata]:
        soup = BeautifulSoup(html, "html.parser")
        documents: list[DocumentMetadata] = []

        for row in soup.select(selectors.row):
            if not isinstance(row, Tag):
                continue

            # Title: prefer configured selector, else the primary link text.
            title = self._select_text(row, selectors.title)
            link_node = row.select_one(selectors.link)
            if not title and link_node:
                title = link_node.get_text(" ", strip=True)
            if not title:
                continue  # Not a document row.

            pdf_url = self._select_href(row, selectors.pdf_link, base_url)
            html_url = self._select_href(row, selectors.link, base_url)
            if not pdf_url and not html_url:
                continue  # Nothing linkable; skip noise rows.

            metadata = DocumentMetadata(
                title=title,
                document_number=self._select_text(row, selectors.document_number),
                publication_date=_parse_date(
                    self._select_text(row, selectors.publication_date), selectors.date_format
                ),
                department=self._select_text(row, selectors.department),
                category_name=category.name if category else None,
                pdf_url=pdf_url,
                html_url=html_url,
                source_url=html_url or pdf_url,
                language=category.language if category else "en",
                document_type=category.document_type if category else None,
            )
            documents.append(metadata)

        logger.info("metadata_extracted", base_url=base_url, count=len(documents))
        return documents


# Registry allowing per-source custom extractors; defaults to the configurable one.
_EXTRACTORS: dict[str, ListingExtractor] = {}


def get_extractor(source_name: str) -> ListingExtractor:
    """Return the extractor registered for a source, or the default."""
    return _EXTRACTORS.get(source_name, ConfigurableListingExtractor())


def register_extractor(source_name: str, extractor: ListingExtractor) -> None:
    """Register a bespoke extractor for a source (future extension point)."""
    _EXTRACTORS[source_name] = extractor
