You are a Principal Python Backend Engineer and Software Architect.

Your task is to implement ONLY the crawler microservice.

This project is an Enterprise Regulatory Legal Intelligence Platform.

DO NOT modify or create files outside the crawler/ directory.

DO NOT implement:
- Parser
- OCR
- AI
- RAG
- Neo4j
- Knowledge Graph
- Embeddings
- Frontend
- APIs unrelated to the crawler

Only implement the crawler service.

===========================================================
TECH STACK
===========================================================

Python 3.12

FastAPI

Playwright

httpx

BeautifulSoup4

SQLAlchemy 2.x

Alembic

PostgreSQL

Kafka

Redis

Docker

Pytest

Pydantic v2

Use asyncio wherever possible.

===========================================================
GOAL
===========================================================

Build a production-grade crawler that continuously discovers legal documents from SEBI.

The crawler must support future regulatory websites like RBI, MCA, IRDAI, PFRDA without changing the architecture.

===========================================================
FEATURES
===========================================================

Implement:

1. Discovery Service

Responsible for discovering legal categories.

Examples:

Acts

Rules

Regulations

General Orders

Guidelines

Master Circulars

Circulars

Gazette Notifications

Guidance Notes

Advisories

Orders

Consultation Papers

The discovery service should be configurable.

No hardcoded URLs inside business logic.

===========================================================

2. Category Registry

Store discovered categories.

Fields

id

uuid

name

url

enabled

crawl_frequency

last_crawl

created_at

updated_at

===========================================================

3. Listing Page Crawler

The crawler should crawl listing pages only.

Extract metadata.

Do NOT parse PDFs.

Extract:

Title

Document Number

Publication Date

Effective Date

Department

Category

PDF URL

HTML URL

Language

Document Type

Version

Store metadata into PostgreSQL.

===========================================================

4. Metadata Registry

Create proper SQLAlchemy models.

Tables:

categories

documents

document_versions

crawl_history

download_history

crawler_jobs

scheduler_jobs

===========================================================

5. Change Detection

Before downloading compare:

URL

SHA256

ETag

Last Modified

Publication Date

If unchanged

Skip download.

If changed

Create new version.

===========================================================

6. Download Manager

Download

PDF

HTML

ZIP

Attachments

Support:

async downloads

retry

timeout

rate limiting

parallel downloads

===========================================================

7. Storage Layer

Create an abstraction.

StorageInterface

S3Storage

MinIOStorage

LocalStorage

The rest of the code should never know which storage backend is used.

===========================================================

8. Folder Structure

crawler/

    app/

    api/

    discovery/

    crawler/

    download/

    workers/

    scheduler/

    storage/

    database/

    repositories/

    services/

    models/

    interfaces/

    utils/

    config/

    tests/

===========================================================

9. Scheduler

Implement scheduler architecture.

Support:

Cron

Manual

Priority Queue

Retry

Future Temporal integration

===========================================================

10. Kafka

Create topics:

discovery

metadata

downloads

errors

ready_for_parse

dead_letter

===========================================================

11. FastAPI

Create APIs

GET /health

GET /status

POST /crawl

POST /crawl/category

GET /documents

GET /categories

GET /jobs

===========================================================

12. Docker

Generate

Dockerfile

docker-compose.yml

requirements.txt

.env.example

===========================================================

13. Testing

Implement pytest tests for

Discovery

Metadata extraction

Downloader

Storage

Repositories

===========================================================

14. Logging

Use structured logging.

JSON logs.

Log:

Discovery

Downloads

Retries

Failures

Skipped documents

===========================================================

15. Configuration

Everything configurable through YAML + .env

No values hardcoded.

===========================================================

CODING RULES

Use Clean Architecture.

Use SOLID principles.

Use Repository Pattern.

Use Service Layer.

Use Dependency Injection.

Type hint everything.

Write docstrings.

Avoid duplicate code.

Use async wherever possible.

===========================================================

OUTPUT

Implement the crawler completely.

Generate production-quality code.

After every major component, explain:

1. Why it exists.

2. How it interacts with other modules.

3. How it can scale.

4. Future extension points.

If any requirement is unclear, ask questions before generating code.

At the end, provide:

- Folder tree
- Class diagram
- Sequence diagram
- Database ER diagram
- API documentation
- Docker instructions
- Local setup guide
- Testing guide

Stop after implementing ONLY the crawler microservice.