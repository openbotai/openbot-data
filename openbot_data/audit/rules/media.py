"""Built-in media rules that operate only on prepared snapshot records."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from openbot_data.audit.models import (
    FindingDraft,
    RuleContext,
    StaticAuditRule,
)
from openbot_data.audit.registry import get_rule_spec


def _unreadable(context: RuleContext) -> Iterable[FindingDraft]:
    for video in context.snapshot.videos:
        if not video.metadata_valid:
            yield FindingDraft(
                code="VIDEO_UNREADABLE",
                message="Video metadata is incomplete or invalid.",
                path=video.path,
                location={"video_key": video.stream},
                evidence={"error": video.error},
            )


def _invalid_fps(context: RuleContext) -> Iterable[FindingDraft]:
    for video in context.snapshot.videos:
        if video.fps <= 0:
            yield FindingDraft(
                code="VIDEO_INVALID_FPS",
                message="Video FPS must be positive.",
                path=video.path,
                location={"video_key": video.stream},
                evidence={"fps": video.fps},
            )


def _invalid_duration(context: RuleContext) -> Iterable[FindingDraft]:
    for video in context.snapshot.videos:
        if video.duration <= 0:
            yield FindingDraft(
                code="VIDEO_INVALID_DURATION",
                message="Video duration must be positive.",
                path=video.path,
                location={"video_key": video.stream},
                evidence={"duration": video.duration},
            )


def _invalid_dimensions(context: RuleContext) -> Iterable[FindingDraft]:
    for video in context.snapshot.videos:
        if video.width <= 0 or video.height <= 0:
            yield FindingDraft(
                code="VIDEO_INVALID_DIMENSIONS",
                message="Video dimensions must be positive.",
                path=video.path,
                location={"video_key": video.stream},
                evidence={"width": video.width, "height": video.height},
            )


def _decode_failed(context: RuleContext) -> Iterable[FindingDraft]:
    for video in context.snapshot.videos:
        if video.decode_valid is False:
            yield FindingDraft(
                code="VIDEO_PREVIEW_DECODE_FAILED",
                message="Video decoding did not satisfy the requested integrity level.",
                path=video.path,
                location={"video_key": video.stream},
                evidence={
                    "integrity_level": video.integrity_level,
                    "decoded_frame_count": video.decoded_frame_count,
                },
            )


def _inconsistent_resolution(context: RuleContext) -> Iterable[FindingDraft]:
    streams = defaultdict(list)
    for video in context.snapshot.videos:
        streams[video.stream].append(video)
    for stream, records in sorted(streams.items()):
        valid = [record for record in records if record.is_valid]
        resolutions = sorted({(record.width, record.height) for record in valid})
        if len(resolutions) > 1:
            yield FindingDraft(
                code="STREAM_INCONSISTENT_RESOLUTION",
                message="Videos in one camera stream use different resolutions.",
                path=stream,
                location={"video_key": stream},
                evidence={"resolutions": [list(value) for value in resolutions]},
            )


def _inconsistent_fps(context: RuleContext) -> Iterable[FindingDraft]:
    streams = defaultdict(list)
    for video in context.snapshot.videos:
        streams[video.stream].append(video)
    for stream, records in sorted(streams.items()):
        valid = [record for record in records if record.is_valid]
        fps_values = sorted({round(record.fps, 3) for record in valid})
        if len(fps_values) > 1:
            yield FindingDraft(
                code="STREAM_INCONSISTENT_FPS",
                message="Videos in one camera stream use different FPS values.",
                path=stream,
                location={"video_key": stream},
                evidence={"fps": fps_values},
            )


def _duplicate_content(context: RuleContext) -> Iterable[FindingDraft]:
    checksums = defaultdict(list)
    for video in context.snapshot.videos:
        if video.checksum_sha256 is not None:
            checksums[video.checksum_sha256].append(video.path)
    for digest, paths in sorted(checksums.items()):
        ordered_paths = sorted(paths)
        if len(ordered_paths) > 1:
            yield FindingDraft(
                code="DUPLICATE_CONTENT",
                message="Multiple files have identical SHA-256 content.",
                path=ordered_paths[0],
                evidence={
                    "checksum_sha256": digest,
                    "paths": ordered_paths,
                },
            )


MEDIA_RULES = (
    StaticAuditRule(get_rule_spec("VIDEO_UNREADABLE"), _unreadable),
    StaticAuditRule(get_rule_spec("VIDEO_INVALID_FPS"), _invalid_fps),
    StaticAuditRule(get_rule_spec("VIDEO_INVALID_DURATION"), _invalid_duration),
    StaticAuditRule(get_rule_spec("VIDEO_INVALID_DIMENSIONS"), _invalid_dimensions),
    StaticAuditRule(get_rule_spec("VIDEO_PREVIEW_DECODE_FAILED"), _decode_failed),
    StaticAuditRule(
        get_rule_spec("STREAM_INCONSISTENT_RESOLUTION"),
        _inconsistent_resolution,
    ),
    StaticAuditRule(get_rule_spec("STREAM_INCONSISTENT_FPS"), _inconsistent_fps),
    StaticAuditRule(get_rule_spec("DUPLICATE_CONTENT"), _duplicate_content),
)
