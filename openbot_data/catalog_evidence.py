"""Versioned, score-free evidence handoff for the OpenBot Catalog."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from openbot_data import __version__
from openbot_data.models import DatasetSnapshot
from openbot_data.preflight import (
    AUDIT_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    audit_dataset,
    dataset_fingerprint,
    prepare_dataset,
)
from openbot_data.serialization import write_json_atomic

CATALOG_EVIDENCE_SCHEMA_VERSION = "catalog-evidence-v1"
CATALOG_EVIDENCE_PROFILE = "openbot-data-catalog-handoff-v1"
CATALOG_EVIDENCE_RULE_PACK = AUDIT_SCHEMA_VERSION
SUPPORTED_SOURCE_KINDS = {"local", "hf_hub"}
JS_SAFE_INTEGER_MAX = (2**53) - 1


def _utf8(value: str) -> bytes:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("canonical evidence strings must contain valid Unicode scalars") from exc


def canonical_evidence_tree(value: Any) -> list[Any]:
    """Convert JSON-compatible evidence into a cross-language typed tree.

    The tree contains no untagged JSON numbers. Integers use decimal strings
    within the JavaScript safe range; non-integral numbers use the exact
    IEEE-754 float64 big-endian bytes. Object keys are sorted by UTF-8 bytes.
    """
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", "true" if value else "false"]
    if isinstance(value, str):
        _utf8(value)
        return ["string", value]
    if isinstance(value, int):
        if abs(value) > JS_SAFE_INTEGER_MAX:
            raise ValueError("canonical evidence integers must be JavaScript-safe")
        return ["number", "integer", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical evidence numbers must be finite")
        if value.is_integer():
            integer = int(value)
            if abs(integer) > JS_SAFE_INTEGER_MAX:
                raise ValueError("canonical evidence integers must be JavaScript-safe")
            return ["number", "integer", str(integer)]
        return ["number", "float64", struct.pack(">d", value).hex()]
    if isinstance(value, (list, tuple)):
        return ["array", [canonical_evidence_tree(item) for item in value]]
    if isinstance(value, dict):
        entries: list[list[Any]] = []
        for key in value:
            if not isinstance(key, str):
                raise ValueError("canonical evidence object keys must be strings")
            _utf8(key)
        for key in sorted(value, key=_utf8):
            entries.append([key, canonical_evidence_tree(value[key])])
        return ["object", entries]
    raise ValueError(
        f"canonical evidence does not support values of type {type(value).__name__}"
    )


def canonical_evidence_sha256(value: Any) -> str:
    """Hash the compact UTF-8 JSON representation of ``canonical_evidence_tree``."""
    tree = canonical_evidence_tree(value)
    encoded = json.dumps(
        tree,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _checked_at(value: str) -> str:
    """Normalize a timezone-aware timestamp to RFC 3339 UTC."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("checked_at must be a non-empty RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("checked_at must be a valid RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("checked_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _public_source_locator(
    source_kind: str,
    dataset_id: str,
    resolved_revision: Optional[str],
    source_locator: Optional[str],
) -> str:
    """Return a portable locator without URL credentials, query strings, or fragments."""
    locator = source_locator
    if locator is None:
        if source_kind == "hf_hub":
            locator = f"hf://datasets/{dataset_id}@{resolved_revision}"
        else:
            locator = "dataset://."
    locator = locator.strip()
    if not locator:
        raise ValueError("source_locator must not be empty")
    parsed = urlsplit(locator)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source_locator must not contain credentials")
    if parsed.scheme == "file" or (not parsed.scheme and Path(locator).is_absolute()):
        raise ValueError("source_locator must not expose an absolute local path")
    if parsed.scheme:
        locator = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return locator


def _snapshot_fingerprint(snapshot: DatasetSnapshot) -> str:
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "input_format": snapshot.input_format,
        "codebase_version": snapshot.codebase_version,
        "episodes": [episode.as_dict() for episode in snapshot.episodes],
        "video_keys": list(snapshot.video_keys),
        "videos": [video.as_dict() for video in snapshot.videos],
    }
    return dataset_fingerprint(payload)


def _capability_status(checked: int, total: int, *, skipped: bool = False) -> str:
    if skipped:
        return "skipped"
    if total == 0:
        return "unavailable"
    if checked == total:
        return "complete"
    if checked == 0:
        return "unavailable"
    return "partial"


def _evidence_level(snapshot: DatasetSnapshot, integrity: str) -> str:
    if (
        integrity in {"sample", "full"}
        and snapshot.videos
        and all(video.decode_valid is True for video in snapshot.videos)
    ):
        return "sample_verified"
    if snapshot.videos and all(video.metadata_valid for video in snapshot.videos):
        return "metadata_verified"
    return "official_claim"


