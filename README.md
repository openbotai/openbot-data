# OpenBot Data

**Clean, label, and organize robot data for training and evaluation.**

OpenBot Data is a lightweight toolkit for cleaning, labeling, organizing, and preparing robot data for model training, evaluation, failure analysis, and deployment debugging.

> Robots do not just need more data.
> They need cleaner data, better labels, and harder failure cases.

---

## Current Status

**OpenBot Data is in early development.**

The current release (0.0.1.post2) provides basic robot video inspection and dataset metadata generation:

- Scan robot video directories
- Extract video metadata (duration, fps, resolution, frame count)
- Extract preview frames for quick inspection
- Generate dataset manifest
- Generate basic quality reports

Planned features include dataset triage, quality-based filtering, annotation export, and failure case mining.

---

## Why OpenBot Data?

Robot data is messy.

Raw robot recordings often contain:

- Motion blur
- Camera shake
- Dark or overexposed frames
- Hand, gripper, or object occlusion
- Target objects moving out of view
- Repeated or invalid clips
- Failed actions mixed with successful ones
- Unclear task phases
- Noisy or incomplete annotations
- Long-tail failure cases
- Data that should not enter the training set

OpenBot Data helps robotics teams convert raw robot data into structured datasets that can be used for training, evaluation, benchmarking, and deployment iteration.

---

## First focus: ego-centric robot data

The first public version of OpenBot Data starts with ego-centric robot videos because they are one of the most common and messy data sources in embodied AI.

This includes:

- First-person robot videos
- Wrist-camera videos
- Close-range manipulation videos
- Robot vision clips
- Failure clips from real-world deployment
- Hard cases from evaluation runs

OpenBot Data helps turn these raw videos into clean, annotation-ready datasets and reusable failure cases.

---

## What it does

OpenBot Data focuses on four core workflows:

### 1. Clean robot data

Detect and organize low-quality frames and clips, including:

- Blurry frames
- Dark scenes
- Duplicate frames
- Camera shake
- Invalid clips
- Target-out-of-view cases
- Heavy occlusion
- Low-quality training samples

### 2. Prepare data for annotation

Convert raw robot data into annotation-ready datasets.

Planned annotation types include:

- Object labels
- Bounding boxes
- Task phases
- Success / failure labels
- Failure reasons
- Scene conditions
- Hand or gripper occlusion
- Object visibility
- Trainable / not-trainable flags

### 3. Mine failure cases

Find hard cases that matter for real-world robot deployment:

- Motion blur failures
- Hand or gripper occlusion failures
- Target lost from view
- Dark-scene failures
- Reflective object failures
- Small-object failures
- Failed grasp or manipulation moments
- Long-tail deployment failures

### 4. Export structured datasets

Export cleaned data for training, evaluation, and benchmarking.

Planned formats:

- OpenBot JSON
- COCO
- YOLO
- WebDataset
- Parquet
- LeRobot-compatible datasets
- RLDS-compatible datasets

---

## Installation

```bash
pip install openbot-data
```

---

## Quickstart (v0.0.1.post2)

```bash
# Scan robot videos and inspect metadata
openbot-data scan ./robot_videos

# Full inspection: extract frames and generate reports
openbot-data inspect ./robot_videos --out ./openbot_dataset
```

Example output:

```text
openbot_dataset/
  previews/              # Preview frames from videos
  metadata/
    manifest.json        # Dataset manifest with video metadata
    report.json          # Quality metrics summary
```

---

## Usage

### CLI

```bash
openbot-data scan ./videos                    # Scan and show video metadata
openbot-data inspect ./videos --out ./dataset  # Full inspection pipeline
```

### Python API

```python
from openbot_data import scan_directory, inspect_dataset

# Scan videos
result = scan_directory("./robot_videos")
print(f"Found {result['valid_videos']} videos")

# Inspect and extract preview frames, manifest, and report
result = inspect_dataset(
    video_dir="./robot_videos",
    output_dir="./dataset",
)

print(result["manifest_path"])
print(result["report_path"])
```

### Development checks

```bash
pip install -e ".[dev]"
pytest
python -m build
python -m twine check dist/*
```

---

## Example use cases

OpenBot Data can be used for:

* Robot vision dataset cleaning
* Ego-centric video preparation
* Wrist-camera data processing
* Embodied AI data preparation
* Failure case mining
* Deployment evaluation data preparation
* Synthetic data generation input curation
* Robot benchmark dataset construction
* Training / validation / test split management

---

## Roadmap

### v0.0.1.post2 — Installable Preview (Current)
* [x] Robot video scanner
* [x] Video metadata extraction (duration, fps, resolution, frame count)
* [x] Dataset manifest generation
* [x] Basic quality reports (JSON)
* [x] Preview frame extraction

### v0.0.2 — Dataset Triage
* [ ] Blur score detection (Laplacian variance)
* [ ] Brightness score detection
* [ ] Black frame detection
* [ ] Basic duplicate-like frame detection
* [ ] keep / review / drop grouping
* [ ] HTML triage reports

### v0.0.3 — Annotation Batch
* [ ] Quality-based sampling
* [ ] Annotation task generation
* [ ] Trainable/not-trainable flags

### v0.0.4 — Export
* [ ] OpenBot JSON schema
* [ ] COCO export
* [ ] YOLO export

### v0.1.0 — Robot Data Cleaning Pipeline
* [ ] Stable cleaning, triage, sampling, and export workflow
* [ ] Failure case tagging
* [ ] Multi-view robot data support
* [ ] LeRobot dataset compatibility
* [ ] RLDS compatibility

---

## OpenBot ecosystem

OpenBot Data is part of the OpenBot robotics intelligence infrastructure.

Planned modules:

* **OpenBot Data** — clean, label, and organize robot data
* **OpenBot Bench** — evaluate robot models before deployment
* **OpenBot Synth** — generate hard cases from failures
* **OpenBot API** — connect data, evaluation, and deployment workflows

---

## License

MIT License.

---

## Citation

If you use OpenBot Data in research or production, please cite the project repository.

```bibtex
@software{openbot_data,
  title = {OpenBot Data},
  author = {OpenBot},
  year = {2026},
  url = {https://github.com/openbotai/openbot-data}
}
```
