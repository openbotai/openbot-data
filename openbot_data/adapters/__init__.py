"""Built-in dataset adapter selection.

Only the fixed LeRobot v2.1 and v3.0 readers are registered for ``0.0.3``.
Arbitrary third-party plugin discovery is intentionally out of scope.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, cast

from openbot_data.adapters._common import (
    capability,
    dataset_file_status,
    finding,
    path_policy_finding,
    safe_error,
    sorted_capabilities,
    sorted_findings,
)
from openbot_data.adapters.base import (
    AdapterResult,
    DatasetAdapter,
    DiscoveryRequest,
    FormatProbeResult,
    FormatVersion,
    freeze_value,
)
from openbot_data.adapters.lerobot_v21 import ADAPTER as LEROBOT_V21_ADAPTER
from openbot_data.adapters.lerobot_v30 import ADAPTER as LEROBOT_V30_ADAPTER

_VERSION_PATTERN = re.compile(
    r"^v?(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)"
    r"(?:\.(?P<patch>0|[1-9][0-9]*))?$"
)
_ADAPTERS_BY_MAJOR: Mapping[int, DatasetAdapter] = {
    2: cast(DatasetAdapter, LEROBOT_V21_ADAPTER),
    3: cast(DatasetAdapter, LEROBOT_V30_ADAPTER),
}
_KNOWN_CONTRACTS: Mapping[tuple[int, int], DatasetAdapter] = {
    (2, 1): cast(DatasetAdapter, LEROBOT_V21_ADAPTER),
    (3, 0): cast(DatasetAdapter, LEROBOT_V30_ADAPTER),
}


def _read_info(
    root: Path,
    *,
    follow_symlinks: bool,
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    findings: list[Mapping[str, Any]] = []
    path = root / "meta/info.json"
    if not root.is_dir():
        findings.append(
            finding(
                "DATASET_NOT_FOUND",
                "error",
                "metadata",
                "Dataset directory was not found.",
                ".",
            )
        )
        return {}, findings
    status = dataset_file_status(
        root,
        "meta/info.json",
        follow_symlinks=follow_symlinks,
    )
    policy_finding = path_policy_finding(status, "meta/info.json")
    if policy_finding is not None:
        findings.append(policy_finding)
    if status != "valid":
        findings.extend(
            [
                finding(
                    "LEROBOT_INFO_MISSING",
                    "error",
                    "metadata",
                    "LeRobot meta/info.json is missing.",
                    "meta/info.json",
                ),
                finding(
                    "LEROBOT_CODEBASE_VERSION_MISSING",
                    "error",
                    "metadata",
                    "LeRobot codebase_version cannot be determined without meta/info.json.",
                    "meta/info.json",
                ),
            ]
        )
        return {}, findings
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(
            finding(
                "LEROBOT_METADATA_INVALID",
                "error",
                "metadata",
                "LeRobot meta/info.json is not valid JSON.",
                "meta/info.json",
                {"error": safe_error(exc, root)},
            )
        )
        return {}, findings
    if not isinstance(value, dict):
        findings.append(
            finding(
                "LEROBOT_METADATA_INVALID",
                "error",
                "metadata",
                "LeRobot meta/info.json must be a JSON object.",
                "meta/info.json",
            )
        )
        return {}, findings
    return value, findings


def probe_version(
    root: str,
    *,
    follow_symlinks: bool = False,
) -> FormatProbeResult:
    """Probe ``meta/info.json.codebase_version`` and select no more than one adapter."""
    resolved_root = Path(root).resolve()
    info, findings = _read_info(
        resolved_root,
        follow_symlinks=follow_symlinks,
    )
    raw_info = freeze_value(info)
    if findings:
        return FormatProbeResult(
            root=resolved_root,
            declared_version=None,
            parsed_version=None,
            adapter_id=None,
            compatibility="missing" if not info else "invalid",
            raw_info=raw_info,
            findings=sorted_findings(findings),
        )

    raw_version = info.get("codebase_version")
    if raw_version is None:
        findings.append(
            finding(
                "LEROBOT_CODEBASE_VERSION_MISSING",
                "error",
                "metadata",
                "LeRobot meta/info.json has no codebase_version.",
                "meta/info.json",
                {"supported_contracts": ["v2.1", "v3.0"]},
            )
        )
        return FormatProbeResult(
            root=resolved_root,
            declared_version=None,
            parsed_version=None,
            adapter_id=None,
            compatibility="missing",
            raw_info=raw_info,
            findings=sorted_findings(findings),
        )
    if not isinstance(raw_version, str):
        findings.append(
            finding(
                "LEROBOT_CODEBASE_VERSION_INVALID",
                "error",
                "metadata",
                "LeRobot codebase_version must be a version string.",
                "meta/info.json",
                {"value_type": type(raw_version).__name__},
            )
        )
        return FormatProbeResult(
            root=resolved_root,
            declared_version=None,
            parsed_version=None,
            adapter_id=None,
            compatibility="invalid",
            raw_info=raw_info,
            findings=sorted_findings(findings),
        )

    match = _VERSION_PATTERN.fullmatch(raw_version.strip())
    if match is None:
        findings.append(
            finding(
                "LEROBOT_CODEBASE_VERSION_INVALID",
                "error",
                "metadata",
                "LeRobot codebase_version is not a supported version syntax.",
                "meta/info.json",
                {"declared_version": raw_version},
            )
        )
        return FormatProbeResult(
            root=resolved_root,
            declared_version=raw_version,
            parsed_version=None,
            adapter_id=None,
            compatibility="invalid",
            raw_info=raw_info,
            findings=sorted_findings(findings),
        )

    patch_text = match.group("patch")
    version = FormatVersion(
        raw=raw_version,
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(patch_text) if patch_text is not None else None,
    )
    exact_adapter = _KNOWN_CONTRACTS.get((version.major, version.minor))
    if exact_adapter is not None and version.patch in {None, 0}:
        return FormatProbeResult(
            root=resolved_root,
            declared_version=raw_version,
            parsed_version=version,
            adapter_id=exact_adapter.adapter_id,
            compatibility="exact",
            raw_info=raw_info,
            findings=(),
        )

    major_adapter = _ADAPTERS_BY_MAJOR.get(version.major)
    if major_adapter is not None:
        compatibility_kind = (
            "unknown_patch"
            if exact_adapter is not None and version.patch not in {None, 0}
            else "unknown_minor"
        )
        findings.append(
            finding(
                "LEROBOT_CODEBASE_VERSION_UNTESTED",
                "warning",
                "metadata",
                "LeRobot version is inspected provisionally with the same-major adapter.",
                "meta/info.json",
                {
                    "declared_version": raw_version,
                    "selected_adapter": major_adapter.adapter_id,
                    "supported_contract": (
                        f"v{major_adapter.major_version}.{major_adapter.contract_minor}"
                    ),
                    "compatibility_kind": compatibility_kind,
                },
            )
        )
        return FormatProbeResult(
            root=resolved_root,
            declared_version=raw_version,
            parsed_version=version,
            adapter_id=major_adapter.adapter_id,
            compatibility="unknown_minor",
            raw_info=raw_info,
            findings=sorted_findings(findings),
        )

    findings.append(
        finding(
            "LEROBOT_CODEBASE_VERSION_UNSUPPORTED",
            "error",
            "metadata",
            "LeRobot major version has no compatible OpenBot Data adapter.",
            "meta/info.json",
            {
                "declared_version": raw_version,
                "declared_major": version.major,
                "supported_contracts": ["v2.1", "v3.0"],
            },
        )
    )
    return FormatProbeResult(
        root=resolved_root,
        declared_version=raw_version,
        parsed_version=version,
        adapter_id=None,
        compatibility="unsupported_major",
        raw_info=raw_info,
        findings=sorted_findings(findings),
    )


def select_adapter(probe: FormatProbeResult) -> Optional[DatasetAdapter]:
    """Return the statically registered adapter selected by ``probe``."""
    for adapter in _ADAPTERS_BY_MAJOR.values():
        if adapter.adapter_id == probe.adapter_id:
            return adapter
    return None


def read_lerobot_dataset(
    root: str,
    request: Optional[DiscoveryRequest] = None,
) -> AdapterResult:
    """Probe and read one local LeRobot dataset through a fixed built-in adapter."""
    normalized_request = request or DiscoveryRequest()
    if normalized_request.parquet_batch_size <= 0:
        raise ValueError("parquet_batch_size must be positive")
    probe = probe_version(
        root,
        follow_symlinks=normalized_request.follow_symlinks,
    )
    adapter = select_adapter(probe)
    if adapter is not None:
        return adapter.read(probe, normalized_request)

    # A malformed/missing info file must not prevent collect-all validation of
    # an otherwise unambiguous canonical metadata layout.
    v21_layout = (probe.root / "meta/episodes.jsonl").is_file()
    v30_layout = bool(tuple(probe.root.glob("meta/episodes/*/*.parquet")))
    if probe.parsed_version is None and v21_layout != v30_layout:
        layout_adapter = (
            cast(DatasetAdapter, LEROBOT_V21_ADAPTER)
            if v21_layout
            else cast(DatasetAdapter, LEROBOT_V30_ADAPTER)
        )
        return layout_adapter.read(probe, normalized_request)

    fallback_findings = list(probe.findings)
    episode_jsonl = probe.root / "meta/episodes.jsonl"
    episode_parquet = tuple(
        probe.root.glob("meta/episodes/*/*.parquet")
    )
    if not episode_jsonl.is_file() and not episode_parquet:
        fallback_findings.append(
            finding(
                "LEROBOT_EPISODES_MISSING",
                "error",
                "metadata",
                "LeRobot episode metadata is missing.",
                "meta/episodes",
            )
        )
    capabilities = [
        capability(
            "metadata.info",
            "complete" if probe.raw_info else "failed",
            checked=1 if probe.raw_info else 0,
            total=1,
        )
    ]
    for name in (
        "alignment.data_relations",
        "alignment.video_relations",
        "data.inventory",
        "media.inventory",
        "metadata.episodes",
        "metadata.stats",
        "metadata.tasks",
    ):
        capabilities.append(
            capability(
                name,
                "skipped",
                reason="no_compatible_adapter",
            )
        )
    return AdapterResult(
        adapter_id=None,
        declared_version=probe.declared_version,
        compatibility=probe.compatibility,
        raw_info=probe.raw_info,
        capabilities=sorted_capabilities(capabilities),
        findings=sorted_findings(fallback_findings),
    )


__all__ = [
    "AdapterResult",
    "DiscoveryRequest",
    "FormatProbeResult",
    "LEROBOT_V21_ADAPTER",
    "LEROBOT_V30_ADAPTER",
    "probe_version",
    "read_lerobot_dataset",
    "select_adapter",
]