def _fact(value: Any, evidence_level: str, source_locator: str) -> Dict[str, Any]:
    return {
        "value": value,
        "evidence_level": evidence_level,
        "source_locator": source_locator,
    }


def _manifest_paths(snapshot: DatasetSnapshot) -> list[str]:
    if snapshot.input_format != "lerobot":
        return []
    candidates = (
        "meta/info.json",
        "meta/stats.json",
        "meta/tasks.parquet",
        "meta/episodes.jsonl",
        "meta/episodes/chunk-000/file-000.parquet",
    )
    return [candidate for candidate in candidates if (snapshot.root / candidate).is_file()]


def _coverage(snapshot: DatasetSnapshot, checksum: Optional[str], integrity: str) -> Dict[str, Any]:
    total_videos = len(snapshot.videos)
    metadata_checked = sum(video.metadata_valid for video in snapshot.videos)
    decode_checked = sum(video.decode_valid is not None for video in snapshot.videos)
    decode_passed = sum(video.decode_valid is True for video in snapshot.videos)
    checksum_count = sum(video.checksum_sha256 is not None for video in snapshot.videos)
    decoded_frames = sum(video.decoded_frame_count or 0 for video in snapshot.videos)
    return {
        "integrity_level": integrity,
        "checksum": checksum,
        "episodes": {
            "discovered": len(snapshot.episodes),
            "with_declared_length": sum(
                episode.length is not None for episode in snapshot.episodes
            ),
        },
        "videos": {
            "discovered": total_videos,
            "metadata_checked": metadata_checked,
            "decode_checked": decode_checked,
            "decode_passed": decode_passed,
            "checksummed": checksum_count,
            "declared_frames": sum(video.frame_count for video in snapshot.videos),
            "decoded_frames": decoded_frames,
        },
        "capabilities": {
            "dataset_discovery": "complete",
            "media_metadata": _capability_status(metadata_checked, total_videos),
            "media_decode": _capability_status(
                decode_checked,
                total_videos,
                skipped=integrity == "metadata",
            ),
            "content_checksum": _capability_status(
                checksum_count,
                total_videos,
                skipped=checksum is None,
            ),
            "policy_readiness": "skipped",
            "annotation_quality": "skipped",
            "downstream_pipeline": "skipped",
        },
    }


def _unresolved_checks(
    snapshot: DatasetSnapshot,
    checksum: Optional[str],
    integrity: str,
) -> list[Dict[str, str]]:
    checks = [
        {
            "code": "CATALOG_GOVERNANCE_UNVERIFIED",
            "reason": "A local audit does not establish license or access terms.",
            "required_for": "governance",
        },
        {
            "code": "CATALOG_POLICY_PROFILE_NOT_RUN",
            "reason": "No policy-specific training-readiness profile was executed.",
            "required_for": "policy_training",
        },
        {
            "code": "CATALOG_DOWNSTREAM_PIPELINE_NOT_RUN",
            "reason": "No training or loading pipeline smoke test was executed.",
            "required_for": "pipeline_tested",
        },
    ]
    if integrity != "full":
        checks.append(
            {
                "code": "CATALOG_FULL_INTEGRITY_NOT_RUN",
                "reason": "The audit did not decode every declared media frame.",
                "required_for": "full_integrity",
            }
        )
    if checksum is None:
        checks.append(
            {
                "code": "CATALOG_CONTENT_DIGEST_NOT_CAPTURED",
                "reason": "Media content digests were not captured.",
                "required_for": "immutable_local_identity",
            }
        )
    if not any(episode.tasks for episode in snapshot.episodes):
        checks.append(
            {
                "code": "CATALOG_ANNOTATIONS_UNVERIFIED",
                "reason": "No task or annotation inventory was verified.",
                "required_for": "failure_recovery",
            }
        )
    return sorted(checks, key=lambda item: item["code"])


