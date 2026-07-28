from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from openbot_data.hub import (
    HUB_FULL_PAYLOAD_ALLOW_PATTERNS,
    HUB_METADATA_ALLOW_PATTERNS,
    HubArgumentError,
    HubDownloadBudget,
    HubDownloadError,
    HubRevisionError,
    allow_patterns_for_integrity,
    parse_hub_source,
    resolve_hub_dataset,
)

COMMIT = "a" * 40


def repository_files() -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = [
        {"rfilename": "README.md", "size": 100},
        {"rfilename": "meta/info.json", "size": 200},
        {"rfilename": "meta/stats.json", "size": 100},
    ]
    files.extend(
        {
            "rfilename": f"data/chunk-000/file-{index:03d}.parquet",
            "size": 10,
        }
        for index in range(5)
    )
    for camera in ("observation.images.top", "observation.images.wrist"):
        files.extend(
            {
                "rfilename": f"videos/{camera}/chunk-000/file-{index:03d}.mp4",
                "size": 20,
            }
            for index in range(5)
        )
    return files


class FakeResolver:
    def __init__(
        self,
        *,
        sha: str = COMMIT,
        files: list[dict[str, Any]] | None = None,
        publication_metadata: bool = True,
    ) -> None:
        self.sha = sha
        self.files = repository_files() if files is None else files
        self.publication_metadata = publication_metadata
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "sha": self.sha,
            "siblings": list(reversed(self.files)),
            "tags": ["robotics"] if self.publication_metadata else [],
            "card_data": (
                {
                    "license": "apache-2.0",
                    "tags": ["robotics"],
                    "task_categories": ["robotics"],
                }
                if self.publication_metadata
                else {}
            ),
        }


class FakeDownloader:
    def __init__(
        self,
        root: Path,
        *,
        codebase_version: str = "v3.0",
        resolved_revision: str = COMMIT,
    ) -> None:
        self.root = root
        self.codebase_version = codebase_version
        self.resolved_revision = resolved_revision
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        self.root.mkdir(parents=True, exist_ok=True)
        patterns = tuple(kwargs["allow_patterns"])
        if patterns == HUB_METADATA_ALLOW_PATTERNS:
            (self.root / "meta").mkdir(exist_ok=True)
            (self.root / "README.md").write_text("dataset")
            (self.root / "meta/info.json").write_text(
                json.dumps(
                    {
                        "codebase_version": self.codebase_version,
                        "total_episodes": 10,
                    }
                )
            )
            (self.root / "meta/stats.json").write_text("{}")
        else:
            selected = (
                [
                    item["rfilename"]
                    for item in repository_files()
                    if item["rfilename"].startswith(("data/", "videos/"))
                ]
                if tuple(patterns) == HUB_FULL_PAYLOAD_ALLOW_PATTERNS
                else list(patterns)
            )
            for relative in selected:
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"payload")
        return {
            "local_path": str(self.root),
            "resolved_revision": self.resolved_revision,
            "cache_hit": len(self.calls) > 1,
            "resumed": len(self.calls) > 1,
        }


def test_parse_hub_source_requires_revision_and_reconciles_explicit_values() -> None:
    parsed = parse_hub_source(
        "hf://datasets/org/name@refs/heads/main",
        repo_id="org/name",
        revision="refs/heads/main",
    )

    assert parsed.repo_id == "org/name"
    assert parsed.requested_revision == "refs/heads/main"
    assert parsed.locator == "hf://datasets/org/name@refs/heads/main"

    with pytest.raises(HubArgumentError, match="revision is required"):
        parse_hub_source("hf://datasets/org/name")
    with pytest.raises(HubArgumentError, match="do not match"):
        parse_hub_source(
            "hf://datasets/org/name@main",
            revision="release",
        )
    with pytest.raises(HubArgumentError, match="same repository"):
        parse_hub_source(
            "hf://datasets/org/name@main",
            repo_id="other/name",
        )


