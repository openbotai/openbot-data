import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from typer.testing import CliRunner

from openbot_data.cli import app
from openbot_data.extract import inspect_dataset
from openbot_data.preflight import (
    audit_dataset,
    dataset_fingerprint,
    detect_input_format,
    read_lerobot,
)


def make_video(path: Path, frames: int = 4) -> None:
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
        frame = np.full((16, 16, 3), frame_index * 30, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def make_lerobot(root: Path, codebase_version: str, shared_video: bool = False) -> None:
    meta = root / "meta"
    meta.mkdir(parents=True)
    (meta / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": codebase_version,
                "total_episodes": 1,
                "video_path": (
                    "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.avi"
                    if shared_video
                    else None
                ),
                "features": {"observation.images.top": {"dtype": "video"}},
            }
        )
    )
    episode = {"episode_index": 0, "length": 4, "tasks": ["pick"]}
    if shared_video:
        episode.update(
            {
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 0.8,
            }
        )
    (meta / "episodes.jsonl").write_text(json.dumps(episode) + "\n")
    if shared_video:
        video = root / "videos" / "observation.images.top" / "chunk-000" / "file-000.avi"
    else:
        video = (
            root
            / "videos"
            / "chunk-000"
            / "observation.images.top"
            / "episode_000000.avi"
        )
    make_video(video)


@pytest.mark.parametrize(
    ("codebase_version", "shared_video"),
    [("v2.1", False), ("v3.0", True)],
)
def test_read_lerobot_discovers_v2_and_v3_layouts(
    tmp_path: Path,
    codebase_version: str,
    shared_video: bool,
) -> None:
    root = tmp_path / "dataset"
    make_lerobot(root, codebase_version, shared_video)

    result = read_lerobot(str(root))

    assert detect_input_format(str(root)) == "lerobot"
    assert result["codebase_version"] == codebase_version
    assert result["video_keys"] == ["observation.images.top"]
    assert result["episodes"][0]["episode_index"] == 0
    assert result["videos"]
    assert not [finding for finding in result["findings"] if finding["severity"] == "error"]


def test_inspection_manifest_is_reproducible_and_uses_relative_paths(tmp_path: Path) -> None:
    source = tmp_path / "videos"
    output = tmp_path / "inspection"
    make_video(source / "camera" / "episode.avi")

    first = inspect_dataset(str(source), str(output), input_format="auto", checksum="sha256")
    first_bytes = Path(first["manifest_path"]).read_bytes()
    second = inspect_dataset(str(source), str(output), input_format="auto", checksum="sha256")
    second_bytes = Path(second["manifest_path"]).read_bytes()
    manifest = json.loads(first_bytes)

    assert first_bytes == second_bytes
    assert str(tmp_path) not in first_bytes.decode()
    assert manifest["dataset_fingerprint"] == second["dataset_fingerprint"]
    assert manifest["videos"][0]["path"] == "camera/episode.avi"
    assert manifest["videos"][0]["checksum_sha256"]
    assert not Path(manifest["videos"][0]["path"]).is_absolute()
    assert not Path(manifest["videos"][0]["previews"][0]["path"]).is_absolute()


def test_audit_detects_duplicate_content_only_with_sha256(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    first = root / "camera" / "first.avi"
    second = root / "camera" / "second.avi"
    make_video(first)
    second.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(first, second)

    without_checksum = audit_dataset(str(root))
    with_checksum = audit_dataset(str(root), checksum="sha256")

    assert "DUPLICATE_CONTENT" not in {
        finding["code"] for finding in without_checksum["findings"]
    }
    assert "DUPLICATE_CONTENT" in {
        finding["code"] for finding in with_checksum["findings"]
    }
    assert "score" not in with_checksum


def test_audit_cli_writes_structured_error_and_honors_fail_on(tmp_path: Path) -> None:
    output = tmp_path / "audit.json"
    result = CliRunner().invoke(
        app,
        [
            "audit",
            str(tmp_path / "missing"),
            "--out",
            str(output),
            "--fail-on",
            "error",
        ],
    )

    assert result.exit_code == 2
    payload = json.loads(output.read_text())
    assert payload["schema_version"] == "openbot.dataset_audit.v1"
    assert payload["findings"][0]["code"] == "DATASET_NOT_FOUND"


def test_documented_manifest_example_has_valid_fingerprint_and_relative_paths() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "docs" / "examples" / "manifest.json").read_text())
    payload = {
        "schema_version": manifest["schema_version"],
        "input_format": manifest["input_format"],
        "codebase_version": manifest["codebase_version"],
        "episodes": manifest["episodes"],
        "video_keys": manifest["video_keys"],
        "videos": [
            {
                key: value
                for key, value in video.items()
                if key
                not in {"previews", "preview_status", "preview_error", "preview_failures"}
            }
            for video in manifest["videos"]
        ],
    }

    assert manifest["dataset_fingerprint"] == dataset_fingerprint(payload)
    assert not Path(manifest["videos"][0]["path"]).is_absolute()