def build_catalog_evidence(
    path: str,
    *,
    dataset_id: str,
    checked_at: str,
    source_kind: str = "local",
    source_locator: Optional[str] = None,
    resolved_revision: Optional[str] = None,
    input_format: str = "auto",
    checksum: Optional[str] = "sha256",
    integrity: str = "sample",
    follow_symlinks: bool = False,
    profile_id: str = CATALOG_EVIDENCE_PROFILE,
    rule_pack_version: str = CATALOG_EVIDENCE_RULE_PACK,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Audit one dataset and emit a deterministic, score-free Catalog handoff.

    ``checked_at`` is caller supplied so rerunning a completed audit with the
    same evidence timestamp remains byte-stable. The evidence fingerprint
    deliberately excludes that timestamp and changes only with identity,
    coverage, facts, findings, or unresolved checks.
    """
    normalized_dataset_id = dataset_id.strip()
    normalized_source_kind = source_kind.strip().lower()
    normalized_revision = resolved_revision.strip() if resolved_revision else None
    if not normalized_dataset_id:
        raise ValueError("dataset_id must not be empty")
    if normalized_source_kind not in SUPPORTED_SOURCE_KINDS:
        raise ValueError(f"source_kind must be one of {sorted(SUPPORTED_SOURCE_KINDS)}")
    if normalized_source_kind == "hf_hub" and not normalized_revision:
        raise ValueError("resolved_revision is required for hf_hub evidence")
    normalized_checked_at = _checked_at(checked_at)
    normalized_locator = _public_source_locator(
        normalized_source_kind,
        normalized_dataset_id,
        normalized_revision,
        source_locator,
    )
    if not profile_id.strip():
        raise ValueError("profile_id must not be empty")
    if not rule_pack_version.strip():
        raise ValueError("rule_pack_version must not be empty")

    snapshot = prepare_dataset(
        path,
        input_format=input_format,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
    )
    audit = audit_dataset(path, snapshot=snapshot)
    maturity = _evidence_level(snapshot, integrity)
    coverage = _coverage(snapshot, checksum, integrity)
    tasks = sorted({task for episode in snapshot.episodes for task in episode.tasks})
    signal_categories = ["video_observation"] if snapshot.videos else []
    manifest_paths = _manifest_paths(snapshot)
    profile_status = "BLOCKED" if audit["summary"]["error"] else "PARTIAL"

    facts = {
        "dataset.schema": _fact(
            {
                "input_format": snapshot.input_format,
                "dataset_format_version": snapshot.codebase_version,
                "video_streams": list(snapshot.video_keys),
            },
            "metadata_verified",
            "openbot://snapshot/schema",
        ),
        "repository.manifest_files": _fact(
            manifest_paths,
            "metadata_verified",
            "openbot://snapshot/manifest-files",
        ),
        "dataset.signals": _fact(
            {
                "categories": signal_categories,
                "video_streams": list(snapshot.video_keys),
            },
            maturity,
            "openbot://snapshot/signals",
        ),
        "dataset.annotation_fields": _fact(
            {
                "task_labels": tasks,
                "coverage": "metadata_only" if tasks else "unverified",
            },
            "metadata_verified" if tasks else "official_claim",
            "openbot://snapshot/annotations",
        ),
        "dataset.scale": _fact(
            {
                "episodes": len(snapshot.episodes),
                "videos": len(snapshot.videos),
                "frames": sum(video.frame_count for video in snapshot.videos),
                "duration_seconds": round(
                    sum(video.duration for video in snapshot.videos),
                    6,
                ),
                "size_bytes": sum(video.size_bytes for video in snapshot.videos),
            },
            "metadata_verified",
            "openbot://snapshot/scale",
        ),
        "dataset.sample": _fact(
            coverage["videos"],
            maturity,
            "openbot://audit/coverage/videos",
        ),
        "dataset.integrity": _fact(
            {
                "summary": dict(audit["summary"]),
                "error_codes": sorted(
                    {
                        finding["code"]
                        for finding in audit["findings"]
                        if finding["severity"] == "error"
                    }
                ),
                "warning_codes": sorted(
                    {
                        finding["code"]
                        for finding in audit["findings"]
                        if finding["severity"] == "warning"
                    }
                ),
            },
            maturity,
            "openbot://audit/findings",
        ),
        "dataset.profile_readiness": _fact(
            {
                "profile_id": profile_id.strip(),
                "status": profile_status,
                "blocking_findings": [
                    finding["code"]
                    for finding in audit["findings"]
                    if finding["severity"] == "error"
                ],
            },
            maturity,
            "openbot://audit/profile-readiness",
        ),
    }
    findings = [dict(finding) for finding in audit["findings"]]
    unresolved = _unresolved_checks(snapshot, checksum, integrity)
    fingerprint_payload = {
        "schema_version": CATALOG_EVIDENCE_SCHEMA_VERSION,
        "dataset": {
            "id": normalized_dataset_id,
            "source_kind": normalized_source_kind,
            "source_locator": normalized_locator,
            "resolved_revision": normalized_revision,
            "snapshot_fingerprint": _snapshot_fingerprint(snapshot),
        },
        "audit": {
            "schema_version": audit["schema_version"],
            "profile_id": profile_id.strip(),
            "rule_pack_version": rule_pack_version.strip(),
            "integrity_level": integrity,
        },
        "evidence_maturity": maturity,
        "coverage": coverage,
        "facts": facts,
        "findings": findings,
        "unresolved_checks": unresolved,
    }
    result = {
        **fingerprint_payload,
        "tool": {"name": "openbot-data", "version": __version__},
        "checked_at": normalized_checked_at,
        "evidence_fingerprint": canonical_evidence_sha256(fingerprint_payload),
    }
    if output_path is not None:
        write_json_atomic(Path(output_path), result)
    return result