def test_allow_pattern_contract_is_integrity_specific() -> None:
    assert allow_patterns_for_integrity("metadata") == HUB_METADATA_ALLOW_PATTERNS
    assert allow_patterns_for_integrity(
        "sample",
        selected_payload=(
            "videos/camera/file-000.mp4",
            "data/chunk-000/file-000.parquet",
        ),
    ) == (
        *HUB_METADATA_ALLOW_PATTERNS,
        "data/chunk-000/file-000.parquet",
        "videos/camera/file-000.mp4",
    )
    assert allow_patterns_for_integrity("full") == (
        *HUB_METADATA_ALLOW_PATTERNS,
        *HUB_FULL_PAYLOAD_ALLOW_PATTERNS,
    )
    with pytest.raises(HubArgumentError, match="safe data"):
        allow_patterns_for_integrity(
            "sample",
            selected_payload=("../outside",),
        )


@pytest.mark.parametrize(
    "source",
    [
        "https://huggingface.co/datasets/org/name",
        "hf://datasets/org/name@main?token=secret",
        "hf://hf_abcdefghijkl@datasets/org/name@main",
        "hf://datasets/org/name@main#credential",
        "hf://datasets/org/name@main%3Ftoken",
    ],
)
def test_hub_source_rejects_secret_bearing_or_ambiguous_locators(
    source: str,
) -> None:
    with pytest.raises(HubArgumentError):
        parse_hub_source(source)


def test_secret_bearing_repository_inventory_path_is_rejected() -> None:
    resolver = FakeResolver(
        files=[
            {
                "rfilename": "videos/token=hf_abcdefghijkl/file.mp4",
                "size": 10,
            }
        ]
    )

    with pytest.raises(HubRevisionError, match="unsafe path"):
        resolve_hub_dataset(
            repo_id="org/name",
            revision="main",
            resolver=resolver,
            download=False,
        )


def test_conflicting_duplicate_inventory_entries_are_rejected() -> None:
    resolver = FakeResolver(
        files=[
            {"rfilename": "meta/info.json", "size": 10},
            {"rfilename": "meta/info.json", "size": 11},
        ]
    )

    with pytest.raises(HubRevisionError, match="conflicting"):
        resolve_hub_dataset(
            repo_id="org/name",
            revision="main",
            resolver=resolver,
            download=False,
        )


def test_metadata_mode_resolves_commit_and_downloads_only_metadata(
    tmp_path: Path,
) -> None:
    resolver = FakeResolver()
    downloader = FakeDownloader(tmp_path / "private-cache" / "checkout")

    result = resolve_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="metadata",
        resolver=resolver,
        downloader=downloader,
        cache_dir=str(tmp_path / "private-cache"),
    )
    public = result.as_dict()

    assert resolver.calls == [
        {
            "repo_id": "org/name",
            "revision": "main",
            "repo_type": "dataset",
        }
    ]
    assert len(downloader.calls) == 1
    call = downloader.calls[0]
    assert call["revision"] == COMMIT
    assert tuple(call["allow_patterns"]) == HUB_METADATA_ALLOW_PATTERNS
    assert "token" not in call
    assert result.local_path == (tmp_path / "private-cache/checkout").resolve()
    assert result.audit_kwargs() == {
        "path": str(result.local_path),
        "input_format": "lerobot",
        "integrity": "metadata",
    }
    assert result.snapshot_kwargs() == {
        "path": str(result.local_path),
        "input_format": "lerobot",
        "integrity": "metadata",
        "source_kind": "hf_hub",
        "source_locator": "hf://datasets/org/name@main",
        "requested_revision": "main",
        "resolved_revision": COMMIT,
    }
    assert public["source"] == {
        "kind": "hf_hub",
        "locator": "hf://datasets/org/name@main",
        "repo_id": "org/name",
        "repo_type": "dataset",
        "requested_revision": "main",
        "resolved_revision": COMMIT,
    }
    assert public["coverage"]["status"] == "partial"
    assert public["coverage"]["validation_scope"] == "metadata_validated"
    assert public["coverage"]["selection"]["data_shards"] == []
    assert public["coverage"]["selection"]["media_shards"] == []
    assert {
        item["capability"]
        for item in public["coverage"]["skipped_capabilities"]
    } == {"data.rows", "media.decode", "statistics.recompute"}
    assert "HUB_PARTIAL_COVERAGE" in {
        finding["code"] for finding in public["findings"]
    }
    serialized = json.dumps(public)
    assert str(tmp_path) not in serialized
    assert "private-cache" not in serialized
    assert str(tmp_path) not in repr(result)


