from __future__ import annotations

import json
import sys
import types
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

import openbot_data.preflight as preflight
from openbot_data import prepare_dataset
from openbot_data.extract import inspect_dataset
from openbot_data.preflight import audit_dataset, read_lerobot


def make_video(
    path: Path,
    *,
    frames: int = 4,
    fps: float = 5.0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        (16, 16),
    )
    if not writer.isOpened():
        pytest.fail("OpenCV video writer is required for the P0.1 fixtures")
    for frame_index in range(frames):
        writer.write(np.full((16, 16, 3), (frame_index * 30) % 256, dtype=np.uint8))
    writer.release()


def finding_codes(result: dict[str, Any]) -> set[str]:
    return {str(finding["code"]) for finding in result["findings"]}


def write_v3_dataset(
    root: Path,
    episodes: list[dict[str, Any]],
    *,
    declared_cameras: tuple[str, ...] = ("observation.images.top",),
    video_path: object = (
        "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.avi"
    ),
) -> None:
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "total_episodes": len(episodes),
                "video_path": video_path,
                "features": {
                    camera: {"dtype": "video"} for camera in declared_cameras
                },
            }
        )
    )
    (root / "meta/episodes/chunk-000/file-000.jsonl").write_text(
        "\n".join(json.dumps(episode) for episode in episodes) + "\n"
    )


@pytest.mark.parametrize(
    ("frame_count", "expected"),
    [
        (1, [0]),
        (2, [0, 1]),
        (3, [0, 1, 2]),
        (9, [0, 4, 8]),
        (10, [0, 4, 9]),
    ],
)
def test_sample_probe_uses_deduplicated_deterministic_positions(
    monkeypatch: pytest.MonkeyPatch,
    frame_count: int,
    expected: list[int],
) -> None:
    class Capture:
        positions: list[int] = []

        def isOpened(self) -> bool:
            return True

        def set(self, _prop: int, value: float) -> bool:
            self.positions.append(int(value))
            return True

        def read(self) -> tuple[bool, object]:
            return True, object()

        def release(self) -> None:
            return None

    capture = Capture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _path: capture)

    valid, decoded = preflight._decode_probe(Path("video.avi"), "sample", frame_count)

    assert valid is True
    assert decoded == len(expected)
    assert capture.positions == expected


@pytest.mark.parametrize(("seek_failure", "decode_failure"), [(4, None), (None, 4)])
def test_sample_probe_collects_all_positions_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
    seek_failure: int | None,
    decode_failure: int | None,
) -> None:
    class Capture:
        position = 0
        positions: list[int] = []

        def isOpened(self) -> bool:
            return True

        def set(self, _prop: int, value: float) -> bool:
            self.position = int(value)
            self.positions.append(self.position)
            return self.position != seek_failure

        def read(self) -> tuple[bool, object | None]:
            return self.position != decode_failure, None

        def release(self) -> None:
            return None

    capture = Capture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _path: capture)

    valid, decoded = preflight._decode_probe(Path("video.avi"), "sample", 10)

    assert valid is False
    assert decoded == 2
    assert capture.positions == [0, 4, 9]


def test_sample_probe_preserves_manifest_v1_decoded_count_projection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "videos"
    make_video(root / "camera/episode.avi", frames=10)

    snapshot = prepare_dataset(str(root), integrity="sample")

    assert snapshot.videos[0].decode_valid is True
    assert snapshot.videos[0].decoded_frame_count == 1


def test_start_middle_end_probe_preserves_clean_manifest_v1_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "videos"
    make_video(root / "camera/episode.avi", frames=10)
    original_probe = preflight._decode_probe
    monkeypatch.setattr(
        preflight,
        "_decode_probe",
        lambda _path, integrity, _frames: (
            (None, None) if integrity == "metadata" else (True, 1)
        ),
    )
    baseline = inspect_dataset(str(root), str(tmp_path / "baseline"))
    baseline_bytes = Path(baseline["manifest_path"]).read_bytes()

    monkeypatch.setattr(preflight, "_decode_probe", original_probe)
    candidate = inspect_dataset(str(root), str(tmp_path / "candidate"))

    assert baseline_bytes == Path(candidate["manifest_path"]).read_bytes()
    assert baseline["dataset_fingerprint"] == candidate["dataset_fingerprint"]


