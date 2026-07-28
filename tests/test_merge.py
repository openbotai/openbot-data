from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from openbot_data.errors import DatasetArgumentError
from openbot_data.merge import (
    PINNED_LEROBOT_PACKAGE,
    PINNED_LEROBOT_TOOL,
    check_merge_compatibility,
    verify_dataset_merge,
)
from openbot_data.preflight import dataset_fingerprint


def refingerprint(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    result.pop("snapshot_fingerprint", None)
    result.pop("tool", None)
    components = {
        "source": result["source"],
        "format": result["format"],
        "features": result["contract"]["features"],
        "tasks": result["contract"]["tasks"],
        "episodes": result["contract"]["episodes"],
        "video_streams": result["contract"]["video_streams"],
        "totals": result["totals"],
        "metadata_inventory": result["inventory"]["metadata"],
        "data_inventory": result["inventory"]["data"],
        "media_inventory": result["inventory"]["media"],
        "coverage": result["coverage"],
    }
    result["component_fingerprints"] = {
        name: dataset_fingerprint(value)
        for name, value in sorted(components.items())
    }
    fingerprint_payload = {
        field: result[field]
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
    fingerprint = dataset_fingerprint(fingerprint_payload)
    result["tool"] = {"name": "openbot-data", "version": "test"}
    result["snapshot_fingerprint"] = fingerprint
    return result


def make_snapshot(
    identity: str,
    *,
    episodes: int = 1,
    fps: int = 30,
    action_shape: list[int] | None = None,
    normalization: str | None = "mean_std",
    task: str = "pick",
    license_name: str | None = "apache-2.0",
    robot_type: str | None = "so100",
    version: str = "v3.0",
) -> dict[str, Any]:
    action_metadata: dict[str, Any] = {"coordinate_system": "joint"}
    state_metadata: dict[str, Any] = {"coordinate_system": "joint"}
    if normalization is not None:
        action_metadata["normalization"] = normalization
        state_metadata["normalization"] = normalization
    metadata: dict[str, Any] = {
        "codebase_version": version,
        "fps": fps,
    }
    if license_name is not None:
        metadata["license"] = license_name
    if robot_type is not None:
        metadata["robot_type"] = robot_type
    digest = dataset_fingerprint({"identity": identity, "payload": "data"})
    episode_records = [
        {
            "episode_index": index,
            "source_ordinal": index,
            "length": 2,
            "tasks": [task],
            "video_files": [],
            "video_segments": [],
            "source": None,
            "data_relation": {
                "path": "data/chunk-000/file-000.parquet",
                "dataset_from_index": index * 2,
                "dataset_to_index": (index + 1) * 2,
            },
            "extensions": {},
            "content_sha256": dataset_fingerprint(
                {
                    "identity": identity,
                    "episode_index": index,
                }
            ),
            "content_rows": 2,
        }
        for index in range(episodes)
    ]
    snapshot = {
        "schema_version": "openbot.dataset_snapshot.v1",
        "fingerprint_version": "openbot.dataset_snapshot.fingerprint.v1",
        "source": {
            "kind": "local",
            "locator": f"dataset://{identity}",
            "requested_revision": None,
            "resolved_revision": None,
        },
        "format": {
            "input_format": "lerobot",
            "adapter": "lerobot_v30",
            "dataset_format_version": version,
            "compatibility_target": PINNED_LEROBOT_PACKAGE,
            "metadata": metadata,
        },
        "contract": {
            "features": [
                {
                    "key": "action",
                    "dtype": "float32",
                    "shape": action_shape or [2],
                    "names": ["joint_a", "joint_b"],
                    "metadata": action_metadata,
                },
                {
                    "key": "observation.state",
                    "dtype": "float32",
                    "shape": [2],
                    "names": ["joint_a", "joint_b"],
                    "metadata": state_metadata,
                },
            ],
            "tasks": [
                {
                    "task_index": 0,
                    "task": task,
                    "source": None,
                    "extensions": {},
                }
            ],
            "episodes": episode_records,
            "video_streams": [],
        },
        "inventory": {
            "metadata": [
                {
                    "path": "meta/info.json",
                    "size_bytes": 10,
                    "sha256": dataset_fingerprint({"identity": identity, "payload": "metadata"}),
                }
            ],
            "data": [
                {
                    "path": "data/chunk-000/file-000.parquet",
                    "size_bytes": episodes * 10,
                    "sha256": digest,
                }
            ],
            "media": [],
        },
        "totals": {
            "episodes": episodes,
            "tasks": 1,
            "features": 2,
            "video_streams": 0,
            "metadata_shards": 1,
            "data_shards": 1,
            "media_shards": 0,
            "frames": episodes * 2,
            "duration_seconds": episodes * 2 / fps,
            "size_bytes": episodes * 10 + 10,
        },
        "coverage": {
            "requested_integrity": "full",
            "checksum": "sha256",
            "capabilities": [
                {
                    "capability": capability,
                    "status": "complete",
                    "integrity": "full",
                    "checked": 1,
                    "total": 1,
                    "selected": [],
                    "omitted": [],
                }
                for capability in (
                    "content.checksum",
                    "format.contract",
                    "metadata.info",
                    "source.identity",
                )
            ],
            "completed_capabilities": [
                "content.checksum",
                "format.contract",
                "metadata.info",
                "source.identity",
            ],
            "skipped_capabilities": [],
            "selection": {
                "episodes": list(range(episodes)),
                "cameras": [],
                "metadata_shards": ["meta/info.json"],
                "data_shards": ["data/chunk-000/file-000.parquet"],
                "media_shards": [],
            },
            "totals": {
                "episodes": episodes,
                "cameras": 0,
                "metadata_shards": 1,
                "data_shards": 1,
                "media_shards": 0,
            },
        },
        "component_fingerprints": {},
    }
    return refingerprint(snapshot)


def make_merged_snapshot(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    feature_regression: bool = False,
) -> dict[str, Any]:
    result = copy.deepcopy(first)
    result["source"]["locator"] = "dataset://merged"
    result["contract"]["episodes"] = [
        {
            **copy.deepcopy(episode),
            "episode_index": index,
            "source_ordinal": index,
        }
        for index, episode in enumerate(
            first["contract"]["episodes"] + second["contract"]["episodes"]
        )
    ]
    result["contract"]["tasks"] = [
        {
            "task_index": task_index,
            "task": task,
            "source": None,
            "extensions": {},
        }
        for task_index, task in enumerate(
            sorted(
                {
                    item["task"]
                    for item in (
                        first["contract"]["tasks"]
                        + second["contract"]["tasks"]
                    )
                }
            )
        )
    ]
    if feature_regression:
        result["contract"]["features"][0]["shape"] = [3]
    result["inventory"]["data"] = [
        {
            "path": "data/chunk-000/file-000.parquet",
            "size_bytes": 20,
            "sha256": dataset_fingerprint({"identity": "merged", "payload": "data"}),
        }
    ]
    result["totals"]["episodes"] = first["totals"]["episodes"] + second["totals"]["episodes"]
    result["totals"]["tasks"] = len(result["contract"]["tasks"])
    result["totals"]["frames"] = first["totals"]["frames"] + second["totals"]["frames"]
    result["totals"]["duration_seconds"] = (
        first["totals"]["duration_seconds"] + second["totals"]["duration_seconds"]
    )
    result["totals"]["size_bytes"] = sum(
        item["size_bytes"]
        for group in result["inventory"].values()
        for item in group
    )
    result["coverage"]["selection"]["episodes"] = list(range(result["totals"]["episodes"]))
    result["coverage"]["totals"]["episodes"] = result["totals"]["episodes"]
    return refingerprint(result)


def with_camera(
    snapshot: dict[str, Any],
    *,
    codec: str = "h264",
    is_depth_map: bool = False,
    complete_coverage: bool = True,
) -> dict[str, Any]:
    result = copy.deepcopy(snapshot)
    identity = result["source"]["locator"]
    camera_key = "observation.images.top"
    path = "videos/observation.images.top/chunk-000/file-000.mp4"
    result["contract"]["features"].append(
        {
            "key": camera_key,
            "dtype": "video",
            "shape": [16, 16, 3],
            "names": ["height", "width", "channel"],
            "metadata": {
                "info": {
                    "video.codec": codec,
                    "video.pix_fmt": "yuv420p",
                    "video.is_depth_map": is_depth_map,
                    "video.fps": result["format"]["metadata"]["fps"],
                }
            },
        }
    )
    result["contract"]["video_streams"] = [
        {
            "key": camera_key,
            "paths": [path],
            "resolutions": [[16, 16]],
            "fps": [result["format"]["metadata"]["fps"]],
            "frame_count": result["totals"]["frames"],
            "duration_seconds": result["totals"]["duration_seconds"],
            "size_bytes": 100,
        }
    ]
    for episode in result["contract"]["episodes"]:
        episode["video_files"] = [path]
        episode["video_segments"] = [
            {
                "video_key": camera_key,
                "path": path,
                "from_timestamp": episode["episode_index"] * 0.1,
                "to_timestamp": (episode["episode_index"] + 1) * 0.1,
            }
        ]
    result["inventory"]["media"] = [
        {
            "path": path,
            "size_bytes": 100,
            "sha256": dataset_fingerprint({"identity": identity, "payload": "media"}),
            "stream": camera_key,
            "frame_count": result["totals"]["frames"],
            "fps": result["format"]["metadata"]["fps"],
            "duration_seconds": result["totals"]["duration_seconds"],
            "resolution": [16, 16],
        }
    ]
    result["totals"]["features"] = 3
    result["totals"]["video_streams"] = 1
    result["totals"]["media_shards"] = 1
    result["coverage"]["selection"]["cameras"] = [camera_key]
    result["coverage"]["selection"]["media_shards"] = [path]
    result["coverage"]["totals"]["cameras"] = 1
    result["coverage"]["totals"]["media_shards"] = 1
    if complete_coverage:
        result["coverage"]["completed_capabilities"].extend(["media.decode", "media.metadata"])
        result["coverage"]["completed_capabilities"] = sorted(
            set(result["coverage"]["completed_capabilities"])
        )
    return refingerprint(result)


def schema(name: str) -> dict[str, Any]:
    path = (
        Path(__file__).parents[1]
        / "openbot_data"
        / "schemas"
        / f"dataset-merge-{name}-v1.schema.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def operation_record(
    inputs: list[dict[str, Any]],
    merged: dict[str, Any],
) -> dict[str, Any]:
    return {
        "tool": PINNED_LEROBOT_TOOL,
        "package": PINNED_LEROBOT_PACKAGE,
        "operation": "merge",
        "command": [
            "uvx",
            "--from",
            "lerobot[dataset]==0.6.0",
            PINNED_LEROBOT_TOOL,
            "--new_repo_id",
            "openbot/merged",
            "--new_root",
            "OPENBOT_MERGED_ROOT",
            "--operation.type",
            "merge",
            "--operation.repo_ids",
            '["input-1","input-2"]',
            "--operation.roots",
            '["OPENBOT_INPUT_ROOT_001","OPENBOT_INPUT_ROOT_002"]',
        ],
        "input_snapshot_fingerprints": [item["snapshot_fingerprint"] for item in inputs],
        "output_snapshot_fingerprint": merged["snapshot_fingerprint"],
        "exit_code": 0,
    }


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda value: value, "direct"),
        (
            lambda value: refingerprint(
                {
                    **value,
                    "format": {
                        **value["format"],
                        "metadata": {
                            **value["format"]["metadata"],
                            "fps": 20,
                        },
                    },
                }
            ),
            "transform_required",
        ),
        (
            lambda value: refingerprint(
                {
                    **value,
                    "contract": {
                        **value["contract"],
                        "features": [
                            {
                                **value["contract"]["features"][0],
                                "shape": [3],
                            },
                            value["contract"]["features"][1],
                        ],
                    },
                }
            ),
            "incompatible",
        ),
        (
            lambda value: make_snapshot("second", robot_type=None),
            "unknown",
        ),
    ],
)
def test_merge_plan_exposes_all_four_decisions(
    mutate: Any,
    expected: str,
) -> None:
    first = make_snapshot("first")
    second = mutate(make_snapshot("second"))

    plan = check_merge_compatibility([first, second])

    assert plan["decision"] == expected
    assert plan["official_operation"]["preconditions_satisfied"] is (expected == "direct")
    assert plan["official_operation"]["will_execute"] is False
    jsonschema.Draft202012Validator(schema("plan")).validate(plan)


