from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from openbot_data import schema_path
from openbot_data.errors import DatasetArgumentError
from openbot_data.snapshot import build_dataset_snapshot


def _write_v30_dataset(root: Path) -> None:
    pa = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip(
        "pyarrow.parquet",
        exc_type=ImportError,
    )
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "data/chunk-000").mkdir(parents=True)
    (root / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "fps": 10,
                "total_episodes": 2,
                "total_frames": 2,
                "total_tasks": 1,
                "data_path": (
                    "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
                ),
                "video_path": None,
                "features": {
                    "action": {"dtype": "float32", "shape": [1]},
                    "observation.state": {
                        "dtype": "float32",
                        "shape": [1],
                        "names": None,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    parquet.write_table(
        pa.table(
            {
                "task_index": [7],
                "task": ["pick"],
                "task_extension": ["preserved"],
            }
        ),
        root / "meta/tasks.parquet",
    )
    # Source order intentionally differs from normalized episode-index order.
    parquet.write_table(
        pa.table(
            {
                "episode_index": [2, 0],
                "length": [1, 1],
                "tasks": [["pick"], ["pick"]],
                "data/chunk_index": [0, 0],
                "data/file_index": [0, 0],
                "dataset_from_index": [1, 0],
                "dataset_to_index": [2, 1],
                "episode_extension": ["source-first", "source-second"],
            }
        ),
        root / "meta/episodes/chunk-000/file-000.parquet",
    )
    parquet.write_table(
        pa.table(
            {
                "index": [0, 1],
                "episode_index": [0, 2],
                "frame_index": [0, 0],
                "timestamp": [0.0, 0.0],
                "task_index": [7, 7],
                "action": pa.array(
                    [[0.0], [1.0]],
                    type=pa.list_(pa.float32(), 1),
                ),
                "observation.state": pa.array(
                    [[1.0], [2.0]],
                    type=pa.list_(pa.float32(), 1),
                ),
            }
        ),
        root / "data/chunk-000/file-000.parquet",
    )


def _validate_snapshot(value: dict[str, Any]) -> None:
    with schema_path("snapshot") as path:
        schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(value)


def test_v30_snapshot_preserves_contract_and_uses_data_rows_for_totals(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    _write_v30_dataset(root)

    result = build_dataset_snapshot(
        str(root),
        input_format="lerobot",
        integrity="full",
        checksum="sha256",
    )

    _validate_snapshot(result)
    assert result["totals"]["frames"] == 2
    assert result["totals"]["duration_seconds"] == pytest.approx(0.2)
    assert result["contract"]["tasks"] == [
        {
            "task_index": 7,
            "task": "pick",
            "source": {"path": "meta/tasks.parquet", "row": 1},
            "extensions": {"task_extension": "preserved"},
        }
    ]
    episodes = result["contract"]["episodes"]
    assert [item["episode_index"] for item in episodes] == [0, 2]
    assert [item["source_ordinal"] for item in episodes] == [1, 0]
    assert episodes[0]["source"] == {
        "path": "meta/episodes/chunk-000/file-000.parquet",
        "row": 2,
    }
    assert episodes[0]["data_relation"] == {
        "path": "data/chunk-000/file-000.parquet",
        "dataset_from_index": 0,
        "dataset_to_index": 1,
    }
    assert episodes[0]["extensions"] == {
        "episode_extension": "source-second"
    }
    assert episodes[1]["extensions"] == {
        "episode_extension": "source-first"
    }
    assert all(item["content_rows"] == 1 for item in episodes)
    assert all(
        isinstance(item["content_sha256"], str)
        and len(item["content_sha256"]) == 64
        for item in episodes
    )
    assert "totals" in result["component_fingerprints"]


def test_hub_snapshot_requires_immutable_resolved_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    _write_v30_dataset(root)

    with pytest.raises(
        DatasetArgumentError,
        match="immutable 40-character Hub commit",
    ):
        build_dataset_snapshot(
            str(root),
            input_format="lerobot",
            source_kind="hf_hub",
            source_locator="hf://datasets/org/name@main",
            requested_revision="main",
            resolved_revision="main",
        )


@pytest.mark.parametrize(
    "source_locator",
    [
        "https://user:secret@example.com/dataset",
        (
            "https://example.com/datasets/"
            "hf_1234567890abcdefghijklmnop"
        ),
        "https://example.com/dataset%20Bearer%20secret-value",
    ],
)
def test_snapshot_rejects_secret_bearing_source_locator(
    tmp_path: Path,
    source_locator: str,
) -> None:
    root = tmp_path / "dataset"
    _write_v30_dataset(root)

    with pytest.raises(
        DatasetArgumentError,
        match="must not contain credentials",
    ):
        build_dataset_snapshot(
            str(root),
            input_format="lerobot",
            source_locator=source_locator,
        )
