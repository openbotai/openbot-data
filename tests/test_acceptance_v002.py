import builtins
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import cv2
import jsonschema
import numpy as np
import pytest
from typer.testing import CliRunner

import openbot_data.preflight as preflight
from openbot_data import prepare_dataset, schema_path
from openbot_data.cli import app
from openbot_data.extract import inspect_dataset
from openbot_data.preflight import audit_dataset, read_lerobot

DOCUMENTED_FINDING_CODES = {
    "DATASET_NOT_FOUND",
    "DATASET_INVALID_ARGUMENT",
    "DATASET_PATH_OUTSIDE_ROOT",
    "LEROBOT_INFO_MISSING",
    "LEROBOT_METADATA_INVALID",
    "LEROBOT_EPISODES_MISSING",
    "LEROBOT_EPISODES_UNREADABLE",
    "LEROBOT_DEPENDENCY_MISSING",
    "LEROBOT_EPISODE_INVALID",
    "LEROBOT_EPISODE_INDEX_INVALID",
    "LEROBOT_EPISODE_COUNT_MISMATCH",
    "LEROBOT_VIDEO_MISSING",
    "LEROBOT_VIDEO_RELATION_MISSING",
    "LEROBOT_VIDEOS_MISSING",
    "VIDEO_UNREADABLE",
    "VIDEO_INVALID_FPS",
    "VIDEO_INVALID_DURATION",
    "VIDEO_INVALID_DIMENSIONS",
    "VIDEO_PREVIEW_DECODE_FAILED",
    "STREAM_INCONSISTENT_RESOLUTION",
    "STREAM_INCONSISTENT_FPS",
    "DUPLICATE_CONTENT",
}


def make_video(
    path: Path,
    *,
    frames: int = 4,
    fps: float = 5.0,
    size: tuple[int, int] = (16, 16),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"MJPG"),
        fps,
        size,
    )
    if not writer.isOpened():
        pytest.fail("OpenCV video writer is required for the acceptance fixtures")
    for frame_index in range(frames):
        frame = np.full((size[1], size[0], 3), frame_index * 30, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def codes(result: dict[str, Any]) -> set[str]:
    return {str(finding["code"]) for finding in result["findings"]}


def fixture_root(name: str) -> Path:
    return Path(__file__).parent / "fixtures" / name


@pytest.mark.parametrize(
    ("fixture_name", "video_path"),
    [
        (
            "lerobot-v2",
            Path("videos/chunk-000/observation.images.top/episode_000000.avi"),
        ),
        (
            "lerobot-v3",
            Path("videos/observation.images.top/chunk-000/file-000.avi"),
        ),
    ],
)
def test_shipped_lerobot_fixtures_connect_episode_metadata_to_video(
    tmp_path: Path,
    fixture_name: str,
    video_path: Path,
) -> None:
    root = tmp_path / fixture_name
    shutil.copytree(fixture_root(fixture_name), root)
    make_video(root / video_path)

    result = read_lerobot(str(root))

    assert result["episodes"][0]["tasks"] == ["pick"]
    assert result["episodes"][0]["video_files"] == [video_path.as_posix()]
    assert result["video_keys"] == ["observation.images.top"]
    assert not [finding for finding in result["findings"] if finding["severity"] == "error"]


def test_lerobot_v3_uses_relational_shard_metadata_for_shared_video(tmp_path: Path) -> None:
    root = tmp_path / "lerobot-v3"
    shutil.copytree(fixture_root("lerobot-v3"), root)
    video_path = root / "videos/observation.images.top/chunk-000/file-000.avi"
    make_video(video_path, frames=8)
    episode_file = root / "meta/episodes/chunk-000/file-000.jsonl"
    episode_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "episode_index": 0,
                        "length": 4,
                        "tasks": ["pick"],
                        "videos/observation.images.top/chunk_index": 0,
                        "videos/observation.images.top/file_index": 0,
                        "videos/observation.images.top/from_timestamp": 0.0,
                        "videos/observation.images.top/to_timestamp": 0.8,
                    }
                ),
                json.dumps(
                    {
                        "episode_index": 1,
                        "length": 4,
                        "tasks": ["place"],
                        "videos/observation.images.top/chunk_index": 0,
                        "videos/observation.images.top/file_index": 0,
                        "videos/observation.images.top/from_timestamp": 0.8,
                        "videos/observation.images.top/to_timestamp": 1.6,
                    }
                ),
            ]
        )
        + "\n"
    )
    info = json.loads((root / "meta/info.json").read_text())
    info["total_episodes"] = 2
    (root / "meta/info.json").write_text(json.dumps(info))

    result = read_lerobot(str(root))

    assert [item["video_files"] for item in result["episodes"]] == [
        ["videos/observation.images.top/chunk-000/file-000.avi"],
        ["videos/observation.images.top/chunk-000/file-000.avi"],
    ]
    assert result["episodes"][1]["video_segments"][0]["from_timestamp"] == 0.8
    assert "LEROBOT_VIDEO_RELATION_MISSING" not in codes(result)


