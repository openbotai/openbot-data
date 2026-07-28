"""
Command-line interface for OpenBot Data.
"""

import json
from pathlib import Path
from typing import Any, Optional

import typer

from openbot_data.catalog import SUPPORTED_FORMATS, export_catalog
from openbot_data.catalog_evidence import (
    CATALOG_EVIDENCE_PROFILE,
    build_catalog_evidence,
)
from openbot_data.diff import CLASSIFICATION_RANK, diff_dataset_snapshots
from openbot_data.errors import OpenBotDataError
from openbot_data.extract import inspect_dataset
from openbot_data.hub import HubDownloadBudget, HubSourceError
from openbot_data.hub_audit import (
    audit_hub_dataset,
    evaluate_hub_dataset_readiness,
    snapshot_hub_dataset,
)
from openbot_data.merge import (
    check_merge_compatibility,
    verify_dataset_merge,
)
from openbot_data.official import smoke_test_lerobot_dataset
from openbot_data.preflight import audit_dataset
from openbot_data.readiness import (
    evaluate_dataset_readiness,
    render_readiness_markdown,
)
from openbot_data.repair import (
    apply_dataset_repair,
    plan_dataset_repair,
    verify_dataset_repair,
)
from openbot_data.serialization import write_json_atomic, write_text_atomic
from openbot_data.snapshot import build_dataset_snapshot
from openbot_data.video import scan_directory

app = typer.Typer(
    name="openbot-data",
    help="Inspect robot video datasets and generate reviewable metadata.",
    add_completion=False
)
repair_app = typer.Typer(
    help="Plan and apply conservative copy-on-write metadata repairs.",
    add_completion=False,
)
app.add_typer(repair_app, name="repair")


def _read_json_object(path: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} could not be read: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


