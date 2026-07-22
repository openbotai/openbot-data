"""
OpenBot Data — Inspect robot video data before training.

OpenBot Data provides basic robot video inspection and dataset metadata generation.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("openbot-data")
except PackageNotFoundError:  # pragma: no cover - raw source tree without installation
    __version__ = "0+unknown"

from openbot_data.video import scan_directory, scan_video
from openbot_data.extract import (
    build_contact_sheets,
    extract_preview_frames,
    extract_timestamped_frames,
    inspect_dataset,
)
from openbot_data.catalog import export_catalog

__all__ = [
    "scan_directory",
    "scan_video",
    "extract_preview_frames",
    "extract_timestamped_frames",
    "build_contact_sheets",
    "inspect_dataset",
    "export_catalog",
]
