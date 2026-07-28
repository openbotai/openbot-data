from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openbot_data.audit import (
    FINDING_REGISTRY,
    MEDIA_RULES,
    CapabilityCoverage,
    UnknownFindingCodeError,
    run_audit_rules,
)
from openbot_data.models import DatasetSnapshot, VideoRecord


def video_record(
    path: str,
    *,
    stream: str = "camera.top",
    width: int = 16,
    height: int = 16,
    fps: float = 5.0,
    duration: float = 0.8,
    metadata_valid: bool = True,
    decode_valid: bool | None = True,
    checksum: str | None = None,
) -> VideoRecord:
    return VideoRecord(
        source_path=Path("/source-that-must-not-be-read") / path,
        path=path,
        filename=Path(path).name,
        stream=stream,
        width=width,
        height=height,
        fps=fps,
        frame_count=4,
        duration=duration,
        size_bytes=128,
        size_mb=0.01,
        metadata_valid=metadata_valid,
        decode_valid=decode_valid,
        integrity_level="metadata" if decode_valid is None else "sample",
        decoded_frame_count=None if decode_valid is None else int(decode_valid),
        error=None if metadata_valid else "invalid metadata",
        checksum_sha256=checksum,
    )


def snapshot(
    *,
    videos: tuple[VideoRecord, ...] = (),
    findings: tuple[dict[str, Any], ...] = (),
    input_format: str = "video",
    integrity: str = "sample",
    checksum: str | None = None,
) -> DatasetSnapshot:
    return DatasetSnapshot(
        root=Path("/dataset-that-must-not-be-read"),
        input_format=input_format,
        codebase_version="v3.0" if input_format == "lerobot" else None,
        episodes=(),
        video_keys=tuple(sorted({video.stream for video in videos})),
        videos=videos,
        findings=findings,
        checksum=checksum,
        integrity=integrity,
    )


def media_rule(code: str):
    return next(rule for rule in MEDIA_RULES if rule.spec.code == code)


