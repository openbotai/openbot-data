import csv
import json
from importlib.metadata import version
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

import openbot_data
from openbot_data.catalog import export_catalog
from openbot_data.cli import app
from openbot_data.extract import (
    build_contact_sheets,
    extract_preview_frames,
    extract_timestamped_frames,
    inspect_dataset,
)
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
        frame = np.full((16, 16, 3), (frame_index * 20) % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_public_api_exports_expected_functions() -> None:
    assert openbot_data.__version__ == version("openbot-data")
    assert set(openbot_data.__all__) == {
        "extract_preview_frames",
        "extract_timestamped_frames",
        "build_contact_sheets",
        "inspect_dataset",
        "scan_directory",
        "scan_video",
        "export_catalog",
    }


def test_scan_directory_reports_missing_directory(tmp_path: Path) -> None:
    result = scan_directory(str(tmp_path / "missing"))
    assert "error" in result
    assert result["videos"] == []


def test_cli_scan_failure_exits_nonzero(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["scan", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert "Directory not found" in result.output


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


def test_preview_seek_failure_is_reported_and_capture_is_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingSeekCapture:
        released = False

        def isOpened(self) -> bool:
            return True

        def get(self, prop: int) -> float:
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 4
            if prop == cv2.CAP_PROP_FPS:
                return 5
            return 0

        def set(self, _prop: int, _value: float) -> bool:
            return False

        def read(self):
            raise AssertionError("read must not run after a failed seek")

        def release(self) -> None:
            self.released = True

    capture = FailingSeekCapture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _path: capture)

    result = extract_preview_frames("fixture.avi", str(tmp_path), max_frames=2)

    assert result["status"] == "failed"
    assert result["failed_frames"] == [
        {"frame_number": 0, "reason": "seek_failed"},
        {"frame_number": 3, "reason": "seek_failed"},
    ]
    assert capture.released is True


def test_contact_sheet_metadata_only_lists_decoded_frames(tmp_path: Path) -> None:
    valid = tmp_path / "valid.jpg"
    assert cv2.imwrite(str(valid), np.full((8, 8, 3), 64, dtype=np.uint8))
    result = build_contact_sheets(
        [
            {"frame_id": "valid", "path": str(valid)},
            {"frame_id": "missing", "path": str(tmp_path / "missing.jpg")},
        ],
        str(tmp_path / "output"),
        columns=2,
        rows=1,
        tile_width=16,
    )

    assert result["count"] == 1
    assert result["contact_sheets"][0]["frame_ids"] == ["valid"]
    assert result["contact_sheets"][0]["failed_frame_ids"] == ["missing"]


def test_timestamped_frame_failures_are_explicit_and_capture_is_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FailingTimestampCapture:
        released = False

        def isOpened(self) -> bool:
            return True

        def get(self, prop: int) -> float:
            if prop == cv2.CAP_PROP_FRAME_COUNT:
                return 5
            if prop == cv2.CAP_PROP_FPS:
                return 5
            return 0

        def set(self, _prop: int, _value: float) -> bool:
            return False

        def read(self):
            raise AssertionError("read must not run after a failed seek")

        def release(self) -> None:
            self.released = True

    capture = FailingTimestampCapture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _path: capture)

    result = extract_timestamped_frames("fixture.avi", str(tmp_path), sample_fps=2)

    assert result["status"] == "failed"
    assert result["extracted_frames"] == 0
    assert result["failed_frames"]
    assert capture.released is True


def test_cli_version_and_inspect_command(tmp_path: Path) -> None:
    runner = CliRunner()
    version_result = runner.invoke(app, ["version"])
    assert version_result.exit_code == 0
    assert f"OpenBot Data v{version('openbot-data')}" in version_result.output

    video_dir = tmp_path / "videos"
    output_dir = tmp_path / "dataset"
    make_video(video_dir / "clip.avi")
    inspect_result = runner.invoke(app, ["inspect", str(video_dir), "--out", str(output_dir)])
    assert inspect_result.exit_code == 0
    assert "Inspection complete!" in inspect_result.output
    assert (output_dir / "metadata" / "manifest.json").exists()


def test_export_catalog_json(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    output_path = tmp_path / "catalog.json"
    make_video(video_dir / "clip.avi")
    result = export_catalog(str(video_dir), str(output_path), fmt="json")

    assert "error" not in result
    catalog = json.loads(output_path.read_text())
    assert catalog["summary"]["total_videos"] == 1
    assert catalog["summary"]["valid_videos"] == 1
    assert catalog["videos"][0]["filename"] == "clip.avi"


def test_export_catalog_csv(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    output_path = tmp_path / "catalog.csv"
    make_video(video_dir / "clip.avi")
    result = export_catalog(str(video_dir), str(output_path), fmt="csv")

    assert "error" not in result
    rows = list(csv.DictReader(output_path.read_text().splitlines()))
    assert len(rows) == 1
    assert rows[0]["filename"] == "clip.avi"


def test_export_catalog_invalid_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_catalog(str(tmp_path / "videos"), str(tmp_path / "catalog.txt"), fmt="txt")


def test_cli_catalog_command(tmp_path: Path) -> None:
    runner = CliRunner()
    video_dir = tmp_path / "videos"
    output_path = tmp_path / "catalog.json"
    make_video(video_dir / "clip.avi")
    result = runner.invoke(
        app,
        ["catalog", str(video_dir), "--out", str(output_path), "--format", "json"],
    )

    assert result.exit_code == 0
    assert "Catalog exported" in result.output
    assert output_path.exists()
