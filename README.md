# OpenBot Data

> Inspect, catalog, and prepare robot video data for embodied AI and robot learning.

[Library documentation](docs/README.md) · [API reference](docs/api-reference.md) ·
[Data product](https://openbot.ai/data) · [Source repository](https://github.com/openbotai/openbot-data)

OpenBot Data is an early Python toolkit for working with **robot data** — especially
**egocentric video** and **teleoperation data** collected from wrist cameras,
head-mounted cameras, and robot demonstrators.

Use it to scan video directories, extract preview frames, build dataset manifests,
and export a searchable **robot dataset catalog** (JSON/CSV) before training or
evaluation.

## Install

```bash
pip install openbot-data
```

PyPI currently installs the released `0.0.3` package. Its P0 interfaces,
supported Python matrix, official LeRobot conformance, build, and clean-install
release gates pass. Requires Python 3.9+.

## Runnable demo

Clone the repository, install the package, and run the complete local preflight
against a video directory or local LeRobot repository:

```bash
pip install -e .
python examples/local_preflight.py ./robot_videos --out ./openbot-preflight --integrity sample
```

The demo uses the public Python API and writes
`inspection/metadata/manifest.json`, `inspection/metadata/report.json`, and
`audit.json`. Add `--format lerobot` for a local LeRobot v2.1/v3 repository,
`--integrity full` to decode every frame, or `--no-checksum` to skip SHA-256
duplicate detection. The demo discovers and hashes the dataset once, then shares
one immutable `DatasetSnapshot` across both renderers.

## What it does

- **Scan robot video directories** — recursively find `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm` files.
- **Read video metadata** — duration, fps, resolution, frame count, file size, validity.
- **Extract preview frames** — uniformly sample frames for quick human inspection.
- **Generate manifests** — `manifest.json` with per-video metadata and preview paths.
- **Generate quality reports** — `report.json` with aggregate duration, size, and resolutions.
- **Export dataset catalogs** — JSON or CSV for dataset registries, documentation, and SEO-friendly catalog pages.
- **Discover local LeRobot data** — read-only episode and video-stream discovery for v2.1/v3 layouts.
- **Audit before training or upload** — stable error/warning codes without an uncalibrated quality score.
- **Snapshot and diff datasets** — portable component fingerprints and semantic change classes.
- **Gate ACT and SmolVLA readiness** — distinguish `READY`, `BLOCKED`, and incomplete `PARTIAL` evidence.
- **Audit pinned Hub revisions** — resolve branches/tags to immutable commits under hard download budgets.
- **Repair conservatively** — plan and apply only unambiguous derived metadata changes to a new copy.
- **Verify official merges** — check compatibility before merge, then reconcile lineage, loader, audit, and diff evidence.
- **Hand off score-free Catalog evidence** — emit versioned facts, coverage,
  findings, and unresolved checks for server-side evaluation.

## CLI

### Scan videos

```bash
openbot-data scan ./robot_videos
openbot-data scan ./robot_videos --output scan.json
```

### Inspect a dataset

```bash
openbot-data inspect ./robot_videos --out ./openbot_dataset
openbot-data inspect ./lerobot_dataset --format lerobot --out ./openbot_dataset
openbot-data audit ./robot_videos --out ./audit.json --fail-on error
```

Output structure:

```text
openbot_dataset/
  previews/
  metadata/
    manifest.json
    report.json
```

### Export a robot dataset catalog

```bash
# JSON catalog for dataset registries and documentation
openbot-data catalog ./robot_videos --out ./catalog.json --format json

# CSV catalog for spreadsheets or Hugging Face dataset cards
openbot-data catalog ./robot_videos --out ./catalog.csv --format csv
```

The catalog is designed to be checked into GitHub or linked from a dataset registry
page such as [OpenBot.ai Datasets](https://openbot.ai/datasets).

### Build Catalog evidence from a completed local audit

```bash
openbot-data catalog-evidence ./robot_videos \
  --dataset-id org/dataset \
  --checked-at 2026-07-28T12:00:00Z \
  --out ./catalog-evidence.json
```

The artifact contains no Overall score or normalized dimension score. OpenBot
Catalog remains responsible for evaluation, review, and publication.

### Snapshot, diff, and readiness

```bash
openbot-data snapshot ./lerobot_dataset \
  --format lerobot --integrity full --out ./dataset.snapshot.json

openbot-data diff ./baseline.snapshot.json ./dataset.snapshot.json \
  --out ./dataset.diff.json --fail-on breaking

openbot-data readiness ./lerobot_dataset \
  --profile lerobot-smolvla --integrity full \
  --out ./readiness.json --markdown ./readiness.md
```

### Repair and merge verification

```bash
openbot-data repair plan ./dataset --integrity full --out ./repair.plan.json
openbot-data repair apply ./dataset \
  --plan ./repair.plan.json --output ./dataset.repaired

openbot-data merge-check ./dataset-a ./dataset-b --out ./merge.plan.json
openbot-data verify-merge ./merged \
  --input ./dataset-a.snapshot.json \
  --input ./dataset-b.snapshot.json \
  --operation-record ./official-operation.json \
  --out ./merge.receipt.json
```

Physical merge stays with `lerobot-edit-dataset` from
`lerobot[dataset]==0.6.0`; OpenBot only plans and verifies it.

## Python API

```python
from openbot_data import (
    audit_dataset,
    build_catalog_evidence,
    build_dataset_snapshot,
    diff_dataset_snapshots,
    evaluate_dataset_readiness,
    export_catalog,
    inspect_dataset,
    prepare_dataset,
    read_lerobot,
    scan_directory,
    schema_path,
)

# Scan a directory
scan = scan_directory("./robot_videos")
print(scan["valid_videos"])

# Inspect and extract previews
result = inspect_dataset(
    video_dir="./robot_videos",
    output_dir="./openbot_dataset",
)
print(result["manifest_path"])
print(result["report_path"])

# Export a catalog for documentation / SEO
export_catalog(
    video_dir="./robot_videos",
    output_path="./catalog.json",
    fmt="json",
)

# Discover/hash once and reuse the immutable snapshot across renderers
snapshot = prepare_dataset("./robot_videos", checksum="sha256", integrity="sample")
inspection = inspect_dataset("./robot_videos", "./inspection", snapshot=snapshot)
audit = audit_dataset("./robot_videos", snapshot=snapshot)

# Discover episodes and camera streams in a local LeRobot repository
lerobot = read_lerobot("./lerobot_dataset")

# Produce score-free evidence for server-side Catalog evaluation
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

# Packaged JSON Schemas are stable independently of the package version
with schema_path("manifest") as manifest_schema:
    print(manifest_schema)
```

## Use cases

- Prepare **teleoperation data** collected from ALOHA, Mobile ALOHA, VR, or SpaceMouse setups.
- Inspect **egocentric video datasets** before converting to LeRobot, RLDS, or HDF5.
- Build a **robot dataset catalog** that links back to your project website or Hugging Face collection.
- Validate video integrity before running robot policy training or VLA pre-training.

## Development

```bash
pip install -e ".[dev]"
python scripts/check_version.py
pytest
scripts/test_matrix.sh
python -m build
python -m twine check dist/*
```

The local matrix script requires [`uv`](https://docs.astral.sh/uv/) and tests
Python 3.9–3.12 without requiring a repository CI workflow.

`VERSION` is the package version source of truth. To release, update `VERSION`
and `CHANGELOG.md`, verify locally, then publish a GitHub Release whose tag is
`v<version>`. The release workflow validates the tag before publishing to PyPI.

## Status

The released PyPI package and current source version are both `0.0.3`.
`v0.0.3` points to commit
`7c4974c0ce53a93ea101529e6bd9565146da0d78`; the GitHub Release workflow
published its wheel and source distribution to PyPI. OpenBot Data remains an
early local Python toolkit. It does not provide platform authentication,
billing, remote jobs, or server infrastructure. The main
[OpenBot](https://github.com/openbotai/OpenBot) repository is an API-first
platform framework, and the separate
[`openbot-sdk`](https://github.com/openbotai/openbot-sdk) package calls only APIs
that the platform has actually published. Future robot/ego-data API adapters are
research directions, not current package or platform promises.

## Roadmap

- [x] Video scanning and metadata extraction
- [x] Preview frame extraction
- [x] Manifest and report generation
- [x] JSON/CSV catalog export
- [x] [`0.0.2`: local dataset preflight](docs/version-0.0.2.md), including a
      versioned manifest, deterministic audit findings, and local LeRobot discovery
- [x] [`0.0.3`: LeRobot compatibility and dataset change control](docs/version-0.0.3.md),
      with correctness closure, explicit v2.1/v3 adapters, layered validation,
      portable snapshots, semantic diff, revision-pinned Hub audit, readiness
      profiles, evidence triage, conservative repair, and merge verification.
      P0 interfaces, clean install, Python matrix, artifacts, and official
      conformance passed before the public `0.0.3` release.
- [ ] [`0.0.4`: read-only robomimic/HDF5 preflight](docs/version-0.0.4.md), with
      file-source detection, bounded structural and payload validation, portable
      snapshot/diff/readiness evidence, and optional P1 Rerun handoff
- [ ] `0.0.5`: read-only RLDS/Open X adapter
- [ ] Later: advanced episode-quality signals and any aggregate score only after
      labeled downstream validation exists

Audit codes are documented in [`docs/audit-findings.md`](docs/audit-findings.md).
The research and differentiation decisions behind `0.0.3` are documented in
[`docs/reference-libraries.md`](docs/reference-libraries.md).
Canonical manifests use explicit `path_base`/`path_bases` fields; they never
serialize private source paths. Symlinks are skipped by default, and even when
`follow_symlinks=True` a target outside the dataset root is rejected.

## License

MIT

## Citation

If you use OpenBot Data in research or production, cite the project repository:

```bibtex
@software{openbot_data,
  title = {OpenBot Data},
  author = {OpenBot},
  year = {2026},
  url = {https://github.com/openbotai/openbot-data}
}
```

## Related

- [OpenBot.ai](https://openbot.ai) — Robot dataset catalog and policy evaluation platform.
- [OpenBot.ai Datasets](https://openbot.ai/datasets) — Searchable index of egocentric and robot datasets.
