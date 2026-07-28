from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import cv2
import jsonschema
import numpy as np
import pytest
from typer.testing import CliRunner

from openbot_data import (
    build_dataset_snapshot,
    diff_dataset_snapshots,
    schema_path,
)
from openbot_data.cli import app
from openbot_data.errors import DatasetArgumentError
from openbot_data.preflight import dataset_fingerprint


def make_video(path: Path, *, frames: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        5.0,
        (16, 16),
    )
    if not writer.isOpened():
        pytest.skip("OpenCV video writer is required for snapshot fixtures")
    for frame_index in range(frames):
        writer.write(
            np.full((16, 16, 3), (frame_index * 30) % 256, dtype=np.uint8)
        )
    writer.release()


def write_metadata_dataset(
    root: Path,
    features: dict[str, dict[str, Any]],
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    (root / "meta").mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "total_episodes": 0,
        "video_path": None,
        "features": features,
        **(extra or {}),
    }
    (root / "meta/info.json").write_text(json.dumps(info))
    (root / "meta/episodes.jsonl").write_text("")


def validate_artifact(name: str, artifact: dict[str, Any]) -> None:
    with schema_path(name) as path:
        schema = json.loads(path.read_text())
    jsonschema.Draft202012Validator(schema).validate(artifact)


def refresh_snapshot_fingerprints(snapshot: dict[str, Any]) -> None:
    components = {
        "source": snapshot["source"],
        "format": snapshot["format"],
        "features": snapshot["contract"]["features"],
        "tasks": snapshot["contract"]["tasks"],
        "episodes": snapshot["contract"]["episodes"],
        "video_streams": snapshot["contract"]["video_streams"],
        "totals": snapshot["totals"],
        "metadata_inventory": snapshot["inventory"]["metadata"],
        "data_inventory": snapshot["inventory"]["data"],
        "media_inventory": snapshot["inventory"]["media"],
        "coverage": snapshot["coverage"],
    }
    snapshot["component_fingerprints"] = {
        name: dataset_fingerprint(value)
        for name, value in sorted(components.items())
    }
    refresh_snapshot_fingerprint(snapshot)


def refresh_snapshot_fingerprint(snapshot: dict[str, Any]) -> None:
    payload = {
        field: snapshot[field]
        for field in (
            "schema_version",
            "fingerprint_version",
            "source",
            "format",
            "contract",
            "inventory",
            "totals",
            "coverage",
            "component_fingerprints",
        )
    }
    snapshot["snapshot_fingerprint"] = dataset_fingerprint(payload)


