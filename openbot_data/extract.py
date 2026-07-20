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


def extract_timestamped_frames(
    video_path: str,
    output_dir: str,
    sample_fps: float = 1.0,
    max_frames: int = 32,
    max_edge: int = 640,
) -> Dict[str, Any]:
    """Extract timestamped frames for model-assisted robot video annotation.

    Unlike :func:`extract_preview_frames`, this samples by time so the output can
    be used as reproducible evidence. The caller still has to validate semantic
    labels; these frames are observations, not ground truth.
    """
    if sample_fps <= 0:
        return {"error": "sample_fps must be greater than 0"}
    if max_frames <= 0:
        return {"error": "max_frames must be greater than 0"}

    source = Path(video_path)
    frames_dir = Path(output_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        return {"error": f"Cannot open video: {source}"}

    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or frame_count <= 0:
        cap.release()
        return {"error": f"Video has invalid timing metadata: {source}"}

    duration = frame_count / fps
    requested = max(1, int(duration * sample_fps) + 1)
    sample_count = min(max_frames, requested, frame_count)
    if sample_count == 1:
        timestamps = [0.0]
    else:
        timestamps = [duration * index / (sample_count - 1) for index in range(sample_count)]

    extracted: List[PreviewFrame] = []
    output_id = _video_output_id(source)
    for index, timestamp in enumerate(timestamps):
        frame_number = min(frame_count - 1, int(round(timestamp * fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, frame = cap.read()
        if not ok:
            continue

        height, width = frame.shape[:2]
        scale = min(1.0, max_edge / max(width, height))
        if scale < 1.0:
            frame = cv2.resize(
                frame,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )

        label = f"{timestamp:.2f}s"
        cv2.rectangle(frame, (0, 0), (max(90, len(label) * 14), 30), (0, 0, 0), -1)
        cv2.putText(
            frame,
            label,
            (8, 21),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        frame_id = f"{output_id}_frame{index:03d}.jpg"
        frame_path = frames_dir / frame_id
        if not cv2.imwrite(str(frame_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88]):
            continue
        extracted.append(
            PreviewFrame(
                frame_id=frame_id,
                video_path=str(source),
                frame_number=frame_number,
                timestamp=round(timestamp, 3),
                path=str(frame_path),
            )
        )

    cap.release()
    return {
        "video_path": str(source),
        "duration_seconds": round(duration, 3),
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "sample_fps": sample_fps,
        "extracted_frames": len(extracted),
        "frames": [asdict(frame) for frame in extracted],
    }


def build_contact_sheets(
    frames: List[Dict[str, Any]],
    output_dir: str,
    columns: int = 5,
    rows: int = 4,
    tile_width: int = 320,
) -> Dict[str, Any]:
    """Build timestamped contact sheets from extracted evidence frames."""
    if columns < 1 or rows < 1:
        return {"error": "columns and rows must be greater than 0"}
    if not frames:
        return {"error": "No frames were supplied"}

    sheets_dir = Path(output_dir) / "contact_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    per_sheet = columns * rows
    sheet_records: List[Dict[str, Any]] = []

    for sheet_index, offset in enumerate(range(0, len(frames), per_sheet)):
        batch = frames[offset : offset + per_sheet]
        decoded = [cv2.imread(str(frame["path"])) for frame in batch]
        decoded = [image for image in decoded if image is not None]
        if not decoded:
            continue

        ratios = [image.shape[0] / max(1, image.shape[1]) for image in decoded]
        tile_height = max(1, int(tile_width * max(ratios)))
        canvas = cv2.copyMakeBorder(
            cv2.resize(decoded[0], (tile_width, tile_height)),
            0,
            tile_height * rows - tile_height,
            0,
            tile_width * columns - tile_width,
            cv2.BORDER_CONSTANT,
            value=(20, 20, 20),
        )
        canvas[:] = (20, 20, 20)
        for item_index, image in enumerate(decoded):
            height, width = image.shape[:2]
            scale = min(tile_width / max(1, width), tile_height / max(1, height))
            resized = cv2.resize(
                image,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            y = (item_index // columns) * tile_height
            x = (item_index % columns) * tile_width
            y_pad = (tile_height - resized.shape[0]) // 2
            x_pad = (tile_width - resized.shape[1]) // 2
            canvas[y + y_pad : y + y_pad + resized.shape[0], x + x_pad : x + x_pad + resized.shape[1]] = resized

        path = sheets_dir / f"contact_sheet_{sheet_index:03d}.jpg"
        if not cv2.imwrite(str(path), canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 88]):
            continue
        sheet_records.append(
            {
                "id": f"contact_sheet_{sheet_index:03d}",
                "path": str(path),
                "frame_ids": [str(frame["frame_id"]) for frame in batch],
            }
        )

    return {"contact_sheets": sheet_records, "count": len(sheet_records)}


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
