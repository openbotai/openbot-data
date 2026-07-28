"""Canonical audit, snapshot, and readiness projections for Hub sources."""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from openbot_data.audit import enrich_findings
from openbot_data.hub import (
    HubDownloadBudget,
    HubResolution,
    resolve_hub_dataset,
)
from openbot_data.preflight import (
    audit_dataset,
    dataset_fingerprint,
    prepare_dataset,
)
from openbot_data.readiness import evaluate_dataset_readiness
from openbot_data.serialization import write_json_atomic
from openbot_data.snapshot import build_dataset_snapshot
from openbot_data.triage import triage_findings

_SPARSE_PAYLOAD_CODES = {
    "LEROBOT_DATA_MISSING",
    "LEROBOT_VIDEO_MISSING",
    "LEROBOT_VIDEOS_MISSING",
}


def _resolve(
    source: str,
    *,
    integrity: str,
    budget: Optional[HubDownloadBudget],
    resolver: Any,
    revision_resolver: Any,
    downloader: Any,
    viewer_validator: Any,
    cache_dir: Optional[str],
    local_dir: Optional[str],
) -> HubResolution:
    return resolve_hub_dataset(
        source,
        integrity=integrity,
        budget=budget,
        resolver=resolver,
        revision_resolver=revision_resolver,
        downloader=downloader,
        viewer_validator=viewer_validator,
        cache_dir=cache_dir,
        local_dir=local_dir,
    )


def _audit_resolution(
    resolution: HubResolution,
    local_path: Path,
    *,
    checksum: Optional[str],
    follow_symlinks: bool,
) -> tuple[dict[str, Any], Any]:
    path_value = str(local_path)
    prepared = prepare_dataset(
        path_value,
        input_format="lerobot",
        checksum=checksum,
        integrity=resolution.integrity,
        follow_symlinks=follow_symlinks,
    )
    local = audit_dataset(
        path_value,
        input_format="lerobot",
        checksum=checksum,
        integrity=resolution.integrity,
        follow_symlinks=follow_symlinks,
        snapshot=prepared,
    )
    payload_download = resolution.coverage.get("downloads", {}).get(
        "payload",
        {},
    )
    payload_missing = (
        payload_download.get("missing_paths", [])
        if isinstance(payload_download, Mapping)
        else []
    )
    suppress_sparse_payload = (
        resolution.integrity == "metadata"
        or (
            resolution.integrity == "sample"
            and not payload_missing
        )
        or (
            resolution.integrity == "full"
            and not payload_download.get("completed", False)
        )
    )
    local_findings = (
        []
        if resolution.local_path is None
        else [
            item
            for item in local["findings"]
            if not (
                suppress_sparse_payload
                and item.get("code") in _SPARSE_PAYLOAD_CODES
            )
        ]
    )
    findings = list(
        enrich_findings(
            [
                *local_findings,
                *resolution.findings,
            ]
        )
    )
    counts = {
        severity: sum(item["severity"] == severity for item in findings)
        for severity in ("error", "warning", "info")
    }
    result = {
        **local,
        "source": dict(resolution.provenance),
        "summary": {
            "videos": local["summary"]["videos"],
            **counts,
        },
        "coverage": {
            **local["coverage"],
            "source": dict(resolution.coverage),
        },
        "findings": findings,
    }
    return result, prepared


@contextmanager
def _working_checkout(
    resolution: HubResolution,
) -> Iterator[Path]:
    if resolution.local_path is not None:
        yield resolution.local_path
        return
    with tempfile.TemporaryDirectory(prefix="openbot-data-hub-partial-") as root:
        yield Path(root)