def test_sample_mode_selects_deterministic_shards_and_cameras(
    tmp_path: Path,
) -> None:
    first_resolver = FakeResolver(files=repository_files())
    second_resolver = FakeResolver(files=list(reversed(repository_files())))
    first_downloader = FakeDownloader(tmp_path / "checkout")
    second_downloader = FakeDownloader(tmp_path / "checkout")

    first = resolve_hub_dataset(
        repo_id="org/name",
        revision="main",
        integrity="sample",
        revision_resolver=first_resolver,
        downloader=first_downloader,
    )
    second = resolve_hub_dataset(
        "hf://org/name@main",
        integrity="sample",
        revision_resolver=second_resolver,
        downloader=second_downloader,
    )

    assert first.as_dict() == second.as_dict()
    assert len(first_downloader.calls) == 2
    assert tuple(first_downloader.calls[0]["allow_patterns"]) == (
        HUB_METADATA_ALLOW_PATTERNS
    )
    payload_patterns = tuple(first_downloader.calls[1]["allow_patterns"])
    assert payload_patterns == first.payload_allow_patterns
    assert all("*" not in path for path in payload_patterns)
    selection = first.coverage["selection"]
    assert selection["data_shards"] == [
        "data/chunk-000/file-000.parquet",
        "data/chunk-000/file-002.parquet",
        "data/chunk-000/file-004.parquet",
    ]
    assert selection["cameras"] == [
        "observation.images.top",
        "observation.images.wrist",
    ]
    assert selection["episodes"] == []
    assert selection["max_episodes"] == 64
    assert first.coverage["totals"] == {
        "episodes": 10,
        "metadata_shards": 3,
        "data_shards": 5,
        "cameras": 2,
        "media_shards": 10,
    }


def test_full_mode_uses_full_payload_patterns_only_when_budgets_cover_all(
    tmp_path: Path,
) -> None:
    resolver = FakeResolver()
    downloader = FakeDownloader(tmp_path / "checkout")
    sufficient = HubDownloadBudget(
        max_bytes=10_000,
        max_shards=20,
        max_episodes=20,
        max_media_shards=10,
    )

    result = resolve_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="full",
        budget=sufficient,
        revision_resolver=resolver,
        downloader=downloader,
    )

    assert len(downloader.calls) == 2
    assert tuple(downloader.calls[1]["allow_patterns"]) == (
        HUB_FULL_PAYLOAD_ALLOW_PATTERNS
    )
    assert result.coverage["status"] == "complete"
    assert result.coverage["exhausted_limits"] == []
    assert result.coverage["selection"]["episodes"] == list(range(10))
    assert "HUB_PARTIAL_COVERAGE" not in {
        finding["code"] for finding in result.findings
    }


def test_full_mode_stops_before_payload_when_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    resolver = FakeResolver()
    downloader = FakeDownloader(tmp_path / "checkout")
    constrained = HubDownloadBudget(
        max_bytes=1_000,
        max_shards=2,
        max_episodes=5,
        max_media_shards=1,
    )

    result = resolve_hub_dataset(
        "hf://datasets/org/name@main",
        integrity="full",
        budget=constrained,
        revision_resolver=resolver,
        downloader=downloader,
    )

    assert len(downloader.calls) == 1
    assert result.payload_allow_patterns == ()
    assert result.coverage["status"] == "partial"
    assert set(result.coverage["exhausted_limits"]) == {
        "max_episodes",
        "max_media_shards",
        "max_shards",
    }
    assert "HUB_DOWNLOAD_BUDGET_EXHAUSTED" in {
        finding["code"] for finding in result.findings
    }


def test_revision_and_download_mismatches_are_rejected(
    tmp_path: Path,
) -> None:
    requested_sha = "b" * 40
    resolver = FakeResolver(sha=COMMIT)

    with pytest.raises(HubRevisionError, match="does not match"):
        resolve_hub_dataset(
            repo_id="org/name",
            revision=requested_sha,
            revision_resolver=resolver,
            download=False,
        )

    bad_downloader = FakeDownloader(
        tmp_path / "checkout",
        resolved_revision="c" * 40,
    )
    with pytest.raises(HubRevisionError, match="downloader revision"):
        resolve_hub_dataset(
            repo_id="org/name",
            revision="main",
            revision_resolver=FakeResolver(),
            downloader=bad_downloader,
        )


