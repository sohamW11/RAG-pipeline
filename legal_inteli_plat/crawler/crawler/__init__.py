"""Listing-page crawler subsystem.

Fetches regulator *listing* pages and extracts document metadata. It never
parses PDF/HTML *content* -- that is the parser service's responsibility. The
subsystem is composed of three collaborators:

* :mod:`crawler.crawler.fetcher`   -- how a page's HTML is retrieved
* :mod:`crawler.crawler.extractor` -- how metadata is read from the HTML
* :mod:`crawler.crawler.listing_crawler` -- orchestrates fetch + extract
"""

from crawler.crawler.extractor import ConfigurableListingExtractor, DocumentMetadata
from crawler.crawler.fetcher import HttpxFetcher, PageFetcher, create_fetcher
from crawler.crawler.listing_crawler import ListingCrawler

__all__ = [
    "DocumentMetadata",
    "ConfigurableListingExtractor",
    "PageFetcher",
    "HttpxFetcher",
    "create_fetcher",
    "ListingCrawler",
]
