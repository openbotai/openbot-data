#!/usr/bin/env python3
"""Generate deterministic, schema-valid 0.0.3 documentation artifacts."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Sequence

import pyarrow as pa
import pyarrow.parquet as pq

from openbot_data import (
    apply_dataset_repair,
    audit_dataset,
    build_catalog_evidence,
    build_dataset_snapshot,
    check_merge_compatibility,
    diff_dataset_snapshots,
    evaluate_dataset_readiness,
    plan_dataset_repair,
    verify_dataset_merge,
)
from openbot_data.serialization import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "examples" / "v0.0.3"


def _features() -> dict[str, dict[str, Any]]:
    semantic = {"coordinate_system": "joint", "normalization": "mean_std"}
    return {
        "action": {
            "dtype": "float32",
            "shape": [2],
            "names": ["joint_a", "joint_b"],
            "info": semantic,
        },
        "observation.state": {
            "dtype": "float32",
            "shape": [2],
            "names": ["joint_a", "joint_b"],
            "info": semantic,
        },
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
    }


def _write_dataset(
    root: Path,
    *,
    task_names: Sequence[str],
    source_offsets: Sequence[float],
) -> None:
    if len(task_names) != len(source_offsets):
        raise ValueError("task_names and source_offsets must have equal length")
    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)
    frames_per_episode = 2
    total_frames = len(task_names) * frames_per_episode
    info = {
        "codebase_version": "v3.0",
        "fps": 10,
        "robot_type": "openbot-test-arm",
        "total_episodes": len(task_names),
        "total_frames": total_frames,
        "total_tasks": len(set(task_names)),
        "chunks_size": 1000,
        "data_files_size_in_mb": 100,
        "video_files_size_in_mb": 200,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": None,
        "features": _features(),
    }
    (root / "meta" / "info.json").write_text(
        json.dumps(info, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    unique_tasks = list(dict.fromkeys(task_names))
    task_indexes = {task: index for index, task in enumerate(unique_tasks)}
    pq.write_table(
        pa.table(
            {
                "task_index": list(range(len(unique_tasks))),
                "task": unique_tasks,
            }
        ),
        root / "meta" / "tasks.parquet",
    )
    starts = [index * frames_per_episode for index in range(len(task_names))]
    pq.write_table(
        pa.table(
            {
                "episode_index": list(range(len(task_names))),
                "length": [frames_per_episode] * len(task_names),
                "tasks": [[task] for task in task_names],
                "data/chunk_index": [0] * len(task_names),
                "data/file_index": [0] * len(task_names),
                "dataset_from_index": starts,
                "dataset_to_index": [
                    start + frames_per_episode for start in starts
                ],
            }
        ),
        root / "meta" / "episodes" / "chunk-000" / "file-000.parquet",
    )
    indexes = list(range(total_frames))
    episode_indexes = [
        episode
        for episode in range(len(task_names))
        for _ in range(frames_per_episode)
    ]
    frame_indexes = [frame for _ in task_names for frame in range(frames_per_episode)]
    actions = [
        [offset + frame * 0.1, offset - frame * 0.1]
        for offset in source_offsets
        for frame in range(frames_per_episode)
    ]
    states = [
        [offset + frame, offset + frame + 1]
        for offset in source_offsets
        for frame in range(frames_per_episode)
    ]
    task_values = [
        task_indexes[task]
        for task in task_names
        for _ in range(frames_per_episode)
    ]
    pq.write_table(
        pa.table(
            {
                "index": pa.array(indexes, type=pa.int64()),
                "episode_index": pa.array(episode_indexes, type=pa.int64()),
                "frame_index": pa.array(frame_indexes, type=pa.int64()),
                "timestamp": pa.array(
                    [frame / 10 for frame in frame_indexes],
                    type=pa.float32(),
                ),
                "task_index": pa.array(task_values, type=pa.int64()),
                "action": pa.array(actions, type=pa.list_(pa.float32(), 2)),
                "observation.state": pa.array(
                    states,
                    type=pa.list_(pa.float32(), 2),
                ),
            }
        ),
        root / "data" / "chunk-000" / "file-000.parquet",
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="openbot-data-v003-examples-") as temporary:
        workspace = Path(temporary)
        first = workspace / "first"
        second = workspace / "second"
        merged = workspace / "merged"
        _write_dataset(first, task_names=["pick cube"], source_offsets=[0.0])
        _write_dataset(second, task_names=["place cube"], source_offsets=[10.0])
        _write_dataset(
            merged,
            task_names=["pick cube", "place cube"],
            source_offsets=[0.0, 10.0],
        )

        audit = audit_dataset(
            str(first),
            input_format="lerobot",
            checksum="sha256",
            integrity="full",
            output_path=str(OUTPUT / "audit.json"),
        )
        snapshot = build_dataset_snapshot(
            str(first),
            input_format="lerobot",
            checksum="sha256",
            integrity="full",
            output_path=str(OUTPUT / "snapshot.json"),
        )
        second_snapshot = build_dataset_snapshot(
            str(second),
            input_format="lerobot",
            checksum="sha256",
            integrity="full",
        )
        diff_dataset_snapshots(
            snapshot,
            snapshot,
            output_path=str(OUTPUT / "diff.json"),
        )
        evaluate_dataset_readiness(
            str(first),
            profile="lerobot-core",
            input_format="lerobot",
            checksum="sha256",
            integrity="full",
            dataset_snapshot=snapshot,
            audit_result=audit,
            output_path=str(OUTPUT / "readiness.json"),
        )
        build_catalog_evidence(
            str(first),
            dataset_id="openbot/example-v003",
            checked_at="2026-07-28T12:00:00Z",
            input_format="lerobot",
            checksum="sha256",
            integrity="full",
            profile_id="lerobot-core",
            output_path=str(OUTPUT / "catalog-evidence.json"),
        )

        stale = workspace / "stale"
        repaired = workspace / "repaired"
        shutil.copytree(first, stale)
        info_path = stale / "meta" / "info.json"
        info = json.loads(info_path.read_text(encoding="utf-8"))
        info["total_frames"] = 99
        info_path.write_text(
            json.dumps(info, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        repair_plan = plan_dataset_repair(
            str(stale),
            integrity="full",
            output_path=str(OUTPUT / "repair-plan.json"),
        )
        repair_receipt = apply_dataset_repair(
            str(stale),
            repair_plan,
            output_path=str(repaired),
        )
        write_json_atomic(OUTPUT / "repair-receipt.json", repair_receipt)

        check_merge_compatibility(
            [snapshot, second_snapshot],
            output_path=str(OUTPUT / "merge-plan.json"),
        )
        verify_dataset_merge(
            str(merged),
            input_snapshots=[snapshot, second_snapshot],
            operation_record=None,
            loader_runner=None,
            output_path=str(OUTPUT / "merge-receipt.json"),
        )

    print(f"Generated 0.0.3 examples in {OUTPUT}")


if __name__ == "__main__":
    main()
