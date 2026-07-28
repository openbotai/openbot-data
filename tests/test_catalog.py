import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import jsonschema
import numpy as np
import pytest

import openbot_data.catalog as catalog_module
import openbot_data.preflight as preflight_module
from openbot_data import prepare_dataset, schema_path
from openbot_data.catalog import export_catalog


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_export_catalog_legacy_call_writes_versioned_json(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    output = tmp_path / "catalog.json"
    make_video(root / "camera" / "episode.avi")

    result = export_catalog(str(root), str(output), "json")
    catalog = read_json(output)

    assert result == {
        "output_path": str(output),
        "format": "json",
        "total_videos": 1,
    }
    assert catalog["schema_version"] == "openbot.dataset_catalog.v1"
    assert catalog["input_format"] == "video"
    assert catalog["codebase_version"] is None
    assert catalog["summary"]["source_dir"] == "."
    assert catalog["summary"]["total_videos"] == 1
    assert catalog["videos"][0]["path"] == "camera/episode.avi"
    assert str(tmp_path) not in output.read_text(encoding="utf-8")

    with schema_path("catalog") as path:
        schema = read_json(path)
    jsonschema.Draft202012Validator(schema).validate(catalog)


def test_export_catalog_reuses_compatible_prepared_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "videos"
    output = tmp_path / "catalog.json"
    make_video(root / "episode.avi")
    snapshot = prepare_dataset(
        str(root),
        input_format="video",
        checksum="sha256",
        integrity="full",
    )

    def fail_prepare(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("export_catalog prepared the dataset a second time")

    monkeypatch.setattr(catalog_module, "prepare_dataset", fail_prepare)

    result = export_catalog(
        str(root),
        str(output),
        input_format="video",
        checksum="sha256",
        integrity="full",
        snapshot=snapshot,
    )

    assert "error" not in result
    assert result["total_videos"] == len(snapshot.videos)
    assert read_json(output)["videos"][0]["frame_count"] == snapshot.videos[0].frame_count


def test_export_catalog_rejects_incompatible_snapshot(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    make_video(first_root / "episode.avi")
    make_video(second_root / "episode.avi")
    snapshot = prepare_dataset(str(first_root))

    result = export_catalog(
        str(second_root),
        str(tmp_path / "catalog.json"),
        snapshot=snapshot,
    )

    assert "snapshot root does not match" in result["error"]
    assert not (tmp_path / "catalog.json").exists()


def test_export_catalog_preserves_legacy_raw_rounding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "videos"
    output = tmp_path / "catalog.json"
    make_video(root / "episode.avi")
    monkeypatch.setattr(
        preflight_module,
        "scan_video",
        lambda _path: SimpleNamespace(
            width=16,
            height=16,
            fps=7.0051,
            frame_count=9,
            duration=1.2851,
            size_mb=0.01,
            error=None,
        ),
    )

    export_catalog(
        str(root),
        str(output),
        integrity="metadata",
    )
    video = read_json(output)["videos"][0]

    assert video["fps"] == 7.01
    assert video["duration"] == 1.29
