FROM python:3.12-slim

# Keep the source under a top-level `crawler/` package so that `crawler.*`
# imports (used throughout the service) resolve with /app on the path.
WORKDIR /app

# Dependencies first for better layer caching.
COPY requirements.txt /app/crawler/requirements.txt
RUN pip install --no-cache-dir -r /app/crawler/requirements.txt

# Application code as the `crawler` package.
COPY . /app/crawler/

ENV PYTHONPATH=/app \
    CRAWLER_CONFIG_PATH=/app/crawler/config/settings.yaml

# Run the API. Use the CLI instead with:
#   docker compose run --rm crawler python -m crawler crawl --max-pages 1
CMD ["uvicorn", "crawler.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
