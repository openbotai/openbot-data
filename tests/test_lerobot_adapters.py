import json
from pathlib import Path
from typing import Any, Optional

import pytest

import openbot_data.adapters.lerobot_v30 as lerobot_v30
from openbot_data.adapters import (
    DiscoveryRequest,
    probe_version,
    read_lerobot_dataset,
    select_adapter,
)


def write_info(root: Path, **overrides: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "codebase_version": "v3.0",
        "fps": 10,
        "total_episodes": 1,
        "total_frames": 2,
        "total_tasks": 1,
        "features": {
            "observation.state": {"dtype": "float32", "shape": [2]},
        },
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": None,
    }
    info.update(overrides)
    path = root / "meta/info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info), encoding="utf-8")
    return info


def finding_codes(result: Any) -> set[str]:
    return {str(item["code"]) for item in result.findings}


@pytest.mark.parametrize(
    ("declared_version", "adapter_id", "compatibility", "expected_code"),
    [
        ("v2.1", "lerobot_v21", "exact", None),
        ("2.1", "lerobot_v21", "exact", None),
        ("v3.0", "lerobot_v30", "exact", None),
        ("v2.2", "lerobot_v21", "unknown_minor", "LEROBOT_CODEBASE_VERSION_UNTESTED"),
        ("v3.1", "lerobot_v30", "unknown_minor", "LEROBOT_CODEBASE_VERSION_UNTESTED"),
        ("v3.0.1", "lerobot_v30", "unknown_minor", "LEROBOT_CODEBASE_VERSION_UNTESTED"),
        (
            "v4.0",
            None,
            "unsupported_major",
            "LEROBOT_CODEBASE_VERSION_UNSUPPORTED",
        ),
    ],
)
def test_version_probe_matrix(
    tmp_path: Path,
    declared_version: str,
    adapter_id: Optional[str],
    compatibility: str,
    expected_code: Optional[str],
) -> None:
    root = tmp_path / declared_version.replace(".", "-")
    write_info(root, codebase_version=declared_version)

    probe = probe_version(str(root))

    assert probe.adapter_id == adapter_id
    assert probe.compatibility == compatibility
    assert (select_adapter(probe).adapter_id if select_adapter(probe) else None) == adapter_id
    codes = finding_codes(probe)
    if expected_code is None:
        assert codes == set()
    else:
        assert expected_code in codes


@pytest.mark.parametrize(
    ("value", "expected_code", "compatibility"),
    [
        (None, "LEROBOT_CODEBASE_VERSION_MISSING", "missing"),
        ("three", "LEROBOT_CODEBASE_VERSION_INVALID", "invalid"),
        (3, "LEROBOT_CODEBASE_VERSION_INVALID", "invalid"),
    ],
)
def test_version_probe_rejects_missing_or_invalid_version(
    tmp_path: Path,
    value: object,
    expected_code: str,
    compatibility: str,
) -> None:
    root = tmp_path / "dataset"
    write_info(root, codebase_version=value)

    probe = probe_version(str(root))

    assert probe.adapter_id is None
    assert probe.compatibility == compatibility
    assert expected_code in finding_codes(probe)


def test_version_probe_preserves_unknown_info_fields_immutably(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    write_info(
        root,
        future_contract={
            "enabled": True,
            "labels": ["alpha", "beta"],
        },
    )

    probe = probe_version(str(root))

    future = probe.raw_info["future_contract"]
    assert future["enabled"] is True
    assert future["labels"] == ("alpha", "beta")
    with pytest.raises(TypeError):
        probe.raw_info["future_contract"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        future["enabled"] = False


def test_metadata_symlink_policy_never_reads_outside_dataset_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    outside = tmp_path / "outside-info.json"
    outside.write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "apiToken": "outside-secret-value",
            }
        ),
        encoding="utf-8",
    )
    (root / "meta/info.json").symlink_to(outside)

    default = probe_version(str(root))
    followed = probe_version(str(root), follow_symlinks=True)

    assert default.adapter_id is None
    assert followed.adapter_id is None
    assert default.raw_info == {}
    assert followed.raw_info == {}
    assert "DATASET_SYMLINK_SKIPPED" in finding_codes(default)
    assert "DATASET_PATH_OUTSIDE_ROOT" in finding_codes(followed)
    assert "outside-secret-value" not in repr(default)
    assert "outside-secret-value" not in repr(followed)


