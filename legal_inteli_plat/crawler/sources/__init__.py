"""Source adapters.

Each regulator whose site needs bespoke navigation (pagination scheme, PDF
embedding, ...) gets an adapter here. Adapters reuse the shared building blocks
(download manager, storage, repositories) and only encode what is unique to the
source, so adding RBI / MCA / IRDAI is additive -- no changes to the core.
"""

from crawler.sources.sebi import (
    SebiCategory,
    SebiDetail,
    SebiListingItem,
    SebiSource,
    parse_categories,
    parse_detail_metadata,
    parse_listing_rows,
    parse_pdf_urls,
)

__all__ = [
    "SebiSource",
    "SebiCategory",
    "SebiListingItem",
    "SebiDetail",
    "parse_categories",
    "parse_listing_rows",
    "parse_pdf_urls",
    "parse_detail_metadata",
]
