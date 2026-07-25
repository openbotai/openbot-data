# Getting started

## Install

Install the released package:

```bash
python -m pip install openbot-data
```

Install the optional Parquet reader used by some LeRobot v3 repositories:

```bash
python -m pip install "openbot-data[lerobot]"
```

For a source checkout:

```bash
python -m pip install -e .
```

Python 3.9–3.12 is the supported release matrix.

Maintainers can run that matrix locally with `scripts/test_matrix.sh`; it uses
`uv` isolated environments and does not depend on hosted CI.

## Run the end-to-end demo

The repository demo inspects and audits one local dataset:

```bash
python examples/local_preflight.py ./robot_videos \
  --out ./openbot-preflight \
  --integrity sample
```

For LeRobot:

```bash
python examples/local_preflight.py ./lerobot_dataset \
  --format lerobot \
  --out ./openbot-preflight
```

The demo writes:

```text
openbot-preflight/
  audit.json
  inspection/
    metadata/
      manifest.json
      report.json
    previews/
```

It returns exit code `2` when the audit contains errors. Use `--no-checksum` to
skip optional SHA-256 calculation and duplicate-content detection. Use
`--integrity metadata` for header-only inspection or `--integrity full` for a
complete decode pass.

## CLI workflow

Inspect a video directory with automatic format detection:

```bash
openbot-data inspect ./robot_videos \
  --format auto \
  --checksum sha256 \
  --integrity sample \
  --out ./inspection
```

Audit it with a release gate:

```bash
openbot-data audit ./robot_videos \
  --format auto \
  --checksum sha256 \
  --integrity sample \
  --out ./audit.json \
  --fail-on error
```

Export a catalog:

```bash
openbot-data catalog ./robot_videos --out ./catalog.json --format json
openbot-data catalog ./robot_videos --out ./catalog.csv --format csv
```

`audit --fail-on none` always exits successfully after writing JSON.
`--fail-on error` exits `2` for errors, while `--fail-on warning` exits `2` for
warnings or errors. Invalid CLI arguments exit `1`.

## Python workflow

```python
from openbot_data import audit_dataset, inspect_dataset, prepare_dataset, read_lerobot

snapshot = prepare_dataset(
    "./robot_videos",
    input_format="auto",
    checksum="sha256",
    integrity="sample",
)

inspection = inspect_dataset(
    "./robot_videos",
    "./inspection",
    snapshot=snapshot,
)

audit = audit_dataset(
    "./robot_videos",
    snapshot=snapshot,
    output_path="./audit.json",
)

if audit["summary"]["error"]:
    for finding in audit["findings"]:
        if finding["severity"] == "error":
            print(finding["code"], finding["path"], finding["message"])

lerobot = read_lerobot("./lerobot_dataset")
for episode in lerobot["episodes"]:
    print(episode["episode_index"], episode["tasks"], episode["video_files"])
```

## Input layouts

For plain video mode, supported files are discovered recursively:

```text
robot_videos/
  top/
    episode-0001.mp4
  wrist/
    episode-0001.mp4
```

LeRobot detection expects `meta/info.json`, episode metadata in JSONL or Parquet,
and local files under `videos/`. V3 discovery follows the relational video table
and `info.video_path`; it does not invent v2-style per-episode filenames.
Discovery is read-only: the library does not download Hugging Face repositories
or rewrite the dataset.

## Determinism and privacy

The `openbot.dataset_manifest.v1` payload sorts videos, episodes, and streams,
uses dataset-relative paths, and derives the dataset fingerprint from normalized
metadata. SHA-256 checksums are opt-in. No local absolute path is written to the
canonical manifest by default.

Manifest video paths use `path_base: "dataset"` and preview paths use
`path_base: "inspection"`. Symlinks are skipped unless explicitly enabled, and
targets outside the dataset root are always rejected.

Audit output uses stable finding codes instead of an uncalibrated numeric quality
score. See the [finding registry](audit-findings.md).
