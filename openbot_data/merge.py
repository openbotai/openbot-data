"""Deterministic merge compatibility planning and post-merge verification."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Union

from openbot_data import __version__
from openbot_data.diff import (
    DIFF_SCHEMA_VERSION,
    _load_snapshot,
    diff_dataset_snapshots,
)
from openbot_data.errors import DatasetArgumentError
from openbot_data.preflight import audit_dataset, dataset_fingerprint
from openbot_data.serialization import write_json_atomic
from openbot_data.snapshot import build_dataset_snapshot

MERGE_PLAN_SCHEMA_VERSION = "openbot.dataset_merge_plan.v1"
MERGE_PLAN_FINGERPRINT_VERSION = "openbot.dataset_merge_plan.fingerprint.v1"
MERGE_RECEIPT_SCHEMA_VERSION = "openbot.dataset_merge_receipt.v1"
MERGE_RECEIPT_FINGERPRINT_VERSION = "openbot.dataset_merge_receipt.fingerprint.v1"
PINNED_LEROBOT_PACKAGE = "lerobot==0.6.0"
PINNED_LEROBOT_REQUIREMENT = "lerobot[dataset]==0.6.0"
PINNED_LEROBOT_TOOL = "lerobot-edit-dataset"

_HEX_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_VERSION = re.compile(
    r"^v?(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)"
    r"(?:\.(?P<patch>0|[1-9][0-9]*))?$"
)
_DECISION_RANK = {
    "direct": 0,
    "transform_required": 1,
    "unknown": 2,
    "incompatible": 3,
}
_NUMERIC_DTYPES = {
    "bool",
    "float16",
    "float32",
    "float64",
    "int8",
    "int16",
    "int32",
    "int64",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
}
_SEMANTIC_KEYS = {
    "axis",
    "axes",
    "coordinate",
    "coordinate_frame",
    "coordinate_system",
    "frame",
    "representation",
    "semantic",
    "semantics",
    "type",
    "unit",
    "units",
}
_NORMALIZATION_KEYS = {
    "normalization",
    "normalization_mode",
    "normalized",
    "range",
    "scale",
}
_CODEC_KEYS = {
    "codec",
    "has_audio",
    "is_depth_map",
    "pix_fmt",
    "video.channels",
    "video.codec",
    "video.fps",
    "video.height",
    "video.is_depth_map",
    "video.pix_fmt",
    "video.width",
}
_RESTRICTIVE_LICENSE_MARKERS = {
    "all rights reserved",
    "no redistribution",
    "proprietary",
}

SnapshotInput = Union[Mapping[str, Any], str, Path]
SnapshotBuilder = Callable[..., Mapping[str, Any]]
AuditRunner = Callable[..., Mapping[str, Any]]
DiffRunner = Callable[[SnapshotInput, SnapshotInput], Mapping[str, Any]]
LoaderRunner = Callable[[Any], Any]


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DatasetArgumentError("Merge evidence cannot contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            if not isinstance(key, str):
                raise DatasetArgumentError("Merge evidence object keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise DatasetArgumentError(
        f"Merge evidence must be JSON-compatible, got {type(value).__name__}"
    )


def _validate_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    # Merge consumes the exact same canonical snapshot contract as semantic
    # diff.  Reusing the strict loader prevents a caller from re-fingerprinting
    # a schema-invalid snapshot or forging one component digest while keeping
    # the top-level digest internally consistent.
    return _load_snapshot(value)


def _resolve_snapshot(
    value: SnapshotInput,
    *,
    snapshot_builder: SnapshotBuilder,
    local_integrity: str = "full",
) -> tuple[dict[str, Any], Optional[Path]]:
    if isinstance(value, Mapping):
        return _validate_snapshot(value), None
    path = Path(value)
    if path.is_dir():
        built = snapshot_builder(
            str(path),
            checksum="sha256",
            integrity=local_integrity,
        )
        return _validate_snapshot(built), path
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetArgumentError(f"Snapshot could not be read: {path}") from exc
    if not isinstance(loaded, Mapping):
        raise DatasetArgumentError(f"Snapshot must be a JSON object: {path}")
    return _validate_snapshot(loaded), None


def _check(
    name: str,
    status: str,
    summary: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if status not in _DECISION_RANK:
        raise ValueError(f"Unsupported merge check status: {status}")
    return {
        "check": name,
        "status": status,
        "summary": summary,
        "evidence": _json_value(evidence),
    }


def _decision(checks: Iterable[Mapping[str, Any]]) -> str:
    return max(
        (str(item["status"]) for item in checks),
        key=lambda status: _DECISION_RANK[status],
        default="unknown",
    )


def _normalized_version(value: object) -> Optional[tuple[int, int, int]]:
    if not isinstance(value, str):
        return None
    match = _VERSION.fullmatch(value.strip())
    if match is None:
        return None
    patch = match.group("patch")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(patch) if patch is not None else 0,
    )


def _format_check(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    formats = [item.get("format") for item in snapshots]
    if any(not isinstance(item, Mapping) for item in formats):
        return _check(
            "format_and_version",
            "unknown",
            "At least one snapshot has no usable format contract.",
            {"formats": formats},
        )
    normalized_formats = [
        item for item in formats if isinstance(item, Mapping)
    ]
    input_formats = [
        item.get("input_format") for item in normalized_formats
    ]
    versions = [
        item.get("dataset_format_version") for item in normalized_formats
    ]
    adapters = [item.get("adapter") for item in normalized_formats]
    targets = [
        item.get("compatibility_target") for item in normalized_formats
    ]
    evidence = {
        "input_formats": input_formats,
        "dataset_format_versions": versions,
        "adapters": adapters,
        "compatibility_targets": targets,
    }
    if any(value != "lerobot" for value in input_formats):
        return _check(
            "format_and_version",
            "incompatible",
            "The pinned official merge only accepts LeRobot datasets.",
            evidence,
        )
    parsed = [_normalized_version(value) for value in versions]
    if any(value is None for value in parsed):
        return _check(
            "format_and_version",
            "unknown",
            "A declared LeRobot version could not be established.",
            evidence,
        )
    if any(
        not isinstance(adapter, str) or "unverified" in adapter or adapter == "lerobot_unknown"
        for adapter in adapters
    ):
        return _check(
            "format_and_version",
            "unknown",
            "At least one snapshot uses an unverified LeRobot adapter contract.",
            evidence,
        )
    if len(set(parsed)) > 1:
        supported = {(2, 1, 0), (3, 0, 0)}
        if set(parsed).issubset(supported):
            return _check(
                "format_and_version",
                "transform_required",
                "Inputs must be migrated to one LeRobot storage contract before merge.",
                evidence,
            )
        return _check(
            "format_and_version",
            "unknown",
            "Version compatibility is not proven for every input.",
            evidence,
        )
    if parsed[0] not in {(2, 1, 0), (3, 0, 0)}:
        return _check(
            "format_and_version",
            "unknown",
            "The declared LeRobot storage contract is not a supported exact version.",
            evidence,
        )
    if len(set(adapters)) != 1:
        return _check(
            "format_and_version",
            "unknown",
            "Equal versions were interpreted by different adapters.",
            evidence,
        )
    if any(target != PINNED_LEROBOT_PACKAGE for target in targets):
        return _check(
            "format_and_version",
            "unknown",
            "Every input must declare compatibility with the pinned LeRobot release.",
            evidence,
        )
    return _check(
        "format_and_version",
        "direct",
        "All inputs use one verified LeRobot storage contract.",
        evidence,
    )


def _feature_map(snapshot: Mapping[str, Any]) -> Optional[dict[str, Mapping[str, Any]]]:
    contract = snapshot.get("contract")
    if not isinstance(contract, Mapping):
        return None
    features = contract.get("features")
    if not isinstance(features, list):
        return None
    result: dict[str, Mapping[str, Any]] = {}
    for feature in features:
        if not isinstance(feature, Mapping):
            return None
        key = feature.get("key")
        if not isinstance(key, str) or not key or key in result:
            return None
        result[key] = feature
    return result


def _dtype_family(value: object) -> str:
    normalized = str(value).lower()
    if normalized in _NUMERIC_DTYPES:
        return "numeric"
    if normalized in {"video", "image", "audio", "string"}:
        return normalized
    return "unknown"


def _selected_metadata(
    value: object,
    selected_keys: set[str],
    *,
    prefix: str = "",
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    selected: dict[str, Any] = {}
    for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
        normalized_key = str(key)
        path = f"{prefix}.{normalized_key}" if prefix else normalized_key
        lowered = normalized_key.lower()
        if lowered in selected_keys or path.lower() in selected_keys:
            selected[path] = _json_value(item)
        if isinstance(item, Mapping):
            selected.update(_selected_metadata(item, selected_keys, prefix=path))
    return selected


def _feature_check(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    feature_maps = [_feature_map(item) for item in snapshots]
    if any(item is None for item in feature_maps):
        return _check(
            "features",
            "unknown",
            "At least one feature contract is malformed or unavailable.",
            {"feature_keys": []},
        )
    maps = [item for item in feature_maps if item is not None]
    key_sets = [set(item) for item in maps]
    mismatches: list[dict[str, Any]] = []
    status = "direct"
    if any(keys != key_sets[0] for keys in key_sets[1:]):
        status = "transform_required"
        mismatches.append(
            {
                "kind": "feature_keys",
                "values": [sorted(keys) for keys in key_sets],
            }
        )
    for key in sorted(set.intersection(*key_sets) if key_sets else set()):
        records = [item[key] for item in maps]
        dtypes = [record.get("dtype") for record in records]
        shapes = [record.get("shape") for record in records]
        names = [record.get("names") for record in records]
        semantics = [
            _selected_metadata(record.get("metadata"), _SEMANTIC_KEYS) for record in records
        ]
        if any(dtype is None for dtype in dtypes) or any(
            not isinstance(shape, list) for shape in shapes
        ):
            status = max(status, "unknown", key=lambda item: _DECISION_RANK[item])
            mismatches.append(
                {
                    "kind": "feature_contract_missing",
                    "feature": key,
                    "dtypes": dtypes,
                    "shapes": shapes,
                }
            )
        elif len(set(str(value) for value in dtypes)) > 1:
            families = {_dtype_family(value) for value in dtypes}
            mismatch_status = "transform_required" if families == {"numeric"} else "incompatible"
            status = max(status, mismatch_status, key=lambda item: _DECISION_RANK[item])
            mismatches.append(
                {
                    "kind": "dtype",
                    "feature": key,
                    "values": dtypes,
                }
            )
        if any(shape != shapes[0] for shape in shapes[1:]):
            status = "incompatible"
            mismatches.append(
                {
                    "kind": "shape",
                    "feature": key,
                    "values": shapes,
                }
            )
        if any(name != names[0] for name in names[1:]):
            status = "incompatible"
            mismatches.append(
                {
                    "kind": "declared_names",
                    "feature": key,
                    "values": names,
                }
            )
        if any(semantic != semantics[0] for semantic in semantics[1:]):
            status = "incompatible"
            mismatches.append(
                {
                    "kind": "declared_semantics",
                    "feature": key,
                    "values": semantics,
                }
            )
    summaries = {
        "direct": "Feature keys, dtypes, shapes, and declared semantics agree.",
        "transform_required": "Known feature transforms are required before merge.",
        "incompatible": "Feature shape or semantic contracts conflict.",
        "unknown": "Feature compatibility is incomplete.",
    }
    return _check(
        "features",
        status,
        summaries[status],
        {
            "feature_keys": [sorted(keys) for keys in key_sets],
            "mismatches": mismatches,
        },
    )


def _role_feature_keys(feature_map: Mapping[str, Any], role: str) -> list[str]:
    if role == "action":
        return sorted(key for key in feature_map if key == "action" or key.startswith("action."))
    return sorted(
        key
        for key in feature_map
        if key == "state" or key == "observation.state" or key.startswith("observation.state.")
    )


def _role_contract(snapshot: Mapping[str, Any], role: str) -> dict[str, Any]:
    features = _feature_map(snapshot) or {}
    raw_format = snapshot.get("format")
    raw_metadata = raw_format.get("metadata") if isinstance(raw_format, Mapping) else {}
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    role_features: dict[str, Any] = {}
    for key in _role_feature_keys(features, role):
        feature = features[key]
        names = feature.get("names")
        feature_metadata = feature.get("metadata")
        role_features[key] = {
            "names": names if isinstance(names, list) else [],
            "coordinates": _selected_metadata(
                feature_metadata,
                _SEMANTIC_KEYS,
            ),
            "normalization": _selected_metadata(
                feature_metadata,
                _NORMALIZATION_KEYS,
            ),
        }
    top_coordinates = _selected_metadata(
        {key: value for key, value in metadata.items() if str(key).lower().startswith(role)},
        _SEMANTIC_KEYS,
    )
    top_normalization = _selected_metadata(
        {
            key: value
            for key, value in metadata.items()
            if str(key).lower().startswith(role) or str(key).lower() in _NORMALIZATION_KEYS
        },
        _NORMALIZATION_KEYS,
    )
    return {
        "features": role_features,
        "top_coordinates": top_coordinates,
        "top_normalization": top_normalization,
    }


def _role_has_coordinates(contract: Mapping[str, Any]) -> bool:
    if contract.get("top_coordinates"):
        return True
    features = contract.get("features")
    if not isinstance(features, Mapping):
        return False
    return all(
        bool(item.get("coordinates")) or bool(item.get("names"))
        for item in features.values()
        if isinstance(item, Mapping)
    )


def _role_has_normalization(contract: Mapping[str, Any]) -> bool:
    if contract.get("top_normalization"):
        return True
    features = contract.get("features")
    if not isinstance(features, Mapping):
        return False
    return all(
        bool(item.get("normalization")) for item in features.values() if isinstance(item, Mapping)
    )


def _action_state_check(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    contracts = {
        role: [_role_contract(snapshot, role) for snapshot in snapshots]
        for role in ("action", "state")
    }
    active_roles = [
        role
        for role, values in contracts.items()
        if any(bool(value["features"]) for value in values)
    ]
    if not active_roles:
        return _check(
            "action_state_contract",
            "direct",
            "No action or state feature requires a coordinate contract.",
            {"roles": contracts},
        )
    missing: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for role in active_roles:
        values = contracts[role]
        if any(not value["features"] for value in values):
            conflicts.append({"role": role, "kind": "feature_presence"})
            continue
        if any(not _role_has_coordinates(value) for value in values):
            missing.append({"role": role, "kind": "coordinates"})
        if any(not _role_has_normalization(value) for value in values):
            missing.append({"role": role, "kind": "normalization"})
        if any(value != values[0] for value in values[1:]):
            conflicts.append(
                {
                    "role": role,
                    "kind": "contract",
                    "values": values,
                }
            )
    if conflicts:
        return _check(
            "action_state_contract",
            "incompatible",
            "Action or state coordinate/normalization contracts conflict.",
            {"roles": contracts, "missing": missing, "conflicts": conflicts},
        )
    if missing:
        return _check(
            "action_state_contract",
            "unknown",
            "Action or state coordinate/normalization evidence is incomplete.",
            {"roles": contracts, "missing": missing, "conflicts": conflicts},
        )
    return _check(
        "action_state_contract",
        "direct",
        "Action and state coordinate/normalization contracts agree.",
        {"roles": contracts, "missing": [], "conflicts": []},
    )


def _video_stream_map(snapshot: Mapping[str, Any]) -> Optional[dict[str, Mapping[str, Any]]]:
    contract = snapshot.get("contract")
    if not isinstance(contract, Mapping):
        return None
    streams = contract.get("video_streams")
    if not isinstance(streams, list):
        return None
    result: dict[str, Mapping[str, Any]] = {}
    for stream in streams:
        if not isinstance(stream, Mapping):
            return None
        key = stream.get("key")
        if not isinstance(key, str) or not key or key in result:
            return None
        result[key] = stream
    return result


def _camera_keys(snapshot: Mapping[str, Any]) -> set[str]:
    streams = _video_stream_map(snapshot) or {}
    features = _feature_map(snapshot) or {}
    return set(streams) | {
        key for key, feature in features.items() if str(feature.get("dtype", "")).lower() == "video"
    }


def _format_metadata(snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
    value = snapshot.get("format")
    if not isinstance(value, Mapping):
        return {}
    metadata = value.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _global_fps(snapshot: Mapping[str, Any]) -> Optional[float]:
    raw = _format_metadata(snapshot).get("fps")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    streams = _video_stream_map(snapshot) or {}
    values: set[float] = set()
    for stream in streams.values():
        raw_values = stream.get("fps")
        if isinstance(raw_values, list):
            values.update(
                float(value)
                for value in raw_values
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
            )
    return next(iter(values)) if len(values) == 1 else None


def _delta_contract(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _format_metadata(snapshot)
    return {
        key: _json_value(value)
        for key, value in sorted(metadata.items())
        if str(key).lower()
        in {
            "delta_horizons",
            "delta_indices",
            "delta_timestamps",
            "delta_time_offsets",
        }
    }


def _video_codec_contract(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    features = _feature_map(snapshot) or {}
    result: dict[str, Any] = {}
    for key in sorted(_camera_keys(snapshot)):
        feature = features.get(key, {})
        result[key] = _selected_metadata(feature.get("metadata"), _CODEC_KEYS)
    return result


def _media_coverage_complete(snapshot: Mapping[str, Any]) -> bool:
    coverage = snapshot.get("coverage")
    if not isinstance(coverage, Mapping):
        return False
    integrity = coverage.get("requested_integrity")
    completed = coverage.get("completed_capabilities")
    return (
        integrity in {"sample", "full"}
        and isinstance(completed, list)
        and "media.metadata" in completed
        and "media.decode" in completed
    )


def _media_check(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    stream_maps = [_video_stream_map(snapshot) for snapshot in snapshots]
    if any(stream is None for stream in stream_maps):
        return _check(
            "timing_and_media",
            "unknown",
            "At least one video-stream contract is malformed.",
            {},
        )
    streams = [stream for stream in stream_maps if stream is not None]
    cameras = [_camera_keys(snapshot) for snapshot in snapshots]
    fps_values = [_global_fps(snapshot) for snapshot in snapshots]
    deltas = [_delta_contract(snapshot) for snapshot in snapshots]
    codecs = [_video_codec_contract(snapshot) for snapshot in snapshots]
    transforms: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    status = "direct"
    if any(keys != cameras[0] for keys in cameras[1:]):
        status = "transform_required"
        transforms.append(
            {"kind": "camera_mapping", "values": [sorted(value) for value in cameras]}
        )
    if any(value is None for value in fps_values):
        status = max(status, "unknown", key=lambda item: _DECISION_RANK[item])
        missing.append({"kind": "fps", "values": fps_values})
    elif any(value != fps_values[0] for value in fps_values[1:]):
        status = max(
            status,
            "transform_required",
            key=lambda item: _DECISION_RANK[item],
        )
        transforms.append({"kind": "fps_resample", "values": fps_values})
    if any(value != deltas[0] for value in deltas[1:]):
        status = max(
            status,
            "transform_required",
            key=lambda item: _DECISION_RANK[item],
        )
        transforms.append({"kind": "delta_horizons", "values": deltas})
    common_cameras = set.intersection(*cameras) if cameras else set()
    for camera in sorted(common_cameras):
        values = [stream[camera] for stream in streams]
        resolutions = [value.get("resolutions") for value in values]
        stream_fps = [value.get("fps") for value in values]
        if any(value != resolutions[0] for value in resolutions[1:]):
            status = max(
                status,
                "transform_required",
                key=lambda item: _DECISION_RANK[item],
            )
            transforms.append(
                {
                    "kind": "resolution",
                    "camera": camera,
                    "values": resolutions,
                }
            )
        if any(value != stream_fps[0] for value in stream_fps[1:]):
            status = max(
                status,
                "transform_required",
                key=lambda item: _DECISION_RANK[item],
            )
            transforms.append(
                {
                    "kind": "stream_fps",
                    "camera": camera,
                    "values": stream_fps,
                }
            )
    if any(cameras):
        for index, snapshot in enumerate(snapshots):
            if cameras[index] and not _media_coverage_complete(snapshot):
                status = max(status, "unknown", key=lambda item: _DECISION_RANK[item])
                missing.append({"kind": "media_coverage", "input": index + 1})
        for index, codec in enumerate(codecs):
            for camera in sorted(cameras[index]):
                if not codec.get(camera):
                    status = max(
                        status,
                        "unknown",
                        key=lambda item: _DECISION_RANK[item],
                    )
                    missing.append(
                        {
                            "kind": "codec_metadata",
                            "input": index + 1,
                            "camera": camera,
                        }
                    )
        for camera in sorted(common_cameras):
            values = [codec.get(camera, {}) for codec in codecs]
            depth_values = [
                {key: value for key, value in item.items() if key.lower().endswith("is_depth_map")}
                for item in values
            ]
            if any(value != depth_values[0] for value in depth_values[1:]):
                status = "incompatible"
                conflicts.append(
                    {
                        "kind": "depth_semantics",
                        "camera": camera,
                        "values": depth_values,
                    }
                )
            elif any(value != values[0] for value in values[1:]):
                status = max(
                    status,
                    "transform_required",
                    key=lambda item: _DECISION_RANK[item],
                )
                transforms.append(
                    {
                        "kind": "codec",
                        "camera": camera,
                        "values": values,
                    }
                )
    summaries = {
        "direct": "FPS, horizons, cameras, codec metadata, and media coverage agree.",
        "transform_required": "Known timing or media transforms are required.",
        "incompatible": "Camera media semantics conflict.",
        "unknown": "Timing or media evidence is incomplete.",
    }
    return _check(
        "timing_and_media",
        status,
        summaries[status],
        {
            "fps": fps_values,
            "delta_horizons": deltas,
            "cameras": [sorted(value) for value in cameras],
            "codec": codecs,
            "transforms": transforms,
            "conflicts": conflicts,
            "missing": missing,
        },
    )


def _task_values(snapshot: Mapping[str, Any]) -> Optional[list[str]]:
    contract = snapshot.get("contract")
    if not isinstance(contract, Mapping):
        return None
    tasks = contract.get("tasks")
    if not isinstance(tasks, list):
        return None
    values: list[str] = []
    for task in tasks:
        if not isinstance(task, Mapping):
            return None
        value = task.get("task")
        if not isinstance(value, str) or not value:
            return None
        values.append(value)
    return values


def _task_check(
    snapshots: Sequence[Mapping[str, Any]],
    task_remap: Mapping[str, str],
) -> dict[str, Any]:
    task_lists = [_task_values(snapshot) for snapshot in snapshots]
    if any(value is None for value in task_lists):
        return _check(
            "tasks",
            "unknown",
            "At least one task contract is unavailable.",
            {"tasks": task_lists, "task_remap": task_remap},
        )
    lists = [value for value in task_lists if value is not None]
    duplicates = [sorted({task for task in values if values.count(task) > 1}) for values in lists]
    if any(duplicates):
        return _check(
            "tasks",
            "incompatible",
            "A snapshot contains duplicate task identities.",
            {"tasks": lists, "duplicates": duplicates, "task_remap": task_remap},
        )
    known_tasks = {task for values in lists for task in values}
    unknown_remap = sorted(set(task_remap) - known_tasks)
    if unknown_remap:
        raise DatasetArgumentError(f"task_remap contains unknown task identities: {unknown_remap}")
    episode_misses: list[dict[str, Any]] = []
    for index, snapshot in enumerate(snapshots):
        contract = snapshot.get("contract")
        episodes = contract.get("episodes", []) if isinstance(contract, Mapping) else []
        if not isinstance(episodes, list):
            continue
        declared = set(lists[index])
        for episode in episodes:
            if not isinstance(episode, Mapping):
                continue
            episode_tasks = episode.get("tasks")
            if not isinstance(episode_tasks, list):
                continue
            missing = sorted(
                task for task in episode_tasks if isinstance(task, str) and task not in declared
            )
            if missing:
                episode_misses.append(
                    {
                        "input": index + 1,
                        "episode_index": episode.get("episode_index"),
                        "tasks": missing,
                    }
                )
    if episode_misses:
        return _check(
            "tasks",
            "incompatible",
            "Episode task references are not declared by their input.",
            {
                "tasks": lists,
                "episode_reference_misses": episode_misses,
                "task_remap": task_remap,
            },
        )
    applied = {key: value for key, value in task_remap.items() if key != value}
    normalized = [sorted({task_remap.get(task, task) for task in values}) for values in lists]
    if applied:
        return _check(
            "tasks",
            "transform_required",
            "The declared task remap must be applied before merge.",
            {
                "tasks": lists,
                "normalized_tasks": normalized,
                "task_remap": task_remap,
            },
        )
    return _check(
        "tasks",
        "direct",
        "Task identities can be unioned without remapping.",
        {
            "tasks": lists,
            "normalized_tasks": normalized,
            "task_remap": {},
        },
    )


def _embodiment(snapshot: Mapping[str, Any]) -> Any:
    metadata = _format_metadata(snapshot)
    for key in ("robot_type", "robot_types", "embodiment", "embodiments"):
        if key in metadata:
            return _json_value(metadata[key])
    return None


def _embodiment_check(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    feature_maps = [_feature_map(snapshot) or {} for snapshot in snapshots]
    has_control = any(
        _role_feature_keys(feature_map, "action") or _role_feature_keys(feature_map, "state")
        for feature_map in feature_maps
    )
    values = [_embodiment(snapshot) for snapshot in snapshots]
    if not has_control:
        return _check(
            "embodiment",
            "direct",
            "No action/state contract requires embodiment identity.",
            {"embodiments": values},
        )
    if any(value is None for value in values):
        return _check(
            "embodiment",
            "unknown",
            "Robot or embodiment provenance is missing.",
            {"embodiments": values},
        )
    if any(value != values[0] for value in values[1:]):
        return _check(
            "embodiment",
            "incompatible",
            "Action/state datasets declare different embodiments.",
            {"embodiments": values},
        )
    return _check(
        "embodiment",
        "direct",
        "Robot or embodiment provenance agrees.",
        {"embodiments": values},
    )


def _inventory(snapshot: Mapping[str, Any], group: str) -> list[Mapping[str, Any]]:
    inventory = snapshot.get("inventory")
    if not isinstance(inventory, Mapping):
        return []
    values = inventory.get(group)
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, Mapping)]


def _duplicate_check(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fingerprints = [str(snapshot["snapshot_fingerprint"]) for snapshot in snapshots]
    if len(set(fingerprints)) != len(fingerprints):
        return _check(
            "duplicates",
            "incompatible",
            "The same snapshot was supplied more than once.",
            {"snapshot_fingerprints": fingerprints, "overlaps": []},
        )
    digest_sets: list[set[str]] = []
    missing: list[dict[str, Any]] = []
    for input_index, snapshot in enumerate(snapshots):
        digests: set[str] = set()
        records = _inventory(snapshot, "data") + _inventory(snapshot, "media")
        totals = snapshot.get("totals")
        declared_payload = (
            int(totals.get("data_shards", 0)) + int(totals.get("media_shards", 0))
            if isinstance(totals, Mapping)
            and isinstance(totals.get("data_shards", 0), int)
            and not isinstance(totals.get("data_shards", 0), bool)
            and isinstance(totals.get("media_shards", 0), int)
            and not isinstance(totals.get("media_shards", 0), bool)
            else None
        )
        if declared_payload and not records:
            missing.append(
                {
                    "input": input_index + 1,
                    "path": None,
                    "reason": "payload_inventory_missing",
                }
            )
        for record in records:
            digest = record.get("sha256")
            if isinstance(digest, str) and _HEX_DIGEST.fullmatch(digest):
                digests.add(digest)
            else:
                missing.append(
                    {
                        "input": input_index + 1,
                        "path": record.get("path"),
                    }
                )
        digest_sets.append(digests)
    overlaps: list[dict[str, Any]] = []
    for left in range(len(digest_sets)):
        for right in range(left + 1, len(digest_sets)):
            shared = sorted(digest_sets[left] & digest_sets[right])
            if shared:
                overlaps.append(
                    {
                        "left_input": left + 1,
                        "right_input": right + 1,
                        "sha256": shared,
                    }
                )
    evidence = {
        "snapshot_fingerprints": fingerprints,
        "content_digest_counts": [len(value) for value in digest_sets],
        "overlaps": overlaps,
        "missing_checksums": missing,
    }
    if overlaps:
        return _check(
            "duplicates",
            "incompatible",
            "Input payload contains exact duplicate data or media content.",
            evidence,
        )
    if missing:
        return _check(
            "duplicates",
            "unknown",
            "Complete content-duplicate evidence is unavailable.",
            evidence,
        )
    return _check(
        "duplicates",
        "direct",
        "No exact duplicate input payload was found.",
        evidence,
    )


def _episode_values(snapshot: Mapping[str, Any]) -> Optional[list[Mapping[str, Any]]]:
    contract = snapshot.get("contract")
    if not isinstance(contract, Mapping):
        return None
    episodes = contract.get("episodes")
    if not isinstance(episodes, list):
        return None
    if any(not isinstance(episode, Mapping) for episode in episodes):
        return None
    return [episode for episode in episodes if isinstance(episode, Mapping)]


def _range_collisions(
    episodes: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_resource: dict[tuple[str, str], list[tuple[float, float, Any]]] = {}
    malformed: list[dict[str, Any]] = []
    for episode in episodes:
        segments = episode.get("video_segments")
        if not isinstance(segments, list):
            malformed.append(
                {
                    "episode_index": episode.get("episode_index"),
                    "reason": "video_segments_not_array",
                }
            )
            continue
        for segment in segments:
            if not isinstance(segment, Mapping):
                malformed.append(
                    {
                        "episode_index": episode.get("episode_index"),
                        "reason": "video_segment_not_object",
                    }
                )
                continue
            stream = segment.get("video_key")
            path = segment.get("path")
            start = segment.get("from_timestamp")
            end = segment.get("to_timestamp")
            if not segment:
                malformed.append(
                    {
                        "episode_index": episode.get("episode_index"),
                        "reason": "video_segment_empty",
                    }
                )
                continue
            if start is None and end is None:
                # v2 snapshots may carry file references without timestamp ranges.
                continue
            if (
                not isinstance(stream, str)
                or not isinstance(path, str)
                or not isinstance(start, (int, float))
                or isinstance(start, bool)
                or not isinstance(end, (int, float))
                or isinstance(end, bool)
                or not math.isfinite(float(start))
                or not math.isfinite(float(end))
                or float(end) <= float(start)
            ):
                malformed.append(
                    {
                        "episode_index": episode.get("episode_index"),
                        "video_key": stream,
                        "path": path,
                        "reason": "video_range_invalid",
                    }
                )
                continue
            by_resource.setdefault((stream, path), []).append(
                (float(start), float(end), episode.get("episode_index"))
            )
    collisions: list[dict[str, Any]] = []
    for (stream, path), ranges in sorted(by_resource.items()):
        ordered = sorted(ranges)
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] < previous[1] - 1e-9:
                collisions.append(
                    {
                        "video_key": stream,
                        "path": path,
                        "left_episode": previous[2],
                        "right_episode": current[2],
                        "left_range": [previous[0], previous[1]],
                        "right_range": [current[0], current[1]],
                    }
                )
    return collisions, malformed


def _collision_check(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "inputs": [],
        "cross_input_episode_index_overlap": [],
        "cross_input_path_overlap": [],
        "official_tool_reindexes_output": True,
    }
    status = "direct"
    index_sets: list[set[int]] = []
    for input_index, snapshot in enumerate(snapshots):
        episodes = _episode_values(snapshot)
        totals = snapshot.get("totals")
        expected_total = totals.get("episodes") if isinstance(totals, Mapping) else None
        if episodes is None:
            status = max(status, "unknown", key=lambda item: _DECISION_RANK[item])
            evidence["inputs"].append(
                {"input": input_index + 1, "reason": "episode_contract_missing"}
            )
            index_sets.append(set())
            continue
        indexes = [episode.get("episode_index") for episode in episodes]
        valid_indexes = [
            value
            for value in indexes
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        ]
        duplicate_indexes = sorted(
            {value for value in valid_indexes if valid_indexes.count(value) > 1}
        )
        contiguous = sorted(valid_indexes) == list(range(len(valid_indexes)))
        paths_by_group: dict[str, list[str]] = {}
        duplicate_paths: dict[str, list[str]] = {}
        for group in ("data", "media"):
            paths = [
                str(record.get("path"))
                for record in _inventory(snapshot, group)
                if isinstance(record.get("path"), str)
            ]
            paths_by_group[group] = paths
            duplicates = sorted({path for path in paths if paths.count(path) > 1})
            if duplicates:
                duplicate_paths[group] = duplicates
        range_collisions, malformed_ranges = _range_collisions(episodes)
        input_evidence = {
            "input": input_index + 1,
            "episode_indexes": indexes,
            "expected_total": expected_total,
            "contiguous": contiguous,
            "duplicate_indexes": duplicate_indexes,
            "duplicate_paths": duplicate_paths,
            "video_range_collisions": range_collisions,
            "malformed_video_ranges": malformed_ranges,
        }
        evidence["inputs"].append(input_evidence)
        index_sets.append(set(valid_indexes))
        if (
            len(valid_indexes) != len(indexes)
            or duplicate_indexes
            or not contiguous
            or duplicate_paths
            or range_collisions
            or (isinstance(expected_total, int) and expected_total != len(episodes))
        ):
            status = "incompatible"
        elif malformed_ranges:
            status = max(status, "unknown", key=lambda item: _DECISION_RANK[item])
    for left in range(len(index_sets)):
        for right in range(left + 1, len(index_sets)):
            shared = sorted(index_sets[left] & index_sets[right])
            if shared:
                evidence["cross_input_episode_index_overlap"].append(
                    {
                        "left_input": left + 1,
                        "right_input": right + 1,
                        "indexes": shared,
                    }
                )
    for group in ("data", "media"):
        path_sets = [
            {
                str(record["path"])
                for record in _inventory(snapshot, group)
                if isinstance(record.get("path"), str)
            }
            for snapshot in snapshots
        ]
        for left in range(len(path_sets)):
            for right in range(left + 1, len(path_sets)):
                shared_paths = sorted(path_sets[left] & path_sets[right])
                if shared_paths:
                    evidence["cross_input_path_overlap"].append(
                        {
                            "group": group,
                            "left_input": left + 1,
                            "right_input": right + 1,
                            "paths": shared_paths,
                        }
                    )
    summaries = {
        "direct": "Input indexes and ranges are internally valid; the official tool will reindex.",
        "incompatible": "An input has duplicate, gapped, or colliding indexes/ranges.",
        "unknown": "Index, shard, or video-range evidence is incomplete.",
        "transform_required": "Index transforms are required.",
    }
    return _check(
        "index_shard_video_collisions",
        status,
        summaries[status],
        evidence,
    )


def _license_value(snapshot: Mapping[str, Any]) -> Any:
    metadata = _format_metadata(snapshot)
    if "license" in metadata:
        return _json_value(metadata["license"])
    for container_key in ("card_data", "dataset_card", "provenance"):
        container = metadata.get(container_key)
        if isinstance(container, Mapping) and "license" in container:
            return _json_value(container["license"])
    return None


def _publication_profile(profile: str) -> bool:
    normalized = profile.lower()
    return "publish" in normalized or "publication" in normalized


def _license_check(
    snapshots: Sequence[Mapping[str, Any]],
    profile: str,
) -> dict[str, Any]:
    licenses = [_license_value(snapshot) for snapshot in snapshots]
    evidence = {"profile": profile, "licenses": licenses}
    if not _publication_profile(profile):
        return _check(
            "license",
            "direct",
            "The selected non-publication profile does not assert license compatibility.",
            evidence,
        )
    if any(value is None or value == "" for value in licenses):
        return _check(
            "license",
            "unknown",
            "Publication license evidence is incomplete.",
            evidence,
        )
    normalized = [str(value).strip().lower() for value in licenses]
    if any(marker in value for value in normalized for marker in _RESTRICTIVE_LICENSE_MARKERS):
        return _check(
            "license",
            "incompatible",
            "At least one source forbids the selected publication workflow.",
            evidence,
        )
    if len(set(normalized)) != 1:
        return _check(
            "license",
            "unknown",
            "Mixed licenses require a publication-specific legal determination.",
            evidence,
        )
    return _check(
        "license",
        "direct",
        "All inputs declare the same publication license.",
        evidence,
    )


def _lineage_check(snapshots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sources = [snapshot.get("source") for snapshot in snapshots]
    evidence: dict[str, Any] = {"sources": sources}
    if any(not isinstance(source, Mapping) for source in sources):
        return _check(
            "source_lineage",
            "unknown",
            "At least one source lineage record is unavailable.",
            evidence,
        )
    normalized_sources = [source for source in sources if isinstance(source, Mapping)]
    for source in normalized_sources:
        kind = source.get("kind")
        locator = source.get("locator")
        if kind not in {"local", "hf_hub"} or not isinstance(locator, str) or not locator:
            return _check(
                "source_lineage",
                "unknown",
                "A source lineage record is incomplete.",
                evidence,
            )
        if kind == "hf_hub" and not source.get("resolved_revision"):
            return _check(
                "source_lineage",
                "unknown",
                "A Hub source is not pinned to a resolved revision.",
                evidence,
            )
    stable_ids = [
        (
            source.get("kind"),
            source.get("locator"),
            source.get("resolved_revision"),
        )
        for source in normalized_sources
        if source.get("locator") != "dataset://."
    ]
    duplicates = sorted(
        {
            json.dumps(value, separators=(",", ":"), ensure_ascii=False)
            for value in stable_ids
            if stable_ids.count(value) > 1
        }
    )
    evidence["duplicate_source_identities"] = duplicates
    if duplicates:
        return _check(
            "source_lineage",
            "incompatible",
            "The same pinned source revision was supplied more than once.",
            evidence,
        )
    return _check(
        "source_lineage",
        "direct",
        "Input source identities are distinct and traceable by snapshot fingerprint.",
        evidence,
    )


def _operation_input_id(snapshot: Mapping[str, Any], ordinal: int) -> str:
    source = snapshot.get("source")
    if isinstance(source, Mapping) and source.get("kind") == "hf_hub":
        locator = source.get("locator")
        if isinstance(locator, str) and locator.startswith("hf://datasets/"):
            return locator[len("hf://datasets/") :]
    return f"OPENBOT_INPUT_{ordinal:03d}"


def _official_operation(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    preconditions_satisfied: bool,
) -> dict[str, Any]:
    repo_ids = [
        _operation_input_id(snapshot, ordinal)
        for ordinal, snapshot in enumerate(snapshots, start=1)
    ]
    input_roots = [
        f"OPENBOT_INPUT_ROOT_{ordinal:03d}"
        for ordinal in range(1, len(snapshots) + 1)
    ]
    return {
        "tool": PINNED_LEROBOT_TOOL,
        "package": PINNED_LEROBOT_PACKAGE,
        "operation": "merge",
        "command": [
            "uvx",
            "--from",
            PINNED_LEROBOT_REQUIREMENT,
            PINNED_LEROBOT_TOOL,
            "--new_repo_id",
            "OPENBOT_MERGED_OUTPUT",
            "--new_root",
            "OPENBOT_MERGED_ROOT",
            "--operation.type",
            "merge",
            "--operation.repo_ids",
            json.dumps(repo_ids, separators=(",", ":"), ensure_ascii=False),
            "--operation.roots",
            json.dumps(input_roots, separators=(",", ":"), ensure_ascii=False),
            "--push_to_hub",
            "false",
        ],
        "input_ids": repo_ids,
        "output_id": "OPENBOT_MERGED_OUTPUT",
        "preconditions_satisfied": preconditions_satisfied,
        "will_execute": False,
    }


def _input_summary(snapshot: Mapping[str, Any], ordinal: int) -> dict[str, Any]:
    format_contract = snapshot.get("format")
    totals = snapshot.get("totals")
    return {
        "ordinal": ordinal,
        "snapshot_fingerprint": snapshot["snapshot_fingerprint"],
        "source": snapshot.get("source"),
        "format": {
            "input_format": (
                format_contract.get("input_format")
                if isinstance(format_contract, Mapping)
                else None
            ),
            "dataset_format_version": (
                format_contract.get("dataset_format_version")
                if isinstance(format_contract, Mapping)
                else None
            ),
            "adapter": (
                format_contract.get("adapter") if isinstance(format_contract, Mapping) else None
            ),
        },
        "totals": totals if isinstance(totals, Mapping) else {},
    }


def check_merge_compatibility(
    inputs: Sequence[SnapshotInput],
    *,
    profile: str = "lerobot-act",
    task_remap: Optional[Mapping[str, str]] = None,
    snapshot_builder: SnapshotBuilder = build_dataset_snapshot,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a deterministic read-only compatibility plan for an official merge."""
    if isinstance(inputs, (str, bytes, Path, Mapping)):
        raise DatasetArgumentError("inputs must be a sequence of snapshots or datasets")
    if not isinstance(profile, str):
        raise DatasetArgumentError("profile must be a string")
    normalized_profile = profile.strip()
    if not normalized_profile:
        raise DatasetArgumentError("profile must not be empty")
    if len(inputs) < 2:
        raise DatasetArgumentError("At least two merge inputs are required")
    normalized_remap: dict[str, str] = {}
    for key, value in sorted(
        (task_remap or {}).items(),
        key=lambda item: str(item[0]),
    ):
        if not isinstance(key, str) or not key or not isinstance(value, str) or not value:
            raise DatasetArgumentError("task_remap keys and values must be non-empty strings")
        normalized_remap[key] = value
    snapshots = [
        _resolve_snapshot(
            value,
            snapshot_builder=snapshot_builder,
            local_integrity="full",
        )[0]
        for value in inputs
    ]
    checks = [
        _format_check(snapshots),
        _feature_check(snapshots),
        _action_state_check(snapshots),
        _media_check(snapshots),
        _task_check(snapshots, normalized_remap),
        _embodiment_check(snapshots),
        _duplicate_check(snapshots),
        _collision_check(snapshots),
        _license_check(snapshots, normalized_profile),
        _lineage_check(snapshots),
    ]
    decision = _decision(checks)
    counts = {
        status: sum(item["status"] == status for item in checks)
        for status in ("direct", "transform_required", "incompatible", "unknown")
    }
    fingerprint_payload = {
        "schema_version": MERGE_PLAN_SCHEMA_VERSION,
        "fingerprint_version": MERGE_PLAN_FINGERPRINT_VERSION,
        "profile": normalized_profile,
        "decision": decision,
        "summary": {"checks": len(checks), **counts},
        "inputs": [
            _input_summary(snapshot, ordinal) for ordinal, snapshot in enumerate(snapshots, start=1)
        ],
        "task_remap": normalized_remap,
        "checks": checks,
        "official_operation": _official_operation(
            snapshots,
            preconditions_satisfied=decision == "direct",
        ),
    }
    result = {
        **fingerprint_payload,
        "tool": {"name": "openbot-data", "version": __version__},
        "plan_fingerprint": dataset_fingerprint(fingerprint_payload),
    }
    if output_path is not None:
        write_json_atomic(Path(output_path), result)
    return result


