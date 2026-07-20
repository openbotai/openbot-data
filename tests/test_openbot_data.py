import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import openbot_data
from openbot_data.cli import app
from openbot_data.extract import extract_preview_frames, inspect_dataset
from openbot_data.video import scan_directory
from openbot_data.catalog import export_catalog
from openbot_data.processor import (
    CloudflareWorkersAIAnnotationProvider,
    ProcessingError,
    ProviderResult,
    process_subtask_job,
    provider_from_env,
)
from openbot_data import service as data_service


def make_video(path: Path, frames: int = 6) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        5.0,
        (16, 16),
    )
    if not writer.isOpened():
        pytest.skip("OpenCV video writer is unavailable in this environment")

    for frame_index in range(frames):
        frame = np.full((16, 16, 3), frame_index * 20, dtype=np.uint8)
        writer.write(frame)

    writer.release()


def test_public_api_exports_expected_functions() -> None:
    assert openbot_data.__version__ == "0.0.1.post2"
    assert set(openbot_data.__all__) == {
        "extract_preview_frames",
        "inspect_dataset",
        "scan_directory",
        "scan_video",
        "export_catalog",
        "process_subtask_job",
        "ProcessingError",
    }


def test_scan_directory_reports_missing_directory(tmp_path: Path) -> None:
    result = scan_directory(str(tmp_path / "missing"))

    assert "error" in result
    assert result["videos"] == []


