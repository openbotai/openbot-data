"""
OpenBot Data — Inspect robot video data before training.

OpenBot Data 0.0.1.post2 provides basic robot video inspection and dataset metadata generation.
"""

__version__ = "0.0.1.post2"

from openbot_data.video import scan_directory, scan_video
from openbot_data.extract import extract_preview_frames, inspect_dataset
from openbot_data.catalog import export_catalog

__all__ = [
    "scan_directory",
    "scan_video",
    "extract_preview_frames",
    "inspect_dataset",
    "export_catalog",
]
