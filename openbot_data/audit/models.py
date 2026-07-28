"""Typed contracts for capability-aware, deterministic audit rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Iterable,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Tuple,
)

from openbot_data.models import DatasetSnapshot

CapabilityStatus = Literal["complete", "partial", "skipped", "unavailable"]
CoverageRequirement = Literal["complete", "available"]
FindingLayer = Literal[
    "metadata",
    "schema",
    "data",
    "media",
    "alignment",
    "provenance",
]
FindingSeverity = Literal["error", "warning", "info"]
Fixability = Literal["automatic", "delegated", "manual", "not_repairable"]
IntegrityLevel = Literal["metadata", "sample", "full"]

CAPABILITY_STATUSES = {"complete", "partial", "skipped", "unavailable"}
COVERAGE_REQUIREMENTS = {"complete", "available"}
FINDING_LAYERS = {
    "metadata",
    "schema",
    "data",
    "media",
    "alignment",
    "provenance",
}
FINDING_SEVERITIES = {"error", "warning", "info"}
FIXABILITY_VALUES = {"automatic", "delegated", "manual", "not_repairable"}
INTEGRITY_LEVELS = {"metadata", "sample", "full"}


def _require_nonempty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True)
class CapabilityCoverage:
    """Observed execution coverage for one stable audit capability.

    Coverage describes whether a check could run; it does not describe whether
    the dataset passed the check.
    """

    capability: str
    status: CapabilityStatus
    integrity: IntegrityLevel
    checked: int = 0
    total: Optional[int] = None
    selected: Tuple[str, ...] = ()
    omitted: Tuple[str, ...] = ()
    reason_code: Optional[str] = None
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        _require_nonempty(self.capability, "capability")
        if self.status not in CAPABILITY_STATUSES:
            raise ValueError(f"unsupported capability status: {self.status}")
        if self.integrity not in INTEGRITY_LEVELS:
            raise ValueError(f"unsupported integrity level: {self.integrity}")
        if self.checked < 0:
            raise ValueError("checked must be non-negative")
        if self.total is not None and self.total < 0:
            raise ValueError("total must be non-negative")
        if self.total is not None and self.checked > self.total:
            raise ValueError("checked cannot exceed total")
        if self.reason_code is not None:
            _require_nonempty(self.reason_code, "reason_code")
        if self.reason is not None:
            _require_nonempty(self.reason, "reason")

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "capability": self.capability,
            "status": self.status,
            "integrity": self.integrity,
            "checked": self.checked,
            "total": self.total,
            "selected": list(self.selected),
            "omitted": list(self.omitted),
        }
        if self.reason_code is not None:
            result["reason_code"] = self.reason_code
        if self.reason is not None:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class CapabilityRequirement:
    """A rule's minimum capability and coverage contract."""

    capability: str
    minimum_integrity: IntegrityLevel = "metadata"
    coverage: CoverageRequirement = "complete"

    def __post_init__(self) -> None:
        _require_nonempty(self.capability, "capability")
        if self.minimum_integrity not in INTEGRITY_LEVELS:
            raise ValueError(
                f"unsupported minimum integrity level: {self.minimum_integrity}"
            )
        if self.coverage not in COVERAGE_REQUIREMENTS:
            raise ValueError(f"unsupported coverage requirement: {self.coverage}")


@dataclass(frozen=True)
class SkippedCheck:
    """One applicable rule scope that could not be fully evaluated."""

    rule_id: str
    layer: FindingLayer
    missing_capabilities: Tuple[str, ...]
    reason_code: str
    reason: str
    path: Optional[str] = None
    location: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonempty(self.rule_id, "rule_id")
        if self.layer not in FINDING_LAYERS:
            raise ValueError(f"unsupported finding layer: {self.layer}")
        if not self.missing_capabilities:
            raise ValueError("missing_capabilities must not be empty")
        if any(not item.strip() for item in self.missing_capabilities):
            raise ValueError("missing_capabilities cannot contain empty values")
        _require_nonempty(self.reason_code, "reason_code")
        _require_nonempty(self.reason, "reason")

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "rule_id": self.rule_id,
            "layer": self.layer,
            "missing_capabilities": list(self.missing_capabilities),
            "reason_code": self.reason_code,
            "reason": self.reason,
            "location": dict(self.location),
        }
        if self.path is not None:
            result["path"] = self.path
        return result


@dataclass(frozen=True)
class RuleSpec:
    """Static registry metadata for one stable finding code."""

    rule_id: str
    code: str
    layer: FindingLayer
    default_severity: FindingSeverity
    message: str
    applies_to: Tuple[str, ...]
    requirements: Tuple[CapabilityRequirement, ...]
    evidence_fields: Tuple[str, ...]
    location_fields: Tuple[str, ...]
    impact: str
    fixability: Fixability
    remediation_ref: str
    ordering_fields: Tuple[str, ...]
    positive_fixture: str
    negative_fixture: str
    numeric_contract: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.rule_id, "rule_id"),
            (self.code, "code"),
            (self.message, "message"),
            (self.impact, "impact"),
            (self.remediation_ref, "remediation_ref"),
            (self.positive_fixture, "positive_fixture"),
            (self.negative_fixture, "negative_fixture"),
        ):
            _require_nonempty(value, field_name)
        if self.layer not in FINDING_LAYERS:
            raise ValueError(f"unsupported finding layer: {self.layer}")
        if self.default_severity not in FINDING_SEVERITIES:
            raise ValueError(f"unsupported finding severity: {self.default_severity}")
        if self.fixability not in FIXABILITY_VALUES:
            raise ValueError(f"unsupported fixability: {self.fixability}")
        if not self.applies_to:
            raise ValueError("applies_to must not be empty")


@dataclass(frozen=True)
class FindingDraft:
    """Rule-produced evidence before registry metadata is applied."""

    code: str
    message: str
    path: Optional[str] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    location: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleContext:
    """Immutable, I/O-free input passed to audit rules."""

    snapshot: DatasetSnapshot
    capabilities: Tuple[CapabilityCoverage, ...]

    @property
    def input_format(self) -> str:
        return self.snapshot.input_format

    @property
    def integrity(self) -> str:
        return self.snapshot.integrity

    def capability(self, capability: str) -> Optional[CapabilityCoverage]:
        return next(
            (
                coverage
                for coverage in self.capabilities
                if coverage.capability == capability
            ),
            None,
        )


class AuditRule(Protocol):
    """Static rule protocol; implementations must not perform source I/O."""

    spec: RuleSpec

    def evaluate(self, context: RuleContext) -> Iterable[FindingDraft]:
        """Return every safely discoverable finding for this prepared context."""


RuleEvaluator = Callable[[RuleContext], Iterable[FindingDraft]]


@dataclass(frozen=True)
class StaticAuditRule:
    """Small immutable implementation used by the built-in rule pack."""

    spec: RuleSpec
    evaluator: RuleEvaluator = field(repr=False, compare=False)

    def evaluate(self, context: RuleContext) -> Iterable[FindingDraft]:
        return self.evaluator(context)


@dataclass(frozen=True)
class AuditRun:
    """Deterministic internal result shared by audit projections."""

    rule_pack_version: str
    findings: Tuple[dict[str, Any], ...]
    capabilities: Tuple[CapabilityCoverage, ...]
    skipped_checks: Tuple[SkippedCheck, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_pack_version": self.rule_pack_version,
            "coverage": {
                "capabilities": [item.as_dict() for item in self.capabilities],
            },
            "skipped_checks": [item.as_dict() for item in self.skipped_checks],
            "findings": [dict(item) for item in self.findings],
        }
