from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

import openbot_data.validation as validation
from openbot_data.adapters.base import (
    AdapterResult,
    ArtifactRecord,
    EpisodeMetadata,
    RelationRecord,
    TaskRecord,
    freeze_value,
)
from openbot_data.models import VideoRecord
from openbot_data.validation import validate_prepared_dataset


def _parquet_modules() -> tuple[Any, Any]:
    pa = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip(
        "pyarrow.parquet",
        exc_type=ImportError,
    )
    return pa, parquet


def _write_table(
    path: Path,
    columns: Mapping[str, Sequence[Any]],
    *,
    vector_dtypes: Mapping[str, str] | None = None,
) -> None:
    pa, parquet = _parquet_modules()
    arrays = {}
    for key, values in columns.items():
        dtype = (vector_dtypes or {}).get(key)
        if dtype is None:
            arrays[key] = pa.array(values)
            continue
        width = len(values[0]) if values else 0
        scalar_type = {
            "float32": pa.float32(),
            "float64": pa.float64(),
        }[dtype]
        arrays[key] = pa.array(values, type=pa.list_(scalar_type, width))
    path.parent.mkdir(parents=True, exist_ok=True)
    parquet.write_table(pa.table(arrays), path)


def _episode(
    episode_index: int,
    length: int,
    data_path: str,
    *,
    start: int | None = None,
    end: int | None = None,
    tasks: tuple[str, ...] = ("pick",),
    source_row: int | None = None,
) -> EpisodeMetadata:
    return EpisodeMetadata(
        episode_index=episode_index,
        length=length,
        tasks=tasks,
        source_path="meta/episodes/chunk-000/file-000.parquet",
        source_row=source_row if source_row is not None else episode_index + 1,
        data_path=data_path,
        dataset_from_index=start,
        dataset_to_index=end,
    )


def _adapter(
    info: Mapping[str, Any],
    *,
    episodes: Sequence[EpisodeMetadata],
    data_paths: Sequence[str],
    row_counts: Mapping[str, int] | None = None,
    tasks: Sequence[TaskRecord] = (),
    relations: Sequence[RelationRecord] | None = None,
    stats_path: str | None = None,
    findings: Sequence[Mapping[str, Any]] = (),
) -> AdapterResult:
    artifacts = [
        ArtifactRecord(
            kind="data",
            path=path,
            exists=True,
            source="canonical",
            row_count=(row_counts or {}).get(path),
        )
        for path in data_paths
    ]
    if stats_path is not None:
        artifacts.append(
            ArtifactRecord(
                kind="stats",
                path=stats_path,
                exists=True,
                source="canonical",
            )
        )
    if relations is None:
        relations = [
            RelationRecord(
                kind="data",
                episode_index=episode.episode_index,
                path=episode.data_path or "",
                exists=True,
                dataset_from_index=episode.dataset_from_index,
                dataset_to_index=episode.dataset_to_index,
            )
            for episode in episodes
            if episode.data_path is not None
        ]
    return AdapterResult(
        adapter_id="lerobot_v30",
        declared_version="v3.0",
        compatibility="exact",
        raw_info=freeze_value(info),
        episodes=tuple(episodes),
        tasks=tuple(tasks),
        artifacts=tuple(artifacts),
        relations=tuple(relations),
        findings=tuple(freeze_value(item) for item in findings),
    )


def _task() -> TaskRecord:
    return TaskRecord(
        task_index=0,
        task="pick",
        source_path="meta/tasks.parquet",
        source_row=1,
    )


def _valid_info(total_frames: int = 4) -> dict[str, Any]:
    return {
        "codebase_version": "v3.0",
        "fps": 10,
        "total_episodes": 1,
        "total_frames": total_frames,
        "total_tasks": 1,
        "features": {
            "action": {"dtype": "float32", "shape": [2]},
            "observation.state": {"dtype": "float32", "shape": [2]},
        },
    }


