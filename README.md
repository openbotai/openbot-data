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

Requires Python 3.9+.

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

## Python API

```python
from openbot_data import (
    audit_dataset,
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

OpenBot Data is an early local Python toolkit. Hosted upload, annotation, review,
export, billing, Workers, and Container infrastructure belong to the main
[OpenBot](https://github.com/openbotai/OpenBot) product repository. Use the
separate [`openbot-sdk`](https://github.com/openbotai/openbot-sdk) package to call
the hosted API.

## Roadmap

- [x] Video scanning and metadata extraction
- [x] Preview frame extraction
- [x] Manifest and report generation
- [x] JSON/CSV catalog export
- [x] [`0.0.2`: local dataset preflight](docs/version-0.0.2.md), including a
      versioned manifest, deterministic audit findings, and local LeRobot discovery
- [ ] [`0.0.3`: LeRobot compatibility and dataset change control](docs/version-0.0.3.md),
      centered on diagnostic outcome parity, readiness profiles, conservative
      copy-on-write repair, merge safety, portable snapshots, and semantic diff
- [ ] Later: read-only robomimic/HDF5 and RLDS/Open X adapters
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