@app.command()
def scan(
    path: str = typer.Argument(..., help="Path to robot video directory"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output JSON file path")
):
    """
    Scan a directory for robot videos and show metadata.
    """
    import json

    result = scan_directory(path)

    if "error" in result:
        typer.echo(f"Error: {result['error']}", err=True)
        raise typer.Exit(1)

    if output:
        output_path = Path(output)
        write_json_atomic(output_path, result)
        typer.echo(f"Scan results saved to {output_path}")
    else:
        typer.echo(json.dumps(result, indent=2))


@app.command()
def inspect(
    video_dir: str = typer.Argument(..., help="Path to video directory or local LeRobot dataset"),
    output: str = typer.Option(..., "--out", "-o", help="Output directory"),
    input_format: str = typer.Option(
        "auto", "--format", "-f", help="Input format: auto, video, or lerobot"
    ),
    checksum: Optional[str] = typer.Option(
        None, "--checksum", help="Optional file checksum: sha256"
    ),
    integrity: str = typer.Option(
        "sample", "--integrity", help="Decode validation: metadata, sample, or full"
    ),
    follow_symlinks: bool = typer.Option(
        False,
        "--follow-symlinks",
        help="Allow media symlinks that still resolve inside the dataset root",
    ),
):
    """
    Inspect robot videos: extract preview frames and generate manifest.
    """
    typer.echo(f"Inspecting robot videos in {video_dir}...")

    result = inspect_dataset(
        video_dir=video_dir,
        output_dir=output,
        input_format=input_format,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
    )

    if "error" in result:
        typer.echo(f"Error: {result['error']}", err=True)
        raise typer.Exit(1)

    typer.echo("")
    typer.echo("Inspection complete!")
    typer.echo("")
    typer.echo(f"Output directory: {output}")
    typer.echo(f"   Total videos: {result['total_videos']}")
    typer.echo(f"   Preview frames: {result['total_previews']}")
    typer.echo("")
    typer.echo("Generated files:")
    typer.echo(f"   {result['manifest_path']}")
    typer.echo(f"   {result['report_path']}")


@app.command()
def audit(
    dataset_dir: str = typer.Argument(..., help="Path to video directory or local LeRobot dataset"),
    output: str = typer.Option(..., "--out", "-o", help="Output audit JSON path"),
    input_format: str = typer.Option(
        "auto", "--format", "-f", help="Input format: auto, video, or lerobot"
    ),
    checksum: Optional[str] = typer.Option(
        None, "--checksum", help="Optional file checksum: sha256"
    ),
    integrity: str = typer.Option(
        "sample", "--integrity", help="Decode validation: metadata, sample, or full"
    ),
    follow_symlinks: bool = typer.Option(
        False,
        "--follow-symlinks",
        help="Allow media symlinks that still resolve inside the dataset root",
    ),
    fail_on: str = typer.Option(
        "none", "--fail-on", help="Exit non-zero on: none, warning, or error"
    ),
    hub_max_bytes: int = typer.Option(
        2_000_000_000,
        "--hub-max-bytes",
        help="Hard Hub download byte budget",
    ),
    hub_max_shards: int = typer.Option(
        12,
        "--hub-max-shards",
        help="Hard Hub payload-shard budget",
    ),
    hub_max_episodes: int = typer.Option(
        64,
        "--hub-max-episodes",
        help="Hard Hub episode-selection budget",
    ),
    hub_max_media_shards: int = typer.Option(
        9,
        "--hub-max-media-shards",
        help="Hard Hub media-shard budget",
    ),
):
    """Audit a local or revision-pinned Hub dataset."""
    normalized_fail_on = fail_on.lower()
    if normalized_fail_on not in {"none", "warning", "error"}:
        typer.echo("Error: --fail-on must be none, warning, or error", err=True)
        raise typer.Exit(1)

    try:
        if dataset_dir.startswith("hf://"):
            result = audit_hub_dataset(
                dataset_dir,
                checksum=checksum,
                integrity=integrity,
                follow_symlinks=follow_symlinks,
                budget=HubDownloadBudget(
                    max_bytes=hub_max_bytes,
                    max_shards=hub_max_shards,
                    max_episodes=hub_max_episodes,
                    max_media_shards=hub_max_media_shards,
                ),
                output_path=output,
            )
        else:
            result = audit_dataset(
                dataset_dir,
                input_format=input_format,
                checksum=checksum,
                output_path=output,
                integrity=integrity,
                follow_symlinks=follow_symlinks,
            )
    except (OpenBotDataError, HubSourceError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    summary = result["summary"]
    typer.echo(f"Audit saved to {output}")
    typer.echo(
        f"  errors: {summary['error']}  warnings: {summary['warning']}  info: {summary['info']}"
    )
    should_fail = summary["error"] > 0 if normalized_fail_on == "error" else False
    if normalized_fail_on == "warning":
        should_fail = summary["error"] > 0 or summary["warning"] > 0
    if should_fail:
        raise typer.Exit(2)


@app.command()
def catalog(
    video_dir: str = typer.Argument(..., help="Path to video directory"),
    output: str = typer.Option(..., "--out", "-o", help="Output catalog file path"),
    fmt: str = typer.Option(
        "json",
        "--format",
        "-f",
        help=f"Catalog format: {', '.join(sorted(SUPPORTED_FORMATS))}",
    ),
):
    """
    Export a robot video catalog as JSON or CSV.

    Useful for dataset registries, documentation, and SEO-friendly
    catalog pages that link back to OpenBot.ai.
    """
    result = export_catalog(video_dir=video_dir, output_path=output, fmt=fmt)

    if "error" in result:
        typer.echo(f"Error: {result['error']}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Catalog exported to {result['output_path']}")
    typer.echo(f"  format: {result['format']}")
    typer.echo(f"  videos: {result['total_videos']}")


@app.command("catalog-evidence")
def catalog_evidence(
    dataset_dir: str = typer.Argument(..., help="Local dataset or revision-pinned checkout"),
    dataset_id: str = typer.Option(..., "--dataset-id", help="Portable Catalog dataset ID"),
    checked_at: str = typer.Option(
        ...,
        "--checked-at",
        help="Timezone-aware RFC 3339 time for the completed audit",
    ),
    output: str = typer.Option(..., "--out", "-o", help="Output evidence JSON path"),
    source_kind: str = typer.Option(
        "local",
        "--source-kind",
        help="Source kind: local or hf_hub",
    ),
    source_locator: Optional[str] = typer.Option(
        None,
        "--source-locator",
        help="Portable public source locator; absolute local paths are rejected",
    ),
    resolved_revision: Optional[str] = typer.Option(
        None,
        "--resolved-revision",
        help="Immutable resolved Hub revision; required for hf_hub",
    ),
    input_format: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help="Input format: auto, video, or lerobot",
    ),
    integrity: str = typer.Option(
        "sample",
        "--integrity",
        help="Decode validation: metadata, sample, or full",
    ),
    profile_id: str = typer.Option(
        CATALOG_EVIDENCE_PROFILE,
        "--profile",
        help="Built-in readiness profile identifier",
    ),
    follow_symlinks: bool = typer.Option(
        False,
        "--follow-symlinks",
        help="Allow media symlinks that still resolve inside the dataset root",
    ),
):
    """Write score-free audit evidence for server-side Catalog evaluation."""
    try:
        result = build_catalog_evidence(
            dataset_dir,
            dataset_id=dataset_id,
            checked_at=checked_at,
            source_kind=source_kind,
            source_locator=source_locator,
            resolved_revision=resolved_revision,
            input_format=input_format,
            checksum="sha256",
            integrity=integrity,
            follow_symlinks=follow_symlinks,
            profile_id=profile_id,
            output_path=output,
        )
    except (OpenBotDataError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    readiness = result["facts"]["dataset.profile_readiness"]["value"]["status"]
    typer.echo(f"Catalog evidence saved to {output}")
    typer.echo(f"  evidence maturity: {result['evidence_maturity']}")
    typer.echo(f"  profile readiness: {readiness}")
    typer.echo(f"  evidence fingerprint: {result['evidence_fingerprint']}")


@app.command("snapshot")
def snapshot_command(
    dataset_dir: str = typer.Argument(
        ...,
        help="Path to a video directory or local LeRobot dataset",
    ),
    output: str = typer.Option(..., "--out", "-o", help="Output snapshot JSON path"),
    input_format: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help="Input format: auto, video, or lerobot",
    ),
    checksum: Optional[str] = typer.Option(
        "sha256",
        "--checksum",
        help="Content checksum: sha256 or omit with an empty value",
    ),
    integrity: str = typer.Option(
        "sample",
        "--integrity",
        help="Validation coverage: metadata, sample, or full",
    ),
    follow_symlinks: bool = typer.Option(
        False,
        "--follow-symlinks",
        help="Allow media symlinks that resolve inside the dataset root",
    ),
    hub_max_bytes: int = typer.Option(
        2_000_000_000,
        "--hub-max-bytes",
        help="Hard Hub download byte budget",
    ),
    hub_max_shards: int = typer.Option(
        12,
        "--hub-max-shards",
        help="Hard Hub payload-shard budget",
    ),
    hub_max_episodes: int = typer.Option(
        64,
        "--hub-max-episodes",
        help="Hard Hub episode-selection budget",
    ),
    hub_max_media_shards: int = typer.Option(
        9,
        "--hub-max-media-shards",
        help="Hard Hub media-shard budget",
    ),
):
    """Write a portable deterministic dataset snapshot."""
    try:
        if dataset_dir.startswith("hf://"):
            result = snapshot_hub_dataset(
                dataset_dir,
                checksum=checksum,
                integrity=integrity,
                follow_symlinks=follow_symlinks,
                budget=HubDownloadBudget(
                    max_bytes=hub_max_bytes,
                    max_shards=hub_max_shards,
                    max_episodes=hub_max_episodes,
                    max_media_shards=hub_max_media_shards,
                ),
                output_path=output,
            )
        else:
            result = build_dataset_snapshot(
                dataset_dir,
                input_format=input_format,
                checksum=checksum,
                integrity=integrity,
                follow_symlinks=follow_symlinks,
                output_path=output,
            )
    except (OpenBotDataError, HubSourceError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Snapshot saved to {output}")
    typer.echo(f"  fingerprint: {result['snapshot_fingerprint']}")


@app.command("diff")
def diff_command(
    baseline: str = typer.Argument(..., help="Baseline dataset snapshot JSON"),
    candidate: str = typer.Argument(..., help="Candidate dataset snapshot JSON"),
    output: str = typer.Option(..., "--out", "-o", help="Output diff JSON path"),
    fail_on: str = typer.Option(
        "none",
        "--fail-on",
        help="Exit 2 on: none, material, or breaking",
    ),
):
    """Compare two portable dataset snapshots."""
    normalized_fail_on = fail_on.strip().lower()
    if normalized_fail_on not in {"none", "material", "breaking"}:
        typer.echo(
            "Error: --fail-on must be none, material, or breaking",
            err=True,
        )
        raise typer.Exit(1)
    try:
        result = diff_dataset_snapshots(
            baseline,
            candidate,
            output_path=output,
        )
    except (OpenBotDataError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    classification = str(result["classification"])
    typer.echo(f"Diff saved to {output}")
    typer.echo(f"  classification: {classification}")
    if normalized_fail_on != "none" and (
        CLASSIFICATION_RANK[classification]
        >= CLASSIFICATION_RANK[normalized_fail_on]
    ):
        raise typer.Exit(2)


@app.command("readiness")
def readiness_command(
    dataset_dir: str = typer.Argument(
        ...,
        help="Path to a local LeRobot dataset",
    ),
    output: str = typer.Option(..., "--out", "-o", help="Output readiness JSON path"),
    profile: str = typer.Option(
        "lerobot-core",
        "--profile",
        help="Built-in readiness profile ID",
    ),
    policy_config: Optional[str] = typer.Option(
        None,
        "--policy-config",
        help="Policy/checkpoint JSON contract; overrides the built-in profile",
    ),
    input_format: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help="Input format: auto, video, or lerobot",
    ),
    integrity: str = typer.Option(
        "full",
        "--integrity",
        help="Validation coverage: metadata, sample, or full",
    ),
    markdown: Optional[str] = typer.Option(
        None,
        "--markdown",
        help="Optional deterministic Markdown projection path",
    ),
    allow_partial: bool = typer.Option(
        False,
        "--allow-partial",
        help="Exit 0 for a completed PARTIAL result",
    ),
    follow_symlinks: bool = typer.Option(
        False,
        "--follow-symlinks",
        help="Allow media symlinks that resolve inside the dataset root",
    ),
    hub_max_bytes: int = typer.Option(
        2_000_000_000,
        "--hub-max-bytes",
        help="Hard Hub download byte budget",
    ),
    hub_max_shards: int = typer.Option(
        12,
        "--hub-max-shards",
        help="Hard Hub payload-shard budget",
    ),
    hub_max_episodes: int = typer.Option(
        64,
        "--hub-max-episodes",
        help="Hard Hub episode-selection budget",
    ),
    hub_max_media_shards: int = typer.Option(
        9,
        "--hub-max-media-shards",
        help="Hard Hub media-shard budget",
    ),
):
    """Evaluate a dataset against a declared training or publication profile."""
    try:
        if dataset_dir.startswith("hf://"):
            result = evaluate_hub_dataset_readiness(
                dataset_dir,
                profile=profile,
                policy_config=policy_config,
                integrity=integrity,
                follow_symlinks=follow_symlinks,
                budget=HubDownloadBudget(
                    max_bytes=hub_max_bytes,
                    max_shards=hub_max_shards,
                    max_episodes=hub_max_episodes,
                    max_media_shards=hub_max_media_shards,
                ),
                output_path=output,
            )
        else:
            result = evaluate_dataset_readiness(
                dataset_dir,
                profile=profile,
                policy_config=policy_config,
                input_format=input_format,
                integrity=integrity,
                follow_symlinks=follow_symlinks,
                output_path=output,
            )
        if markdown is not None:
            write_text_atomic(
                Path(markdown),
                render_readiness_markdown(result),
            )
    except (OpenBotDataError, HubSourceError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Readiness saved to {output}")
    typer.echo(f"  profile: {result['profile']['id']}")
    typer.echo(f"  status: {result['status']}")
    if result["status"] == "BLOCKED" or (
        result["status"] == "PARTIAL" and not allow_partial
    ):
        raise typer.Exit(2)


@repair_app.command("plan")
def repair_plan_command(
    dataset_dir: str = typer.Argument(..., help="Local LeRobot dataset"),
    output: str = typer.Option(..., "--out", "-o", help="Repair plan JSON path"),
    input_format: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help="Input format: auto or lerobot",
    ),
    integrity: str = typer.Option(
        "metadata",
        "--integrity",
        help="Planning coverage: metadata, sample, or full",
    ),
    follow_symlinks: bool = typer.Option(
        False,
        "--follow-symlinks",
        help="Allow in-root dataset symlinks",
    ),
):
    """Write a read-only plan for uniquely derived metadata repairs."""
    try:
        result = plan_dataset_repair(
            dataset_dir,
            input_format=input_format,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
            output_path=output,
        )
    except (OpenBotDataError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Repair plan saved to {output}")
    typer.echo(f"  status: {result['status']}")
    typer.echo(f"  automatic steps: {len(result['steps'])}")
    typer.echo(f"  plan fingerprint: {result['plan_fingerprint']}")


@repair_app.command("apply")
def repair_apply_command(
    dataset_dir: str = typer.Argument(..., help="Source LeRobot dataset"),
    plan: str = typer.Option(..., "--plan", help="Approved repair plan JSON"),
    output: str = typer.Option(
        ...,
        "--output",
        "-o",
        help="Distinct copy-on-write output dataset",
    ),
    receipt: Optional[str] = typer.Option(
        None,
        "--receipt",
        help="Repair receipt JSON path",
    ),
):
    """Apply only allowlisted automatic steps to a new dataset."""
    receipt_path = receipt or f"{output}.repair-receipt.json"
    try:
        result = apply_dataset_repair(
            dataset_dir,
            plan,
            output_path=output,
            loader_runner=smoke_test_lerobot_dataset,
        )
        write_json_atomic(Path(receipt_path), result)
    except (OpenBotDataError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Repaired dataset written to {output}")
    typer.echo(f"Repair receipt saved to {receipt_path}")
    typer.echo(f"  status: {result['status']}")
    if result["status"] != "verified":
        raise typer.Exit(2)


@app.command("verify")
def verify_repair_command(
    dataset_dir: str = typer.Argument(..., help="Repaired LeRobot dataset"),
    against: str = typer.Option(
        ...,
        "--against",
        help="Repair plan JSON used to create this dataset",
    ),
    output: str = typer.Option(..., "--out", "-o", help="Receipt JSON path"),
):
    """Re-audit a repaired dataset against its approved plan."""
    try:
        result = verify_dataset_repair(
            dataset_dir,
            against=against,
            loader_runner=smoke_test_lerobot_dataset,
            output_path=output,
        )
    except (OpenBotDataError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Repair verification saved to {output}")
    typer.echo(f"  status: {result['status']}")
    if result["status"] != "verified":
        raise typer.Exit(2)


@app.command("merge-check")
def merge_check_command(
    inputs: list[str] = typer.Argument(
        ...,
        help="Two or more datasets or snapshot JSON files",
    ),
    output: str = typer.Option(..., "--out", "-o", help="Merge plan JSON path"),
    profile: str = typer.Option(
        "lerobot-act",
        "--profile",
        help="Readiness/profile contract for compatibility checks",
    ),
    task_remap: Optional[str] = typer.Option(
        None,
        "--task-remap",
        help="Optional JSON object mapping input task names",
    ),
):
    """Check merge compatibility without executing a physical merge."""
    try:
        remap = (
            _read_json_object(task_remap, "Task remap")
            if task_remap is not None
            else None
        )
        result = check_merge_compatibility(
            inputs,
            profile=profile,
            task_remap=remap,
            output_path=output,
        )
    except (OpenBotDataError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Merge plan saved to {output}")
    typer.echo(f"  decision: {result['decision']}")
    typer.echo(f"  plan fingerprint: {result['plan_fingerprint']}")
    if result["decision"] != "direct":
        raise typer.Exit(2)


@app.command("verify-merge")
def verify_merge_command(
    merged: str = typer.Argument(
        ...,
        help="Merged dataset or full snapshot JSON",
    ),
    input_snapshots: list[str] = typer.Option(
        ...,
        "--input",
        "--inputs",
        help="Input snapshot JSON; repeat for every merge input",
    ),
    output: str = typer.Option(..., "--out", "-o", help="Merge receipt JSON"),
    profile: str = typer.Option(
        "lerobot-act",
        "--profile",
        help="Compatibility profile used for the merge",
    ),
    operation_record: Optional[str] = typer.Option(
        None,
        "--operation-record",
        help="JSON record produced by the pinned official merge operation",
    ),
):
    """Verify a completed official merge; this command never merges data."""
    try:
        record = (
            _read_json_object(operation_record, "Operation record")
            if operation_record is not None
            else None
        )
        result = verify_dataset_merge(
            merged,
            input_snapshots=input_snapshots,
            profile=profile,
            operation_record=record,
            loader_runner=smoke_test_lerobot_dataset,
            output_path=output,
        )
    except (OpenBotDataError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Merge verification saved to {output}")
    typer.echo(f"  status: {result['verification_status']}")
    if result["verification_status"] != "verified":
        raise typer.Exit(2)


@app.command()
def version():
    """Show version information."""
    from openbot_data import __version__
    typer.echo(f"OpenBot Data v{__version__}")
    typer.echo("https://github.com/openbotai/openbot-data")


def main():
    app()


if __name__ == "__main__":
    main()