def test_malformed_lerobot_fields_collect_structured_findings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    write_v3_dataset(
        root,
        [
            {
                "episode_index": 0,
                "length": "4",
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": "bad",
                "videos/observation.images.top/to_timestamp": 0.8,
            },
            {
                "episode_index": 1,
                "length": 4,
                "videos/observation.images.top/chunk_index": "x",
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 0.8,
            },
            {
                "episode_index": 2,
                "length": 4,
                "videos": {
                    "observation.images.top": {
                        "path": "../escape.avi",
                        "from_timestamp": 0.0,
                        "to_timestamp": 0.8,
                    }
                },
            },
            {
                "episode_index": 2,
                "length": 4,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
            },
        ],
    )
    make_video(root / "videos/observation.images.top/chunk-000/file-000.avi")

    result = read_lerobot(str(root))

    assert {
        "LEROBOT_EPISODE_LENGTH_INVALID",
        "LEROBOT_EPISODE_INDEX_DUPLICATE",
        "LEROBOT_VIDEO_RELATION_INVALID",
        "LEROBOT_VIDEO_PATH_INVALID",
        "LEROBOT_VIDEO_SEGMENT_BOUNDS_INVALID",
    } <= finding_codes(result)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "video_path",
    [
        42,
        "videos/{video_key}/file-{file_index:03d}.avi",
        "videos/{video_key}/{unknown}.avi",
        "/videos/{video_key}/chunk-{chunk_index}/file-{file_index}.avi",
        (
            "videos/{video_key:{video_key.foo}}/"
            "chunk-{chunk_index}/file-{file_index}.avi"
        ),
    ],
)
def test_invalid_v3_path_template_is_structured(
    tmp_path: Path,
    video_path: object,
) -> None:
    root = tmp_path / "dataset"
    write_v3_dataset(
        root,
        [
            {
                "episode_index": 0,
                "length": 4,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 0.8,
            }
        ],
        video_path=video_path,
    )

    assert "LEROBOT_VIDEO_PATH_TEMPLATE_INVALID" in finding_codes(
        read_lerobot(str(root))
    )


def test_v3_collects_relation_timestamp_and_path_failures_independently(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    write_v3_dataset(
        root,
        [
            {
                "episode_index": 0,
                "length": 4,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 9,
                "videos/observation.images.top/from_timestamp": "bad",
                "videos/observation.images.top/to_timestamp": 0.8,
            },
            {
                "episode_index": 1,
                "length": 4,
                "videos": {
                    "observation.images.top": {
                        "path": "../escape.avi",
                        "from_timestamp": "bad",
                        "to_timestamp": 0.8,
                    }
                },
            },
            {
                "episode_index": 2,
                "length": 4,
                "videos": {
                    "observation.images.top": {
                        "path": "videos/custom/top.avi",
                        "chunk_index": "bad",
                        "file_index": -1,
                        "from_timestamp": 0.0,
                        "to_timestamp": 0.8,
                    }
                },
            },
            {
                "episode_index": 3,
                "length": 4,
                "videos": {"observation.images.top": 42},
            },
        ],
    )
    make_video(root / "videos/custom/top.avi")

    findings = read_lerobot(str(root))["findings"]
    by_episode = {
        episode_index: {
            finding["code"]
            for finding in findings
            if finding.get("evidence", {}).get("episode_index") == episode_index
        }
        for episode_index in range(4)
    }

    assert {
        "LEROBOT_VIDEO_MISSING",
        "LEROBOT_VIDEO_SEGMENT_BOUNDS_INVALID",
    } <= by_episode[0]
    assert {
        "LEROBOT_VIDEO_PATH_INVALID",
        "LEROBOT_VIDEO_SEGMENT_BOUNDS_INVALID",
    } <= by_episode[1]
    assert "LEROBOT_VIDEO_RELATION_INVALID" in by_episode[2]
    assert "LEROBOT_VIDEO_PATH_INVALID" in by_episode[3]


def test_v3_without_declared_video_features_allows_null_video_path(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    (root / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "total_episodes": 0,
                "video_path": None,
                "features": {
                    "observation.state": {
                        "dtype": "float32",
                        "shape": [6],
                    }
                },
            }
        )
    )
    (root / "meta/episodes.jsonl").write_text("")

    result = read_lerobot(str(root))

    assert "LEROBOT_VIDEO_PATH_TEMPLATE_INVALID" not in finding_codes(result)