def _verification_check(
    name: str,
    status: str,
    summary: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if status not in {"passed", "failed", "unverified"}:
        raise ValueError(f"Unsupported verification status: {status}")
    return {
        "check": name,
        "status": status,
        "summary": summary,
        "evidence": _json_value(evidence),
    }


def _post_snapshot_check(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    coverage = snapshot.get("coverage")
    integrity = coverage.get("requested_integrity") if isinstance(coverage, Mapping) else None
    checksum = coverage.get("checksum") if isinstance(coverage, Mapping) else None
    completed = coverage.get("completed_capabilities") if isinstance(coverage, Mapping) else []
    evidence = {
        "requested_integrity": integrity,
        "checksum": checksum,
        "completed_capabilities": completed,
        "snapshot_fingerprint": snapshot["snapshot_fingerprint"],
    }
    if (
        integrity == "full"
        and checksum == "sha256"
        and isinstance(completed, list)
        and "content.checksum" in completed
    ):
        return _verification_check(
            "post_snapshot",
            "passed",
            "The merged output has a full SHA-256 snapshot.",
            evidence,
        )
    return _verification_check(
        "post_snapshot",
        "unverified",
        "The merged output snapshot does not prove full integrity coverage.",
        evidence,
    )


def _audit_error_count(report: Mapping[str, Any]) -> int:
    counts: list[int] = []
    findings = report.get("findings")
    if isinstance(findings, list):
        counts.append(
            sum(isinstance(item, Mapping) and item.get("severity") == "error" for item in findings)
        )
    summary = report.get("summary")
    if isinstance(summary, Mapping):
        value = summary.get("error", summary.get("errors"))
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            counts.append(value)
    return max(counts, default=0)


def _run_post_audit(
    merged_input: SnapshotInput,
    local_path: Optional[Path],
    runner: Optional[AuditRunner],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result: dict[str, Any]
    if runner is None:
        result = {
            "status": "unverified",
            "requested_integrity": "full",
            "report": None,
        }
        return result, _verification_check(
            "full_post_audit",
            "unverified",
            "No full-audit runner was supplied.",
            result,
        )
    if local_path is None and runner is audit_dataset:
        result = {
            "status": "unverified",
            "requested_integrity": "full",
            "report": None,
        }
        return result, _verification_check(
            "full_post_audit",
            "unverified",
            "A snapshot artifact alone cannot run the default full audit.",
            result,
        )
    target: Any = str(local_path) if local_path is not None else merged_input
    try:
        report = runner(
            target,
            checksum="sha256",
            integrity="full",
        )
        normalized = dict(_json_value(report))
    except Exception as exc:  # runner failures belong in the receipt
        result = {
            "status": "failed",
            "requested_integrity": "full",
            "report": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
        return result, _verification_check(
            "full_post_audit",
            "failed",
            "The full post-merge audit runner failed.",
            result,
        )
    errors = _audit_error_count(normalized)
    skipped = normalized.get("skipped_checks")
    findings = normalized.get("findings")
    findings_complete = isinstance(findings, list) and all(
        isinstance(item, Mapping) for item in findings
    )
    skipped_complete = skipped is None or isinstance(skipped, list)
    evidence_complete = (
        normalized.get("schema_version") == "openbot.dataset_audit.v1"
        and isinstance(normalized.get("summary"), Mapping)
        and findings_complete
        and skipped_complete
    )
    if not evidence_complete:
        status = "unverified"
        summary = "The full post-merge audit report is structurally incomplete."
    elif errors:
        status = "failed"
        summary = "The full post-merge audit reported errors."
    elif isinstance(skipped, list) and skipped:
        status = "unverified"
        summary = "The full post-merge audit skipped required checks."
    else:
        status = "passed"
        summary = "The full post-merge audit completed without errors."
    result = {
        "status": status,
        "requested_integrity": "full",
        "report": normalized,
    }
    return result, _verification_check(
        "full_post_audit",
        status,
        summary,
        {
            "evidence_complete": evidence_complete,
            "error_count": errors,
            "skipped_checks": skipped or [],
        },
    )


def _loader_status(value: Any, expected_episodes: object) -> tuple[str, dict[str, Any]]:
    normalized = _json_value(value)
    if isinstance(normalized, bool):
        return ("passed" if normalized else "failed"), {"result": normalized}
    if not isinstance(normalized, Mapping):
        return "failed", {"result": normalized}
    raw_status = str(normalized.get("status", "")).lower()
    if raw_status == "unavailable":
        return "unverified", dict(normalized)
    if "ok" in normalized and isinstance(normalized["ok"], bool):
        passed = bool(normalized["ok"])
    else:
        passed = raw_status in {"ok", "passed", "success", "succeeded"}
    loaded_episodes = normalized.get(
        "loaded_episodes",
        normalized.get("episodes"),
    )
    if (
        passed
        and isinstance(expected_episodes, int)
        and isinstance(loaded_episodes, int)
        and loaded_episodes != expected_episodes
    ):
        passed = False
    return ("passed" if passed else "failed"), dict(normalized)


def _run_loader(
    merged_input: SnapshotInput,
    local_path: Optional[Path],
    runner: Optional[LoaderRunner],
    expected_episodes: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if runner is None:
        result: dict[str, Any] = {
            "status": "unverified",
            "evidence": None,
        }
        return result, _verification_check(
            "loader_smoke",
            "unverified",
            "No official LeRobot loader runner was supplied.",
            result,
        )
    target: Any = str(local_path) if local_path is not None else merged_input
    try:
        raw_result = runner(target)
        status, evidence = _loader_status(raw_result, expected_episodes)
    except Exception as exc:  # runner failures belong in the receipt
        status = "failed"
        evidence = {"error": f"{type(exc).__name__}: {exc}"}
    result = {"status": status, "evidence": evidence}
    return result, _verification_check(
        "loader_smoke",
        status,
        (
            "The official LeRobot loader smoke test passed."
            if status == "passed"
            else (
                "The pinned official LeRobot loader is unavailable."
                if status == "unverified"
                else "The official LeRobot loader smoke test failed."
            )
        ),
        evidence,
    )


def _operation_and_lineage_check(
    operation_record: Optional[Mapping[str, Any]],
    input_fingerprints: Sequence[str],
    output_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    lineage: dict[str, Any]
    expected = {
        "tool": PINNED_LEROBOT_TOOL,
        "package": PINNED_LEROBOT_PACKAGE,
        "operation": "merge",
        "input_snapshot_fingerprints": list(input_fingerprints),
        "output_snapshot_fingerprint": output_fingerprint,
    }
    if operation_record is None:
        lineage = {
            "status": "unverified",
            "expected": expected,
            "recorded": None,
        }
        return lineage, _verification_check(
            "operation_and_lineage",
            "unverified",
            "No pinned official operation record was supplied.",
            lineage,
        )
    recorded = dict(_json_value(operation_record))
    mismatches: list[str] = []
    for key in ("tool", "package", "operation"):
        if recorded.get(key) != expected[key]:
            mismatches.append(key)
    if recorded.get("input_snapshot_fingerprints") != list(input_fingerprints):
        mismatches.append("input_snapshot_fingerprints")
    if recorded.get("output_snapshot_fingerprint") != output_fingerprint:
        mismatches.append("output_snapshot_fingerprint")
    exit_code = recorded.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code != 0:
        mismatches.append("exit_code")
    command = recorded.get("command")
    if (
        not isinstance(command, list)
        or not all(isinstance(item, str) for item in command)
        or PINNED_LEROBOT_TOOL not in command
        or "--new_repo_id" not in command
        or "--new_root" not in command
        or "--operation.type" not in command
        or "merge" not in command
        or "--operation.repo_ids" not in command
        or "--operation.roots" not in command
        or "--repo_id" in command
    ):
        mismatches.append("command")
    lineage_status = "failed" if mismatches else "passed"
    lineage = {
        "status": lineage_status,
        "expected": expected,
        "recorded": recorded,
        "mismatches": sorted(set(mismatches)),
    }
    return lineage, _verification_check(
        "operation_and_lineage",
        lineage_status,
        (
            "The official operation record reconciles input and output lineage."
            if not mismatches
            else "The official operation record does not reconcile lineage."
        ),
        {"mismatches": sorted(set(mismatches))},
    )


def _set_by_key(values: object, key: str) -> Optional[dict[Any, Mapping[str, Any]]]:
    if not isinstance(values, list):
        return None
    result: dict[Any, Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, Mapping) or key not in value or value[key] in result:
            return None
        result[value[key]] = value
    return result


def _episode_merge_semantics(episode: Mapping[str, Any]) -> dict[str, Any]:
    raw_tasks = episode.get("tasks")
    raw_segments = episode.get("video_segments")
    raw_files = episode.get("video_files")
    tasks = (
        sorted(item for item in raw_tasks if isinstance(item, str))
        if isinstance(raw_tasks, list)
        else []
    )
    video_keys = (
        sorted(
            {
                segment["video_key"]
                for segment in raw_segments
                if isinstance(segment, Mapping) and isinstance(segment.get("video_key"), str)
            }
        )
        if isinstance(raw_segments, list)
        else []
    )
    return {
        "length": episode.get("length"),
        "tasks": tasks,
        "video_keys": video_keys,
        "video_file_count": len(raw_files) if isinstance(raw_files, list) else None,
    }


def _semantic_reconciliation(
    inputs: Sequence[Mapping[str, Any]],
    merged: Mapping[str, Any],
) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    first_contract = inputs[0].get("contract")
    merged_contract = merged.get("contract")
    if not isinstance(first_contract, Mapping) or not isinstance(merged_contract, Mapping):
        return _verification_check(
            "semantic_reconciliation",
            "unverified",
            "Feature/task/episode contracts are unavailable.",
            {"failures": [{"kind": "contract_missing"}]},
        )
    expected_features = first_contract.get("features")
    if merged_contract.get("features") != expected_features:
        failures.append({"kind": "features"})
    format_fields = (
        "input_format",
        "adapter",
        "dataset_format_version",
        "compatibility_target",
    )
    expected_format = inputs[0].get("format")
    merged_format = merged.get("format")
    if not isinstance(expected_format, Mapping) or not isinstance(
        merged_format,
        Mapping,
    ):
        return _verification_check(
            "semantic_reconciliation",
            "unverified",
            "Dataset format contracts are unavailable.",
            {"failures": failures + [{"kind": "format_missing"}]},
        )
    format_mismatches = [
        field for field in format_fields if merged_format.get(field) != expected_format.get(field)
    ]
    if format_mismatches:
        failures.append(
            {
                "kind": "format",
                "fields": format_mismatches,
            }
        )
    expected_tasks = sorted(
        {
            task["task"]
            for snapshot in inputs
            for task in (
                snapshot.get("contract", {}).get("tasks", [])
                if isinstance(snapshot.get("contract"), Mapping)
                else []
            )
            if isinstance(task, Mapping) and isinstance(task.get("task"), str)
        }
    )
    actual_tasks = sorted(
        task["task"]
        for task in merged_contract.get("tasks", [])
        if isinstance(task, Mapping) and isinstance(task.get("task"), str)
    )
    if actual_tasks != expected_tasks:
        failures.append(
            {
                "kind": "tasks",
                "expected": expected_tasks,
                "actual": actual_tasks,
            }
        )
    input_episode_lists = [_episode_values(snapshot) for snapshot in inputs]
    merged_episodes = _episode_values(merged)
    if any(value is None for value in input_episode_lists) or merged_episodes is None:
        return _verification_check(
            "semantic_reconciliation",
            "unverified",
            "Episode contracts are unavailable.",
            {"failures": failures + [{"kind": "episodes_missing"}]},
        )
    expected_episode_semantics = [
        _episode_merge_semantics(episode)
        for episodes in input_episode_lists
        for episode in (episodes or [])
    ]
    def episode_sort_index(episode: Mapping[str, Any]) -> int:
        value = episode.get("episode_index")
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool)
            else -1
        )

    ordered_merged_episodes = sorted(
        merged_episodes,
        key=episode_sort_index,
    )
    actual_indexes = [episode.get("episode_index") for episode in ordered_merged_episodes]
    actual_episode_semantics = [
        _episode_merge_semantics(episode) for episode in ordered_merged_episodes
    ]
    if actual_indexes != list(range(len(ordered_merged_episodes))):
        failures.append(
            {
                "kind": "episode_reindex",
                "expected": list(range(len(ordered_merged_episodes))),
                "actual": actual_indexes,
            }
        )
    if actual_episode_semantics != expected_episode_semantics:
        failures.append(
            {
                "kind": "episode_semantics",
                "expected": expected_episode_semantics,
                "actual": actual_episode_semantics,
            }
        )
    input_totals: list[Mapping[str, Any]] = []
    for snapshot in inputs:
        totals = snapshot.get("totals")
        if isinstance(totals, Mapping):
            input_totals.append(totals)
    merged_totals = merged.get("totals")
    if len(input_totals) != len(inputs) or not isinstance(merged_totals, Mapping):
        return _verification_check(
            "semantic_reconciliation",
            "unverified",
            "Dataset totals are unavailable.",
            {"failures": failures + [{"kind": "totals_missing"}]},
        )
    for field in ("episodes", "frames"):
        expected_values = [totals.get(field) for totals in input_totals]
        integer_values = [
            value
            for value in expected_values
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        if len(integer_values) == len(expected_values):
            expected_total = sum(integer_values)
            if merged_totals.get(field) != expected_total:
                failures.append(
                    {
                        "kind": f"{field}_total",
                        "expected": expected_total,
                        "actual": merged_totals.get(field),
                    }
                )
        else:
            return _verification_check(
                "semantic_reconciliation",
                "unverified",
                f"Input {field} totals are incomplete.",
                {"failures": failures + [{"kind": f"{field}_totals_missing"}]},
            )
    input_stream_maps = [
        _set_by_key(
            (
                snapshot.get("contract", {}).get("video_streams")
                if isinstance(snapshot.get("contract"), Mapping)
                else None
            ),
            "key",
        )
        for snapshot in inputs
    ]
    merged_stream_map = _set_by_key(merged_contract.get("video_streams"), "key")
    if any(value is None for value in input_stream_maps) or merged_stream_map is None:
        return _verification_check(
            "semantic_reconciliation",
            "unverified",
            "Video stream contracts are unavailable.",
            {"failures": failures + [{"kind": "video_stream_contract_missing"}]},
        )
    expected_streams = sorted(
        {key for stream_map in input_stream_maps for key in (stream_map or {})}
    )
    actual_streams = sorted(merged_stream_map)
    if actual_streams != expected_streams:
        failures.append(
            {
                "kind": "video_streams",
                "expected": expected_streams,
                "actual": actual_streams,
            }
        )
    for key in sorted(set(expected_streams) & set(actual_streams)):
        source_streams = [
            stream_map[key]
            for stream_map in input_stream_maps
            if stream_map is not None and key in stream_map
        ]
        merged_stream = merged_stream_map[key]
        for field in ("resolutions", "fps"):
            source_values = [stream.get(field) for stream in source_streams]
            if (
                not source_values
                or any(value != source_values[0] for value in source_values[1:])
                or merged_stream.get(field) != source_values[0]
            ):
                failures.append(
                    {
                        "kind": "video_stream_metadata",
                        "stream": key,
                        "field": field,
                        "inputs": source_values,
                        "actual": merged_stream.get(field),
                    }
                )
        frame_counts = [stream.get("frame_count") for stream in source_streams]
        integer_frame_counts = [
            value
            for value in frame_counts
            if isinstance(value, int) and not isinstance(value, bool)
        ]
        if len(integer_frame_counts) == len(frame_counts):
            expected_frames = sum(integer_frame_counts)
            if merged_stream.get("frame_count") != expected_frames:
                failures.append(
                    {
                        "kind": "video_stream_frame_count",
                        "stream": key,
                        "expected": expected_frames,
                        "actual": merged_stream.get("frame_count"),
                    }
                )
        else:
            return _verification_check(
                "semantic_reconciliation",
                "unverified",
                "Video stream frame-count evidence is incomplete.",
                {
                    "failures": failures
                    + [
                        {
                            "kind": "video_stream_frame_counts_missing",
                            "stream": key,
                        }
                    ]
                },
            )
    status = "failed" if failures else "passed"
    return _verification_check(
        "semantic_reconciliation",
        status,
        (
            "Merged features, tasks, streams, and totals reconcile."
            if not failures
            else "Merged semantic contracts or totals do not reconcile."
        ),
        {"failures": failures},
    )


def _run_diffs(
    inputs: Sequence[Mapping[str, Any]],
    merged: Mapping[str, Any],
    runner: Optional[DiffRunner],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if runner is None:
        return [], _verification_check(
            "semantic_diff",
            "unverified",
            "No semantic diff runner was supplied.",
            {"diffs": 0},
        )
    diffs: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    try:
        for ordinal, snapshot in enumerate(inputs, start=1):
            raw = runner(snapshot, merged)
            diff = dict(_json_value(raw))
            if diff.get("schema_version") != DIFF_SCHEMA_VERSION:
                failures.append({"input": ordinal, "kind": "diff_schema_version"})
            if diff.get("baseline_fingerprint") != snapshot["snapshot_fingerprint"]:
                failures.append({"input": ordinal, "kind": "diff_baseline_fingerprint"})
            if diff.get("candidate_fingerprint") != merged["snapshot_fingerprint"]:
                failures.append({"input": ordinal, "kind": "diff_candidate_fingerprint"})
            diff_fingerprint = diff.get("diff_fingerprint")
            if (
                not isinstance(diff_fingerprint, str)
                or _HEX_DIGEST.fullmatch(diff_fingerprint) is None
            ):
                failures.append({"input": ordinal, "kind": "diff_fingerprint"})
            else:
                expected_diff_fingerprint = dataset_fingerprint(
                    {
                        key: value
                        for key, value in diff.items()
                        if key not in {"diff_fingerprint", "tool"}
                    }
                )
                if diff_fingerprint != expected_diff_fingerprint:
                    failures.append(
                        {
                            "input": ordinal,
                            "kind": "diff_fingerprint_mismatch",
                        }
                    )
            changes = diff.get("changes")
            if not isinstance(changes, list):
                failures.append({"input": ordinal, "kind": "diff_changes"})
                changes = []
            for change in changes:
                if (
                    isinstance(change, Mapping)
                    and change.get("classification") == "breaking"
                    and change.get("component") in {"features", "format", "video_streams"}
                ):
                    failures.append(
                        {
                            "input": ordinal,
                            "kind": "breaking_regression",
                            "component": change.get("component"),
                            "path": change.get("path"),
                        }
                    )
            diffs.append(diff)
    except Exception as exc:
        return diffs, _verification_check(
            "semantic_diff",
            "failed",
            "Semantic diff generation failed.",
            {
                "diffs": len(diffs),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
    status = "failed" if failures else "passed"
    return diffs, _verification_check(
        "semantic_diff",
        status,
        (
            "Every input-to-output semantic diff contains no contract regression."
            if not failures
            else "An input-to-output semantic diff contains a contract regression."
        ),
        {"diffs": len(diffs), "failures": failures},
    )


def verify_dataset_merge(
    merged: SnapshotInput,
    *,
    input_snapshots: Sequence[SnapshotInput],
    profile: str = "lerobot-act",
    operation_record: Optional[Mapping[str, Any]] = None,
    snapshot_builder: SnapshotBuilder = build_dataset_snapshot,
    audit_runner: Optional[AuditRunner] = audit_dataset,
    diff_runner: Optional[DiffRunner] = diff_dataset_snapshots,
    loader_runner: Optional[LoaderRunner] = None,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Verify a completed official merge without ever executing the merge command."""
    if isinstance(input_snapshots, (str, bytes, Path, Mapping)):
        raise DatasetArgumentError("input_snapshots must be a sequence of snapshots or datasets")
    if len(input_snapshots) < 2:
        raise DatasetArgumentError("At least two input snapshots are required")
    inputs = [
        _resolve_snapshot(
            value,
            snapshot_builder=snapshot_builder,
            local_integrity="full",
        )[0]
        for value in input_snapshots
    ]
    post_snapshot, local_path = _resolve_snapshot(
        merged,
        snapshot_builder=snapshot_builder,
        local_integrity="full",
    )
    merge_plan = check_merge_compatibility(
        inputs,
        profile=profile,
        snapshot_builder=snapshot_builder,
    )
    if merge_plan["decision"] == "direct":
        premerge_check = _verification_check(
            "premerge_compatibility",
            "passed",
            "Input snapshots were directly compatible.",
            {"decision": merge_plan["decision"]},
        )
    elif merge_plan["decision"] == "unknown":
        premerge_check = _verification_check(
            "premerge_compatibility",
            "unverified",
            "Input compatibility remains unknown.",
            {"decision": merge_plan["decision"]},
        )
    else:
        premerge_check = _verification_check(
            "premerge_compatibility",
            "failed",
            "Input snapshots did not satisfy direct-merge preconditions.",
            {"decision": merge_plan["decision"]},
        )
    post_check = _post_snapshot_check(post_snapshot)
    post_audit, audit_check = _run_post_audit(
        merged,
        local_path,
        audit_runner,
    )
    expected_episodes = (
        post_snapshot.get("totals", {}).get("episodes")
        if isinstance(post_snapshot.get("totals"), Mapping)
        else None
    )
    loader_smoke, loader_check = _run_loader(
        merged,
        local_path,
        loader_runner,
        expected_episodes,
    )
    input_fingerprints = [str(snapshot["snapshot_fingerprint"]) for snapshot in inputs]
    lineage, lineage_check = _operation_and_lineage_check(
        operation_record,
        input_fingerprints,
        str(post_snapshot["snapshot_fingerprint"]),
    )
    reconciliation_check = _semantic_reconciliation(inputs, post_snapshot)
    semantic_diffs, diff_check = _run_diffs(
        inputs,
        post_snapshot,
        diff_runner,
    )
    checks = [
        premerge_check,
        post_check,
        audit_check,
        loader_check,
        lineage_check,
        reconciliation_check,
        diff_check,
    ]
    verification_status = (
        "verified" if all(check["status"] == "passed" for check in checks) else "unverified"
    )
    counts = {
        status: sum(item["status"] == status for item in checks)
        for status in ("passed", "failed", "unverified")
    }
    fingerprint_payload = {
        "schema_version": MERGE_RECEIPT_SCHEMA_VERSION,
        "fingerprint_version": MERGE_RECEIPT_FINGERPRINT_VERSION,
        "profile": profile.strip(),
        "verification_status": verification_status,
        "summary": {"checks": len(checks), **counts},
        "input_snapshots": inputs,
        "merge_plan": merge_plan,
        "official_operation": {
            "recorded": operation_record is not None,
            "record": (
                dict(_json_value(operation_record)) if operation_record is not None else None
            ),
        },
        "post_snapshot": post_snapshot,
        "post_audit": post_audit,
        "loader_smoke": loader_smoke,
        "lineage": lineage,
        "semantic_diffs": semantic_diffs,
        "checks": checks,
    }
    result = {
        **fingerprint_payload,
        "tool": {"name": "openbot-data", "version": __version__},
        "receipt_fingerprint": dataset_fingerprint(fingerprint_payload),
    }
    if output_path is not None:
        write_json_atomic(Path(output_path), result)
    return result


__all__ = [
    "MERGE_PLAN_SCHEMA_VERSION",
    "MERGE_RECEIPT_SCHEMA_VERSION",
    "check_merge_compatibility",
    "verify_dataset_merge",
]
