# OpenBot Data

> Inspect, catalog, and prepare robot video data for embodied AI and robot learning.

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

Current version: `0.0.1.post2`

- **Scan robot video directories** — recursively find `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm` files.
- **Read video metadata** — duration, fps, resolution, frame count, file size, validity.
- **Extract preview frames** — uniformly sample frames for quick human inspection.
- **Generate manifests** — `manifest.json` with per-video metadata and preview paths.
- **Generate quality reports** — `report.json` with aggregate duration, size, and resolutions.
- **Export dataset catalogs** — JSON or CSV for dataset registries, documentation, and SEO-friendly catalog pages.
- **Process subtask annotation jobs** — download a signed video URL, decode and sample timestamped frames, build contact sheets, call a structured VLM provider, and return evidence-backed review suggestions.

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

## Data processor service

The container entrypoint exposes the real worker used by `POST /v1/data/subtask-jobs`:

```bash
pip install -e ".[service]"
export OPENBOT_PROCESSOR_SECRET="replace-me"
export GEMINI_API_KEY="..."
export OPENBOT_ANNOTATION_MODEL="gemini-3.5-flash"
uvicorn openbot_data.service:app --host 0.0.0.0 --port 8080
```

```http
POST /v1/process/subtasks
Authorization: Bearer $OPENBOT_PROCESSOR_SECRET
```

The processor accepts an HTTP(S) video source and returns:

- real video metadata and decoding failures;
- timestamped evidence frames and contact sheets;
- canonical action/object/source/target/state-change/outcome segments;
- provider, model, prompt, sampling and usage provenance;
- `confidence: null` until OpenBot has a calibrated evaluation set.

Remote input rejects credentials, private/loopback addresses, unsafe redirects, and downloads over the configured size limit. Model output remains `needs_review`; the public API is responsible for R2 persistence, immutable review revisions, approved export gating, and billing.

## Use cases

- Prepare **teleoperation data** collected from ALOHA, Mobile ALOHA, VR, or SpaceMouse setups.
- Inspect **egocentric video datasets** before converting to LeRobot, RLDS, or HDF5.
- Build a **robot dataset catalog** that links back to your project website or Hugging Face collection.
- Validate video integrity before running robot policy training or VLA pre-training.

## Development

```bash
pip install -e ".[dev]"
pytest
python -m build
python -m twine check dist/*
```

## Status

OpenBot Data is in early preview. The library and container now provide the real video preprocessing and annotation-provider data plane. Production availability still depends on deploying the processor and configuring the OpenBot API service binding/secret.

## Roadmap

- [x] Video scanning and metadata extraction
- [x] Preview frame extraction
- [x] Manifest and report generation
- [x] JSON/CSV catalog export
- [x] Timestamped frame sampling and contact sheets
- [x] Authenticated subtask processor service and structured Gemini adapter
- [ ] LeRobot dataset format reader
- [ ] RLDS / HDF5 ingestion helpers
- [ ] Per-episode quality scoring

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