def _valid_columns() -> dict[str, Sequence[Any]]:
    return {
        "index": [0, 1, 2, 3],
        "episode_index": [0, 0, 0, 0],
        "frame_index": [0, 1, 2, 3],
        "timestamp": [0.0, 0.1, 0.2, 0.3],
        "task_index": [0, 0, 0, 0],
        "action": [[0.0, 0.0], [0.0, 0.0], [2.0, 2.0], [4.0, 4.0]],
        "observation.state": [[1.0, 2.0], [2.0, 3.0], [3.0, 4.0], [4.0, 5.0]],
    }


def _valid_stats() -> dict[str, Any]:
    return {
        "action": {
            "min": [0.0, 0.0],
            "max": [4.0, 4.0],
            "mean": [1.5, 1.5],
            "std": [math.sqrt(2.75), math.sqrt(2.75)],
            "count": 4,
        },
        "observation.state": {
            "min": [1.0, 2.0],
            "max": [4.0, 5.0],
            "mean": [2.5, 3.5],
            "std": [math.sqrt(1.25), math.sqrt(1.25)],
            "count": 4,
        },
    }


def _codes(result: Any) -> set[str]:
    return {str(item["code"]) for item in result.findings}


def _capability(result: Any, name: str) -> Any:
    return next(item for item in result.capabilities if item.capability == name)


def test_full_validation_is_clean_batched_and_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    data_path = "data/chunk-000/file-000.parquet"
    _write_table(
        root / data_path,
        _valid_columns(),
        vector_dtypes={"action": "float32", "observation.state": "float32"},
    )
    stats_path = root / "meta/stats.json"
    stats_path.parent.mkdir(parents=True)
    stats_path.write_text(json.dumps(_valid_stats()), encoding="utf-8")
    adapter = _adapter(
        _valid_info(),
        episodes=(_episode(0, 4, data_path, start=0, end=4),),
        data_paths=(data_path,),
        row_counts={data_path: 4},
        tasks=(_task(),),
        stats_path="meta/stats.json",
    )

    real_parquet = pytest.importorskip(
        "pyarrow.parquet",
        exc_type=ImportError,
    )
    batch_sizes: list[int] = []

    class TrackingFile:
        def __init__(self, path: Path) -> None:
            self.inner = real_parquet.ParquetFile(path)
            self.metadata = self.inner.metadata
            self.schema_arrow = self.inner.schema_arrow

        def iter_batches(self, *, batch_size: int) -> Any:
            batch_sizes.append(batch_size)
            return self.inner.iter_batches(batch_size=batch_size)

    class TrackingParquet:
        ParquetFile = TrackingFile

    monkeypatch.setattr(validation, "_load_parquet_module", lambda: TrackingParquet)
    result = validate_prepared_dataset(
        root,
        adapter,
        (),
        "full",
        parquet_batch_size=1,
    )

    assert not [item for item in result.findings if item["severity"] == "error"]
    assert batch_sizes == [1]
    assert _capability(result, "data.parquet_rows").status == "complete"
    assert _capability(result, "stats.recomputed").status == "complete"
    assert result.measurements["coverage"]["partial"] is False
    assert result.measurements["numeric_features"]["action"]["variance"] == pytest.approx(
        (2.75, 2.75)
    )
    assert result.measurements["static_action_spans"] == (
        {
            "episode_index": 0,
            "feature_key": "action",
            "start_frame_index": 0,
            "end_frame_index": 1,
            "length_frames": 2,
            "value": (0.0, 0.0),
            "comparison": "exact_canonical_value",
            "coverage": "complete",
        },
    )
    assert result.measurements["task_counts"][0]["row_count"] == 4
    with pytest.raises(TypeError):
        result.measurements["coverage"]["partial"] = True


