from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from openbot_data.diff import diff_dataset_snapshots
from openbot_data.schema import schema_path

EXAMPLES = {
    "audit.json": "audit",
    "snapshot.json": "snapshot",
    "diff.json": "diff",
    "readiness.json": "readiness",
    "catalog-evidence.json": "catalog_evidence",
    "repair-plan.json": "repair_plan",
    "repair-receipt.json": "repair_receipt",
    "merge-plan.json": "merge_plan",
    "merge-receipt.json": "merge_receipt",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_all_v003_examples_match_their_packaged_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    examples = root / "docs" / "examples" / "v0.0.3"
    assert {path.name for path in examples.glob("*.json")} == set(EXAMPLES)

    for filename, schema_name in EXAMPLES.items():
        with schema_path(schema_name) as path:
            schema = _load(path)
        jsonschema.Draft202012Validator(schema).validate(
            _load(examples / filename)
        )


def test_v003_examples_are_portable_and_snapshot_is_strictly_canonical() -> None:
    root = Path(__file__).resolve().parents[1]
    examples = root / "docs" / "examples" / "v0.0.3"
    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(examples.glob("*.json"))
    )
    assert str(root) not in serialized
    assert "/private/" not in serialized
    assert "/tmp/" not in serialized
    assert "Bearer " not in serialized
    assert "hf_" not in serialized

    snapshot = _load(examples / "snapshot.json")
    result = diff_dataset_snapshots(snapshot, snapshot)
    assert result["classification"] == "unchanged"
