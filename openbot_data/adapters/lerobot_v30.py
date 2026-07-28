"""Read-only adapter for the stable LeRobot 0.6.0 / dataset v3.0 layout."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

from openbot_data.adapters._common import (
    VIDEO_SUFFIXES,
    capability,
    dataset_file_status,
    declared_video_keys,
    file_artifact,
    finding,
    finite_number,
    nonnegative_integer,
    read_json_object,
    relative_files,
    render_path_template,
    safe_error,
    sorted_capabilities,
    sorted_findings,
)
from openbot_data.adapters.base import (
    AdapterResult,
    ArtifactRecord,
    CapabilityStatus,
    DiscoveryRequest,
    EpisodeMetadata,
    Finding,
    FormatProbeResult,
    RelationRecord,
    TaskRecord,
    freeze_value,
)

DEFAULT_DATA_PATH = "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
DEFAULT_VIDEO_PATH = (
    "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
)
DATA_PATH_FIELDS = ("chunk_index", "file_index")
VIDEO_PATH_FIELDS = ("video_key", "chunk_index", "file_index")


def _record_extensions(
    record: Mapping[str, Any],
    known_fields: set[str],
) -> Mapping[str, Any]:
    return freeze_value(
        {
            str(key): value
            for key, value in record.items()
            if str(key) not in known_fields
        }
    )


def _load_parquet_module() -> Optional[Any]:
    try:
        parquet = __import__("pyarrow.parquet", fromlist=["ParquetFile"])
    except ImportError:
        return None
    return parquet


def _parquet_rows(
    root: Path,
    paths: Sequence[str],
    parquet: Any,
    findings: list[Finding],
    *,
    role: str,
    unreadable_code: str,
    batch_size: int,
    follow_symlinks: bool,
) -> Tuple[list[Tuple[str, int, dict[str, Any]]], list[ArtifactRecord], int]:
    rows: list[Tuple[str, int, dict[str, Any]]] = []
    artifacts: list[ArtifactRecord] = []
    readable_files = 0
    for relative_path in sorted(paths):
        path = root / relative_path
        try:
            parquet_file = parquet.ParquetFile(path)
            columns = tuple(str(name) for name in parquet_file.schema_arrow.names)
            row_count = int(parquet_file.metadata.num_rows)
            row_number = 0
            for batch in parquet_file.iter_batches(batch_size=batch_size):
                for value in batch.to_pylist():
                    row_number += 1
                    if isinstance(value, dict):
                        rows.append((relative_path, row_number, dict(value)))
                    else:
                        findings.append(
                            finding(
                                unreadable_code,
                                "error",
                                "metadata" if role in {"episodes", "tasks"} else "data",
                                "LeRobot parquet row is not an object.",
                                relative_path,
                                {"row": row_number, "artifact_role": role},
                            )
                        )
            artifacts.append(
                file_artifact(
                    root,
                    kind=role,
                    path=relative_path,
                    source="canonical",
                    follow_symlinks=follow_symlinks,
                    row_count=row_count,
                    columns=columns,
                )
            )
            readable_files += 1
        except Exception as exc:
            artifacts.append(
                file_artifact(
                    root,
                    kind=role,
                    path=relative_path,
                    source="canonical",
                    follow_symlinks=follow_symlinks,
                )
            )
            findings.append(
                finding(
                    unreadable_code,
                    "error",
                    "metadata" if role in {"episodes", "tasks"} else "data",
                    "LeRobot parquet footer or row groups could not be read.",
                    relative_path,
                    {
                        "artifact_role": role,
                        "reason": "parquet_open_or_read_failed",
                        "error": safe_error(exc, root),
                    },
                )
            )
    return rows, artifacts, readable_files


def _parquet_inventory(
    root: Path,
    paths: Sequence[str],
    parquet: Any,
    findings: list[Finding],
    *,
    follow_symlinks: bool,
) -> Tuple[list[ArtifactRecord], int]:
    artifacts: list[ArtifactRecord] = []
    readable = 0
    for relative_path in sorted(paths):
        try:
            parquet_file = parquet.ParquetFile(root / relative_path)
            columns = tuple(str(name) for name in parquet_file.schema_arrow.names)
            row_count = int(parquet_file.metadata.num_rows)
            artifacts.append(
                file_artifact(
                    root,
                    kind="data",
                    path=relative_path,
                    source="canonical",
                    follow_symlinks=follow_symlinks,
                    row_count=row_count,
                    columns=columns,
                )
            )
            readable += 1
        except Exception as exc:
            artifacts.append(
                file_artifact(
                    root,
                    kind="data",
                    path=relative_path,
                    source="canonical",
                    follow_symlinks=follow_symlinks,
                )
            )
            findings.append(
                finding(
                    "LEROBOT_DATA_UNREADABLE",
                    "error",
                    "data",
                    "LeRobot data parquet footer could not be read.",
                    relative_path,
                    {
                        "reason": "parquet_footer_unreadable",
                        "error": safe_error(exc, root),
                    },
                )
            )
    return artifacts, readable


def _tasks(
    rows: Sequence[Tuple[str, int, dict[str, Any]]],
    findings: list[Finding],
) -> Tuple[TaskRecord, ...]:
    tasks = []
    for source_path, row_number, record in rows:
        task_index = nonnegative_integer(record.get("task_index"))
        task = record.get("task")
        if task_index is None or not isinstance(task, str) or not task.strip():
            findings.append(
                finding(
                    "LEROBOT_TASK_INVALID",
                    "error",
                    "metadata",
                    "LeRobot task metadata requires a non-negative task_index and task text.",
                    source_path,
                    {"row": row_number},
                )
            )
            continue
        tasks.append(
            TaskRecord(
                task_index=task_index,
                task=task,
                source_path=source_path,
                source_row=row_number,
                extensions=_record_extensions(record, {"task_index", "task"}),
            )
        )
    return tuple(sorted(tasks, key=lambda item: (item.task_index, item.task)))


def _episode_tasks(value: object) -> Tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(sorted(str(task) for task in value))
    return ()


def _actual_video_paths(
    root: Path,
    *,
    follow_symlinks: bool,
) -> Tuple[str, ...]:
    return tuple(
        path
        for path in relative_files(
            root,
            "videos/**/*",
            follow_symlinks=follow_symlinks,
        )
        if Path(path).suffix.lower() in VIDEO_SUFFIXES
    )


class LeRobotV30Adapter:
    """Direct reader for canonical v3 Parquet metadata and shared payload shards."""

    adapter_id = "lerobot_v30"
    major_version = 3
    contract_minor = 0
    capabilities = (
        "alignment.data_relations",
        "alignment.video_relations",
        "data.inventory",
        "data.parquet_footer",
        "media.inventory",
        "metadata.episodes",
        "metadata.info",
        "metadata.stats",
        "metadata.tasks",
    )

    def read(
        self,
        probe: FormatProbeResult,
        request: DiscoveryRequest,
    ) -> AdapterResult:
        findings = list(probe.findings)
        info = probe.raw_info
        artifacts: list[ArtifactRecord] = [
            file_artifact(
                probe.root,
                kind="metadata",
                path="meta/info.json",
                source="canonical",
                follow_symlinks=request.follow_symlinks,
            )
        ]
        relations: list[RelationRecord] = []
        capabilities: list[CapabilityStatus] = [
            capability("metadata.info", "complete", checked=1, total=1)
        ]

        legacy_paths = list(
            relative_files(
                probe.root,
                "meta/episodes.jsonl",
                follow_symlinks=request.follow_symlinks,
            )
        )
        legacy_paths.extend(
            relative_files(
                probe.root,
                "meta/episodes/**/*.jsonl",
                follow_symlinks=request.follow_symlinks,
            )
        )
        legacy_paths.extend(
            relative_files(
                probe.root,
                "meta/tasks.jsonl",
                follow_symlinks=request.follow_symlinks,
            )
        )
        if legacy_paths:
            findings.append(
                finding(
                    "LEROBOT_LAYOUT_VERSION_MISMATCH",
                    "error",
                    "metadata",
                    "LeRobot v3 metadata must use canonical Parquet files; "
                    "legacy JSONL was not read.",
                    "meta",
                    {
                        "adapter_id": self.adapter_id,
                        "legacy_paths": sorted(set(legacy_paths)),
                        "records_mixed": False,
                    },
                )
            )

        task_paths = list(
            relative_files(
                probe.root,
                "meta/tasks.parquet",
                follow_symlinks=request.follow_symlinks,
            )
        )
        episode_paths = list(
            relative_files(
                probe.root,
                "meta/episodes/*/*.parquet",
                follow_symlinks=request.follow_symlinks,
            )
        )
        data_paths = list(
            relative_files(
                probe.root,
                "data/*/*.parquet",
                follow_symlinks=request.follow_symlinks,
            )
        )
        total_frames = nonnegative_integer(info.get("total_frames"))
        data_required = total_frames is not None and total_frames > 0
        if not task_paths:
            findings.append(
                finding(
                    "LEROBOT_TASKS_MISSING",
                    "error",
                    "metadata",
                    "LeRobot v3 meta/tasks.parquet is missing.",
                    "meta/tasks.parquet",
                )
            )
            artifacts.append(
                file_artifact(
                    probe.root,
                    kind="tasks",
                    path="meta/tasks.parquet",
                    source="canonical",
                    follow_symlinks=request.follow_symlinks,
                )
            )
        if not episode_paths:
            findings.append(
                finding(
                    "LEROBOT_EPISODES_MISSING",
                    "error",
                    "metadata",
                    "LeRobot v3 episode parquet metadata is missing.",
                    "meta/episodes",
                )
            )
        if not data_paths and data_required:
            findings.append(
                finding(
                    "LEROBOT_DATA_MISSING",
                    "error",
                    "data",
                    "LeRobot v3 declares frames but has no canonical data parquet shards.",
                    "data",
                    {"declared_total_frames": total_frames},
                )
            )

        stats = read_json_object(
            probe.root,
            "meta/stats.json",
            findings,
            unreadable_code="LEROBOT_STATS_INVALID",
            follow_symlinks=request.follow_symlinks,
        )
        stats_exists = (
            dataset_file_status(
                probe.root,
                "meta/stats.json",
                follow_symlinks=request.follow_symlinks,
            )
            == "valid"
        )
        if stats_exists:
            artifacts.append(
                file_artifact(
                    probe.root,
                    kind="stats",
                    path="meta/stats.json",
                    source="canonical",
                    follow_symlinks=request.follow_symlinks,
                )
            )
        capabilities.append(
            capability(
                "metadata.stats",
                "complete"
                if stats is not None
                else "failed" if stats_exists else "unavailable",
                reason=(
                    None
                    if stats is not None
                    else "invalid_optional_file" if stats_exists else "optional_file_missing"
                ),
                checked=1 if stats is not None else 0,
                total=1,
            )
        )

        parquet = _load_parquet_module()
        if parquet is None:
            findings.append(
                finding(
                    "LEROBOT_DEPENDENCY_MISSING",
                    "error",
                    "metadata",
                    "Reading canonical LeRobot v3 parquet requires the optional lerobot extra.",
                    (task_paths or episode_paths or ["meta/tasks.parquet"])[0],
                    {"install": "pip install 'openbot-data[lerobot]'"},
                )
            )
            capabilities.extend(
                [
                    capability(
                        "metadata.tasks",
                        "skipped",
                        reason="pyarrow_unavailable",
                        checked=0,
                        total=len(task_paths),
                    ),
                    capability(
                        "metadata.episodes",
                        "skipped",
                        reason="pyarrow_unavailable",
                        checked=0,
                        total=len(episode_paths),
                    ),
                    capability(
                        "data.inventory",
                        "failed" if data_required and not data_paths else "complete",
                        checked=len(data_paths),
                        total=len(data_paths),
                        reason=(
                            "required_payload_missing"
                            if data_required and not data_paths
                            else None
                        ),
                    ),
                    capability(
                        "data.parquet_footer",
                        "skipped",
                        reason="pyarrow_unavailable",
                        checked=0,
                        total=len(data_paths),
                    ),
                    capability(
                        "alignment.data_relations",
                        "skipped",
                        reason="episode_metadata_unavailable",
                    ),
                    capability(
                        "alignment.video_relations",
                        "skipped",
                        reason="episode_metadata_unavailable",
                    ),
                ]
            )
            for path in task_paths:
                artifacts.append(
                    file_artifact(
                        probe.root,
                        kind="tasks",
                        path=path,
                        source="canonical",
                        follow_symlinks=request.follow_symlinks,
                    )
                )
            for path in episode_paths:
                artifacts.append(
                    file_artifact(
                        probe.root,
                        kind="episodes",
                        path=path,
                        source="canonical",
                        follow_symlinks=request.follow_symlinks,
                    )
                )
            for path in data_paths:
                artifacts.append(
                    file_artifact(
                        probe.root,
                        kind="data",
                        path=path,
                        source="canonical",
                        follow_symlinks=request.follow_symlinks,
                    )
                )
            return self._result(
                probe,
                episodes=(),
                tasks=(),
                artifacts=artifacts,
                relations=relations,
                capabilities=capabilities,
                findings=findings,
            )

        task_rows, task_artifacts, readable_task_files = _parquet_rows(
            probe.root,
            task_paths,
            parquet,
            findings,
            role="tasks",
            unreadable_code="LEROBOT_TASKS_UNREADABLE",
            batch_size=request.parquet_batch_size,
            follow_symlinks=request.follow_symlinks,
        )
        episode_rows, episode_artifacts, readable_episode_files = _parquet_rows(
            probe.root,
            episode_paths,
            parquet,
            findings,
            role="episodes",
            unreadable_code="LEROBOT_EPISODES_UNREADABLE",
            batch_size=request.parquet_batch_size,
            follow_symlinks=request.follow_symlinks,
        )
        data_artifacts, readable_data_files = _parquet_inventory(
            probe.root,
            data_paths,
            parquet,
            findings,
            follow_symlinks=request.follow_symlinks,
        )
        artifacts.extend(task_artifacts)
        artifacts.extend(episode_artifacts)
        artifacts.extend(data_artifacts)
        task_records = _tasks(task_rows, findings)
        capabilities.extend(
            [
                capability(
                    "metadata.tasks",
                    "complete"
                    if task_paths and readable_task_files == len(task_paths)
                    else "failed" if not task_paths else "partial",
                    checked=readable_task_files,
                    total=len(task_paths),
                ),
                capability(
                    "metadata.episodes",
                    "complete"
                    if episode_paths and readable_episode_files == len(episode_paths)
                    else "failed" if not episode_paths else "partial",
                    checked=readable_episode_files,
                    total=len(episode_paths),
                ),
                capability(
                    "data.inventory",
                    "failed" if data_required and not data_paths else "complete",
                    checked=len(data_paths),
                    total=len(data_paths),
                    reason=(
                        "required_payload_missing"
                        if data_required and not data_paths
                        else None
                    ),
                ),
                capability(
                    "data.parquet_footer",
                    "failed"
                    if data_required and not data_paths
                    else (
                        "complete"
                        if readable_data_files == len(data_paths)
                        else "partial"
                    ),
                    checked=readable_data_files,
                    total=len(data_paths),
                    reason=(
                        "required_payload_missing"
                        if data_required and not data_paths
                        else None
                    ),
                ),
            ]
        )

        data_template = info.get("data_path", DEFAULT_DATA_PATH)
        _example, data_template_error = render_path_template(
            data_template,
            allowed_fields=DATA_PATH_FIELDS,
            required_fields=DATA_PATH_FIELDS,
            values={"chunk_index": 0, "file_index": 0},
        )
        if data_template_error is not None:
            findings.append(
                finding(
                    "LEROBOT_DATA_PATH_TEMPLATE_INVALID",
                    "error",
                    "metadata",
                    "LeRobot v3 data_path template is invalid.",
                    "meta/info.json",
                    {
                        "reason": data_template_error,
                        "required_placeholders": sorted(DATA_PATH_FIELDS),
                    },
                )
            )

        video_keys = declared_video_keys(info)
        raw_video_template = info.get("video_path", DEFAULT_VIDEO_PATH)
        video_template: Optional[object] = raw_video_template
        video_template_error: Optional[str] = None
        if not video_keys and raw_video_template is None:
            video_template = None
        elif video_keys:
            _example, video_template_error = render_path_template(
                raw_video_template,
                allowed_fields=VIDEO_PATH_FIELDS,
                required_fields=VIDEO_PATH_FIELDS,
                values={"video_key": "camera", "chunk_index": 0, "file_index": 0},
            )
            if video_template_error is not None:
                findings.append(
                    finding(
                        "LEROBOT_VIDEO_PATH_TEMPLATE_INVALID",
                        "error",
                        "metadata",
                        "LeRobot v3 video_path template is invalid.",
                        "meta/info.json",
                        {
                            "reason": video_template_error,
                            "required_placeholders": sorted(VIDEO_PATH_FIELDS),
                        },
                    )
                )

        actual_data = set(data_paths)
        actual_videos = set(
            _actual_video_paths(
                probe.root,
                follow_symlinks=request.follow_symlinks,
            )
        )
        for path in sorted(actual_videos):
            artifacts.append(
                file_artifact(
                    probe.root,
                    kind="video",
                    path=path,
                    source="canonical",
                    follow_symlinks=request.follow_symlinks,
                )
            )

        episodes = []
        valid_data_relations = 0
        total_data_relations = 0
        valid_video_relations = 0
        total_video_relations = 0
        known_episode_fields = {
            "episode_index",
            "length",
            "tasks",
            "data/chunk_index",
            "data/file_index",
            "dataset_from_index",
            "dataset_to_index",
            "meta/episodes/chunk_index",
            "meta/episodes/file_index",
        }
        for source_path, row_number, record in episode_rows:
            episode_index = nonnegative_integer(record.get("episode_index"))
            if episode_index is None:
                findings.append(
                    finding(
                        "LEROBOT_EPISODE_INDEX_INVALID",
                        "error",
                        "metadata",
                        "Episode metadata has no valid episode_index.",
                        source_path,
                        {"row": row_number},
                    )
                )
                continue

            raw_length = record.get("length")
            length = nonnegative_integer(raw_length) if "length" in record else None
            if "length" in record and length is None:
                findings.append(
                    finding(
                        "LEROBOT_EPISODE_LENGTH_INVALID",
                        "error",
                        "metadata",
                        "Episode length must be a non-negative integer.",
                        source_path,
                        {"episode_index": episode_index, "row": row_number},
                    )
                )

            data_path: Optional[str] = None
            data_chunk = record.get("data/chunk_index")
            data_file = record.get("data/file_index")
            dataset_from = record.get("dataset_from_index")
            dataset_to = record.get("dataset_to_index")
            missing_data_fields = [
                name
                for name, value in (
                    ("data/chunk_index", data_chunk),
                    ("data/file_index", data_file),
                    ("dataset_from_index", dataset_from),
                    ("dataset_to_index", dataset_to),
                )
                if value is None
            ]
            total_data_relations += 1
            data_chunk_index = nonnegative_integer(data_chunk)
            data_file_index = nonnegative_integer(data_file)
            dataset_from_index = nonnegative_integer(dataset_from)
            dataset_to_index = nonnegative_integer(dataset_to)
            if missing_data_fields:
                findings.append(
                    finding(
                        "LEROBOT_DATA_RELATION_MISSING",
                        "error",
                        "alignment",
                        "LeRobot v3 episode lacks a complete data-shard relation.",
                        source_path,
                        {
                            "episode_index": episode_index,
                            "row": row_number,
                            "missing": sorted(missing_data_fields),
                        },
                    )
                )
            else:
                invalid_data_fields = [
                    name
                    for name, value in (
                        ("data/chunk_index", data_chunk_index),
                        ("data/file_index", data_file_index),
                        ("dataset_from_index", dataset_from_index),
                        ("dataset_to_index", dataset_to_index),
                    )
                    if value is None
                ]
                if (
                    dataset_from_index is not None
                    and dataset_to_index is not None
                    and dataset_from_index > dataset_to_index
                ):
                    invalid_data_fields.append("dataset_index_order")
                if invalid_data_fields:
                    findings.append(
                        finding(
                            "LEROBOT_DATA_RELATION_INVALID",
                            "error",
                            "alignment",
                            "LeRobot v3 data-shard relation contains invalid indexes.",
                            source_path,
                            {
                                "episode_index": episode_index,
                                "row": row_number,
                                "invalid_fields": sorted(invalid_data_fields),
                            },
                        )
                    )
                elif data_template_error is None:
                    data_path, render_error = render_path_template(
                        data_template,
                        allowed_fields=DATA_PATH_FIELDS,
                        required_fields=DATA_PATH_FIELDS,
                        values={
                            "chunk_index": data_chunk_index,
                            "file_index": data_file_index,
                        },
                    )
                    if render_error is None and data_path is not None:
                        exists = data_path in actual_data
                        relations.append(
                            RelationRecord(
                                kind="data",
                                episode_index=episode_index,
                                path=data_path,
                                exists=exists,
                                chunk_index=data_chunk_index,
                                file_index=data_file_index,
                                dataset_from_index=dataset_from_index,
                                dataset_to_index=dataset_to_index,
                            )
                        )
                        if exists:
                            valid_data_relations += 1
                        else:
                            artifacts.append(
                                file_artifact(
                                    probe.root,
                                    kind="data",
                                    path=data_path,
                                    source="declared_relation",
                                    follow_symlinks=request.follow_symlinks,
                                    episode_index=episode_index,
                                )
                            )
                            findings.append(
                                finding(
                                    "LEROBOT_DATA_MISSING",
                                    "error",
                                    "data",
                                    "Episode references a data shard that does not exist.",
                                    data_path,
                                    {"episode_index": episode_index},
                                )
                            )

            video_paths = []
            for video_key in video_keys:
                total_video_relations += 1
                known_episode_fields.update(
                    {
                        f"videos/{video_key}/chunk_index",
                        f"videos/{video_key}/file_index",
                        f"videos/{video_key}/from_timestamp",
                        f"videos/{video_key}/to_timestamp",
                    }
                )
                chunk_value = record.get(f"videos/{video_key}/chunk_index")
                file_value = record.get(f"videos/{video_key}/file_index")
                from_value = record.get(f"videos/{video_key}/from_timestamp")
                to_value = record.get(f"videos/{video_key}/to_timestamp")
                missing_video_fields = [
                    name
                    for name, value in (
                        ("chunk_index", chunk_value),
                        ("file_index", file_value),
                        ("from_timestamp", from_value),
                        ("to_timestamp", to_value),
                    )
                    if value is None
                ]
                if missing_video_fields:
                    findings.append(
                        finding(
                            "LEROBOT_VIDEO_RELATION_MISSING",
                            "error",
                            "alignment",
                            "LeRobot v3 episode lacks a complete video-shard relation.",
                            source_path,
                            {
                                "episode_index": episode_index,
                                "video_key": video_key,
                                "row": row_number,
                                "missing": sorted(missing_video_fields),
                            },
                        )
                    )
                    continue
                chunk_index = nonnegative_integer(chunk_value)
                file_index = nonnegative_integer(file_value)
                from_timestamp = finite_number(from_value)
                to_timestamp = finite_number(to_value)
                invalid_video_fields = [
                    name
                    for name, value in (
                        ("chunk_index", chunk_index),
                        ("file_index", file_index),
                        ("from_timestamp", from_timestamp),
                        ("to_timestamp", to_timestamp),
                    )
                    if value is None
                ]
                if (
                    from_timestamp is not None
                    and to_timestamp is not None
                    and (
                        from_timestamp < 0
                        or to_timestamp < 0
                        or from_timestamp >= to_timestamp
                    )
                ):
                    invalid_video_fields.append("timestamp_order")
                if invalid_video_fields:
                    findings.append(
                        finding(
                            "LEROBOT_VIDEO_RELATION_INVALID",
                            "error",
                            "alignment",
                            "LeRobot v3 video-shard relation contains invalid values.",
                            source_path,
                            {
                                "episode_index": episode_index,
                                "video_key": video_key,
                                "row": row_number,
                                "invalid_fields": sorted(invalid_video_fields),
                            },
                        )
                    )
                    continue
                if video_template is None or video_template_error is not None:
                    continue
                video_path, render_error = render_path_template(
                    video_template,
                    allowed_fields=VIDEO_PATH_FIELDS,
                    required_fields=VIDEO_PATH_FIELDS,
                    values={
                        "video_key": video_key,
                        "chunk_index": chunk_index,
                        "file_index": file_index,
                    },
                )
                if render_error is not None or video_path is None:
                    continue
                exists = video_path in actual_videos
                relations.append(
                    RelationRecord(
                        kind="video",
                        episode_index=episode_index,
                        path=video_path,
                        exists=exists,
                        feature_key=video_key,
                        chunk_index=chunk_index,
                        file_index=file_index,
                        from_timestamp=from_timestamp,
                        to_timestamp=to_timestamp,
                    )
                )
                if exists:
                    valid_video_relations += 1
                    video_paths.append(video_path)
                else:
                    artifacts.append(
                        file_artifact(
                            probe.root,
                            kind="video",
                            path=video_path,
                            source="declared_relation",
                            follow_symlinks=request.follow_symlinks,
                            episode_index=episode_index,
                            feature_key=video_key,
                        )
                    )
                    findings.append(
                        finding(
                            "LEROBOT_VIDEO_MISSING",
                            "error",
                            "media",
                            "Episode references a video shard that does not exist.",
                            video_path,
                            {
                                "episode_index": episode_index,
                                "video_key": video_key,
                            },
                        )
                    )

            episodes.append(
                EpisodeMetadata(
                    episode_index=episode_index,
                    length=length,
                    tasks=_episode_tasks(record.get("tasks", [])),
                    source_path=source_path,
                    source_row=row_number,
                    data_path=data_path,
                    dataset_from_index=dataset_from_index,
                    dataset_to_index=dataset_to_index,
                    video_paths=tuple(sorted(video_paths)),
                    extensions=_record_extensions(record, known_episode_fields),
                )
            )

        capabilities.extend(
            [
                capability(
                    "alignment.data_relations",
                    "complete"
                    if valid_data_relations == total_data_relations
                    else "partial",
                    checked=valid_data_relations,
                    total=total_data_relations,
                ),
                capability(
                    "alignment.video_relations",
                    "complete"
                    if valid_video_relations == total_video_relations
                    else "partial",
                    checked=valid_video_relations,
                    total=total_video_relations,
                ),
                capability(
                    "media.inventory",
                    "complete",
                    checked=len(actual_videos),
                    total=len(actual_videos),
                ),
            ]
        )
        return self._result(
            probe,
            episodes=tuple(episodes),
            tasks=task_records,
            artifacts=artifacts,
            relations=relations,
            capabilities=capabilities,
            findings=findings,
        )

    def _result(
        self,
        probe: FormatProbeResult,
        *,
        episodes: Sequence[EpisodeMetadata],
        tasks: Sequence[TaskRecord],
        artifacts: Sequence[ArtifactRecord],
        relations: Sequence[RelationRecord],
        capabilities: Sequence[CapabilityStatus],
        findings: Sequence[Finding],
    ) -> AdapterResult:
        actual_videos = tuple(
            sorted(
                {
                    artifact.path
                    for artifact in artifacts
                    if artifact.kind == "video" and artifact.exists
                }
            )
        )
        if not any(capability_item.name == "media.inventory" for capability_item in capabilities):
            capabilities = tuple(capabilities) + (
                capability(
                    "media.inventory",
                    "complete",
                    checked=len(actual_videos),
                    total=len(actual_videos),
                ),
            )
        unique_artifacts = tuple(
            sorted(
                set(artifacts),
                key=lambda item: (
                    item.kind,
                    item.path,
                    item.episode_index if item.episode_index is not None else -1,
                    item.feature_key or "",
                    item.source,
                ),
            )
        )
        return AdapterResult(
            adapter_id=self.adapter_id,
            declared_version=probe.declared_version,
            compatibility=probe.compatibility,
            raw_info=probe.raw_info,
            episodes=tuple(
                sorted(episodes, key=lambda item: (item.episode_index, item.source_row))
            ),
            tasks=tuple(sorted(tasks, key=lambda item: (item.task_index, item.task))),
            artifacts=unique_artifacts,
            relations=tuple(
                sorted(
                    set(relations),
                    key=lambda item: (
                        item.kind,
                        item.episode_index,
                        item.feature_key or "",
                        item.path,
                    ),
                )
            ),
            capabilities=sorted_capabilities(capabilities),
            findings=sorted_findings(findings),
        )


ADAPTER = LeRobotV30Adapter()