def test_plan_is_deterministic_schema_valid_and_only_contains_pinned_argv(
    tmp_path: Path,
) -> None:
    first = make_snapshot("first")
    second = make_snapshot("second")
    first_path = tmp_path / "first-plan.json"
    second_path = tmp_path / "second-plan.json"

    first_plan = check_merge_compatibility(
        [first, second],
        output_path=str(first_path),
    )
    second_plan = check_merge_compatibility(
        [first, second],
        output_path=str(second_path),
    )

    assert first_plan == second_plan
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_plan["official_operation"]["package"] == "lerobot==0.6.0"
    assert first_plan["official_operation"]["tool"] == "lerobot-edit-dataset"
    assert isinstance(first_plan["official_operation"]["command"], list)
    command = first_plan["official_operation"]["command"]
    assert "lerobot[dataset]==0.6.0" in command
    assert "--new_repo_id" in command
    assert "OPENBOT_MERGED_OUTPUT" in command
    assert "--new_root" in command
    assert "OPENBOT_MERGED_ROOT" in command
    assert "--operation.roots" in command
    assert "--repo_id" not in command
    jsonschema.Draft202012Validator(schema("plan")).validate(first_plan)


def test_snapshot_json_and_local_dataset_builder_inputs_are_supported(
    tmp_path: Path,
) -> None:
    first = make_snapshot("first")
    second = make_snapshot("second")
    snapshot_path = tmp_path / "first.snapshot.json"
    snapshot_path.write_text(json.dumps(first), encoding="utf-8")
    local_dataset = tmp_path / "second-dataset"
    local_dataset.mkdir()
    calls: list[tuple[str, str, str]] = []

    def builder(path: str, *, checksum: str, integrity: str) -> dict[str, Any]:
        calls.append((path, checksum, integrity))
        return second

    plan = check_merge_compatibility(
        [snapshot_path, local_dataset],
        snapshot_builder=builder,
    )

    assert plan["decision"] == "direct"
    assert calls == [(str(local_dataset), "sha256", "full")]