def test_inspect_dataset_generates_manifest_report_and_previews(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    output_dir = tmp_path / "dataset"
    make_video(video_dir / "clip.avi")

    result = inspect_dataset(str(video_dir), str(output_dir))

    manifest_path = Path(result["manifest_path"])
    report_path = Path(result["report_path"])
    assert manifest_path.exists()
    assert report_path.exists()

    manifest = json.loads(manifest_path.read_text())
    report = json.loads(report_path.read_text())

    assert manifest["valid_videos"] == 1
    assert manifest["total_previews"] > 0
    assert report["resolutions"] == [[16, 16]]
    assert all(Path(frame["path"]).exists() for frame in manifest["videos"][0]["previews"])


def test_preview_frame_names_do_not_collide_for_same_basename(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    output_dir = tmp_path / "dataset"
    make_video(video_dir / "left" / "clip.avi")
    make_video(video_dir / "right" / "clip.avi")

    result = inspect_dataset(str(video_dir), str(output_dir))
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    preview_paths = [
        frame["path"]
        for video in manifest["videos"]
        for frame in video["previews"]
    ]

    assert len(preview_paths) == len(set(preview_paths))


def test_cli_version_and_inspect_command(tmp_path: Path) -> None:
    runner = CliRunner()
    version_result = runner.invoke(app, ["version"])

    assert version_result.exit_code == 0
    assert "OpenBot Data v0.0.1.post2" in version_result.output

    video_dir = tmp_path / "videos"
    output_dir = tmp_path / "dataset"
    make_video(video_dir / "clip.avi")
    inspect_result = runner.invoke(app, ["inspect", str(video_dir), "--out", str(output_dir)])

    assert inspect_result.exit_code == 0
    assert "Inspection complete!" in inspect_result.output
    assert (output_dir / "metadata" / "manifest.json").exists()


def test_export_catalog_json(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    output_path = tmp_path / "catalog.json"
    make_video(video_dir / "clip.avi")

    result = export_catalog(str(video_dir), str(output_path), fmt="json")

    assert "error" not in result
    assert output_path.exists()

    catalog = json.loads(output_path.read_text())
    assert catalog["summary"]["total_videos"] == 1
    assert catalog["summary"]["valid_videos"] == 1
    assert len(catalog["videos"]) == 1
    assert catalog["videos"][0]["filename"] == "clip.avi"


def test_export_catalog_csv(tmp_path: Path) -> None:
    video_dir = tmp_path / "videos"
    output_path = tmp_path / "catalog.csv"
    make_video(video_dir / "clip.avi")

    result = export_catalog(str(video_dir), str(output_path), fmt="csv")

    assert "error" not in result
    assert output_path.exists()

    import csv

    rows = list(csv.DictReader(output_path.read_text().splitlines()))
    assert len(rows) == 1
    assert rows[0]["filename"] == "clip.avi"


def test_export_catalog_invalid_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        export_catalog(str(tmp_path / "videos"), str(tmp_path / "catalog.txt"), fmt="txt")


def test_cli_catalog_command(tmp_path: Path) -> None:
    runner = CliRunner()
    video_dir = tmp_path / "videos"
    output_path = tmp_path / "catalog.json"
    make_video(video_dir / "clip.avi")

    result = runner.invoke(app, ["catalog", str(video_dir), "--out", str(output_path), "--format", "json"])

    assert result.exit_code == 0
    assert "Catalog exported" in result.output
    assert output_path.exists()


class FakeAnnotationProvider:
    def annotate(self, **kwargs):
        frames = kwargs["frames"]
        duration = kwargs["video"]["duration_seconds"]
        return ProviderResult(
            segments=[
                {
                    "start_sec": 0,
                    "end_sec": duration / 2,
                    "action": "reach",
                    "object": "block",
                    "source": None,
                    "target": "bowl",
                    "state_change": None,
                    "outcome": "uncertain",
                    "label": "reach for the block",
                    "evidence_frame_indices": [0],
                },
                {
                    "start_sec": duration / 2,
                    "end_sec": duration,
                    "action": "place",
                    "object": "block",
                    "source": None,
                    "target": "bowl",
                    "state_change": "block is in bowl",
                    "outcome": "success",
                    "label": "place the block in the bowl",
                    "evidence_frame_indices": [len(frames) - 1],
                },
            ],
            provider="fixture",
            model_version="fixture-v1",
            provider_run_id="fixture-run",
            usage={"input_frames": len(frames)},
        )


def test_process_subtask_job_generates_grounded_review_artifact(tmp_path: Path) -> None:
    video_path = tmp_path / "episode.avi"
    make_video(video_path, frames=20)

    result = process_subtask_job(
        {
            "job_id": "djob_test",
            "dataset_id": "data_test",
            "source": {"video_path": str(video_path), "video_key": "observation.images.top"},
            "task_hint": "place the block in the bowl",
            "segmentation": {"sample_fps": 2, "max_frames": 8, "contact_sheet": {"columns": 4}},
            "labeling": {"taxonomy": ["reach", "place"]},
            "prompt_version": "subtask-timeline-v1",
        },
        provider=FakeAnnotationProvider(),
        allow_local_input=True,
    )

    assert result["schema_version"] == "openbot.data_processor_result.v1"
    assert result["metrics"]["segment_count"] == 2
    assert result["checks"]["manifest"]["processing"]["provider"] == "fixture"
    segments = result["annotations"]["timeline"]["segments"]
    assert segments[0]["confidence"] is None
    assert segments[1]["outcome"] == "success"
    assert segments[0]["evidence_frames"][0]["artifact_key"].startswith("evidence/")
    assert any(artifact["name"].startswith("contact_sheets/") for artifact in result["artifacts"])
    assert any(artifact["name"].startswith("evidence/") for artifact in result["artifacts"])


def test_process_subtask_job_rejects_local_paths_by_default(tmp_path: Path) -> None:
    video_path = tmp_path / "episode.avi"
    make_video(video_path)

    with pytest.raises(ProcessingError, match="local video paths are disabled"):
        process_subtask_job(
            {
                "job_id": "djob_test",
                "dataset_id": "data_test",
                "source": {"video_path": str(video_path)},
                "segmentation": {"sample_fps": 1, "contact_sheet": {"columns": 4}},
                "labeling": {"taxonomy": ["reach"]},
            },
            provider=FakeAnnotationProvider(),
        )


def test_workers_ai_provider_forwards_contact_sheets(respx_mock, tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.jpg"
    sheet.write_bytes(b"jpeg-bytes")
    route = respx_mock.post("https://annotator.example.test/v1/annotate/subtasks").mock(
        return_value=__import__("httpx").Response(
            200,
            json={
                "segments": [{"start_sec": 0, "end_sec": 1, "label": "reach"}],
                "provider": "cloudflare-workers-ai",
                "model_version": "@cf/test/model",
                "provider_run_id": "run_test",
                "usage": {"total_tokens": 12},
            },
        )
    )
    provider = CloudflareWorkersAIAnnotationProvider(
        "https://annotator.example.test/v1/annotate/subtasks",
        "annotation-secret",
        "@cf/test/model",
    )

    result = provider.annotate(
        task_hint="reach the block",
        taxonomy=["reach"],
        video={"duration_seconds": 1},
        frames=[{"timestamp": 0, "frame_id": "frame_000"}],
        contact_sheet_paths=[sheet],
        prompt_version="subtask-timeline-v1",
    )

    assert result.provider_run_id == "run_test"
    assert result.usage == {"total_tokens": 12}
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer annotation-secret"
    body = json.loads(request.content)
    assert body["contact_sheets"][0]["base64_data"] == "anBlZy1ieXRlcw=="
    assert body["frames"] == [{"index": 0, "timestamp_sec": 0}]


def test_provider_from_env_selects_workers_ai(monkeypatch) -> None:
    monkeypatch.setenv("OPENBOT_ANNOTATION_PROVIDER", "cloudflare-workers-ai")
    monkeypatch.setenv("OPENBOT_ANNOTATION_URL", "https://annotator.example.test")
    monkeypatch.setenv("OPENBOT_ANNOTATION_SECRET", "secret")

    provider = provider_from_env()

    assert isinstance(provider, CloudflareWorkersAIAnnotationProvider)


def test_processor_service_requires_auth_and_forwards_valid_jobs(monkeypatch) -> None:
    monkeypatch.setenv("OPENBOT_PROCESSOR_SECRET", "processor-secret")
    monkeypatch.setattr(
        data_service,
        "process_subtask_job",
        lambda payload: {
            "schema_version": "openbot.data_processor_result.v1",
            "job_id": payload["job_id"],
        },
    )
    client = TestClient(data_service.app)
    payload = {
        "job_id": "djob_test",
        "dataset_id": "data_test",
        "source": {"video_url": "https://cdn.example.com/video.mp4"},
        "segmentation": {"sample_fps": 1, "contact_sheet": {"columns": 5}},
        "labeling": {"taxonomy": ["reach"]},
    }

    unauthorized = client.post("/v1/process/subtasks", json=payload)
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/v1/process/subtasks",
        json=payload,
        headers={"Authorization": "Bearer processor-secret"},
    )
    assert authorized.status_code == 200
    assert authorized.json()["job_id"] == "djob_test"
