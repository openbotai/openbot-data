"""Portable, deterministic dataset snapshot artifacts."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, cast
from urllib.parse import unquote, urlsplit, urlunsplit

from openbot_data import __version__
from openbot_data.adapters._common import dataset_file_status
from openbot_data.adapters.base import thaw_value
from openbot_data.audit import infer_snapshot_capabilities
from openbot_data.errors import DatasetArgumentError
from openbot_data.models import DatasetSnapshot, VideoRecord
from openbot_data.preflight import (
    dataset_fingerprint,
    prepare_dataset,
    validate_snapshot_request,
)
from openbot_data.serialization import write_json_atomic

SNAPSHOT_SCHEMA_VERSION = "openbot.dataset_snapshot.v1"
SNAPSHOT_FINGERPRINT_VERSION = "openbot.dataset_snapshot.fingerprint.v1"
LEROBOT_COMPATIBILITY_TARGET = "lerobot==0.6.0"
SUPPORTED_SOURCE_KINDS = {"local", "hf_hub"}
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_UNC = re.compile(r"^(?:[\\]{2}|//)")
_BEARER_CREDENTIAL = re.compile(
    r"(?:^|[\s:])bearer\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)
_HF_ACCESS_TOKEN = re.compile(
    r"(?:^|[^A-Za-z0-9])hf_[A-Za-z0-9]{16,}(?:$|[^A-Za-z0-9])"
)
_HUB_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_PRIVATE_KEY_PARTS = {
    "authorization",
    "credential",
    "credentials",
    "passwd",
    "password",
    "passwords",
    "secret",
    "secrets",
    "token",
    "tokens",
}
_PRIVATE_COMPOUND_KEYS = {
    "accesskey",
    "apikey",
    "clientsecret",
    "privatekey",
    "sessionkey",
    "signingkey",
}


def _private_key(key: str) -> bool:
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    parts = [
        part.lower()
        for part in re.split(r"[^A-Za-z0-9]+", expanded)
        if part
    ]
    return bool(
        set(parts) & _PRIVATE_KEY_PARTS
        or "".join(parts) in _PRIVATE_COMPOUND_KEYS
    )


def _portable_string(value: str) -> str:
    if _BEARER_CREDENTIAL.search(value) or _HF_ACCESS_TOKEN.search(value):
        return "[redacted]"
    try:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.netloc:
            hostname = parsed.hostname or ""
            netloc = hostname
            if parsed.port is not None:
                netloc = f"{netloc}:{parsed.port}"
            return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except ValueError:
        return "[private-path-redacted]"
    if (
        Path(value).is_absolute()
        or _WINDOWS_ABSOLUTE.match(value)
        or _WINDOWS_UNC.match(value)
    ):
        return "[private-path-redacted]"
    return value


def _portable_json(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _private_key(key):
        return "[redacted]"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _portable_string(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if value == float("inf"):
            return "Infinity"
        if value == float("-inf"):
            return "-Infinity"
        return value
    if isinstance(value, (list, tuple)):
        return [_portable_json(item) for item in value]
    if isinstance(value, dict):
        return {
            str(item_key): _portable_json(item_value, key=str(item_key))
            for item_key, item_value in sorted(
                value.items(),
                key=lambda item: str(item[0]).encode("utf-8"),
            )
        }
    return str(value)


def _read_info(root: Path, *, follow_symlinks: bool) -> Dict[str, Any]:
    path = root / "meta" / "info.json"
    if (
        dataset_file_status(
            root,
            "meta/info.json",
            follow_symlinks=follow_symlinks,
        )
        != "valid"
    ):
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _adapter_id(input_format: str, codebase_version: str | None) -> str:
    if input_format == "video":
        return "video_directory"
    normalized = (codebase_version or "").lower().lstrip("v")
    if normalized == "2.1" or normalized.startswith("2.1."):
        return "lerobot_v21"
    if normalized == "3.0" or normalized.startswith("3.0."):
        return "lerobot_v30"
    if normalized.startswith("2."):
        return "lerobot_v21_unverified_minor"
    if normalized.startswith("3."):
        return "lerobot_v30_unverified_minor"
    return "lerobot_unknown"


def _normalized_features(info: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw_features = info.get("features")
    if not isinstance(raw_features, dict):
        return []
    features: list[Dict[str, Any]] = []
    for key, raw in sorted(raw_features.items(), key=lambda item: str(item[0])):
        if isinstance(raw, dict):
            normalized = {
                "key": str(key),
                "dtype": (
                    str(raw["dtype"]) if raw.get("dtype") is not None else None
                ),
                "shape": _portable_json(raw.get("shape", [])),
                "names": _portable_json(raw.get("names", [])),
                "metadata": _portable_json(
                    {
                        item_key: item_value
                        for item_key, item_value in raw.items()
                        if item_key not in {"dtype", "shape", "names"}
                    }
                ),
            }
        else:
            normalized = {
                "key": str(key),
                "dtype": None,
                "shape": [],
                "names": [],
                "metadata": {"declared_value": _portable_json(raw)},
            }
        features.append(normalized)
    return features


def _episode_digests(snapshot: DatasetSnapshot) -> Dict[int, Dict[str, Any]]:
    validation = snapshot.validation_result
    measurements = (
        getattr(validation, "measurements", {})
        if validation is not None
        else {}
    )
    raw = (
        measurements.get("episode_content_digests", ())
        if isinstance(measurements, Mapping)
        else ()
    )
    return {
        int(item["episode_index"]): dict(item)
        for item in raw
        if (
            isinstance(item, Mapping)
            and isinstance(item.get("episode_index"), int)
            and item.get("coverage") == "complete"
            and isinstance(item.get("digest"), str)
        )
    }


def _normalized_tasks(snapshot: DatasetSnapshot) -> list[Dict[str, Any]]:
    adapter_result = snapshot.adapter_result
    if adapter_result is not None and adapter_result.tasks:
        return [
            {
                "task_index": task.task_index,
                "task": task.task,
                "source": {
                    "path": task.source_path,
                    "row": task.source_row,
                },
                "extensions": _portable_json(thaw_value(task.extensions)),
            }
            for task in adapter_result.tasks
        ]
    return [
        {
            "task_index": None,
            "task": task,
            "source": None,
            "extensions": {},
        }
        for task in sorted(
            {task for episode in snapshot.episodes for task in episode.tasks}
        )
    ]


def _normalized_episodes(snapshot: DatasetSnapshot) -> list[Dict[str, Any]]:
    adapter_result = snapshot.adapter_result
    adapter_episodes = (
        list(adapter_result.episodes) if adapter_result is not None else []
    )
    adapter_source_ordinals = {
        id(episode): ordinal
        for ordinal, episode in enumerate(
            sorted(
                adapter_episodes,
                key=lambda item: (
                    item.source_path,
                    item.source_row,
                    item.episode_index,
                ),
            )
        )
    }
    digests = _episode_digests(snapshot)
    normalized = []
    ordered = sorted(
        enumerate(snapshot.episodes),
        key=lambda item: (item[1].episode_index, item[0]),
    )
    for projection_index, episode in ordered:
        adapter_episode = (
            adapter_episodes[projection_index]
            if projection_index < len(adapter_episodes)
            else None
        )
        digest = digests.get(episode.episode_index)
        normalized.append(
            {
                "episode_index": episode.episode_index,
                "source_ordinal": (
                    adapter_source_ordinals[id(adapter_episode)]
                    if adapter_episode is not None
                    else projection_index
                ),
                "length": episode.length,
                "tasks": sorted(episode.tasks),
                "video_files": sorted(episode.video_files),
                "video_segments": sorted(
                    (
                        cast(
                            dict[str, Any],
                            _portable_json(dict(segment)),
                        )
                        for segment in episode.video_segments
                    ),
                    key=lambda segment: (
                        str(segment.get("video_key", "")),
                        str(segment.get("path", "")),
                        float(segment.get("from_timestamp", 0.0)),
                    ),
                ),
                "source": (
                    {
                        "path": adapter_episode.source_path,
                        "row": adapter_episode.source_row,
                    }
                    if adapter_episode is not None
                    else None
                ),
                "data_relation": (
                    {
                        "path": adapter_episode.data_path,
                        "dataset_from_index": (
                            adapter_episode.dataset_from_index
                        ),
                        "dataset_to_index": adapter_episode.dataset_to_index,
                    }
                    if (
                        adapter_episode is not None
                        and adapter_episode.data_path is not None
                    )
                    else None
                ),
                "extensions": (
                    _portable_json(thaw_value(adapter_episode.extensions))
                    if adapter_episode is not None
                    else {}
                ),
                "content_sha256": (
                    str(digest["digest"]) if digest is not None else None
                ),
                "content_rows": (
                    int(digest["rows"])
                    if digest is not None
                    and isinstance(digest.get("rows"), int)
                    else None
                ),
            }
        )
    return normalized


def _frame_total(
    snapshot: DatasetSnapshot,
    info: Mapping[str, Any],
) -> int:
    if snapshot.input_format == "video":
        return sum(video.frame_count for video in snapshot.videos)
    data_artifacts = [
        artifact
        for artifact in snapshot.artifacts
        if artifact.kind == "data"
    ]
    if data_artifacts and all(
        artifact.row_count is not None for artifact in data_artifacts
    ):
        return sum(int(artifact.row_count or 0) for artifact in data_artifacts)
    if snapshot.episodes and all(
        episode.length is not None for episode in snapshot.episodes
    ):
        return sum(int(episode.length or 0) for episode in snapshot.episodes)
    declared = info.get("total_frames")
    if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 0:
        return declared
    return 0


def _duration_total(
    snapshot: DatasetSnapshot,
    info: Mapping[str, Any],
    frames: int,
) -> float:
    if snapshot.input_format == "video":
        return round(
            sum(
                video.raw_duration
                if video.raw_duration is not None
                else video.duration
                for video in snapshot.videos
            ),
            6,
        )
    declared = info.get("total_duration")
    if (
        isinstance(declared, (int, float))
        and not isinstance(declared, bool)
        and math.isfinite(float(declared))
        and float(declared) >= 0
    ):
        return round(float(declared), 6)
    fps = info.get("fps")
    if (
        isinstance(fps, (int, float))
        and not isinstance(fps, bool)
        and math.isfinite(float(fps))
        and float(fps) > 0
    ):
        return round(frames / float(fps), 6)
    return 0.0


def _video_streams(videos: Iterable[VideoRecord]) -> list[Dict[str, Any]]:
    grouped: Dict[str, list[VideoRecord]] = {}
    for video in videos:
        grouped.setdefault(video.stream, []).append(video)
    streams: list[Dict[str, Any]] = []
    for stream, records in sorted(grouped.items()):
        ordered = sorted(records, key=lambda item: item.path)
        streams.append(
            {
                "key": stream,
                "paths": [record.path for record in ordered],
                "resolutions": [
                    list(value)
                    for value in sorted(
                        {(record.width, record.height) for record in ordered}
                    )
                ],
                "fps": sorted(
                    {
                        (
                            record.raw_fps
                            if record.raw_fps is not None
                            else record.fps
                        )
                        for record in ordered
                    }
                ),
                "frame_count": sum(record.frame_count for record in ordered),
                "duration_seconds": round(
                    sum(
                        (
                            record.raw_duration
                            if record.raw_duration is not None
                            else record.duration
                        )
                        for record in ordered
                    ),
                    6,
                ),
                "size_bytes": sum(record.size_bytes for record in ordered),
            }
        )
    return streams


def _inventory(
    snapshot: DatasetSnapshot,
    checksum: Optional[str],
    integrity: str,
) -> Dict[str, list[Dict[str, Any]]]:
    del checksum, integrity
    metadata = [
        {
            "path": artifact.path,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.checksum_sha256,
        }
        for artifact in snapshot.artifacts
        if artifact.kind == "metadata"
    ]
    data = [
        {
            "path": artifact.path,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.checksum_sha256,
        }
        for artifact in snapshot.artifacts
        if artifact.kind == "data"
    ]
    media = [
        {
            "path": video.path,
            "size_bytes": video.size_bytes,
            "sha256": video.checksum_sha256,
            "stream": video.stream,
            "frame_count": video.frame_count,
            "fps": video.raw_fps if video.raw_fps is not None else video.fps,
            "duration_seconds": (
                video.raw_duration
                if video.raw_duration is not None
                else video.duration
            ),
            "resolution": [video.width, video.height],
        }
        for video in sorted(snapshot.videos, key=lambda item: item.path)
    ]
    return {"metadata": metadata, "data": data, "media": media}


def _coverage(
    snapshot: DatasetSnapshot,
    inventory: Dict[str, list[Dict[str, Any]]],
    checksum: Optional[str],
    integrity: str,
) -> Dict[str, Any]:
    capability_records = [
        item.as_dict() for item in infer_snapshot_capabilities(snapshot)
    ]
    completed = {
        str(item["capability"])
        for item in capability_records
        if item["status"] == "complete"
    }
    if inventory["metadata"]:
        completed.add("metadata.inventory")
    skipped: list[Dict[str, str]] = [
        {
            "capability": str(item["capability"]),
            "reason_code": str(
                item.get("reason_code")
                or (
                    "partial_capability_coverage"
                    if item["status"] == "partial"
                    else "source_capability_unavailable"
                )
            ),
            "reason": str(
                item.get("reason")
                or (
                    "Only part of this capability was checked."
                    if item["status"] == "partial"
                    else "This capability was not completed."
                )
            ),
        }
        for item in capability_records
        if item["status"] != "complete"
    ]
    episode_indexes = [
        episode.episode_index
        for episode in sorted(
            snapshot.episodes,
            key=lambda item: item.episode_index,
        )
    ]
    if integrity == "metadata":
        selected_episodes: list[int] = []
    elif integrity == "full" or len(episode_indexes) <= 3:
        selected_episodes = episode_indexes
    else:
        selected_episodes = [
            episode_indexes[index]
            for index in sorted(
                {0, (len(episode_indexes) - 1) // 2, len(episode_indexes) - 1}
            )
        ]
    return {
        "requested_integrity": integrity,
        "checksum": checksum,
        "capabilities": capability_records,
        "completed_capabilities": sorted(completed),
        "skipped_capabilities": sorted(
            skipped,
            key=lambda item: (item["capability"], item["reason_code"]),
        ),
        "selection": {
            "episodes": selected_episodes,
            "cameras": sorted(snapshot.video_keys),
            "metadata_shards": [
                item["path"] for item in inventory["metadata"]
            ],
            "data_shards": [
                item["path"] for item in inventory["data"] if item["sha256"] is not None
            ],
            "media_shards": [
                item["path"] for item in inventory["media"] if item["sha256"] is not None
            ],
        },
        "totals": {
            "episodes": len(snapshot.episodes),
            "cameras": len(snapshot.video_keys),
            "metadata_shards": len(inventory["metadata"]),
            "data_shards": len(inventory["data"]),
            "media_shards": len(inventory["media"]),
        },
    }


def _component_fingerprints(components: Dict[str, Any]) -> Dict[str, str]:
    return {
        key: dataset_fingerprint(value)
        for key, value in sorted(components.items())
    }


def build_dataset_snapshot(
    path: str,
    *,
    input_format: str = "auto",
    checksum: Optional[str] = "sha256",
    integrity: str = "sample",
    follow_symlinks: bool = False,
    source_kind: str = "local",
    source_locator: str | None = None,
    requested_revision: str | None = None,
    resolved_revision: str | None = None,
    source_coverage: Mapping[str, Any] | None = None,
    snapshot: DatasetSnapshot | None = None,
    output_path: str | None = None,
) -> Dict[str, Any]:
    """Build a secret-free snapshot without changing manifest v1 identity."""
    normalized_source_kind = source_kind.strip().lower()
    if normalized_source_kind not in SUPPORTED_SOURCE_KINDS:
        raise DatasetArgumentError(
            f"source_kind must be one of {sorted(SUPPORTED_SOURCE_KINDS)}"
        )
    if (
        normalized_source_kind == "hf_hub"
        and (
            resolved_revision is None
            or _HUB_COMMIT.fullmatch(resolved_revision) is None
        )
    ):
        raise DatasetArgumentError(
            "resolved_revision must be an immutable 40-character Hub commit"
        )
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
    locator = source_locator or (
        "dataset://."
        if normalized_source_kind == "local"
        else "hf://datasets/unknown"
    )
    decoded_locator = unquote(locator)
    try:
        parsed_locator = urlsplit(decoded_locator)
    except ValueError as exc:
        raise DatasetArgumentError(
            "source_locator must be a valid portable locator"
        ) from exc
    if (
        _BEARER_CREDENTIAL.search(decoded_locator)
        or _HF_ACCESS_TOKEN.search(decoded_locator)
        or parsed_locator.username is not None
        or parsed_locator.password is not None
    ):
        raise DatasetArgumentError(
            "source_locator must not contain credentials"
        )
    portable_locator = _portable_string(locator)
    if portable_locator == "[redacted]":
        raise DatasetArgumentError(
            "source_locator must not contain credentials"
        )
    if portable_locator == "[private-path-redacted]":
        portable_locator = "dataset://."

    adapter_result = prepared.adapter_result
    info = (
        thaw_value(adapter_result.raw_info)
        if adapter_result is not None
        else _read_info(
            prepared.root,
            follow_symlinks=prepared.follow_symlinks,
        )
    )
    episodes = _normalized_episodes(prepared)
    tasks = _normalized_tasks(prepared)
    features = _normalized_features(info)
    streams = _video_streams(prepared.videos)
    inventory = _inventory(prepared, checksum, integrity)
    coverage = _coverage(prepared, inventory, checksum, integrity)
    if source_coverage is not None:
        coverage["source"] = _portable_json(dict(source_coverage))
    source = {
        "kind": normalized_source_kind,
        "locator": portable_locator,
        "requested_revision": requested_revision,
        "resolved_revision": resolved_revision,
    }
    format_contract = {
        "input_format": prepared.input_format,
        "adapter": (
            str(adapter_result.adapter_id)
            if adapter_result is not None and adapter_result.adapter_id is not None
            else _adapter_id(prepared.input_format, prepared.codebase_version)
        ),
        "dataset_format_version": prepared.codebase_version,
        "compatibility_target": (
            LEROBOT_COMPATIBILITY_TARGET
            if prepared.input_format == "lerobot"
            else None
        ),
        "metadata": _portable_json(info),
    }
    contract = {
        "features": features,
        "tasks": tasks,
        "episodes": episodes,
        "video_streams": streams,
    }
    frames = _frame_total(prepared, info)
    totals = {
        "episodes": len(episodes),
        "tasks": len(tasks),
        "features": len(features),
        "video_streams": len(streams),
        "metadata_shards": len(inventory["metadata"]),
        "data_shards": len(inventory["data"]),
        "media_shards": len(inventory["media"]),
        "frames": frames,
        "duration_seconds": _duration_total(prepared, info, frames),
        "size_bytes": sum(
            int(item["size_bytes"])
            for group in inventory.values()
            for item in group
        ),
    }
    components = {
        "source": source,
        "format": format_contract,
        "features": features,
        "tasks": contract["tasks"],
        "episodes": episodes,
        "video_streams": streams,
        "totals": totals,
        "metadata_inventory": inventory["metadata"],
        "data_inventory": inventory["data"],
        "media_inventory": inventory["media"],
        "coverage": coverage,
    }
    component_fingerprints = _component_fingerprints(components)
    fingerprint_payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "fingerprint_version": SNAPSHOT_FINGERPRINT_VERSION,
        "source": source,
        "format": format_contract,
        "contract": contract,
        "inventory": inventory,
        "totals": totals,
        "coverage": coverage,
        "component_fingerprints": component_fingerprints,
    }
    result = {
        **fingerprint_payload,
        "tool": {"name": "openbot-data", "version": __version__},
        "snapshot_fingerprint": dataset_fingerprint(fingerprint_payload),
    }
    if output_path is not None:
        write_json_atomic(Path(output_path), result)
    return result
