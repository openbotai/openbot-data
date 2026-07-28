"""Read-only adapter for the canonical LeRobot v2.1 storage layout."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

from openbot_data.adapters._common import (
    capability,
    dataset_file_status,
    declared_video_keys,
    file_artifact,
    finding,
    nonnegative_integer,
    read_jsonl_objects,
    relative_files,
    render_path_template,
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

DEFAULT_DATA_PATH = (
    "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet"
)
DEFAULT_VIDEO_PATH = (
    "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4"
)
DATA_PATH_FIELDS = ("episode_chunk", "episode_index")
VIDEO_PATH_FIELDS = ("episode_chunk", "episode_index", "video_key")


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


def _tasks(
    probe: FormatProbeResult,
    findings: list[Finding],
    *,
    follow_symlinks: bool,
) -> Tuple[TaskRecord, ...]:
    records = read_jsonl_objects(
        probe.root,
        "meta/tasks.jsonl",
        findings,
        missing_code="LEROBOT_TASKS_MISSING",
        unreadable_code="LEROBOT_TASKS_UNREADABLE",
        invalid_record_code="LEROBOT_TASK_INVALID",
        follow_symlinks=follow_symlinks,
    )
    tasks = []
    for line_number, record in records:
        task_index = nonnegative_integer(record.get("task_index"))
        task = record.get("task")
        if task_index is None or not isinstance(task, str) or not task.strip():
            findings.append(
                finding(
                    "LEROBOT_TASK_INVALID",
                    "error",
                    "metadata",
                    "LeRobot task metadata requires a non-negative task_index and task text.",
                    "meta/tasks.jsonl",
                    {"line": line_number},
                )
            )
            continue
        tasks.append(
            TaskRecord(
                task_index=task_index,
                task=task,
                source_path="meta/tasks.jsonl",
                source_row=line_number,
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


class LeRobotV21Adapter:
    """Direct reader for v2.1 JSONL metadata and episode-per-file payloads."""

    adapter_id = "lerobot_v21"
    major_version = 2
    contract_minor = 1
    capabilities = (
        "alignment.data_relations",
        "alignment.video_relations",
        "data.inventory",
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
        findings.append(
            finding(
                "LEROBOT_V21_MIGRATION_RECOMMENDED",
                "info",
                "provenance",
                "LeRobot v2.1 remains read-only; migrate a reviewed copy with "
                "the pinned official v2.1-to-v3.0 converter before using "
                "LeRobot 0.6.0 training or merge tools.",
                "meta/info.json",
                {
                    "source_contract": "v2.1",
                    "target_contract": "v3.0",
                    "package": "lerobot==0.6.0",
                    "module": "lerobot.scripts.convert_dataset_v21_to_v30",
                    "mutates_source": False,
                    "openbot_will_execute": False,
                },
            )
        )

        canonical_v30 = list(
            relative_files(
                probe.root,
                "meta/episodes/*/*.parquet",
                follow_symlinks=request.follow_symlinks,
            )
        )
        if (
            dataset_file_status(
                probe.root,
                "meta/tasks.parquet",
                follow_symlinks=request.follow_symlinks,
            )
            == "valid"
        ):
            canonical_v30.append("meta/tasks.parquet")
        if canonical_v30:
            findings.append(
                finding(
                    "LEROBOT_LAYOUT_VERSION_MISMATCH",
                    "error",
                    "metadata",
                    "LeRobot v2.1 metadata must use canonical JSONL files.",
                    "meta",
                    {
                        "adapter_id": self.adapter_id,
                        "unexpected_v3_paths": sorted(canonical_v30),
                    },
                )
            )

        task_records = _tasks(
            probe,
            findings,
            follow_symlinks=request.follow_symlinks,
        )
        tasks_exists = (
            dataset_file_status(
                probe.root,
                "meta/tasks.jsonl",
                follow_symlinks=request.follow_symlinks,
            )
            == "valid"
        )
        artifacts.append(
            file_artifact(
                probe.root,
                kind="tasks",
                path="meta/tasks.jsonl",
                source="canonical",
                follow_symlinks=request.follow_symlinks,
                row_count=len(task_records) if tasks_exists else None,
                columns=("task", "task_index"),
            )
        )
        capabilities.append(
            capability(
                "metadata.tasks",
                "complete" if tasks_exists else "failed",
                checked=len(task_records),
                total=len(task_records) if tasks_exists else None,
                reason=None if tasks_exists else "required_file_missing",
            )
        )

        episode_records = read_jsonl_objects(
            probe.root,
            "meta/episodes.jsonl",
            findings,
            missing_code="LEROBOT_EPISODES_MISSING",
            unreadable_code="LEROBOT_EPISODES_UNREADABLE",
            invalid_record_code="LEROBOT_EPISODE_INVALID",
            follow_symlinks=request.follow_symlinks,
        )
        episodes_exists = (
            dataset_file_status(
                probe.root,
                "meta/episodes.jsonl",
                follow_symlinks=request.follow_symlinks,
            )
            == "valid"
        )
        artifacts.append(
            file_artifact(
                probe.root,
                kind="episodes",
                path="meta/episodes.jsonl",
                source="canonical",
                follow_symlinks=request.follow_symlinks,
                row_count=len(episode_records) if episodes_exists else None,
            )
        )

        stats_path = "meta/episodes_stats.jsonl"
        stats_exists = (
            dataset_file_status(
                probe.root,
                stats_path,
                follow_symlinks=request.follow_symlinks,
            )
            == "valid"
        )
        if stats_exists:
            artifacts.append(
                file_artifact(
                    probe.root,
                    kind="stats",
                    path=stats_path,
                    source="canonical",
                    follow_symlinks=request.follow_symlinks,
                )
            )
        capabilities.append(
            capability(
                "metadata.stats",
                "complete" if stats_exists else "unavailable",
                reason=None if stats_exists else "optional_file_missing",
                checked=1 if stats_exists else 0,
                total=1,
            )
        )

        chunks_size = nonnegative_integer(info.get("chunks_size", 1000))
        if chunks_size is None or chunks_size == 0:
            chunks_size = 1000
            findings.append(
                finding(
                    "LEROBOT_METADATA_INVALID",
                    "error",
                    "metadata",
                    "LeRobot chunks_size must be a positive integer.",
                    "meta/info.json",
                    {"field": "chunks_size"},
                )
            )

        data_template = info.get("data_path", DEFAULT_DATA_PATH)
        _example, data_template_error = render_path_template(
            data_template,
            allowed_fields=DATA_PATH_FIELDS,
            required_fields=DATA_PATH_FIELDS,
            values={"episode_chunk": 0, "episode_index": 0},
        )
        if data_template_error is not None:
            findings.append(
                finding(
                    "LEROBOT_DATA_PATH_TEMPLATE_INVALID",
                    "error",
                    "metadata",
                    "LeRobot v2.1 data_path template is invalid.",
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
                values={"episode_chunk": 0, "episode_index": 0, "video_key": "camera"},
            )
            if video_template_error is not None:
                findings.append(
                    finding(
                        "LEROBOT_VIDEO_PATH_TEMPLATE_INVALID",
                        "error",
                        "metadata",
                        "LeRobot v2.1 video_path template is invalid.",
                        "meta/info.json",
                        {
                            "reason": video_template_error,
                            "required_placeholders": sorted(VIDEO_PATH_FIELDS),
                        },
                    )
                )

        episodes = []
        data_checked = 0
        data_total = 0
        videos_checked = 0
        videos_total = 0
        for line_number, record in episode_records:
            episode_index = nonnegative_integer(record.get("episode_index", record.get("index")))
            if episode_index is None:
                findings.append(
                    finding(
                        "LEROBOT_EPISODE_INDEX_INVALID",
                        "error",
                        "metadata",
                        "Episode metadata has no valid episode_index.",
                        "meta/episodes.jsonl",
                        {"line": line_number},
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
                        "meta/episodes.jsonl",
                        {"episode_index": episode_index, "line": line_number},
                    )
                )

            episode_chunk = episode_index // chunks_size
            data_path: Optional[str] = None
            if data_template_error is None:
                data_path, render_error = render_path_template(
                    data_template,
                    allowed_fields=DATA_PATH_FIELDS,
                    required_fields=DATA_PATH_FIELDS,
                    values={
                        "episode_chunk": episode_chunk,
                        "episode_index": episode_index,
                    },
                )
                if render_error is not None:
                    data_path = None
            if data_path is not None:
                data_total += 1
                artifact = file_artifact(
                    probe.root,
                    kind="data",
                    path=data_path,
                    source="declared_relation",
                    follow_symlinks=request.follow_symlinks,
                    episode_index=episode_index,
                )
                artifacts.append(artifact)
                data_checked += int(artifact.exists)
                relations.append(
                    RelationRecord(
                        kind="data",
                        episode_index=episode_index,
                        path=data_path,
                        exists=artifact.exists,
                        chunk_index=episode_chunk,
                    )
                )
                if not artifact.exists:
                    findings.append(
                        finding(
                            "LEROBOT_DATA_MISSING",
                            "error",
                            "data",
                            "LeRobot v2.1 episode data file is missing.",
                            data_path,
                            {"episode_index": episode_index},
                        )
                    )

            video_paths = []
            if video_template is not None and video_template_error is None:
                for video_key in video_keys:
                    video_path, render_error = render_path_template(
                        video_template,
                        allowed_fields=VIDEO_PATH_FIELDS,
                        required_fields=VIDEO_PATH_FIELDS,
                        values={
                            "episode_chunk": episode_chunk,
                            "episode_index": episode_index,
                            "video_key": video_key,
                        },
                    )
                    if render_error is not None or video_path is None:
                        continue
                    videos_total += 1
                    artifact = file_artifact(
                        probe.root,
                        kind="video",
                        path=video_path,
                        source="declared_relation",
                        follow_symlinks=request.follow_symlinks,
                        episode_index=episode_index,
                        feature_key=video_key,
                    )
                    artifacts.append(artifact)
                    videos_checked += int(artifact.exists)
                    relations.append(
                        RelationRecord(
                            kind="video",
                            episode_index=episode_index,
                            path=video_path,
                            exists=artifact.exists,
                            feature_key=video_key,
                            chunk_index=episode_chunk,
                        )
                    )
                    if artifact.exists:
                        video_paths.append(video_path)
                    else:
                        findings.append(
                            finding(
                                "LEROBOT_VIDEO_MISSING",
                                "error",
                                "media",
                                "LeRobot v2.1 episode video file is missing.",
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
                    tasks=_episode_tasks(record.get("tasks", record.get("task", []))),
                    source_path="meta/episodes.jsonl",
                    source_row=line_number,
                    data_path=data_path,
                    video_paths=tuple(sorted(video_paths)),
                    extensions=_record_extensions(
                        record,
                        {"episode_index", "index", "length", "task", "tasks"},
                    ),
                )
            )

        declared_episodes = nonnegative_integer(info.get("total_episodes"))
        if (
            declared_episodes is not None
            and declared_episodes != len(episodes)
        ):
            findings.append(
                finding(
                    "LEROBOT_EPISODE_COUNT_MISMATCH",
                    "error",
                    "alignment",
                    "Declared episode count does not match readable episode metadata.",
                    "meta/info.json",
                    {
                        "declared": declared_episodes,
                        "discovered": len(episodes),
                    },
                )
            )
        if video_keys and not any(
            artifact.kind == "video" and artifact.exists
            for artifact in artifacts
        ):
            findings.append(
                finding(
                    "LEROBOT_VIDEOS_MISSING",
                    "error",
                    "media",
                    "LeRobot declares video features but no referenced videos are readable.",
                    "videos",
                    {"video_keys": list(video_keys)},
                )
            )

        capabilities.extend(
            [
                capability(
                    "metadata.episodes",
                    "complete" if episodes_exists else "failed",
                    checked=len(episodes),
                    total=len(episode_records) if episodes_exists else None,
                    reason=None if episodes_exists else "required_file_missing",
                ),
                capability(
                    "data.inventory",
                    "complete" if data_checked == data_total else "partial",
                    checked=data_checked,
                    total=data_total,
                ),
                capability(
                    "alignment.data_relations",
                    "complete" if data_checked == data_total else "partial",
                    checked=data_checked,
                    total=data_total,
                ),
                capability(
                    "media.inventory",
                    "complete" if videos_checked == videos_total else "partial",
                    checked=videos_checked,
                    total=videos_total,
                ),
                capability(
                    "alignment.video_relations",
                    "complete" if videos_checked == videos_total else "partial",
                    checked=videos_checked,
                    total=videos_total,
                ),
            ]
        )

        unique_artifacts = tuple(
            sorted(
                set(artifacts),
                key=lambda item: (
                    item.kind,
                    item.path,
                    item.episode_index if item.episode_index is not None else -1,
                    item.feature_key or "",
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
            tasks=task_records,
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


ADAPTER = LeRobotV21Adapter()
