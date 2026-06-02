"""
Video processing module for OpenBot Data.
"""

import cv2
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class VideoInfo:
    """Video metadata."""
    path: str
    filename: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float
    size_mb: float
    is_valid: bool
    error: Optional[str] = None


def scan_video(video_path: str) -> VideoInfo:
    """
    Scan a video file and extract metadata.

    Args:
        video_path: Path to video file

    Returns:
        VideoInfo object with metadata
    """
    path = Path(video_path)

    if not path.exists():
        return VideoInfo(
            path=str(path),
            filename=path.name,
            width=0,
            height=0,
            fps=0,
            frame_count=0,
            duration=0,
            size_mb=0,
            is_valid=False,
            error="File not found"
        )

    cap = None

    try:
        size_mb = path.stat().st_size / (1024 * 1024)
        cap = cv2.VideoCapture(str(path))

        if not cap.isOpened():
            return VideoInfo(
                path=str(path),
                filename=path.name,
                width=0,
                height=0,
                fps=0,
                frame_count=0,
                duration=0,
                size_mb=size_mb,
                is_valid=False,
                error="Cannot open video file"
            )

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if fps > 0:
            duration = frame_count / fps
        else:
            duration = 0

        is_valid = width > 0 and height > 0 and frame_count > 0

        return VideoInfo(
            path=str(path),
            filename=path.name,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            duration=duration,
            size_mb=size_mb,
            is_valid=is_valid,
            error=None if is_valid else "Video has no readable frames"
        )

    except Exception as e:
        return VideoInfo(
            path=str(path),
            filename=path.name,
            width=0,
            height=0,
            fps=0,
            frame_count=0,
            duration=0,
            size_mb=path.stat().st_size / (1024 * 1024) if path.exists() else 0,
            is_valid=False,
            error=str(e)
        )
    finally:
        if cap is not None:
            cap.release()


def scan_directory(directory: str) -> Dict[str, Any]:
    """
    Scan a directory for video files.

    Args:
        directory: Path to directory

    Returns:
        Dictionary with scan results
    """
    path = Path(directory)

    if not path.exists() or not path.is_dir():
        return {
            "error": f"Directory not found: {directory}",
            "videos": []
        }

    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    video_files = []

    for f in sorted(path.rglob("*")):
        if f.is_file() and f.suffix.lower() in video_extensions:
            info = scan_video(str(f))
            video_files.append({
                "path": str(f),
                "filename": f.name,
                "width": info.width,
                "height": info.height,
                "fps": info.fps,
                "frame_count": info.frame_count,
                "duration": round(info.duration, 2),
                "size_mb": round(info.size_mb, 2),
                "is_valid": info.is_valid,
                "error": info.error
            })

    total_videos = len(video_files)
    valid_videos = sum(1 for v in video_files if v["is_valid"])
    total_size_mb = sum(v["size_mb"] for v in video_files)
    total_duration = sum(v["duration"] for v in video_files if v["is_valid"])

    return {
        "directory": str(path),
        "total_videos": total_videos,
        "valid_videos": valid_videos,
        "invalid_videos": total_videos - valid_videos,
        "total_size_mb": round(total_size_mb, 2),
        "total_duration": round(total_duration, 2),
        "videos": video_files
    }
