from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openbot_data.preflight import audit_dataset


def _fixture() -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures/competitor-outcomes-v003.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _write_comparison_dataset(root: Path, *, total_frames: int) -> None:
    pa = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip("pyarrow.parquet", exc_type=ImportError)
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "data/chunk-000").mkdir(parents=True)
    (root / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "robot_type": "so100",
                "fps": 10,
                "total_episodes": 1,
                "total_frames": total_frames,
                "total_tasks": 1,
                "total_videos": 0,
                "total_chunks": 1,
                "chunks_size": 1000,
                "data_files_size_in_mb": 0,
                "video_files_size_in_mb": 0,
                "data_path": (
                    "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
                ),
                "video_path": None,
                "features": {
                    "action": {"dtype": "float32", "shape": [1], "names": None},
                    "observation.state": {
                        "dtype": "float32",
                        "shape": [1],
                        "names": None,
                    },
                },
            },
            indent=4,
        )
        + "\n",
        encoding="utf-8",
    )
    parquet.write_table(
        pa.table({"task_index": [0], "task": ["pick"]}),
        root / "meta/tasks.parquet",
    )
    parquet.write_table(
        pa.table(
            {
                "episode_index": [0],
                "length": [4],
                "tasks": [["pick"]],
                "dataset_from_index": [0],
                "dataset_to_index": [4],
                "data/chunk_index": [0],
                "data/file_index": [0],
            }
        ),
        root / "meta/episodes/chunk-000/file-000.parquet",
    )
    vector = pa.list_(pa.float32(), 1)
    parquet.write_table(
        pa.table(
            {
                "index": [0, 1, 2, 3],
                "episode_index": [0, 0, 0, 0],
                "frame_index": [0, 1, 2, 3],
                "timestamp": [0.0, 0.1, 0.2, 0.3],
                "task_index": [0, 0, 0, 0],
                "action": pa.array([[0.0], [1.0], [2.0], [3.0]], type=vector),
                "observation.state": pa.array(
                    [[1.0], [2.0], [3.0], [4.0]], type=vector
                ),
            }
        ),
        root / "data/chunk-000/file-000.parquet",
    )
    (root / "meta/stats.json").write_text(
        json.dumps(
            {
                "action": {
                    "min": [0.0],
                    "max": [3.0],
                    "mean": [1.5],
                    "std": [1.118033988749895],
                    "count": 4,
                },
                "observation.state": {
                    "min": [1.0],
                    "max": [4.0],
                    "mean": [2.5],
                    "std": [1.118033988749895],
                    "count": 4,
                },
            }
        ),
        encoding="utf-8",
    )


def test_pinned_competitor_fixture_records_real_outcome_parity() -> None:
    fixture = _fixture()
    tools = {item["name"]: item for item in fixture["tools"]}

    assert fixture["scenario"]["required_user_outcome"] == "block"
    assert tools["lerobot-doctor"]["install"] == "lerobot-doctor==0.2.0"
    assert tools["robovet"]["install"] == "robovet==0.2.2"
    assert tools["lerobot-doctor"]["clean"]["outcome"] == "non_blocking"
    assert tools["robovet"]["clean"]["outcome"] == "non_blocking"
    assert {item["mutated"]["outcome"] for item in tools.values()} == {"block"}


def test_openbot_candidate_preserves_the_pinned_comparison_outcome(
    tmp_path: Path,
) -> None:
    clean = tmp_path / "clean"
    stale = tmp_path / "stale"
    _write_comparison_dataset(clean, total_frames=4)
    _write_comparison_dataset(stale, total_frames=999)

    clean_audit = audit_dataset(str(clean), integrity="full")
    stale_audit = audit_dataset(str(stale), integrity="full")
    fixture = _fixture()
    expected = next(
        item for item in fixture["tools"] if item["name"] == "openbot-data"
    )

    assert clean_audit["summary"]["error"] == expected["clean"]["failures"]
    assert stale_audit["summary"]["error"] == expected["mutated"]["failures"]
    codes = {item["code"] for item in stale_audit["findings"]}
    assert set(expected["mutated"]["evidence"]) <= codes
