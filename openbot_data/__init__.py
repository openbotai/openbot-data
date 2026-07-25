"""
OpenBot Data — Inspect robot video data before training.

OpenBot Data provides basic robot video inspection and dataset metadata generation.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("openbot-data")
except PackageNotFoundError:  # pragma: no cover - raw source tree without installation
    __version__ = "0+unknown"

from openbot_data.catalog import export_catalog
from openbot_data.errors import DatasetArgumentError, DatasetNotFoundError, OpenBotDataError
from openbot_data.extract import (
    build_contact_sheets,
    extract_preview_frames,
    extract_timestamped_frames,
    inspect_dataset,
)
from openbot_data.models import DatasetSnapshot, EpisodeRecord, VideoRecord
from openbot_data.preflight import (
    audit_dataset,
    detect_input_format,
    prepare_dataset,
    read_lerobot,
)
from openbot_data.schema import schema_path
from openbot_data.video import scan_directory, scan_video

__all__ = [
    "scan_directory",
    "scan_video",
    "extract_preview_frames",
    "extract_timestamped_frames",
    "build_contact_sheets",
    "inspect_dataset",
    "export_catalog",
    "audit_dataset",
    "detect_input_format",
    "read_lerobot",
    "prepare_dataset",
    "DatasetSnapshot",
    "EpisodeRecord",
    "VideoRecord",
    "OpenBotDataError",
    "DatasetNotFoundError",
    "DatasetArgumentError",
    "schema_path",
]
