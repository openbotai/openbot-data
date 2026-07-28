from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pytest

import openbot_data.repair as repair_module
from openbot_data.errors import DatasetArgumentError
from openbot_data.preflight import dataset_fingerprint
from openbot_data.repair import (
    apply_dataset_repair,
    plan_dataset_repair,
    verify_dataset_repair,
)


def write_dataset(
    root: Path,
    *,
    stale_totals: bool = True,
    invalid_episode_index: bool = False,
) -> None:
    (root / "meta").mkdir(parents=True)
    info = {
        "codebase_version": "v2.1",
        "features": {},
        "total_episodes": 9 if stale_totals else (0 if invalid_episode_index else 2),
        "total_frames": 99 if stale_totals else (0 if invalid_episode_index else 5),
        "total_tasks": 8 if stale_totals else 1,
        "future_extension": {
            "preserve": True,
            "nested": {"value": "unknown-field"},
        },
    }
    (root / "meta" / "info.json").write_text(
        json.dumps(info, ensure_ascii=False),
        encoding="utf-8",
    )
    if invalid_episode_index:
        episodes = [
            {"episode_index": "ambiguous", "length": 2, "tasks": ["pick"]},
        ]
    else:
        episodes = [
            {"episode_index": 0, "length": 2, "tasks": ["pick"]},
            {"episode_index": 1, "length": 3, "tasks": ["pick"]},
        ]
        for episode_index in range(2):
            data_path = (
                root
                / "data"
                / "chunk-000"
                / f"episode_{episode_index:06d}.parquet"
            )
            data_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                import pyarrow as pa
                import pyarrow.parquet as parquet
            except ImportError:
                data_path.write_bytes(b"fixture")
            else:
                length = int(episodes[episode_index]["length"])
                parquet.write_table(
                    pa.table(
                        {
                            "episode_index": [episode_index] * length,
                            "frame_index": list(range(length)),
                        }
                    ),
                    data_path,
                )
    (root / "meta" / "episodes.jsonl").write_text(
        "".join(
            json.dumps(episode, ensure_ascii=False) + "\n"
            for episode in episodes
        ),
        encoding="utf-8",
    )
    (root / "meta" / "tasks.jsonl").write_text(
        json.dumps({"task_index": 0, "task": "pick"}) + "\n",
        encoding="utf-8",
    )


def byte_inventory(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def load_schema(name: str) -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[1]
        / "openbot_data"
        / "schemas"
        / name
    )
    return json.loads(path.read_text(encoding="utf-8"))


