"""Deterministic and atomic JSON serialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def write_text_atomic(path: Path, value: str) -> None:
    """Write UTF-8 text through a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Write canonical pretty JSON and atomically replace the destination."""
    write_text_atomic(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
