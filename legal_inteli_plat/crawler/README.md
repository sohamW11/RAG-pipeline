# Legal Document Crawler

Production crawler for regulatory legal documents. The first supported source is
**SEBI** (`https://www.sebi.gov.in/legal.html`): it discovers the legal
categories, pages through each category's listing, follows every document to its
detail page, resolves the embedded PDF, downloads it, and records the metadata.

The architecture is source-agnostic — RBI / MCA / IRDAI plug in as new adapters
under `sources/` without changing the core (download manager, storage,
repositories, change detection).

## How the SEBI crawl works

```
legal.html                         discover categories (Acts, Rules, … + ssid)
   └─ HomeAction.do?ssid=N          category listing (AJAX-paginated)
        └─ …/<slug>_<id>.html       document detail page
             └─ <iframe file=…pdf>  the real, downloadable PDF
                  └─ download → storage + Postgres/SQLite metadata
```

SEBI specifics handled by `sources/sebi.py`:

- Listing pages are paged via the AJAX endpoint `getnewslistinfo.jsp` with a
  live `JSESSIONID` cookie + `Referer` (a plain GET returns HTTP 530), the page
  index passed as `doDirect`.
- The PDF is embedded in an `<iframe src="…/web/?file=/sebi_data/attachdocs/…">`;
  the `file=` parameter is the direct PDF URL.

## Setup

```bash
cd crawler
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # tweak DATABASE_URL / storage as needed
```

Structural config lives in `config/settings.yaml`; deployment-varying values
(secrets, DB URL, feature toggles) come from the environment (`.env`).

## Usage (CLI)

Run from the **repository root** so the `crawler` package resolves:

```bash
# List the discoverable legal categories
python -m crawler list-categories

# Bounded, polite run: first page of Acts, up to 5 documents
python -m crawler crawl --category Acts --max-pages 1 --max-docs 5

# Several categories
python -m crawler crawl --category Acts --category Regulations --max-pages 2

# Full archival crawl of every category (no limits)
python -m crawler crawl --max-pages 0 --max-docs 0

# Re-download and re-version everything, ignoring the dedup cache
python -m crawler crawl --category Circulars --force
```

Downloaded PDFs land under the configured storage backend with
**human-readable filenames** derived from the document title plus SEBI's stable
document id, e.g.
`storage-data/sebi/acts/securities-contracts-regulation-act-1956…_49750.pdf`.

For each document the crawler also records, in the database:

- the **full title** and **precise publication date** (read from the detail page,
  not just the listing year),
- **keywords** — salient terms derived from the title + category + years
  (e.g. `acts, 1956, 2021, securities, contracts, regulation, finance`),
  stored on `documents.keywords` to give search / RAG cheap context without
  parsing the PDF,
- version history and per-download / per-crawl audit rows.

Re-running is **idempotent**: documents already stored (matched by PDF URL /
document id) are skipped unless `--force` is given.

> A full crawl fetches thousands of PDFs and makes thousands of requests to
> SEBI — run it deliberately, off-peak, and consider the per-request rate limit
> in `config/settings.yaml` (`download.rate_limit_per_second`).

## Usage (HTTP API)

```bash
# From the repo root (dev):
uvicorn crawler.api.app:app --reload
# or in Docker:
docker compose up --build
```

| Method & path        | Purpose                                                        |
| -------------------- | ------------------------------------------------------------- |
| `GET  /health`       | Liveness probe                                                |
| `GET  /status`       | Counts of categories / documents / jobs                       |
| `GET  /categories`   | Categories in the registry                                    |
| `GET  /documents`    | Crawled document metadata (`?limit=&offset=`)                 |
| `GET  /jobs`         | Recent crawl jobs and their results                           |
| `POST /crawl`        | Trigger a background crawl (`{source, categories, max_pages, max_documents, force}`) |
| `POST /crawl/category` | Trigger a background crawl of one category (`{category, ...}`) |

Crawls run in the background and are tracked as `crawler_jobs` rows, so poll
`GET /jobs` to watch a run finish (status `queued` → `completed`/`failed`, with a
result summary in the payload).

```bash
curl -X POST localhost:8000/crawl/category \
     -H 'content-type: application/json' \
     -d '{"category":"Acts","max_pages":1,"max_documents":5}'
curl localhost:8000/jobs
curl localhost:8000/documents
```

## Testing

```bash
cd crawler
python -m pytest        # 20 tests, no network required
```

The SEBI parsers (`parse_categories`, `parse_listing_rows`, `parse_pdf_urls`)
are pure functions tested against fixtures modeled on SEBI's real markup; the
networked adapter is exercised by the CLI end-to-end.

## Layout

```
crawler/
  __main__.py            CLI entrypoint (list-categories / crawl)
  api/                   FastAPI app (health/status, reads, crawl triggers)
  config/                typed settings (YAML + env), settings.yaml
  crawler/               generic listing fetch + selector-driven extraction
  database/              async SQLAlchemy engine/session + Alembic migrations
  discovery/             generic keyword-based category discovery
  download/              async download manager (retry, rate-limit, parallel)
  interfaces/            contracts (storage, rate limiter, messaging)
  models/                ORM models (7 tables)
  repositories/          async repository layer
  services/              change detection + crawl orchestration (crawl_service)
  sources/               per-regulator adapters (sebi.py)
  storage/               local / S3 / MinIO backends behind StorageInterface
  tests/                 pytest suite
```
