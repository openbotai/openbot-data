"""
Catalog export for OpenBot Data.

Exports a scanned robot video dataset into formats useful for indexing,
sharing, and linking from documentation or dataset registries.
"""

import csv
from pathlib import Path
from typing import Any, Dict

from openbot_data.serialization import write_json_atomic
from openbot_data.video import scan_directory

SUPPORTED_FORMATS = {"json", "csv"}


def _flatten_video(video: Dict[str, Any], source_dir: str) -> Dict[str, Any]:
    """Flatten a video entry into a catalog-friendly row."""
    return {
        "source_dir": source_dir,
        "filename": video["filename"],
        "path": video["path"],
        "width": video["width"],
        "height": video["height"],
        "fps": round(video["fps"], 2),
        "frame_count": video["frame_count"],
        "duration": video["duration"],
        "size_mb": video["size_mb"],
        "is_valid": video["is_valid"],
        "error": video["error"] or "",
    }


def export_catalog(
    video_dir: str,
    output_path: str,
    fmt: str = "json",
) -> Dict[str, Any]:
    """
    Export a scanned robot video directory as a catalog file.

    Args:
        video_dir: Path to the input video directory.
        output_path: Path to the output catalog file.
        fmt: Output format, either "json" or "csv".

    Returns:
        Dictionary with the output path and record count.
    """
    fmt = fmt.lower().lstrip(".")
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported catalog format: {fmt}. Use one of {SUPPORTED_FORMATS}")

    scan_result = scan_directory(video_dir)
    if "error" in scan_result:
        return scan_result

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [_flatten_video(v, scan_result["directory"]) for v in scan_result["videos"]]

    summary = {
        "source_dir": scan_result["directory"],
        "total_videos": scan_result["total_videos"],
        "valid_videos": scan_result["valid_videos"],
        "invalid_videos": scan_result["invalid_videos"],
        "total_duration": scan_result["total_duration"],
        "total_size_mb": scan_result["total_size_mb"],
    }

    if fmt == "json":
        catalog = {
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