def validate_plan(plan: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(
        load_schema("dataset-repair-plan-v1.schema.json")
    ).validate(plan)


def validate_receipt(receipt: dict[str, Any]) -> None:
    jsonschema.Draft202012Validator(
        load_schema("dataset-repair-receipt-v1.schema.json")
    ).validate(receipt)


def refingerprint(plan: dict[str, Any]) -> None:
    plan["plan_fingerprint"] = dataset_fingerprint(
        {
            key: value
            for key, value in plan.items()
            if key != "plan_fingerprint"
        }
    )


def test_plan_is_read_only_deterministic_and_schema_valid(tmp_path: Path) -> None:
    source = tmp_path / "dataset"
    first_path = tmp_path / "first.plan.json"
    second_path = tmp_path / "second.plan.json"
    write_dataset(source)
    before = byte_inventory(source)

    first = plan_dataset_repair(str(source), output_path=str(first_path))
    second = plan_dataset_repair(str(source), output_path=str(second_path))

    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    assert byte_inventory(source) == before
    assert first["status"] == "repairable"
    assert {
        step["json_pointer"]: step["after"] for step in first["steps"]
    } == {
        "/total_episodes": 2,
        "/total_frames": 5,
        "/total_tasks": 1,
    }
    assert first["source"]["tree_sha256"] != first["source"][
        "expected_output_tree_sha256"
    ]
    validate_plan(first)


def test_apply_repairs_only_totals_preserves_unknown_fields_and_source(
    tmp_path: Path,
) -> None:
    pytest.importorskip("pyarrow.parquet", exc_type=ImportError)
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    before = byte_inventory(source)
    plan = plan_dataset_repair(str(source))

    receipt = apply_dataset_repair(
        str(source),
        plan,
        output_path=str(destination),
        loader_runner=lambda _path: {
            "success": True,
            "loader_version": "0.6.0",
        },
    )

    repaired = json.loads(
        (destination / "meta" / "info.json").read_text(encoding="utf-8")
    )
    assert repaired["total_episodes"] == 2
    assert repaired["total_frames"] == 5
    assert repaired["total_tasks"] == 1
    assert repaired["future_extension"] == {
        "preserve": True,
        "nested": {"value": "unknown-field"},
    }
    assert byte_inventory(source) == before
    assert receipt["status"] == "verified"
    assert receipt["verified"] is True
    assert receipt["hashes"]["before_tree_sha256"] == receipt["hashes"][
        "source_after_apply_tree_sha256"
    ]
    assert receipt["hashes"]["after_tree_sha256"] == receipt["hashes"][
        "expected_after_tree_sha256"
    ]
    assert {
        finding["code"] for finding in receipt["resolved_findings"]
    } == {
        "LEROBOT_EPISODE_COUNT_MISMATCH",
        "LEROBOT_FRAME_COUNT_MISMATCH",
        "LEROBOT_TASK_COUNT_MISMATCH",
    }
    assert len(receipt["executed_steps"]) == 3
    validate_receipt(receipt)


def test_verify_without_loader_runner_is_canonically_unverified(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    receipt_path = tmp_path / "verify.receipt.json"
    write_dataset(source)
    plan = plan_dataset_repair(str(source))
    apply_dataset_repair(str(source), plan, output_path=str(destination))

    first = verify_dataset_repair(
        str(destination),
        against=plan,
        output_path=str(receipt_path),
    )
    second = verify_dataset_repair(str(destination), against=plan)

    assert first == second
    assert first["status"] == "unverified"
    assert first["verified"] is False
    assert first["loader_verification"] == {
        "status": "unavailable",
        "runner": None,
        "details": {"reason_code": "loader_runner_not_provided"},
    }
    assert json.loads(receipt_path.read_text(encoding="utf-8")) == first
    validate_receipt(first)


def test_apply_refuses_stale_source_and_leaves_no_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    plan = plan_dataset_repair(str(source))
    (source / "meta" / "tasks.jsonl").write_text(
        json.dumps({"task_index": 0, "task": "changed"}) + "\n",
        encoding="utf-8",
    )
    changed = byte_inventory(source)

    with pytest.raises(DatasetArgumentError, match="stale"):
        apply_dataset_repair(
            str(source),
            plan,
            output_path=str(destination),
        )

    assert not destination.exists()
    assert byte_inventory(source) == changed


def test_apply_refuses_existing_target_without_touching_either_tree(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("existing", encoding="utf-8")
    source_before = byte_inventory(source)
    target_before = byte_inventory(destination)
    plan = plan_dataset_repair(str(source))

    with pytest.raises(DatasetArgumentError, match="already exists"):
        apply_dataset_repair(
            str(source),
            plan,
            output_path=str(destination),
        )

    assert byte_inventory(source) == source_before
    assert byte_inventory(destination) == target_before


def test_apply_failure_never_exposes_partial_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    source_before = byte_inventory(source)
    plan = plan_dataset_repair(str(source))
    original = repair_module._apply_step
    calls = 0

    def fail_after_one_step(
        root: Path,
        step: dict[str, Any],
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected staging failure")
        return original(root, step)

    monkeypatch.setattr(repair_module, "_apply_step", fail_after_one_step)

    with pytest.raises(RuntimeError, match="injected staging failure"):
        apply_dataset_repair(
            str(source),
            plan,
            output_path=str(destination),
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".dataset.fixed.repair-*"))
    assert byte_inventory(source) == source_before


def test_ambiguous_or_payload_mutating_step_is_never_executed(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    source_before = byte_inventory(source)
    plan = json.loads(json.dumps(plan_dataset_repair(str(source))))
    plan["steps"][0]["operation"] = "trim_idle_span"
    plan["steps"][0]["json_pointer"] = "/timestamp"
    refingerprint(plan)

    with pytest.raises(DatasetArgumentError, match="ambiguous|allowlist"):
        apply_dataset_repair(
            str(source),
            plan,
            output_path=str(destination),
        )

    assert not destination.exists()
    assert byte_inventory(source) == source_before


def test_plan_with_only_ambiguous_episode_evidence_cannot_apply(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(
        source,
        stale_totals=False,
        invalid_episode_index=True,
    )
    source_before = byte_inventory(source)

    plan = plan_dataset_repair(str(source))

    assert plan["steps"] == []
    assert any(
        item["reason_code"] == "episode_metadata_is_ambiguous"
        for item in plan["unresolved_findings"]
    )
    with pytest.raises(DatasetArgumentError, match="no unambiguous"):
        apply_dataset_repair(
            str(source),
            plan,
            output_path=str(destination),
        )
    assert not destination.exists()
    assert byte_inventory(source) == source_before
    validate_plan(plan)


def test_verify_detects_unplanned_output_changes(tmp_path: Path) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    plan = plan_dataset_repair(str(source))
    apply_dataset_repair(str(source), plan, output_path=str(destination))
    (destination / "meta" / "unexpected.json").write_text(
        '{"unexpected":true}\n',
        encoding="utf-8",
    )

    receipt = verify_dataset_repair(
        str(destination),
        against=plan,
        loader_runner=lambda _path: True,
    )

    assert receipt["status"] == "failed"
    assert receipt["verified"] is False
    assert receipt["verification_checks"]["expected_output_hash"] is False
    validate_receipt(receipt)


def test_loader_mutation_is_reaudited_and_cannot_be_verified(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    plan = plan_dataset_repair(str(source))

    def mutating_loader(path: str) -> bool:
        (Path(path) / "meta" / "loader-side-effect.json").write_text(
            '{"unexpected":true}\n',
            encoding="utf-8",
        )
        return True

    receipt = apply_dataset_repair(
        str(source),
        plan,
        output_path=str(destination),
        loader_runner=mutating_loader,
    )

    assert receipt["status"] == "failed"
    assert receipt["verified"] is False
    assert receipt["loader_verification"]["status"] == "passed"
    assert receipt["verification_checks"]["expected_output_hash"] is False
    assert receipt["hashes"]["after_tree_sha256"] != receipt["hashes"][
        "expected_after_tree_sha256"
    ]
    validate_receipt(receipt)


@pytest.mark.parametrize(
    "task_rows",
    [
        [],
        [{"task_index": 0, "task": "place"}],
    ],
)
def test_task_total_requires_complete_episode_to_ledger_mapping(
    tmp_path: Path,
    task_rows: list[dict[str, Any]],
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    (source / "meta" / "tasks.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in task_rows),
        encoding="utf-8",
    )

    plan = plan_dataset_repair(str(source))

    assert "/total_tasks" not in {
        step["json_pointer"] for step in plan["steps"]
    }
    assert any(
        item["reason_code"] == "task_mapping_is_ambiguous"
        for item in plan["unresolved_findings"]
    )
    apply_dataset_repair(
        str(source),
        plan,
        output_path=str(destination),
    )
    repaired = json.loads(
        (destination / "meta" / "info.json").read_text(encoding="utf-8")
    )
    assert repaired["total_tasks"] == 8


def test_mixed_jsonl_and_parquet_ledgers_are_not_combined(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    (source / "meta" / "episodes").mkdir()
    (source / "meta" / "episodes" / "legacy.parquet").write_bytes(b"not parquet")
    (source / "meta" / "tasks.parquet").write_bytes(b"not parquet")

    plan = plan_dataset_repair(str(source))

    assert plan["steps"] == []
    assert {
        item["reason_code"] for item in plan["unresolved_findings"]
    } >= {
        "episode_metadata_is_ambiguous",
        "frame_extent_is_ambiguous",
        "task_mapping_is_ambiguous",
    }
    with pytest.raises(DatasetArgumentError, match="no unambiguous"):
        apply_dataset_repair(
            str(source),
            plan,
            output_path=str(destination),
        )
    assert not destination.exists()
    validate_plan(plan)


def test_float_totals_are_repaired_to_strict_json_integers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source, stale_totals=False)
    info_path = source / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info.update(
        {
            "total_episodes": 2.0,
            "total_frames": 5.0,
            "total_tasks": 1.0,
        }
    )
    info_path.write_text(json.dumps(info), encoding="utf-8")

    plan = plan_dataset_repair(str(source))

    assert len(plan["steps"]) == 3
    apply_dataset_repair(
        str(source),
        plan,
        output_path=str(destination),
    )
    repaired = json.loads(
        (destination / "meta" / "info.json").read_text(encoding="utf-8")
    )
    assert type(repaired["total_episodes"]) is int
    assert type(repaired["total_frames"]) is int
    assert type(repaired["total_tasks"]) is int


def test_atomic_publish_never_replaces_a_raced_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    source_before = byte_inventory(source)
    plan = plan_dataset_repair(str(source))
    original = repair_module._publish_directory_no_replace

    def race_target(staged: Path, target: Path) -> None:
        target.mkdir()
        (target / "keep.txt").write_text("raced", encoding="utf-8")
        original(staged, target)

    monkeypatch.setattr(
        repair_module,
        "_publish_directory_no_replace",
        race_target,
    )

    with pytest.raises(DatasetArgumentError, match="appeared during staging"):
        apply_dataset_repair(
            str(source),
            plan,
            output_path=str(destination),
        )

    assert (destination / "keep.txt").read_text(encoding="utf-8") == "raced"
    assert byte_inventory(source) == source_before
    assert not list(tmp_path.glob(".dataset.fixed.repair-*"))


def test_unknown_major_adapter_never_receives_automatic_repair(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dataset"
    destination = tmp_path / "dataset.fixed"
    write_dataset(source)
    info_path = source / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["codebase_version"] = "v4.0"
    info_path.write_text(json.dumps(info), encoding="utf-8")

    plan = plan_dataset_repair(str(source))

    assert plan["source"]["adapter"] == "lerobot_unknown"
    assert plan["steps"] == []
    assert any(
        item["reason_code"] == "repair_adapter_is_not_exactly_supported"
        for item in plan["unresolved_findings"]
    )
    with pytest.raises(DatasetArgumentError, match="no unambiguous"):
        apply_dataset_repair(
            str(source),
            plan,
            output_path=str(destination),
        )
    assert not destination.exists()