def test_default_symlink_policy_reports_skip_without_escape_error(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    make_video(root / "real.avi")
    (root / "alias.avi").symlink_to("real.avi")

    default_result = audit_dataset(str(root))
    followed_result = audit_dataset(str(root), follow_symlinks=True)

    assert "DATASET_SYMLINK_SKIPPED" in finding_codes(default_result)
    assert "DATASET_PATH_OUTSIDE_ROOT" not in finding_codes(default_result)
    assert "DATASET_SYMLINK_SKIPPED" not in finding_codes(followed_result)
    assert followed_result["summary"]["videos"] == 2


def test_default_policy_skips_outside_symlink_and_opt_in_rejects_it(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.avi"
    make_video(outside)
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "escape.avi").symlink_to(outside)

    default_result = audit_dataset(str(root))
    followed_result = audit_dataset(str(root), follow_symlinks=True)

    assert "DATASET_SYMLINK_SKIPPED" in finding_codes(default_result)
    assert "DATASET_PATH_OUTSIDE_ROOT" not in finding_codes(default_result)
    assert "DATASET_PATH_OUTSIDE_ROOT" in finding_codes(followed_result)


def test_broken_opt_in_symlink_is_structured(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "broken.avi").symlink_to("missing.avi")

    result = audit_dataset(str(root), follow_symlinks=True)

    assert "DATASET_SYMLINK_BROKEN" in finding_codes(result)


def test_opted_in_symlink_cycle_is_structured(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    try:
        (root / "a.avi").symlink_to("b.avi")
        (root / "b.avi").symlink_to("a.avi")
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    result = audit_dataset(str(root), follow_symlinks=True)

    assert "DATASET_SYMLINK_BROKEN" in finding_codes(result)


def test_v2_checks_every_declared_camera_per_episode(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    (root / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v2.1",
                "total_episodes": 1,
                "features": {
                    "observation.images.top": {"dtype": "video"},
                    "observation.images.side": {"dtype": "video"},
                },
            }
        )
    )
    (root / "meta/episodes.jsonl").write_text(
        json.dumps({"episode_index": 0, "length": 4}) + "\n"
    )
    make_video(
        root / "videos/chunk-000/observation.images.top/episode_000000.avi"
    )

    result = read_lerobot(str(root))
    missing = [
        finding
        for finding in result["findings"]
        if finding["code"] == "LEROBOT_VIDEO_MISSING"
    ]

    assert len(missing) == 1
    assert missing[0]["evidence"]["video_key"] == "observation.images.side"


def test_v2_explicit_camera_path_satisfies_declared_camera_coverage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    (root / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v2.1",
                "total_episodes": 1,
                "features": {"observation.images.top": {"dtype": "video"}},
            }
        )
    )
    (root / "meta/episodes.jsonl").write_text(
        json.dumps(
            {
                "episode_index": 0,
                "length": 4,
                "videos": {
                    "observation.images.top": "videos/custom/top0.avi",
                },
            }
        )
        + "\n"
    )
    make_video(root / "videos/custom/top0.avi")

    result = read_lerobot(str(root))

    assert result["episodes"][0]["video_files"] == ["videos/custom/top0.avi"]
    assert "LEROBOT_VIDEO_MISSING" not in finding_codes(result)


