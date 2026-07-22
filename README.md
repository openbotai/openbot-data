# OpenBot Data

> Inspect, catalog, and prepare robot video data for embodied AI and robot learning.

[Data product](https://openbot.ai/data) · [Data API documentation](https://openbot.ai/api/docs#data-recipe) · [Source repository](https://github.com/openbotai/openbot-data)

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

## What it does

- **Scan robot video directories** — recursively find `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm` files.
- **Read video metadata** — duration, fps, resolution, frame count, file size, validity.
- **Extract preview frames** — uniformly sample frames for quick human inspection.
- **Generate manifests** — `manifest.json` with per-video metadata and preview paths.
- **Generate quality reports** — `report.json` with aggregate duration, size, and resolutions.
- **Export dataset catalogs** — JSON or CSV for dataset registries, documentation, and SEO-friendly catalog pages.

## CLI

### Scan videos

```bash
openbot-data scan ./robot_videos
openbot-data scan ./robot_videos --output scan.json
```

### Inspect a dataset

```bash
openbot-data inspect ./robot_videos --out ./openbot_dataset
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
from openbot_data import scan_directory, inspect_dataset, export_catalog

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
python -m build
python -m twine check dist/*
```

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
- [ ] [`0.0.2`: local dataset preflight](docs/version-0.0.2.md), including a
      versioned manifest, deterministic audit findings, and local LeRobot discovery
- [ ] Later: RLDS / HDF5 ingestion helpers
- [ ] Later: calibrated quality evaluation after a labeled validation set exists

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
