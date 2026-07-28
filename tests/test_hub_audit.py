from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from openbot_data import schema_path
from openbot_data.hub import HubDownloadBudget
from openbot_data.hub_audit import (
    audit_hub_dataset,
    evaluate_hub_dataset_readiness,
    snapshot_hub_dataset,
)

COMMIT = "d" * 40


class Resolver:
    def __call__(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "sha": COMMIT,
            "siblings": [
                {"rfilename": "README.md", "size": 20},
                {"rfilename": "meta/info.json", "size": 100},
                {"rfilename": "meta/tasks.parquet", "size": 100},
                {
                    "rfilename": "meta/episodes/chunk-000/file-000.parquet",
                    "size": 100,
                },
                {"rfilename": "data/chunk-000/file-000.parquet", "size": 100},
            ],
            "tags": ["robotics"],
            "card_data": {
                "license": "apache-2.0",
                "tags": ["robotics"],
                "task_categories": ["robotics"],
            },
        }


class Downloader:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        pa = pytest.importorskip("pyarrow", exc_type=ImportError)
        parquet = pytest.importorskip(
            "pyarrow.parquet",
            exc_type=ImportError,
        )
        (self.root / "meta/episodes/chunk-000").mkdir(
            parents=True,
            exist_ok=True,
        )
        (self.root / "README.md").write_text("dataset", encoding="utf-8")
        (self.root / "meta/info.json").write_text(
            json.dumps(
                {
                    "codebase_version": "v3.0",
                    "total_episodes": 1,
                    "total_frames": 2,
                    "total_tasks": 1,
                    "video_path": None,
                    "features": {},
                    "license": "apache-2.0",
                    "tags": ["robotics"],
                    "task_categories": ["robotics"],
                }
            ),
            encoding="utf-8",
        )
        parquet.write_table(
            pa.table({"task_index": [0], "task": ["pick"]}),
            self.root / "meta/tasks.parquet",
        )
        parquet.write_table(
            pa.table(
                {
                    "episode_index": [0],
                    "length": [2],
                    "tasks": [["pick"]],
                    "data/chunk_index": [0],
                    "data/file_index": [0],
                    "dataset_from_index": [0],
                    "dataset_to_index": [2],
                }
            ),
            self.root / "meta/episodes/chunk-000/file-000.parquet",
        )
        return {
            "local_path": str(self.root),
            "resolved_revision": COMMIT,
            "cache_hit": False,
            "resumed": False,
        }


def validate(name: str, value: dict[str, Any]) -> None:
    with schema_path(name) as path:
        schema = json.loads(path.read_text())
    jsonschema.Draft202012Validator(schema).validate(value)


def test_metadata_hub_audit_is_revision_pinned_and_partial(
    tmp_path: Path,
) -> None:
    downloader = Downloader(tmp_path / "private-cache")

    result = audit_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="metadata",
        resolver=Resolver(),
        downloader=downloader,
    )

    assert result["source"]["requested_revision"] == "main"
    assert result["source"]["resolved_revision"] == COMMIT
    assert result["coverage"]["source"]["status"] == "partial"
    codes = {item["code"] for item in result["findings"]}
    assert "HUB_PARTIAL_COVERAGE" in codes
    assert not {
        "LEROBOT_DATA_MISSING",
        "LEROBOT_VIDEO_MISSING",
        "LEROBOT_VIDEOS_MISSING",
    } & codes
    assert str(tmp_path) not in json.dumps(result)
    validate("audit", result)


def test_hub_snapshot_carries_remote_coverage_without_private_paths(
    tmp_path: Path,
) -> None:
    result = snapshot_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="metadata",
        resolver=Resolver(),
        downloader=Downloader(tmp_path / "private-cache"),
    )

    assert result["source"] == {
        "kind": "hf_hub",
        "locator": "hf://datasets/org/name@main",
        "requested_revision": "main",
        "resolved_revision": COMMIT,
    }
    assert result["coverage"]["source"]["validation_scope"] == (
        "metadata_validated"
    )
    assert str(tmp_path) not in json.dumps(result)
    validate("snapshot", result)


