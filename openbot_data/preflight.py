"""Deterministic local dataset discovery and audit helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from string import Formatter
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import cv2

from openbot_data.adapters import DiscoveryRequest, read_lerobot_dataset
from openbot_data.adapters._common import dataset_file_status
from openbot_data.adapters.base import AdapterResult, thaw_value
from openbot_data.audit import (
    AUDIT_RULE_PACK_VERSION,
    enrich_findings,
    run_audit_rules,
)
from openbot_data.errors import DatasetArgumentError, DatasetNotFoundError
from openbot_data.models import (
    DatasetArtifact,
    DatasetSnapshot,
    EpisodeRecord,
    VideoRecord,
)
from openbot_data.serialization import write_json_atomic
from openbot_data.validation import validate_prepared_dataset
from openbot_data.video import VIDEO_EXTENSIONS, scan_video

MANIFEST_SCHEMA_VERSION = "openbot.dataset_manifest.v1"
AUDIT_SCHEMA_VERSION = "openbot.dataset_audit.v1"
SUPPORTED_INPUT_FORMATS = {"auto", "video", "lerobot"}
SUPPORTED_CHECKSUMS = {None, "sha256"}
SUPPORTED_INTEGRITY_LEVELS = {"metadata", "sample", "full"}
INTEGRITY_ORDER = {"metadata": 0, "sample": 1, "full": 2}
DEFAULT_V3_VIDEO_PATH = (
    "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
)
V3_VIDEO_PATH_FIELDS = {"video_key", "chunk_index", "file_index"}
_URI_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _safe_error(error: object, root: Path) -> str:
    return str(error).replace(str(root), ".")


def _is_within_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _safe_media_path(candidate: Path, root: Path, *, follow_symlinks: bool) -> bool:
    if candidate.is_symlink() and not follow_symlinks:
        return False
    return candidate.is_file() and _is_within_root(candidate, root)


def _append_unique_finding(
    findings: List[Dict[str, Any]],
    finding: Dict[str, Any],
) -> None:
    if finding not in findings:
        findings.append(finding)


def _relative_evidence_path(candidate: Path, root: Path) -> str:
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return candidate.name


def _media_reference_status(
    candidate: Path,
    root: Path,
    findings: List[Dict[str, Any]],
    path: str,
    *,
    follow_symlinks: bool,
) -> str:
    if candidate.is_symlink():
        if not follow_symlinks:
            _append_unique_finding(
                findings,
                _finding(
                    "DATASET_SYMLINK_SKIPPED",
                    "warning",
                    "Dataset media symlink was skipped by the default policy.",
                    path,
                    {"follow_symlinks": False},
                ),
            )
            return "skipped"
        try:
            candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            _append_unique_finding(
                findings,
                _finding(
                    "DATASET_SYMLINK_BROKEN",
                    "error",
                    "Dataset media symlink does not resolve to a file.",
                    path,
                ),
            )
            return "broken"
    if not candidate.is_file():
        return "missing"
    if not _is_within_root(candidate, root):
        _append_unique_finding(
            findings,
            _finding(
                "DATASET_PATH_OUTSIDE_ROOT",
                "error",
                "Dataset media must resolve inside the dataset root.",
                path,
                {"symlink": candidate.is_symlink()},
            ),
        )
        return "outside"
    return "valid"


def _discover_media_paths(
    root: Path,
    search_root: Path,
    findings: List[Dict[str, Any]],
    *,
    follow_symlinks: bool,
) -> List[Path]:
    paths: List[Path] = []
    if not search_root.is_dir():
        return paths
    for candidate in search_root.rglob("*"):
        if candidate.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        relative = _relative_evidence_path(candidate, root)
        status = _media_reference_status(
            candidate,
            root,
            findings,
            relative,
            follow_symlinks=follow_symlinks,
        )
        if status != "valid":
            continue
        paths.append(candidate)
    return sorted(set(paths))


def _finding(
    code: str,
    severity: str,
    message: str,
    path: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
        "evidence": evidence or {},
    }
    if path is not None:
        result["path"] = path
    return result


def _append_metadata_path_policy_finding(
    findings: List[Dict[str, Any]],
    path: str,
    status: str,
) -> None:
    if status == "skipped":
        _append_unique_finding(
            findings,
            _finding(
                "DATASET_SYMLINK_SKIPPED",
                "warning",
                "Dataset metadata symlink was skipped by the default policy.",
                path,
                {"follow_symlinks": False},
            ),
        )
    elif status == "broken":
        _append_unique_finding(
            findings,
            _finding(
                "DATASET_SYMLINK_BROKEN",
                "error",
                "Dataset metadata symlink does not resolve to a regular file.",
                path,
            ),
        )
    elif status == "outside":
        _append_unique_finding(
            findings,
            _finding(
                "DATASET_PATH_OUTSIDE_ROOT",
                "error",
                "Dataset metadata must resolve inside the dataset root.",
                path,
                {"symlink": True},
            ),
        )


def _read_json(
    path: Path,
    root: Path,
    findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(
            _finding(
                "LEROBOT_METADATA_INVALID",
                "error",
                "LeRobot metadata is not valid JSON.",
                path.relative_to(root).as_posix(),
                {"error": _safe_error(exc, root)},
            )
        )
        return {}
    if not isinstance(value, dict):
        findings.append(
            _finding(
                "LEROBOT_METADATA_INVALID",
                "error",
                "LeRobot metadata must be a JSON object.",
                path.relative_to(root).as_posix(),
            )
        )
        return {}
    return value


def _read_jsonl(
    paths: Iterable[Path],
    root: Path,
    findings: List[Dict[str, Any]],
    *,
    follow_symlinks: bool = False,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in sorted(paths):
        relative = _relative_evidence_path(path, root)
        status = dataset_file_status(
            root,
            relative,
            follow_symlinks=follow_symlinks,
        )
        if status != "valid":
            _append_metadata_path_policy_finding(findings, relative, status)
            findings.append(
                _finding(
                    "LEROBOT_EPISODES_UNREADABLE",
                    "error",
                    "LeRobot episode metadata is not readable under the dataset path policy.",
                    relative,
                    {"reason": status},
                )
            )
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            findings.append(
                _finding(
                    "LEROBOT_EPISODES_UNREADABLE",
                    "error",
                    "LeRobot episode metadata could not be read.",
                    path.relative_to(root).as_posix(),
                    {"error": _safe_error(exc, root)},
                )
            )
            continue
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                findings.append(
                    _finding(
                        "LEROBOT_EPISODE_INVALID",
                        "error",
                        "LeRobot episode line is not valid JSON.",
                        path.relative_to(root).as_posix(),
                        {"line": line_number, "error": _safe_error(exc, root)},
                    )
                )
                continue
            if not isinstance(value, dict):
                findings.append(
                    _finding(
                        "LEROBOT_EPISODE_INVALID",
                        "error",
                        "LeRobot episode line must be a JSON object.",
                        path.relative_to(root).as_posix(),
                        {"line": line_number},
                    )
                )
                continue
            records.append(value)
    return records


def _read_episode_parquet(
    paths: Iterable[Path],
    root: Path,
    findings: List[Dict[str, Any]],
    *,
    follow_symlinks: bool = False,
) -> List[Dict[str, Any]]:
    parquet_paths = sorted(paths)
    if not parquet_paths:
        return []
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found,import-untyped]
    except ImportError:
        findings.append(
            _finding(
                "LEROBOT_DEPENDENCY_MISSING",
                "error",
                "Reading LeRobot episode parquet requires the 'lerobot' extra.",
                parquet_paths[0].relative_to(root).as_posix(),
                {"install": "pip install 'openbot-data[lerobot]'"},
            )
        )
        return []
    records: List[Dict[str, Any]] = []
    for path in parquet_paths:
        relative = _relative_evidence_path(path, root)
        status = dataset_file_status(
            root,
            relative,
            follow_symlinks=follow_symlinks,
        )
        if status != "valid":
            _append_metadata_path_policy_finding(findings, relative, status)
            findings.append(
                _finding(
                    "LEROBOT_EPISODES_UNREADABLE",
                    "error",
                    "LeRobot episode parquet is not readable under the dataset path policy.",
                    relative,
                    {"reason": status},
                )
            )
            continue
        try:
            parquet_file = parquet.ParquetFile(path)
            batches: Iterator[Any] = parquet_file.iter_batches(batch_size=1024)
            for batch in batches:
                records.extend(
                    dict(row) for row in batch.to_pylist() if isinstance(row, dict)
                )
        except Exception as exc:
            findings.append(
                _finding(
                    "LEROBOT_EPISODES_UNREADABLE",
                    "error",
                    "LeRobot episode parquet could not be read.",
                    path.relative_to(root).as_posix(),
                    {"error": _safe_error(exc, root)},
                )
            )
            continue
    return records


def detect_input_format(path: str, input_format: str = "auto") -> str:
    """Resolve ``auto`` to a supported local input format."""
    normalized = input_format.lower()
    if normalized not in SUPPORTED_INPUT_FORMATS:
        raise DatasetArgumentError(
            "Unsupported input format: "
            f"{input_format}. Use one of {sorted(SUPPORTED_INPUT_FORMATS)}"
        )
    if normalized != "auto":
        return normalized
    root = Path(path)
    if (root / "meta" / "info.json").is_file() or (
        (root / "meta").is_dir() and (root / "videos").is_dir()
    ):
        return "lerobot"
    return "video"


def _validate_request_options(checksum: Optional[str], integrity: str) -> None:
    if checksum not in SUPPORTED_CHECKSUMS:
        raise DatasetArgumentError("checksum must be omitted or 'sha256'")
    if integrity not in SUPPORTED_INTEGRITY_LEVELS:
        raise DatasetArgumentError(
            f"integrity must be one of {sorted(SUPPORTED_INTEGRITY_LEVELS)}"
        )


def validate_snapshot_request(
    snapshot: DatasetSnapshot,
    path: str,
    input_format: str = "auto",
    checksum: Optional[str] = None,
    integrity: str = "sample",
    follow_symlinks: bool = False,
) -> DatasetSnapshot:
    """Reject a prepared snapshot that cannot satisfy a rendering request."""
    _validate_request_options(checksum, integrity)
    root = Path(path).resolve()
    if not root.is_dir():
        raise DatasetNotFoundError(f"Directory not found: {path}")
    if snapshot.checksum not in SUPPORTED_CHECKSUMS:
        raise DatasetArgumentError("snapshot has unsupported checksum coverage")
    if snapshot.integrity not in SUPPORTED_INTEGRITY_LEVELS:
        raise DatasetArgumentError("snapshot has unsupported integrity coverage")
    if snapshot.input_format not in {"video", "lerobot"}:
        raise DatasetArgumentError("snapshot has unsupported input format")
    if not isinstance(snapshot.follow_symlinks, bool):
        raise DatasetArgumentError("snapshot has an invalid symlink policy")
    for video in snapshot.videos:
        if video.integrity_level not in SUPPORTED_INTEGRITY_LEVELS:
            raise DatasetArgumentError(
                "snapshot video has unsupported integrity coverage"
            )
        if (
            INTEGRITY_ORDER[video.integrity_level]
            < INTEGRITY_ORDER[snapshot.integrity]
        ):
            raise DatasetArgumentError(
                "snapshot video coverage does not satisfy its declared integrity"
            )
        if (
            snapshot.integrity in {"sample", "full"}
            and video.decode_valid is None
        ):
            raise DatasetArgumentError(
                "snapshot video has no decode result for its declared integrity"
            )
        if snapshot.checksum == "sha256" and (
            not isinstance(video.checksum_sha256, str)
            or _SHA256.fullmatch(video.checksum_sha256) is None
        ):
            raise DatasetArgumentError(
                "snapshot video has no valid SHA-256 for its declared checksum"
            )
    if snapshot.root.resolve() != root:
        raise DatasetArgumentError("snapshot root does not match the requested dataset")

    resolved_format = detect_input_format(path, input_format)
    if snapshot.input_format != resolved_format:
        raise DatasetArgumentError(
            "snapshot input format does not match the requested dataset format"
        )
    if checksum == "sha256" and snapshot.checksum != "sha256":
        raise DatasetArgumentError(
            "snapshot checksum coverage does not satisfy the requested checksum"
        )
    if INTEGRITY_ORDER.get(snapshot.integrity, -1) < INTEGRITY_ORDER[integrity]:
        raise DatasetArgumentError(
            "snapshot integrity does not satisfy the requested integrity level"
        )
    if snapshot.follow_symlinks != follow_symlinks:
        raise DatasetArgumentError(
            "snapshot symlink policy does not match the requested policy"
        )
    return snapshot


def _video_key(relative_path: str) -> str:
    parts = Path(relative_path).parts
    if "videos" not in parts:
        return Path(relative_path).parent.as_posix() or "default"
    tail = parts[parts.index("videos") + 1 : -1]
    candidates = [part for part in tail if not part.startswith(("chunk-", "file-"))]
    return candidates[0] if candidates else "default"


def _declared_video_keys(info: Dict[str, Any]) -> List[str]:
    features = info.get("features")
    if not isinstance(features, dict):
        return []
    keys = []
    for key, value in features.items():
        if not isinstance(value, dict):
            continue
        dtype = str(value.get("dtype", "")).lower()
        if dtype == "video" or isinstance(value.get("video_info"), dict):
            keys.append(str(key))
    return sorted(set(keys))


def _is_lerobot_v3(info: Dict[str, Any], episodes: Iterable[Dict[str, Any]]) -> bool:
    version = str(info.get("codebase_version", "")).lower().lstrip("v")
    if version.startswith("3"):
        return True
    return any(
        any(str(key).startswith("videos/") and str(key).endswith("/file_index") for key in row)
        for row in episodes
    )


def _normalize_declared_path(path: object) -> str | None:
    if (
        not isinstance(path, str)
        or not path
        or "\x00" in path
        or "\\" in path
        or path.startswith(("/", "//"))
        or _URI_SCHEME.match(path)
    ):
        return None
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    normalized = candidate.as_posix()
    return normalized if normalized not in {"", "."} else None


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _validate_v3_video_template(
    info: Dict[str, Any],
    findings: List[Dict[str, Any]],
) -> str | None:
    template = info.get("video_path", DEFAULT_V3_VIDEO_PATH)
    reason: str | None = None
    fields: set[str] = set()
    if not isinstance(template, str):
        reason = "non_string"
    else:
        try:
            for _literal, field_name, format_spec, conversion in Formatter().parse(
                template
            ):
                if field_name is not None:
                    fields.add(field_name)
                    if (
                        "{" in (format_spec or "")
                        or "}" in (format_spec or "")
                        or conversion is not None
                    ):
                        reason = "nested_or_converted_placeholder"
                        break
            if reason is not None:
                pass
            elif not V3_VIDEO_PATH_FIELDS.issubset(fields):
                reason = "missing_required_placeholder"
            elif not fields.issubset(V3_VIDEO_PATH_FIELDS):
                reason = "unknown_placeholder"
            else:
                example = template.format(
                    video_key="camera",
                    chunk_index=0,
                    file_index=0,
                )
                if _normalize_declared_path(example) is None:
                    reason = "invalid_path"
        except (
            AttributeError,
            IndexError,
            KeyError,
            OverflowError,
            TypeError,
            ValueError,
        ):
            reason = "invalid_format"
    if reason is None:
        return template
    findings.append(
        _finding(
            "LEROBOT_VIDEO_PATH_TEMPLATE_INVALID",
            "error",
            "LeRobot v3 video_path template is invalid.",
            "meta/info.json",
            {
                "reason": reason,
                "required_placeholders": sorted(V3_VIDEO_PATH_FIELDS),
            },
        )
    )
    return None


def _segment_bounds(
    from_timestamp: object,
    to_timestamp: object,
    episode_index: int,
    video_key: str,
    findings: List[Dict[str, Any]],
) -> Tuple[float, float] | None:
    values = {
        "from_timestamp": from_timestamp,
        "to_timestamp": to_timestamp,
    }
    missing = sorted(key for key, value in values.items() if value is None)
    reason: str | None = "missing_pair" if missing else None
    parsed: Dict[str, float] = {}
    if reason is None:
        for key, value in values.items():
            if isinstance(value, bool):
                reason = "non_numeric"
                break
            try:
                parsed[key] = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError, OverflowError):
                reason = "non_numeric"
                break
        if reason is None and not all(math.isfinite(value) for value in parsed.values()):
            reason = "non_finite"
        elif reason is None and any(value < 0 for value in parsed.values()):
            reason = "negative"
        elif reason is None and parsed["from_timestamp"] >= parsed["to_timestamp"]:
            reason = "not_ordered"
    if reason is None:
        return parsed["from_timestamp"], parsed["to_timestamp"]
    evidence: Dict[str, Any] = {
        "episode_index": episode_index,
        "video_key": video_key,
        "reason": reason,
    }
    if missing:
        evidence["missing"] = missing
    findings.append(
        _finding(
            "LEROBOT_VIDEO_SEGMENT_BOUNDS_INVALID",
            "error",
            "LeRobot video segment bounds must be finite, non-negative, and ordered.",
            "meta/episodes",
            evidence,
        )
    )
    return None


def _relation_indexes(
    chunk: object,
    file_index: object,
    episode_index: int,
    video_key: str,
    findings: List[Dict[str, Any]],
    *,
    required: bool,
) -> Tuple[int, int] | None:
    relation_present = chunk is not None or file_index is not None
    if not required and not relation_present:
        return None
    missing_fields = [
        field
        for field, value in (
            ("chunk_index", chunk),
            ("file_index", file_index),
        )
        if value is None
    ]
    if missing_fields:
        findings.append(
            _finding(
                "LEROBOT_VIDEO_RELATION_MISSING",
                "error",
                "LeRobot v3 episode has no complete relational video shard reference.",
                "meta/episodes",
                {
                    "episode_index": episode_index,
                    "video_key": video_key,
                    "missing": missing_fields,
                },
            )
        )
        return None
    chunk_index = _nonnegative_integer(chunk)
    normalized_file_index = _nonnegative_integer(file_index)
    if chunk_index is not None and normalized_file_index is not None:
        return chunk_index, normalized_file_index
    invalid_fields = []
    if chunk_index is None:
        invalid_fields.append("chunk_index")
    if normalized_file_index is None:
        invalid_fields.append("file_index")
    findings.append(
        _finding(
            "LEROBOT_VIDEO_RELATION_INVALID",
            "error",
            "LeRobot v3 video shard indexes must be non-negative integers.",
            "meta/episodes",
            {
                "episode_index": episode_index,
                "video_key": video_key,
                "invalid_fields": invalid_fields,
            },
        )
    )
    return None


def _v3_video_segments(
    raw: Dict[str, Any],
    template: str | None,
    video_keys: Iterable[str],
    root: Path,
    episode_index: int,
    findings: List[Dict[str, Any]],
    *,
    follow_symlinks: bool,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    segments: List[Dict[str, Any]] = []
    matched_paths: List[str] = []
    nested_videos = raw.get("videos")
    for video_key in video_keys:
        chunk = raw.get(f"videos/{video_key}/chunk_index")
        file_index = raw.get(f"videos/{video_key}/file_index")
        from_timestamp = raw.get(f"videos/{video_key}/from_timestamp")
        to_timestamp = raw.get(f"videos/{video_key}/to_timestamp")
        explicit_path: str | None = None
        invalid_explicit_path = False
        if isinstance(nested_videos, dict):
            nested = nested_videos.get(video_key)
            if isinstance(nested, dict):
                chunk = nested.get("chunk_index", chunk)
                file_index = nested.get("file_index", file_index)
                from_timestamp = nested.get("from_timestamp", from_timestamp)
                to_timestamp = nested.get("to_timestamp", to_timestamp)
                if "path" in nested:
                    if isinstance(nested.get("path"), str):
                        explicit_path = str(nested["path"])
                    else:
                        invalid_explicit_path = True
            elif isinstance(nested, str):
                explicit_path = nested
            elif video_key in nested_videos:
                invalid_explicit_path = True

        bounds = _segment_bounds(
            from_timestamp,
            to_timestamp,
            episode_index,
            video_key,
            findings,
        )
        relation = _relation_indexes(
            chunk,
            file_index,
            episode_index,
            video_key,
            findings,
            required=explicit_path is None and not invalid_explicit_path,
        )

        if invalid_explicit_path:
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_PATH_INVALID",
                    "error",
                    "Episode video path must be a portable dataset-relative path.",
                    "meta/episodes",
                    {"episode_index": episode_index, "video_key": video_key},
                )
            )
            continue

        if explicit_path is None:
            if relation is None:
                continue
            if template is None:
                continue
            try:
                explicit_path = template.format(
                    video_key=video_key,
                    chunk_index=relation[0],
                    file_index=relation[1],
                )
            except (
                AttributeError,
                IndexError,
                KeyError,
                OverflowError,
                TypeError,
                ValueError,
            ):
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_PATH_TEMPLATE_INVALID",
                        "error",
                        "LeRobot v3 video_path template could not render a shard path.",
                        "meta/info.json",
                        {"episode_index": episode_index, "video_key": video_key},
                    )
                )
                continue

        normalized = _normalize_declared_path(explicit_path)
        if normalized is None:
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_PATH_INVALID",
                    "error",
                    "Episode video path must be a portable dataset-relative path.",
                    "meta/episodes",
                    {"episode_index": episode_index, "video_key": video_key},
                )
            )
            continue
        candidate = root / normalized
        status = _media_reference_status(
            candidate,
            root,
            findings,
            normalized,
            follow_symlinks=follow_symlinks,
        )
        if status != "valid":
            if status == "missing":
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_MISSING",
                        "error",
                        "Episode references a local video shard that does not exist.",
                        normalized,
                        {"episode_index": episode_index, "video_key": video_key},
                    )
                )
            continue
        matched_paths.append(normalized)
        if bounds is None:
            continue
        segment: Dict[str, Any] = {
            "video_key": video_key,
            "path_base": "dataset",
            "path": normalized,
            "from_timestamp": bounds[0],
            "to_timestamp": bounds[1],
        }
        segments.append(segment)
    return segments, sorted(set(matched_paths))


def _v2_video_files(
    raw: Dict[str, Any],
    videos: Iterable[str],
    video_keys: Iterable[str],
    root: Path,
    episode_index: int,
    findings: List[Dict[str, Any]],
    *,
    follow_symlinks: bool,
) -> List[str]:
    token = f"episode_{episode_index:06d}"
    available = sorted(set(videos))
    declared_keys = sorted(set(video_keys))
    matched: List[str] = []
    covered_keys: set[str] = set()
    if declared_keys:
        for video_key in declared_keys:
            camera_matches = [
                item
                for item in available
                if Path(item).stem == token and video_key in PurePosixPath(item).parts
            ]
            matched.extend(camera_matches)
            if camera_matches:
                covered_keys.add(video_key)
    else:
        matched.extend(item for item in available if Path(item).stem == token)

    explicit_paths: List[Tuple[str | None, object]] = []
    raw_videos = raw.get("videos")
    if isinstance(raw_videos, dict):
        explicit_paths.extend((str(key), value) for key, value in raw_videos.items())
    raw_video_path = raw.get("video_path")
    if raw_video_path is not None:
        explicit_paths.append((None, raw_video_path))
    for explicit_key, explicit in explicit_paths:
        if not isinstance(explicit, str):
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_PATH_INVALID",
                    "error",
                    "Episode video path must be a portable dataset-relative path.",
                    "meta/episodes",
                    {
                        "episode_index": episode_index,
                        **({"video_key": explicit_key} if explicit_key is not None else {}),
                    },
                )
            )
            continue
        normalized = _normalize_declared_path(explicit)
        if normalized is None:
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_PATH_INVALID",
                    "error",
                    "Episode video path must be a portable dataset-relative path.",
                    "meta/episodes",
                    {
                        "episode_index": episode_index,
                        **({"video_key": explicit_key} if explicit_key is not None else {}),
                    },
                )
            )
            continue
        status = _media_reference_status(
            root / normalized,
            root,
            findings,
            normalized,
            follow_symlinks=follow_symlinks,
        )
        if status == "missing":
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_MISSING",
                    "error",
                    "Episode references a local video file that does not exist.",
                    normalized,
                    {
                        "episode_index": episode_index,
                        **({"video_key": explicit_key} if explicit_key is not None else {}),
                    },
                )
            )
        elif status == "valid":
            if normalized not in matched:
                matched.append(normalized)
            path_keys = {
                video_key
                for video_key in declared_keys
                if video_key in PurePosixPath(normalized).parts
            }
            if explicit_key in declared_keys:
                path_keys.add(str(explicit_key))
            elif explicit_key is None and len(declared_keys) == 1:
                path_keys.add(declared_keys[0])
            covered_keys.update(path_keys)
    for video_key in sorted(set(declared_keys) - covered_keys):
        findings.append(
            _finding(
                "LEROBOT_VIDEO_MISSING",
                "error",
                "LeRobot v2.1 episode has no file for a declared video stream.",
                "videos",
                {"episode_index": episode_index, "video_key": video_key},
            )
        )
    return sorted(set(matched))


def _validate_video_segments(
    episodes: Iterable[Dict[str, Any]],
    root: Path,
    findings: List[Dict[str, Any]],
) -> None:
    grouped: Dict[Tuple[str, str], List[Tuple[float, float, int]]] = defaultdict(list)
    for episode in episodes:
        episode_index = int(episode["episode_index"])
        for segment in episode.get("video_segments", []):
            if not isinstance(segment, dict):
                continue
            path = segment.get("path")
            video_key = segment.get("video_key")
            from_timestamp = segment.get("from_timestamp")
            to_timestamp = segment.get("to_timestamp")
            if (
                not isinstance(path, str)
                or not isinstance(video_key, str)
                or not isinstance(from_timestamp, (int, float))
                or not isinstance(to_timestamp, (int, float))
            ):
                continue
            grouped[(video_key, path)].append(
                (float(from_timestamp), float(to_timestamp), episode_index)
            )

    for (video_key, path), segments in sorted(grouped.items()):
        ordered = sorted(segments, key=lambda item: (item[0], item[1], item[2]))
        previous: Tuple[float, float, int] | None = None
        for current in ordered:
            if previous is not None and current[0] < previous[1] - 1e-9:
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_SEGMENT_OVERLAP",
                        "error",
                        "LeRobot episodes overlap in one shared video shard.",
                        path,
                        {
                            "video_key": video_key,
                            "previous_episode_index": previous[2],
                            "previous_range": [previous[0], previous[1]],
                            "episode_index": current[2],
                            "range": [current[0], current[1]],
                        },
                    )
                )
            if previous is None or current[1] > previous[1]:
                previous = current

        video_info = scan_video(str(root / path))
        fps = float(video_info.fps)
        frame_count = int(video_info.frame_count)
        if fps <= 0 or frame_count <= 0:
            continue
        duration = frame_count / fps
        tolerance = 1.0 / fps
        for from_timestamp, to_timestamp, episode_index in ordered:
            if to_timestamp > duration + tolerance:
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_SEGMENT_OUT_OF_RANGE",
                        "error",
                        "LeRobot video segment extends beyond the referenced shard duration.",
                        path,
                        {
                            "episode_index": episode_index,
                            "video_key": video_key,
                            "from_timestamp": from_timestamp,
                            "to_timestamp": to_timestamp,
                            "duration_seconds": round(duration, 9),
                            "tolerance_seconds": round(tolerance, 9),
                        },
                    )
                )


def read_lerobot(path: str, *, follow_symlinks: bool = False) -> Dict[str, Any]:
    """Discover episodes and local video streams in a LeRobot v2.1/v3 dataset."""
    root = Path(path).resolve()
    findings: List[Dict[str, Any]] = []
    if not root.is_dir():
        return {
            "format": "lerobot",
            "codebase_version": None,
            "episodes": [],
            "video_keys": [],
            "videos": [],
            "findings": [
                _finding("DATASET_NOT_FOUND", "error", "Dataset directory was not found.", ".")
            ],
        }

    info_path = root / "meta" / "info.json"
    info_status = dataset_file_status(
        root,
        "meta/info.json",
        follow_symlinks=follow_symlinks,
    )
    if info_status != "valid":
        _append_metadata_path_policy_finding(
            findings,
            "meta/info.json",
            info_status,
        )
        info: Dict[str, Any] = {}
        findings.append(
            _finding(
                "LEROBOT_INFO_MISSING",
                "error",
                "LeRobot meta/info.json is missing.",
                "meta/info.json",
            )
        )
    else:
        info = _read_json(info_path, root, findings)

    episode_paths = list((root / "meta").glob("episodes.jsonl"))
    episode_directory = root / "meta" / "episodes"
    if episode_directory.is_dir():
        episode_paths.extend(episode_directory.rglob("*.jsonl"))
    parquet_paths = (
        list((root / "meta" / "episodes").rglob("*.parquet"))
        if (root / "meta" / "episodes").is_dir()
        else []
    )
    raw_episodes = _read_jsonl(
        episode_paths,
        root,
        findings,
        follow_symlinks=follow_symlinks,
    )
    raw_episodes.extend(
        _read_episode_parquet(
            parquet_paths,
            root,
            findings,
            follow_symlinks=follow_symlinks,
        )
    )
    if not episode_paths and not parquet_paths:
        findings.append(
            _finding(
                "LEROBOT_EPISODES_MISSING",
                "error",
                "LeRobot episode metadata is missing.",
                "meta/episodes.jsonl",
            )
        )

    video_paths = _discover_media_paths(
        root,
        root / "videos",
        findings,
        follow_symlinks=follow_symlinks,
    )
    videos = [candidate.relative_to(root).as_posix() for candidate in video_paths]
    declared_video_keys = _declared_video_keys(info)
    video_keys = sorted(set(declared_video_keys) | {_video_key(item) for item in videos})
    is_v3 = _is_lerobot_v3(info, raw_episodes)
    video_template = (
        _validate_v3_video_template(info, findings)
        if is_v3 and declared_video_keys
        else None
    )

    episodes: List[Dict[str, Any]] = []
    episode_occurrences: Dict[int, List[int]] = defaultdict(list)
    for ordinal, raw in enumerate(raw_episodes):
        raw_index = raw.get("episode_index", raw.get("index"))
        episode_index = _nonnegative_integer(raw_index)
        if episode_index is None:
            findings.append(
                _finding(
                    "LEROBOT_EPISODE_INDEX_INVALID",
                    "error",
                    "Episode metadata has no valid episode_index.",
                    "meta/episodes.jsonl",
                    {"record": ordinal},
                )
            )
            continue
        episode_occurrences[episode_index].append(ordinal)

        tasks_value = raw.get("tasks", raw.get("task", []))
        if isinstance(tasks_value, str):
            tasks = [tasks_value]
        elif isinstance(tasks_value, list):
            tasks = sorted(str(task) for task in tasks_value)
        else:
            tasks = []

        length: int | None = None
        if "length" in raw:
            length = _nonnegative_integer(raw.get("length"))
            if length is None:
                findings.append(
                    _finding(
                        "LEROBOT_EPISODE_LENGTH_INVALID",
                        "error",
                        "Episode length must be a non-negative integer.",
                        "meta/episodes",
                        {"episode_index": episode_index, "record": ordinal},
                    )
                )

        if is_v3:
            segments, matched = _v3_video_segments(
                raw,
                video_template,
                declared_video_keys,
                root,
                episode_index,
                findings,
                follow_symlinks=follow_symlinks,
            )
        else:
            segments = []
            matched = _v2_video_files(
                raw,
                videos,
                declared_video_keys,
                root,
                episode_index,
                findings,
                follow_symlinks=follow_symlinks,
            )

        episode = EpisodeRecord(
            episode_index=episode_index,
            length=length,
            tasks=tuple(tasks),
            video_files=tuple(matched),
            video_segments=tuple(segments),
        )
        episodes.append(episode.as_dict())

    episodes.sort(key=lambda item: item["episode_index"])
    for episode_index, occurrences in sorted(episode_occurrences.items()):
        if len(occurrences) > 1:
            findings.append(
                _finding(
                    "LEROBOT_EPISODE_INDEX_DUPLICATE",
                    "error",
                    "Episode index appears in more than one metadata record.",
                    "meta/episodes",
                    {
                        "episode_index": episode_index,
                        "records": occurrences,
                        "count": len(occurrences),
                    },
                )
            )
    _validate_video_segments(episodes, root, findings)
    total_declared = info.get("total_episodes")
    if isinstance(total_declared, int) and total_declared != len(episodes):
        findings.append(
            _finding(
                "LEROBOT_EPISODE_COUNT_MISMATCH",
                "error",
                "Declared episode count does not match readable episode metadata.",
                "meta/info.json",
                {"declared": total_declared, "discovered": len(episodes)},
            )
        )
    if video_keys and not videos:
        findings.append(
            _finding(
                "LEROBOT_VIDEOS_MISSING",
                "error",
                "LeRobot metadata declares video streams but no local video files were found.",
                "videos",
                {"video_keys": video_keys},
            )
        )

    return {
        "format": "lerobot",
        "codebase_version": info.get("codebase_version"),
        "episodes": episodes,
        "video_keys": video_keys,
        "videos": videos,
        "findings": sorted(findings, key=_finding_sort_key),
    }


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _decode_probe(
    path: Path,
    integrity: str,
    expected_frames: int,
) -> Tuple[bool | None, int | None]:
    if integrity == "metadata":
        return None, None
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            return False, 0
        if integrity == "sample":
            last_frame = max(0, expected_frames - 1)
            positions = sorted({0, last_frame // 2, last_frame})
            decoded = 0
            valid = True
            for position in positions:
                if not capture.set(cv2.CAP_PROP_POS_FRAMES, position):
                    valid = False
                    continue
                ok, _frame = capture.read()
                if not ok:
                    valid = False
                    continue
                decoded += 1
            return valid, decoded
        decoded = 0
        while True:
            ok, _frame = capture.read()
            if not ok:
                break
            decoded += 1
        if expected_frames > 0:
            return decoded >= expected_frames, decoded
        return decoded > 0, decoded
    finally:
        capture.release()


def _scan_paths(
    root: Path,
    paths: Iterable[Path],
    checksum: Optional[str],
    integrity: str,
) -> Tuple[VideoRecord, ...]:
    records: List[VideoRecord] = []
    for candidate in sorted(set(paths)):
        info = scan_video(str(candidate))
        relative = candidate.relative_to(root).as_posix()
        metadata_valid = (
            info.width > 0
            and info.height > 0
            and info.frame_count > 0
            and float(info.fps) > 0
            and float(info.duration) > 0
        )
        decode_valid, probed_frames = _decode_probe(candidate, integrity, info.frame_count)
        decoded_frames = (
            1
            if integrity == "sample" and decode_valid is True
            else probed_frames
        )
        error = _safe_error(info.error, root) if info.error is not None else None
        if not metadata_valid and error is None:
            error = "Video metadata is incomplete or invalid"
        records.append(
            VideoRecord(
                source_path=candidate,
                path=relative,
                filename=candidate.name,
                stream=_video_key(relative),
                width=info.width,
                height=info.height,
                fps=round(float(info.fps), 3),
                frame_count=info.frame_count,
                duration=round(float(info.duration), 3),
                size_bytes=candidate.stat().st_size if candidate.exists() else 0,
                size_mb=round(float(info.size_mb), 2),
                metadata_valid=metadata_valid,
                decode_valid=decode_valid,
                integrity_level=integrity,
                decoded_frame_count=decoded_frames,
                error=error,
                checksum_sha256=sha256_file(candidate) if checksum == "sha256" else None,
                raw_fps=float(info.fps),
                raw_duration=float(info.duration),
            )
        )
    return tuple(records)


def _adapter_episode_records(result: AdapterResult) -> Tuple[EpisodeRecord, ...]:
    """Project one adapter result into the legacy episode facade without I/O."""
    video_relations: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for relation in result.relations:
        if (
            relation.kind != "video"
            or not relation.exists
            or relation.from_timestamp is None
            or relation.to_timestamp is None
        ):
            continue
        video_relations[relation.episode_index].append(
            {
                "video_key": relation.feature_key,
                "path_base": "dataset",
                "path": relation.path,
                "from_timestamp": relation.from_timestamp,
                "to_timestamp": relation.to_timestamp,
            }
        )
    return tuple(
        EpisodeRecord(
            episode_index=episode.episode_index,
            length=episode.length,
            tasks=episode.tasks,
            video_files=episode.video_paths,
            video_segments=tuple(
                sorted(
                    video_relations.get(episode.episode_index, []),
                    key=lambda item: (
                        str(item["video_key"]),
                        str(item["path"]),
                        float(item["from_timestamp"]),
                        float(item["to_timestamp"]),
                    ),
                )
            ),
        )
        for episode in result.episodes
    )


def _selected_artifact_paths(
    paths: List[str],
    integrity: str,
) -> set[str]:
    if integrity == "full":
        return set(paths)
    if integrity == "metadata" or not paths:
        return set()
    last = len(paths) - 1
    return {paths[index] for index in sorted({0, last // 2, last})}


def _prepared_artifacts(
    root: Path,
    adapter_result: AdapterResult | None,
    videos: Tuple[VideoRecord, ...],
    *,
    checksum: Optional[str],
    integrity: str,
    follow_symlinks: bool,
) -> Tuple[DatasetArtifact, ...]:
    """Capture portable inventory facts once, before any renderer runs."""
    candidates: Dict[Tuple[str, str], Any] = {}
    if adapter_result is not None:
        for artifact in adapter_result.artifacts:
            if not artifact.exists:
                continue
            kind = (
                "data"
                if artifact.kind == "data"
                else "media"
                if artifact.kind in {"media", "video"}
                else "metadata"
            )
            key = (kind, artifact.path)
            candidates.setdefault(key, artifact)
        info_path = root / "meta/info.json"
        if (
            adapter_result.raw_info
            and _safe_media_path(
                info_path,
                root,
                follow_symlinks=follow_symlinks,
            )
        ):
            candidates.setdefault(("metadata", "meta/info.json"), None)

    data_paths = sorted(
        path for kind, path in candidates if kind == "data"
    )
    selected_data = _selected_artifact_paths(data_paths, integrity)
    records: Dict[Tuple[str, str], DatasetArtifact] = {}
    for (kind, relative), artifact in sorted(candidates.items()):
        candidate = root / relative
        if not _safe_media_path(
            candidate,
            root,
            follow_symlinks=follow_symlinks,
        ):
            continue
        try:
            size_bytes = candidate.stat().st_size
        except OSError:
            continue
        include_digest = checksum == "sha256" and (
            kind == "metadata"
            or kind == "media"
            or relative in selected_data
        )
        records[(kind, relative)] = DatasetArtifact(
            kind=kind,
            path=relative,
            size_bytes=size_bytes,
            checksum_sha256=(
                sha256_file(candidate) if include_digest else None
            ),
            row_count=(
                artifact.row_count if artifact is not None else None
            ),
            columns=(
                artifact.columns if artifact is not None else ()
            ),
        )

    for video in videos:
        records[("media", video.path)] = DatasetArtifact(
            kind="media",
            path=video.path,
            size_bytes=video.size_bytes,
            checksum_sha256=video.checksum_sha256,
        )
    return tuple(
        records[key]
        for key in sorted(records, key=lambda item: (item[0], item[1]))
    )


def prepare_dataset(
    path: str,
    input_format: str = "auto",
    checksum: Optional[str] = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
) -> DatasetSnapshot:
    """Build one immutable snapshot shared by all output renderers."""
    _validate_request_options(checksum, integrity)
    root = Path(path).resolve()
    if not root.is_dir():
        raise DatasetNotFoundError(f"Directory not found: {path}")
    resolved_format = detect_input_format(path, input_format)
    if resolved_format == "lerobot":
        adapter_result = read_lerobot_dataset(
            path,
            DiscoveryRequest(
                integrity=integrity,
                checksum=checksum,
                follow_symlinks=follow_symlinks,
            ),
        )
        paths = [
            candidate
            for relative_path in adapter_result.video_paths
            if _safe_media_path(
                candidate := root / relative_path,
                root,
                follow_symlinks=follow_symlinks,
            )
        ]
        episodes = _adapter_episode_records(adapter_result)
        video_keys = adapter_result.video_keys
        findings = tuple(
            thaw_value(value) for value in adapter_result.findings
        )
        codebase_version = adapter_result.declared_version
    else:
        adapter_result = None
        discovery_findings: List[Dict[str, Any]] = []
        paths = _discover_media_paths(
            root,
            root,
            discovery_findings,
            follow_symlinks=follow_symlinks,
        )
        episodes = ()
        video_keys = tuple(
            sorted({_video_key(candidate.relative_to(root).as_posix()) for candidate in paths})
        )
        findings = tuple(discovery_findings)
        codebase_version = None
    videos = _scan_paths(root, paths, checksum, integrity)
    validation_result = (
        validate_prepared_dataset(
            root,
            adapter_result,
            videos,
            integrity,
        )
        if adapter_result is not None
        else None
    )
    return DatasetSnapshot(
        root=root,
        input_format=resolved_format,
        codebase_version=str(codebase_version) if codebase_version is not None else None,
        episodes=episodes,
        video_keys=video_keys,
        videos=videos,
        findings=findings,
        checksum=checksum,
        integrity=integrity,
        follow_symlinks=follow_symlinks,
        adapter_result=adapter_result,
        artifacts=_prepared_artifacts(
            root,
            adapter_result,
            videos,
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
        ),
        validation_result=validation_result,
    )


def dataset_fingerprint(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _finding_sort_key(item: Dict[str, Any]) -> Tuple[str, str, str]:
    return (str(item.get("severity", "")), str(item.get("code", "")), str(item.get("path", "")))


def audit_dataset(
    path: str,
    input_format: str = "auto",
    checksum: Optional[str] = None,
    output_path: Optional[str] = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
    snapshot: DatasetSnapshot | None = None,
) -> Dict[str, Any]:
    """Audit a local dataset using deterministic, evidence-backed rules."""
    run = None
    try:
        prepared = (
            validate_snapshot_request(
                snapshot,
                path,
                input_format,
                checksum,
                integrity,
                follow_symlinks,
            )
            if snapshot is not None
            else prepare_dataset(
                path,
                input_format=input_format,
                checksum=checksum,
                integrity=integrity,
                follow_symlinks=follow_symlinks,
            )
        )
    except DatasetArgumentError as exc:
        findings = list(
            enrich_findings(
                [_finding("DATASET_INVALID_ARGUMENT", "error", str(exc), ".")]
            )
        )
        resolved_format = input_format
        videos: Tuple[VideoRecord, ...] = ()
    except DatasetNotFoundError as exc:
        findings = list(
            enrich_findings(
                [_finding("DATASET_NOT_FOUND", "error", str(exc), ".")]
            )
        )
        resolved_format = input_format
        videos = ()
    else:
        resolved_format = prepared.input_format
        videos = prepared.videos
        seed_findings = list(prepared.findings)
        adapter_result = prepared.adapter_result
        if adapter_result is not None:
            seed_findings.extend(adapter_result.findings)
        validation_result = prepared.validation_result
        if validation_result is not None:
            seed_findings.extend(validation_result.findings)
        run = run_audit_rules(
            prepared,
            seed_findings=seed_findings,
        )
        findings = [dict(item) for item in run.findings]

    counts = {
        severity: sum(item["severity"] == severity for item in findings)
        for severity in ("error", "warning", "info")
    }
    result = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "input_format": resolved_format,
        "summary": {"videos": len(videos), **counts},
        "rule_pack_version": (
            run.rule_pack_version if run is not None else AUDIT_RULE_PACK_VERSION
        ),
        "coverage": (
            {
                "capabilities": [
                    item.as_dict() for item in run.capabilities
                ]
            }
            if run is not None
            else {"capabilities": []}
        ),
        "skipped_checks": (
            [item.as_dict() for item in run.skipped_checks]
            if run is not None
            else []
        ),
        "findings": findings,
    }
    if output_path is not None:
        destination = Path(output_path)
        write_json_atomic(destination, result)
    return result
