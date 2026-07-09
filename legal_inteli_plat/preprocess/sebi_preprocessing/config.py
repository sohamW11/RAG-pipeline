"""Typed configuration, layered exactly like the Phase-1 crawler.

1. Defaults on the pydantic models below.
2. A YAML file (``config.yaml`` next to the package root, overridable with the
   ``SEBI_PREPROCESS_CONFIG`` environment variable).

Business logic reads settings only through :class:`PreprocessSettings` — never
the filesystem or environment directly — which keeps thresholds in one place
and the pipeline trivially testable (build a settings object, inject it).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

# config.yaml lives at the service root, one level above the package.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class AppConfig(BaseModel):
    log_level: str = "INFO"
    json_logs: bool = True


class PathsConfig(BaseModel):
    input_dir: str = "data/pdfs"
    parsed_dir: str = "parsed"
    logs_dir: str = "logs"
    inventory_path: str | None = None


class TriageConfig(BaseModel):
    native_char_threshold: int = 100
    render_dpi: int = 300


class CamelotConfig(BaseModel):
    enabled: bool = True
    flavor: str = "lattice"


class DoclingConfig(BaseModel):
    enabled: bool = True


class OcrConfig(BaseModel):
    adapter: str = "tesseract"
    language: str = "eng"


class ParsersConfig(BaseModel):
    docling: DoclingConfig = Field(default_factory=DoclingConfig)
    camelot: CamelotConfig = Field(default_factory=CamelotConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)


class TableGateConfig(BaseModel):
    min_rows: int = 2
    min_cols: int = 2
    max_empty_cell_ratio: float = 0.40
    max_single_cell_text_share: float = 0.60


class PreprocessSettings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    triage: TriageConfig = Field(default_factory=TriageConfig)
    parsers: ParsersConfig = Field(default_factory=ParsersConfig)
    table_gate: TableGateConfig = Field(default_factory=TableGateConfig)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "PreprocessSettings":
        path = Path(
            config_path or os.getenv("SEBI_PREPROCESS_CONFIG", DEFAULT_CONFIG_PATH)
        )
        data: dict[str, Any] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        return cls.model_validate(data)


_SETTINGS: PreprocessSettings | None = None


def get_settings() -> PreprocessSettings:
    """Process-wide cached settings instance (dependency-injection root)."""
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = PreprocessSettings.load()
    return _SETTINGS
