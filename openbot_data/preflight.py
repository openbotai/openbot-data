"""Deterministic local dataset discovery and audit helpers."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import cv2

from openbot_data.errors import DatasetArgumentError, DatasetNotFoundError
from openbot_data.models import DatasetSnapshot, EpisodeRecord, VideoRecord
from openbot_data.serialization import write_json_atomic
from openbot_data.video import VIDEO_EXTENSIONS, scan_video

MANIFEST_SCHEMA_VERSION = "openbot.dataset_manifest.v1"
AUDIT_SCHEMA_VERSION = "openbot.dataset_audit.v1"
SUPPORTED_INPUT_FORMATS = {"auto", "video", "lerobot"}
SUPPORTED_CHECKSUMS = {None, "sha256"}
SUPPORTED_INTEGRITY_LEVELS = {"metadata", "sample", "full"}
DEFAULT_V3_VIDEO_PATH = (
    "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
)


def _safe_error(error: object, root: Path) -> str:
    return str(error).replace(str(root), ".")


def _is_within_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _safe_media_path(candidate: Path, root: Path, *, follow_symlinks: bool) -> bool:
    if candidate.is_symlink() and not follow_symlinks:
        return False
    return candidate.is_file() and _is_within_root(candidate, root)


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
        if not _safe_media_path(candidate, root, follow_symlinks=follow_symlinks):
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                relative = candidate.name
            findings.append(
                _finding(
                    "DATASET_PATH_OUTSIDE_ROOT",
                    "error",
                    "Dataset media must resolve inside the dataset root.",
                    relative,
                    {"symlink": candidate.is_symlink()},
                )
            )
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
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in sorted(paths):
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
) -> List[Dict[str, Any]]:
    parquet_paths = sorted(paths)
    if not parquet_paths:
        return []
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
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


def _normalize_declared_path(path: str) -> str | None:
    normalized = Path(path).as_posix().lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        return None
    return normalized


def _v3_video_segments(
    raw: Dict[str, Any],
    info: Dict[str, Any],
    video_keys: Iterable[str],
    root: Path,
    episode_index: int,
    findings: List[Dict[str, Any]],
    *,
    follow_symlinks: bool,
) -> List[Dict[str, Any]]:
    template = info.get("video_path") or DEFAULT_V3_VIDEO_PATH
    if not isinstance(template, str):
        template = DEFAULT_V3_VIDEO_PATH
    segments: List[Dict[str, Any]] = []
    nested_videos = raw.get("videos")
    for video_key in video_keys:
        chunk = raw.get(f"videos/{video_key}/chunk_index")
        file_index = raw.get(f"videos/{video_key}/file_index")
        from_timestamp = raw.get(f"videos/{video_key}/from_timestamp")
        to_timestamp = raw.get(f"videos/{video_key}/to_timestamp")
        explicit_path: str | None = None
        if isinstance(nested_videos, dict):
            nested = nested_videos.get(video_key)
            if isinstance(nested, dict):
                chunk = nested.get("chunk_index", chunk)
                file_index = nested.get("file_index", file_index)
                from_timestamp = nested.get("from_timestamp", from_timestamp)
                to_timestamp = nested.get("to_timestamp", to_timestamp)
                if isinstance(nested.get("path"), str):
                    explicit_path = str(nested["path"])
            elif isinstance(nested, str):
                explicit_path = nested

        if explicit_path is None:
            try:
                if chunk is None or file_index is None:
                    raise KeyError("chunk_index/file_index")
                explicit_path = template.format(
                    video_key=video_key,
                    chunk_index=int(chunk),
                    file_index=int(file_index),
                )
            except (KeyError, TypeError, ValueError, IndexError):
                findings.append(
                    _finding(
                        "LEROBOT_VIDEO_RELATION_MISSING",
                        "error",
                        "LeRobot v3 episode has no relational video shard reference.",
                        "meta/episodes",
                        {"episode_index": episode_index, "video_key": video_key},
                    )
                )
                continue

        normalized = _normalize_declared_path(explicit_path)
        candidate = root / normalized if normalized is not None else root.parent
        if (
            normalized is None
            or not _safe_media_path(candidate, root, follow_symlinks=follow_symlinks)
        ):
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_MISSING",
                    "error",
                    "Episode references a video shard that is missing or outside the dataset root.",
                    normalized or str(explicit_path),
                    {"episode_index": episode_index, "video_key": video_key},
                )
            )
            continue
        segment: Dict[str, Any] = {
            "video_key": video_key,
            "path_base": "dataset",
            "path": normalized,
        }
        if from_timestamp is not None:
            segment["from_timestamp"] = float(from_timestamp)
        if to_timestamp is not None:
            segment["to_timestamp"] = float(to_timestamp)
        segments.append(segment)
    return segments


def _v2_video_files(
    raw: Dict[str, Any],
    videos: Iterable[str],
    root: Path,
    episode_index: int,
    findings: List[Dict[str, Any]],
    *,
    follow_symlinks: bool,
) -> List[str]:
    token = f"episode_{episode_index:06d}"
    matched = [item for item in videos if token in Path(item).stem]
    explicit_paths: List[str] = []
    raw_videos = raw.get("videos")
    if isinstance(raw_videos, dict):
        explicit_paths.extend(str(value) for value in raw_videos.values() if isinstance(value, str))
    raw_video_path = raw.get("video_path")
    if isinstance(raw_video_path, str):
        explicit_paths.append(raw_video_path)
    for explicit in explicit_paths:
        normalized = _normalize_declared_path(explicit)
        candidate = root / normalized if normalized is not None else root.parent
        if (
            normalized is None
            or not _safe_media_path(candidate, root, follow_symlinks=follow_symlinks)
        ):
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_MISSING",
                    "error",
                    "Episode references a video file that is missing or outside the dataset root.",
                    normalized or explicit,
                    {"episode_index": episode_index},
                )
            )
        elif normalized not in matched:
            matched.append(normalized)
    return sorted(set(matched))


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
    if not info_path.is_file():
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
    raw_episodes = _read_jsonl(episode_paths, root, findings)
    raw_episodes.extend(_read_episode_parquet(parquet_paths, root, findings))
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
    video_keys = sorted(set(_declared_video_keys(info)) | {_video_key(item) for item in videos})
    is_v3 = _is_lerobot_v3(info, raw_episodes)

    episodes: List[Dict[str, Any]] = []
    for ordinal, raw in enumerate(raw_episodes):
        raw_index = raw.get("episode_index", raw.get("index"))
        try:
            if raw_index is None:
                raise TypeError
            episode_index = int(raw_index)
        except (TypeError, ValueError):
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

        tasks_value = raw.get("tasks", raw.get("task", []))
        if isinstance(tasks_value, str):
            tasks = [tasks_value]
        elif isinstance(tasks_value, list):
            tasks = sorted(str(task) for task in tasks_value)
        else:
            tasks = []

        if is_v3:
            segments = _v3_video_segments(
                raw,
                info,
                video_keys,
                root,
                episode_index,
                findings,
                follow_symlinks=follow_symlinks,
            )
            matched = sorted({str(item["path"]) for item in segments})
        else:
            segments = []
            matched = _v2_video_files(
                raw,
                videos,
                root,
                episode_index,
                findings,
                follow_symlinks=follow_symlinks,
            )

        episode = EpisodeRecord(
            episode_index=episode_index,
            length=int(raw["length"]) if isinstance(raw.get("length"), int) else None,
            tasks=tuple(tasks),
            video_files=tuple(matched),
            video_segments=tuple(segments),
        )
        episodes.append(episode.as_dict())
        if video_keys and not matched:
            findings.append(
                _finding(
                    "LEROBOT_VIDEO_MISSING",
                    "error",
                    "LeRobot episode has no matching local video file.",
                    "videos",
                    {"episode_index": episode_index, "video_keys": video_keys},
                )
            )

    episodes.sort(key=lambda item: item["episode_index"])
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
            ok, _frame = capture.read()
            return bool(ok), 1 if ok else 0
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
        decode_valid, decoded_frames = _decode_probe(candidate, integrity, info.frame_count)
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
            )
        )
    return tuple(records)


def prepare_dataset(
    path: str,
    input_format: str = "auto",
    checksum: Optional[str] = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
) -> DatasetSnapshot:
    """Build one immutable snapshot shared by all output renderers."""
    if checksum not in SUPPORTED_CHECKSUMS:
        raise DatasetArgumentError("checksum must be omitted or 'sha256'")
    if integrity not in SUPPORTED_INTEGRITY_LEVELS:
        raise DatasetArgumentError(
            f"integrity must be one of {sorted(SUPPORTED_INTEGRITY_LEVELS)}"
        )
    root = Path(path).resolve()
    if not root.is_dir():
        raise DatasetNotFoundError(f"Directory not found: {path}")
    resolved_format = detect_input_format(path, input_format)
    if resolved_format == "lerobot":
        discovery = read_lerobot(path, follow_symlinks=follow_symlinks)
        paths = [root / item for item in discovery["videos"]]
        episodes = tuple(
            EpisodeRecord(
                episode_index=int(item["episode_index"]),
                length=int(item["length"]) if isinstance(item.get("length"), int) else None,
                tasks=tuple(str(task) for task in item.get("tasks", [])),
                video_files=tuple(str(value) for value in item.get("video_files", [])),
                video_segments=tuple(
                    dict(value)
                    for value in item.get("video_segments", [])
                    if isinstance(value, dict)
                ),
            )
            for item in discovery["episodes"]
        )
        video_keys = tuple(str(value) for value in discovery["video_keys"])
        findings = tuple(dict(value) for value in discovery["findings"])
        codebase_version = discovery["codebase_version"]
    else:
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
    return DatasetSnapshot(
        root=root,
        input_format=resolved_format,
        codebase_version=str(codebase_version) if codebase_version is not None else None,
        episodes=episodes,
        video_keys=video_keys,
        videos=_scan_paths(root, paths, checksum, integrity),
        findings=findings,
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
    try:
        prepared = snapshot or prepare_dataset(
            path,
            input_format=input_format,
            checksum=checksum,
            integrity=integrity,
            follow_symlinks=follow_symlinks,
        )
    except DatasetArgumentError as exc:
        findings = [
            _finding("DATASET_INVALID_ARGUMENT", "error", str(exc), ".")
        ]
        resolved_format = input_format
        videos: Tuple[VideoRecord, ...] = ()
    except DatasetNotFoundError as exc:
        findings = [_finding("DATASET_NOT_FOUND", "error", str(exc), ".")]
        resolved_format = input_format
        videos = ()
    else:
        resolved_format = prepared.input_format
        videos = prepared.videos
        findings = [dict(item) for item in prepared.findings]

    checksums: Dict[str, List[str]] = defaultdict(list)
    streams: Dict[str, List[VideoRecord]] = defaultdict(list)
    for video in videos:
        relative = video.path
        streams[video.stream].append(video)
        if not video.metadata_valid:
            findings.append(
                _finding(
                    "VIDEO_UNREADABLE",
                    "error",
                    "Video metadata is incomplete or invalid.",
                    relative,
                    {"error": video.error},
                )
            )
        if video.fps <= 0:
            findings.append(
                _finding("VIDEO_INVALID_FPS", "error", "Video FPS must be positive.", relative)
            )
        if video.duration <= 0:
            findings.append(
                _finding(
                    "VIDEO_INVALID_DURATION",
                    "error",
                    "Video duration must be positive.",
                    relative,
                )
            )
        if video.width <= 0 or video.height <= 0:
            findings.append(
                _finding(
                    "VIDEO_INVALID_DIMENSIONS",
                    "error",
                    "Video dimensions must be positive.",
                    relative,
                )
            )
        if video.decode_valid is False:
            findings.append(
                _finding(
                    "VIDEO_PREVIEW_DECODE_FAILED",
                    "error",
                    "Video decoding did not satisfy the requested integrity level.",
                    relative,
                    {
                        "integrity_level": video.integrity_level,
                        "decoded_frame_count": video.decoded_frame_count,
                    },
                )
            )
        digest = video.checksum_sha256
        if isinstance(digest, str):
            checksums[digest].append(relative)

    for stream, records in sorted(streams.items()):
        valid = [record for record in records if record.is_valid]
        resolutions = sorted({(record.width, record.height) for record in valid})
        fps_values = sorted({round(record.fps, 3) for record in valid})
        if len(resolutions) > 1:
            findings.append(
                _finding(
                    "STREAM_INCONSISTENT_RESOLUTION",
                    "warning",
                    "Videos in one camera stream use different resolutions.",
                    stream,
                    {"resolutions": [list(value) for value in resolutions]},
                )
            )
        if len(fps_values) > 1:
            findings.append(
                _finding(
                    "STREAM_INCONSISTENT_FPS",
                    "warning",
                    "Videos in one camera stream use different FPS values.",
                    stream,
                    {"fps": fps_values},
                )
            )

    for digest, paths in sorted(checksums.items()):
        if len(paths) > 1:
            findings.append(
                _finding(
                    "DUPLICATE_CONTENT",
                    "warning",
                    "Multiple files have identical SHA-256 content.",
                    sorted(paths)[0],
                    {"checksum_sha256": digest, "paths": sorted(paths)},
                )
            )

    findings = sorted(findings, key=_finding_sort_key)
    counts = {
        severity: sum(item["severity"] == severity for item in findings)
        for severity in ("error", "warning", "info")
    }
    result = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "input_format": resolved_format,
        "summary": {"videos": len(videos), **counts},
        "findings": findings,
    }
    if output_path is not None:
        destination = Path(output_path)
        write_json_atomic(destination, result)
    return result
