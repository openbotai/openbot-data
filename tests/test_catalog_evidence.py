import json
from pathlib import Path

import cv2
import jsonschema
import numpy as np
import pytest
from typer.testing import CliRunner

from openbot_data import build_catalog_evidence, schema_path
from openbot_data.catalog_evidence import (
    canonical_evidence_sha256,
    canonical_evidence_tree,
)
from openbot_data.cli import app


def make_video(path: Path, frames: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        5.0,
        (16, 16),
    )
    if not writer.isOpened():
        pytest.skip("OpenCV video writer is unavailable in this environment")
    for frame_index in range(frames):
        frame = np.full((16, 16, 3), frame_index * 30, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def all_object_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            key
            for child in value.values()
            for key in all_object_keys(child)
        }
    if isinstance(value, list):
        return {key for child in value for key in all_object_keys(child)}
    return set()


def test_canonical_evidence_tree_has_fixed_cross_language_digest() -> None:
    fixture = {
        "null": None,
        "bool": True,
        "string": "机器人",
        "safe_max": 9007199254740991,
        "negative": -42,
        "float": 1.5,
        "negative_float": -0.25,
        "array": [None, False, "é", 7, 0.1],
        "object": {"中": 3, "é": 2, "z": 1, "a": 4},
    }

    tree = canonical_evidence_tree(fixture)

    assert [entry[0] for entry in tree[1]] == [
        "array",
        "bool",
        "float",
        "negative",
        "negative_float",
        "null",
        "object",
        "safe_max",
        "string",
    ]
    object_tree = dict(tree[1])["object"]
    assert [entry[0] for entry in object_tree[1]] == ["a", "z", "é", "中"]
    assert canonical_evidence_sha256(fixture) == (
        "bd6756335eaeb568303a0824b4e06dc864ed93694fb83aac93fcf9218f6bf0af"
    )


@pytest.mark.parametrize(
    "value",
    [
        9007199254740992,
        -9007199254740992,
        float(9007199254740992),
        float("inf"),
        float("-inf"),
        float("nan"),
    ],
)
def test_canonical_evidence_rejects_unsafe_or_nonfinite_numbers(value: object) -> None:
    with pytest.raises(ValueError):
        canonical_evidence_tree(value)


def test_canonical_evidence_uses_exact_numeric_encodings() -> None:
    assert canonical_evidence_tree(1.0) == ["number", "integer", "1"]
    assert canonical_evidence_tree(-0.0) == ["number", "integer", "0"]
    assert canonical_evidence_tree(1.5) == [
        "number",
        "float64",
        "3ff8000000000000",
    ]


def test_catalog_evidence_is_deterministic_valid_and_score_free(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    make_video(dataset / "camera" / "episode.avi")
    arguments = {
        "dataset_id": "fixture/robot-video",
        "checked_at": "2026-07-28T12:30:45+08:00",
        "source_locator": "https://example.com/datasets/fixture?token=redacted#section",
        "output_path": str(first_path),
    }

    first = build_catalog_evidence(str(dataset), **arguments)
    arguments["output_path"] = str(second_path)
    second = build_catalog_evidence(str(dataset), **arguments)

    assert first == second
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first["schema_version"] == "catalog-evidence-v1"
    assert first["checked_at"] == "2026-07-28T04:30:45Z"
    assert first["dataset"]["source_locator"] == "https://example.com/datasets/fixture"
    assert first["dataset"]["snapshot_fingerprint"]
    assert first["evidence_maturity"] == "sample_verified"
    assert first["coverage"]["videos"]["checksummed"] == 1
    assert first["facts"]["dataset.profile_readiness"]["value"]["status"] == "PARTIAL"
    assert {
        "score",
        "overall",
        "dimensions",
        "evaluation",
    }.isdisjoint(all_object_keys(first))
    assert str(tmp_path) not in json.dumps(first)

    with schema_path("catalog_evidence") as path:
        schema = json.loads(path.read_text())
    jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    ).validate(first)


def test_catalog_evidence_fingerprint_changes_with_covered_fact(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    make_video(dataset / "camera" / "episode.avi", frames=4)
    first = build_catalog_evidence(
        str(dataset),
        dataset_id="fixture",
        checked_at="2026-07-28T00:00:00Z",
    )

    make_video(dataset / "camera" / "second.avi", frames=7)
    second = build_catalog_evidence(
        str(dataset),
        dataset_id="fixture",
        checked_at="2026-07-28T00:00:00Z",
    )

    assert first["dataset"]["snapshot_fingerprint"] != second["dataset"]["snapshot_fingerprint"]
    assert first["evidence_fingerprint"] != second["evidence_fingerprint"]
    assert first["facts"]["dataset.scale"]["value"] != second["facts"]["dataset.scale"]["value"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {"source_kind": "hf_hub"},
            "resolved_revision is required",
        ),
        (
            {"source_locator": "/private/dataset"},
            "absolute local path",
        ),
        (
            {"source_locator": "https://user:secret@example.com/dataset"},
            "must not contain credentials",
        ),
        (
            {"checked_at": "2026-07-28T00:00:00"},
            "must include a timezone",
        ),
    ],
)
def test_catalog_evidence_rejects_ambiguous_identity(
    tmp_path: Path,
    kwargs: dict[str, str],
    message: str,
) -> None:
    dataset = tmp_path / "dataset"
    make_video(dataset / "episode.avi")
    arguments = {
        "dataset_id": "fixture",
        "checked_at": "2026-07-28T00:00:00Z",
        **kwargs,
    }

    with pytest.raises(ValueError, match=message):
        build_catalog_evidence(str(dataset), **arguments)


def test_catalog_evidence_cli_writes_handoff(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    output = tmp_path / "catalog-evidence.json"
    make_video(dataset / "episode.avi")

    result = CliRunner().invoke(
        app,
        [
            "catalog-evidence",
            str(dataset),
            "--dataset-id",
            "fixture",
            "--checked-at",
            "2026-07-28T00:00:00Z",
            "--out",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Catalog evidence saved" in result.output
    payload = json.loads(output.read_text())
    assert payload["schema_version"] == "catalog-evidence-v1"
    assert payload["coverage"]["checksum"] == "sha256"
