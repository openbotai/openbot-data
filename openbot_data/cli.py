"""
Command-line interface for OpenBot Data v0.0.1.
"""

import typer
from typing import Optional
from pathlib import Path

from openbot_data.video import scan_directory
from openbot_data.extract import inspect_dataset
from openbot_data.catalog import export_catalog, SUPPORTED_FORMATS

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
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        typer.echo(f"Scan results saved to {output_path}")
    else:
        typer.echo(json.dumps(result, indent=2))


@app.command()
def inspect(
    video_dir: str = typer.Argument(..., help="Path to video directory"),
    output: str = typer.Option(..., "--out", "-o", help="Output directory"),
):
    """
    Inspect robot videos: extract preview frames and generate manifest.
    """
    typer.echo(f"Inspecting robot videos in {video_dir}...")

    result = inspect_dataset(
        video_dir=video_dir,
        output_dir=output
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
    import json

    result = export_catalog(video_dir=video_dir, output_path=output, fmt=fmt)

    if "error" in result:
        typer.echo(f"Error: {result['error']}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Catalog exported to {result['output_path']}")
    typer.echo(f"  format: {result['format']}")
    typer.echo(f"  videos: {result['total_videos']}")


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
