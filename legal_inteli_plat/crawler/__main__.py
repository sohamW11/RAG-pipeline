"""Command-line entrypoint for the crawler.

Examples
--------
List the discoverable legal categories::

    python -m crawler list-categories

Crawl a few Acts (polite, bounded) and store the PDFs locally::

    python -m crawler crawl --category Acts --max-pages 1 --max-docs 5

Full archival crawl of every category (no limits)::

    python -m crawler crawl --max-pages 0 --max-docs 0
"""

from __future__ import annotations

import argparse
import asyncio

from crawler.config.settings import get_settings
from crawler.database.session import get_database
from crawler.services.crawl_service import CrawlService
from crawler.utils.logging import configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crawler", description="Legal document crawler")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", default="sebi", help="Configured source name (default: sebi)")

    p_list = sub.add_parser(
        "list-categories", parents=[common], help="Discover and print legal categories"
    )
    p_list.set_defaults(func=_cmd_list_categories)

    p_crawl = sub.add_parser(
        "crawl", parents=[common], help="Crawl a source and download its PDFs"
    )
    p_crawl.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="Restrict to this category (repeatable). Omit to crawl all.",
    )
    p_crawl.add_argument(
        "--max-pages", type=int, default=None, help="Max listing pages per category (0 = all)"
    )
    p_crawl.add_argument(
        "--max-docs", type=int, default=None, help="Max documents per category (0 = all)"
    )
    p_crawl.add_argument(
        "--force", action="store_true", help="Re-download even if already stored"
    )
    p_crawl.add_argument(
        "--no-archive",
        dest="include_archive",
        action="store_false",
        help="Skip each section's 'Historical Data' archive (crawl active only)",
    )
    p_crawl.set_defaults(func=_cmd_crawl, include_archive=True)
    return parser


async def _cmd_list_categories(args: argparse.Namespace) -> int:
    service = CrawlService()
    try:
        categories = await service.list_categories(args.source)
    finally:
        await service.close()
    print(f"{len(categories)} categories for source {args.source!r}:")
    for category in categories:
        print(f"  - {category.name}  (ssid={category.ssid})")
    return 0


async def _cmd_crawl(args: argparse.Namespace) -> int:
    # Ensure the schema exists for local/dev SQLite runs.
    await get_database().create_all()

    service = CrawlService()
    try:
        summary = await service.crawl(
            args.source,
            categories=args.categories,
            max_pages=args.max_pages,
            max_documents=args.max_docs,
            force=args.force,
            include_archive=args.include_archive,
        )
    finally:
        await service.close()

    print(f"\nCrawl summary for {summary.source!r}:")
    for result in summary.categories:
        print(
            f"  {result.category:<24} found={result.found:<4} "
            f"downloaded={result.downloaded:<4} skipped={result.skipped:<4} "
            f"failed={result.failed}"
        )
    print(
        f"  {'TOTAL':<24} found={summary.found:<4} downloaded={summary.downloaded:<4} "
        f"skipped={summary.skipped:<4} failed={summary.failed}"
    )
    return 0


def main() -> int:
    settings = get_settings()
    configure_logging(level=settings.app.log_level, json_logs=settings.app.json_logs)
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