def test_lerobot_v3_missing_relation_is_not_inferred_from_filename(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    (root / "meta/info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v3.0",
                "total_episodes": 1,
                "features": {"observation.images.top": {"dtype": "video"}},
            }
        )
    )
    (root / "meta/episodes.jsonl").write_text(
        json.dumps({"episode_index": 0, "length": 4}) + "\n"
    )
    make_video(root / "videos/observation.images.top/episode_000000.avi")

    assert "LEROBOT_VIDEO_RELATION_MISSING" in codes(read_lerobot(str(root)))


def test_lerobot_missing_and_malformed_metadata_findings(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert {"LEROBOT_INFO_MISSING", "LEROBOT_EPISODES_MISSING"} <= codes(
        audit_dataset(str(empty), input_format="lerobot")
    )

    malformed = tmp_path / "malformed"
    (malformed / "meta").mkdir(parents=True)
    (malformed / "meta" / "info.json").write_text("[")
    (malformed / "meta" / "episodes.jsonl").write_text("[\n42\n{}\n")

    malformed_codes = codes(audit_dataset(str(malformed), input_format="lerobot"))
    assert {
        "LEROBOT_METADATA_INVALID",
        "LEROBOT_EPISODE_INVALID",
        "LEROBOT_EPISODE_INDEX_INVALID",
    } <= malformed_codes


def test_lerobot_declared_count_and_video_reference_findings(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    (root / "meta" / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v2.1",
                "total_episodes": 2,
                "features": {"observation.images.top": {"dtype": "video"}},
            }
        )
    )
    (root / "meta" / "episodes.jsonl").write_text(
        json.dumps(
            {
                "episode_index": 0,
                "video_path": "videos/observation.images.top/episode_000000.mp4",
            }
        )
        + "\n"
    )

    result_codes = codes(audit_dataset(str(root), input_format="lerobot"))

    assert {
        "LEROBOT_EPISODE_COUNT_MISMATCH",
        "LEROBOT_VIDEO_MISSING",
        "LEROBOT_VIDEOS_MISSING",
    } <= result_codes


def test_unreadable_episode_metadata_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    info_path = root / "meta" / "info.json"
    episodes_path = root / "meta" / "episodes.jsonl"
    info_path.write_text(json.dumps({"codebase_version": "v2.1"}))
    episodes_path.write_text("{}\n")
    original_read_text = Path.read_text

    def selective_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == episodes_path:
            raise OSError("fixture read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", selective_read_text)

    assert "LEROBOT_EPISODES_UNREADABLE" in codes(
        audit_dataset(str(root), input_format="lerobot")
    )