def test_metadata_symlink_opt_in_only_allows_targets_inside_dataset_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    target = root / "meta/info.real.json"
    target.write_text(
        json.dumps({"codebase_version": "v3.0"}),
        encoding="utf-8",
    )
    (root / "meta/info.json").symlink_to(target.name)

    default = probe_version(str(root))
    followed = probe_version(str(root), follow_symlinks=True)

    assert default.adapter_id is None
    assert "DATASET_SYMLINK_SKIPPED" in finding_codes(default)
    assert followed.adapter_id == "lerobot_v30"
    assert followed.compatibility == "exact"


def test_unknown_major_returns_metadata_only_result(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    write_info(root, codebase_version="v4.0", future_field={"kept": 1})

    result = read_lerobot_dataset(str(root))

    assert result.adapter_id is None
    assert result.compatibility == "unsupported_major"
    assert result.raw_info["future_field"]["kept"] == 1
    assert result.episodes == ()
    assert result.tasks == ()
    assert "LEROBOT_CODEBASE_VERSION_UNSUPPORTED" in finding_codes(result)
    assert {
        item.status
        for item in result.capabilities
        if item.name == "metadata.episodes"
    } == {"skipped"}


def make_v21_dataset(root: Path) -> None:
    write_info(
        root,
        codebase_version="v2.1",
        chunks_size=1000,
        data_path="data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
        video_path=(
            "videos/chunk-{episode_chunk:03d}/{video_key}/"
            "episode_{episode_index:06d}.mp4"
        ),
        features={
            "observation.images.top": {
                "dtype": "video",
                "shape": [16, 16, 3],
                "future_video_field": "retained",
            },
            "observation.state": {"dtype": "float32", "shape": [2]},
        },
        future_top_level={"retained": True},
    )
    (root / "meta/tasks.jsonl").write_text(
        json.dumps({"task_index": 0, "task": "pick", "future_task": "kept"}) + "\n",
        encoding="utf-8",
    )
    (root / "meta/episodes.jsonl").write_text(
        json.dumps(
            {
                "episode_index": 0,
                "length": 2,
                "tasks": ["pick"],
                "future_episode": {"kept": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "meta/episodes_stats.jsonl").write_text(
        json.dumps({"episode_index": 0, "stats": {}}) + "\n",
        encoding="utf-8",
    )
    data_path = root / "data/chunk-000/episode_000000.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_bytes(b"inventory-only-v21")
    video_path = root / "videos/chunk-000/observation.images.top/episode_000000.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"inventory-only-v21")


def test_v21_adapter_reads_jsonl_and_episode_per_file_inventory(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    make_v21_dataset(root)

    result = read_lerobot_dataset(str(root))

    assert result.adapter_id == "lerobot_v21"
    assert not [item for item in result.findings if item["severity"] == "error"]
    migration = next(
        item
        for item in result.findings
        if item["code"] == "LEROBOT_V21_MIGRATION_RECOMMENDED"
    )
    assert migration["severity"] == "info"
    assert migration["evidence"] == {
        "source_contract": "v2.1",
        "target_contract": "v3.0",
        "package": "lerobot==0.6.0",
        "module": "lerobot.scripts.convert_dataset_v21_to_v30",
        "mutates_source": False,
        "openbot_will_execute": False,
    }
    assert result.raw_info["future_top_level"]["retained"] is True
    assert result.tasks[0].extensions["future_task"] == "kept"
    assert result.episodes[0].extensions["future_episode"]["kept"] is True
    assert result.episodes[0].data_path == "data/chunk-000/episode_000000.parquet"
    assert result.episodes[0].video_paths == (
        "videos/chunk-000/observation.images.top/episode_000000.mp4",
    )
    assert {(item.kind, item.path, item.exists) for item in result.artifacts} >= {
        ("data", "data/chunk-000/episode_000000.parquet", True),
        (
            "video",
            "videos/chunk-000/observation.images.top/episode_000000.mp4",
            True,
        ),
    }
    assert {(item.kind, item.path) for item in result.relations} == {
        ("data", "data/chunk-000/episode_000000.parquet"),
        ("video", "videos/chunk-000/observation.images.top/episode_000000.mp4"),
    }
    for artifact in result.artifacts:
        assert not Path(artifact.path).is_absolute()


def test_v21_metadata_symlink_never_reads_outside_dataset_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    make_v21_dataset(root)
    outside = tmp_path / "outside-tasks.jsonl"
    outside.write_text(
        json.dumps(
            {
                "task_index": 7,
                "task": "outside-secret-task",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "meta/tasks.jsonl").unlink()
    (root / "meta/tasks.jsonl").symlink_to(outside)

    default = read_lerobot_dataset(str(root))
    followed = read_lerobot_dataset(
        str(root),
        DiscoveryRequest(follow_symlinks=True),
    )

    assert default.tasks == ()
    assert followed.tasks == ()
    assert "DATASET_SYMLINK_SKIPPED" in finding_codes(default)
    assert "DATASET_PATH_OUTSIDE_ROOT" in finding_codes(followed)
    assert "outside-secret-task" not in repr(default)
    assert "outside-secret-task" not in repr(followed)


def test_v21_missing_episode_data_is_structured(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    make_v21_dataset(root)
    (root / "data/chunk-000/episode_000000.parquet").unlink()

    result = read_lerobot_dataset(str(root))

    assert "LEROBOT_DATA_MISSING" in finding_codes(result)
    relation = next(item for item in result.relations if item.kind == "data")
    assert relation.exists is False


def test_v30_jsonl_layout_is_reported_but_never_mixed(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    write_info(root, codebase_version="v3.0", video_path=None)
    (root / "meta/tasks.jsonl").write_text(
        json.dumps({"task_index": 0, "task": "pick"}) + "\n",
        encoding="utf-8",
    )
    (root / "meta/episodes.jsonl").write_text(
        json.dumps({"episode_index": 0, "length": 2, "tasks": ["pick"]}) + "\n",
        encoding="utf-8",
    )

    result = read_lerobot_dataset(str(root))

    assert result.adapter_id == "lerobot_v30"
    assert result.episodes == ()
    assert result.tasks == ()
    assert {
        "LEROBOT_LAYOUT_VERSION_MISMATCH",
        "LEROBOT_TASKS_MISSING",
        "LEROBOT_EPISODES_MISSING",
    } <= finding_codes(result)
    assert "LEROBOT_VIDEO_PATH_TEMPLATE_INVALID" not in finding_codes(result)
    mismatch = next(
        item
        for item in result.findings
        if item["code"] == "LEROBOT_LAYOUT_VERSION_MISMATCH"
    )
    assert mismatch["evidence"]["records_mixed"] is False


def test_v30_missing_pyarrow_is_a_structured_capability_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    write_info(root, codebase_version="v3.0", video_path=None)
    task_path = root / "meta/tasks.parquet"
    task_path.write_bytes(b"dependency-not-used")
    episode_path = root / "meta/episodes/chunk-000/file-000.parquet"
    episode_path.parent.mkdir(parents=True)
    episode_path.write_bytes(b"dependency-not-used")
    data_path = root / "data/chunk-000/file-000.parquet"
    data_path.parent.mkdir(parents=True)
    data_path.write_bytes(b"dependency-not-used")
    monkeypatch.setattr(lerobot_v30, "_load_parquet_module", lambda: None)

    result = read_lerobot_dataset(str(root))

    assert "LEROBOT_DEPENDENCY_MISSING" in finding_codes(result)
    assert result.episodes == ()
    assert result.tasks == ()
    assert {
        item.status
        for item in result.capabilities
        if item.name in {"metadata.tasks", "metadata.episodes", "data.parquet_footer"}
    } == {"skipped"}


def write_v30_parquet_dataset(
    root: Path,
    *,
    use_video: bool = True,
    bad_episode_footer: bool = False,
    bad_data_footer: bool = False,
) -> None:
    pa = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip(
        "pyarrow.parquet",
        exc_type=ImportError,
    )
    features: dict[str, Any] = {
        "observation.state": {"dtype": "float32", "shape": [2]},
    }
    video_path: Optional[str] = None
    if use_video:
        features["observation.images.top"] = {
            "dtype": "video",
            "shape": [16, 16, 3],
        }
        video_path = (
            "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
        )
    write_info(
        root,
        codebase_version="v3.0",
        features=features,
        video_path=video_path,
        future_top_level={"retained": True},
    )
    task_path = root / "meta/tasks.parquet"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    parquet.write_table(
        pa.table(
            {
                "task_index": [0],
                "task": ["pick"],
                "future_task": ["kept"],
            }
        ),
        task_path,
    )

    episode_values: dict[str, Any] = {
        "episode_index": [0],
        "length": [2],
        "tasks": [["pick"]],
        "meta/episodes/chunk_index": [0],
        "meta/episodes/file_index": [0],
        "data/chunk_index": [0],
        "data/file_index": [0],
        "dataset_from_index": [0],
        "dataset_to_index": [2],
        "future_episode": ["kept"],
    }
    if use_video:
        episode_values.update(
            {
                "videos/observation.images.top/chunk_index": [0],
                "videos/observation.images.top/file_index": [0],
                "videos/observation.images.top/from_timestamp": [0.0],
                "videos/observation.images.top/to_timestamp": [0.2],
            }
        )
    episode_path = root / "meta/episodes/chunk-000/file-000.parquet"
    episode_path.parent.mkdir(parents=True, exist_ok=True)
    if bad_episode_footer:
        episode_path.write_bytes(b"not-parquet")
    else:
        parquet.write_table(pa.table(episode_values), episode_path)

    data_path = root / "data/chunk-000/file-000.parquet"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    if bad_data_footer:
        data_path.write_bytes(b"not-parquet")
    else:
        parquet.write_table(
            pa.table(
                {
                    "index": [0, 1],
                    "episode_index": [0, 0],
                    "frame_index": [0, 1],
                    "timestamp": [0.0, 0.1],
                }
            ),
            data_path,
        )
    (root / "meta/stats.json").write_text(
        json.dumps({"observation.state": {"mean": [0.0, 0.0]}}),
        encoding="utf-8",
    )
    if use_video:
        video = root / "videos/observation.images.top/chunk-000/file-000.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"adapter-inventory-does-not-decode")


def test_v30_adapter_reads_canonical_parquet_contract(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow", exc_type=ImportError)
    root = tmp_path / "dataset"
    write_v30_parquet_dataset(root)

    result = read_lerobot_dataset(
        str(root),
        DiscoveryRequest(parquet_batch_size=1),
    )

    assert result.adapter_id == "lerobot_v30"
    assert not [item for item in result.findings if item["severity"] == "error"]
    assert result.raw_info["future_top_level"]["retained"] is True
    assert result.tasks[0].extensions["future_task"] == "kept"
    assert result.episodes[0].extensions["future_episode"] == "kept"
    assert result.episodes[0].data_path == "data/chunk-000/file-000.parquet"
    assert result.episodes[0].dataset_from_index == 0
    assert result.episodes[0].dataset_to_index == 2
    assert result.episodes[0].video_paths == (
        "videos/observation.images.top/chunk-000/file-000.mp4",
    )
    assert {(item.kind, item.path) for item in result.relations} == {
        ("data", "data/chunk-000/file-000.parquet"),
        ("video", "videos/observation.images.top/chunk-000/file-000.mp4"),
    }
    data_artifact = next(
        item
        for item in result.artifacts
        if item.kind == "data" and item.path == "data/chunk-000/file-000.parquet"
    )
    assert data_artifact.row_count == 2
    assert data_artifact.columns == (
        "episode_index",
        "frame_index",
        "index",
        "timestamp",
    )


def test_v30_video_path_none_is_valid_without_video_features(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow", exc_type=ImportError)
    root = tmp_path / "dataset"
    write_v30_parquet_dataset(root, use_video=False)

    result = read_lerobot_dataset(str(root))

    assert "LEROBOT_VIDEO_PATH_TEMPLATE_INVALID" not in finding_codes(result)
    assert not [item for item in result.findings if item["severity"] == "error"]
    assert result.video_paths == ()
    assert result.video_keys == ()


def test_v30_bad_episode_footer_is_structured(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow", exc_type=ImportError)
    root = tmp_path / "dataset"
    write_v30_parquet_dataset(root, bad_episode_footer=True)

    result = read_lerobot_dataset(str(root))

    assert "LEROBOT_EPISODES_UNREADABLE" in finding_codes(result)
    assert result.episodes == ()


def test_v30_bad_data_footer_is_structured(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow", exc_type=ImportError)
    root = tmp_path / "dataset"
    write_v30_parquet_dataset(root, bad_data_footer=True)

    result = read_lerobot_dataset(str(root))

    assert "LEROBOT_DATA_UNREADABLE" in finding_codes(result)
    data_capability = next(
        item for item in result.capabilities if item.name == "data.parquet_footer"
    )
    assert data_capability.status == "partial"


def test_v30_missing_data_relation_is_structured(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip(
        "pyarrow.parquet",
        exc_type=ImportError,
    )
    root = tmp_path / "dataset"
    write_v30_parquet_dataset(root, use_video=False)
    episode_path = root / "meta/episodes/chunk-000/file-000.parquet"
    table = parquet.read_table(episode_path)
    columns = [
        name for name in table.column_names if name != "data/file_index"
    ]
    parquet.write_table(table.select(columns), episode_path)

    result = read_lerobot_dataset(str(root))

    assert pa is not None
    assert "LEROBOT_DATA_RELATION_MISSING" in finding_codes(result)


def test_v30_invalid_data_relation_is_structured(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip(
        "pyarrow.parquet",
        exc_type=ImportError,
    )
    root = tmp_path / "dataset"
    write_v30_parquet_dataset(root, use_video=False)
    episode_path = root / "meta/episodes/chunk-000/file-000.parquet"
    table = parquet.read_table(episode_path)
    column_index = table.column_names.index("data/chunk_index")
    table = table.set_column(
        column_index,
        "data/chunk_index",
        pa.array(["invalid"]),
    )
    parquet.write_table(table, episode_path)

    result = read_lerobot_dataset(str(root))

    assert "LEROBOT_DATA_RELATION_INVALID" in finding_codes(result)


def test_v30_invalid_task_row_is_structured(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip(
        "pyarrow.parquet",
        exc_type=ImportError,
    )
    root = tmp_path / "dataset"
    write_v30_parquet_dataset(root, use_video=False)
    task_path = root / "meta/tasks.parquet"
    parquet.write_table(
        pa.table({"task_index": [-1], "task": ["pick"]}),
        task_path,
    )

    result = read_lerobot_dataset(str(root))

    assert "LEROBOT_TASK_INVALID" in finding_codes(result)
    assert result.tasks == ()