def test_metadata_hub_readiness_is_partial_not_ready(
    tmp_path: Path,
) -> None:
    result = evaluate_hub_dataset_readiness(
        "hf://datasets/org/name@main",
        profile="lerobot-core",
        integrity="metadata",
        resolver=Resolver(),
        downloader=Downloader(tmp_path / "private-cache"),
    )

    assert result["status"] == "PARTIAL"
    assert result["blocking_findings"] == []
    assert result["coverage"]["missing_capabilities"]
    assert result["snapshot_fingerprint"]
    validate("readiness", result)


def test_missing_selected_sample_payload_is_preserved_in_audit(
    tmp_path: Path,
) -> None:
    result = audit_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="sample",
        resolver=Resolver(),
        downloader=Downloader(tmp_path / "private-cache"),
    )

    assert result["coverage"]["source"]["validation_scope"] == "partial"
    assert result["coverage"]["source"]["downloads"]["payload"][
        "missing_paths"
    ] == ["data/chunk-000/file-000.parquet"]
    codes = {item["code"] for item in result["findings"]}
    assert "LEROBOT_DATA_MISSING" in codes
    assert "HUB_PARTIAL_COVERAGE" in codes
    validate("audit", result)


def test_metadata_budget_exhaustion_still_produces_canonical_partial_artifacts(
    tmp_path: Path,
) -> None:
    def unexpected_downloader(**_kwargs: Any) -> None:
        raise AssertionError("download must not start after metadata budget exhaustion")

    budget = HubDownloadBudget(
        max_bytes=1,
        max_shards=12,
        max_episodes=64,
        max_media_shards=9,
    )
    audit = audit_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="metadata",
        resolver=Resolver(),
        downloader=unexpected_downloader,
        budget=budget,
    )
    readiness = evaluate_hub_dataset_readiness(
        "hf://datasets/org/name@main",
        integrity="metadata",
        resolver=Resolver(),
        downloader=unexpected_downloader,
        budget=budget,
    )

    assert audit["coverage"]["source"]["status"] == "partial"
    assert "HUB_DOWNLOAD_BUDGET_EXHAUSTED" in {
        item["code"] for item in audit["findings"]
    }
    assert readiness["status"] == "PARTIAL"
    assert readiness["blocking_findings"] == []
    validate("audit", audit)
    validate("readiness", readiness)


def test_hub_card_metadata_reaches_publication_readiness(
    tmp_path: Path,
) -> None:
    class CardOnlyDownloader(Downloader):
        def __call__(self, **kwargs: Any) -> dict[str, Any]:
            result = super().__call__(**kwargs)
            info_path = self.root / "meta/info.json"
            info = json.loads(info_path.read_text(encoding="utf-8"))
            for key in ("license", "tags", "task_categories"):
                info.pop(key, None)
            info_path.write_text(json.dumps(info), encoding="utf-8")
            return result

    result = evaluate_hub_dataset_readiness(
        "hf://datasets/org/name@main",
        profile="hf-publication",
        integrity="metadata",
        resolver=Resolver(),
        downloader=CardOnlyDownloader(tmp_path / "private-cache"),
    )

    missing_keys = {
        item.get("location", {}).get("metadata_key")
        for item in [
            *result["blocking_findings"],
            *result["warnings"],
        ]
        if item.get("code") == "READINESS_METADATA_MISSING"
    }
    assert not {"license", "tags", "task_categories"} & missing_keys
    validate("readiness", result)


def test_operational_cache_flags_do_not_change_snapshot_fingerprint(
    tmp_path: Path,
) -> None:
    class FlagDownloader(Downloader):
        def __init__(self, root: Path, flag: bool) -> None:
            super().__init__(root)
            self.flag = flag

        def __call__(self, **kwargs: Any) -> dict[str, Any]:
            result = super().__call__(**kwargs)
            result["cache_hit"] = self.flag
            result["resumed"] = self.flag
            return result

    cold = snapshot_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="metadata",
        resolver=Resolver(),
        downloader=FlagDownloader(tmp_path / "cold", False),
    )
    warm = snapshot_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="metadata",
        resolver=Resolver(),
        downloader=FlagDownloader(tmp_path / "warm", True),
    )

    assert cold["snapshot_fingerprint"] == warm["snapshot_fingerprint"]
    assert cold["coverage"]["source"] == warm["coverage"]["source"]
