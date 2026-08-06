# Getting started

## Install

Install the released package:

```bash
python -m pip install openbot-data
```

PyPI currently provides `0.0.3`. Its P0 interfaces and release gates passed
before publication.

Install the optional Parquet reader required for layered LeRobot validation:

```bash
python -m pip install "openbot-data[lerobot]"
```

For a source checkout:

```bash
python -m pip install -e .
```

Hub audit needs the Hub extra. Official repair/merge loader verification runs
only in the pinned conformance environment:

```bash
python -m pip install -e ".[hub]"
python -m pip install "lerobot[dataset]==0.6.0"
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

Audit it with a severity gate:

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

Produce score-free evidence for server-side Catalog evaluation:

```bash
openbot-data catalog-evidence ./robot_videos \
  --dataset-id org/dataset \
  --checked-at 2026-07-28T12:00:00Z \
  --out ./catalog-evidence.json
```

`audit --fail-on none` always exits successfully after writing JSON.
`--fail-on error` exits `2` for errors, while `--fail-on warning` exits `2` for
warnings or errors. Invalid CLI arguments exit `1`.

## 0.0.3 preflight workflow

Capture and compare portable identities:

```bash
openbot-data snapshot ./lerobot_dataset \
  --format lerobot --integrity full --out ./before.snapshot.json

openbot-data diff ./before.snapshot.json ./after.snapshot.json \
  --out ./dataset.diff.json --fail-on breaking
```

Evaluate built-in ACT or SmolVLA readiness and write deterministic Markdown:

```bash
openbot-data readiness ./lerobot_dataset \
  --profile lerobot-act \
  --integrity full \
  --out ./act.readiness.json \
  --markdown ./act.readiness.md

openbot-data readiness ./lerobot_dataset \
  --profile lerobot-smolvla \
  --integrity full \
  --out ./smolvla.readiness.json
```

Audit a Hub branch/tag under explicit budgets; the artifact records the resolved
immutable commit and every omitted capability:

```bash
openbot-data audit hf://datasets/org/name@main \
  --integrity metadata \
  --hub-max-bytes 2000000000 \
  --hub-max-shards 12 \
  --out ./hub.audit.json
```

Plan and apply only safe metadata repairs to a new destination:

```bash
openbot-data repair plan ./dataset --integrity full --out ./repair.plan.json
openbot-data repair apply ./dataset \
  --plan ./repair.plan.json \
  --output ./dataset.repaired
openbot-data verify ./dataset.repaired \
  --against ./repair.plan.json \
  --out ./repair.receipt.json
```

Check compatibility before invoking the official merge tool, then verify its
result:

```bash
openbot-data merge-check ./dataset-a ./dataset-b --out ./merge.plan.json
openbot-data verify-merge ./merged \
  --input ./dataset-a.snapshot.json \
  --input ./dataset-b.snapshot.json \
  --operation-record ./official-operation.json \
  --out ./merge.receipt.json
```

`repair apply` and `verify-merge` automatically attempt the official loader
smoke when exactly `lerobot[dataset]==0.6.0` is installed. Without it, the
result is explicitly unverified and exits `2`.

## Python workflow

```python
from openbot_data import (
    audit_dataset,
    build_catalog_evidence,
    build_dataset_snapshot,
    diff_dataset_snapshots,
    evaluate_dataset_readiness,
    inspect_dataset,
    prepare_dataset,
    read_lerobot,
)

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

evidence = build_catalog_evidence(
    "./robot_videos",
    dataset_id="org/dataset",
    checked_at="2026-07-28T12:00:00Z",
    output_path="./catalog-evidence.json",
)

portable = build_dataset_snapshot(
    "./lerobot_dataset",
    input_format="lerobot",
    integrity="full",
)
readiness = evaluate_dataset_readiness(
    "./lerobot_dataset",
    profile="lerobot-act",
    integrity="full",
    dataset_snapshot=portable,
)
change = diff_dataset_snapshots(portable, portable)
assert readiness["status"] in {"READY", "BLOCKED", "PARTIAL"}
assert change["classification"] == "unchanged"
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
Local discovery is read-only. The Hub resolver downloads only the immutable,
budgeted working set requested by `metadata`, `sample`, or `full`. Repair writes
a distinct copy; merge execution remains an explicit official LeRobot action.

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
