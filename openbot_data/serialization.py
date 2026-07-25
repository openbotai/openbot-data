"""Deterministic and atomic JSON serialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Write canonical pretty JSON and atomically replace the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