def test_format_version_ref_must_match_downloaded_info(tmp_path: Path) -> None:
    downloader = FakeDownloader(
        tmp_path / "checkout",
        codebase_version="v2.1",
    )

    with pytest.raises(HubRevisionError, match="codebase_version"):
        resolve_hub_dataset(
            "hf://datasets/org/name@v3.0",
            revision_resolver=FakeResolver(),
            downloader=downloader,
        )


def test_resolution_only_and_viewer_signal_do_not_require_download(
    tmp_path: Path,
) -> None:
    downloader_calls: list[dict[str, Any]] = []

    def unexpected_downloader(**kwargs: Any) -> None:
        downloader_calls.append(kwargs)

    result = resolve_hub_dataset(
        repo_id="org/name",
        revision="main",
        revision_resolver=FakeResolver(publication_metadata=False),
        downloader=unexpected_downloader,
        viewer_validator=lambda **_kwargs: {"is_valid": True},
        local_dir=str(tmp_path / "secret-local-path"),
        download=False,
    )

    assert downloader_calls == []
    assert result.local_path is None
    with pytest.raises(HubDownloadError, match="no local checkout"):
        result.audit_kwargs()
    assert result.coverage["dataset_viewer"] == {
        "status": "complete",
        "is_valid": True,
    }
    assert "source.download" in {
        item["capability"]
        for item in result.coverage["skipped_capabilities"]
    }
    assert "HUB_PUBLICATION_METADATA_MISSING" in {
        finding["code"] for finding in result.findings
    }
    assert str(tmp_path) not in json.dumps(result.as_dict())


def test_standard_hf_cache_symlinks_are_materialized_as_regular_files(
    tmp_path: Path,
) -> None:
    repository_cache = tmp_path / "datasets--org--name"
    snapshot = repository_cache / "snapshots" / COMMIT
    blob = repository_cache / "blobs" / ("f" * 64)
    blob.parent.mkdir(parents=True)
    blob.write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "total_episodes": 0,
            }
        ),
        encoding="utf-8",
    )
    info = snapshot / "meta/info.json"
    info.parent.mkdir(parents=True)
    info.symlink_to(os.path.relpath(blob, info.parent))

    result = resolve_hub_dataset(
        repo_id="org/name",
        revision="main",
        resolver=FakeResolver(
            files=[{"rfilename": "meta/info.json", "size": blob.stat().st_size}]
        ),
        downloader=lambda **_kwargs: {
            "local_path": str(snapshot),
            "resolved_revision": COMMIT,
        },
    )

    assert result.local_path is not None
    assert result.local_path != snapshot
    materialized = result.local_path / "meta/info.json"
    assert materialized.is_file()
    assert not materialized.is_symlink()
    assert materialized.read_bytes() == blob.read_bytes()
    assert result.coverage["validation_scope"] == "metadata_validated"