def canonical_run(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def test_registry_covers_current_documented_codes_and_contract_metadata() -> None:
    repository = Path(__file__).resolve().parents[1]
    rows = [
        line.split("|")
        for line in (repository / "docs" / "audit-findings.md").read_text().splitlines()
        if line.startswith("| `")
    ]
    documented = {
        cells[1].strip().strip("`"): cells[2].strip()
        for cells in rows
    }

    assert set(FINDING_REGISTRY) == set(documented)
    for code, spec in FINDING_REGISTRY.items():
        assert spec.default_severity == documented[code]
        assert spec.layer in {
            "metadata",
            "schema",
            "data",
            "media",
            "alignment",
            "provenance",
        }
        assert spec.impact
        assert spec.fixability in {
            "automatic",
            "delegated",
            "manual",
            "not_repairable",
        }
        assert spec.remediation_ref == f"openbot://remediation/{code}"
        assert spec.positive_fixture
        assert spec.negative_fixture


def test_rule_run_is_byte_stable_when_inputs_and_rules_are_shuffled() -> None:
    digest = "a" * 64
    videos = (
        video_record(
            "z/invalid.avi",
            width=0,
            height=0,
            fps=0,
            duration=0,
            metadata_valid=False,
            decode_valid=False,
            checksum=digest,
        ),
        video_record(
            "a/first.avi",
            width=16,
            height=16,
            fps=5,
            checksum=digest,
        ),
        video_record(
            "b/second.avi",
            width=32,
            height=24,
            fps=10,
            checksum=digest,
        ),
    )
    seeds = (
        {
            "code": "LEROBOT_VIDEO_MISSING",
            "severity": "error",
            "message": "Missing top camera.",
            "path": "videos",
            "evidence": {"episode_index": 2, "video_key": "camera.top"},
        },
        {
            "code": "LEROBOT_VIDEO_MISSING",
            "severity": "error",
            "message": "Missing top camera.",
            "path": "videos",
            "evidence": {"episode_index": 2, "video_key": "camera.top"},
        },
    )
    first = run_audit_rules(
        snapshot(
            videos=videos,
            findings=seeds,
            input_format="lerobot",
            checksum="sha256",
        ),
        rules=MEDIA_RULES,
    )
    second = run_audit_rules(
        snapshot(
            videos=tuple(reversed(videos)),
            findings=tuple(reversed(seeds)),
            input_format="lerobot",
            checksum="sha256",
        ),
        rules=tuple(reversed(MEDIA_RULES)),
    )

    assert canonical_run(first.as_dict()) == canonical_run(second.as_dict())
    codes = [finding["code"] for finding in first.findings]
    assert {
        "DUPLICATE_CONTENT",
        "LEROBOT_VIDEO_MISSING",
        "STREAM_INCONSISTENT_FPS",
        "STREAM_INCONSISTENT_RESOLUTION",
        "VIDEO_INVALID_DIMENSIONS",
        "VIDEO_INVALID_DURATION",
        "VIDEO_INVALID_FPS",
        "VIDEO_PREVIEW_DECODE_FAILED",
        "VIDEO_UNREADABLE",
    } <= set(codes)
    assert codes.count("LEROBOT_VIDEO_MISSING") == 1


def test_metadata_integrity_explicitly_skips_decode_rule() -> None:
    prepared = snapshot(
        videos=(video_record("camera/episode.avi", decode_valid=None),),
        integrity="metadata",
    )

    result = run_audit_rules(
        prepared,
        rules=(media_rule("VIDEO_PREVIEW_DECODE_FAILED"),),
    )

    assert result.findings == ()
    assert len(result.skipped_checks) == 1
    skipped = result.skipped_checks[0]
    assert skipped.rule_id == "video.preview.decode.failed"
    assert skipped.missing_capabilities == ("media.decode",)
    assert skipped.reason_code == "integrity_too_low"
    decode_coverage = next(
        item for item in result.capabilities if item.capability == "media.decode"
    )
    assert decode_coverage.status == "skipped"
    assert decode_coverage.checked == 0
    assert decode_coverage.total == 1


def test_legacy_seed_is_enriched_with_registry_metadata_and_deduplicated() -> None:
    seed = {
        "code": "LEROBOT_VIDEO_RELATION_MISSING",
        "severity": "error",
        "message": "Episode has no top-camera relation.",
        "path": "meta/episodes",
        "evidence": {
            "episode_index": 12,
            "video_key": "observation.images.top",
            "missing": ["file_index"],
        },
    }

    result = run_audit_rules(
        snapshot(
            findings=(seed, dict(seed)),
            input_format="lerobot",
        ),
        rules=(),
    )

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding["severity"] == "error"
    assert finding["layer"] == "alignment"
    assert finding["location"] == {
        "episode_index": 12,
        "video_key": "observation.images.top",
    }
    assert finding["impact"] == "episode_media_relation_is_incomplete"
    assert finding["fixability"] == "delegated"
    assert finding["remediation_ref"] == (
        "openbot://remediation/LEROBOT_VIDEO_RELATION_MISSING"
    )
    assert finding["evidence"] == seed["evidence"]


def test_static_media_rules_do_not_rescan_source_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_io(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("rule execution attempted source I/O")

    monkeypatch.setattr("builtins.open", unexpected_io)
    monkeypatch.setattr(Path, "open", unexpected_io)
    monkeypatch.setattr(Path, "read_text", unexpected_io)
    monkeypatch.setattr(Path, "read_bytes", unexpected_io)
    prepared = snapshot(
        videos=(
            video_record(
                "camera/broken.avi",
                width=0,
                height=0,
                fps=0,
                duration=0,
                metadata_valid=False,
                decode_valid=False,
            ),
        ),
    )

    result = run_audit_rules(prepared)

    assert {
        "VIDEO_UNREADABLE",
        "VIDEO_INVALID_FPS",
        "VIDEO_INVALID_DURATION",
        "VIDEO_INVALID_DIMENSIONS",
        "VIDEO_PREVIEW_DECODE_FAILED",
    } <= {finding["code"] for finding in result.findings}


def test_partial_available_capability_runs_rule_and_records_unchecked_scope() -> None:
    prepared = snapshot(
        videos=(
            video_record(
                "camera/invalid.avi",
                fps=0,
                metadata_valid=False,
            ),
        )
    )
    capabilities = (
        CapabilityCoverage(
            capability="media.metadata",
            status="partial",
            integrity="sample",
            checked=1,
            total=2,
            selected=("camera/invalid.avi",),
            omitted=("camera/unreadable.avi",),
            reason_code="partial_capability_coverage",
            reason="One media file was unavailable.",
        ),
    )

    result = run_audit_rules(
        prepared,
        rules=(media_rule("VIDEO_INVALID_FPS"),),
        capabilities=capabilities,
    )

    assert [finding["code"] for finding in result.findings] == ["VIDEO_INVALID_FPS"]
    assert len(result.skipped_checks) == 1
    assert result.skipped_checks[0].location == {
        "omitted": ["camera/unreadable.avi"]
    }


def test_unknown_seed_code_is_a_developer_error_or_conservative_fallback() -> None:
    seed = {
        "code": "UNREGISTERED_RULE",
        "severity": "warning",
        "message": "Unknown implementation output.",
        "evidence": {},
    }
    prepared = snapshot(findings=(seed,))

    with pytest.raises(UnknownFindingCodeError, match="UNREGISTERED_RULE"):
        run_audit_rules(prepared, rules=())

    fallback = run_audit_rules(
        prepared,
        rules=(),
        unknown_code_policy="fallback",
    )
    assert fallback.findings[0]["code"] == "UNREGISTERED_RULE"
    assert fallback.findings[0]["severity"] == "error"
    assert fallback.findings[0]["layer"] == "provenance"
    assert fallback.findings[0]["fixability"] == "not_repairable"
