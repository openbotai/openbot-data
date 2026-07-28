from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from openbot_data import prepare_dataset, schema_path
from openbot_data.errors import DatasetArgumentError
from openbot_data.preflight import dataset_fingerprint
from openbot_data.readiness import (
    evaluate_dataset_readiness,
    load_readiness_profile,
    render_readiness_markdown,
)
from openbot_data.triage import analyze_advisory_signals, triage_findings


def seal_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot = copy.deepcopy(snapshot)
    snapshot["totals"]["features"] = len(snapshot["contract"]["features"])
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
        key: dataset_fingerprint(value) for key, value in sorted(components.items())
    }
    payload = {
        key: snapshot[key]
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
    snapshot["snapshot_fingerprint"] = dataset_fingerprint(payload)
    return snapshot


def snapshot_artifact(
    *,
    integrity: str = "full",
    features: list[dict[str, Any]] | None = None,
    source_kind: str = "local",
    requested_revision: str | None = None,
    resolved_revision: str | None = None,
) -> dict[str, Any]:
    snapshot = {
        "schema_version": "openbot.dataset_snapshot.v1",
        "fingerprint_version": "openbot.dataset_snapshot.fingerprint.v1",
        "source": {
            "kind": source_kind,
            "locator": ("hf://datasets/org/name" if source_kind == "hf_hub" else "dataset://."),
            "requested_revision": (
                requested_revision or "main" if source_kind == "hf_hub" else None
            ),
            "resolved_revision": resolved_revision,
        },
        "format": {
            "input_format": "lerobot",
            "adapter": "lerobot_v30",
            "dataset_format_version": "v3.0",
            "compatibility_target": "lerobot==0.6.0",
            "metadata": {
                "codebase_version": "v3.0",
                "license": "apache-2.0",
                "tags": ["robotics"],
                "task_categories": ["robotics"],
            },
        },
        "contract": {
            "features": features or [],
            "tasks": [
                {
                    "task_index": 0,
                    "task": "pick",
                    "source": None,
                    "extensions": {},
                },
                {
                    "task_index": 1,
                    "task": "place",
                    "source": None,
                    "extensions": {},
                },
            ],
            "episodes": [
                {
                    "episode_index": 0,
                    "source_ordinal": 0,
                    "length": 2,
                    "tasks": ["pick"],
                    "video_files": ["videos/top.mp4"],
                    "video_segments": [
                        {
                            "video_key": "observation.images.top",
                            "path": "videos/top.mp4",
                        }
                    ],
                    "source": None,
                    "data_relation": None,
                    "extensions": {},
                    "content_sha256": None,
                    "content_rows": None,
                },
                {
                    "episode_index": 1,
                    "source_ordinal": 1,
                    "length": 20,
                    "tasks": ["place"],
                    "video_files": [],
                    "video_segments": [],
                    "source": None,
                    "data_relation": None,
                    "extensions": {},
                    "content_sha256": None,
                    "content_rows": None,
                },
            ],
            "video_streams": [
                {
                    "key": "observation.images.top",
                    "paths": ["videos/top.mp4"],
                    "resolutions": [[640, 480]],
                    "fps": [10.0],
                    "frame_count": 22,
                    "duration_seconds": 2.2,
                    "size_bytes": 10,
                }
            ],
        },
        "inventory": {"metadata": [], "data": [], "media": []},
        "totals": {
            "episodes": 2,
            "tasks": 2,
            "features": len(features or []),
            "video_streams": 1,
            "metadata_shards": 0,
            "data_shards": 0,
            "media_shards": 0,
            "frames": 22,
            "duration_seconds": 2.2,
            "size_bytes": 10,
        },
        "coverage": {
            "requested_integrity": integrity,
            "checksum": "sha256",
            "capabilities": [],
            "completed_capabilities": [],
            "skipped_capabilities": [],
            "selection": {
                "episodes": [0, 1],
                "cameras": ["observation.images.top"],
                "metadata_shards": [],
                "data_shards": [],
                "media_shards": [],
            },
            "totals": {
                "episodes": 2,
                "cameras": 1,
                "metadata_shards": 0,
                "data_shards": 0,
                "media_shards": 0,
            },
        },
        "component_fingerprints": {},
        "tool": {"name": "openbot-data", "version": "0.0.2"},
        "snapshot_fingerprint": "a" * 64,
    }
    return seal_snapshot(snapshot)


def audit_artifact(
    capabilities: list[str],
    *,
    integrity: str = "full",
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "openbot.dataset_audit.v1",
        "input_format": "lerobot",
        "summary": {"videos": 0, "error": 0, "warning": 0, "info": 0},
        "rule_pack_version": "openbot.dataset_audit.rules.v1",
        "coverage": {
            "capabilities": [
                {
                    "capability": capability,
                    "status": "complete",
                    "integrity": integrity,
                    "checked": 1,
                    "total": 1,
                    "selected": [],
                    "omitted": [],
                }
                for capability in capabilities
            ]
        },
        "skipped_checks": [],
        "findings": [],
    }
    if source is not None:
        result["source"] = dict(source)
        result["coverage"]["source"] = {"requested_integrity": integrity}
    return result


def required_capabilities(profile: str) -> list[str]:
    return list(load_readiness_profile(profile)["required_capabilities"])


def validate_readiness(value: dict[str, Any]) -> None:
    with schema_path("readiness") as path:
        schema = json.loads(path.read_text())
    jsonschema.Draft202012Validator(schema).validate(value)


def test_complete_lerobot_core_contract_is_ready_and_deterministic() -> None:
    snapshot = snapshot_artifact()
    audit = audit_artifact(required_capabilities("lerobot-core"))

    first = evaluate_dataset_readiness(
        ".",
        profile="lerobot-core",
        dataset_snapshot=snapshot,
        audit_result=audit,
    )
    second = evaluate_dataset_readiness(
        ".",
        profile="lerobot-core",
        dataset_snapshot=snapshot,
        audit_result=audit,
    )

    assert first == second
    assert first["status"] == "READY"
    assert first["blocking_findings"] == []
    assert first["coverage"]["missing_capabilities"] == []
    assert "score" not in json.dumps(first).lower()
    validate_readiness(first)


def test_metadata_only_training_gate_is_partial_and_never_ready() -> None:
    snapshot = snapshot_artifact(integrity="metadata")
    audit = audit_artifact(required_capabilities("training-common"))
    for item in audit["coverage"]["capabilities"]:
        item["integrity"] = "metadata"

    result = evaluate_dataset_readiness(
        ".",
        profile="training-common",
        integrity="metadata",
        dataset_snapshot=snapshot,
        audit_result=audit,
    )

    assert result["status"] == "BLOCKED"
    assert any(
        finding["code"] == "READINESS_FEATURE_MISSING" for finding in result["blocking_findings"]
    )
    assert {item["capability"] for item in result["coverage"]["missing_capabilities"]} == {
        "integrity.full"
    }


def test_policy_config_overrides_builtin_and_enforces_action_dimension() -> None:
    snapshot = snapshot_artifact(
        features=[
            {
                "key": "action",
                "dtype": "float32",
                "shape": [6],
                "names": [],
                "metadata": {"normalization": "mean_std"},
            }
        ]
    )
    policy = {
        "id": "checkpoint-42",
        "required_integrity": "full",
        "required_capabilities": ["format.contract"],
        "action_dim": 7,
        "normalization": "mean_std",
    }

    result = evaluate_dataset_readiness(
        ".",
        profile="lerobot-core",
        policy_config=policy,
        dataset_snapshot=snapshot,
        audit_result=audit_artifact(["format.contract"]),
    )

    assert result["profile"]["id"] == "checkpoint-42"
    assert result["profile"]["contract_source"] == "policy_config"
    assert result["status"] == "BLOCKED"
    assert {finding["code"] for finding in result["blocking_findings"]} == {
        "READINESS_ACTION_DIMENSION_MISMATCH"
    }


def test_missing_required_coverage_without_blocker_is_partial() -> None:
    result = evaluate_dataset_readiness(
        ".",
        profile="lerobot-core",
        dataset_snapshot=snapshot_artifact(),
        audit_result=audit_artifact(["format.contract"]),
    )

    assert result["status"] == "PARTIAL"
    assert result["blocking_findings"] == []
    assert result["coverage"]["missing_capabilities"]


def test_hf_publication_requires_pinned_revision() -> None:
    capabilities = required_capabilities("hf-publication")
    local = evaluate_dataset_readiness(
        ".",
        profile="hf-publication",
        integrity="metadata",
        dataset_snapshot=snapshot_artifact(integrity="metadata"),
        audit_result=audit_artifact(capabilities, integrity="metadata"),
    )
    pinned_snapshot = snapshot_artifact(
        integrity="metadata",
        source_kind="hf_hub",
        resolved_revision="b" * 40,
    )
    pinned = evaluate_dataset_readiness(
        ".",
        profile="hf-publication",
        integrity="metadata",
        dataset_snapshot=pinned_snapshot,
        audit_result=audit_artifact(
            capabilities,
            integrity="metadata",
            source=pinned_snapshot["source"],
        ),
    )

    assert local["status"] == "BLOCKED"
    assert "READINESS_HUB_REVISION_UNPINNED" in {
        item["code"] for item in local["blocking_findings"]
    }
    assert pinned["status"] == "READY"


def test_triage_groups_locations_and_orders_blockers_first() -> None:
    findings = [
        {
            "code": "WARN",
            "severity": "warning",
            "layer": "data",
            "message": "warning",
            "location": {"episode_index": 2},
            "evidence": {},
        },
        {
            "code": "BLOCK",
            "severity": "error",
            "layer": "alignment",
            "message": "blocker",
            "location": {"episode_index": 2},
            "evidence": {},
        },
    ]

    result = triage_findings(reversed(findings))

    assert len(result) == 1
    assert result[0]["group"] == {
        "kind": "episode",
        "location": {"episode_index": 2},
    }
    assert [item["code"] for item in result[0]["blocking_findings"]] == ["BLOCK"]
    assert [item["code"] for item in result[0]["warnings"]] == ["WARN"]


def test_advisory_signals_expose_raw_threshold_and_coverage() -> None:
    snapshot = snapshot_artifact(
        features=[
            {
                "key": "action",
                "dtype": "float32",
                "shape": [2],
                "names": [],
                "metadata": {},
            }
        ]
    )
    snapshot["contract"]["episodes"][0]["tasks"] = ["pick", "place"]
    snapshot["contract"]["episodes"][0]["video_segments"].append(
        {
            "video_key": "observation.images.wrist",
            "path": "videos/wrist.mp4",
        }
    )
    snapshot["contract"]["episodes"][1]["video_segments"] = [
        {
            "video_key": "observation.images.top",
            "path": "videos/top.mp4",
        }
    ]

    signals = analyze_advisory_signals(
        snapshot,
        thresholds={
            "short_episode_seconds": 0.5,
            "short_episode_threshold_source": "test-contract",
            "near_zero_variance": 1e-9,
            "idle_action_span_frames": 3,
            "action_range": [-1.0, 1.0],
        },
        measurements={
            "features": {
                "action": {
                    "variance": [0.0, 0.2],
                    "minimum": -1.0,
                    "maximum": 0.5,
                    "idle_spans": [
                        {
                            "episode_index": 0,
                            "frame_from": 2,
                            "frame_to": 5,
                        }
                    ],
                    "action_semantics": "normalized_joint_delta",
                    "coverage": {"checked": 20, "total": 20},
                }
            }
        },
    )

    codes = {signal["code"] for signal in signals}
    assert {
        "ADVISORY_SHORT_EPISODE",
        "ADVISORY_NEAR_ZERO_VARIANCE_DIMENSION",
        "ADVISORY_IDLE_ACTION_SPAN",
        "ADVISORY_ACTION_SATURATION",
        "ADVISORY_TASK_COVERAGE_IMBALANCE",
        "ADVISORY_CAMERA_COVERAGE_IMBALANCE",
    } <= codes
    for signal in signals:
        assert "raw_value" in signal
        assert "threshold" in signal
        assert "threshold_source" in signal
        assert "coverage" in signal
    idle = next(
        signal for signal in signals if signal["code"] == "ADVISORY_IDLE_ACTION_SPAN"
    )
    trim_plan = idle["evidence"]["trim_plan"]
    assert trim_plan["schema_version"] == "openbot.idle_trim_plan.v1"
    assert trim_plan["execution_status"] == "review_required_not_executable"
    assert trim_plan["mutates_source"] is False
    assert trim_plan["scope"] == {
        "episode_index": 0,
        "feature_key": "action",
        "frame_from": 2,
        "frame_to": 5,
    }
    assert "video_segments_all_cameras" in trim_plan["required_synchronization"]
    assert trim_plan["verification"] == [
        "full_post_audit",
        "official_loader_smoke",
        "semantic_snapshot_diff",
    ]


def test_markdown_is_a_stable_projection() -> None:
    result = evaluate_dataset_readiness(
        ".",
        profile="lerobot-core",
        dataset_snapshot=snapshot_artifact(),
        audit_result=audit_artifact(required_capabilities("lerobot-core")),
    )

    first = render_readiness_markdown(result)
    second = render_readiness_markdown(result)

    assert first == second
    assert "Status: `READY`" in first
    assert result["snapshot_fingerprint"] in first


@pytest.mark.parametrize(
    "policy",
    [
        {"id": "strict", "unknown_field": True},
        {"id": "strict", "requires_hf_revision": "true"},
        {
            "id": "strict",
            "required_features": [{"key": "action", "shape_min_rank": 0}],
        },
        {
            "id": "strict",
            "any_feature_groups": [{"id": "camera", "patterns": "observation.images.*"}],
        },
        {
            "id": "strict",
            "thresholds": {"action_range": [1.0, -1.0]},
        },
        {
            "id": "strict",
            "policy_defaults": {"undeclared_default": 1},
        },
    ],
)
def test_policy_config_rejects_unknown_or_malformed_contracts(
    policy: dict[str, Any],
) -> None:
    with pytest.raises(DatasetArgumentError, match="Invalid policy config"):
        evaluate_dataset_readiness(
            ".",
            policy_config=policy,
            dataset_snapshot=snapshot_artifact(),
            audit_result=audit_artifact([]),
        )


def test_external_snapshot_requires_valid_schema_and_fingerprints() -> None:
    snapshot = snapshot_artifact()
    audit = audit_artifact([])

    malformed = copy.deepcopy(snapshot)
    del malformed["coverage"]["capabilities"]
    with pytest.raises(DatasetArgumentError, match="JSON Schema"):
        evaluate_dataset_readiness(
            ".",
            policy_config={"id": "external"},
            dataset_snapshot=malformed,
            audit_result=audit,
        )

    tampered = copy.deepcopy(snapshot)
    tampered["format"]["adapter"] = "tampered"
    with pytest.raises(DatasetArgumentError, match="fingerprint"):
        evaluate_dataset_readiness(
            ".",
            policy_config={"id": "external"},
            dataset_snapshot=tampered,
            audit_result=audit,
        )

    inconsistent = copy.deepcopy(snapshot)
    inconsistent["totals"]["tasks"] = 9
    inconsistent = seal_snapshot(inconsistent)
    with pytest.raises(DatasetArgumentError, match="totals"):
        evaluate_dataset_readiness(
            ".",
            policy_config={"id": "external"},
            dataset_snapshot=inconsistent,
            audit_result=audit,
        )


def test_external_audit_requires_schema_summary_and_identity_consistency() -> None:
    snapshot = snapshot_artifact()

    malformed = audit_artifact([])
    malformed["coverage"]["unexpected"] = True
    with pytest.raises(DatasetArgumentError, match="JSON Schema"):
        evaluate_dataset_readiness(
            ".",
            policy_config={"id": "external"},
            dataset_snapshot=snapshot,
            audit_result=malformed,
        )

    wrong_summary = audit_artifact([])
    wrong_summary["summary"]["warning"] = 1
    with pytest.raises(DatasetArgumentError, match="summary"):
        evaluate_dataset_readiness(
            ".",
            policy_config={"id": "external"},
            dataset_snapshot=snapshot,
            audit_result=wrong_summary,
        )

    duplicate_capability = audit_artifact(["format.contract", "format.contract"])
    with pytest.raises(DatasetArgumentError, match="duplicate capability"):
        evaluate_dataset_readiness(
            ".",
            policy_config={"id": "external"},
            dataset_snapshot=snapshot,
            audit_result=duplicate_capability,
        )


@pytest.mark.parametrize(
    "revision",
    ["main", "a" * 39, "A" * 40],
)
def test_external_hub_snapshot_requires_lowercase_commit_sha(
    revision: str,
) -> None:
    snapshot = snapshot_artifact(
        source_kind="hf_hub",
        resolved_revision=revision,
    )
    with pytest.raises(DatasetArgumentError, match="JSON Schema|immutable"):
        evaluate_dataset_readiness(
            ".",
            policy_config={"id": "hub"},
            dataset_snapshot=snapshot,
            audit_result=audit_artifact(
                [],
                source=snapshot["source"],
            ),
        )


def test_external_hub_snapshot_and_audit_identity_must_match() -> None:
    snapshot = snapshot_artifact(
        source_kind="hf_hub",
        resolved_revision="c" * 40,
    )
    audit = audit_artifact([], source=snapshot["source"])
    audit["source"]["locator"] = "hf://datasets/other/repository"

    with pytest.raises(DatasetArgumentError, match="identity mismatch"):
        evaluate_dataset_readiness(
            ".",
            policy_config={"id": "hub"},
            dataset_snapshot=snapshot,
            audit_result=audit,
        )


@pytest.mark.parametrize(
    ("shape", "expected_status"),
    [
        ([], "BLOCKED"),
        ([2, 5], "BLOCKED"),
        (["unknown"], "BLOCKED"),
        ([2, 4], "READY"),
    ],
)
def test_feature_rank_and_width_contracts_fail_closed(
    shape: list[Any],
    expected_status: str,
) -> None:
    policy = {
        "id": "shape-contract",
        "required_capabilities": [],
        "required_features": [
            {
                "key": "action",
                "dtype": "float32",
                "shape_min_rank": 1,
                "shape_last_dimension_max": 4,
            }
        ],
    }
    result = evaluate_dataset_readiness(
        ".",
        policy_config=policy,
        dataset_snapshot=snapshot_artifact(
            features=[
                {
                    "key": "action",
                    "dtype": "float32",
                    "shape": shape,
                    "names": [],
                    "metadata": {},
                }
            ]
        ),
        audit_result=audit_artifact([]),
    )

    assert result["status"] == expected_status
    if expected_status == "BLOCKED":
        assert "READINESS_FEATURE_SHAPE_MISMATCH" in {
            finding["code"] for finding in result["blocking_findings"]
        }


def test_hub_publication_uses_clean_publication_metadata_without_leaking_values() -> None:
    snapshot = snapshot_artifact(
        integrity="metadata",
        source_kind="hf_hub",
        resolved_revision="d" * 40,
    )
    del snapshot["format"]["metadata"]["license"]
    snapshot = seal_snapshot(snapshot)
    audit = audit_artifact(
        required_capabilities("hf-publication"),
        integrity="metadata",
        source=snapshot["source"],
    )

    result = evaluate_dataset_readiness(
        ".",
        profile="hf-publication",
        integrity="metadata",
        dataset_snapshot=snapshot,
        audit_result=audit,
        publication_metadata={
            "license": "apache-2.0",
            "tags": ["robotics"],
            "task_categories": ["robotics"],
            "private_token": "must-not-leak",
        },
    )

    assert result["status"] == "READY"
    assert result["profile"]["metadata_source"] == "publication_metadata"
    assert "must-not-leak" not in json.dumps(result)
    validate_readiness(result)


def test_local_readiness_ignores_publication_metadata_override() -> None:
    snapshot = snapshot_artifact()
    del snapshot["format"]["metadata"]["license"]
    snapshot = seal_snapshot(snapshot)
    result = evaluate_dataset_readiness(
        ".",
        policy_config={
            "id": "local-metadata",
            "required_capabilities": [],
            "metadata_requirements": [{"key": "license", "severity": "error"}],
        },
        dataset_snapshot=snapshot,
        audit_result=audit_artifact([]),
        publication_metadata={"license": "should-not-override-local"},
    )

    assert result["status"] == "BLOCKED"
    assert result["profile"]["metadata_source"] == "snapshot_format_metadata"


def _write_advisory_dataset(root: Path) -> None:
    pa = pytest.importorskip("pyarrow", exc_type=ImportError)
    parquet = pytest.importorskip(
        "pyarrow.parquet",
        exc_type=ImportError,
    )
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "fps": 10,
                "total_episodes": 2,
                "total_frames": 4,
                "total_tasks": 1,
                "features": {
                    "action": {"dtype": "float32", "shape": [1]},
                },
                "data_path": ("data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"),
                "video_path": None,
            }
        ),
        encoding="utf-8",
    )
    parquet.write_table(
        pa.table({"task_index": [0], "task": ["pick"]}),
        root / "meta/tasks.parquet",
    )
    parquet.write_table(
        pa.table(
            {
                "episode_index": [0, 1],
                "length": [2, 2],
                "tasks": [["pick"], ["pick"]],
                "meta/episodes/chunk_index": [0, 0],
                "meta/episodes/file_index": [0, 0],
                "data/chunk_index": [0, 0],
                "data/file_index": [0, 0],
                "dataset_from_index": [0, 2],
                "dataset_to_index": [2, 4],
            }
        ),
        root / "meta/episodes/chunk-000/file-000.parquet",
    )
    data_path = root / "data/chunk-000/file-000.parquet"
    data_path.parent.mkdir(parents=True)
    action_type = pa.list_(pa.float32(), 1)
    parquet.write_table(
        pa.table(
            {
                "index": [0, 1, 2, 3],
                "episode_index": [0, 0, 1, 1],
                "frame_index": [0, 1, 0, 1],
                "timestamp": [0.0, 0.1, 0.0, 0.1],
                "task_index": [0, 0, 0, 0],
                "action": pa.array(
                    [[-1.0], [-1.0], [-1.0], [-1.0]],
                    type=action_type,
                ),
            }
        ),
        data_path,
    )
    (root / "meta/stats.json").write_text(
        json.dumps(
            {
                "action": {
                    "min": [-1.0],
                    "max": [-1.0],
                    "mean": [-1.0],
                    "std": [0.0],
                    "count": 4,
                }
            }
        ),
        encoding="utf-8",
    )


def test_prepare_to_readiness_uses_validation_measurements_automatically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    _write_advisory_dataset(root)
    prepared = prepare_dataset(
        str(root),
        input_format="lerobot",
        checksum="sha256",
        integrity="full",
    )

    assert prepared.validation_result is not None
    assert prepared.validation_result.measurements["numeric_features"]
    result = evaluate_dataset_readiness(
        str(root),
        policy_config={
            "id": "validation-advisories",
            "required_integrity": "full",
            "required_capabilities": [],
            "thresholds": {
                "near_zero_variance": 0.0,
                "idle_action_span_frames": 2,
                "action_range": [-1.0, 1.0],
            },
        },
        input_format="lerobot",
        integrity="full",
        prepared=prepared,
    )

    codes = {signal["code"] for signal in result["advisory_signals"]}
    assert {
        "ADVISORY_NEAR_ZERO_VARIANCE_DIMENSION",
        "ADVISORY_IDLE_ACTION_SPAN",
        "ADVISORY_ACTION_SATURATION",
        "ADVISORY_DUPLICATE_EPISODE_CONTENT",
    } <= codes
