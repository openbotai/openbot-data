"""Real data-plane primitives for OpenBot robot video annotation jobs."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from openbot_data.extract import build_contact_sheets, extract_timestamped_frames
from openbot_data.video import scan_video


PROCESSOR_SCHEMA_VERSION = "openbot.data_processor_result.v1"
TIMELINE_SCHEMA_VERSION = "openbot.subtask_timeline.v1"
DEFAULT_MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024


class ProcessingError(RuntimeError):
    """A safe, user-facing processor failure."""


@dataclass
class ProviderResult:
    segments: List[Dict[str, Any]]
    provider: str
    model_version: str
    provider_run_id: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class AnnotationProvider(Protocol):
    def annotate(
        self,
        *,
        task_hint: Optional[str],
        taxonomy: List[str],
        video: Dict[str, Any],
        frames: List[Dict[str, Any]],
        contact_sheet_paths: List[Path],
        prompt_version: str,
    ) -> ProviderResult:
        """Return candidate subtask segments backed by sampled frames."""


def _assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProcessingError("video_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ProcessingError("video_url must not contain credentials")

    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProcessingError("video_url hostname could not be resolved") from exc

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ProcessingError("video_url resolves to a non-public address")


def download_video(
    video_url: str,
    destination: Path,
    *,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    timeout_seconds: float = 60.0,
) -> Dict[str, Any]:
    """Download a public video with redirect and size controls."""
    current_url = video_url
    total = 0
    content_type = "application/octet-stream"
    with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
        for redirect_index in range(6):
            _assert_public_http_url(current_url)
            with client.stream("GET", current_url, headers={"User-Agent": "OpenBot-Data/0.1"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ProcessingError("video_url redirect did not include a location")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise ProcessingError(f"video download failed with HTTP {response.status_code}")
                declared_size = response.headers.get("content-length")
                if declared_size and int(declared_size) > max_bytes:
                    raise ProcessingError("video exceeds the configured download size limit")
                content_type = response.headers.get("content-type", content_type).split(";", 1)[0]
                with destination.open("wb") as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ProcessingError("video exceeds the configured download size limit")
                        output.write(chunk)
                return {
                    "source_url": video_url,
                    "resolved_url": current_url,
                    "size_bytes": total,
                    "content_type": content_type,
                    "redirects": redirect_index,
                }
    raise ProcessingError("video_url exceeded the redirect limit")


def _segment_schema() -> Dict[str, Any]:
    nullable_string = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start_sec": {"type": "number", "minimum": 0},
                        "end_sec": {"type": "number", "minimum": 0},
                        "action": nullable_string,
                        "object": nullable_string,
                        "source": nullable_string,
                        "target": nullable_string,
                        "state_change": nullable_string,
                        "outcome": {
                            "type": "string",
                            "enum": [
                                "success",
                                "failure",
                                "intervention",
                                "recovery",
                                "uncertain",
                            ],
                        },
                        "label": {"type": "string"},
                        "evidence_frame_indices": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 0},
                        },
                    },
                    "required": [
                        "start_sec",
                        "end_sec",
                        "action",
                        "object",
                        "source",
                        "target",
                        "state_change",
                        "outcome",
                        "label",
                        "evidence_frame_indices",
                    ],
                },
            }
        },
        "required": ["segments"],
    }


class GeminiAnnotationProvider:
    """Gemini contact-sheet provider with structured JSON output."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_seconds: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def annotate(
        self,
        *,
        task_hint: Optional[str],
        taxonomy: List[str],
        video: Dict[str, Any],
        frames: List[Dict[str, Any]],
        contact_sheet_paths: List[Path],
        prompt_version: str,
    ) -> ProviderResult:
        frame_index = [
            {"index": index, "timestamp_sec": frame["timestamp"]}
            for index, frame in enumerate(frames)
        ]
        prompt = (
            "You annotate robot and egocentric video for training data. Split the observed episode "
            "into non-overlapping chronological subtasks. Only state details supported by the images. "
            "Use null for an unobservable object/source/target/state change and uncertain for an unclear "
            "outcome. Evidence indices must reference the supplied frame index. Do not infer success from "
            "the task description alone.\n\n"
            f"Task hint: {task_hint or 'not supplied'}\n"
            f"Allowed taxonomy hints: {json.dumps(taxonomy)}\n"
            f"Video duration seconds: {video['duration_seconds']}\n"
            f"Frame index: {json.dumps(frame_index)}\n"
            f"Prompt version: {prompt_version}"
        )
        parts: List[Dict[str, Any]] = [{"text": prompt}]
        for path in contact_sheet_paths:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    }
                }
            )
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": _segment_schema(),
            },
        }
        url = f"{self.base_url}/models/{self.model}:generateContent"
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                url,
                headers={"x-goog-api-key": self.api_key, "content-type": "application/json"},
                json=body,
            )
        if response.status_code < 200 or response.status_code >= 300:
            raise ProcessingError(f"annotation provider failed with HTTP {response.status_code}")
        payload = response.json()
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            segments = parsed["segments"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProcessingError("annotation provider returned an invalid structured response") from exc
        if not isinstance(segments, list):
            raise ProcessingError("annotation provider segments must be an array")
        return ProviderResult(
            segments=segments,
            provider="gemini",
            model_version=str(payload.get("modelVersion") or self.model),
            provider_run_id=payload.get("responseId"),
            usage=payload.get("usageMetadata"),
        )


def provider_from_env() -> AnnotationProvider:
    provider_name = os.getenv("OPENBOT_ANNOTATION_PROVIDER", "gemini").strip().lower()
    if provider_name != "gemini":
        raise ProcessingError(f"Unsupported annotation provider: {provider_name}")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ProcessingError("GEMINI_API_KEY is required for the annotation provider")
    return GeminiAnnotationProvider(
        api_key=api_key,
        model=os.getenv("OPENBOT_ANNOTATION_MODEL", "gemini-3.5-flash"),
        base_url=os.getenv(
            "GEMINI_API_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        ),
    )


def _normalize_segments(
    raw_segments: List[Dict[str, Any]],
    *,
    duration_seconds: float,
    frames: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    ordered = sorted(raw_segments, key=lambda item: float(item.get("start_sec", 0)))
    previous_end = 0.0
    for index, raw in enumerate(ordered):
        try:
            start = max(0.0, min(duration_seconds, float(raw.get("start_sec", 0))))
            end = max(start, min(duration_seconds, float(raw.get("end_sec", start))))
        except (TypeError, ValueError) as exc:
            raise ProcessingError("provider returned a non-numeric segment boundary") from exc
        if end <= start:
            continue
        if start < previous_end:
            start = previous_end
        if end <= start:
            continue

        evidence: List[Dict[str, Any]] = []
        indices = raw.get("evidence_frame_indices", [])
        if isinstance(indices, list):
            for value in indices:
                if isinstance(value, int) and 0 <= value < len(frames):
                    frame = frames[value]
                    evidence.append(
                        {
                            "frame_id": frame["frame_id"],
                            "timestamp_sec": frame["timestamp"],
                            "artifact_key": f"evidence/{frame['frame_id']}",
                        }
                    )
        normalized.append(
            {
                "id": f"segment_{index + 1:03d}",
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "action": raw.get("action"),
                "object": raw.get("object"),
                "source": raw.get("source"),
                "target": raw.get("target"),
                "state_change": raw.get("state_change"),
                "outcome": raw.get("outcome", "uncertain"),
                "label": str(raw.get("label") or "unlabeled subtask"),
                "evidence_frames": evidence,
                "confidence": None,
                "status": "needs_review",
            }
        )
        previous_end = end
    if not normalized:
        raise ProcessingError("annotation provider did not return any valid segments")
    return normalized


def _artifact(path: Path, name: str, content_type: str) -> Dict[str, str]:
    return {
        "name": name,
        "content_type": content_type,
        "base64_data": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def process_subtask_job(
    request: Dict[str, Any],
    *,
    provider: Optional[AnnotationProvider] = None,
    allow_local_input: bool = False,
) -> Dict[str, Any]:
    """Process one subtask job and return canonical output plus small artifacts."""
    job_id = str(request.get("job_id") or "")
    dataset_id = str(request.get("dataset_id") or "")
    if not job_id or not dataset_id:
        raise ProcessingError("job_id and dataset_id are required")
    source = request.get("source")
    if not isinstance(source, dict):
        raise ProcessingError("source is required")

    segmentation = request.get("segmentation") if isinstance(request.get("segmentation"), dict) else {}
    contact_sheet = (
        segmentation.get("contact_sheet")
        if isinstance(segmentation.get("contact_sheet"), dict)
        else {}
    )
    labeling = request.get("labeling") if isinstance(request.get("labeling"), dict) else {}
    taxonomy = [str(item) for item in labeling.get("taxonomy", []) if str(item).strip()]
    if not taxonomy:
        raise ProcessingError("labeling.taxonomy must contain at least one label")

    sample_fps = float(segmentation.get("sample_fps", 1.0))
    columns = int(contact_sheet.get("columns", 5))
    max_frames = int(segmentation.get("max_frames", 32))
    processor = provider or provider_from_env()

    with tempfile.TemporaryDirectory(prefix=f"openbot-{job_id}-") as temporary:
        workdir = Path(temporary)
        local_path_value = source.get("video_path")
        download: Optional[Dict[str, Any]] = None
        if local_path_value:
            if not allow_local_input:
                raise ProcessingError("local video paths are disabled")
            video_path = Path(str(local_path_value))
        else:
            video_url = source.get("video_url")
            if not isinstance(video_url, str) or not video_url:
                raise ProcessingError("source.video_url is required")
            suffix = Path(urlparse(video_url).path).suffix or ".mp4"
            video_path = workdir / f"input{suffix[:10]}"
            download = download_video(video_url, video_path)

        info = scan_video(str(video_path))
        if not info.is_valid:
            raise ProcessingError(info.error or "video could not be decoded")
        frames_result = extract_timestamped_frames(
            str(video_path),
            str(workdir),
            sample_fps=sample_fps,
            max_frames=max_frames,
        )
        if "error" in frames_result:
            raise ProcessingError(str(frames_result["error"]))
        frames = frames_result["frames"]
        sheets_result = build_contact_sheets(
            frames,
            str(workdir),
            columns=columns,
            rows=4,
        )
        if "error" in sheets_result:
            raise ProcessingError(str(sheets_result["error"]))
        sheet_paths = [Path(item["path"]) for item in sheets_result["contact_sheets"]]

        video_metadata = {
            "duration_seconds": round(info.duration, 3),
            "fps": round(info.fps, 3),
            "width": info.width,
            "height": info.height,
            "frame_count": info.frame_count,
            "size_bytes": int(info.size_mb * 1024 * 1024),
        }
        provider_result = processor.annotate(
            task_hint=request.get("task_hint"),
            taxonomy=taxonomy,
            video=video_metadata,
            frames=frames,
            contact_sheet_paths=sheet_paths,
            prompt_version=str(request.get("prompt_version") or "subtask-timeline-v1"),
        )
        segments = _normalize_segments(
            provider_result.segments,
            duration_seconds=info.duration,
            frames=frames,
        )

        used_frame_ids = {
            evidence["frame_id"]
            for segment in segments
            for evidence in segment["evidence_frames"]
        }
        artifacts = [
            _artifact(path, f"contact_sheets/{path.name}", "image/jpeg") for path in sheet_paths
        ]
        artifacts.extend(
            _artifact(Path(frame["path"]), f"evidence/{frame['frame_id']}", "image/jpeg")
            for frame in frames
            if frame["frame_id"] in used_frame_ids
        )
        manifest = {
            "schema_version": TIMELINE_SCHEMA_VERSION,
            "processor_schema_version": PROCESSOR_SCHEMA_VERSION,
            "job_id": job_id,
            "dataset_id": dataset_id,
            "input": {
                "video_key": source.get("video_key"),
                "video_url": source.get("video_url"),
                "download": download,
                "video": video_metadata,
            },
            "processing": {
                "sample_fps": sample_fps,
                "sampled_frames": len(frames),
                "contact_sheet_columns": columns,
                "provider": provider_result.provider,
                "model_version": provider_result.model_version,
                "provider_run_id": provider_result.provider_run_id,
                "prompt_version": request.get("prompt_version"),
            },
            "review": {"required": True, "policy": "human_approval_before_export"},
        }
        return {
            "schema_version": PROCESSOR_SCHEMA_VERSION,
            "summary": f"Generated {len(segments)} reviewable subtask suggestions.",
            "metrics": {
                "segment_count": len(segments),
                "duration_seconds": round(info.duration, 3),
                "sampled_frames": len(frames),
                "requires_human_review": True,
                "provider_usage": provider_result.usage,
            },
            "annotations": {
                "timeline": {
                    "video_url": source.get("video_url"),
                    "duration_seconds": round(info.duration, 3),
                    "segments": segments,
                }
            },
            "checks": {
                "generated_by": "openbot_data_processor",
                "warning": "Suggestions are not ground truth and require human approval.",
                "manifest": manifest,
            },
            "artifacts": artifacts,
        }