def test_official_v30_scalar_columns_match_logical_shape_one(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    data_path = "data/chunk-000/file-000.parquet"
    columns = _valid_columns()
    _write_table(
        root / data_path,
        columns,
        vector_dtypes={
            "action": "float32",
            "observation.state": "float32",
        },
    )
    info = _valid_info()
    info["features"].update(
        {
            "timestamp": {"dtype": "float32", "shape": [1]},
            "frame_index": {"dtype": "int64", "shape": [1]},
            "episode_index": {"dtype": "int64", "shape": [1]},
            "index": {"dtype": "int64", "shape": [1]},
            "task_index": {"dtype": "int64", "shape": [1]},
        }
    )
    adapter = _adapter(
        info,
        episodes=(_episode(0, 4, data_path, start=0, end=4),),
        data_paths=(data_path,),
        row_counts={data_path: 4},
        tasks=(_task(),),
    )

    result = validate_prepared_dataset(root, adapter, (), "full")

    assert "LEROBOT_FEATURE_SHAPE_MISMATCH" not in _codes(result)


def test_metadata_collects_totals_episode_indexes_and_range_failures(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    data_path = "data/chunk-000/file-000.parquet"
    _write_table(
        root / data_path,
        {
            "episode_index": [0, 0, 2],
            "frame_index": [0, 1, 0],
            "timestamp": [0.0, 0.1, 0.0],
            "action": [[0.0], [1.0], [2.0]],
        },
        vector_dtypes={"action": "float32"},
    )
    episodes = (
        _episode(0, 2, data_path, start=0, end=1, source_row=1),
        _episode(2, 1, data_path, start=2, end=3, source_row=2),
        _episode(2, 1, data_path, start=2, end=3, source_row=3),
    )
    info = {
        "codebase_version": "v3.0",
        "fps": 10,
        "total_episodes": 4,
        "total_frames": 99,
        "total_tasks": 2,
        "total_videos": 2,
        "total_data_shards": 2,
        "features": {"action": {"dtype": "float32", "shape": [1]}},
    }
    adapter = _adapter(
        info,
        episodes=episodes,
        data_paths=(data_path,),
        row_counts={data_path: 3},
        tasks=(_task(),),
    )

    result = validate_prepared_dataset(root, adapter, (), "metadata")

    assert {
        "LEROBOT_EPISODE_COUNT_MISMATCH",
        "LEROBOT_FRAME_COUNT_MISMATCH",
        "LEROBOT_TASK_COUNT_MISMATCH",
        "LEROBOT_VIDEO_COUNT_MISMATCH",
        "LEROBOT_DATA_SHARD_COUNT_MISMATCH",
        "LEROBOT_EPISODE_INDEX_DUPLICATE",
        "LEROBOT_EPISODE_INDEX_NON_CONTIGUOUS",
        "LEROBOT_EPISODE_RANGE_LENGTH_MISMATCH",
        "LEROBOT_EPISODE_RANGE_GAP",
        "LEROBOT_EPISODE_RANGE_OVERLAP",
    } <= _codes(result)
    assert _capability(result, "data.parquet_footer").status == "complete"
    assert _capability(result, "data.parquet_schema").status == "complete"
    assert _capability(result, "data.parquet_rows").status == "skipped"
    assert result.measurements["numeric_features"] == {}


def test_official_v30_multishard_ranges_are_dataset_global(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    paths = (
        "data/chunk-000/file-000.parquet",
        "data/chunk-000/file-001.parquet",
    )
    for episode_index, path in enumerate(paths):
        _write_table(
            root / path,
            {
                "index": [episode_index * 2, episode_index * 2 + 1],
                "episode_index": [episode_index, episode_index],
                "frame_index": [0, 1],
                "timestamp": [0.0, 0.1],
                "action": [[0.0], [1.0]],
            },
            vector_dtypes={"action": "float32"},
        )
    adapter = _adapter(
        {
            "codebase_version": "v3.0",
            "fps": 10,
            "total_episodes": 2,
            "total_frames": 4,
            "total_tasks": 0,
            "features": {"action": {"dtype": "float32", "shape": [1]}},
        },
        episodes=(
            _episode(0, 2, paths[0], start=0, end=2, tasks=()),
            _episode(1, 2, paths[1], start=2, end=4, tasks=()),
        ),
        data_paths=paths,
    )

    result = validate_prepared_dataset(root, adapter, (), "full")

    assert {
        "LEROBOT_EPISODE_RANGE_GAP",
        "LEROBOT_EPISODE_RANGE_OVERLAP",
        "LEROBOT_EPISODE_RANGE_OUT_OF_BOUNDS",
    }.isdisjoint(_codes(result))


def test_sample_selection_is_deterministic_and_explicitly_partial(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    paths = tuple(f"data/chunk-000/file-{index:03d}.parquet" for index in range(5))
    episodes = []
    for index, path in enumerate(paths):
        _write_table(
            root / path,
            {
                "index": [index],
                "episode_index": [index],
                "frame_index": [0],
                "timestamp": [0.0],
                "action": [[1.0]],
            },
            vector_dtypes={"action": "float32"},
        )
        episodes.append(_episode(index, 1, path, tasks=()))
    adapter = _adapter(
        {
            "codebase_version": "v3.0",
            "fps": 10,
            "total_episodes": 5,
            "total_frames": 5,
            "total_tasks": 0,
            "features": {"action": {"dtype": "float32", "shape": [1]}},
        },
        episodes=episodes,
        data_paths=paths,
    )

    first = validate_prepared_dataset(root, adapter, (), "sample")
    reversed_adapter = replace(adapter, artifacts=tuple(reversed(adapter.artifacts)))
    second = validate_prepared_dataset(root, reversed_adapter, (), "sample")
    coverage = _capability(first, "data.parquet_rows")

    assert coverage.status == "partial"
    assert coverage.reason_code == "sample_integrity"
    assert coverage.selected == (paths[0], paths[2], paths[4])
    assert coverage.omitted == (paths[1], paths[3])
    assert first.findings == second.findings
    assert first.capabilities == second.capabilities
    assert first.measurements == second.measurements
    assert first.measurements["coverage"]["partial"] is True
    digests = first.measurements["episode_content_digests"]
    assert {item["episode_index"] for item in digests} == {0, 2, 4}
    assert len({item["digest"] for item in digests}) == 1
    assert _capability(first, "stats.recomputed").status == "skipped"


def test_full_rows_collect_schema_index_numeric_task_and_timestamp_failures(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    data_path = "data/chunk-000/file-000.parquet"
    _write_table(
        root / data_path,
        {
            "index": [0, 0, 3, 5],
            "episode_index": [0, 0, 0, 0],
            "frame_index": [0, 0, 2, 4],
            "timestamp": [0.0, 0.0, 0.25, 0.4],
            "task_index": [0, 9, 0, 0],
            "action": [[0.0, 0.0], [float("inf"), 1.0], [2.0, 2.0], [3.0, 3.0]],
        },
        vector_dtypes={"action": "float64"},
    )
    info = _valid_info(total_frames=4)
    info["features"]["action"]["shape"] = [3]
    adapter = _adapter(
        info,
        episodes=(_episode(0, 5, data_path, start=0, end=5),),
        data_paths=(data_path,),
        row_counts={data_path: 99},
        tasks=(_task(),),
    )

    result = validate_prepared_dataset(root, adapter, (), "full")

    assert {
        "LEROBOT_FEATURE_COLUMN_MISSING",
        "LEROBOT_FEATURE_DTYPE_MISMATCH",
        "LEROBOT_FEATURE_SHAPE_MISMATCH",
        "LEROBOT_DATA_ROW_COUNT_MISMATCH",
        "LEROBOT_EPISODE_ROW_COUNT_MISMATCH",
        "LEROBOT_FRAME_INDEX_DUPLICATE",
        "LEROBOT_FRAME_INDEX_NON_CONTIGUOUS",
        "LEROBOT_GLOBAL_INDEX_DUPLICATE",
        "LEROBOT_GLOBAL_INDEX_NON_CONTIGUOUS",
        "LEROBOT_NUMERIC_NON_FINITE",
        "LEROBOT_TASK_REFERENCE_INVALID",
        "LEROBOT_TIMESTAMP_NON_MONOTONIC",
        "LEROBOT_TIMESTAMP_OFF_GRID",
        "LEROBOT_EPISODE_RANGE_OUT_OF_BOUNDS",
    } <= _codes(result)
    off_grid = next(
        item for item in result.findings if item["code"] == "LEROBOT_TIMESTAMP_OFF_GRID"
    )
    assert off_grid["evidence"]["tolerance_seconds"] == 0.0001
    assert _capability(result, "data.indexes").status == "complete"


def _video(path: Path, relative: str) -> VideoRecord:
    return VideoRecord(
        source_path=path,
        path=relative,
        filename=Path(relative).name,
        stream="observation.images.top",
        width=8,
        height=16,
        fps=4.0,
        frame_count=8,
        duration=2.0,
        size_bytes=100,
        size_mb=0.0001,
        metadata_valid=True,
        decode_valid=True,
        integrity_level="full",
    )


def test_video_contract_and_segments_use_only_prepared_records(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    relative = "videos/observation.images.top/chunk-000/file-000.mp4"
    episodes = (
        _episode(0, 2, "", tasks=()),
        _episode(1, 2, "", tasks=()),
    )
    relations = (
        RelationRecord(
            kind="video",
            episode_index=0,
            path=relative,
            exists=True,
            feature_key="observation.images.top",
            from_timestamp=0.0,
            to_timestamp=3.0,
        ),
        RelationRecord(
            kind="video",
            episode_index=1,
            path=relative,
            exists=True,
            feature_key="observation.images.top",
            from_timestamp=1.0,
            to_timestamp=2.0,
        ),
    )
    adapter = _adapter(
        {
            "codebase_version": "v3.0",
            "total_episodes": 2,
            "total_frames": 4,
            "total_tasks": 0,
            "features": {
                "observation.images.top": {
                    "dtype": "video",
                    "shape": [16, 16, 1],
                    "names": ["height", "width", "channels"],
                    "video_info": {"video.fps": 5.0},
                },
                "observation.images.side": {
                    "dtype": "video",
                    "shape": [16, 16, 3],
                },
            },
        },
        episodes=episodes,
        data_paths=(),
        relations=relations,
    )

    result = validate_prepared_dataset(
        root,
        adapter,
        (_video(root / relative, relative),),
        "full",
    )

    assert {
        "LEROBOT_VIDEO_RESOLUTION_MISMATCH",
        "LEROBOT_VIDEO_FPS_MISMATCH",
        "LEROBOT_VIDEO_CHANNELS_MISMATCH",
        "LEROBOT_VIDEO_COVERAGE_MISSING",
        "LEROBOT_VIDEO_SEGMENT_OVERLAP",
        "LEROBOT_VIDEO_SEGMENT_OUT_OF_RANGE",
    } <= _codes(result)
    assert result.measurements["camera_counts"] == (
        {
            "feature_key": "observation.images.side",
            "episode_count": 0,
            "segment_count": 0,
            "video_count": 0,
        },
        {
            "feature_key": "observation.images.top",
            "episode_count": 2,
            "segment_count": 2,
            "video_count": 1,
        },
    )


def test_official_v30_video_feature_is_not_required_in_data_parquet(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    data_path = "data/chunk-000/file-000.parquet"
    _write_table(
        root / data_path,
        _valid_columns(),
        vector_dtypes={"action": "float32", "observation.state": "float32"},
    )
    info = _valid_info()
    info["features"]["observation.images.top"] = {
        "dtype": "video",
        "shape": [3, 16, 16],
        "names": ["channels", "height", "width"],
        "info": {"video.fps": 10.0},
    }
    relative = "videos/observation.images.top/chunk-000/file-000.mp4"
    adapter = _adapter(
        info,
        episodes=(_episode(0, 4, data_path, start=0, end=4),),
        data_paths=(data_path,),
        tasks=(_task(),),
        relations=(
            RelationRecord(
                kind="data",
                episode_index=0,
                path=data_path,
                exists=True,
                dataset_from_index=0,
                dataset_to_index=4,
            ),
            RelationRecord(
                kind="video",
                episode_index=0,
                path=relative,
                exists=True,
                feature_key="observation.images.top",
                from_timestamp=0.0,
                to_timestamp=0.4,
            ),
        ),
    )

    result = validate_prepared_dataset(
        root,
        adapter,
        (_video(root / relative, relative),),
        "full",
    )

    assert "LEROBOT_FEATURE_COLUMN_MISSING" not in _codes(result)


def test_stats_validate_fields_shape_count_finiteness_and_full_values(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    data_path = "data/chunk-000/file-000.parquet"
    _write_table(
        root / data_path,
        _valid_columns(),
        vector_dtypes={"action": "float32", "observation.state": "float32"},
    )
    stats_path = root / "meta/stats.json"
    stats_path.parent.mkdir(parents=True)
    stats_path.write_text(
        json.dumps(
            {
                "action": {
                    "min": [float("nan"), 0.0],
                    "max": [4.0],
                    "mean": [99.0, 99.0],
                    "count": 99,
                }
            }
        ),
        encoding="utf-8",
    )
    adapter = _adapter(
        _valid_info(),
        episodes=(_episode(0, 4, data_path, start=0, end=4),),
        data_paths=(data_path,),
        tasks=(_task(),),
        stats_path="meta/stats.json",
    )

    result = validate_prepared_dataset(root, adapter, (), "full")

    assert {
        "LEROBOT_STATS_FIELD_MISSING",
        "LEROBOT_STATS_SHAPE_MISMATCH",
        "LEROBOT_STATS_COUNT_MISMATCH",
        "LEROBOT_STATS_NON_FINITE",
        "LEROBOT_STATS_VALUE_MISMATCH",
    } <= _codes(result)
    mismatch = next(
        item for item in result.findings if item["code"] == "LEROBOT_STATS_VALUE_MISMATCH"
    )
    assert mismatch["evidence"]["method"] == "online_welford_population"
    assert mismatch["evidence"]["absolute_tolerance"] == 1e-6
    assert mismatch["evidence"]["relative_tolerance"] == 1e-5


def test_missing_pyarrow_is_structured_and_preserves_adapter_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    relative = "data/chunk-000/file-000.parquet"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"dependency is intentionally unavailable")
    adapter = _adapter(
        {
            "codebase_version": "v3.0",
            "total_episodes": 0,
            "total_frames": 0,
            "total_tasks": 0,
            "features": {},
        },
        episodes=(),
        data_paths=(relative,),
        findings=(
            {
                "code": "EXISTING_DISCOVERY_FINDING",
                "severity": "warning",
                "layer": "metadata",
                "message": "Preserve me.",
            },
        ),
    )
    monkeypatch.setattr(validation, "_load_parquet_module", lambda: None)

    result = validate_prepared_dataset(root, adapter, (), "sample")

    assert {
        "EXISTING_DISCOVERY_FINDING",
        "LEROBOT_DEPENDENCY_MISSING",
    } <= _codes(result)
    assert _capability(result, "data.parquet_footer").status == "unavailable"
    assert _capability(result, "data.parquet_schema").reason_code == ("pyarrow_unavailable")
    assert _capability(result, "data.parquet_rows").status == "unavailable"
    existing = next(
        item for item in result.findings if item["code"] == "EXISTING_DISCOVERY_FINDING"
    )
    assert existing["location"] == {}
    assert existing["evidence"] == {}
    with pytest.raises(TypeError):
        existing["evidence"]["changed"] = True


def test_unfinalized_parquet_has_specific_finding_and_does_not_hide_stats(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pyarrow.parquet", exc_type=ImportError)
    root = tmp_path / "dataset"
    relative = "data/chunk-000/file-000.parquet"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-a-finalized-parquet")
    adapter = _adapter(
        {
            "codebase_version": "v3.0",
            "total_episodes": 0,
            "total_frames": 0,
            "total_tasks": 0,
            "features": {},
        },
        episodes=(),
        data_paths=(relative,),
    )

    result = validate_prepared_dataset(root, adapter, (), "metadata")

    assert "LEROBOT_PARQUET_UNFINALIZED" in _codes(result)
    assert "LEROBOT_STATS_MISSING" in _codes(result)
    assert _capability(result, "data.parquet_footer").status == "unavailable"
    assert _capability(result, "data.parquet_rows").status == "skipped"


@pytest.mark.parametrize(
    ("integrity", "batch_size"),
    [
        ("unknown", 1024),
        ("full", 0),
        ("sample", True),
    ],
)
def test_validation_rejects_invalid_execution_contract(
    tmp_path: Path,
    integrity: str,
    batch_size: Any,
) -> None:
    adapter = _adapter(
        {"features": {}},
        episodes=(),
        data_paths=(),
    )

    with pytest.raises(ValueError):
        validate_prepared_dataset(
            tmp_path,
            adapter,
            (),
            integrity,
            parquet_batch_size=batch_size,
        )