def _partial_readiness(result: dict[str, Any]) -> dict[str, Any]:
    """Demote unknown contract checks when a budget prevented metadata access."""
    blockers = [
        dict(item)
        for item in result.get("blocking_findings", [])
        if isinstance(item, Mapping)
    ]
    warnings = [
        dict(item)
        for item in result.get("warnings", [])
        if isinstance(item, Mapping)
    ]
    for finding in blockers:
        finding["severity"] = "warning"
        evidence = finding.get("evidence")
        normalized_evidence = (
            dict(evidence) if isinstance(evidence, Mapping) else {}
        )
        normalized_evidence["evaluation_state"] = (
            "not_evaluated_due_to_source_budget"
        )
        finding["evidence"] = normalized_evidence
        warnings.append(finding)
    warnings = sorted(
        warnings,
        key=lambda item: (
            str(item.get("code", "")),
            json.dumps(
                item.get("location", {}),
                sort_keys=True,
                separators=(",", ":"),
            ),
            json.dumps(
                item.get("evidence", {}),
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )
    result["status"] = "PARTIAL"
    result["blocking_findings"] = []
    result["warnings"] = warnings
    result["triage"] = triage_findings(warnings)
    fingerprint_payload = {
        key: value
        for key, value in result.items()
        if key not in {"tool", "readiness_fingerprint"}
    }
    result["readiness_fingerprint"] = dataset_fingerprint(
        fingerprint_payload
    )
    return result


def audit_hub_dataset(
    source: str,
    *,
    checksum: Optional[str] = "sha256",
    integrity: str = "metadata",
    follow_symlinks: bool = False,
    budget: Optional[HubDownloadBudget] = None,
    resolver: Any = None,
    revision_resolver: Any = None,
    downloader: Any = None,
    viewer_validator: Any = None,
    cache_dir: Optional[str] = None,
    local_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve one immutable Hub revision and write its canonical audit."""
    resolution = _resolve(
        source,
        integrity=integrity,
        budget=budget,
        resolver=resolver,
        revision_resolver=revision_resolver,
        downloader=downloader,
        viewer_validator=viewer_validator,
        cache_dir=cache_dir,
        local_dir=local_dir,
    )
    with _working_checkout(resolution) as local_path:
        result, _prepared = _audit_resolution(
            resolution,
            local_path,
            checksum=checksum,
            follow_symlinks=follow_symlinks,
        )
    if output_path is not None:
        write_json_atomic(Path(output_path), result)
    return result


def snapshot_hub_dataset(
    source: str,
    *,
    checksum: Optional[str] = "sha256",
    integrity: str = "metadata",
    follow_symlinks: bool = False,
    budget: Optional[HubDownloadBudget] = None,
    resolver: Any = None,
    revision_resolver: Any = None,
    downloader: Any = None,
    viewer_validator: Any = None,
    cache_dir: Optional[str] = None,
    local_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve one immutable Hub revision and write a portable snapshot."""
    resolution = _resolve(
        source,
        integrity=integrity,
        budget=budget,
        resolver=resolver,
        revision_resolver=revision_resolver,
        downloader=downloader,
        viewer_validator=viewer_validator,
        cache_dir=cache_dir,
        local_dir=local_dir,
    )
    with _working_checkout(resolution) as local_root:
        local_path = str(local_root)
        prepared = prepare_dataset(
            local_path,
            input_format="lerobot",
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
        )
        return build_dataset_snapshot(
            local_path,
            input_format="lerobot",
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
            source_kind="hf_hub",
            source_locator=resolution.request.locator,
            requested_revision=resolution.request.requested_revision,
            resolved_revision=resolution.resolved_revision,
            source_coverage=resolution.coverage,
            snapshot=prepared,
            output_path=output_path,
        )


def evaluate_hub_dataset_readiness(
    source: str,
    *,
    profile: str = "lerobot-core",
    policy_config: Mapping[str, Any] | str | Path | None = None,
    checksum: Optional[str] = "sha256",
    integrity: str = "metadata",
    follow_symlinks: bool = False,
    budget: Optional[HubDownloadBudget] = None,
    resolver: Any = None,
    revision_resolver: Any = None,
    downloader: Any = None,
    viewer_validator: Any = None,
    cache_dir: Optional[str] = None,
    local_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve/download once, then project one audit and readiness result."""
    resolution = _resolve(
        source,
        integrity=integrity,
        budget=budget,
        resolver=resolver,
        revision_resolver=revision_resolver,
        downloader=downloader,
        viewer_validator=viewer_validator,
        cache_dir=cache_dir,
        local_dir=local_dir,
    )
    with _working_checkout(resolution) as local_root:
        audit, prepared = _audit_resolution(
            resolution,
            local_root,
            checksum=checksum,
            follow_symlinks=follow_symlinks,
        )
        local_path = str(local_root)
        snapshot = build_dataset_snapshot(
            local_path,
            input_format="lerobot",
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
            source_kind="hf_hub",
            source_locator=resolution.request.locator,
            requested_revision=resolution.request.requested_revision,
            resolved_revision=resolution.resolved_revision,
            source_coverage=resolution.coverage,
            snapshot=prepared,
        )
        result = evaluate_dataset_readiness(
            local_path,
            profile=profile,
            policy_config=policy_config,
            input_format="lerobot",
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
            prepared=prepared,
            dataset_snapshot=snapshot,
            audit_result=audit,
            source_kind="hf_hub",
            source_locator=resolution.request.locator,
            requested_revision=resolution.request.requested_revision,
            resolved_revision=resolution.resolved_revision,
            publication_metadata=resolution.publication_metadata,
            output_path=None,
        )
        if resolution.local_path is None:
            result = _partial_readiness(result)
        if output_path is not None:
            write_json_atomic(Path(output_path), result)
        return result


__all__ = [
    "audit_hub_dataset",
    "evaluate_hub_dataset_readiness",
    "snapshot_hub_dataset",
]
