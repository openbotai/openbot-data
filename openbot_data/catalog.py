"""
Catalog export for OpenBot Data.

Renders a prepared dataset snapshot into formats useful for indexing, sharing,
and linking from documentation or dataset registries.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Optional

from openbot_data.errors import DatasetArgumentError, DatasetNotFoundError
from openbot_data.models import DatasetSnapshot, VideoRecord
from openbot_data.preflight import prepare_dataset, validate_snapshot_request
from openbot_data.serialization import write_json_atomic

CATALOG_SCHEMA_VERSION = "openbot.dataset_catalog.v1"
SUPPORTED_FORMATS = {"json", "csv"}


def _flatten_video(video: VideoRecord) -> Dict[str, Any]:
    """Flatten a video entry into a catalog-friendly row."""
    return {
        "source_dir": ".",
        "filename": video.filename,
        "path": video.path,
        "width": video.width,
        "height": video.height,
        "fps": round(
            video.raw_fps if video.raw_fps is not None else video.fps,
            2,
        ),
        "frame_count": video.frame_count,
        "duration": round(
            video.raw_duration if video.raw_duration is not None else video.duration,
            2,
        ),
        "size_mb": video.size_mb,
        "is_valid": video.is_valid,
        "error": video.error or "",
    }


def export_catalog(
    video_dir: str,
    output_path: str,
    fmt: str = "json",
    *,
    input_format: str = "auto",
    checksum: Optional[str] = None,
    integrity: str = "sample",
    follow_symlinks: bool = False,
    snapshot: DatasetSnapshot | None = None,
) -> Dict[str, Any]:
    """
    Export a prepared robot video dataset as a catalog file.

    Args:
        video_dir: Path to the input video directory or local LeRobot dataset.
        output_path: Path to the output catalog file.
        fmt: Output format, either "json" or "csv".
        input_format: Input format: ``auto``, ``video``, or ``lerobot``.
        checksum: Optional checksum algorithm. Only ``sha256`` is supported.
        integrity: Decode validation level: ``metadata``, ``sample``, or ``full``.
        follow_symlinks: Allow media symlinks that resolve inside the dataset root.
        snapshot: Optional compatible snapshot from :func:`prepare_dataset`.

    Returns:
        Dictionary with the output path and record count.
    """
    fmt = fmt.lower().lstrip(".")
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported catalog format: {fmt}. Use one of {SUPPORTED_FORMATS}")

    try:
        if snapshot is None:
            prepared = prepare_dataset(
                video_dir,
                input_format=input_format,
                checksum=checksum,
                integrity=integrity,
                follow_symlinks=follow_symlinks,
            )
        else:
            prepared = validate_snapshot_request(
                snapshot,
                video_dir,
                input_format,
                checksum,
                integrity,
                follow_symlinks,
            )
    except (DatasetArgumentError, DatasetNotFoundError) as exc:
        return {"error": str(exc), "videos": []}

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [_flatten_video(video) for video in prepared.videos]

    summary = {
        "source_dir": ".",
        "total_videos": len(rows),
        "valid_videos": sum(1 for row in rows if row["is_valid"]),
        "invalid_videos": sum(1 for row in rows if not row["is_valid"]),
        "total_duration": round(
            sum(float(row["duration"]) for row in rows if row["is_valid"]),
            2,
        ),
        "total_size_mb": round(sum(float(row["size_mb"]) for row in rows), 2),
    }

    if fmt == "json":
        catalog = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "input_format": prepared.input_format,
            "codebase_version": prepared.codebase_version,
            "summary": summary,
            "videos": rows,
        }
        write_json_atomic(out_path, catalog)
    else:  # csv
        fieldnames = list(rows[0].keys()) if rows else []
        temporary = out_path.with_suffix(out_path.suffix + ".tmp")
        with open(temporary, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(out_path)

    return {
        "output_path": str(out_path),
        "format": fmt,
        "total_videos": len(rows),
    }
