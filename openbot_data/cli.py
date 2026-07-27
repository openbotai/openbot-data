"""
Command-line interface for OpenBot Data.
"""

from pathlib import Path
from typing import Optional

import typer

from openbot_data.catalog import SUPPORTED_FORMATS, export_catalog
from openbot_data.catalog_evidence import (
    CATALOG_EVIDENCE_PROFILE,
    build_catalog_evidence,
)
from openbot_data.errors import OpenBotDataError
from openbot_data.extract import inspect_dataset
from openbot_data.preflight import audit_dataset
from openbot_data.serialization import write_json_atomic
from openbot_data.video import scan_directory

app = typer.Typer(
    name="openbot-data",
    help="Inspect robot video datasets and generate reviewable metadata.",
    add_completion=False
)


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
):
    """Audit a local dataset with deterministic findings and exit policy."""
    normalized_fail_on = fail_on.lower()
    if normalized_fail_on not in {"none", "warning", "error"}:
        typer.echo("Error: --fail-on must be none, warning, or error", err=True)
        raise typer.Exit(1)

    result = audit_dataset(
        dataset_dir,
        input_format=input_format,
        checksum=checksum,
        output_path=output,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
    )
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
        help="Versioned audit profile identifier",
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
