"""Profile-specific readiness gates built from one prepared dataset."""

from __future__ import annotations

import json
import math
import re
from fnmatch import fnmatchcase
from importlib import import_module, resources
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from openbot_data import __version__
from openbot_data.adapters.base import thaw_value
from openbot_data.errors import DatasetArgumentError
from openbot_data.models import DatasetSnapshot
from openbot_data.preflight import (
    audit_dataset,
    dataset_fingerprint,
    prepare_dataset,
)
from openbot_data.serialization import write_json_atomic
from openbot_data.snapshot import build_dataset_snapshot
from openbot_data.triage import analyze_advisory_signals, triage_findings

READINESS_SCHEMA_VERSION = "openbot.dataset_readiness.v1"
READINESS_FINGERPRINT_VERSION = "openbot.dataset_readiness.fingerprint.v1"
BUILTIN_PROFILE_FILES = {
    "lerobot-core": "lerobot-core-v1.json",
    "training-common": "training-common-v1.json",
    "lerobot-act": "lerobot-act-v1.json",
    "lerobot-smolvla": "lerobot-smolvla-v1.json",
    "hf-publication": "hf-publication-v1.json",
}
_INTEGRITY_RANK = {"metadata": 0, "sample": 1, "full": 2}
_IMMUTABLE_HF_REVISION = re.compile(r"^[0-9a-f]{40}$")
_POLICY_FIELDS = {
    "action_dim",
    "action_feature",
    "adapters",
    "advisory_metadata",
    "any_feature_groups",
    "camera_features",
    "dataset_versions",
    "delta_horizons",
    "delta_indices",
    "id",
    "input_features",
    "input_formats",
    "input_shapes",
    "language_features",
    "metadata_requirements",
    "name",
    "normalization",
    "normalization_mapping",
    "output_features",
    "output_shapes",
    "policy_defaults",
    "required_capabilities",
    "required_features",
    "required_integrity",
    "requires_hf_revision",
    "source_reference",
    "source_version",
    "state_features",
    "thresholds",
    "version",
}
_FEATURE_REQUIREMENT_FIELDS = {
    "delta_horizon",
    "delta_indices",
    "delta_timestamps",
    "dtype",
    "key",
    "normalization",
    "role",
    "shape",
    "shape_last_dimension",
    "shape_last_dimension_max",
    "shape_min_rank",
}
_FEATURE_ROLES = {
    "camera",
    "index",
    "input",
    "language",
    "output",
    "state",
    "unspecified",
}
_THRESHOLD_FIELDS = {
    "action_range",
    "action_range_source",
    "idle_action_span_frames",
    "idle_threshold_source",
    "near_zero_variance",
    "short_episode_seconds",
    "short_episode_threshold_source",
    "variance_threshold_source",
}
_POLICY_DEFAULT_FIELDS = {
    "action_delta_indices",
    "max_action_dim",
    "max_state_dim",
    "normalization_mapping",
    "observation_delta_indices",
}


