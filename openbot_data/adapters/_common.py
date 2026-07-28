"""Shared, side-effect-free helpers for the built-in LeRobot adapters."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from openbot_data.adapters.base import (
    ArtifactRecord,
    CapabilityStatus,
    Finding,
    freeze_value,
)

VIDEO_SUFFIXES = frozenset({".avi", ".mkv", ".mov", ".mp4", ".webm"})
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def finding(
    code: str,
    severity: str,
    layer: str,
    message: str,
    path: Optional[str] = None,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Finding:
    result: dict[str, Any] = {
        "code": code,
        "severity": severity,
        "layer": layer,
        "message": message,
        "evidence": dict(evidence or {}),
    }
    if path is not None:
        result["path"] = path
    return freeze_value(result)


def finding_sort_key(item: Finding) -> Tuple[int, str, str, str]:
    severity = str(item.get("severity", ""))
    evidence = json.dumps(
        _jsonable(item.get("evidence", {})),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        _SEVERITY_ORDER.get(severity, 99),
        str(item.get("code", "")),
        str(item.get("path", "")),
        evidence,
    )


def sorted_findings(items: Iterable[Finding]) -> Tuple[Finding, ...]:
    unique: dict[str, Finding] = {}
    for item in items:
        key = json.dumps(
            _jsonable(item),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        unique[key] = item
    return tuple(sorted(unique.values(), key=finding_sort_key))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def safe_error(error: object, root: Path) -> str:
    return str(error).replace(str(root), ".")


def dataset_file_status(
    root: Path,
    relative_path: str,
    *,
    follow_symlinks: bool,
) -> str:
    """Return the access status for one dataset-relative regular file.

    The default policy rejects a symlink in any path component. Opting in still
    requires the final target to resolve inside ``root``.
    """
    normalized = normalize_relative_path(relative_path)
    if normalized is None:
        return "outside"
    candidate = root / normalized
    current = root
    contains_symlink = False
    for part in PurePosixPath(normalized).parts:
        current = current / part
        if current.is_symlink():
            contains_symlink = True
            break
    if contains_symlink and not follow_symlinks:
        return "skipped"
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return "broken" if contains_symlink else "missing"
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return "outside"
    return "valid" if resolved.is_file() else "missing"


def path_policy_finding(
    status: str,
    relative_path: str,
    *,
    layer: str = "metadata",
) -> Optional[Finding]:
    """Return a stable finding for a rejected dataset file access."""
    if status == "skipped":
        return finding(
            "DATASET_SYMLINK_SKIPPED",
            "warning",
            layer,
            "Dataset file symlink was skipped by the default policy.",
            relative_path,
            {"follow_symlinks": False},
        )
    if status == "broken":
        return finding(
            "DATASET_SYMLINK_BROKEN",
            "error",
            layer,
            "Dataset file symlink does not resolve to a regular file.",
            relative_path,
        )
    if status == "outside":
        return finding(
            "DATASET_PATH_OUTSIDE_ROOT",
            "error",
            layer,
            "Dataset file must resolve inside the dataset root.",
            relative_path,
            {"symlink": True},
        )
    return None


def normalize_relative_path(value: object) -> Optional[str]:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or value.startswith(("/", "//"))
        or _URI_SCHEME.match(value)
    ):
        return None
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    normalized = candidate.as_posix()
    return normalized if normalized not in {"", "."} else None


def render_path_template(
    template: object,
    *,
    allowed_fields: Sequence[str],
    required_fields: Sequence[str],
    values: Mapping[str, object],
) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(template, str):
        return None, "non_string"
    fields = set()
    try:
        for _literal, field_name, _format_spec, conversion in Formatter().parse(template):
            if field_name is None:
                continue
            if conversion is not None:
                return None, "conversion_not_allowed"
            fields.add(field_name)
        if not set(required_fields).issubset(fields):
            return None, "missing_required_placeholder"
        if not fields.issubset(set(allowed_fields)):
            return None, "unknown_placeholder"
        rendered = template.format(**values)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None, "invalid_format"
    normalized = normalize_relative_path(rendered)
    if normalized is None:
        return None, "invalid_path"
    return normalized, None


def nonnegative_integer(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def finite_number(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (OverflowError, TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def declared_video_keys(info: Mapping[str, Any]) -> Tuple[str, ...]:
    features = info.get("features")
    if not isinstance(features, Mapping):
        return ()
    keys = []
    for key, raw_feature in features.items():
        if not isinstance(raw_feature, Mapping):
            continue
        dtype = str(raw_feature.get("dtype", "")).lower()
        if dtype == "video" or isinstance(raw_feature.get("video_info"), Mapping):
            keys.append(str(key))
    return tuple(sorted(set(keys)))


def read_json_object(
    root: Path,
    relative_path: str,
    findings: list[Finding],
    *,
    missing_code: Optional[str] = None,
    unreadable_code: str = "LEROBOT_METADATA_INVALID",
    follow_symlinks: bool = False,
) -> Optional[dict[str, Any]]:
    path = root / relative_path
    status = dataset_file_status(
        root,
        relative_path,
        follow_symlinks=follow_symlinks,
    )
    policy_finding = path_policy_finding(status, relative_path)
    if policy_finding is not None:
        findings.append(policy_finding)
    if status != "valid":
        if missing_code is not None:
            findings.append(
                finding(
                    missing_code,
                    "error",
                    "metadata",
                    "Required LeRobot metadata is missing.",
                    relative_path,
                )
            )
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(
            finding(
                unreadable_code,
                "error",
                "metadata",
                "LeRobot metadata could not be read.",
                relative_path,
                {"error": safe_error(exc, root)},
            )
        )
        return None
    if not isinstance(value, dict):
        findings.append(
            finding(
                unreadable_code,
                "error",
                "metadata",
                "LeRobot metadata must be a JSON object.",
                relative_path,
            )
        )
        return None
    return value


def read_jsonl_objects(
    root: Path,
    relative_path: str,
    findings: list[Finding],
    *,
    missing_code: str,
    unreadable_code: str,
    invalid_record_code: str,
    follow_symlinks: bool = False,
) -> list[Tuple[int, dict[str, Any]]]:
    path = root / relative_path
    status = dataset_file_status(
        root,
        relative_path,
        follow_symlinks=follow_symlinks,
    )
    policy_finding = path_policy_finding(status, relative_path)
    if policy_finding is not None:
        findings.append(policy_finding)
    if status != "valid":
        findings.append(
            finding(
                missing_code,
                "error",
                "metadata",
                "Required LeRobot JSONL metadata is missing.",
                relative_path,
            )
        )
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        findings.append(
            finding(
                unreadable_code,
                "error",
                "metadata",
                "LeRobot JSONL metadata could not be read.",
                relative_path,
                {"error": safe_error(exc, root)},
            )
        )
        return []
    records: list[Tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            findings.append(
                finding(
                    invalid_record_code,
                    "error",
                    "metadata",
                    "LeRobot JSONL record is not valid JSON.",
                    relative_path,
                    {"line": line_number, "error": safe_error(exc, root)},
                )
            )
            continue
        if not isinstance(value, dict):
            findings.append(
                finding(
                    invalid_record_code,
                    "error",
                    "metadata",
                    "LeRobot JSONL record must be an object.",
                    relative_path,
                    {"line": line_number},
                )
            )
            continue
        records.append((line_number, value))
    return records


def relative_files(
    root: Path,
    pattern: str,
    *,
    follow_symlinks: bool,
) -> Tuple[str, ...]:
    paths = []
    for path in root.glob(pattern):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if (
            dataset_file_status(
                root,
                relative,
                follow_symlinks=follow_symlinks,
            )
            != "valid"
        ):
            continue
        paths.append(relative)
    return tuple(sorted(set(paths)))


def file_artifact(
    root: Path,
    *,
    kind: str,
    path: str,
    source: str,
    follow_symlinks: bool = False,
    episode_index: Optional[int] = None,
    feature_key: Optional[str] = None,
    row_count: Optional[int] = None,
    columns: Sequence[str] = (),
) -> ArtifactRecord:
    candidate = root / path
    exists = (
        dataset_file_status(
            root,
            path,
            follow_symlinks=follow_symlinks,
        )
        == "valid"
    )
    size_bytes: Optional[int] = None
    if exists:
        try:
            size_bytes = candidate.stat().st_size
        except OSError:
            size_bytes = None
    return ArtifactRecord(
        kind=kind,
        path=path,
        exists=exists,
        source=source,
        episode_index=episode_index,
        feature_key=feature_key,
        size_bytes=size_bytes,
        row_count=row_count,
        columns=tuple(sorted(set(columns))),
    )


def capability(
    name: str,
    status: str,
    *,
    reason: Optional[str] = None,
    checked: Optional[int] = None,
    total: Optional[int] = None,
) -> CapabilityStatus:
    return CapabilityStatus(
        name=name,
        status=status,
        reason=reason,
        checked=checked,
        total=total,
    )


def sorted_capabilities(items: Iterable[CapabilityStatus]) -> Tuple[CapabilityStatus, ...]:
    return tuple(sorted(items, key=lambda item: item.name))
