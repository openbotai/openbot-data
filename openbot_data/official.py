"""Optional smoke checks backed by the pinned official LeRobot package.

The core package does not depend on LeRobot.  These helpers import it lazily so
CLI verification can become conclusive in the separate conformance
environment, while a core-only install records the missing capability instead
of pretending the loader ran.
"""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Dict

PINNED_LEROBOT_VERSION = "0.6.0"


def smoke_test_lerobot_dataset(path: str) -> Dict[str, Any]:
    """Load first/middle/last rows with ``lerobot==0.6.0`` when available."""
    root = Path(path).resolve()
    try:
        installed_version = version("lerobot")
    except PackageNotFoundError:
        return {
            "status": "unavailable",
            "reason_code": "lerobot_package_not_installed",
            "required_package": f"lerobot[dataset]=={PINNED_LEROBOT_VERSION}",
        }
    if installed_version != PINNED_LEROBOT_VERSION:
        return {
            "status": "unavailable",
            "reason_code": "lerobot_version_not_pinned",
            "required_package": f"lerobot[dataset]=={PINNED_LEROBOT_VERSION}",
            "installed_version": installed_version,
        }
    try:
        module = import_module("lerobot.datasets.lerobot_dataset")
        dataset_type = module.LeRobotDataset
        dataset = dataset_type(
            repo_id="openbot/verification",
            root=root,
        )
        frame_count = len(dataset)
        sample_indices = (
            sorted({0, frame_count // 2, frame_count - 1})
            if frame_count > 0
            else []
        )
        for index in sample_indices:
            dataset[index]
        episode_count = int(dataset.num_episodes)
    except Exception as exc:
        return {
            "status": "failed",
            "package": f"lerobot=={installed_version}",
            "error_type": type(exc).__name__,
            "error": str(exc).replace(str(root), "."),
        }
    return {
        "status": "passed",
        "package": f"lerobot=={installed_version}",
        "loaded_episodes": episode_count,
        "loaded_frames": frame_count,
        "sample_indices": sample_indices,
    }


__all__ = ["PINNED_LEROBOT_VERSION", "smoke_test_lerobot_dataset"]
