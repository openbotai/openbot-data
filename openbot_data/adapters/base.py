"""Internal contracts for format-specific dataset readers.

The adapter layer deliberately has no dependency on the public renderers.  It
captures format truth once, then lets the existing preflight facade project the
result into backwards-compatible manifests and audits.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional, Protocol, Tuple

Finding = Mapping[str, Any]


def _empty_mapping() -> Mapping[str, Any]:
    return MappingProxyType({})


def freeze_value(value: Any) -> Any:
    """Return a recursively immutable copy of JSON-like metadata."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): freeze_value(item)
                for key, item in copy.deepcopy(dict(value)).items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    return copy.deepcopy(value)


def thaw_value(value: Any) -> Any:
    """Return mutable JSON-compatible containers from :func:`freeze_value`."""
    if isinstance(value, Mapping):
        return {str(key): thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_value(item) for item in value]
    return copy.deepcopy(value)


@dataclass(frozen=True)
class FormatVersion:
    """A parsed LeRobot storage-contract version."""

    raw: str
    major: int
    minor: int
    patch: Optional[int] = None


@dataclass(frozen=True)
class FormatProbeResult:
    """Version probe result used for deterministic static adapter selection."""

    root: Path = field(repr=False, compare=False)
    declared_version: Optional[str]
    parsed_version: Optional[FormatVersion]
    adapter_id: Optional[str]
    compatibility: str
    raw_info: Mapping[str, Any] = field(default_factory=_empty_mapping)
    findings: Tuple[Finding, ...] = ()


@dataclass(frozen=True)
class DiscoveryRequest:
    """Capabilities and cost boundary requested from one adapter read."""

    integrity: str = "metadata"
    checksum: Optional[str] = None
    follow_symlinks: bool = False
    required_capabilities: Tuple[str, ...] = ()
    parquet_batch_size: int = 1024


@dataclass(frozen=True)
class TaskRecord:
    """One normalized task-table entry."""

    task_index: int
    task: str
    source_path: str
    source_row: int
    extensions: Mapping[str, Any] = field(default_factory=_empty_mapping)


@dataclass(frozen=True)
class EpisodeMetadata:
    """One normalized episode record with adapter-only relation metadata."""

    episode_index: int
    length: Optional[int]
    tasks: Tuple[str, ...]
    source_path: str
    source_row: int
    data_path: Optional[str] = None
    dataset_from_index: Optional[int] = None
    dataset_to_index: Optional[int] = None
    video_paths: Tuple[str, ...] = ()
    extensions: Mapping[str, Any] = field(default_factory=_empty_mapping)

    def as_legacy_dict(self) -> dict[str, Any]:
        """Project only the fields exposed by the existing preflight result."""
        return {
            "episode_index": self.episode_index,
            "length": self.length,
            "tasks": list(self.tasks),
            "video_files": list(self.video_paths),
        }


@dataclass(frozen=True)
class ArtifactRecord:
    """One portable dataset-relative file observation."""

    kind: str
    path: str
    exists: bool
    source: str
    episode_index: Optional[int] = None
    feature_key: Optional[str] = None
    size_bytes: Optional[int] = None
    row_count: Optional[int] = None
    columns: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationRecord:
    """A normalized episode-to-shard relationship."""

    kind: str
    episode_index: int
    path: str
    exists: bool
    feature_key: Optional[str] = None
    chunk_index: Optional[int] = None
    file_index: Optional[int] = None
    dataset_from_index: Optional[int] = None
    dataset_to_index: Optional[int] = None
    from_timestamp: Optional[float] = None
    to_timestamp: Optional[float] = None


@dataclass(frozen=True)
class CapabilityStatus:
    """Deterministic coverage state for one reader capability."""

    name: str
    status: str
    reason: Optional[str] = None
    checked: Optional[int] = None
    total: Optional[int] = None


@dataclass(frozen=True)
class AdapterResult:
    """Complete immutable output from one selected format adapter."""

    adapter_id: Optional[str]
    declared_version: Optional[str]
    compatibility: str
    raw_info: Mapping[str, Any]
    episodes: Tuple[EpisodeMetadata, ...] = ()
    tasks: Tuple[TaskRecord, ...] = ()
    artifacts: Tuple[ArtifactRecord, ...] = ()
    relations: Tuple[RelationRecord, ...] = ()
    capabilities: Tuple[CapabilityStatus, ...] = ()
    findings: Tuple[Finding, ...] = ()

    @property
    def video_paths(self) -> Tuple[str, ...]:
        return tuple(
            sorted(
                {
                    artifact.path
                    for artifact in self.artifacts
                    if artifact.kind == "video" and artifact.exists
                }
            )
        )

    @property
    def video_keys(self) -> Tuple[str, ...]:
        declared = set()
        features = self.raw_info.get("features")
        if isinstance(features, Mapping):
            for key, feature in features.items():
                if not isinstance(feature, Mapping):
                    continue
                if (
                    str(feature.get("dtype", "")).lower() == "video"
                    or isinstance(feature.get("video_info"), Mapping)
                ):
                    declared.add(str(key))
        return tuple(
            sorted(
                declared
                | {
                    relation.feature_key
                    for relation in self.relations
                    if relation.kind == "video" and relation.feature_key is not None
                }
            )
        )

    def as_legacy_discovery(self) -> dict[str, Any]:
        """Return the shape consumed by the current ``read_lerobot`` facade."""
        return {
            "format": "lerobot",
            "codebase_version": self.declared_version,
            "episodes": [episode.as_legacy_dict() for episode in self.episodes],
            "video_keys": list(self.video_keys),
            "videos": list(self.video_paths),
            "findings": [thaw_value(item) for item in self.findings],
        }


class DatasetAdapter(Protocol):
    """Static internal reader interface; third-party discovery is intentionally absent."""

    adapter_id: str
    major_version: int
    contract_minor: int
    capabilities: Tuple[str, ...]

    def read(
        self,
        probe: FormatProbeResult,
        request: DiscoveryRequest,
    ) -> AdapterResult:
        """Read one already-probed dataset root without rescanning in a renderer."""