def _load_json_object(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    path = Path(value)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetArgumentError(f"Policy config could not be read: {path}") from exc
    if not isinstance(loaded, dict):
        raise DatasetArgumentError("Policy config must be a JSON object")
    return loaded


def load_readiness_profile(profile: str) -> dict[str, Any]:
    """Load one immutable built-in profile by stable ID."""
    normalized = profile.strip().lower()
    filename = BUILTIN_PROFILE_FILES.get(normalized)
    if filename is None:
        raise DatasetArgumentError(
            f"Unknown readiness profile {profile!r}; use one of {sorted(BUILTIN_PROFILE_FILES)}"
        )
    try:
        text = (
            resources.files("openbot_data.profiles").joinpath(filename).read_text(encoding="utf-8")
        )
        loaded = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetArgumentError(
            f"Built-in readiness profile is unreadable: {normalized}"
        ) from exc
    if not isinstance(loaded, dict) or loaded.get("id") != normalized:
        raise DatasetArgumentError(f"Built-in readiness profile is invalid: {normalized}")
    _validate_policy_config(loaded)
    return loaded


def _policy_error(path: str, message: str) -> DatasetArgumentError:
    return DatasetArgumentError(f"Invalid policy config at {path}: {message}")


def _unknown_fields(
    value: Mapping[Any, Any],
    allowed: set[str],
    path: str,
) -> list[str]:
    keys: list[str] = []
    for key in value:
        if not isinstance(key, str):
            raise _policy_error(path, "object keys must be strings")
        keys.append(key)
    return sorted(key for key in keys if key not in allowed)


def _nonempty_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _policy_error(path, "must be a non-empty string")
    return value


def _string_list(
    value: object,
    path: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list):
        raise _policy_error(path, "must be a list of non-empty strings")
    result = []
    for index, item in enumerate(value):
        result.append(_nonempty_string(item, f"{path}/{index}"))
    if not allow_empty and not result:
        raise _policy_error(path, "must not be empty")
    if len(result) != len(set(result)):
        raise _policy_error(path, "must not contain duplicate values")
    return result


def _shape_contract(value: object, path: str) -> list[int]:
    if not isinstance(value, (list, tuple)):
        raise _policy_error(path, "must be a list of non-negative integers")
    result = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise _policy_error(
                f"{path}/{index}",
                "must be a non-negative integer",
            )
        result.append(item)
    return result


def _delta_contract(value: object, path: str) -> list[float | int]:
    if not isinstance(value, (list, tuple)):
        raise _policy_error(path, "must be a list of finite numbers")
    result: list[float | int] = []
    for index, item in enumerate(value):
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            raise _policy_error(f"{path}/{index}", "must be a finite number")
        result.append(item)
    return result


def _validate_feature_requirement(
    value: object,
    path: str,
    *,
    key_required: bool,
) -> None:
    if not isinstance(value, Mapping):
        raise _policy_error(path, "must be an object")
    unknown = _unknown_fields(value, _FEATURE_REQUIREMENT_FIELDS, path)
    if unknown:
        raise _policy_error(path, f"contains unknown fields: {unknown}")
    if key_required:
        _nonempty_string(value.get("key"), f"{path}/key")
    elif "key" in value:
        _nonempty_string(value["key"], f"{path}/key")
    if "role" in value:
        role = _nonempty_string(value["role"], f"{path}/role")
        if role not in _FEATURE_ROLES:
            raise _policy_error(
                f"{path}/role",
                f"must be one of {sorted(_FEATURE_ROLES)}",
            )
    for field_name in ("dtype", "normalization"):
        if field_name in value:
            _nonempty_string(value[field_name], f"{path}/{field_name}")
    if "shape" in value:
        _shape_contract(value["shape"], f"{path}/shape")
    for field_name in (
        "shape_last_dimension",
        "shape_last_dimension_max",
        "shape_min_rank",
    ):
        if field_name not in value:
            continue
        field_value = value[field_name]
        if isinstance(field_value, bool) or not isinstance(field_value, int) or field_value <= 0:
            raise _policy_error(
                f"{path}/{field_name}",
                "must be a positive integer",
            )
    delta_fields = [
        field_name
        for field_name in (
            "delta_horizon",
            "delta_indices",
            "delta_timestamps",
        )
        if field_name in value
    ]
    if len(delta_fields) > 1:
        raise _policy_error(
            path,
            "must declare at most one delta horizon field",
        )
    for field_name in delta_fields:
        _delta_contract(value[field_name], f"{path}/{field_name}")


def _validate_policy_metadata_requirements(
    value: object,
    path: str,
    *,
    expected_severity: str,
) -> None:
    if not isinstance(value, list):
        raise _policy_error(path, "must be a list of objects")
    seen = set()
    for index, item in enumerate(value):
        item_path = f"{path}/{index}"
        if not isinstance(item, Mapping):
            raise _policy_error(item_path, "must be an object")
        unknown = _unknown_fields(item, {"key", "severity"}, item_path)
        if unknown:
            raise _policy_error(item_path, f"contains unknown fields: {unknown}")
        key = _nonempty_string(item.get("key"), f"{item_path}/key")
        if key in seen:
            raise _policy_error(path, f"contains duplicate key {key!r}")
        seen.add(key)
        if "severity" in item and item["severity"] != expected_severity:
            raise _policy_error(
                f"{item_path}/severity",
                f"must equal {expected_severity!r}",
            )


def _validate_json_value(value: object, path: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise _policy_error(path, "must be finite")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise _policy_error(path, "object keys must be non-empty strings")
            _validate_json_value(item, f"{path}/{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}/{index}")
        return
    raise _policy_error(path, "must contain only JSON-compatible values")


def _validate_thresholds(value: object) -> None:
    path = "/thresholds"
    if not isinstance(value, Mapping):
        raise _policy_error(path, "must be an object")
    unknown = _unknown_fields(value, _THRESHOLD_FIELDS, path)
    if unknown:
        raise _policy_error(path, f"contains unknown fields: {unknown}")
    for field in (
        "near_zero_variance",
        "short_episode_seconds",
    ):
        if field not in value:
            continue
        item = value[field]
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            or item < 0
        ):
            raise _policy_error(
                f"{path}/{field}",
                "must be a finite non-negative number",
            )
    if "idle_action_span_frames" in value:
        item = value["idle_action_span_frames"]
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise _policy_error(
                f"{path}/idle_action_span_frames",
                "must be a positive integer",
            )
    if "action_range" in value:
        action_range = _delta_contract(
            value["action_range"],
            f"{path}/action_range",
        )
        if len(action_range) != 2 or action_range[0] >= action_range[1]:
            raise _policy_error(
                f"{path}/action_range",
                "must contain two increasing finite numbers",
            )
    for field in (
        "action_range_source",
        "idle_threshold_source",
        "short_episode_threshold_source",
        "variance_threshold_source",
    ):
        if field in value:
            _nonempty_string(value[field], f"{path}/{field}")


def _validate_policy_defaults(value: object) -> None:
    path = "/policy_defaults"
    if not isinstance(value, Mapping):
        raise _policy_error(path, "must be an object")
    unknown = _unknown_fields(value, _POLICY_DEFAULT_FIELDS, path)
    if unknown:
        raise _policy_error(path, f"contains unknown fields: {unknown}")
    normalization = value.get("normalization_mapping")
    if normalization is not None:
        if not isinstance(normalization, Mapping):
            raise _policy_error(
                f"{path}/normalization_mapping",
                "must be an object",
            )
        for key, item in normalization.items():
            _nonempty_string(key, f"{path}/normalization_mapping/<key>")
            _nonempty_string(
                item,
                f"{path}/normalization_mapping/{key}",
            )
    for field in ("action_delta_indices", "observation_delta_indices"):
        if field not in value or value[field] is None:
            continue
        item = value[field]
        item_path = f"{path}/{field}"
        if isinstance(item, Mapping):
            unknown_bounds = _unknown_fields(
                item,
                {"start", "stop"},
                item_path,
            )
            if unknown_bounds:
                raise _policy_error(
                    item_path,
                    f"contains unknown fields: {unknown_bounds}",
                )
            if set(item) != {"start", "stop"}:
                raise _policy_error(
                    item_path,
                    "must contain start and stop",
                )
            start, stop = item["start"], item["stop"]
            for key, bound in (("start", start), ("stop", stop)):
                if isinstance(bound, bool) or not isinstance(bound, int):
                    raise _policy_error(
                        f"{item_path}/{key}",
                        "must be an integer",
                    )
            if start >= stop:
                raise _policy_error(
                    item_path,
                    "start must be lower than stop",
                )
        else:
            _delta_contract(item, item_path)
    for field in ("max_action_dim", "max_state_dim"):
        if field not in value:
            continue
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise _policy_error(
                f"{path}/{field}",
                "must be a positive integer",
            )


def _validate_policy_config(config: Mapping[str, Any]) -> None:
    unknown = _unknown_fields(config, _POLICY_FIELDS, "/")
    if unknown:
        raise _policy_error("/", f"contains unknown fields: {unknown}")
    if "id" not in config and "name" not in config:
        raise _policy_error("/", "requires id or name")
    identifiers = [
        _nonempty_string(config[field], f"/{field}") for field in ("id", "name") if field in config
    ]
    if len(set(identifiers)) > 1:
        raise _policy_error("/", "id and name must match when both are present")
    for field in ("version", "source_version", "source_reference"):
        if field in config:
            _nonempty_string(config[field], f"/{field}")
    if "required_integrity" in config:
        value = _nonempty_string(
            config["required_integrity"],
            "/required_integrity",
        ).lower()
        if value not in _INTEGRITY_RANK:
            raise _policy_error(
                "/required_integrity",
                "must be metadata, sample, or full",
            )
    for field in (
        "input_formats",
        "adapters",
        "dataset_versions",
        "required_capabilities",
    ):
        if field in config:
            _string_list(
                config[field],
                f"/{field}",
                allow_empty=field == "required_capabilities",
            )

    raw_required = config.get("required_features")
    if raw_required is not None:
        if isinstance(raw_required, list):
            seen_required_features: set[str] = set()
            for index, item in enumerate(raw_required):
                if isinstance(item, str):
                    key = _nonempty_string(
                        item,
                        f"/required_features/{index}",
                    )
                else:
                    _validate_feature_requirement(
                        item,
                        f"/required_features/{index}",
                        key_required=True,
                    )
                    assert isinstance(item, Mapping)
                    key = str(item["key"])
                if key in seen_required_features:
                    raise _policy_error(
                        "/required_features",
                        f"contains duplicate key {key!r}",
                    )
                seen_required_features.add(key)
        elif isinstance(raw_required, Mapping):
            for key, item in raw_required.items():
                _nonempty_string(key, "/required_features/<key>")
                _validate_feature_requirement(
                    item,
                    f"/required_features/{key}",
                    key_required=False,
                )
        else:
            raise _policy_error(
                "/required_features",
                "must be a list or object",
            )

    for field in ("input_features", "output_features"):
        if field not in config:
            continue
        value = config[field]
        if not isinstance(value, Mapping):
            raise _policy_error(f"/{field}", "must be an object")
        for key, item in value.items():
            _nonempty_string(key, f"/{field}/<key>")
            _validate_feature_requirement(
                item,
                f"/{field}/{key}",
                key_required=False,
            )
    for field in ("input_shapes", "output_shapes"):
        if field not in config:
            continue
        value = config[field]
        if not isinstance(value, Mapping):
            raise _policy_error(f"/{field}", "must be an object")
        for key, item in value.items():
            _nonempty_string(key, f"/{field}/<key>")
            _shape_contract(item, f"/{field}/{key}")

    for field in ("camera_features", "language_features", "state_features"):
        if field not in config:
            continue
        value = config[field]
        if isinstance(value, str):
            _nonempty_string(value, f"/{field}")
        else:
            _string_list(value, f"/{field}")
    if "action_feature" in config:
        _nonempty_string(config["action_feature"], "/action_feature")
    if "action_dim" in config:
        value = config["action_dim"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise _policy_error("/action_dim", "must be a positive integer")
    if "normalization" in config:
        _nonempty_string(config["normalization"], "/normalization")
    if "normalization_mapping" in config:
        value = config["normalization_mapping"]
        if not isinstance(value, Mapping):
            raise _policy_error("/normalization_mapping", "must be an object")
        for key, item in value.items():
            _nonempty_string(key, "/normalization_mapping/<key>")
            _nonempty_string(item, f"/normalization_mapping/{key}")
    for field in ("delta_indices", "delta_horizons"):
        if field not in config:
            continue
        value = config[field]
        if not isinstance(value, Mapping):
            raise _policy_error(f"/{field}", "must be an object")
        for key, item in value.items():
            _nonempty_string(key, f"/{field}/<key>")
            _delta_contract(item, f"/{field}/{key}")

    if "any_feature_groups" in config:
        groups = config["any_feature_groups"]
        if not isinstance(groups, list):
            raise _policy_error("/any_feature_groups", "must be a list")
        seen_groups = set()
        for index, group in enumerate(groups):
            path = f"/any_feature_groups/{index}"
            if not isinstance(group, Mapping):
                raise _policy_error(path, "must be an object")
            unknown_group = _unknown_fields(
                group,
                {"id", "minimum", "patterns"},
                path,
            )
            if unknown_group:
                raise _policy_error(
                    path,
                    f"contains unknown fields: {unknown_group}",
                )
            group_id = _nonempty_string(group.get("id"), f"{path}/id")
            if group_id in seen_groups:
                raise _policy_error(
                    "/any_feature_groups",
                    f"contains duplicate id {group_id!r}",
                )
            seen_groups.add(group_id)
            _string_list(
                group.get("patterns"),
                f"{path}/patterns",
                allow_empty=False,
            )
            minimum = group.get("minimum", 1)
            if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum <= 0:
                raise _policy_error(
                    f"{path}/minimum",
                    "must be a positive integer",
                )

    if "metadata_requirements" in config:
        _validate_policy_metadata_requirements(
            config["metadata_requirements"],
            "/metadata_requirements",
            expected_severity="error",
        )
    if "advisory_metadata" in config:
        _validate_policy_metadata_requirements(
            config["advisory_metadata"],
            "/advisory_metadata",
            expected_severity="warning",
        )
    if "requires_hf_revision" in config and not isinstance(
        config["requires_hf_revision"],
        bool,
    ):
        raise _policy_error("/requires_hf_revision", "must be a boolean")
    if "thresholds" in config:
        _validate_thresholds(config["thresholds"])
    if "policy_defaults" in config:
        _validate_policy_defaults(config["policy_defaults"])


def _feature_requirement(key: str, raw: object, *, role: str) -> dict[str, Any]:
    requirement: dict[str, Any] = {"key": key, "role": role}
    if isinstance(raw, Mapping):
        dtype = raw.get("dtype")
        shape = raw.get("shape")
        if isinstance(dtype, str):
            requirement["dtype"] = dtype
        if isinstance(shape, Sequence) and not isinstance(
            shape,
            (str, bytes, bytearray),
        ):
            requirement["shape"] = list(shape)
        normalization = raw.get("normalization")
        if isinstance(normalization, str):
            requirement["normalization"] = normalization
        delta = raw.get("delta_indices", raw.get("delta_timestamps"))
        if isinstance(delta, Sequence) and not isinstance(
            delta,
            (str, bytes, bytearray),
        ):
            requirement["delta_horizon"] = list(delta)
        for field_name in (
            "shape_last_dimension",
            "shape_last_dimension_max",
            "shape_min_rank",
        ):
            if field_name in raw:
                requirement[field_name] = raw[field_name]
    return requirement


def _policy_profile(config: Mapping[str, Any]) -> dict[str, Any]:
    _validate_policy_config(config)
    required_features: list[dict[str, Any]] = []
    raw_required = config.get("required_features")
    if isinstance(raw_required, list):
        for item in raw_required:
            if isinstance(item, str):
                required_features.append({"key": item, "role": "unspecified"})
            elif isinstance(item, Mapping) and isinstance(item.get("key"), str):
                required_features.append(dict(item))
    elif isinstance(raw_required, Mapping):
        for key, raw in raw_required.items():
            required_features.append(_feature_requirement(str(key), raw, role="unspecified"))

    for field, role in (
        ("input_features", "input"),
        ("output_features", "output"),
        ("input_shapes", "input"),
        ("output_shapes", "output"),
    ):
        raw_features = config.get(field)
        if not isinstance(raw_features, Mapping):
            continue
        for key, raw in raw_features.items():
            normalized_raw = (
                {"shape": raw}
                if field.endswith("_shapes") and isinstance(raw, (list, tuple))
                else raw
            )
            required_features.append(_feature_requirement(str(key), normalized_raw, role=role))

    for field, role in (
        ("camera_features", "camera"),
        ("language_features", "language"),
        ("state_features", "state"),
    ):
        values = config.get(field)
        if isinstance(values, str):
            values = [values]
        if isinstance(values, list):
            for key in values:
                if isinstance(key, str):
                    required_features.append({"key": key, "role": role})

    action_key = str(config.get("action_feature", "action"))
    action_dim = config.get("action_dim")
    if isinstance(action_dim, int) and not isinstance(action_dim, bool):
        required_features.append(
            {
                "key": action_key,
                "role": "output",
                "shape_last_dimension": action_dim,
            }
        )

    normalization = config.get("normalization")
    if isinstance(normalization, str):
        required_features.append(
            {
                "key": action_key,
                "role": "output",
                "normalization": normalization,
            }
        )
    normalization_mapping = config.get("normalization_mapping")
    if isinstance(normalization_mapping, Mapping):
        for key, value in normalization_mapping.items():
            if isinstance(value, str):
                required_features.append(
                    {
                        "key": str(key),
                        "role": "unspecified",
                        "normalization": value,
                    }
                )

    delta_mapping = config.get("delta_indices", config.get("delta_horizons"))
    if isinstance(delta_mapping, Mapping):
        for key, value in delta_mapping.items():
            if isinstance(value, Sequence) and not isinstance(
                value,
                (str, bytes, bytearray),
            ):
                required_features.append(
                    {
                        "key": str(key),
                        "role": "unspecified",
                        "delta_horizon": list(value),
                    }
                )

    deduped: dict[str, dict[str, Any]] = {}
    for requirement in required_features:
        key = str(requirement["key"])
        current = deduped.setdefault(
            key,
            {"key": key, "role": requirement.get("role", "unspecified")},
        )
        current.update(requirement)

    required_capabilities = config.get(
        "required_capabilities",
        [
            "format.contract",
            "metadata.info",
            "metadata.episodes",
            "data.inventory",
            "data.parquet_footer",
            "alignment.data_relations",
        ],
    )
    if not isinstance(required_capabilities, list) or any(
        not isinstance(item, str) for item in required_capabilities
    ):
        raise DatasetArgumentError("policy required_capabilities must be a list of strings")
    required_integrity = str(config.get("required_integrity", "full")).lower()
    if required_integrity not in _INTEGRITY_RANK:
        raise DatasetArgumentError("policy required_integrity must be metadata, sample, or full")
    profile_id = config.get("id", config.get("name", "policy-config"))
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise DatasetArgumentError("policy id/name must be a non-empty string")
    return {
        "id": profile_id,
        "version": str(config.get("version", "user-declared")),
        "source_version": str(config.get("source_version", "caller-supplied-policy-config")),
        "source_reference": str(config.get("source_reference", "caller-supplied-policy-config")),
        "required_integrity": required_integrity,
        "input_formats": list(config.get("input_formats", ["lerobot"])),
        "adapters": list(config.get("adapters", ["lerobot_v30"])),
        "dataset_versions": list(config.get("dataset_versions", ["v3.0", "3.0"])),
        "required_capabilities": sorted(set(required_capabilities)),
        "required_features": [deduped[key] for key in sorted(deduped)],
        "any_feature_groups": list(config.get("any_feature_groups", [])),
        "metadata_requirements": list(config.get("metadata_requirements", [])),
        "advisory_metadata": list(config.get("advisory_metadata", [])),
        "requires_hf_revision": bool(config.get("requires_hf_revision", False)),
        "thresholds": dict(config.get("thresholds", {})),
        "declared_policy_contract": dict(config),
    }


def _readiness_finding(
    code: str,
    severity: str,
    message: str,
    *,
    location: Optional[Mapping[str, Any]] = None,
    evidence: Optional[Mapping[str, Any]] = None,
    impact: str,
    remediation_ref: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "layer": "schema",
        "message": message,
        "location": dict(location or {}),
        "evidence": dict(evidence or {}),
        "impact": impact,
        "fixability": "manual",
        "remediation_ref": remediation_ref,
    }


def _features(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    contract = snapshot.get("contract")
    raw = contract.get("features") if isinstance(contract, Mapping) else None
    if not isinstance(raw, list):
        return {}
    return {
        str(item["key"]): item
        for item in raw
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    }


def _feature_metadata(feature: Mapping[str, Any]) -> Mapping[str, Any]:
    value = feature.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _publication_contract(
    snapshot: Mapping[str, Any],
    profile: Mapping[str, Any],
    publication_metadata: Optional[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], str]:
    source = snapshot.get("source")
    source_kind = source.get("kind") if isinstance(source, Mapping) else None
    if (
        publication_metadata is not None
        and source_kind == "hf_hub"
        and (profile.get("id") == "hf-publication" or profile.get("requires_hf_revision") is True)
    ):
        return publication_metadata, "publication_metadata"
    format_contract = snapshot.get("format")
    format_metadata = (
        format_contract.get("metadata") if isinstance(format_contract, Mapping) else None
    )
    return (
        format_metadata if isinstance(format_metadata, Mapping) else {},
        "snapshot_format_metadata",
    )


def _contract_findings(
    snapshot: Mapping[str, Any],
    profile: Mapping[str, Any],
    publication_metadata: Optional[Mapping[str, Any]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    format_contract = snapshot.get("format")
    normalized_format = format_contract if isinstance(format_contract, Mapping) else {}
    input_format = normalized_format.get("input_format")
    adapter = normalized_format.get("adapter")
    version = normalized_format.get("dataset_format_version")
    if input_format not in profile.get("input_formats", []):
        blockers.append(
            _readiness_finding(
                "READINESS_FORMAT_INCOMPATIBLE",
                "error",
                "Dataset input format is not accepted by the selected profile.",
                location={"field": "format.input_format"},
                evidence={
                    "actual": input_format,
                    "expected": profile.get("input_formats", []),
                },
                impact="profile_loader_contract_is_incompatible",
                remediation_ref="openbot://remediation/READINESS_FORMAT_INCOMPATIBLE",
            )
        )
    if adapter not in profile.get("adapters", []):
        blockers.append(
            _readiness_finding(
                "READINESS_ADAPTER_INCOMPATIBLE",
                "error",
                "Dataset adapter is not accepted by the selected profile.",
                location={"field": "format.adapter"},
                evidence={
                    "actual": adapter,
                    "expected": profile.get("adapters", []),
                },
                impact="profile_format_contract_is_incompatible",
                remediation_ref="openbot://remediation/READINESS_ADAPTER_INCOMPATIBLE",
            )
        )
    expected_versions = profile.get("dataset_versions", [])
    if expected_versions and version not in expected_versions:
        blockers.append(
            _readiness_finding(
                "READINESS_DATASET_VERSION_INCOMPATIBLE",
                "error",
                "Dataset storage-contract version is incompatible with the profile.",
                location={"field": "format.dataset_format_version"},
                evidence={"actual": version, "expected": expected_versions},
                impact="profile_dataset_version_is_incompatible",
                remediation_ref=("openbot://remediation/READINESS_DATASET_VERSION_INCOMPATIBLE"),
            )
        )

    features = _features(snapshot)
    requirements = profile.get("required_features", [])
    if isinstance(requirements, list):
        for raw_requirement in requirements:
            if not isinstance(raw_requirement, Mapping):
                continue
            key = raw_requirement.get("key")
            if not isinstance(key, str):
                continue
            feature = features.get(key)
            if feature is None:
                blockers.append(
                    _readiness_finding(
                        "READINESS_FEATURE_MISSING",
                        "error",
                        "A policy-required dataset feature is missing.",
                        location={"feature_key": key},
                        evidence={"requirement": dict(raw_requirement)},
                        impact="policy_input_or_output_is_unavailable",
                        remediation_ref=("openbot://remediation/READINESS_FEATURE_MISSING"),
                    )
                )
                continue
            expected_dtype = raw_requirement.get("dtype")
            if isinstance(expected_dtype, str) and feature.get("dtype") != expected_dtype:
                blockers.append(
                    _readiness_finding(
                        "READINESS_FEATURE_DTYPE_MISMATCH",
                        "error",
                        "Feature dtype does not match the declared policy contract.",
                        location={"feature_key": key},
                        evidence={
                            "actual": feature.get("dtype"),
                            "expected": expected_dtype,
                        },
                        impact="policy_tensor_dtype_is_incompatible",
                        remediation_ref=("openbot://remediation/READINESS_FEATURE_DTYPE_MISMATCH"),
                    )
                )
            expected_shape = raw_requirement.get("shape")
            if isinstance(expected_shape, list) and feature.get("shape") != expected_shape:
                blockers.append(
                    _readiness_finding(
                        "READINESS_FEATURE_SHAPE_MISMATCH",
                        "error",
                        "Feature shape does not match the declared policy contract.",
                        location={"feature_key": key},
                        evidence={
                            "actual": feature.get("shape"),
                            "expected": expected_shape,
                        },
                        impact="policy_tensor_shape_is_incompatible",
                        remediation_ref=("openbot://remediation/READINESS_FEATURE_SHAPE_MISMATCH"),
                    )
                )
            expected_last = raw_requirement.get("shape_last_dimension")
            shape = feature.get("shape")
            actual_last = shape[-1] if isinstance(shape, list) and shape else None
            if isinstance(expected_last, int) and actual_last != expected_last:
                blockers.append(
                    _readiness_finding(
                        "READINESS_ACTION_DIMENSION_MISMATCH",
                        "error",
                        "Action dimension does not match the declared policy contract.",
                        location={"feature_key": key},
                        evidence={"actual": actual_last, "expected": expected_last},
                        impact="policy_action_tensor_is_incompatible",
                        remediation_ref=(
                            "openbot://remediation/READINESS_ACTION_DIMENSION_MISMATCH"
                        ),
                    )
                )
            minimum_rank = raw_requirement.get("shape_min_rank")
            if (
                isinstance(minimum_rank, int)
                and not isinstance(minimum_rank, bool)
                and (not isinstance(shape, list) or len(shape) < minimum_rank)
            ):
                blockers.append(
                    _readiness_finding(
                        "READINESS_FEATURE_SHAPE_MISMATCH",
                        "error",
                        "Feature rank is below the policy contract minimum.",
                        location={"feature_key": key},
                        evidence={
                            "actual": shape,
                            "constraint": "shape_min_rank",
                            "minimum": minimum_rank,
                        },
                        impact="policy_tensor_shape_is_incompatible",
                        remediation_ref=("openbot://remediation/READINESS_FEATURE_SHAPE_MISMATCH"),
                    )
                )
            maximum_last = raw_requirement.get("shape_last_dimension_max")
            if (
                isinstance(maximum_last, int)
                and not isinstance(maximum_last, bool)
                and (
                    not isinstance(actual_last, int)
                    or isinstance(actual_last, bool)
                    or actual_last > maximum_last
                )
            ):
                blockers.append(
                    _readiness_finding(
                        "READINESS_FEATURE_SHAPE_MISMATCH",
                        "error",
                        "Feature width exceeds the policy contract maximum.",
                        location={"feature_key": key},
                        evidence={
                            "actual": shape,
                            "constraint": "shape_last_dimension_max",
                            "maximum": maximum_last,
                        },
                        impact="policy_tensor_shape_is_incompatible",
                        remediation_ref=("openbot://remediation/READINESS_FEATURE_SHAPE_MISMATCH"),
                    )
                )
            metadata = _feature_metadata(feature)
            expected_normalization = raw_requirement.get("normalization")
            actual_normalization = metadata.get(
                "normalization",
                metadata.get("normalization_mode"),
            )
            if (
                isinstance(expected_normalization, str)
                and actual_normalization != expected_normalization
            ):
                blockers.append(
                    _readiness_finding(
                        "READINESS_NORMALIZATION_MISMATCH",
                        "error",
                        "Feature normalization does not match the policy contract.",
                        location={"feature_key": key},
                        evidence={
                            "actual": actual_normalization,
                            "expected": expected_normalization,
                        },
                        impact="policy_normalization_contract_is_incompatible",
                        remediation_ref=("openbot://remediation/READINESS_NORMALIZATION_MISMATCH"),
                    )
                )
            expected_delta = raw_requirement.get("delta_horizon")
            actual_delta = metadata.get(
                "delta_indices",
                metadata.get("delta_timestamps"),
            )
            if isinstance(expected_delta, list) and actual_delta != expected_delta:
                blockers.append(
                    _readiness_finding(
                        "READINESS_DELTA_HORIZON_MISMATCH",
                        "error",
                        "Feature delta horizon does not match the policy contract.",
                        location={"feature_key": key},
                        evidence={
                            "actual": actual_delta,
                            "expected": expected_delta,
                        },
                        impact="policy_temporal_contract_is_incompatible",
                        remediation_ref=("openbot://remediation/READINESS_DELTA_HORIZON_MISMATCH"),
                    )
                )

    groups = profile.get("any_feature_groups", [])
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            patterns = group.get("patterns", [])
            if not isinstance(patterns, list):
                continue
            matched = sorted(
                key
                for key in features
                if any(
                    isinstance(pattern, str) and fnmatchcase(key, pattern) for pattern in patterns
                )
            )
            minimum = group.get("minimum", 1)
            if (
                isinstance(minimum, int)
                and not isinstance(minimum, bool)
                and len(matched) < minimum
            ):
                blockers.append(
                    _readiness_finding(
                        "READINESS_FEATURE_GROUP_MISSING",
                        "error",
                        "Too few features satisfy a required policy feature group.",
                        location={"feature_group": group.get("id")},
                        evidence={
                            "patterns": patterns,
                            "minimum": minimum,
                            "matched": matched,
                        },
                        impact="policy_modality_contract_is_incomplete",
                        remediation_ref=("openbot://remediation/READINESS_FEATURE_GROUP_MISSING"),
                    )
                )

    normalized_metadata, _metadata_source = _publication_contract(
        snapshot,
        profile,
        publication_metadata,
    )
    for bucket, severity in (
        ("metadata_requirements", "error"),
        ("advisory_metadata", "warning"),
    ):
        values = profile.get(bucket, [])
        if not isinstance(values, list):
            continue
        for requirement in values:
            if not isinstance(requirement, Mapping):
                continue
            key = requirement.get("key")
            if not isinstance(key, str):
                continue
            value = normalized_metadata.get(key)
            if value is not None and value != "" and value != () and value != []:
                continue
            finding = _readiness_finding(
                "READINESS_METADATA_MISSING",
                severity,
                "Profile metadata is missing.",
                location={"metadata_key": key},
                evidence={"profile": profile.get("id")},
                impact=(
                    "publication_contract_is_incomplete"
                    if severity == "error"
                    else "publication_metadata_needs_review"
                ),
                remediation_ref="openbot://remediation/READINESS_METADATA_MISSING",
            )
            (blockers if severity == "error" else warnings).append(finding)

    source = snapshot.get("source")
    normalized_source = source if isinstance(source, Mapping) else {}
    revision = normalized_source.get("resolved_revision")
    source_is_hf = normalized_source.get("kind") == "hf_hub"
    if source_is_hf and (
        not isinstance(revision, str) or _IMMUTABLE_HF_REVISION.fullmatch(revision) is None
    ):
        blockers.append(
            _readiness_finding(
                "READINESS_HUB_REVISION_UNPINNED",
                "error",
                "Hub readiness requires an immutable 40-character commit SHA.",
                location={"field": "source.resolved_revision"},
                evidence={
                    "source_kind": normalized_source.get("kind"),
                    "resolved_revision": revision,
                },
                impact="publication_provenance_is_not_immutable",
                remediation_ref=("openbot://remediation/READINESS_HUB_REVISION_UNPINNED"),
            )
        )
    elif profile.get("requires_hf_revision"):
        if (
            not source_is_hf
            or not isinstance(revision, str)
            or _IMMUTABLE_HF_REVISION.fullmatch(revision) is None
        ):
            blockers.append(
                _readiness_finding(
                    "READINESS_HUB_REVISION_UNPINNED",
                    "error",
                    "Publication readiness requires an immutable Hub revision.",
                    location={"field": "source.resolved_revision"},
                    evidence={
                        "source_kind": normalized_source.get("kind"),
                        "resolved_revision": revision,
                    },
                    impact="publication_provenance_is_not_immutable",
                    remediation_ref=("openbot://remediation/READINESS_HUB_REVISION_UNPINNED"),
                )
            )
    return blockers, warnings


def _coverage(
    audit: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    profile: Mapping[str, Any],
    integrity: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_coverage = audit.get("coverage")
    capability_items = (
        raw_coverage.get("capabilities", []) if isinstance(raw_coverage, Mapping) else []
    )
    capabilities = {
        str(item.get("capability")): item
        for item in capability_items
        if isinstance(item, Mapping) and isinstance(item.get("capability"), str)
    }
    missing = []
    required = profile.get("required_capabilities", [])
    if isinstance(required, list):
        for capability in sorted(item for item in required if isinstance(item, str)):
            observed = capabilities.get(capability)
            if observed is None or observed.get("status") != "complete":
                missing.append(
                    {
                        "capability": capability,
                        "status": (
                            observed.get("status")
                            if isinstance(observed, Mapping)
                            else "not_produced"
                        ),
                        "reason_code": (
                            observed.get("reason_code")
                            if isinstance(observed, Mapping)
                            else "capability_not_produced"
                        ),
                    }
                )
    required_integrity = str(profile.get("required_integrity", "metadata"))
    if _INTEGRITY_RANK[integrity] < _INTEGRITY_RANK[required_integrity]:
        missing.append(
            {
                "capability": f"integrity.{required_integrity}",
                "status": "skipped",
                "reason_code": "integrity_too_low",
            }
        )
    snapshot_coverage = snapshot.get("coverage")
    normalized_snapshot_coverage = (
        snapshot_coverage if isinstance(snapshot_coverage, Mapping) else {}
    )
    return (
        {
            "requested_integrity": integrity,
            "required_integrity": required_integrity,
            "capabilities": [
                dict(item)
                for item in sorted(
                    (item for item in capability_items if isinstance(item, Mapping)),
                    key=lambda item: str(item.get("capability", "")),
                )
            ],
            "missing_capabilities": missing,
            "selection": dict(
                normalized_snapshot_coverage.get("selection", {})
                if isinstance(
                    normalized_snapshot_coverage.get("selection"),
                    Mapping,
                )
                else {}
            ),
            "totals": dict(
                normalized_snapshot_coverage.get("totals", {})
                if isinstance(
                    normalized_snapshot_coverage.get("totals"),
                    Mapping,
                )
                else {}
            ),
        },
        missing,
    )


def _finding_sort_key(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("code", "")),
        json.dumps(
            item.get("location", {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        json.dumps(
            item.get("evidence", {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _external_schema(
    artifact: Mapping[str, Any],
    *,
    label: str,
    filename: str,
) -> None:
    try:
        jsonschema = import_module("jsonschema")
    except ImportError as exc:
        raise DatasetArgumentError(
            "Validating external readiness artifacts requires jsonschema"
        ) from exc
    try:
        schema = json.loads(
            resources.files("openbot_data.schemas").joinpath(filename).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetArgumentError(f"Packaged {label} JSON Schema is unavailable") from exc
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(dict(artifact)),
        key=lambda error: (
            tuple(str(item) for item in error.absolute_path),
            error.message,
        ),
    )
    if not errors:
        return
    first = errors[0]
    pointer = "/" + "/".join(str(item) for item in first.absolute_path)
    raise DatasetArgumentError(f"External {label} failed JSON Schema at {pointer}: {first.message}")


def _snapshot_component_payloads(
    snapshot: Mapping[str, Any],
) -> Mapping[str, Any]:
    contract = snapshot["contract"]
    inventory = snapshot["inventory"]
    assert isinstance(contract, Mapping)
    assert isinstance(inventory, Mapping)
    return {
        "source": snapshot["source"],
        "format": snapshot["format"],
        "features": contract["features"],
        "tasks": contract["tasks"],
        "episodes": contract["episodes"],
        "video_streams": contract["video_streams"],
        "totals": snapshot["totals"],
        "metadata_inventory": inventory["metadata"],
        "data_inventory": inventory["data"],
        "media_inventory": inventory["media"],
        "coverage": snapshot["coverage"],
    }


def _validate_snapshot_fingerprints(snapshot: Mapping[str, Any]) -> None:
    components = _snapshot_component_payloads(snapshot)
    expected_components = {
        key: dataset_fingerprint(dict(value) if isinstance(value, Mapping) else value)
        for key, value in sorted(components.items())
    }
    actual_components = snapshot.get("component_fingerprints")
    if not isinstance(actual_components, Mapping):
        raise DatasetArgumentError("External dataset snapshot has no component_fingerprints object")
    if dict(actual_components) != expected_components:
        mismatched = sorted(
            key
            for key in set(actual_components) | set(expected_components)
            if actual_components.get(key) != expected_components.get(key)
        )
        raise DatasetArgumentError(
            f"External dataset snapshot component fingerprint mismatch: {mismatched}"
        )
    fingerprint_payload = {
        key: snapshot[key]
        for key in (
            "schema_version",
            "fingerprint_version",
            "source",
            "format",
            "contract",
            "inventory",
            "totals",
            "coverage",
            "component_fingerprints",
        )
    }
    expected = dataset_fingerprint(fingerprint_payload)
    if snapshot.get("snapshot_fingerprint") != expected:
        raise DatasetArgumentError(
            "External dataset snapshot fingerprint does not match its payload"
        )


def _validate_snapshot_internal_identity(snapshot: Mapping[str, Any]) -> None:
    contract = snapshot["contract"]
    inventory = snapshot["inventory"]
    totals = snapshot["totals"]
    coverage = snapshot["coverage"]
    assert isinstance(contract, Mapping)
    assert isinstance(inventory, Mapping)
    assert isinstance(totals, Mapping)
    assert isinstance(coverage, Mapping)
    expected_totals = {
        "episodes": len(contract["episodes"]),
        "tasks": len(contract["tasks"]),
        "features": len(contract["features"]),
        "video_streams": len(contract["video_streams"]),
        "metadata_shards": len(inventory["metadata"]),
        "data_shards": len(inventory["data"]),
        "media_shards": len(inventory["media"]),
    }
    mismatched_totals = sorted(
        key for key, value in expected_totals.items() if totals.get(key) != value
    )
    if mismatched_totals:
        raise DatasetArgumentError(
            "External dataset snapshot totals are inconsistent with its "
            f"contract/inventory: {mismatched_totals}"
        )
    coverage_totals = coverage.get("totals")
    if not isinstance(coverage_totals, Mapping):
        raise DatasetArgumentError("External dataset snapshot coverage totals are missing")
    expected_coverage_totals = {
        "episodes": expected_totals["episodes"],
        "cameras": expected_totals["video_streams"],
        "metadata_shards": expected_totals["metadata_shards"],
        "data_shards": expected_totals["data_shards"],
        "media_shards": expected_totals["media_shards"],
    }
    mismatched_coverage = sorted(
        key for key, value in expected_coverage_totals.items() if coverage_totals.get(key) != value
    )
    if mismatched_coverage:
        raise DatasetArgumentError(
            f"External dataset snapshot coverage totals are inconsistent: {mismatched_coverage}"
        )


def _validate_external_identity(
    snapshot: Mapping[str, Any],
    audit: Mapping[str, Any],
    integrity: str,
) -> None:
    snapshot_format = snapshot["format"]
    snapshot_coverage = snapshot["coverage"]
    snapshot_source = snapshot["source"]
    assert isinstance(snapshot_format, Mapping)
    assert isinstance(snapshot_coverage, Mapping)
    assert isinstance(snapshot_source, Mapping)
    if audit.get("input_format") != snapshot_format.get("input_format"):
        raise DatasetArgumentError(
            "External audit input_format does not match the dataset snapshot"
        )
    if snapshot_coverage.get("requested_integrity") != integrity:
        raise DatasetArgumentError(
            "External dataset snapshot integrity does not match the readiness request"
        )

    audit_coverage = audit.get("coverage")
    assert isinstance(audit_coverage, Mapping)
    capability_items = audit_coverage.get("capabilities")
    assert isinstance(capability_items, list)
    capability_names = [
        item.get("capability") for item in capability_items if isinstance(item, Mapping)
    ]
    if len(capability_names) != len(set(capability_names)):
        raise DatasetArgumentError("External audit contains duplicate capability identities")
    wrong_integrity = sorted(
        str(item.get("capability"))
        for item in capability_items
        if isinstance(item, Mapping) and item.get("integrity") != integrity
    )
    if wrong_integrity:
        raise DatasetArgumentError(
            "External audit capability integrity does not match the readiness "
            f"request: {wrong_integrity}"
        )

    findings = audit.get("findings")
    summary = audit.get("summary")
    assert isinstance(findings, list)
    assert isinstance(summary, Mapping)
    observed_counts = {
        severity: sum(
            isinstance(item, Mapping) and item.get("severity") == severity for item in findings
        )
        for severity in ("error", "warning", "info")
    }
    mismatched_summary = sorted(
        key for key, value in observed_counts.items() if summary.get(key) != value
    )
    if mismatched_summary:
        raise DatasetArgumentError(
            f"External audit summary is inconsistent with its findings: {mismatched_summary}"
        )
    snapshot_totals = snapshot.get("totals")
    assert isinstance(snapshot_totals, Mapping)
    if summary.get("videos") != snapshot_totals.get("media_shards"):
        raise DatasetArgumentError(
            "External audit video identity does not match snapshot media shards"
        )

    source_kind = snapshot_source.get("kind")
    audit_source = audit.get("source")
    if source_kind == "local":
        if audit_source is not None:
            raise DatasetArgumentError("External local snapshot cannot be paired with a Hub audit")
        if (
            snapshot_source.get("requested_revision") is not None
            or snapshot_source.get("resolved_revision") is not None
        ):
            raise DatasetArgumentError("External local snapshot cannot carry Hub revisions")
        return

    revision = snapshot_source.get("resolved_revision")
    if not isinstance(revision, str) or _IMMUTABLE_HF_REVISION.fullmatch(revision) is None:
        raise DatasetArgumentError(
            "External Hub snapshot resolved_revision must be an immutable "
            "40-character lowercase hex commit"
        )
    requested = snapshot_source.get("requested_revision")
    if (
        isinstance(requested, str)
        and _IMMUTABLE_HF_REVISION.fullmatch(requested) is not None
        and requested != revision
    ):
        raise DatasetArgumentError(
            "External Hub requested immutable revision does not match resolved_revision"
        )
    if not isinstance(audit_source, Mapping):
        raise DatasetArgumentError("External Hub snapshot must be paired with a Hub audit identity")
    identity_fields = (
        "kind",
        "locator",
        "requested_revision",
        "resolved_revision",
    )
    mismatched_identity = sorted(
        field for field in identity_fields if audit_source.get(field) != snapshot_source.get(field)
    )
    if mismatched_identity:
        raise DatasetArgumentError(
            f"External snapshot and audit source identity mismatch: {mismatched_identity}"
        )
    source_coverage = audit_coverage.get("source")
    if isinstance(source_coverage, Mapping) and (
        source_coverage.get("requested_integrity") != integrity
    ):
        raise DatasetArgumentError(
            "External Hub audit source coverage integrity does not match the readiness request"
        )


def _validate_external_artifacts(
    snapshot: Mapping[str, Any],
    audit: Mapping[str, Any],
    integrity: str,
) -> None:
    _external_schema(
        snapshot,
        label="dataset snapshot",
        filename="dataset-snapshot-v1.schema.json",
    )
    _external_schema(
        audit,
        label="dataset audit",
        filename="dataset-audit-v1.schema.json",
    )
    _validate_snapshot_internal_identity(snapshot)
    _validate_snapshot_fingerprints(snapshot)
    _validate_external_identity(snapshot, audit, integrity)


def evaluate_dataset_readiness(
    path: str,
    *,
    profile: str = "lerobot-core",
    policy_config: Mapping[str, Any] | str | Path | None = None,
    input_format: str = "auto",
    checksum: Optional[str] = "sha256",
    integrity: str = "full",
    follow_symlinks: bool = False,
    prepared: DatasetSnapshot | None = None,
    dataset_snapshot: Mapping[str, Any] | None = None,
    audit_result: Mapping[str, Any] | None = None,
    source_kind: str = "local",
    source_locator: str | None = None,
    requested_revision: str | None = None,
    resolved_revision: str | None = None,
    measurements: Mapping[str, Any] | None = None,
    publication_metadata: Mapping[str, Any] | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """Evaluate format-independent truth against one declared target profile."""
    if integrity not in _INTEGRITY_RANK:
        raise DatasetArgumentError("integrity must be metadata, sample, or full")
    for label, artifact in (
        ("dataset_snapshot", dataset_snapshot),
        ("audit_result", audit_result),
        ("measurements", measurements),
    ):
        if artifact is not None and not isinstance(artifact, Mapping):
            raise DatasetArgumentError(f"{label} must be a mapping")
    if publication_metadata is not None and not isinstance(
        publication_metadata,
        Mapping,
    ):
        raise DatasetArgumentError("publication_metadata must be a mapping")
    selected_profile = (
        _policy_profile(_load_json_object(policy_config))
        if policy_config is not None
        else load_readiness_profile(profile)
    )
    prepared_dataset = prepared
    if dataset_snapshot is None or audit_result is None:
        prepared_dataset = prepared_dataset or prepare_dataset(
            path,
            input_format=input_format,
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
        )
    snapshot_artifact = (
        dict(dataset_snapshot)
        if dataset_snapshot is not None
        else build_dataset_snapshot(
            path,
            input_format=input_format,
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
            source_kind=source_kind,
            source_locator=source_locator,
            requested_revision=requested_revision,
            resolved_revision=resolved_revision,
            snapshot=prepared_dataset,
        )
    )
    audit_artifact = (
        dict(audit_result)
        if audit_result is not None
        else audit_dataset(
            path,
            input_format=input_format,
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
            snapshot=prepared_dataset,
        )
    )
    if dataset_snapshot is not None or audit_result is not None:
        _validate_external_artifacts(
            snapshot_artifact,
            audit_artifact,
            integrity,
        )
    audit_findings = audit_artifact.get("findings", [])
    audit_items = [dict(item) for item in audit_findings if isinstance(item, Mapping)]
    blockers = [item for item in audit_items if item.get("severity") == "error"]
    warnings = [item for item in audit_items if item.get("severity") == "warning"]
    profile_blockers, profile_warnings = _contract_findings(
        snapshot_artifact,
        selected_profile,
        publication_metadata,
    )
    blockers.extend(profile_blockers)
    warnings.extend(profile_warnings)
    blockers = sorted(blockers, key=_finding_sort_key)
    warnings = sorted(warnings, key=_finding_sort_key)
    coverage, missing_capabilities = _coverage(
        audit_artifact,
        snapshot_artifact,
        selected_profile,
        integrity,
    )
    effective_measurements: Mapping[str, Any] | None = measurements
    if effective_measurements is None and prepared_dataset is not None:
        validation_result = getattr(
            prepared_dataset,
            "validation_result",
            None,
        )
        prepared_measurements = getattr(
            validation_result,
            "measurements",
            None,
        )
        if isinstance(prepared_measurements, Mapping):
            thawed = thaw_value(prepared_measurements)
            if isinstance(thawed, Mapping):
                effective_measurements = thawed
    advisory_signals = analyze_advisory_signals(
        snapshot_artifact,
        thresholds=(
            selected_profile.get("thresholds")
            if isinstance(selected_profile.get("thresholds"), Mapping)
            else {}
        ),
        measurements=effective_measurements,
    )
    if blockers:
        status = "BLOCKED"
    elif missing_capabilities:
        status = "PARTIAL"
    else:
        status = "READY"
    _metadata_contract, metadata_source = _publication_contract(
        snapshot_artifact,
        selected_profile,
        publication_metadata,
    )
    profile_projection = {
        "id": selected_profile["id"],
        "version": selected_profile["version"],
        "source_version": selected_profile["source_version"],
        "source_reference": selected_profile["source_reference"],
        "contract_source": ("policy_config" if policy_config is not None else "built_in"),
        "metadata_source": metadata_source,
    }
    remediation = sorted(
        {
            str(item["remediation_ref"])
            for item in [*blockers, *warnings]
            if isinstance(item.get("remediation_ref"), str)
        }
    )
    triage_input = [
        *blockers,
        *warnings,
        *(
            {
                "code": signal["code"],
                "severity": "advisory",
                "layer": signal["layer"],
                "message": signal["message"],
                "location": signal["location"],
                "evidence": {
                    "raw_value": signal["raw_value"],
                    "unit": signal["unit"],
                    "threshold": signal["threshold"],
                    "threshold_source": signal["threshold_source"],
                    "applicability": signal["applicability"],
                    "coverage": signal["coverage"],
                    **signal["evidence"],
                },
            }
            for signal in advisory_signals
        ),
    ]
    fingerprint_payload = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "fingerprint_version": READINESS_FINGERPRINT_VERSION,
        "profile": profile_projection,
        "status": status,
        "coverage": coverage,
        "blocking_findings": blockers,
        "warnings": warnings,
        "advisory_signals": advisory_signals,
        "triage": triage_findings(triage_input),
        "remediation": remediation,
        "snapshot_fingerprint": snapshot_artifact["snapshot_fingerprint"],
        "audit_rule_pack_version": audit_artifact.get("rule_pack_version"),
    }
    result = {
        **fingerprint_payload,
        "tool": {"name": "openbot-data", "version": __version__},
        "readiness_fingerprint": dataset_fingerprint(fingerprint_payload),
    }
    if output_path is not None:
        write_json_atomic(Path(output_path), result)
    return result


def render_readiness_markdown(readiness: Mapping[str, Any]) -> str:
    """Render a deterministic human projection of the canonical result."""
    profile = readiness.get("profile", {})
    lines = [
        "# Dataset readiness",
        "",
        f"- Status: `{readiness.get('status', 'UNKNOWN')}`",
        f"- Profile: `{profile.get('id', 'unknown')}@{profile.get('version', 'unknown')}`",
        f"- Snapshot: `{readiness.get('snapshot_fingerprint', '')}`",
        "",
        "## Blocking findings",
        "",
    ]
    blockers = readiness.get("blocking_findings", [])
    if isinstance(blockers, list) and blockers:
        for finding in blockers:
            if isinstance(finding, Mapping):
                lines.append(f"- `{finding.get('code', 'UNKNOWN')}`: {finding.get('message', '')}")
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    warnings = readiness.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        for finding in warnings:
            if isinstance(finding, Mapping):
                lines.append(f"- `{finding.get('code', 'UNKNOWN')}`: {finding.get('message', '')}")
    else:
        lines.append("- None")
    lines.extend(["", "## Missing capabilities", ""])
    coverage = readiness.get("coverage", {})
    missing = coverage.get("missing_capabilities", []) if isinstance(coverage, Mapping) else []
    if isinstance(missing, list) and missing:
        for item in missing:
            if isinstance(item, Mapping):
                lines.append(
                    f"- `{item.get('capability', 'unknown')}` "
                    f"({item.get('reason_code', 'unknown')})"
                )
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


__all__ = [
    "BUILTIN_PROFILE_FILES",
    "READINESS_FINGERPRINT_VERSION",
    "READINESS_SCHEMA_VERSION",
    "evaluate_dataset_readiness",
    "load_readiness_profile",
    "render_readiness_markdown",
]
