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
from openbot_data.catalog_evidence import build_catalog_evidence
from openbot_data.diff import diff_dataset_snapshots
from openbot_data.errors import DatasetArgumentError, DatasetNotFoundError, OpenBotDataError
from openbot_data.extract import (
    build_contact_sheets,
    extract_preview_frames,
    extract_timestamped_frames,
    inspect_dataset,
)
from openbot_data.hub import (
    HubDownloadBudget,
    HubResolution,
    HubSourceRequest,
    parse_hub_source,
    resolve_hub_dataset,
)
from openbot_data.hub_audit import (
    audit_hub_dataset,
    evaluate_hub_dataset_readiness,
    snapshot_hub_dataset,
)
from openbot_data.merge import (
    check_merge_compatibility,
    verify_dataset_merge,
)
from openbot_data.models import DatasetSnapshot, EpisodeRecord, VideoRecord
from openbot_data.preflight import (
    audit_dataset,
    detect_input_format,
    prepare_dataset,
    read_lerobot,
)
from openbot_data.readiness import (
    evaluate_dataset_readiness,
    load_readiness_profile,
    render_readiness_markdown,
)
from openbot_data.repair import (
    apply_dataset_repair,
    plan_dataset_repair,
    verify_dataset_repair,
)
from openbot_data.schema import schema_path
from openbot_data.snapshot import build_dataset_snapshot
from openbot_data.triage import analyze_advisory_signals, triage_findings
from openbot_data.video import scan_directory, scan_video

__all__ = [
    "scan_directory",
    "scan_video",
    "extract_preview_frames",
    "extract_timestamped_frames",
    "build_contact_sheets",
    "inspect_dataset",
    "export_catalog",
    "build_catalog_evidence",
    "build_dataset_snapshot",
    "diff_dataset_snapshots",
    "evaluate_dataset_readiness",
    "load_readiness_profile",
    "render_readiness_markdown",
    "triage_findings",
    "analyze_advisory_signals",
    "HubDownloadBudget",
    "HubResolution",
    "HubSourceRequest",
    "parse_hub_source",
    "resolve_hub_dataset",
    "audit_hub_dataset",
    "snapshot_hub_dataset",
    "evaluate_hub_dataset_readiness",
    "plan_dataset_repair",
    "apply_dataset_repair",
    "verify_dataset_repair",
    "check_merge_compatibility",
    "verify_dataset_merge",
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