def test_v3_checks_declared_cameras_but_ignores_stray_streams(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    write_v3_dataset(
        root,
        [
            {
                "episode_index": 0,
                "length": 4,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 0.8,
            }
        ],
    )
    make_video(root / "videos/observation.images.top/chunk-000/file-000.avi")
    make_video(root / "videos/observation.images.stray/chunk-000/file-000.avi")

    result = read_lerobot(str(root))

    assert "observation.images.stray" in result["video_keys"]
    assert "LEROBOT_VIDEO_RELATION_MISSING" not in finding_codes(result)


def test_v3_missing_declared_camera_relation_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    write_v3_dataset(
        root,
        [
            {
                "episode_index": 0,
                "length": 4,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": 0.0,
                "videos/observation.images.top/to_timestamp": 0.8,
            }
        ],
        declared_cameras=("observation.images.top", "observation.images.side"),
    )
    make_video(root / "videos/observation.images.top/chunk-000/file-000.avi")

    missing = [
        finding
        for finding in read_lerobot(str(root))["findings"]
        if finding["code"] == "LEROBOT_VIDEO_RELATION_MISSING"
    ]

    assert len(missing) == 1
    assert missing[0]["evidence"]["video_key"] == "observation.images.side"


def test_shared_video_segments_detect_overlap_and_duration_overflow(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    episodes = []
    for episode_index, bounds in enumerate(((0.0, 0.8), (0.7, 1.2), (1.2, 2.0))):
        episodes.append(
            {
                "episode_index": episode_index,
                "length": 4,
                "videos/observation.images.top/chunk_index": 0,
                "videos/observation.images.top/file_index": 0,
                "videos/observation.images.top/from_timestamp": bounds[0],
                "videos/observation.images.top/to_timestamp": bounds[1],
            }
        )
    write_v3_dataset(root, episodes)
    make_video(
        root / "videos/observation.images.top/chunk-000/file-000.avi",
        frames=8,
        fps=5.0,
    )

    result = read_lerobot(str(root))
    overlap = [
        finding
        for finding in result["findings"]
        if finding["code"] == "LEROBOT_VIDEO_SEGMENT_OVERLAP"
    ]
    overflow = [
        finding
        for finding in result["findings"]
        if finding["code"] == "LEROBOT_VIDEO_SEGMENT_OUT_OF_RANGE"
    ]

    assert len(overlap) == 1
    assert len(overflow) == 1
    assert overflow[0]["evidence"]["episode_index"] == 2


def test_snapshot_coverage_lattice_and_root_alias(
    tmp_path: Path,
) -> None:
    root = tmp_path / "videos"
    make_video(root / "camera/episode.avi")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    snapshot = prepare_dataset(str(root), checksum="sha256", integrity="full")

    assert audit_dataset(str(alias), snapshot=snapshot)["summary"]["videos"] == 1
    assert audit_dataset(
        str(root),
        checksum="sha256",
        integrity="full",
        snapshot=snapshot,
    )["summary"]["videos"] == 1
    assert audit_dataset(
        str(root),
        integrity="metadata",
        snapshot=snapshot,
    )["summary"]["videos"] == 1

    invalid_snapshot = replace(snapshot, integrity="unknown")
    assert finding_codes(audit_dataset(str(root), snapshot=invalid_snapshot)) == {
        "DATASET_INVALID_ARGUMENT"
    }


def test_snapshot_validation_rejects_forged_coverage_labels(
    tmp_path: Path,
) -> None:
    root = tmp_path / "videos"
    make_video(root / "camera/episode.avi")
    weak = prepare_dataset(str(root), checksum=None, integrity="metadata")
    forged = replace(weak, checksum="sha256", integrity="full")

    result = audit_dataset(
        str(root),
        checksum="sha256",
        integrity="full",
        snapshot=forged,
    )

    assert finding_codes(result) == {"DATASET_INVALID_ARGUMENT"}
    assert "snapshot video coverage" in result["findings"][0]["message"]


def test_snapshot_mismatch_does_not_create_inspection_output(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    other = tmp_path / "other"
    output = tmp_path / "inspection"
    make_video(root / "camera/episode.avi")
    make_video(other / "camera/episode.avi")
    snapshot = prepare_dataset(str(root))

    result = inspect_dataset(str(other), str(output), snapshot=snapshot)

    assert "snapshot root" in result["error"]
    assert not output.exists()


def test_episode_parquet_reader_uses_bounded_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    parquet_path = root / "meta/episodes/chunk-000/file-000.parquet"
    parquet_path.parent.mkdir(parents=True)
    parquet_path.write_bytes(b"fixture")
    batch_sizes: list[int] = []

    class Batch:
        def to_pylist(self) -> list[dict[str, int]]:
            return [{"episode_index": 0}]

    class ParquetFile:
        def __init__(self, _path: Path) -> None:
            pass

        def iter_batches(self, *, batch_size: int) -> list[Batch]:
            batch_sizes.append(batch_size)
            return [Batch()]

    pyarrow_module = types.ModuleType("pyarrow")
    parquet_module = types.ModuleType("pyarrow.parquet")
    parquet_module.ParquetFile = ParquetFile  # type: ignore[attr-defined]
    pyarrow_module.parquet = parquet_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyarrow", pyarrow_module)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", parquet_module)
    findings: list[dict[str, Any]] = []

    records = preflight._read_episode_parquet([parquet_path], root, findings)

    assert records == [{"episode_index": 0}]
    assert batch_sizes == [1024]
    assert findings == []
