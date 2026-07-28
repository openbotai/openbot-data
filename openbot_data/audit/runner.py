"""Capability-aware collect-all runner for static audit rules."""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, Mapping, Optional, Sequence, cast

from openbot_data.audit.models import (
    AuditRule,
    AuditRun,
    CapabilityCoverage,
    CapabilityRequirement,
    FindingDraft,
    RuleContext,
    RuleSpec,
    SkippedCheck,
)
from openbot_data.audit.registry import FINDING_REGISTRY, get_rule_spec
from openbot_data.audit.rules import MEDIA_RULES
from openbot_data.models import DatasetSnapshot

AUDIT_RULE_PACK_VERSION = "openbot.dataset_audit.rules.v1"
_INTEGRITY_RANK = {"metadata": 0, "sample": 1, "full": 2}
_LAYER_RANK = {
    "metadata": 0,
    "schema": 1,
    "data": 2,
    "media": 3,
    "alignment": 4,
    "provenance": 5,
}


class FindingContractError(RuntimeError):
    """Raised when a rule violates the static finding registry contract."""


def _normalize_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise FindingContractError("finding object keys must be strings")
        normalized = {}
        for key in sorted(value):
            normalized[key] = _normalize_json(value[key])
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    raise FindingContractError(
        f"finding evidence must be JSON-compatible, got {type(value).__name__}"
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _normalize_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _location_for(
    spec: RuleSpec,
    evidence: Mapping[str, Any],
    explicit: Mapping[str, Any],
) -> dict[str, Any]:
    location = dict(_normalize_json(explicit))
    for field_name in spec.location_fields:
        if field_name not in location and field_name in evidence:
            location[field_name] = _normalize_json(evidence[field_name])
    return {key: location[key] for key in sorted(location)}


def _registered_finding(
    finding: Mapping[str, Any],
    *,
    unknown_code_policy: str,
) -> dict[str, Any]:
    raw_code = finding.get("code")
    if not isinstance(raw_code, str) or not raw_code:
        raise FindingContractError("finding code must be a non-empty string")
    raw_severity = finding.get("severity")
    spec = get_rule_spec(
        raw_code,
        unknown_policy=unknown_code_policy,
    )
    if (
        raw_code in FINDING_REGISTRY
        and raw_severity is not None
        and raw_severity != spec.default_severity
    ):
        raise FindingContractError(
            f"{raw_code} severity {raw_severity!r} does not match "
            f"registry severity {spec.default_severity!r}"
        )
    raw_message = finding.get("message", spec.message)
    if not isinstance(raw_message, str) or not raw_message:
        raise FindingContractError(f"{raw_code} message must be a non-empty string")
    raw_path = finding.get("path")
    if raw_path is not None and not isinstance(raw_path, str):
        raise FindingContractError(f"{raw_code} path must be a string")
    raw_evidence = finding.get("evidence", {})
    raw_location = finding.get("location", {})
    if not isinstance(raw_evidence, Mapping):
        raise FindingContractError(f"{raw_code} evidence must be an object")
    if not isinstance(raw_location, Mapping):
        raise FindingContractError(f"{raw_code} location must be an object")
    evidence = dict(_normalize_json(raw_evidence))
    location = _location_for(spec, evidence, raw_location)
    result: dict[str, Any] = {
        "code": spec.code,
        "severity": spec.default_severity,
        "layer": spec.layer,
        "rule_id": spec.rule_id,
        "message": raw_message,
        "location": location,
        "evidence": evidence,
        "impact": spec.impact,
        "fixability": spec.fixability,
        "remediation_ref": spec.remediation_ref,
    }
    if raw_path is not None:
        result["path"] = raw_path
    return result


def _draft_finding(
    draft: FindingDraft,
    spec: RuleSpec,
    *,
    unknown_code_policy: str,
) -> dict[str, Any]:
    if draft.code != spec.code:
        raise FindingContractError(
            f"rule {spec.rule_id!r} emitted {draft.code!r}; expected {spec.code!r}"
        )
    return _registered_finding(
        {
            "code": draft.code,
            "message": draft.message,
            "path": draft.path,
            "evidence": draft.evidence,
            "location": draft.location,
        },
        unknown_code_policy=unknown_code_policy,
    )


def _sort_atom(value: Any) -> tuple[int, Any]:
    normalized = _normalize_json(value)
    if normalized is None:
        return (5, "")
    if isinstance(normalized, bool):
        return (0, int(normalized))
    if isinstance(normalized, (int, float)):
        return (1, float(normalized))
    if isinstance(normalized, str):
        return (2, normalized)
    return (3, _canonical_json(normalized))


def _finding_sort_key(finding: Mapping[str, Any]) -> tuple[Any, ...]:
    spec = get_rule_spec(
        str(finding["code"]),
        unknown_policy="fallback",
    )
    location = finding.get("location", {})
    evidence = finding.get("evidence", {})
    ordering = []
    for field_name in spec.ordering_fields:
        if isinstance(location, Mapping) and field_name in location:
            ordering.append(_sort_atom(location[field_name]))
        elif isinstance(evidence, Mapping) and field_name in evidence:
            ordering.append(_sort_atom(evidence[field_name]))
        else:
            ordering.append(_sort_atom(None))
    return (
        _LAYER_RANK[spec.layer],
        spec.rule_id,
        str(finding.get("path", "")),
        tuple(ordering),
        _canonical_json(location),
        _canonical_json(evidence),
        str(finding.get("message", "")),
        _canonical_json(finding),
    )


def _dedupe_findings(findings: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    ordered = sorted(findings, key=_finding_sort_key)
    selected: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for finding in ordered:
        key = (
            str(finding["code"]),
            str(finding.get("path", "")),
            _canonical_json(finding.get("location", {})),
            _canonical_json(finding.get("evidence", {})),
        )
        selected.setdefault(key, finding)
    return tuple(selected[key] for key in selected)


def enrich_findings(
    findings: Iterable[Mapping[str, Any]],
    *,
    unknown_code_policy: str = "error",
) -> tuple[dict[str, Any], ...]:
    """Apply the static finding contract without running any audit rule.

    This is used for source/configuration failures where no prepared dataset
    exists, so every canonical audit finding still carries the same layer,
    location, impact, fixability, and remediation metadata.
    """
    if unknown_code_policy not in {"error", "fallback"}:
        raise ValueError("unknown_code_policy must be 'error' or 'fallback'")
    return _dedupe_findings(
        _registered_finding(
            finding,
            unknown_code_policy=unknown_code_policy,
        )
        for finding in findings
    )


def _coverage_state(
    capability: str,
    integrity: str,
    checked: int,
    total: int,
    selected: Sequence[str],
    omitted: Sequence[str],
    *,
    skipped_reason: Optional[tuple[str, str]] = None,
) -> CapabilityCoverage:
    if skipped_reason is not None:
        status = "skipped"
        reason_code, reason = skipped_reason
    elif checked == total:
        status = "complete"
        reason_code = None
        reason = None
    elif checked > 0:
        status = "partial"
        reason_code = "partial_capability_coverage"
        reason = "Only part of the discovered capability scope was checked."
    else:
        status = "unavailable"
        reason_code = "source_capability_unavailable"
        reason = "The prepared dataset contains no usable evidence for this capability."
    return CapabilityCoverage(
        capability=capability,
        status=status,  # type: ignore[arg-type]
        integrity=integrity,  # type: ignore[arg-type]
        checked=checked,
        total=total,
        selected=tuple(sorted(set(selected))),
        omitted=tuple(sorted(set(omitted))),
        reason_code=reason_code,
        reason=reason,
    )


def infer_snapshot_capabilities(
    snapshot: DatasetSnapshot,
) -> tuple[CapabilityCoverage, ...]:
    """Infer execution coverage from already prepared records without source I/O."""
    integrity = snapshot.integrity
    videos = tuple(snapshot.videos)
    video_paths = tuple(video.path for video in videos)
    decoded = tuple(video.path for video in videos if video.decode_valid is not None)
    undecoded = tuple(video.path for video in videos if video.decode_valid is None)
    checksummed = tuple(
        video.path for video in videos if video.checksum_sha256 is not None
    )
    unchecked = tuple(
        video.path for video in videos if video.checksum_sha256 is None
    )
    if integrity == "metadata":
        decode = _coverage_state(
            "media.decode",
            integrity,
            0,
            len(videos),
            (),
            video_paths,
            skipped_reason=(
                "integrity_too_low",
                "Metadata integrity does not request media decoding.",
            ),
        )
    else:
        decode = _coverage_state(
            "media.decode",
            integrity,
            len(decoded),
            len(videos),
            decoded,
            undecoded,
        )
    if snapshot.checksum != "sha256" and not checksummed:
        checksum = _coverage_state(
            "content.checksum",
            integrity,
            0,
            len(videos),
            (),
            video_paths,
            skipped_reason=(
                "option_not_enabled",
                "SHA-256 content checking was not requested.",
            ),
        )
    else:
        checksum = _coverage_state(
            "content.checksum",
            integrity,
            len(checksummed),
            len(videos),
            checksummed,
            unchecked,
        )
    capabilities = [
        CapabilityCoverage(
            "source.identity",
            "complete",
            integrity,  # type: ignore[arg-type]
            checked=1,
            total=1,
        ),
        CapabilityCoverage(
            "format.contract",
            "complete",
            integrity,  # type: ignore[arg-type]
            checked=1,
            total=1,
        ),
        CapabilityCoverage(
            "media.inventory",
            "complete",
            integrity,  # type: ignore[arg-type]
            checked=len(videos),
            total=len(videos),
            selected=tuple(sorted(video_paths)),
        ),
        CapabilityCoverage(
            "media.metadata",
            "complete",
            integrity,  # type: ignore[arg-type]
            checked=len(videos),
            total=len(videos),
            selected=tuple(sorted(video_paths)),
        ),
        decode,
        checksum,
    ]
    adapter_result = snapshot.adapter_result
    if adapter_result is not None:
        compatibility = str(getattr(adapter_result, "compatibility", ""))
        format_status = (
            "complete"
            if compatibility == "exact"
            else "partial"
            if compatibility == "unknown_minor"
            else "unavailable"
        )
        capabilities = [
            item
            for item in capabilities
            if item.capability != "format.contract"
        ]
        capabilities.append(
            CapabilityCoverage(
                "format.contract",
                format_status,  # type: ignore[arg-type]
                integrity,  # type: ignore[arg-type]
                checked=1 if format_status in {"complete", "partial"} else 0,
                total=1,
                reason_code=(
                    None
                    if format_status == "complete"
                    else "format_contract_unverified"
                    if format_status == "partial"
                    else "format_contract_unsupported"
                ),
                reason=(
                    None
                    if format_status == "complete"
                    else "The selected format adapter is provisional."
                    if format_status == "partial"
                    else "No compatible format adapter was selected."
                ),
            )
        )
        by_name = {item.capability: item for item in capabilities}
        for raw in getattr(adapter_result, "capabilities", ()):
            raw_status = str(getattr(raw, "status", "unavailable"))
            status = (
                raw_status
                if raw_status in {"complete", "partial", "skipped", "unavailable"}
                else "unavailable"
            )
            checked_value = getattr(raw, "checked", None)
            total_value = getattr(raw, "total", None)
            checked = (
                int(checked_value)
                if isinstance(checked_value, int) and checked_value >= 0
                else 0
            )
            total = (
                int(total_value)
                if isinstance(total_value, int) and total_value >= 0
                else None
            )
            if total is not None:
                checked = min(checked, total)
            raw_reason = getattr(raw, "reason", None)
            reason_code = (
                str(raw_reason)
                if raw_reason is not None and status != "complete"
                else (
                    "adapter_capability_failed"
                    if raw_status == "failed"
                    else None
                )
            )
            reason = (
                reason_code.replace("_", " ").capitalize() + "."
                if reason_code is not None
                else None
            )
            name = str(getattr(raw, "name", "")).strip()
            if not name:
                continue
            by_name[name] = CapabilityCoverage(
                name,
                status,  # type: ignore[arg-type]
                integrity,  # type: ignore[arg-type]
                checked=checked,
                total=total,
                reason_code=reason_code,
                reason=reason,
            )
        capabilities = list(by_name.values())
    validation_result = snapshot.validation_result
    if validation_result is not None:
        by_name = {item.capability: item for item in capabilities}
        for coverage in getattr(validation_result, "capabilities", ()):
            by_name[coverage.capability] = coverage
        capabilities = list(by_name.values())
    return tuple(sorted(capabilities, key=lambda item: item.capability))


def _normalize_capabilities(
    capabilities: Iterable[CapabilityCoverage],
) -> tuple[CapabilityCoverage, ...]:
    by_name: dict[str, CapabilityCoverage] = {}
    for coverage in capabilities:
        normalized = CapabilityCoverage(
            capability=coverage.capability,
            status=coverage.status,
            integrity=coverage.integrity,
            checked=coverage.checked,
            total=coverage.total,
            selected=tuple(sorted(set(coverage.selected))),
            omitted=tuple(sorted(set(coverage.omitted))),
            reason_code=coverage.reason_code,
            reason=coverage.reason,
        )
        existing = by_name.get(normalized.capability)
        if existing is not None and existing != normalized:
            raise FindingContractError(
                f"conflicting coverage for capability {normalized.capability!r}"
            )
        by_name[normalized.capability] = normalized
    return tuple(by_name[name] for name in sorted(by_name))


def _skip_for_requirement(
    spec: RuleSpec,
    requirement: CapabilityRequirement,
    coverage: Optional[CapabilityCoverage],
    reason_code: str,
    reason: str,
) -> SkippedCheck:
    location: dict[str, Any] = {}
    if coverage is not None and coverage.omitted:
        location["omitted"] = list(coverage.omitted)
    return SkippedCheck(
        rule_id=spec.rule_id,
        layer=spec.layer,
        missing_capabilities=(requirement.capability,),
        reason_code=reason_code,
        reason=reason,
        location=location,
    )


def _requirements(
    spec: RuleSpec,
    context: RuleContext,
) -> tuple[bool, tuple[SkippedCheck, ...]]:
    can_run = True
    skipped = []
    for requirement in spec.requirements:
        coverage = context.capability(requirement.capability)
        if coverage is None:
            can_run = False
            skipped.append(
                _skip_for_requirement(
                    spec,
                    requirement,
                    None,
                    "capability_not_produced",
                    "The prepared dataset did not produce the required capability.",
                )
            )
            continue
        if _INTEGRITY_RANK[coverage.integrity] < _INTEGRITY_RANK[
            requirement.minimum_integrity
        ]:
            can_run = False
            skipped.append(
                _skip_for_requirement(
                    spec,
                    requirement,
                    coverage,
                    "integrity_too_low",
                    "The prepared integrity level is below the rule requirement.",
                )
            )
            continue
        if coverage.status in {"skipped", "unavailable"}:
            can_run = False
            skipped.append(
                _skip_for_requirement(
                    spec,
                    requirement,
                    coverage,
                    coverage.reason_code or "source_capability_unavailable",
                    coverage.reason
                    or "The required capability is unavailable in the prepared dataset.",
                )
            )
            continue
        if coverage.status == "partial":
            skipped.append(
                _skip_for_requirement(
                    spec,
                    requirement,
                    coverage,
                    coverage.reason_code or "partial_capability_coverage",
                    coverage.reason
                    or "The rule can inspect only part of the discovered scope.",
                )
            )
            if requirement.coverage == "complete":
                can_run = False
    return can_run, tuple(skipped)


def _skip_sort_key(item: SkippedCheck) -> tuple[str, str, str, str, str]:
    return (
        item.rule_id,
        item.reason_code,
        str(item.path or ""),
        ",".join(item.missing_capabilities),
        _canonical_json(item.location),
    )


def _dedupe_skips(skips: Iterable[SkippedCheck]) -> tuple[SkippedCheck, ...]:
    ordered = sorted(skips, key=_skip_sort_key)
    selected: dict[tuple[str, str, str, str, str], SkippedCheck] = {}
    for item in ordered:
        key = _skip_sort_key(item)
        selected.setdefault(key, item)
    return tuple(selected[key] for key in selected)


def _normalize_rules(rules: Iterable[AuditRule]) -> tuple[AuditRule, ...]:
    by_id: dict[str, AuditRule] = {}
    for rule in rules:
        registered = get_rule_spec(rule.spec.code)
        if rule.spec != registered:
            raise FindingContractError(
                f"rule {rule.spec.rule_id!r} does not match its registry metadata"
            )
        existing = by_id.get(rule.spec.rule_id)
        if existing is not None:
            raise FindingContractError(
                f"duplicate definition for rule {rule.spec.rule_id!r}"
            )
        by_id[rule.spec.rule_id] = rule
    return tuple(by_id[rule_id] for rule_id in sorted(by_id))


def run_audit_rules(
    snapshot: DatasetSnapshot,
    *,
    rules: Optional[Iterable[AuditRule]] = None,
    capabilities: Optional[Iterable[CapabilityCoverage]] = None,
    seed_findings: Optional[Iterable[Mapping[str, Any]]] = None,
    rule_pack_version: str = AUDIT_RULE_PACK_VERSION,
    unknown_code_policy: str = "error",
) -> AuditRun:
    """Run a static rule pack over a prepared snapshot without source rescans."""
    if not rule_pack_version.strip():
        raise ValueError("rule_pack_version must not be empty")
    if unknown_code_policy not in {"error", "fallback"}:
        raise ValueError("unknown_code_policy must be 'error' or 'fallback'")
    prepared_capabilities = _normalize_capabilities(
        infer_snapshot_capabilities(snapshot)
        if capabilities is None
        else capabilities
    )
    context = RuleContext(snapshot=snapshot, capabilities=prepared_capabilities)
    selected_findings: Iterable[Mapping[str, Any]] = (
        snapshot.findings if seed_findings is None else seed_findings
    )
    normalized_findings = [
        _registered_finding(
            finding,
            unknown_code_policy=unknown_code_policy,
        )
        for finding in selected_findings
    ]
    skipped: list[SkippedCheck] = []
    rule_source = (
        cast(Iterable[AuditRule], MEDIA_RULES)
        if rules is None
        else rules
    )
    selected_rules = _normalize_rules(rule_source)
    for rule in selected_rules:
        if "*" not in rule.spec.applies_to and context.input_format not in rule.spec.applies_to:
            continue
        can_run, rule_skips = _requirements(rule.spec, context)
        skipped.extend(rule_skips)
        if not can_run:
            continue
        for draft in rule.evaluate(context):
            normalized_findings.append(
                _draft_finding(
                    draft,
                    rule.spec,
                    unknown_code_policy=unknown_code_policy,
                )
            )
    return AuditRun(
        rule_pack_version=rule_pack_version,
        findings=_dedupe_findings(normalized_findings),
        capabilities=prepared_capabilities,
        skipped_checks=_dedupe_skips(skipped),
    )