def test_parquet_without_optional_dependency_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dataset"
    parquet_path = root / "meta" / "episodes" / "chunk-000" / "file-000.parquet"
    parquet_path.parent.mkdir(parents=True)
    (root / "meta" / "info.json").write_text(json.dumps({"codebase_version": "v3.0"}))
    parquet_path.write_bytes(b"not-needed-for-dependency-check")
    original_import = builtins.__import__

    def reject_pyarrow(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pyarrow.parquet":
            raise ImportError("pyarrow intentionally unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_pyarrow)

    assert "LEROBOT_DEPENDENCY_MISSING" in codes(
        audit_dataset(str(root), input_format="lerobot")
    )


def test_corrupt_video_reports_all_media_validation_findings(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    root.mkdir()
    (root / "broken.mp4").write_bytes(b"not-a-video")

    result_codes = codes(audit_dataset(str(root)))

    assert {
        "VIDEO_UNREADABLE",
        "VIDEO_INVALID_FPS",
        "VIDEO_INVALID_DURATION",
        "VIDEO_INVALID_DIMENSIONS",
        "VIDEO_PREVIEW_DECODE_FAILED",
    } <= result_codes


def test_dataset_symlink_outside_root_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.avi"
    make_video(outside)
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "escape.avi").symlink_to(outside)

    result = audit_dataset(str(root), follow_symlinks=True)

    assert "DATASET_PATH_OUTSIDE_ROOT" in codes(result)
    assert result["summary"]["videos"] == 0


def test_preview_failure_is_reported_for_otherwise_valid_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "videos"
    make_video(root / "camera" / "episode.avi")
    monkeypatch.setattr(
        preflight,
        "_decode_probe",
        lambda _path, _integrity, _expected_frames: (False, 0),
    )

    result_codes = codes(audit_dataset(str(root)))

    assert "VIDEO_PREVIEW_DECODE_FAILED" in result_codes
    assert "VIDEO_UNREADABLE" not in result_codes


def test_inconsistent_stream_and_opt_in_duplicate_findings(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    first = root / "camera" / "first.avi"
    second = root / "camera" / "second.avi"
    duplicate = root / "camera" / "duplicate.avi"
    make_video(first, fps=5.0, size=(16, 16))
    make_video(second, fps=10.0, size=(32, 16))
    shutil.copyfile(first, duplicate)

    without_checksum = audit_dataset(str(root))
    with_checksum = audit_dataset(str(root), checksum="sha256")

    assert {
        "STREAM_INCONSISTENT_RESOLUTION",
        "STREAM_INCONSISTENT_FPS",
    } <= codes(without_checksum)
    assert "DUPLICATE_CONTENT" not in codes(without_checksum)
    assert "DUPLICATE_CONTENT" in codes(with_checksum)


@pytest.mark.parametrize(
    ("kwargs", "expected_message"),
    [
        ({"input_format": "rlds"}, "Unsupported input format"),
        ({"checksum": "md5"}, "checksum must be omitted"),
    ],
)
def test_invalid_arguments_return_structured_finding(
    tmp_path: Path,
    kwargs: dict[str, str],
    expected_message: str,
) -> None:
    result = audit_dataset(str(tmp_path), **kwargs)

    assert codes(result) == {"DATASET_INVALID_ARGUMENT"}
    assert expected_message in result["findings"][0]["message"]


def test_cli_fail_on_policies_cover_none_warning_error_and_invalid(
    tmp_path: Path,
) -> None:
    root = tmp_path / "videos"
    first = root / "camera" / "first.avi"
    duplicate = root / "camera" / "duplicate.avi"
    make_video(first)
    shutil.copyfile(first, duplicate)
    runner = CliRunner()

    none_result = runner.invoke(
        app,
        [
            "audit",
            str(root),
            "--checksum",
            "sha256",
            "--out",
            str(tmp_path / "none.json"),
            "--fail-on",
            "none",
        ],
    )
    error_result = runner.invoke(
        app,
        [
            "audit",
            str(root),
            "--checksum",
            "sha256",
            "--out",
            str(tmp_path / "error.json"),
            "--fail-on",
            "error",
        ],
    )
    warning_result = runner.invoke(
        app,
        [
            "audit",
            str(root),
            "--checksum",
            "sha256",
            "--out",
            str(tmp_path / "warning.json"),
            "--fail-on",
            "warning",
        ],
    )
    invalid_result = runner.invoke(
        app,
        [
            "audit",
            str(root),
            "--out",
            str(tmp_path / "invalid.json"),
            "--fail-on",
            "fatal",
        ],
    )

    assert none_result.exit_code == 0
    assert error_result.exit_code == 0
    assert warning_result.exit_code == 2
    assert invalid_result.exit_code == 1


def test_plain_video_fixture_is_byte_stable_and_json_serializable(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    output = tmp_path / "inspection"
    make_video(root / "camera" / "episode.avi")

    first = inspect_dataset(str(root), str(output), input_format="video", checksum="sha256")
    first_bytes = Path(first["manifest_path"]).read_bytes()
    second = inspect_dataset(str(root), str(output), input_format="video", checksum="sha256")

    assert first_bytes == Path(second["manifest_path"]).read_bytes()
    json.dump(json.loads(first_bytes), io.StringIO(), sort_keys=True)


def test_snapshot_reuse_and_packaged_json_schemas(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    output = tmp_path / "inspection"
    make_video(root / "camera/episode.avi")
    snapshot = prepare_dataset(str(root), checksum="sha256", integrity="full")

    inspection = inspect_dataset(str(root), str(output), snapshot=snapshot)
    audit = audit_dataset(str(root), snapshot=snapshot)
    manifest = json.loads(Path(inspection["manifest_path"]).read_text())

    assert audit["summary"]["videos"] == 1
    assert manifest["videos"][0]["integrity_level"] == "full"
    assert manifest["videos"][0]["path_base"] == "dataset"
    assert manifest["videos"][0]["previews"][0]["path_base"] == "inspection"
    for name in ("manifest", "audit"):
        with schema_path(name) as path:
            payload = json.loads(path.read_text())
            assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
            instance = manifest if name == "manifest" else audit
            jsonschema.Draft202012Validator(payload).validate(instance)


def test_runnable_demo_uses_public_api_and_writes_expected_outputs(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    root = tmp_path / "videos"
    output = tmp_path / "demo-output"
    make_video(root / "camera" / "episode.avi")

    completed = subprocess.run(
        [
            sys.executable,
            str(repository / "examples" / "local_preflight.py"),
            str(root),
            "--out",
            str(output),
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["summary"]["error"] == 0
    assert (output / "inspection" / "metadata" / "manifest.json").is_file()
    assert (output / "inspection" / "metadata" / "report.json").is_file()
    assert (output / "audit.json").is_file()


def test_documented_audit_registry_and_example_cover_stable_contract() -> None:
    repository = Path(__file__).resolve().parents[1]
    registry = (repository / "docs" / "audit-findings.md").read_text()
    registered_codes = {
        line.split("`")[1]
        for line in registry.splitlines()
        if line.startswith("| `")
    }
    example = json.loads((repository / "docs" / "examples" / "audit.json").read_text())

    assert registered_codes == DOCUMENTED_FINDING_CODES
    assert example["schema_version"] == "openbot.dataset_audit.v1"
    assert "score" not in example
    assert str(repository) not in json.dumps(example)


def test_documentation_navigation_has_no_broken_local_links() -> None:
    repository = Path(__file__).resolve().parents[1]
    documents = [repository / "README.md", *sorted((repository / "docs").rglob("*.md"))]

    for document in documents:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text()):
            path = target.split("#", 1)[0]
            if not path or path.startswith(("http://", "https://", "mailto:")):
                continue
            assert (document.parent / path).resolve().exists(), (
                f"{document.relative_to(repository)} links to missing {target}"
            )
