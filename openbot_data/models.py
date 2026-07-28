"""Typed internal records for deterministic dataset inspection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EpisodeRecord:
    """One logical episode and the shared media segments that contain it."""

    episode_index: int
    length: int | None
    tasks: tuple[str, ...] = ()
    video_files: tuple[str, ...] = ()
    video_segments: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "episode_index": self.episode_index,
            "length": self.length,
            "tasks": list(self.tasks),
            "video_files": list(self.video_files),
        }
        if self.video_segments:
            result["video_segments"] = [dict(item) for item in self.video_segments]
        return result


@dataclass(frozen=True)
class VideoRecord:
    """A scanned local video with a private source path and portable metadata."""

    source_path: Path = field(repr=False, compare=False)
    path: str
    filename: str
    stream: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float
    size_bytes: int
    size_mb: float
    metadata_valid: bool
    decode_valid: bool | None
    integrity_level: str
    decoded_frame_count: int | None = None
    error: str | None = None
    checksum_sha256: str | None = None
    raw_fps: float | None = field(default=None, repr=False, compare=False)
    raw_duration: float | None = field(default=None, repr=False, compare=False)

    @property
    def is_valid(self) -> bool:
        """Compatibility alias for callers that still consume ``is_valid``."""
        return self.metadata_valid and self.decode_valid is not False

    def as_dict(self, *, include_private: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path_base": "dataset",
            "path": self.path,
            "filename": self.filename,
            "stream": self.stream,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "duration": self.duration,
            "size_bytes": self.size_bytes,
            "size_mb": self.size_mb,
            "metadata_valid": self.metadata_valid,
            "decode_valid": self.decode_valid,
            "integrity_level": self.integrity_level,
            "is_valid": self.is_valid,
            "error": self.error,
        }
        if self.decoded_frame_count is not None:
            result["decoded_frame_count"] = self.decoded_frame_count
        if self.checksum_sha256 is not None:
            result["checksum_sha256"] = self.checksum_sha256
        if include_private:
            result["_source_path"] = str(self.source_path)
        return result


@dataclass(frozen=True)
class DatasetArtifact:
    """One file observation captured during the prepared discovery pass."""

    kind: str
    path: str
    size_bytes: int
    checksum_sha256: str | None = None
    row_count: int | None = None
    columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatasetSnapshot:
    """Immutable discovery result shared by manifest, preview and audit renderers."""

    root: Path = field(repr=False, compare=False)
    input_format: str
    codebase_version: str | None
    episodes: tuple[EpisodeRecord, ...]
    video_keys: tuple[str, ...]
    videos: tuple[VideoRecord, ...]
    findings: tuple[dict[str, Any], ...]
    checksum: str | None = None
    integrity: str = "sample"
    follow_symlinks: bool = False
    adapter_result: Any | None = field(default=None, repr=False, compare=False)
    artifacts: tuple[DatasetArtifact, ...] = field(
        default=(),
        repr=False,
        compare=False,
    )
    validation_result: Any | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def as_legacy_dict(self) -> dict[str, Any]:
        """Return the old internal shape while renderers migrate to typed records."""
        return {
            "input_format": self.input_format,
            "codebase_version": self.codebase_version,
            "episodes": [episode.as_dict() for episode in self.episodes],
            "video_keys": list(self.video_keys),
            "videos": [video.as_dict(include_private=True) for video in self.videos],
            "findings": [dict(item) for item in self.findings],
        }
