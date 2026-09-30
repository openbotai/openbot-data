"""Local, read-only episode review for LeRobot v3 datasets.

The reviewer reuses OpenBot Data's prepared discovery and audit. Video previews
are temporary files outside the dataset; no request handler writes to the source.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
from collections import OrderedDict
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Optional
from urllib.parse import parse_qs, urlsplit

from openbot_data.errors import DatasetArgumentError
from openbot_data.preflight import audit_dataset, prepare_dataset

_MAX_TRACE_POINTS = 1500
_MAX_PREVIEW_SECONDS = 180.0
_MAX_CACHE_BYTES = 256 * 1024 * 1024
_MAX_CACHE_FILES = 32
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _inside(root: Path, relative: str) -> Path:
    """Resolve only existing, regular, in-root files without following symlinks."""
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise ValueError("invalid dataset-relative path")
    candidate = root
    for part in raw.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("symlinked dataset file is not available for review")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("dataset file is outside the review root") from exc
    if not resolved.is_file():
        raise FileNotFoundError(relative)
    return resolved


def _numbers(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)):
        value = [value]
    result: list[float] = []
    for item in value[:16]:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return []
        if not math.isfinite(number):
            return []
        result.append(number)
    return result


class ReviewWorkspace:
    """One immutable audit view and a bounded temporary preview cache."""

    def __init__(self, path: str, *, ffmpeg: Optional[str] = None):
        self.root = Path(path).expanduser().resolve()
        self.snapshot = prepare_dataset(
            str(self.root), input_format="lerobot", integrity="metadata"
        )
        result = self.snapshot.adapter_result
        if result is None or result.adapter_id != "lerobot_v30":
            raise DatasetArgumentError("review currently supports local LeRobot v3 datasets")
        self.audit = audit_dataset(
            str(self.root), input_format="lerobot", integrity="metadata",
            snapshot=self.snapshot,
        )
        self.episodes = {episode.episode_index: episode for episode in self.snapshot.episodes}
        self.metadata = {episode.episode_index: episode for episode in result.episodes}
        related: dict[str, set[int]] = {}
        for relation in result.relations:
            if relation.kind in ("data", "video") and relation.episode_index in self.episodes:
                related.setdefault(relation.path, set()).add(relation.episode_index)
        self._episode_findings: dict[int, list[dict[str, Any]]] = {
            index: [] for index in self.episodes
        }
        self._general_findings: list[dict[str, Any]] = []
        for finding in self.audit["findings"]:
            index = (finding.get("location") or {}).get("episode_index")
            affected = (
                {index} if index in self.episodes else related.get(finding.get("path"), set())
            )
            if affected:
                for index in affected:
                    self._episode_findings[index].append(finding)
            else:
                self._general_findings.append(finding)
        self.ffmpeg = ffmpeg if ffmpeg is not None else shutil.which("ffmpeg")
        self._cache = tempfile.TemporaryDirectory(prefix="openbot-review-")
        self._clip_lock = threading.RLock()
        self._cached: OrderedDict[Path, int] = OrderedDict()
        self._active: dict[Path, int] = {}
        self._closed = False

    def close(self) -> None:
        with self._clip_lock:
            self._closed = True
            self._cache.cleanup()

    def _evict_clips(self, *, protect: Optional[Path] = None) -> None:
        for path in list(self._cached):
            if (
                sum(self._cached.values()) <= _MAX_CACHE_BYTES
                and len(self._cached) <= _MAX_CACHE_FILES
            ):
                break
            if path == protect or self._active.get(path):
                continue
            path.unlink(missing_ok=True)
            del self._cached[path]

    @contextmanager
    def open_clip(self, index: int, camera: str) -> Iterator[BinaryIO]:
        """Keep a preview alive until its HTTP range response finishes."""
        with self._clip_lock:
            path = self.clip(index, camera)
            source = path.open("rb")
            self._active[path] = self._active.get(path, 0) + 1
        try:
            yield source
        finally:
            source.close()
            with self._clip_lock:
                self._active[path] -= 1
                if not self._active[path]:
                    del self._active[path]
                self._evict_clips()

    def _episode(self, index: int):
        try:
            return self.episodes[index]
        except KeyError as exc:
            raise KeyError(f"episode {index} not found") from exc

    def overview(self) -> dict[str, Any]:
        return {
            "dataset": self.root.name,
            "format": self.snapshot.codebase_version,
            "summary": self.audit["summary"],
            "audit": {
                "integrity": self.snapshot.integrity,
                "skipped_checks": self.audit["skipped_checks"],
            },
            "ffmpeg_available": bool(self.ffmpeg),
            "episodes": [
                {
                    "index": ep.episode_index,
                    "length": ep.length,
                    "tasks": list(ep.tasks),
                    "cameras": len(ep.video_segments),
                    "findings": {
                        severity: sum(
                            finding.get("severity") == severity
                            for finding in self._episode_findings[ep.episode_index]
                        )
                        for severity in ("error", "warning")
                    },
                }
                for ep in self.snapshot.episodes
            ],
            "general_findings": self._general_findings,
        }

    def episode(self, index: int) -> dict[str, Any]:
        ep = self._episode(index)
        segments = [
            {
                "key": item["video_key"],
                "path": item["path"],
                "from_timestamp": item["from_timestamp"],
                "to_timestamp": item["to_timestamp"],
                "preview_seconds": min(
                    _MAX_PREVIEW_SECONDS,
                    max(0.0, item["to_timestamp"] - item["from_timestamp"]),
                ),
            }
            for item in ep.video_segments
        ]
        return {
            "index": index,
            "length": ep.length,
            "tasks": list(ep.tasks),
            "videos": segments,
            "findings": self._episode_findings[index],
            "traces": self._traces(index),
        }

    def _traces(self, index: int) -> dict[str, Any]:
        try:
            import pyarrow as arrow  # type: ignore[import-not-found,import-untyped]
        except ImportError:
            return {"frames": [], "series": {}, "error": "Install openbot-data[lerobot]."}
        try:
            return self._read_traces(index)
        except (arrow.ArrowException, OSError, ValueError, TypeError):
            # A damaged shard is precisely what this reviewer needs to expose.
            # Keep its audit evidence and video available when the chart cannot load.
            return {
                "frames": [], "series": {},
                "error": "Motion data is missing, unreadable, or unsafe to open. "
                         "See the audit findings for this episode.",
            }

    def _read_traces(self, index: int) -> dict[str, Any]:
        metadata = self.metadata.get(index)
        if metadata is None or not metadata.data_path:
            return {"frames": [], "series": {}}
        path = _inside(self.root, metadata.data_path)
        try:
            import pyarrow.parquet as parquet  # type: ignore[import-not-found,import-untyped]
        except ImportError as exc:
            raise DatasetArgumentError("review traces require openbot-data[lerobot]") from exc
        with parquet.ParquetFile(path) as parquet_file:
            return self._sample_traces(parquet_file, index, metadata.length)

    @staticmethod
    def _sample_traces(parquet_file: Any, index: int, length: Optional[int]) -> dict[str, Any]:
        available = set(parquet_file.schema_arrow.names)
        keys = [name for name in ("observation.state", "action") if name in available]
        if "episode_index" not in available or "frame_index" not in available:
            return {
                "frames": [], "series": {},
                "error": "Motion data has no episode_index or frame_index column.",
            }
        stride = max(1, math.ceil((length or 0) / _MAX_TRACE_POINTS))
        frames: list[int] = []
        series: dict[str, list[list[float]]] = {key: [] for key in keys}
        for batch in parquet_file.iter_batches(columns=["episode_index", "frame_index", *keys]):
            for row in batch.to_pylist():
                if row["episode_index"] != index:
                    continue
                frame = row["frame_index"]
                if not isinstance(frame, int) or frame % stride:
                    continue
                frames.append(frame)
                for key in keys:
                    series[key].append(_numbers(row.get(key)))
                if len(frames) >= _MAX_TRACE_POINTS:
                    break
            if len(frames) >= _MAX_TRACE_POINTS:
                break
        return {"frames": frames, "series": series}

    def clip(self, index: int, camera: str) -> Path:
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg is required for browser video previews")
        ep = self._episode(index)
        segment = next(
            (item for item in ep.video_segments if item["video_key"] == camera), None
        )
        if segment is None:
            raise KeyError(f"camera {camera} not found in episode {index}")
        video = _inside(self.root, segment["path"])
        start = float(segment["from_timestamp"])
        duration = min(
            _MAX_PREVIEW_SECONDS,
            float(segment["to_timestamp"]) - start,
        )
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
            raise ValueError("invalid video segment timestamps")
        stat = video.stat()
        identity = json.dumps(
            [str(video), stat.st_size, stat.st_mtime_ns, start, duration],
            separators=(",", ":"),
        )
        name = hashlib.sha256(identity.encode()).hexdigest()[:24]
        target = Path(self._cache.name) / f"{name}.mp4"
        with self._clip_lock:
            if self._closed:
                raise RuntimeError("review workspace is closed")
            if target.is_file():
                self._cached.move_to_end(target)
                return target
            temporary = target.with_suffix(".tmp.mp4")
            command = [
                self.ffmpeg, "-nostdin", "-y", "-loglevel", "error",
                "-ss", f"{start:.6f}", "-i", str(video), "-t", f"{duration:.6f}",
                "-vf", "scale='min(640,iw)':-2", "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(temporary),
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, timeout=120)
                temporary.replace(target)
                self._cached[target] = target.stat().st_size
                self._evict_clips(protect=target)
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                temporary.unlink(missing_ok=True)
                raise RuntimeError("video preview could not be generated") from exc
        return target


class ReviewHandler(BaseHTTPRequestHandler):
    """Only named read routes are exposed; there are no source write handlers."""

    server: ReviewHTTPServer

    def _headers(self, status: HTTPStatus, content_type: str, size: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "media-src 'self'; connect-src 'self'; img-src 'self' data:",
        )
        self.end_headers()

    def _bytes(self, data: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._headers(status, content_type, len(data))
        self.wfile.write(data)

    def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._bytes(json.dumps(value, ensure_ascii=False).encode(), "application/json", status)

    def _file(self, source: BinaryIO) -> None:
        size = source.seek(0, 2)
        match = _RANGE.fullmatch(self.headers.get("Range", ""))
        start, end = 0, size - 1
        if match:
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), end) if last else end
            elif last:
                start = max(0, size - int(last))
            if start >= size or end < start:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        status = HTTPStatus.PARTIAL_CONTENT if match else HTTPStatus.OK
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        if match:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        source.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = source.read(min(remaining, 256 * 1024))
            if not chunk:
                break
            self.wfile.write(chunk)
            remaining -= len(chunk)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        workspace = self.server.workspace
        try:
            if parsed.path == "/api/overview":
                self._json(workspace.overview())
            elif parsed.path == "/api/episode":
                query = parse_qs(parsed.query)
                self._json(workspace.episode(int(query["index"][0])))
            elif parsed.path == "/api/clip":
                query = parse_qs(parsed.query)
                with workspace.open_clip(int(query["index"][0]), query["camera"][0]) as source:
                    self._file(source)
            elif parsed.path in ("/", "/review.js", "/review.css"):
                name = "review.html" if parsed.path == "/" else parsed.path[1:]
                content_type = {
                    "review.html": "text/html; charset=utf-8",
                    "review.js": "text/javascript; charset=utf-8",
                    "review.css": "text/css; charset=utf-8",
                }[name]
                asset = resources.files("openbot_data").joinpath("review_assets").joinpath(name)
                data = asset.read_bytes()
                self._bytes(data, content_type)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionResetError):
            return  # Switching episodes can cancel a range request.
        except (KeyError, IndexError, ValueError, FileNotFoundError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        self._json({"error": "review is read-only"}, HTTPStatus.METHOD_NOT_ALLOWED)


class ReviewHTTPServer(ThreadingHTTPServer):
    # Wait for transcodes and streams before deleting their temporary files.
    daemon_threads = False

    def __init__(self, workspace: ReviewWorkspace, port: int):
        self.workspace = workspace
        super().__init__(("127.0.0.1", port), ReviewHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(30)
        return connection, address


def serve_review(path: str, *, port: int = 8766, open_browser: bool = True) -> None:
    import webbrowser

    workspace = ReviewWorkspace(path)
    try:
        with ReviewHTTPServer(workspace, port) as server:
            url = f"http://127.0.0.1:{server.server_port}/"
            print(f"OpenBot Data review: {url}", flush=True)
            print(f"Dataset: {workspace.root} ({len(workspace.episodes)} episodes)", flush=True)
            if not workspace.ffmpeg:
                print("ffmpeg not found; video previews are unavailable", flush=True)
            if open_browser:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        workspace.close()
