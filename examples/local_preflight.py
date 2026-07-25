"""Run the OpenBot Data 0.0.2 local preflight workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openbot_data import audit_dataset, inspect_dataset, prepare_dataset


def run_preflight(
    dataset: str | Path,
    output: str | Path,
    *,
    input_format: str = "auto",
    checksum: str | None = "sha256",
    integrity: str = "sample",
    follow_symlinks: bool = False,
) -> dict[str, Any]:
    """Inspect and audit one local dataset using only the public Python API."""
    dataset_path = Path(dataset)
    output_path = Path(output)
    snapshot = prepare_dataset(
        str(dataset_path),
        input_format=input_format,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
    )
    inspection = inspect_dataset(
        str(dataset_path),
        str(output_path / "inspection"),
        input_format=input_format,
        checksum=checksum,
        snapshot=snapshot,
    )
    if "error" in inspection:
        raise RuntimeError(str(inspection["error"]))
    audit = audit_dataset(
        str(dataset_path),
        input_format=input_format,
        checksum=checksum,
        output_path=str(output_path / "audit.json"),
        snapshot=snapshot,
    )
    return {
        "manifest": inspection["manifest_path"],
        "report": inspection["report_path"],
        "audit": str(output_path / "audit.json"),
        "summary": audit["summary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect and audit a local robot-video or LeRobot dataset."
    )
    parser.add_argument("dataset", help="Local dataset directory")
    parser.add_argument("--out", default="./openbot-preflight", help="Output directory")
    parser.add_argument(
        "--format",
        choices=("auto", "video", "lerobot"),
        default="auto",
        dest="input_format",
    )
    parser.add_argument(
        "--integrity",
        choices=("metadata", "sample", "full"),
        default="sample",
        help="Video decode validation level",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow in-root symlinks; out-of-root targets remain rejected",
    )
    parser.add_argument(
        "--no-checksum",
        action="store_true",
        help="Skip optional SHA-256 file checksums",
    )
    args = parser.parse_args()
    result = run_preflight(
        args.dataset,
        args.out,
        input_format=args.input_format,
        checksum=None if args.no_checksum else "sha256",
        integrity=args.integrity,
        follow_symlinks=args.follow_symlinks,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if result["summary"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
