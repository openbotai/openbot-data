"""Access packaged machine-readable output schemas."""

from __future__ import annotations

from contextlib import AbstractContextManager
from importlib.resources import as_file, files
from pathlib import Path


def schema_path(name: str) -> AbstractContextManager[Path]:
    """Return a context manager yielding a packaged JSON Schema path."""
    supported = {
        "manifest": "dataset-manifest-v1.schema.json",
        "audit": "dataset-audit-v1.schema.json",
        "catalog_evidence": "catalog-evidence-v1.schema.json",
    }
    try:
        filename = supported[name]
    except KeyError as exc:
        raise ValueError(f"Unknown schema {name!r}; use one of {sorted(supported)}") from exc
    return as_file(files("openbot_data.schemas").joinpath(filename))
