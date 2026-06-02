"""
Frame extraction module for OpenBot Data v0.0.1.
Extracts preview frames from videos for inspection.
"""

import hashlib
import cv2
import json
from pathlib import Path
from typing import Dict, Any, List
from dataclasses import dataclass, asdict
from tqdm import tqdm


@dataclass
class PreviewFrame:
    """Metadata for a preview frame."""
    frame_id: str
    video_path: str
    frame_number: int
    timestamp: float
    path: str


def _video_output_id(video_path: Path) -> str:
    digest = hashlib.sha1(str(video_path.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{video_path.stem}_{digest}"


def extract_preview_frames(
    video_path: str,
    output_dir: str,
    max_frames: int = 10
) -> Dict[str, Any]:
    """
    Extract a few preview frames from a video.

    Args:
        video_path: Path to input video
        output_dir: Path to output directory
        max_frames: Maximum number of frames to extract

    Returns:
        Dictionary with extraction results
    """
    video_path = Path(video_path)
    output_dir = Path(output_dir)

    previews_dir = output_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))

    if not cap.isOpened():
        return {"error": f"Cannot open video: {video_path}"}

    if max_frames <= 0:
        cap.release()
        return {"error": "max_frames must be greater than 0"}

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 30.0

    if total_frames <= 0:
        cap.release()
        return {"error": f"Video has no readable frames: {video_path}"}

    video_name = video_path.stem
    video_output_id = _video_output_id(video_path)

    sample_count = min(max_frames, total_frames)
    if sample_count == 1:
        frame_indices = [0]
    else:
        frame_indices = [
            int(round(i * (total_frames - 1) / (sample_count - 1)))
            for i in range(sample_count)
        ]

    extracted_frames: List[PreviewFrame] = []

    for frame_idx in frame_indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()

        if not ret:
            continue

        timestamp = frame_idx / fps
        frame_id = f"{video_output_id}_preview{len(extracted_frames):02d}.jpg"
        frame_path = previews_dir / frame_id

        cv2.imwrite(str(frame_path), frame)

        extracted_frame = PreviewFrame(
            frame_id=frame_id,
            video_path=str(video_path),
            frame_number=frame_idx,
            timestamp=round(timestamp, 2),
            path=str(frame_path)
        )
        extracted_frames.append(extracted_frame)

    cap.release()

    return {
        "video_path": str(video_path),
        "video_name": video_name,
        "total_frames": total_frames,
        "fps": fps,
        "extracted_frames": len(extracted_frames),
        "frames": [asdict(f) for f in extracted_frames]
    }


def inspect_dataset(
    video_dir: str,
    output_dir: str
) -> Dict[str, Any]:
    """
    Inspect a robot video dataset and generate manifest.

    Args:
        video_dir: Path to video directory
        output_dir: Path to output directory

    Returns:
        Dictionary with inspection results
    """
    from openbot_data.video import scan_directory

    scan_result = scan_directory(video_dir)

    if "error" in scan_result:
        return scan_result

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata_dir = output_dir / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    all_previews = []
    all_videos = []

    for video_info in tqdm(scan_result["videos"], desc="Extracting previews"):
        if not video_info["is_valid"]:
            all_videos.append({
                **video_info,
                "previews": []
            })
            continue

        video_path = video_info["path"]
        video_name = Path(video_path).stem

        preview_result = extract_preview_frames(
            video_path,
            str(output_dir),
            max_frames=10
        )

        if "error" not in preview_result:
            all_previews.extend(preview_result["frames"])

        all_videos.append({
            **video_info,
            "previews": preview_result.get("frames", []) if "error" not in preview_result else []
        })

    # Generate manifest
    manifest = {
        "version": "0.0.1",
        "source_dir": str(video_dir),
        "output_dir": str(output_dir),
        "total_videos": len(all_videos),
        "valid_videos": sum(1 for v in all_videos if v["is_valid"]),
        "total_previews": len(all_previews),
        "videos": all_videos
    }

    manifest_path = metadata_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Generate basic report
    report = {
        "version": "0.0.1",
        "source_dir": str(video_dir),
        "output_dir": str(output_dir),
        "total_videos": len(all_videos),
        "valid_videos": sum(1 for v in all_videos if v["is_valid"]),
        "invalid_videos": sum(1 for v in all_videos if not v["is_valid"]),
        "total_duration": round(sum(v["duration"] for v in all_videos if v["is_valid"]), 2),
        "total_size_mb": round(sum(v["size_mb"] for v in all_videos), 2),
        "total_previews": len(all_previews),
        "resolutions": [
            list(resolution)
            for resolution in sorted({(v["width"], v["height"]) for v in all_videos if v["is_valid"]})
        ]
    }

    report_path = metadata_dir / "report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    return {
        "manifest_path": str(manifest_path),
        "report_path": str(report_path),
        "total_videos": len(all_videos),
        "total_previews": len(all_previews)
    }
