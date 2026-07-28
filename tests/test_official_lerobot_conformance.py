from __future__ import annotations

import json
import os
from importlib.metadata import version
from pathlib import Path

import pytest

from openbot_data.official import smoke_test_lerobot_dataset
from openbot_data.preflight import audit_dataset, prepare_dataset
from openbot_data.readiness import evaluate_dataset_readiness

pytestmark = pytest.mark.skipif(
    os.environ.get("OPENBOT_DATA_OFFICIAL_CONFORMANCE") != "1",
    reason="runs only in the pinned Python 3.12 LeRobot conformance job",
)


def test_official_v30_writer_loader_video_and_policy_conformance(
    tmp_path: Path,
) -> None:
    assert version("lerobot") == "0.6.0"

    import numpy as np
    from lerobot.configs.video import RGBEncoderConfig
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    root = tmp_path / "official-v30"
    dataset = LeRobotDataset.create(
        repo_id="openbot/official-conformance",
        root=root,
        fps=10,
        robot_type="so100",
        use_videos=True,
        rgb_encoder=RGBEncoderConfig(
            vcodec="h264",
            pix_fmt="yuv420p",
            g=2,
            crf=23,
            preset="ultrafast",
        ),
        features={
            "action": {
                "dtype": "float32",
                "shape": (2,),
                "names": ["joint_0", "joint_1"],
            },
            "observation.state": {
                "dtype": "float32",
                "shape": (3,),
                "names": ["joint_0", "joint_1", "gripper"],
            },
            "observation.images.top": {
                "dtype": "video",
                "shape": (3, 32, 32),
                "names": ["channels", "height", "width"],
            },
        },
    )
    for episode_index in range(3):
        for frame_index in range(5):
            dataset.add_frame(
                {
                    "action": np.asarray(
                        [
                            episode_index + frame_index / 10,
                            episode_index - frame_index / 10,
                        ],
                        dtype=np.float32,
                    ),
                    "observation.state": np.asarray(
                        [episode_index, frame_index / 10, frame_index % 2],
                        dtype=np.float32,
                    ),
                    "observation.images.top": np.full(
                        (32, 32, 3),
                        (episode_index * 60 + frame_index * 10) % 256,
                        dtype=np.uint8,
                    ),
                    "task": "pick" if episode_index < 2 else "place",
                }
            )
        dataset.save_episode()
    dataset.finalize()

    info_path = root / "meta/info.json"
    info = json.loads(info_path.read_text(encoding="utf-8"))
    info["future_optional_contract"] = {"retained": True}
    info_path.write_text(
        json.dumps(info, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert (root / "meta/tasks.parquet").is_file()
    assert len(list((root / "data").rglob("*.parquet"))) == 1
    assert len(list((root / "videos").rglob("*.mp4"))) == 1
    stats = json.loads((root / "meta/stats.json").read_text(encoding="utf-8"))
    assert {"q01", "q50", "q99"} <= set(stats["action"])

    prepared = prepare_dataset(
        str(root),
        input_format="lerobot",
        checksum="sha256",
        integrity="full",
    )
    assert prepared.adapter_result is not None
    assert prepared.adapter_result.raw_info["future_optional_contract"]["retained"] is True

    audit = audit_dataset(
        str(root),
        input_format="lerobot",
        checksum="sha256",
        integrity="full",
        snapshot=prepared,
    )
    assert audit["summary"] == {
        "videos": 1,
        "error": 0,
        "warning": 0,
        "info": 0,
    }

    for profile in (
        "lerobot-core",
        "training-common",
        "lerobot-act",
        "lerobot-smolvla",
    ):
        readiness = evaluate_dataset_readiness(
            str(root),
            profile=profile,
            prepared=prepared,
        )
        assert readiness["status"] == "READY"

    smoke = smoke_test_lerobot_dataset(str(root))
    assert smoke == {
        "status": "passed",
        "package": "lerobot==0.6.0",
        "loaded_episodes": 3,
        "loaded_frames": 15,
        "sample_indices": [0, 7, 14],
    }

    loaded = LeRobotDataset(
        repo_id="openbot/official-conformance",
        root=root,
    )
    for index in (0, 7, 14):
        sample = loaded[index]
        assert tuple(sample["action"].shape) == (2,)
        assert tuple(sample["observation.images.top"].shape) == (3, 32, 32)