def test_merge_rejects_forged_snapshot_component_fingerprint() -> None:
    first = make_snapshot("first")
    second = make_snapshot("second")
    first["component_fingerprints"]["totals"] = "0" * 64
    first["snapshot_fingerprint"] = dataset_fingerprint(
        {
            key: first[key]
            for key in (
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
    )

    with pytest.raises(DatasetArgumentError, match="component fingerprint"):
        check_merge_compatibility([first, second])


def test_duplicate_content_and_invalid_indexes_block_merge() -> None:
    first = make_snapshot("first")
    duplicate = make_snapshot("duplicate")
    duplicate["inventory"]["data"][0]["sha256"] = first["inventory"]["data"][0]["sha256"]
    duplicate = refingerprint(duplicate)

    duplicate_plan = check_merge_compatibility([first, duplicate])

    assert duplicate_plan["decision"] == "incompatible"
    duplicate_check = next(
        item for item in duplicate_plan["checks"] if item["check"] == "duplicates"
    )
    assert duplicate_check["status"] == "incompatible"

    invalid_indexes = make_snapshot("invalid", episodes=2)
    invalid_indexes["contract"]["episodes"][1]["episode_index"] = 3
    invalid_indexes = refingerprint(invalid_indexes)

    index_plan = check_merge_compatibility([first, invalid_indexes])

    assert index_plan["decision"] == "incompatible"
    collision_check = next(
        item for item in index_plan["checks"] if item["check"] == "index_shard_video_collisions"
    )
    assert collision_check["status"] == "incompatible"


def test_publication_profile_requires_license_evidence() -> None:
    first = make_snapshot("first", license_name=None)
    second = make_snapshot("second")

    plan = check_merge_compatibility(
        [first, second],
        profile="publication",
    )

    assert plan["decision"] == "unknown"
    license_check = next(item for item in plan["checks"] if item["check"] == "license")
    assert license_check["status"] == "unknown"


def test_task_remap_is_a_declared_transform() -> None:
    first = make_snapshot("first", task="pick")
    second = make_snapshot("second", task="grasp")

    plan = check_merge_compatibility(
        [first, second],
        task_remap={"grasp": "pick"},
    )

    assert plan["decision"] == "transform_required"
    task_check = next(item for item in plan["checks"] if item["check"] == "tasks")
    assert task_check["status"] == "transform_required"


@pytest.mark.parametrize(
    ("second_kwargs", "expected"),
    [
        ({"codec": "av1"}, "transform_required"),
        ({"is_depth_map": True}, "incompatible"),
        ({"complete_coverage": False}, "unknown"),
    ],
)
def test_camera_codec_depth_and_coverage_affect_compatibility(
    second_kwargs: dict[str, Any],
    expected: str,
) -> None:
    first = with_camera(make_snapshot("first"))
    second = with_camera(make_snapshot("second"), **second_kwargs)

    plan = check_merge_compatibility([first, second])

    assert plan["decision"] == expected
    media_check = next(item for item in plan["checks"] if item["check"] == "timing_and_media")
    assert media_check["status"] == expected


def test_verify_without_required_runners_or_operation_is_unverified() -> None:
    first = make_snapshot("first")
    second = make_snapshot("second")
    merged = make_merged_snapshot(first, second)

    receipt = verify_dataset_merge(
        merged,
        input_snapshots=[first, second],
        audit_runner=None,
        loader_runner=None,
    )

    assert receipt["verification_status"] == "unverified"
    statuses = {item["check"]: item["status"] for item in receipt["checks"]}
    assert statuses["full_post_audit"] == "unverified"
    assert statuses["loader_smoke"] == "unverified"
    assert statuses["operation_and_lineage"] == "unverified"
    jsonschema.Draft202012Validator(schema("receipt")).validate(receipt)


def test_verify_can_issue_deterministic_schema_valid_verified_receipt(
    tmp_path: Path,
) -> None:
    first = make_snapshot("first")
    second = make_snapshot("second")
    merged = make_merged_snapshot(first, second)
    audit_calls: list[dict[str, Any]] = []

    def audit_runner(target: Any, **kwargs: Any) -> dict[str, Any]:
        audit_calls.append({"target": target, **kwargs})
        return {
            "schema_version": "openbot.dataset_audit.v1",
            "summary": {"videos": 0, "error": 0, "warning": 0, "info": 0},
            "findings": [],
        }

    def loader_runner(target: Any) -> dict[str, Any]:
        assert target == merged
        return {"status": "passed", "loaded_episodes": 2}

    first_path = tmp_path / "first-receipt.json"
    second_path = tmp_path / "second-receipt.json"
    kwargs = {
        "input_snapshots": [first, second],
        "operation_record": operation_record([first, second], merged),
        "audit_runner": audit_runner,
        "loader_runner": loader_runner,
    }

    first_receipt = verify_dataset_merge(
        merged,
        output_path=str(first_path),
        **kwargs,
    )
    second_receipt = verify_dataset_merge(
        merged,
        output_path=str(second_path),
        **kwargs,
    )

    assert first_receipt == second_receipt
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_receipt["verification_status"] == "verified"
    assert all(item["status"] == "passed" for item in first_receipt["checks"])
    assert all(call["checksum"] == "sha256" for call in audit_calls)
    assert all(call["integrity"] == "full" for call in audit_calls)
    jsonschema.Draft202012Validator(schema("receipt")).validate(first_receipt)


def test_verify_catches_post_merge_contract_regression() -> None:
    first = make_snapshot("first")
    second = make_snapshot("second")
    merged = make_merged_snapshot(first, second, feature_regression=True)

    receipt = verify_dataset_merge(
        merged,
        input_snapshots=[first, second],
        operation_record=operation_record([first, second], merged),
        audit_runner=lambda _target, **_kwargs: {
            "schema_version": "openbot.dataset_audit.v1",
            "summary": {"error": 0},
            "findings": [],
        },
        loader_runner=lambda _target: {
            "status": "passed",
            "loaded_episodes": 2,
        },
    )

    assert receipt["verification_status"] == "unverified"
    failed = {item["check"] for item in receipt["checks"] if item["status"] == "failed"}
    assert {"semantic_reconciliation", "semantic_diff"} <= failed
    jsonschema.Draft202012Validator(schema("receipt")).validate(receipt)


def test_verify_catches_episode_semantic_loss() -> None:
    first = make_snapshot("first")
    second = make_snapshot("second", task="place")
    merged = make_merged_snapshot(first, second)
    merged["contract"]["episodes"][1]["tasks"] = ["pick"]
    merged = refingerprint(merged)

    receipt = verify_dataset_merge(
        merged,
        input_snapshots=[first, second],
        operation_record=operation_record([first, second], merged),
        audit_runner=lambda _target, **_kwargs: {
            "schema_version": "openbot.dataset_audit.v1",
            "summary": {"error": 0},
            "findings": [],
        },
        loader_runner=lambda _target: True,
    )

    assert receipt["verification_status"] == "unverified"
    reconciliation = next(
        item for item in receipt["checks"] if item["check"] == "semantic_reconciliation"
    )
    assert reconciliation["status"] == "failed"
    assert "episode_semantics" in {item["kind"] for item in reconciliation["evidence"]["failures"]}


def test_incomplete_audit_report_is_not_success_evidence() -> None:
    first = make_snapshot("first")
    second = make_snapshot("second")
    merged = make_merged_snapshot(first, second)

    receipt = verify_dataset_merge(
        merged,
        input_snapshots=[first, second],
        operation_record=operation_record([first, second], merged),
        audit_runner=lambda _target, **_kwargs: {},
        loader_runner=lambda _target: True,
    )

    audit_check = next(item for item in receipt["checks"] if item["check"] == "full_post_audit")
    assert receipt["verification_status"] == "unverified"
    assert audit_check["status"] == "unverified"


def test_bad_lineage_or_loader_result_cannot_be_verified() -> None:
    first = make_snapshot("first")
    second = make_snapshot("second")
    merged = make_merged_snapshot(first, second)
    record = operation_record([first, second], merged)
    record["output_snapshot_fingerprint"] = "0" * 64

    receipt = verify_dataset_merge(
        merged,
        input_snapshots=[first, second],
        operation_record=record,
        audit_runner=lambda _target, **_kwargs: {
            "schema_version": "openbot.dataset_audit.v1",
            "summary": {"error": 0},
            "findings": [],
        },
        loader_runner=lambda _target: False,
    )

    assert receipt["verification_status"] == "unverified"
    statuses = {item["check"]: item["status"] for item in receipt["checks"]}
    assert statuses["operation_and_lineage"] == "failed"
    assert statuses["loader_smoke"] == "failed"


def test_planning_and_verification_never_execute_the_merge_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a merge command was executed")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    first = make_snapshot("first")
    second = make_snapshot("second")
    merged = make_merged_snapshot(first, second)

    plan = check_merge_compatibility([first, second])
    receipt = verify_dataset_merge(
        merged,
        input_snapshots=[first, second],
        operation_record=operation_record([first, second], merged),
        audit_runner=lambda _target, **_kwargs: {
            "schema_version": "openbot.dataset_audit.v1",
            "summary": {"error": 0},
            "findings": [],
        },
        loader_runner=lambda _target: True,
    )

    assert plan["official_operation"]["will_execute"] is False
    assert receipt["merge_plan"]["official_operation"]["will_execute"] is False
