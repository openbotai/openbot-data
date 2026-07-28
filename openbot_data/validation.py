"""Layered validation for an already discovered LeRobot dataset.

The adapter layer owns format discovery.  This module consumes that immutable
result and performs the bounded schema/data/alignment checks that require
opening prepared payloads.  It deliberately does not rescan media: declared
video contracts are compared only with the supplied :class:`VideoRecord`
objects.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from types import MappingProxyType
from typing import Any, DefaultDict, Iterable, Mapping, Optional, Sequence, Tuple

from openbot_data.adapters.base import AdapterResult, EpisodeMetadata, freeze_value
from openbot_data.audit.models import CapabilityCoverage
from openbot_data.models import VideoRecord

Finding = Mapping[str, Any]

TIMESTAMP_TOLERANCE_SECONDS = 0.0001
STATS_ABSOLUTE_TOLERANCE = 1e-6
STATS_RELATIVE_TOLERANCE = 1e-5

_INTEGRITIES = {"metadata", "sample", "full"}
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_LAYER_ORDER = {
    "metadata": 0,
    "schema": 1,
    "data": 2,
    "media": 3,
    "alignment": 4,
    "provenance": 5,
}
_STANDARD_COLUMNS = frozenset(
    {
        "episode_index",
        "frame_index",
        "global_index",
        "index",
        "task_index",
        "timestamp",
    }
)
_NUMERIC_DTYPES = frozenset(
    {
        "float",
        "float16",
        "float32",
        "float64",
        "double",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
    }
)


def _empty_mapping() -> Mapping[str, Any]:
    return MappingProxyType({})


@dataclass(frozen=True)
class ValidationResult:
    """Immutable layered validation output."""

    findings: Tuple[Finding, ...] = ()
    capabilities: Tuple[CapabilityCoverage, ...] = ()
    measurements: Mapping[str, Any] = field(default_factory=_empty_mapping)


def _load_parquet_module() -> Optional[Any]:
    """Load the optional dependency without making module import conditional."""
    try:
        return import_module("pyarrow.parquet")
    except Exception:
        # Binary/NumPy incompatibilities can fail with more than ImportError.
        # They are still an unavailable optional capability to this layer.
        return None


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "as_py"):
        try:
            return _json_value(value.as_py())
        except Exception:
            pass
    if hasattr(value, "tolist"):
        try:
            return _json_value(value.tolist())
        except Exception:
            pass
    return repr(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _safe_error(error: object, root: Path) -> str:
    return str(error).replace(str(root), ".")


def _finding(
    code: str,
    severity: str,
    layer: str,
    message: str,
    path: Optional[str] = None,
    *,
    location: Optional[Mapping[str, Any]] = None,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Finding:
    result: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "layer": layer,
        "message": message,
        # ``freeze_value`` deep-copies before freezing.  Normalize an already
        # frozen adapter finding back to plain JSON containers first so nested
        # MappingProxyType values never reach ``copy.deepcopy``.
        "location": _json_value(location or {}),
        "evidence": _json_value(evidence or {}),
    }
    if path is not None:
        result["path"] = path
    return freeze_value(result)


def _normalize_finding(item: Mapping[str, Any]) -> Finding:
    code = str(item.get("code", "VALIDATION_FINDING_INVALID"))
    severity = str(item.get("severity", "error"))
    layer = str(item.get("layer", "provenance"))
    message = str(item.get("message", "A validation finding was emitted."))
    raw_location = item.get("location", {})
    raw_evidence = item.get("evidence", {})
    location = raw_location if isinstance(raw_location, Mapping) else {}
    evidence = raw_evidence if isinstance(raw_evidence, Mapping) else {}
    return _finding(
        code,
        severity,
        layer,
        message,
        str(item["path"]) if item.get("path") is not None else None,
        location=location,
        evidence=evidence,
    )


def _finding_sort_key(item: Finding) -> Tuple[Any, ...]:
    return (
        _SEVERITY_ORDER.get(str(item.get("severity", "")), 99),
        _LAYER_ORDER.get(str(item.get("layer", "")), 99),
        str(item.get("code", "")),
        str(item.get("path", "")),
        _canonical_json(item.get("location", {})),
        _canonical_json(item.get("evidence", {})),
        str(item.get("message", "")),
    )


def _sorted_findings(items: Iterable[Finding]) -> Tuple[Finding, ...]:
    unique: dict[str, Finding] = {}
    for item in items:
        normalized = _normalize_finding(item)
        unique.setdefault(_canonical_json(normalized), normalized)
    return tuple(sorted(unique.values(), key=_finding_sort_key))


def _capability(
    capability: str,
    integrity: str,
    *,
    status: str,
    checked: int = 0,
    total: Optional[int] = None,
    selected: Sequence[str] = (),
    omitted: Sequence[str] = (),
    reason_code: Optional[str] = None,
    reason: Optional[str] = None,
) -> CapabilityCoverage:
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


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonnegative_integer(value: Any) -> Optional[int]:
    return int(value) if _is_integer(value) and value >= 0 else None


def _finite_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _shape(value: Any) -> Optional[Tuple[int, ...]]:
    if hasattr(value, "tolist") and not isinstance(value, (list, tuple)):
        try:
            value = value.tolist()
        except Exception:
            pass
    if not isinstance(value, (list, tuple)):
        return ()
    child_shapes = [_shape(item) for item in value]
    if any(item is None for item in child_shapes):
        return None
    if child_shapes and any(item != child_shapes[0] for item in child_shapes[1:]):
        return None
    if not child_shapes:
        return (len(value),)
    first_shape = child_shapes[0]
    if first_shape is None:
        return None
    return (len(value),) + first_shape


def _flatten(value: Any) -> list[Any]:
    if hasattr(value, "tolist") and not isinstance(value, (list, tuple)):
        try:
            value = value.tolist()
        except Exception:
            pass
    if isinstance(value, (list, tuple)):
        result: list[Any] = []
        for item in value:
            result.extend(_flatten(item))
        return result
    return [value]


def _numeric_leaves(
    value: Any,
    coordinates: Tuple[int, ...] = (),
) -> Iterable[Tuple[Tuple[int, ...], Any]]:
    if hasattr(value, "tolist") and not isinstance(value, (list, tuple)):
        try:
            value = value.tolist()
        except Exception:
            pass
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _numeric_leaves(item, coordinates + (index,))
        return
    yield coordinates, value


def _declared_features(info: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    raw = info.get("features")
    if not isinstance(raw, Mapping):
        return MappingProxyType({})
    result = {}
    for key, value in raw.items():
        if isinstance(value, Mapping):
            result[str(key)] = value
    return MappingProxyType(result)


def _declared_shape(feature: Mapping[str, Any]) -> Optional[Tuple[int, ...]]:
    raw = feature.get("shape")
    if not isinstance(raw, (list, tuple)):
        return None
    parsed = []
    for value in raw:
        parsed_value = _nonnegative_integer(value)
        if parsed_value is None:
            return None
        parsed.append(parsed_value)
    return tuple(parsed)


def _feature_shape_matches(
    feature_key: str,
    declared_shape: Optional[Tuple[int, ...]],
    actual_shape: Optional[Tuple[int, ...]],
) -> bool:
    """Match LeRobot's logical feature shape to its Arrow storage shape.

    LeRobot v3 declares its generated scalar index/timestamp columns with
    logical shape ``[1]`` while storing each row as an Arrow scalar.  That is
    the canonical 0.6.0 writer contract, not a malformed tensor shape.
    """
    if declared_shape == actual_shape:
        return True
    return (
        feature_key in _STANDARD_COLUMNS
        and declared_shape == (1,)
        and actual_shape == ()
    )


def _is_video_feature(feature: Mapping[str, Any]) -> bool:
    return str(feature.get("dtype", "")).lower() == "video" or isinstance(
        feature.get("video_info"), Mapping
    )


def _is_numeric_feature(feature: Mapping[str, Any]) -> bool:
    return str(feature.get("dtype", "")).lower() in _NUMERIC_DTYPES


def _normalization_features(
    features: Mapping[str, Mapping[str, Any]],
) -> Tuple[str, ...]:
    return tuple(
        sorted(
            key
            for key, feature in features.items()
            if _is_numeric_feature(feature) and key not in _STANDARD_COLUMNS
        )
    )


def _action_features(
    features: Mapping[str, Mapping[str, Any]],
) -> Tuple[str, ...]:
    return tuple(
        key
        for key in sorted(features)
        if key == "action" or key.startswith("action.") or key.endswith(".action")
    )


def _metadata_totals(
    adapter_result: AdapterResult,
    videos: Sequence[VideoRecord],
    findings: list[Finding],
) -> None:
    info = adapter_result.raw_info
    episodes = tuple(adapter_result.episodes)
    unique_episodes = {episode.episode_index for episode in episodes}
    declared_episodes = _nonnegative_integer(info.get("total_episodes"))
    if declared_episodes is not None and declared_episodes != len(episodes):
        findings.append(
            _finding(
                "LEROBOT_EPISODE_COUNT_MISMATCH",
                "error",
                "alignment",
                "Declared and discovered episode counts differ.",
                "meta/info.json",
                location={"json_pointer": "/total_episodes"},
                evidence={
                    "declared": declared_episodes,
                    "discovered": len(episodes),
                    "unique": len(unique_episodes),
                    "method": "count_readable_episode_records",
                },
            )
        )

    if episodes and all(episode.length is not None for episode in episodes):
        discovered_frames = sum(int(episode.length or 0) for episode in episodes)
        declared_frames = _nonnegative_integer(info.get("total_frames"))
        if declared_frames is not None and declared_frames != discovered_frames:
            findings.append(
                _finding(
                    "LEROBOT_FRAME_COUNT_MISMATCH",
                    "error",
                    "metadata",
                    "Declared frame total differs from episode metadata.",
                    "meta/info.json",
                    location={"json_pointer": "/total_frames"},
                    evidence={
                        "declared": declared_frames,
                        "discovered": discovered_frames,
                        "method": "sum_episode_lengths",
                    },
                )
            )

    declared_tasks = _nonnegative_integer(info.get("total_tasks"))
    discovered_tasks = len({task.task_index for task in adapter_result.tasks})
    if declared_tasks is not None and declared_tasks != discovered_tasks:
        findings.append(
            _finding(
                "LEROBOT_TASK_COUNT_MISMATCH",
                "error",
                "metadata",
                "Declared task total differs from the task table.",
                "meta/info.json",
                location={"json_pointer": "/total_tasks"},
                evidence={
                    "declared": declared_tasks,
                    "discovered": discovered_tasks,
                    "method": "count_unique_task_indexes",
                },
            )
        )

    declared_videos = _nonnegative_integer(info.get("total_videos"))
    discovered_videos = len({video.path for video in videos})
    if declared_videos is not None and declared_videos != discovered_videos:
        findings.append(
            _finding(
                "LEROBOT_VIDEO_COUNT_MISMATCH",
                "error",
                "metadata",
                "Declared video total differs from prepared video records.",
                "meta/info.json",
                location={"json_pointer": "/total_videos"},
                evidence={
                    "declared": declared_videos,
                    "discovered": discovered_videos,
                    "method": "count_prepared_video_records",
                },
            )
        )

    data_paths = {
        artifact.path
        for artifact in adapter_result.artifacts
        if artifact.kind == "data" and artifact.exists
    }
    for field_name in ("total_data_shards", "total_data_files"):
        declared_shards = _nonnegative_integer(info.get(field_name))
        if declared_shards is None or declared_shards == len(data_paths):
            continue
        findings.append(
            _finding(
                "LEROBOT_DATA_SHARD_COUNT_MISMATCH",
                "error",
                "metadata",
                "Declared data-shard total differs from discovered artifacts.",
                "meta/info.json",
                location={"json_pointer": f"/{field_name}"},
                evidence={
                    "field": field_name,
                    "declared": declared_shards,
                    "discovered": len(data_paths),
                    "method": "count_unique_existing_data_artifacts",
                },
            )
        )


def _episode_indexes(
    episodes: Sequence[EpisodeMetadata],
    findings: list[Finding],
) -> None:
    occurrences: DefaultDict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for episode in episodes:
        occurrences[episode.episode_index].append(
            {
                "path": episode.source_path,
                "row": episode.source_row,
            }
        )
    for episode_index, records in sorted(occurrences.items()):
        if len(records) <= 1:
            continue
        findings.append(
            _finding(
                "LEROBOT_EPISODE_INDEX_DUPLICATE",
                "error",
                "data",
                "An episode index occurs in more than one metadata record.",
                records[0]["path"],
                location={"episode_index": episode_index},
                evidence={"count": len(records), "records": records},
            )
        )

    actual = sorted(occurrences)
    expected = list(range(len(actual)))
    if actual != expected:
        actual_set = set(actual)
        expected_set = set(expected)
        findings.append(
            _finding(
                "LEROBOT_EPISODE_INDEX_NON_CONTIGUOUS",
                "error",
                "data",
                "Episode indexes must be unique and continuous from zero.",
                "meta/episodes",
                location={},
                evidence={
                    "actual": actual,
                    "expected_start": 0,
                    "expected_stop_exclusive": len(actual),
                    "missing": sorted(expected_set - actual_set),
                    "unexpected": sorted(actual_set - expected_set),
                },
            )
        )


def _data_artifact_paths(
    adapter_result: AdapterResult,
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    by_path: dict[str, bool] = {}
    for artifact in adapter_result.artifacts:
        if artifact.kind != "data":
            continue
        by_path[artifact.path] = by_path.get(artifact.path, False) or artifact.exists
    all_paths = tuple(sorted(by_path))
    existing = tuple(path for path in all_paths if by_path[path])
    return all_paths, existing


def _sample_paths(paths: Sequence[str]) -> Tuple[str, ...]:
    if not paths:
        return ()
    indexes = sorted({0, (len(paths) - 1) // 2, len(paths) - 1})
    return tuple(paths[index] for index in indexes)


def _arrow_base_type(value: Any) -> str:
    current = value
    seen = set()
    while hasattr(current, "value_type") and id(current) not in seen:
        seen.add(id(current))
        try:
            current = current.value_type
        except Exception:
            break
    return str(current).lower()


def _arrow_shape(value: Any) -> Optional[Tuple[int, ...]]:
    result = []
    current = value
    seen = set()
    while hasattr(current, "value_type") and id(current) not in seen:
        seen.add(id(current))
        size = getattr(current, "list_size", None)
        if not isinstance(size, int):
            return None
        result.append(size)
        try:
            current = current.value_type
        except Exception:
            return None
    return tuple(result)


def _dtype_matches(declared: str, actual: str) -> bool:
    aliases = {
        "float": {"float", "float32"},
        "float16": {"halffloat", "float16"},
        "float32": {"float", "float32"},
        "float64": {"double", "float64"},
        "double": {"double", "float64"},
        "int": {"int64", "int32", "int16", "int8"},
        "string": {"string", "large_string", "utf8"},
        "str": {"string", "large_string", "utf8"},
        "bool": {"bool", "boolean"},
    }
    accepted = aliases.get(declared, {declared})
    return actual in accepted


def _schema_fields(schema: Any) -> Mapping[str, Any]:
    fields = {}
    try:
        names = tuple(str(name) for name in schema.names)
    except Exception:
        names = ()
    for name in names:
        try:
            fields[name] = schema.field(name)
        except Exception:
            continue
    return fields


def _validate_feature_schema(
    path: str,
    schema: Any,
    features: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> None:
    fields = _schema_fields(schema)
    actual_names = set(fields)
    declared_names = set(features)
    # LeRobot v3 stores video frames exclusively in ``videos/**`` and resolves
    # them from episode metadata plus timestamps.  Its canonical writer
    # intentionally omits video feature keys from data Parquet shards.
    required_names = {
        key for key, feature in features.items() if not _is_video_feature(feature)
    }
    for feature_key in sorted(required_names - actual_names):
        findings.append(
            _finding(
                "LEROBOT_FEATURE_COLUMN_MISSING",
                "error",
                "schema",
                "A declared feature column is absent from a data shard.",
                path,
                location={"feature_key": feature_key},
                evidence={"declared": True, "actual_columns": sorted(actual_names)},
            )
        )
    for feature_key in sorted(actual_names - declared_names - _STANDARD_COLUMNS):
        findings.append(
            _finding(
                "LEROBOT_FEATURE_COLUMN_UNDECLARED",
                "warning",
                "schema",
                "A data-shard feature column is not declared in info.json.",
                path,
                location={"feature_key": feature_key},
                evidence={"declared": False},
            )
        )

    for feature_key in sorted(declared_names & actual_names):
        feature = features[feature_key]
        if _is_video_feature(feature):
            continue
        field_value = fields[feature_key]
        arrow_type = getattr(field_value, "type", field_value)
        declared_dtype = str(feature.get("dtype", "")).lower()
        actual_dtype = _arrow_base_type(arrow_type)
        if declared_dtype and not _dtype_matches(declared_dtype, actual_dtype):
            findings.append(
                _finding(
                    "LEROBOT_FEATURE_DTYPE_MISMATCH",
                    "error",
                    "schema",
                    "A data-shard feature dtype differs from info.json.",
                    path,
                    location={"feature_key": feature_key},
                    evidence={
                        "declared_dtype": declared_dtype,
                        "actual_dtype": actual_dtype,
                    },
                )
            )
        declared_shape = _declared_shape(feature)
        actual_shape = _arrow_shape(arrow_type)
        if (
            declared_shape is not None
            and actual_shape is not None
            and not _feature_shape_matches(
                feature_key,
                declared_shape,
                actual_shape,
            )
        ):
            findings.append(
                _finding(
                    "LEROBOT_FEATURE_SHAPE_MISMATCH",
                    "error",
                    "schema",
                    "A fixed-size data-shard feature shape differs from info.json.",
                    path,
                    location={"feature_key": feature_key},
                    evidence={
                        "declared_shape": declared_shape,
                        "actual_shape": actual_shape,
                        "source": "parquet_schema",
                    },
                )
            )
        declared_nullable = feature.get("nullable")
        actual_nullable = getattr(field_value, "nullable", None)
        if (
            isinstance(declared_nullable, bool)
            and isinstance(actual_nullable, bool)
            and declared_nullable != actual_nullable
        ):
            findings.append(
                _finding(
                    "LEROBOT_FEATURE_NULLABILITY_MISMATCH",
                    "error",
                    "schema",
                    "A feature nullability contract differs from the Parquet schema.",
                    path,
                    location={"feature_key": feature_key},
                    evidence={
                        "declared_nullable": declared_nullable,
                        "actual_nullable": actual_nullable,
                    },
                )
            )


@dataclass
class _Moments:
    count: list[int] = field(default_factory=list)
    minimum: list[float] = field(default_factory=list)
    maximum: list[float] = field(default_factory=list)
    mean: list[float] = field(default_factory=list)
    m2: list[float] = field(default_factory=list)
    rows: int = 0

    def update(self, value: Any) -> bool:
        flat = _flatten(value)
        parsed = [_finite_float(item) for item in flat]
        if any(item is None for item in parsed):
            return False
        values = [float(item) for item in parsed if item is not None]
        if not self.count:
            self.count = [0] * len(values)
            self.minimum = [math.inf] * len(values)
            self.maximum = [-math.inf] * len(values)
            self.mean = [0.0] * len(values)
            self.m2 = [0.0] * len(values)
        if len(values) != len(self.count):
            return False
        self.rows += 1
        for index, value_item in enumerate(values):
            self.count[index] += 1
            self.minimum[index] = min(self.minimum[index], value_item)
            self.maximum[index] = max(self.maximum[index], value_item)
            delta = value_item - self.mean[index]
            self.mean[index] += delta / self.count[index]
            delta_after = value_item - self.mean[index]
            self.m2[index] += delta * delta_after
        return True

    def values(self) -> Mapping[str, Tuple[float, ...]]:
        variance = tuple(
            self.m2[index] / count if count else math.nan for index, count in enumerate(self.count)
        )
        return {
            "min": tuple(self.minimum),
            "max": tuple(self.maximum),
            "mean": tuple(self.mean),
            "variance": variance,
            "std": tuple(math.sqrt(max(0.0, value)) for value in variance),
            "count": tuple(float(value) for value in self.count),
        }


@dataclass
class _RowObservation:
    path: str
    row: int
    episode_index: Optional[int]
    frame_index: Optional[int]
    global_index: Optional[int]
    timestamp: Optional[float]
    value: Mapping[str, Any]


class _RowCollector:
    def __init__(
        self,
        *,
        adapter_result: AdapterResult,
        features: Mapping[str, Mapping[str, Any]],
        selected_paths: Sequence[str],
        integrity: str,
        findings: list[Finding],
    ) -> None:
        self.adapter_result = adapter_result
        self.features = features
        self.selected_paths = set(selected_paths)
        self.integrity = integrity
        self.findings = findings
        self.task_indexes = {task.task_index for task in adapter_result.tasks}
        self.row_counts: Counter[str] = Counter()
        self.episode_rows: Counter[int] = Counter()
        self.task_rows: Counter[int] = Counter()
        self.frame_occurrences: DefaultDict[Tuple[int, int], list[Mapping[str, Any]]] = defaultdict(
            list
        )
        self.global_occurrences: DefaultDict[int, list[Mapping[str, Any]]] = defaultdict(list)
        self.timestamp_rows: DefaultDict[int, list[_RowObservation]] = defaultdict(list)
        self.digest_rows: DefaultDict[int, list[Tuple[Tuple[int, int, str, int], str]]] = (
            defaultdict(list)
        )
        self.action_rows: DefaultDict[
            Tuple[int, str], list[Tuple[Tuple[int, int, str, int], Any]]
        ] = defaultdict(list)
        self.moments = {key: _Moments() for key in _normalization_features(features)}
        self.reported_shapes: set[Tuple[str, str, str]] = set()
        self.total_rows = 0

    def observe(self, path: str, row_number: int, value: Any) -> None:
        if not isinstance(value, Mapping):
            self.findings.append(
                _finding(
                    "LEROBOT_PARQUET_ROW_INVALID",
                    "error",
                    "data",
                    "A Parquet data row is not an object.",
                    path,
                    location={"row": row_number},
                    evidence={"actual_type": type(value).__name__},
                )
            )
            return
        row = {str(key): item for key, item in value.items()}
        self.total_rows += 1
        self.row_counts[path] += 1
        episode_index = _nonnegative_integer(row.get("episode_index"))
        frame_index = _nonnegative_integer(row.get("frame_index"))
        raw_global = row.get("index", row.get("global_index"))
        global_index = _nonnegative_integer(raw_global)
        timestamp = _finite_float(row.get("timestamp"))
        location = {
            **({"episode_index": episode_index} if episode_index is not None else {}),
            **({"frame_index": frame_index} if frame_index is not None else {}),
            "row": row_number,
        }

        if episode_index is not None:
            self.episode_rows[episode_index] += 1
        if episode_index is not None and frame_index is not None:
            self.frame_occurrences[(episode_index, frame_index)].append(
                {"path": path, "row": row_number}
            )
        if global_index is not None:
            self.global_occurrences[global_index].append(
                {
                    "path": path,
                    "row": row_number,
                    **({"episode_index": episode_index} if episode_index is not None else {}),
                }
            )
        if episode_index is not None and timestamp is not None:
            observation = _RowObservation(
                path=path,
                row=row_number,
                episode_index=episode_index,
                frame_index=frame_index,
                global_index=global_index,
                timestamp=timestamp,
                value=row,
            )
            self.timestamp_rows[episode_index].append(observation)

        if "task_index" in row:
            task_index = _nonnegative_integer(row.get("task_index"))
            if task_index is None or task_index not in self.task_indexes:
                self.findings.append(
                    _finding(
                        "LEROBOT_TASK_REFERENCE_INVALID",
                        "error",
                        "alignment",
                        "A data row references no task-table entry.",
                        path,
                        location={
                            **location,
                            "feature_key": "task_index",
                        },
                        evidence={
                            "task_index": _json_value(row.get("task_index")),
                            "known_task_indexes": sorted(self.task_indexes),
                        },
                    )
                )
            elif task_index is not None:
                self.task_rows[task_index] += 1

        for feature_key, feature in self.features.items():
            if feature_key not in row or _is_video_feature(feature):
                continue
            feature_value = row[feature_key]
            declared_shape = _declared_shape(feature)
            actual_shape = _shape(feature_value)
            if (
                declared_shape is not None
                and not _feature_shape_matches(
                    feature_key,
                    declared_shape,
                    actual_shape,
                )
                and (
                    path,
                    feature_key,
                    _canonical_json(actual_shape),
                )
                not in self.reported_shapes
            ):
                self.reported_shapes.add((path, feature_key, _canonical_json(actual_shape)))
                self.findings.append(
                    _finding(
                        "LEROBOT_FEATURE_SHAPE_MISMATCH",
                        "error",
                        "schema",
                        "A feature row shape differs from info.json.",
                        path,
                        location={**location, "feature_key": feature_key},
                        evidence={
                            "declared_shape": declared_shape,
                            "actual_shape": actual_shape,
                            "source": "parquet_row",
                        },
                    )
                )
            if not _is_numeric_feature(feature):
                continue
            for coordinates, leaf in _numeric_leaves(feature_value):
                if _finite_float(leaf) is not None:
                    continue
                self.findings.append(
                    _finding(
                        "LEROBOT_NUMERIC_NON_FINITE",
                        "error",
                        "data",
                        "A numeric feature contains NaN or infinity.",
                        path,
                        location={**location, "feature_key": feature_key},
                        evidence={
                            "coordinates": coordinates,
                            "value": _json_value(leaf),
                        },
                    )
                )
            if (
                feature_key in self.moments
                and _feature_shape_matches(
                    feature_key,
                    declared_shape,
                    actual_shape,
                )
            ):
                self.moments[feature_key].update(feature_value)

        for standard_key in _STANDARD_COLUMNS:
            if standard_key not in row:
                continue
            for coordinates, leaf in _numeric_leaves(row[standard_key]):
                if _finite_float(leaf) is not None:
                    continue
                self.findings.append(
                    _finding(
                        "LEROBOT_NUMERIC_NON_FINITE",
                        "error",
                        "data",
                        "A numeric index or timestamp contains NaN or infinity.",
                        path,
                        location={**location, "feature_key": standard_key},
                        evidence={
                            "coordinates": coordinates,
                            "value": _json_value(leaf),
                        },
                    )
                )

        if episode_index is not None:
            order = (
                frame_index if frame_index is not None else 2**63 - 1,
                global_index if global_index is not None else 2**63 - 1,
                path,
                row_number,
            )
            identity_free = {
                key: item
                for key, item in row.items()
                if key not in {"episode_index", "global_index", "index"}
            }
            self.digest_rows[episode_index].append((order, _canonical_json(identity_free)))
            for feature_key in _action_features(self.features):
                if feature_key in row:
                    self.action_rows[(episode_index, feature_key)].append(
                        (order, _json_value(row[feature_key]))
                    )

    def _covered_episode(self, episode_index: int, complete_paths: set[str]) -> bool:
        related = {
            relation.path
            for relation in self.adapter_result.relations
            if relation.kind == "data" and relation.episode_index == episode_index
        }
        if related:
            return related.issubset(complete_paths)
        return self.integrity == "full"

    def finalize(
        self,
        *,
        complete_paths: set[str],
        all_rows_complete: bool,
    ) -> Mapping[str, Any]:
        for (episode_index, frame_index), records in sorted(self.frame_occurrences.items()):
            if len(records) <= 1:
                continue
            self.findings.append(
                _finding(
                    "LEROBOT_FRAME_INDEX_DUPLICATE",
                    "error",
                    "data",
                    "A frame index occurs more than once within an episode.",
                    str(records[0]["path"]),
                    location={
                        "episode_index": episode_index,
                        "frame_index": frame_index,
                    },
                    evidence={"count": len(records), "records": records},
                )
            )
        for global_index, records in sorted(self.global_occurrences.items()):
            if len(records) <= 1:
                continue
            self.findings.append(
                _finding(
                    "LEROBOT_GLOBAL_INDEX_DUPLICATE",
                    "error",
                    "data",
                    "A global frame index occurs more than once.",
                    str(records[0]["path"]),
                    location={"global_index": global_index},
                    evidence={"count": len(records), "records": records},
                )
            )

        episodes_by_index = {
            episode.episode_index: episode for episode in self.adapter_result.episodes
        }
        frames_by_episode: DefaultDict[int, set[int]] = defaultdict(set)
        for episode_index, frame_index in self.frame_occurrences:
            frames_by_episode[episode_index].add(frame_index)
        for episode_index, actual_set in sorted(frames_by_episode.items()):
            if not self._covered_episode(episode_index, complete_paths):
                continue
            episode = episodes_by_index.get(episode_index)
            expected_length = episode.length if episode is not None else None
            stop = expected_length if expected_length is not None else len(actual_set)
            expected_set = set(range(stop))
            if actual_set != expected_set:
                self.findings.append(
                    _finding(
                        "LEROBOT_FRAME_INDEX_NON_CONTIGUOUS",
                        "error",
                        "data",
                        "Frame indexes must be continuous from zero per episode.",
                        episode.data_path if episode is not None else None,
                        location={"episode_index": episode_index},
                        evidence={
                            "expected_start": 0,
                            "expected_stop_exclusive": stop,
                            "missing": sorted(expected_set - actual_set),
                            "unexpected": sorted(actual_set - expected_set),
                        },
                    )
                )
            if (
                episode is not None
                and episode.length is not None
                and self.episode_rows[episode_index] != episode.length
            ):
                self.findings.append(
                    _finding(
                        "LEROBOT_EPISODE_ROW_COUNT_MISMATCH",
                        "error",
                        "data",
                        "Observed episode rows differ from the declared length.",
                        episode.data_path,
                        location={"episode_index": episode_index},
                        evidence={
                            "declared": episode.length,
                            "observed": self.episode_rows[episode_index],
                        },
                    )
                )

        if all_rows_complete and self.global_occurrences:
            actual_global = set(self.global_occurrences)
            expected_global = set(range(len(actual_global)))
            if actual_global != expected_global:
                self.findings.append(
                    _finding(
                        "LEROBOT_GLOBAL_INDEX_NON_CONTIGUOUS",
                        "error",
                        "data",
                        "Global frame indexes must be continuous from zero.",
                        "data",
                        location={},
                        evidence={
                            "expected_start": 0,
                            "expected_stop_exclusive": len(actual_global),
                            "missing": sorted(expected_global - actual_global),
                            "unexpected": sorted(actual_global - expected_global),
                        },
                    )
                )

        fps = _finite_float(self.adapter_result.raw_info.get("fps"))
        for episode_index, observations in sorted(self.timestamp_rows.items()):
            ordered = sorted(
                observations,
                key=lambda item: (
                    item.frame_index if item.frame_index is not None else 2**63 - 1,
                    item.global_index if item.global_index is not None else 2**63 - 1,
                    item.path,
                    item.row,
                ),
            )
            previous: Optional[_RowObservation] = None
            for observation in ordered:
                if (
                    previous is not None
                    and observation.timestamp is not None
                    and previous.timestamp is not None
                    and observation.timestamp <= previous.timestamp
                ):
                    self.findings.append(
                        _finding(
                            "LEROBOT_TIMESTAMP_NON_MONOTONIC",
                            "error",
                            "alignment",
                            "Timestamps must increase monotonically within an episode.",
                            observation.path,
                            location={
                                "episode_index": episode_index,
                                "frame_index": observation.frame_index,
                                "timestamp": observation.timestamp,
                                "feature_key": "timestamp",
                            },
                            evidence={
                                "previous_frame_index": previous.frame_index,
                                "previous_timestamp": previous.timestamp,
                            },
                        )
                    )
                if (
                    fps is not None
                    and fps > 0
                    and observation.frame_index is not None
                    and observation.timestamp is not None
                ):
                    expected_timestamp = observation.frame_index / fps
                    if (
                        abs(observation.timestamp - expected_timestamp)
                        > TIMESTAMP_TOLERANCE_SECONDS
                    ):
                        self.findings.append(
                            _finding(
                                "LEROBOT_TIMESTAMP_OFF_GRID",
                                "error",
                                "alignment",
                                "Timestamp does not match frame_index / fps within tolerance.",
                                observation.path,
                                location={
                                    "episode_index": episode_index,
                                    "frame_index": observation.frame_index,
                                    "timestamp": observation.timestamp,
                                    "feature_key": "timestamp",
                                },
                                evidence={
                                    "expected_timestamp": expected_timestamp,
                                    "fps": fps,
                                    "tolerance_seconds": TIMESTAMP_TOLERANCE_SECONDS,
                                },
                            )
                        )
                previous = observation

        digests = []
        for episode_index, rows in sorted(self.digest_rows.items()):
            digest = hashlib.sha256()
            for _order, canonical in sorted(rows):
                digest.update(canonical.encode("utf-8"))
                digest.update(b"\n")
            digests.append(
                {
                    "episode_index": episode_index,
                    "algorithm": "sha256",
                    "digest": digest.hexdigest(),
                    "rows": len(rows),
                    "coverage": (
                        "complete"
                        if self._covered_episode(episode_index, complete_paths)
                        else "partial"
                    ),
                }
            )

        static_spans = []
        for (episode_index, feature_key), rows in sorted(self.action_rows.items()):
            ordered_rows = sorted(rows)
            if not ordered_rows:
                continue
            start = 0
            for cursor in range(1, len(ordered_rows) + 1):
                continues = (
                    cursor < len(ordered_rows)
                    and _canonical_json(ordered_rows[cursor][1])
                    == _canonical_json(ordered_rows[start][1])
                    and ordered_rows[cursor][0][0] == ordered_rows[cursor - 1][0][0] + 1
                )
                if continues:
                    continue
                length = cursor - start
                if length >= 2:
                    static_spans.append(
                        {
                            "episode_index": episode_index,
                            "feature_key": feature_key,
                            "start_frame_index": ordered_rows[start][0][0],
                            "end_frame_index": ordered_rows[cursor - 1][0][0],
                            "length_frames": length,
                            "value": ordered_rows[start][1],
                            "comparison": "exact_canonical_value",
                            "coverage": (
                                "complete"
                                if self._covered_episode(episode_index, complete_paths)
                                else "partial"
                            ),
                        }
                    )
                start = cursor

        numeric = {}
        for feature_key, moments in sorted(self.moments.items()):
            if not moments.count:
                continue
            values = moments.values()
            numeric[feature_key] = {
                "rows": moments.rows,
                "count": tuple(int(value) for value in values["count"]),
                "min": values["min"],
                "max": values["max"],
                "variance": values["variance"],
                "unit": "declared_feature_value",
            }
        return {
            "numeric_features": numeric,
            "static_action_spans": static_spans,
            "episode_content_digests": digests,
        }


@dataclass
class _ParquetResult:
    footer_rows: dict[str, int]
    complete_row_paths: set[str]
    row_collector: _RowCollector
    capabilities: list[CapabilityCoverage]


def _is_unfinalized(path: Path) -> bool:
    try:
        if path.stat().st_size < 8:
            return True
        with path.open("rb") as stream:
            stream.seek(-4, 2)
            return stream.read(4) != b"PAR1"
    except OSError:
        return False


def _scan_parquet(
    root: Path,
    adapter_result: AdapterResult,
    integrity: str,
    batch_size: int,
    features: Mapping[str, Mapping[str, Any]],
    findings: list[Finding],
) -> _ParquetResult:
    all_paths, existing_paths = _data_artifact_paths(adapter_result)
    if integrity == "metadata":
        row_paths: Tuple[str, ...] = ()
    elif integrity == "sample":
        row_paths = _sample_paths(existing_paths)
    else:
        row_paths = existing_paths
    row_collector = _RowCollector(
        adapter_result=adapter_result,
        features=features,
        selected_paths=row_paths,
        integrity=integrity,
        findings=findings,
    )
    capabilities: list[CapabilityCoverage] = []
    parquet = _load_parquet_module() if existing_paths else None
    if existing_paths and parquet is None:
        findings.append(
            _finding(
                "LEROBOT_DEPENDENCY_MISSING",
                "error",
                "metadata",
                "Parquet validation requires the optional lerobot dependency.",
                existing_paths[0],
                location={},
                evidence={
                    "dependency": "pyarrow",
                    "install": "pip install 'openbot-data[lerobot]'",
                },
            )
        )
        for capability_name in (
            "data.parquet_footer",
            "data.parquet_schema",
        ):
            capabilities.append(
                _capability(
                    capability_name,
                    integrity,
                    status="unavailable",
                    checked=0,
                    total=len(all_paths),
                    omitted=all_paths,
                    reason_code="pyarrow_unavailable",
                    reason="The optional PyArrow dependency could not be loaded.",
                )
            )
        if integrity == "metadata":
            capabilities.append(
                _capability(
                    "data.parquet_rows",
                    integrity,
                    status="skipped",
                    checked=0,
                    total=len(all_paths),
                    omitted=all_paths,
                    reason_code="integrity_too_low",
                    reason="Metadata integrity does not read Parquet rows.",
                )
            )
        else:
            capabilities.append(
                _capability(
                    "data.parquet_rows",
                    integrity,
                    status="unavailable",
                    checked=0,
                    total=len(all_paths),
                    omitted=all_paths,
                    reason_code="pyarrow_unavailable",
                    reason="The optional PyArrow dependency could not be loaded.",
                )
            )
        return _ParquetResult({}, set(), row_collector, capabilities)

    footer_rows: dict[str, int] = {}
    footer_complete: set[str] = set()
    schema_complete: set[str] = set()
    row_complete: set[str] = set()
    for relative_path in existing_paths:
        assert parquet is not None
        path = root / relative_path
        if _is_unfinalized(path):
            findings.append(
                _finding(
                    "LEROBOT_PARQUET_UNFINALIZED",
                    "error",
                    "data",
                    "A Parquet shard has no finalized footer magic.",
                    relative_path,
                    location={},
                    evidence={"expected_footer_magic": "PAR1"},
                )
            )
            continue
        try:
            parquet_file = parquet.ParquetFile(path)
            row_count = int(parquet_file.metadata.num_rows)
            footer_rows[relative_path] = row_count
            footer_complete.add(relative_path)
        except Exception as exc:
            findings.append(
                _finding(
                    "LEROBOT_DATA_UNREADABLE",
                    "error",
                    "data",
                    "A LeRobot data Parquet footer could not be read.",
                    relative_path,
                    location={},
                    evidence={
                        "stage": "footer",
                        "reason": "parquet_footer_unreadable",
                        "error": _safe_error(exc, root),
                    },
                )
            )
            continue
        declared_artifact_rows = {
            artifact.row_count
            for artifact in adapter_result.artifacts
            if artifact.kind == "data"
            and artifact.path == relative_path
            and artifact.row_count is not None
        }
        for declared_rows in sorted(declared_artifact_rows):
            if declared_rows == row_count:
                continue
            findings.append(
                _finding(
                    "LEROBOT_DATA_ROW_COUNT_MISMATCH",
                    "error",
                    "data",
                    "Prepared artifact rows differ from the Parquet footer.",
                    relative_path,
                    location={},
                    evidence={
                        "prepared_rows": declared_rows,
                        "footer_rows": row_count,
                        "source": "adapter_artifact",
                    },
                )
            )
        try:
            schema = parquet_file.schema_arrow
            tuple(str(name) for name in schema.names)
            schema_complete.add(relative_path)
            _validate_feature_schema(relative_path, schema, features, findings)
        except Exception as exc:
            findings.append(
                _finding(
                    "LEROBOT_PARQUET_SCHEMA_UNREADABLE",
                    "error",
                    "schema",
                    "A LeRobot data Parquet schema could not be read.",
                    relative_path,
                    location={},
                    evidence={
                        "stage": "schema",
                        "error": _safe_error(exc, root),
                    },
                )
            )
            continue
        if relative_path not in row_paths:
            continue
        read_failed = False
        try:
            for batch in parquet_file.iter_batches(batch_size=batch_size):
                values = batch.to_pylist()
                for value in values:
                    row_collector.observe(
                        relative_path,
                        row_collector.row_counts[relative_path] + 1,
                        value,
                    )
        except Exception as exc:
            read_failed = True
            findings.append(
                _finding(
                    "LEROBOT_PARQUET_ROW_GROUP_UNREADABLE",
                    "error",
                    "data",
                    "A LeRobot Parquet row group could not be read.",
                    relative_path,
                    location={"row": row_collector.row_counts[relative_path] + 1},
                    evidence={
                        "stage": "row_group",
                        "batch_size": batch_size,
                        "error": _safe_error(exc, root),
                    },
                )
            )
        observed_rows = row_collector.row_counts[relative_path]
        if observed_rows != row_count:
            findings.append(
                _finding(
                    "LEROBOT_DATA_ROW_COUNT_MISMATCH",
                    "error",
                    "data",
                    "Rows read from a shard differ from its Parquet footer.",
                    relative_path,
                    location={},
                    evidence={
                        "footer_rows": row_count,
                        "observed_rows": observed_rows,
                    },
                )
            )
        if not read_failed and observed_rows == row_count:
            row_complete.add(relative_path)

    missing_paths = set(all_paths) - set(existing_paths)
    footer_omitted = tuple(sorted(set(all_paths) - footer_complete))
    schema_omitted = tuple(sorted(set(all_paths) - schema_complete))
    capabilities.extend(
        [
            _capability(
                "data.parquet_footer",
                integrity,
                status=(
                    "complete"
                    if len(footer_complete) == len(all_paths)
                    else "partial"
                    if footer_complete
                    else "unavailable"
                ),
                checked=len(footer_complete),
                total=len(all_paths),
                selected=tuple(footer_complete),
                omitted=footer_omitted,
                reason_code=(
                    None
                    if len(footer_complete) == len(all_paths)
                    else "partial_capability_coverage"
                    if footer_complete
                    else "source_capability_unavailable"
                ),
                reason=(
                    None
                    if len(footer_complete) == len(all_paths)
                    else "Some discovered data shards have no readable footer."
                ),
            ),
            _capability(
                "data.parquet_schema",
                integrity,
                status=(
                    "complete"
                    if len(schema_complete) == len(all_paths)
                    else "partial"
                    if schema_complete
                    else "unavailable"
                ),
                checked=len(schema_complete),
                total=len(all_paths),
                selected=tuple(schema_complete),
                omitted=schema_omitted,
                reason_code=(
                    None
                    if len(schema_complete) == len(all_paths)
                    else "partial_capability_coverage"
                    if schema_complete
                    else "source_capability_unavailable"
                ),
                reason=(
                    None
                    if len(schema_complete) == len(all_paths)
                    else "Some discovered data shards have no readable schema."
                ),
            ),
        ]
    )
    if integrity == "metadata":
        capabilities.append(
            _capability(
                "data.parquet_rows",
                integrity,
                status="skipped",
                checked=0,
                total=len(all_paths),
                omitted=all_paths,
                reason_code="integrity_too_low",
                reason="Metadata integrity does not read Parquet rows.",
            )
        )
    else:
        omitted_rows = tuple(sorted(set(all_paths) - row_complete))
        if integrity == "sample":
            row_status = "partial"
            row_reason_code = "sample_integrity"
            row_reason = "Sample integrity reads deterministic first, middle, and last shards."
        elif len(row_complete) == len(all_paths):
            row_status = "complete"
            row_reason_code = None
            row_reason = None
        elif row_complete:
            row_status = "partial"
            row_reason_code = "partial_capability_coverage"
            row_reason = "Some selected Parquet shards could not be read completely."
        else:
            row_status = "unavailable"
            row_reason_code = "source_capability_unavailable"
            row_reason = "No selected Parquet shard could be read completely."
        capabilities.append(
            _capability(
                "data.parquet_rows",
                integrity,
                status=row_status,
                checked=len(row_complete),
                total=len(all_paths),
                selected=tuple(row_complete),
                omitted=omitted_rows,
                reason_code=row_reason_code,
                reason=row_reason,
            )
        )
    del missing_paths
    return _ParquetResult(
        footer_rows=footer_rows,
        complete_row_paths=row_complete,
        row_collector=row_collector,
        capabilities=capabilities,
    )


def _episode_ranges(
    adapter_result: AdapterResult,
    footer_rows: Mapping[str, int],
    findings: list[Finding],
) -> Tuple[int, int]:
    by_path: DefaultDict[str, list[EpisodeMetadata]] = defaultdict(list)
    checked = 0
    for episode in adapter_result.episodes:
        if (
            episode.data_path is None
            or episode.dataset_from_index is None
            or episode.dataset_to_index is None
        ):
            continue
        checked += 1
        by_path[episode.data_path].append(episode)
        start = episode.dataset_from_index
        end = episode.dataset_to_index
        if start < 0 or end <= start:
            findings.append(
                _finding(
                    "LEROBOT_EPISODE_RANGE_INVALID",
                    "error",
                    "alignment",
                    "An episode data range must be non-negative and ordered.",
                    episode.data_path,
                    location={"episode_index": episode.episode_index},
                    evidence={
                        "dataset_from_index": start,
                        "dataset_to_index": end,
                    },
                )
            )
            continue
        if episode.length is not None and end - start != episode.length:
            findings.append(
                _finding(
                    "LEROBOT_EPISODE_RANGE_LENGTH_MISMATCH",
                    "error",
                    "alignment",
                    "An episode data range differs from its declared length.",
                    episode.data_path,
                    location={"episode_index": episode.episode_index},
                    evidence={
                        "dataset_from_index": start,
                        "dataset_to_index": end,
                        "range_length": end - start,
                        "declared_length": episode.length,
                    },
                )
            )

    # ``dataset_from_index`` and ``dataset_to_index`` are dataset-global in the
    # canonical LeRobot v3 writer, even when episodes point at later Parquet
    # files.  Validate the ledger globally, then compare each shard's global
    # span with its local footer row count.
    ordered_all = sorted(
        (
            episode
            for episodes in by_path.values()
            for episode in episodes
            if (
                episode.dataset_from_index is not None
                and episode.dataset_to_index is not None
                and episode.dataset_from_index >= 0
                and episode.dataset_to_index > episode.dataset_from_index
            )
        ),
        key=lambda item: (
            int(item.dataset_from_index or 0),
            int(item.dataset_to_index or 0),
            item.episode_index,
            item.data_path or "",
        ),
    )
    cursor = 0
    previous: Optional[EpisodeMetadata] = None
    for episode in ordered_all:
        start = int(episode.dataset_from_index or 0)
        end = int(episode.dataset_to_index or 0)
        if start > cursor:
            findings.append(
                _finding(
                    "LEROBOT_EPISODE_RANGE_GAP",
                    "error",
                    "alignment",
                    "Episode data ranges leave an uncovered dataset interval.",
                    episode.data_path,
                    location={"episode_index": episode.episode_index},
                    evidence={
                        "gap": (cursor, start),
                        "previous_episode_index": (
                            previous.episode_index if previous is not None else None
                        ),
                    },
                )
            )
        elif previous is not None and start < cursor:
            findings.append(
                _finding(
                    "LEROBOT_EPISODE_RANGE_OVERLAP",
                    "error",
                    "alignment",
                    "Episode data ranges overlap in the dataset-global ledger.",
                    episode.data_path,
                    location={"episode_index": episode.episode_index},
                    evidence={
                        "range": (start, end),
                        "previous_episode_index": previous.episode_index,
                        "previous_range": (
                            previous.dataset_from_index,
                            previous.dataset_to_index,
                        ),
                    },
                )
            )
        if end > cursor:
            cursor = end
            previous = episode

    for path, episodes in sorted(by_path.items()):
        valid_ranges = [
            (
                int(episode.dataset_from_index),
                int(episode.dataset_to_index),
                episode,
            )
            for episode in episodes
            if (
                episode.dataset_from_index is not None
                and episode.dataset_to_index is not None
                and episode.dataset_from_index >= 0
                and episode.dataset_to_index > episode.dataset_from_index
            )
        ]
        if not valid_ranges:
            continue
        shard_start = min(item[0] for item in valid_ranges)
        shard_end = max(item[1] for item in valid_ranges)
        last_episode = max(
            valid_ranges,
            key=lambda item: (item[1], item[0], item[2].episode_index),
        )[2]
        shard_rows = footer_rows.get(path)
        if shard_rows is None:
            artifact = next(
                (
                    item
                    for item in adapter_result.artifacts
                    if item.kind == "data" and item.path == path and item.row_count is not None
                ),
                None,
            )
            shard_rows = artifact.row_count if artifact is not None else None
        shard_span = shard_end - shard_start
        if shard_rows is not None and shard_span < shard_rows:
            findings.append(
                _finding(
                    "LEROBOT_EPISODE_RANGE_GAP",
                    "error",
                    "alignment",
                    "Episode data ranges leave trailing shard rows uncovered.",
                    path,
                    location={},
                    evidence={
                        "gap": (shard_end, shard_start + shard_rows),
                        "shard_global_start": shard_start,
                        "shard_rows": shard_rows,
                    },
                )
            )
        if shard_rows is not None and shard_span > shard_rows:
            findings.append(
                _finding(
                    "LEROBOT_EPISODE_RANGE_OUT_OF_BOUNDS",
                    "error",
                    "alignment",
                    "An episode data range extends beyond its shard row count.",
                    path,
                    location={
                        "episode_index": last_episode.episode_index,
                    },
                    evidence={
                        "range_end": shard_end,
                        "shard_global_start": shard_start,
                        "shard_rows": shard_rows,
                    },
                )
            )
    return checked, len(adapter_result.episodes)


def _task_metadata_references(
    adapter_result: AdapterResult,
    findings: list[Finding],
) -> None:
    task_names = {task.task for task in adapter_result.tasks}
    for episode in adapter_result.episodes:
        for task in episode.tasks:
            if task in task_names:
                continue
            findings.append(
                _finding(
                    "LEROBOT_TASK_REFERENCE_INVALID",
                    "error",
                    "alignment",
                    "Episode metadata references no task-table entry.",
                    episode.source_path,
                    location={
                        "episode_index": episode.episode_index,
                        "task": task,
                        "row": episode.source_row,
                    },
                    evidence={"known_tasks": sorted(task_names)},
                )
            )


def _video_contract(
    adapter_result: AdapterResult,
    videos: Sequence[VideoRecord],
    findings: list[Finding],
) -> Tuple[list[Mapping[str, Any]], int, int]:
    features = _declared_features(adapter_result.raw_info)
    video_features = {key: value for key, value in features.items() if _is_video_feature(value)}
    videos_by_path = {video.path: video for video in videos}
    relation_keys: DefaultDict[str, set[str]] = defaultdict(set)
    for relation in adapter_result.relations:
        if relation.kind == "video" and relation.feature_key is not None:
            relation_keys[relation.path].add(relation.feature_key)

    checked = 0
    targets = 0
    compared_pairs: set[Tuple[str, str]] = set()
    for video in sorted(videos, key=lambda item: item.path):
        keys = set(relation_keys.get(video.path, set()))
        if video.stream in video_features:
            keys.add(video.stream)
        if not keys and len(video_features) == 1:
            keys.update(video_features)
        for feature_key in sorted(keys):
            if feature_key not in video_features:
                continue
            pair = (video.path, feature_key)
            if pair in compared_pairs:
                continue
            compared_pairs.add(pair)
            checked += 1
            targets += 1
            feature = video_features[feature_key]
            raw_info = feature.get("video_info")
            video_info = raw_info if isinstance(raw_info, Mapping) else {}
            shape = _declared_shape(feature)

            def declared_number(*names: str) -> Optional[float]:
                for name in names:
                    if name in video_info:
                        parsed = _finite_float(video_info[name])
                        if parsed is not None:
                            return parsed
                return None

            declared_height = declared_number("video.height", "height")
            declared_width = declared_number("video.width", "width")
            declared_channels = declared_number("video.channels", "channels")
            declared_fps = declared_number("video.fps", "fps")
            if shape is not None:
                names = feature.get("names")
                if isinstance(names, (list, tuple)) and len(names) == len(shape):
                    named = {
                        str(name).lower(): float(shape[index]) for index, name in enumerate(names)
                    }
                    declared_height = declared_height or named.get("height")
                    declared_width = declared_width or named.get("width")
                    declared_channels = declared_channels or named.get("channels")
                elif len(shape) >= 3:
                    declared_height = declared_height or float(shape[-3])
                    declared_width = declared_width or float(shape[-2])
                    declared_channels = declared_channels or float(shape[-1])
            if (
                declared_width is not None
                and declared_height is not None
                and (video.width != int(declared_width) or video.height != int(declared_height))
            ):
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_RESOLUTION_MISMATCH",
                        "error",
                        "media",
                        "Prepared video resolution differs from the declared feature.",
                        video.path,
                        location={"feature_key": feature_key},
                        evidence={
                            "declared": (
                                int(declared_width),
                                int(declared_height),
                            ),
                            "observed": (video.width, video.height),
                        },
                    )
                )
            if (
                declared_fps is not None
                and abs(video.fps - declared_fps) > TIMESTAMP_TOLERANCE_SECONDS
            ):
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_FPS_MISMATCH",
                        "error",
                        "media",
                        "Prepared video FPS differs from the declared feature.",
                        video.path,
                        location={"feature_key": feature_key},
                        evidence={
                            "declared_fps": declared_fps,
                            "observed_fps": video.fps,
                            "tolerance_fps": TIMESTAMP_TOLERANCE_SECONDS,
                        },
                    )
                )
            if declared_channels is not None and int(declared_channels) != 3:
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_CHANNELS_MISMATCH",
                        "error",
                        "media",
                        "Decoded video channels differ from the declared feature.",
                        video.path,
                        location={"feature_key": feature_key},
                        evidence={
                            "declared_channels": int(declared_channels),
                            "observed_channels": 3,
                            "observation_source": "prepared_video_decoder_contract",
                        },
                    )
                )

    declared_keys = tuple(sorted(video_features))
    for episode in adapter_result.episodes:
        for feature_key in declared_keys:
            targets += 1
            relations = [
                relation
                for relation in adapter_result.relations
                if relation.kind == "video"
                and relation.episode_index == episode.episode_index
                and relation.feature_key == feature_key
            ]
            existing = [
                relation
                for relation in relations
                if relation.exists and relation.path in videos_by_path
            ]
            if existing:
                checked += 1
                continue
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_COVERAGE_MISSING",
                    "error",
                    "media",
                    "An episode has no prepared video for a declared camera.",
                    relations[0].path if relations else "videos",
                    location={
                        "episode_index": episode.episode_index,
                        "feature_key": feature_key,
                    },
                    evidence={
                        "declared_camera": True,
                        "relation_paths": sorted(relation.path for relation in relations),
                    },
                )
            )

    segments: DefaultDict[Tuple[str, str], list[Tuple[float, float, int]]] = defaultdict(list)
    segment_measurements: list[Mapping[str, Any]] = []
    for relation in adapter_result.relations:
        if relation.kind != "video":
            continue
        if relation.from_timestamp is None and relation.to_timestamp is None:
            continue
        feature_key = relation.feature_key or ""
        start = _finite_float(relation.from_timestamp)
        end = _finite_float(relation.to_timestamp)
        if start is None or end is None or start < 0 or end <= start:
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_SEGMENT_BOUNDS_INVALID",
                    "error",
                    "alignment",
                    "Video segment timestamps are invalid or unordered.",
                    relation.path,
                    location={
                        "episode_index": relation.episode_index,
                        "feature_key": feature_key,
                    },
                    evidence={
                        "from_timestamp": _json_value(relation.from_timestamp),
                        "to_timestamp": _json_value(relation.to_timestamp),
                    },
                )
            )
            continue
        segments[(feature_key, relation.path)].append((start, end, relation.episode_index))
        segment_measurements.append(
            {
                "episode_index": relation.episode_index,
                "feature_key": feature_key,
                "path": relation.path,
                "from_timestamp": start,
                "to_timestamp": end,
            }
        )

    for (feature_key, path), raw_segments in sorted(segments.items()):
        ordered = sorted(raw_segments)
        previous: Optional[Tuple[float, float, int]] = None
        for current in ordered:
            if previous is not None and current[0] < previous[1] - 1e-9:
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_SEGMENT_OVERLAP",
                        "error",
                        "alignment",
                        "Two episodes overlap in one camera and video shard.",
                        path,
                        location={
                            "episode_index": current[2],
                            "feature_key": feature_key,
                        },
                        evidence={
                            "previous_episode_index": previous[2],
                            "previous_range": previous[:2],
                            "range": current[:2],
                        },
                    )
                )
            if previous is None or current[1] > previous[1]:
                previous = current
        matched_video = videos_by_path.get(path)
        if matched_video is None or matched_video.fps <= 0:
            continue
        duration = (
            matched_video.frame_count / matched_video.fps
            if matched_video.frame_count > 0
            else matched_video.duration
        )
        tolerance = 1.0 / matched_video.fps
        for start, end, episode_index in ordered:
            if end <= duration + tolerance:
                continue
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_SEGMENT_OUT_OF_RANGE",
                    "error",
                    "alignment",
                    "A segment extends beyond its prepared video duration.",
                    path,
                    location={
                        "episode_index": episode_index,
                        "feature_key": feature_key,
                    },
                    evidence={
                        "from_timestamp": start,
                        "to_timestamp": end,
                        "duration_seconds": duration,
                        "tolerance_seconds": tolerance,
                    },
                )
            )
    return segment_measurements, checked, targets


def _read_stats(
    root: Path,
    adapter_result: AdapterResult,
    findings: list[Finding],
) -> Tuple[Optional[Mapping[str, Any]], Tuple[str, ...]]:
    paths = tuple(
        sorted(
            {
                artifact.path
                for artifact in adapter_result.artifacts
                if artifact.kind == "stats" and artifact.exists
            }
        )
    )
    if not paths:
        findings.append(
            _finding(
                "LEROBOT_STATS_MISSING",
                "warning",
                "metadata",
                "Optional normalization statistics are missing.",
                "meta/stats.json",
                location={},
                evidence={"required_by_base_loader": False},
            )
        )
        return None, ()
    merged: dict[str, Any] = {}
    for relative_path in paths:
        path = root / relative_path
        try:
            if path.suffix.lower() == ".jsonl":
                payloads = []
                for line_number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(),
                    start=1,
                ):
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    if not isinstance(value, Mapping):
                        raise ValueError(f"line {line_number} is not an object")
                    payloads.append(value)
                if len(payloads) == 1 and isinstance(payloads[0].get("stats"), Mapping):
                    payload = payloads[0]["stats"]
                else:
                    # Per-episode statistics are preserved as evidence but are
                    # not mistaken for one global normalization contract.
                    payload = {"_episode_stats": payloads}
            else:
                payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("stats root is not an object")
            for key, value in payload.items():
                merged[str(key)] = value
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            findings.append(
                _finding(
                    "LEROBOT_STATS_INVALID",
                    "error",
                    "metadata",
                    "Normalization statistics could not be read.",
                    relative_path,
                    location={},
                    evidence={"error": _safe_error(exc, root)},
                )
            )
    return (MappingProxyType(merged) if merged else None), paths


def _stats_values(value: Any) -> Optional[Mapping[str, Any]]:
    return value if isinstance(value, Mapping) else None


def _stats_shape_valid(
    value: Any,
    declared_shape: Optional[Tuple[int, ...]],
    *,
    count: bool,
) -> bool:
    actual = _shape(value)
    if declared_shape is None:
        return actual is not None
    if count and actual in {(), (1,), declared_shape}:
        return True
    return actual == declared_shape


def _all_finite(value: Any) -> bool:
    return all(_finite_float(item) is not None for item in _flatten(value))


def _compare_stat_values(
    feature_key: str,
    stat_name: str,
    stored: Any,
    observed: Sequence[float],
    path: str,
    findings: list[Finding],
) -> None:
    stored_flat = [_finite_float(value) for value in _flatten(stored)]
    if any(value is None for value in stored_flat) or len(stored_flat) != len(observed):
        return
    deltas = [
        abs(float(stored_value) - float(observed_value))
        for stored_value, observed_value in zip(stored_flat, observed)
        if stored_value is not None
    ]
    mismatches = [
        index
        for index, (stored_value, observed_value) in enumerate(zip(stored_flat, observed))
        if stored_value is not None
        and not math.isclose(
            stored_value,
            observed_value,
            rel_tol=STATS_RELATIVE_TOLERANCE,
            abs_tol=STATS_ABSOLUTE_TOLERANCE,
        )
    ]
    if not mismatches:
        return
    findings.append(
        _finding(
            "LEROBOT_STATS_VALUE_MISMATCH",
            "error",
            "data",
            "Stored normalization statistics differ from full recomputation.",
            path,
            location={"feature_key": feature_key, "stat": stat_name},
            evidence={
                "stored": stored_flat,
                "recomputed": tuple(observed),
                "mismatch_dimensions": mismatches,
                "max_absolute_difference": max(deltas) if deltas else None,
                "absolute_tolerance": STATS_ABSOLUTE_TOLERANCE,
                "relative_tolerance": STATS_RELATIVE_TOLERANCE,
                "method": "online_welford_population",
            },
        )
    )


def _validate_stats(
    root: Path,
    adapter_result: AdapterResult,
    features: Mapping[str, Mapping[str, Any]],
    integrity: str,
    row_collector: _RowCollector,
    all_rows_complete: bool,
    findings: list[Finding],
) -> Tuple[list[CapabilityCoverage], Mapping[str, Any]]:
    stats, paths = _read_stats(root, adapter_result, findings)
    capabilities = []
    if stats is None:
        capabilities.append(
            _capability(
                "stats.stored",
                integrity,
                status="unavailable",
                checked=0,
                total=max(1, len(paths)),
                omitted=paths or ("meta/stats.json",),
                reason_code="optional_file_missing" if not paths else "stats_unreadable",
                reason=(
                    "Optional normalization statistics are absent."
                    if not paths
                    else "Normalization statistics could not be parsed."
                ),
            )
        )
    else:
        capabilities.append(
            _capability(
                "stats.stored",
                integrity,
                status="complete",
                checked=len(paths),
                total=len(paths),
                selected=paths,
            )
        )

    normalization_keys = _normalization_features(features)
    stored_path = paths[0] if paths else "meta/stats.json"
    if stats is not None:
        for feature_key in normalization_keys:
            feature_stats = _stats_values(stats.get(feature_key))
            if feature_stats is None:
                findings.append(
                    _finding(
                        "LEROBOT_STATS_FIELD_MISSING",
                        "warning",
                        "schema",
                        "A numeric feature has no stored normalization statistics.",
                        stored_path,
                        location={"feature_key": feature_key},
                        evidence={"missing": ("feature",)},
                    )
                )
                continue
            missing = tuple(
                name for name in ("min", "max", "mean", "std", "count") if name not in feature_stats
            )
            if missing:
                findings.append(
                    _finding(
                        "LEROBOT_STATS_FIELD_MISSING",
                        "warning",
                        "schema",
                        "Stored normalization statistics omit standard fields.",
                        stored_path,
                        location={"feature_key": feature_key},
                        evidence={"missing": missing},
                    )
                )
            declared_shape = _declared_shape(features[feature_key])
            for stat_name in ("min", "max", "mean", "std", "count"):
                if stat_name not in feature_stats:
                    continue
                stat_value = feature_stats[stat_name]
                if not _stats_shape_valid(
                    stat_value,
                    declared_shape,
                    count=stat_name == "count",
                ):
                    findings.append(
                        _finding(
                            "LEROBOT_STATS_SHAPE_MISMATCH",
                            "error",
                            "schema",
                            "A stored statistic has the wrong feature dimension.",
                            stored_path,
                            location={
                                "feature_key": feature_key,
                                "stat": stat_name,
                            },
                            evidence={
                                "declared_shape": declared_shape,
                                "actual_shape": _shape(stat_value),
                            },
                        )
                    )
                if not _all_finite(stat_value):
                    findings.append(
                        _finding(
                            "LEROBOT_STATS_NON_FINITE",
                            "error",
                            "data",
                            "Stored normalization statistics contain NaN or infinity.",
                            stored_path,
                            location={
                                "feature_key": feature_key,
                                "stat": stat_name,
                            },
                            evidence={"value": _json_value(stat_value)},
                        )
                    )
            if "count" in feature_stats:
                count_values = [_finite_float(value) for value in _flatten(feature_stats["count"])]
                declared_total = _nonnegative_integer(adapter_result.raw_info.get("total_frames"))
                invalid_count = any(
                    value is None or value <= 0 or not float(value).is_integer()
                    for value in count_values
                )
                wrong_total = declared_total is not None and any(
                    value != declared_total for value in count_values
                )
                if invalid_count or wrong_total:
                    findings.append(
                        _finding(
                            "LEROBOT_STATS_COUNT_MISMATCH",
                            "error",
                            "data",
                            "Stored statistic counts are invalid or differ from total frames.",
                            stored_path,
                            location={"feature_key": feature_key, "stat": "count"},
                            evidence={
                                "stored_count": count_values,
                                "declared_total_frames": declared_total,
                            },
                        )
                    )

    recomputed: dict[str, dict[str, Any]] = {}
    for feature_key, moments in sorted(row_collector.moments.items()):
        if not moments.count:
            continue
        values = moments.values()
        recomputed[feature_key] = {
            "min": values["min"],
            "max": values["max"],
            "mean": values["mean"],
            "std": values["std"],
            "count": tuple(int(value) for value in values["count"]),
            "method": "online_welford_population",
        }

    if integrity != "full":
        capabilities.append(
            _capability(
                "stats.recomputed",
                integrity,
                status="skipped",
                checked=0,
                total=len(normalization_keys),
                omitted=normalization_keys,
                reason_code="integrity_too_low",
                reason="Only full integrity recomputes normalization statistics.",
            )
        )
    elif not all_rows_complete:
        capabilities.append(
            _capability(
                "stats.recomputed",
                integrity,
                status="partial" if recomputed else "unavailable",
                checked=len(recomputed),
                total=len(normalization_keys),
                selected=tuple(recomputed),
                omitted=tuple(set(normalization_keys) - set(recomputed)),
                reason_code="parquet_rows_incomplete",
                reason="Full statistics cannot be compared after incomplete row reads.",
            )
        )
    else:
        compared = 0
        if stats is not None:
            for feature_key, observed_stats in recomputed.items():
                feature_stats = _stats_values(stats.get(feature_key))
                if feature_stats is None:
                    continue
                compared += 1
                for stat_name in ("min", "max", "mean", "std"):
                    if stat_name in feature_stats:
                        _compare_stat_values(
                            feature_key,
                            stat_name,
                            feature_stats[stat_name],
                            tuple(float(value) for value in observed_stats[stat_name]),
                            stored_path,
                            findings,
                        )
                if "count" in feature_stats:
                    observed_count = observed_stats["count"]
                    stored_count = [
                        _finite_float(value) for value in _flatten(feature_stats["count"])
                    ]
                    if len(stored_count) == 1 and len(observed_count) > 1:
                        stored_count *= len(observed_count)
                    if len(stored_count) != len(observed_count) or any(
                        value is None or value != observed_count[index]
                        for index, value in enumerate(stored_count)
                    ):
                        findings.append(
                            _finding(
                                "LEROBOT_STATS_COUNT_MISMATCH",
                                "error",
                                "data",
                                "Stored statistic counts differ from full recomputation.",
                                stored_path,
                                location={
                                    "feature_key": feature_key,
                                    "stat": "count",
                                },
                                evidence={
                                    "stored_count": stored_count,
                                    "recomputed_count": observed_count,
                                },
                            )
                        )
        if stats is None:
            status = "partial"
            reason_code = "stored_stats_unavailable"
            reason = "Statistics were recomputed but no stored values exist to compare."
        elif compared == len(normalization_keys):
            status = "complete"
            reason_code = None
            reason = None
        else:
            status = "partial"
            reason_code = "stored_stats_incomplete"
            reason = "Some numeric features have no stored statistics to compare."
        capabilities.append(
            _capability(
                "stats.recomputed",
                integrity,
                status=status,
                checked=len(recomputed),
                total=len(normalization_keys),
                selected=tuple(recomputed),
                omitted=tuple(set(normalization_keys) - set(recomputed)),
                reason_code=reason_code,
                reason=reason,
            )
        )
    return capabilities, recomputed


def _task_measurements(
    adapter_result: AdapterResult,
    row_counts: Mapping[int, int],
) -> list[Mapping[str, Any]]:
    episode_counts: Counter[str] = Counter()
    for episode in adapter_result.episodes:
        episode_counts.update(set(episode.tasks))
    return [
        {
            "task_index": task.task_index,
            "task": task.task,
            "row_count": int(row_counts.get(task.task_index, 0)),
            "episode_count": int(episode_counts.get(task.task, 0)),
        }
        for task in sorted(
            adapter_result.tasks,
            key=lambda item: (item.task_index, item.task),
        )
    ]


def _camera_measurements(
    adapter_result: AdapterResult,
    videos: Sequence[VideoRecord],
) -> list[Mapping[str, Any]]:
    features = _declared_features(adapter_result.raw_info)
    keys = {key for key, feature in features.items() if _is_video_feature(feature)}
    keys.update(
        relation.feature_key
        for relation in adapter_result.relations
        if relation.kind == "video" and relation.feature_key is not None
    )
    result: list[Mapping[str, Any]] = []
    for key in sorted(keys):
        relations = [
            relation
            for relation in adapter_result.relations
            if relation.kind == "video" and relation.feature_key == key
        ]
        paths = {relation.path for relation in relations}
        result.append(
            {
                "feature_key": key,
                "episode_count": len({relation.episode_index for relation in relations}),
                "segment_count": sum(
                    relation.from_timestamp is not None or relation.to_timestamp is not None
                    for relation in relations
                ),
                "video_count": len(
                    {video.path for video in videos if video.path in paths or video.stream == key}
                ),
            }
        )
    return result


def validate_prepared_dataset(
    root: str | Path,
    adapter_result: AdapterResult,
    videos: Sequence[VideoRecord],
    integrity: str,
    parquet_batch_size: int = 1024,
) -> ValidationResult:
    """Validate one already discovered dataset with deterministic coverage.

    ``metadata`` reads every available Parquet footer and schema but no rows.
    ``sample`` reads all rows from deterministic first/middle/last shards and
    reports partial coverage.  ``full`` reads every shard in bounded batches
    and performs the standard-statistics comparison.
    """
    if integrity not in _INTEGRITIES:
        raise ValueError("integrity must be 'metadata', 'sample', or 'full'")
    if (
        isinstance(parquet_batch_size, bool)
        or not isinstance(parquet_batch_size, int)
        or parquet_batch_size <= 0
    ):
        raise ValueError("parquet_batch_size must be a positive integer")

    dataset_root = Path(root).resolve()
    findings = [_normalize_finding(item) for item in adapter_result.findings]
    features = _declared_features(adapter_result.raw_info)
    _metadata_totals(adapter_result, videos, findings)
    _episode_indexes(adapter_result.episodes, findings)
    _task_metadata_references(adapter_result, findings)

    parquet_result = _scan_parquet(
        dataset_root,
        adapter_result,
        integrity,
        parquet_batch_size,
        features,
        findings,
    )
    range_checked, range_total = _episode_ranges(
        adapter_result,
        parquet_result.footer_rows,
        findings,
    )
    segment_measurements, video_checked, video_total = _video_contract(
        adapter_result,
        videos,
        findings,
    )

    all_paths, _existing_paths = _data_artifact_paths(adapter_result)
    all_rows_complete = integrity == "full" and len(parquet_result.complete_row_paths) == len(
        all_paths
    )
    row_measurements = parquet_result.row_collector.finalize(
        complete_paths=parquet_result.complete_row_paths,
        all_rows_complete=all_rows_complete,
    )
    if all_rows_complete:
        declared_frames = _nonnegative_integer(adapter_result.raw_info.get("total_frames"))
        if (
            declared_frames is not None
            and declared_frames != parquet_result.row_collector.total_rows
        ):
            findings.append(
                _finding(
                    "LEROBOT_FRAME_COUNT_MISMATCH",
                    "error",
                    "data",
                    "Declared frame total differs from full Parquet row count.",
                    "meta/info.json",
                    location={"json_pointer": "/total_frames"},
                    evidence={
                        "declared": declared_frames,
                        "discovered": parquet_result.row_collector.total_rows,
                        "method": "count_all_parquet_rows",
                    },
                )
            )

    stats_capabilities, recomputed_stats = _validate_stats(
        dataset_root,
        adapter_result,
        features,
        integrity,
        parquet_result.row_collector,
        all_rows_complete,
        findings,
    )

    capabilities = list(parquet_result.capabilities)
    capabilities.extend(stats_capabilities)
    capabilities.extend(
        [
            _capability(
                "metadata.totals",
                integrity,
                status="complete",
                checked=1,
                total=1,
                selected=("meta/info.json",),
            ),
            _capability(
                "metadata.episode_indexes",
                integrity,
                status="complete",
                checked=len(adapter_result.episodes),
                total=len(adapter_result.episodes),
                selected=tuple(
                    sorted({episode.source_path for episode in adapter_result.episodes})
                ),
            ),
            _capability(
                "alignment.episode_ranges",
                integrity,
                status=(
                    "complete"
                    if range_checked == range_total
                    else "partial"
                    if range_checked
                    else "unavailable"
                ),
                checked=range_checked,
                total=range_total,
                reason_code=(
                    None
                    if range_checked == range_total
                    else "partial_capability_coverage"
                    if range_checked
                    else "source_capability_unavailable"
                ),
                reason=(
                    None
                    if range_checked == range_total
                    else "Some episodes have no prepared data-range relation."
                ),
            ),
            _capability(
                "media.declared_contract",
                integrity,
                status=(
                    "complete"
                    if video_checked == video_total
                    else "partial"
                    if video_checked
                    else "unavailable"
                ),
                checked=video_checked,
                total=video_total,
                reason_code=(
                    None
                    if video_checked == video_total
                    else "partial_capability_coverage"
                    if video_checked
                    else "source_capability_unavailable"
                ),
                reason=(
                    None
                    if video_checked == video_total
                    else "Some declared cameras have no prepared video record."
                ),
            ),
        ]
    )
    row_capability = next(item for item in capabilities if item.capability == "data.parquet_rows")
    row_dependent_status = row_capability.status
    row_reason_code = row_capability.reason_code
    row_reason = row_capability.reason
    for capability_name in (
        "data.indexes",
        "alignment.timestamps",
        "alignment.task_foreign_keys",
        "advisory.measurements",
    ):
        capabilities.append(
            _capability(
                capability_name,
                integrity,
                status=row_dependent_status,
                checked=row_capability.checked,
                total=row_capability.total,
                selected=row_capability.selected,
                omitted=row_capability.omitted,
                reason_code=row_reason_code,
                reason=row_reason,
            )
        )

    measurements = {
        "schema_version": "openbot.validation_measurements.v1",
        "integrity": integrity,
        "coverage": {
            "status": row_capability.status,
            "selected_data_shards": row_capability.selected,
            "omitted_data_shards": row_capability.omitted,
            "partial": row_capability.status != "complete",
            "reason_code": row_capability.reason_code,
        },
        "numeric_features": row_measurements["numeric_features"],
        "static_action_spans": row_measurements["static_action_spans"],
        "episode_content_digests": row_measurements["episode_content_digests"],
        "task_counts": _task_measurements(
            adapter_result,
            parquet_result.row_collector.task_rows,
        ),
        "camera_counts": _camera_measurements(adapter_result, videos),
        "video_segments": segment_measurements,
        "recomputed_stats": recomputed_stats if integrity == "full" else {},
    }
    return ValidationResult(
        findings=_sorted_findings(findings),
        capabilities=tuple(sorted(capabilities, key=lambda item: item.capability)),
        measurements=freeze_value(measurements),
    )


__all__ = [
    "STATS_ABSOLUTE_TOLERANCE",
    "STATS_RELATIVE_TOLERANCE",
    "TIMESTAMP_TOLERANCE_SECONDS",
    "ValidationResult",
    "validate_prepared_dataset",
]
