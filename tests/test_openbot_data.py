import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

import openbot_data
from openbot_data.cli import app
from openbot_data.extract import extract_preview_frames, inspect_dataset
from openbot_data.video import scan_directory


def make_video(path: Path, frames: int = 6) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        5.0,
        (16, 16),
    )
    if not writer.isOpened():
        pytest.skip("OpenCV video writer is unavailable in this environment")

    for frame_index in range(frames):
        frame = np.full((16, 16, 3), frame_index * 20, dtype=np.uint8)
        writer.write(frame)

    writer.release()


def test_public_api_exports_expected_functions() -> None:
    assert openbot_data.__version__ == "0.0.1.post2"
    assert set(openbot_data.__all__) == {
        "extract_preview_frames",
        "inspect_dataset",
        "scan_directory",
        "scan_video",
    }


def test_scan_directory_reports_missing_directory(tmp_path: Path) -> None:
    result = scan_directory(str(tmp_path / "missing"))

    assert "error" in result
    assert result["videos"] == []


def test_inspect_dataset_generates_manifest_report_and_previews(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    output_dir = tmp_path / "dataset"
    make_video(video_dir / "clip.avi")

    result = inspect_dataset(str(video_dir), str(output_dir))

    manifest_path = Path(result["manifest_path"])
    report_path = Path(result["report_path"])
    assert manifest_path.exists()
    assert report_path.exists()

    manifest = json.loads(manifest_path.read_text())
    report = json.loads(report_path.read_text())

    assert manifest["valid_videos"] == 1
    assert manifest["total_previews"] > 0
    assert report["resolutions"] == [[16, 16]]
    assert all(Path(frame["path"]).exists() for frame in manifest["videos"][0]["previews"])


def test_preview_frame_names_do_not_collide_for_same_basename(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    output_dir = tmp_path / "dataset"
    make_video(video_dir / "left" / "clip.avi")
    make_video(video_dir / "right" / "clip.avi")

    result = inspect_dataset(str(video_dir), str(output_dir))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    preview_paths = [
        frame["path"]
        for video in manifest["videos"]
        for frame in video["previews"]
    ]

    assert len(preview_paths) == len(set(preview_paths))


def test_cli_version_and_inspect_command(tmp_path: Path) -> None:
    runner = CliRunner()
    version_result = runner.invoke(app, ["version"])

    assert version_result.exit_code == 0
    assert "OpenBot Data v0.0.1.post2" in version_result.output

    video_dir = tmp_path / "videos"
    output_dir = tmp_path / "dataset"
    make_video(video_dir / "clip.avi")
    inspect_result = runner.invoke(app, ["inspect", str(video_dir), "--out", str(output_dir)])

    assert inspect_result.exit_code == 0
    assert "Inspection complete!" in inspect_result.output
    assert (output_dir / "metadata" / "manifest.json").exists()