def test_snapshot_is_byte_stable_schema_valid_and_portable(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    first_path = tmp_path / "first.snapshot.json"
    second_path = tmp_path / "second.snapshot.json"
    make_video(root / "camera/episode.avi")

    first = build_dataset_snapshot(str(root), output_path=str(first_path))
    second = build_dataset_snapshot(str(root), output_path=str(second_path))

    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first["schema_version"] == "openbot.dataset_snapshot.v1"
    assert first["format"]["adapter"] == "video_directory"
    assert first["inventory"]["media"][0]["sha256"]
    assert str(tmp_path) not in json.dumps(first)
    validate_artifact("snapshot", first)


def test_snapshot_sanitizes_unknown_metadata_without_dropping_keys(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    write_metadata_dataset(
        root,
        {"observation.state": {"dtype": "float32", "shape": [6]}},
        extra={
            "future_extension": {"enabled": True},
            "api_token": "secret-value",
            "apiToken": "camel-secret-value",
            "accessToken": "camel-access-value",
            "authorizationHeader": "Bearer header-secret-value",
            "futureHeader": "Authorization: Bearer embedded-secret-value",
            "hubReference": "credential hf_1234567890abcdefghijklmnop",
            "reference_url": "https://example.com/data?token=secret#fragment",
            "cache_path": "/private/cache/dataset",
            "uncPath": r"\\private-server\share\dataset",
        },
    )

    snapshot = build_dataset_snapshot(
        str(root),
        input_format="lerobot",
        integrity="metadata",
    )
    metadata = snapshot["format"]["metadata"]

    assert metadata["future_extension"] == {"enabled": True}
    assert metadata["api_token"] == "[redacted]"
    assert metadata["apiToken"] == "[redacted]"
    assert metadata["accessToken"] == "[redacted]"
    assert metadata["authorizationHeader"] == "[redacted]"
    assert metadata["futureHeader"] == "[redacted]"
    assert metadata["hubReference"] == "[redacted]"
    assert metadata["reference_url"] == "https://example.com/data"
    assert metadata["cache_path"] == "[private-path-redacted]"
    assert metadata["uncPath"] == "[private-path-redacted]"
    assert "secret-value" not in json.dumps(snapshot)
    assert "camel-access-value" not in json.dumps(snapshot)
    assert "embedded-secret-value" not in json.dumps(snapshot)
    assert "hf_1234567890abcdefghijklmnop" not in json.dumps(snapshot)
    assert "private-server" not in json.dumps(snapshot)


def test_snapshot_never_reads_metadata_symlink_outside_dataset_root(
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

    for follow_symlinks in (False, True):
        snapshot = build_dataset_snapshot(
            str(root),
            input_format="lerobot",
            integrity="metadata",
            follow_symlinks=follow_symlinks,
        )
        serialized = json.dumps(snapshot)

        assert snapshot["format"]["metadata"] == {}
        assert "outside-secret-value" not in serialized
        assert str(outside) not in serialized


def test_unchanged_snapshot_diff_is_byte_stable(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    first_path = tmp_path / "first.diff.json"
    second_path = tmp_path / "second.diff.json"
    make_video(root / "episode.avi")
    snapshot = build_dataset_snapshot(str(root))

    first = diff_dataset_snapshots(
        snapshot,
        snapshot,
        output_path=str(first_path),
    )
    second = diff_dataset_snapshots(
        snapshot,
        snapshot,
        output_path=str(second_path),
    )

    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first["classification"] == "unchanged"
    assert first["changes"] == []
    validate_artifact("diff", first)


def test_diff_rejects_snapshot_that_fails_json_schema(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    make_video(root / "episode.avi")
    baseline = build_dataset_snapshot(str(root))
    invalid = deepcopy(baseline)
    del invalid["totals"]["frames"]

    with pytest.raises(DatasetArgumentError, match="JSON Schema"):
        diff_dataset_snapshots(baseline, invalid)


def test_diff_rejects_stale_snapshot_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    make_video(root / "episode.avi")
    baseline = build_dataset_snapshot(str(root))
    invalid = deepcopy(baseline)
    invalid["snapshot_fingerprint"] = "0" * 64

    with pytest.raises(
        DatasetArgumentError,
        match="snapshot_fingerprint does not match",
    ):
        diff_dataset_snapshots(baseline, invalid)


def test_diff_rejects_forged_component_fingerprint(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    make_video(root / "episode.avi")
    baseline = build_dataset_snapshot(str(root))
    invalid = deepcopy(baseline)
    invalid["component_fingerprints"]["totals"] = "0" * 64
    refresh_snapshot_fingerprint(invalid)

    with pytest.raises(
        DatasetArgumentError,
        match="component fingerprint does not match totals",
    ):
        diff_dataset_snapshots(baseline, invalid)


def test_diff_rejects_noncanonical_component_fingerprint_set(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    make_video(root / "episode.avi")
    baseline = build_dataset_snapshot(str(root))
    invalid = deepcopy(baseline)
    invalid["component_fingerprints"]["future"] = "0" * 64
    refresh_snapshot_fingerprint(invalid)

    with pytest.raises(
        DatasetArgumentError,
        match="exactly the canonical component set",
    ):
        diff_dataset_snapshots(baseline, invalid)


@pytest.mark.parametrize(
    ("baseline_feature", "candidate_feature", "expected_kind"),
    [
        (
            {"action": {"dtype": "float32", "shape": [6]}},
            {},
            "removed",
        ),
        (
            {"action": {"dtype": "float32", "shape": [6]}},
            {"action": {"dtype": "float64", "shape": [6]}},
            "dtype_changed",
        ),
        (
            {"action": {"dtype": "float32", "shape": [6]}},
            {"action": {"dtype": "float32", "shape": [7]}},
            "shape_changed",
        ),
    ],
)
def test_feature_contract_regressions_are_breaking(
    tmp_path: Path,
    baseline_feature: dict[str, dict[str, Any]],
    candidate_feature: dict[str, dict[str, Any]],
    expected_kind: str,
) -> None:
    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    write_metadata_dataset(baseline_root, baseline_feature)
    write_metadata_dataset(candidate_root, candidate_feature)

    result = diff_dataset_snapshots(
        build_dataset_snapshot(
            str(baseline_root),
            input_format="lerobot",
            integrity="metadata",
        ),
        build_dataset_snapshot(
            str(candidate_root),
            input_format="lerobot",
            integrity="metadata",
        ),
    )

    assert result["classification"] == "breaking"
    assert expected_kind in {change["kind"] for change in result["changes"]}


def test_media_content_change_is_material(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    video = root / "camera/episode.avi"
    make_video(video, frames=4)
    baseline = build_dataset_snapshot(str(root))
    make_video(video, frames=8)
    candidate = build_dataset_snapshot(str(root))

    result = diff_dataset_snapshots(baseline, candidate)

    assert result["classification"] == "material"
    assert "media_inventory" in {
        change["component"] for change in result["changes"]
    }
    assert {
        change["path"]
        for change in result["changes"]
        if change["component"] == "totals"
    } >= {"totals/frames", "totals/duration_seconds"}


def test_task_index_remap_is_breaking(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    make_video(root / "episode.avi")
    baseline = build_dataset_snapshot(str(root))
    baseline["contract"]["tasks"] = [
        {
            "task_index": 0,
            "task": "pick",
            "source": None,
            "extensions": {},
        }
    ]
    baseline["totals"]["tasks"] = 1
    refresh_snapshot_fingerprints(baseline)
    candidate = deepcopy(baseline)
    candidate["contract"]["tasks"][0]["task_index"] = 7
    refresh_snapshot_fingerprints(candidate)

    result = diff_dataset_snapshots(baseline, candidate)

    assert result["classification"] == "breaking"
    assert any(
        change["component"] == "tasks"
        and change["kind"] == "task_index_remapped"
        and change["before"] == 0
        and change["after"] == 7
        for change in result["changes"]
    )


def test_episode_source_ordinal_reorder_is_material(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    make_video(root / "episode.avi")
    baseline = build_dataset_snapshot(str(root))
    baseline["contract"]["episodes"] = [
        {
            "episode_index": episode_index,
            "source_ordinal": episode_index,
            "length": 1,
            "tasks": [],
            "video_files": [],
            "video_segments": [],
            "source": None,
            "data_relation": None,
            "extensions": {},
            "content_sha256": None,
            "content_rows": None,
        }
        for episode_index in (0, 1)
    ]
    baseline["totals"]["episodes"] = 2
    baseline["coverage"]["selection"]["episodes"] = [0, 1]
    baseline["coverage"]["totals"]["episodes"] = 2
    refresh_snapshot_fingerprints(baseline)
    candidate = deepcopy(baseline)
    candidate["contract"]["episodes"][0]["source_ordinal"] = 1
    candidate["contract"]["episodes"][1]["source_ordinal"] = 0
    refresh_snapshot_fingerprints(candidate)

    result = diff_dataset_snapshots(baseline, candidate)

    episode_changes = [
        change
        for change in result["changes"]
        if change["component"] == "episodes"
    ]
    assert result["classification"] == "material"
    assert [change["kind"] for change in episode_changes] == [
        "reordered",
        "reordered",
    ]


def test_additive_unknown_metadata_is_non_breaking(tmp_path: Path) -> None:
    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    features = {"observation.state": {"dtype": "float32", "shape": [6]}}
    write_metadata_dataset(baseline_root, features)
    write_metadata_dataset(
        candidate_root,
        features,
        extra={"future_extension": {"enabled": True}},
    )

    result = diff_dataset_snapshots(
        build_dataset_snapshot(
            str(baseline_root),
            input_format="lerobot",
            integrity="metadata",
        ),
        build_dataset_snapshot(
            str(candidate_root),
            input_format="lerobot",
            integrity="metadata",
        ),
    )

    assert result["classification"] == "non_breaking"


def test_snapshot_and_diff_cli_write_artifacts_and_gate(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.json"
    diff_path = tmp_path / "diff.json"
    make_video(root / "episode.avi", frames=4)
    runner = CliRunner()

    baseline_result = runner.invoke(
        app,
        ["snapshot", str(root), "--out", str(baseline_path)],
    )
    make_video(root / "episode.avi", frames=7)
    candidate_result = runner.invoke(
        app,
        ["snapshot", str(root), "--out", str(candidate_path)],
    )
    diff_result = runner.invoke(
        app,
        [
            "diff",
            str(baseline_path),
            str(candidate_path),
            "--out",
            str(diff_path),
            "--fail-on",
            "material",
        ],
    )

    assert baseline_result.exit_code == 0, baseline_result.output
    assert candidate_result.exit_code == 0, candidate_result.output
    assert diff_result.exit_code == 2, diff_result.output
    assert json.loads(diff_path.read_text())["classification"] == "material"