def test_nonstandard_checkout_symlink_is_rejected(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    outside = tmp_path / "outside-info.json"
    outside.write_text('{"codebase_version":"v3.0"}', encoding="utf-8")
    info = checkout / "meta/info.json"
    info.parent.mkdir(parents=True)
    info.symlink_to(outside)

    with pytest.raises(HubDownloadError, match="standard Hugging Face cache"):
        resolve_hub_dataset(
            repo_id="org/name",
            revision="main",
            resolver=FakeResolver(
                files=[
                    {
                        "rfilename": "meta/info.json",
                        "size": outside.stat().st_size,
                    }
                ]
            ),
            downloader=lambda **_kwargs: {
                "local_path": str(checkout),
                "resolved_revision": COMMIT,
            },
        )


def test_missing_selected_sample_path_is_not_reported_as_validated(
    tmp_path: Path,
) -> None:
    class MissingSelectedDownloader(FakeDownloader):
        def __call__(self, **kwargs: Any) -> dict[str, Any]:
            result = super().__call__(**kwargs)
            patterns = tuple(kwargs["allow_patterns"])
            if patterns != HUB_METADATA_ALLOW_PATTERNS:
                missing = self.root / "data/chunk-000/file-002.parquet"
                missing.unlink()
            return result

    result = resolve_hub_dataset(
        repo_id="org/name",
        revision="main",
        integrity="sample",
        resolver=FakeResolver(),
        downloader=MissingSelectedDownloader(tmp_path / "checkout"),
    )

    payload = result.coverage["downloads"]["payload"]
    assert payload["validated"] is False
    assert payload["missing_paths"] == [
        "data/chunk-000/file-002.parquet"
    ]
    assert result.coverage["validation_scope"] == "partial"
    assert "data.sample" not in result.coverage["completed_capabilities"]
    assert "data/chunk-000/file-002.parquet" not in (
        result.coverage["selection"]["data_shards"]
    )


def test_sample_episode_selection_uses_successful_payload_relations_and_cap(
    tmp_path: Path,
) -> None:
    metadata_paths = [
        "README.md",
        "meta/info.json",
        "meta/tasks.jsonl",
        "meta/episodes.jsonl",
    ]
    data_paths = [
        f"data/chunk-000/episode_{index:06d}.parquet"
        for index in range(3)
    ]
    files = [
        {"rfilename": path, "size": 100}
        for path in (*metadata_paths, *data_paths)
    ]
    root = tmp_path / "checkout"

    def downloader(**kwargs: Any) -> dict[str, Any]:
        root.mkdir(parents=True, exist_ok=True)
        patterns = tuple(kwargs["allow_patterns"])
        if patterns == HUB_METADATA_ALLOW_PATTERNS:
            (root / "meta").mkdir(exist_ok=True)
            (root / "README.md").write_text("dataset", encoding="utf-8")
            (root / "meta/info.json").write_text(
                json.dumps(
                    {
                        "codebase_version": "v2.1",
                        "total_episodes": 3,
                        "total_frames": 3,
                        "chunks_size": 1000,
                        "features": {},
                    }
                ),
                encoding="utf-8",
            )
            (root / "meta/tasks.jsonl").write_text(
                '{"task_index":0,"task":"pick"}\n',
                encoding="utf-8",
            )
            (root / "meta/episodes.jsonl").write_text(
                "".join(
                    json.dumps(
                        {
                            "episode_index": index,
                            "length": 1,
                            "tasks": ["pick"],
                        }
                    )
                    + "\n"
                    for index in range(3)
                ),
                encoding="utf-8",
            )
        else:
            for relative in patterns:
                if relative.endswith("episode_000001.parquet"):
                    continue
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"payload")
        return {
            "local_path": str(root),
            "resolved_revision": COMMIT,
        }

    result = resolve_hub_dataset(
        repo_id="org/name",
        revision="main",
        integrity="sample",
        budget=HubDownloadBudget(
            max_bytes=10_000,
            max_shards=3,
            max_episodes=2,
            max_media_shards=1,
        ),
        resolver=FakeResolver(files=files),
        downloader=downloader,
    )

    assert result.coverage["selection"]["episodes"] == [0, 2]
    assert result.coverage["selection"]["max_episodes"] == 2
    assert result.coverage["downloads"]["payload"]["missing_paths"] == [
        "data/chunk-000/episode_000001.parquet"
    ]


def test_cache_and_resume_flags_are_not_semantic_resolution_evidence(
    tmp_path: Path,
) -> None:
    class OperationalDownloader(FakeDownloader):
        def __init__(self, root: Path, flag: bool) -> None:
            super().__init__(root)
            self.flag = flag

        def __call__(self, **kwargs: Any) -> dict[str, Any]:
            result = super().__call__(**kwargs)
            result["cache_hit"] = self.flag
            result["resumed"] = self.flag
            return result

    cold = resolve_hub_dataset(
        repo_id="org/name",
        revision="main",
        resolver=FakeResolver(),
        downloader=OperationalDownloader(tmp_path / "cold", False),
    )
    warm = resolve_hub_dataset(
        repo_id="org/name",
        revision="main",
        resolver=FakeResolver(),
        downloader=OperationalDownloader(tmp_path / "warm", True),
    )

    assert cold.as_dict() == warm.as_dict()
    serialized = json.dumps(cold.as_dict())
    assert "cache_hit" not in serialized
    assert "resumed" not in serialized


def test_publication_metadata_is_filtered_and_exposed(tmp_path: Path) -> None:
    resolver = FakeResolver()

    result = resolve_hub_dataset(
        repo_id="org/name",
        revision="main",
        resolver=resolver,
        downloader=FakeDownloader(tmp_path / "checkout"),
    )

    assert result.publication_metadata == {
        "license": "apache-2.0",
        "tags": ["robotics"],
        "task_categories": ["robotics"],
    }
    assert result.as_dict()["publication_metadata"] == (
        result.publication_metadata
    )
