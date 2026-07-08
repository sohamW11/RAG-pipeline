from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="Crawler Service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict[str, str]:
    return {"status": "running"}


@app.post("/crawl")
def crawl() -> dict[str, str]:
    return {"status": "accepted"}


@app.post("/crawl/category")
def crawl_category() -> dict[str, str]:
    return {"status": "accepted"}


@app.get("/documents")
def documents() -> list[dict[str, str]]:
    return []


@app.get("/categories")
def categories() -> list[dict[str, str]]:
    return []


@app.get("/jobs")
def jobs() -> list[dict[str, str]]:
    return []
