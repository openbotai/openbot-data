"""End-to-end checks for the local read-only LeRobot episode reviewer."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from openbot_data.errors import DatasetArgumentError
from openbot_data.review import ReviewHTTPServer, ReviewWorkspace, _inside


def _dataset(root: Path, *, real_video: bool) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    camera = "observation.images.top"
    (root / "meta/episodes/chunk-000").mkdir(parents=True)
    (root / "data/chunk-000").mkdir(parents=True)
    video = root / f"videos/{camera}/chunk-000/file-000.mp4"
    video.parent.mkdir(parents=True)
    info = {
        "codebase_version": "v3.0",
        "fps": 10,
        "total_episodes": 2,
        "total_frames": 10,
        "total_tasks": 2,
        "splits": {"train": "0:2"},
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "features": {
            "observation.state": {"dtype": "float32", "shape": [2]},
            "action": {"dtype": "float32", "shape": [2]},
            camera: {"dtype": "video", "shape": [32, 32, 3]},
        },
    }
    (root / "meta/info.json").write_text(json.dumps(info), encoding="utf-8")
    (root / "meta/stats.json").write_text("{}", encoding="utf-8")
    pq.write_table(
        pa.table({"task_index": [0, 1], "task": ["pick", "place"]}),
        root / "meta/tasks.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "episode_index": [0, 1],
                "length": [5, 5],
                "tasks": [["pick"], ["place"]],
                "meta/episodes/chunk_index": [0, 0],
                "meta/episodes/file_index": [0, 0],
                "data/chunk_index": [0, 0],
                "data/file_index": [0, 0],
                "dataset_from_index": [0, 5],
                "dataset_to_index": [5, 10],
                f"videos/{camera}/chunk_index": [0, 0],
                f"videos/{camera}/file_index": [0, 0],
                f"videos/{camera}/from_timestamp": [0.0, 0.5],
                f"videos/{camera}/to_timestamp": [0.5, 1.0],
            }
        ),
        root / "meta/episodes/chunk-000/file-000.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "index": list(range(10)),
                "episode_index": [0] * 5 + [1] * 5,
                "frame_index": list(range(5)) * 2,
                "timestamp": [i / 10 for i in range(5)] * 2,
                "task_index": [0] * 5 + [1] * 5,
                "observation.state": pa.array(
                    [[float(e), float(f)] for e in range(2) for f in range(5)],
                    type=pa.list_(pa.float32(), 2),
                ),
                "action": pa.array(
                    [[float(e + f), float(f)] for e in range(2) for f in range(5)],
                    type=pa.list_(pa.float32(), 2),
                ),
            }
        ),
        root / "data/chunk-000/file-000.parquet",
    )
    if real_video:
        subprocess.run(
            [
                str(shutil.which("ffmpeg")), "-nostdin", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=32x32:rate=10:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
            ],
            check=True,
            capture_output=True,
        )
    else:
        video.write_bytes(b"video-not-decodable")


def test_review_model_reuses_audit_and_samples_episode_traces(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _dataset(root, real_video=False)
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    episodes_path = root / "meta/episodes/chunk-000/file-000.parquet"
    episodes = pq.read_table(episodes_path)
    episodes = episodes.set_column(
        episodes.schema.get_field_index("length"), "length", pa.array([5, 6])
    )
    pq.write_table(episodes, episodes_path)
    workspace = ReviewWorkspace(str(root), ffmpeg="")
    try:
        overview = workspace.overview()
        assert [item["index"] for item in overview["episodes"]] == [0, 1]
        assert overview["summary"]["error"] >= 1  # invalid synthetic video is visible
        assert overview["episodes"][1]["findings"]["error"] >= 1
        episode = workspace.episode(1)
        assert episode["tasks"] == ["place"]
        assert any(
            finding["code"] == "LEROBOT_EPISODE_RANGE_LENGTH_MISMATCH"
            for finding in episode["findings"]
        )
        assert episode["traces"]["frames"] == list(range(5))
        assert episode["traces"]["series"]["observation.state"][3] == [1.0, 3.0]
        assert episode["videos"][0]["from_timestamp"] == 0.5
        with pytest.raises(ValueError):
            _inside(root, "../outside.parquet")
    finally:
        workspace.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required for video preview")
def test_review_http_video_range_and_source_immutability(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _dataset(root, real_video=True)
    original = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }
    workspace = ReviewWorkspace(str(root))
    server = ReviewHTTPServer(workspace, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/", timeout=10) as response:
            assert b"OpenBot Data" in response.read()
        with urlopen(base + "/api/overview", timeout=10) as response:
            overview = json.load(response)
        assert overview["summary"]["error"] == 0, workspace.audit["findings"]
        assert overview["audit"]["integrity"] == "metadata"
        assert "video.preview.decode.failed" in {
            check["rule_id"] for check in overview["audit"]["skipped_checks"]
        }
        assert overview["episodes"][0]["cameras"] == 1
        with urlopen(base + "/api/episode?index=0", timeout=10) as response:
            assert json.load(response)["traces"]["frames"] == list(range(5))
        with urlopen(
            Request(
                base + "/api/clip?index=0&camera=observation.images.top",
                headers={"Range": "bytes=0-99"},
            ),
            timeout=30,
        ) as response:
            assert response.status == 206
            assert len(response.read()) == 100
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/api/overview", data=b"{}", method="POST"), timeout=10)
        assert error.value.code == 405
        current = {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()
        }
        assert current == original
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        workspace.close()


def test_review_rejects_non_v3_dataset(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "meta").mkdir(parents=True)
    (root / "meta/info.json").write_text('{"codebase_version":"v2.1"}', encoding="utf-8")
    with pytest.raises(DatasetArgumentError, match="LeRobot v3"):
        ReviewWorkspace(str(root))


@pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink"])
def test_damaged_traces_do_not_hide_episode_evidence(tmp_path: Path, damage: str) -> None:
    root = tmp_path / "dataset"
    _dataset(root, real_video=False)
    data = root / "data/chunk-000/file-000.parquet"
    if damage == "corrupt":
        data.write_bytes(b"broken parquet")
    else:
        data.unlink()
        if damage == "symlink":
            outside = tmp_path / "outside.parquet"
            outside.write_bytes(b"must not read this")
            data.symlink_to(outside)
    workspace = ReviewWorkspace(str(root), ffmpeg="")
    try:
        episode = workspace.episode(0)
        assert episode["tasks"] == ["pick"]
        assert episode["videos"]
        assert episode["findings"]
        assert episode["traces"]["frames"] == []
        assert episode["traces"]["error"]
    finally:
        workspace.close()


def test_shared_video_findings_are_visible_for_every_affected_episode(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _dataset(root, real_video=False)
    workspace = ReviewWorkspace(str(root), ffmpeg="")
    try:
        assert all(ep["findings"]["error"] > 0 for ep in workspace.overview()["episodes"])
        for index in (0, 1):
            assert "VIDEO_UNREADABLE" in {
                finding["code"] for finding in workspace.episode(index)["findings"]
            }
    finally:
        workspace.close()


def test_preview_cache_eviction_preserves_active_streams(tmp_path: Path, monkeypatch) -> None:
    from openbot_data import review

    root = tmp_path / "dataset"
    _dataset(root, real_video=False)
    monkeypatch.setattr(review, "_MAX_CACHE_BYTES", 8)

    def fake_transcode(command, **kwargs):
        Path(command[-1]).write_bytes(b"preview!")

    monkeypatch.setattr(review.subprocess, "run", fake_transcode)
    workspace = ReviewWorkspace(str(root), ffmpeg="test-ffmpeg")
    try:
        with workspace.open_clip(0, "observation.images.top") as first:
            first_path = Path(first.name)
            with workspace.open_clip(1, "observation.images.top") as second:
                assert first_path.exists()
                assert second.read() == b"preview!"
            assert first.read() == b"preview!"
            assert first_path.exists()
        with workspace.open_clip(1, "observation.images.top") as second:
            assert not first_path.exists()
            assert second.read() == b"preview!"
        assert sum(path.stat().st_size for path in Path(workspace._cache.name).iterdir()) <= 8
    finally:
        workspace.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required for UI regression tests")
def test_review_ui_regressions() -> None:
    result = subprocess.run(
        [str(shutil.which("node")), "--test", str(Path(__file__).with_name("review_ui.test.cjs"))],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
