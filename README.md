# OpenBot Data

Inspect robot video datasets before training.

OpenBot Data is an early Python package for scanning robot video directories,
extracting basic video metadata, saving preview frames, and generating dataset
manifests and quality summaries.

## Install

```bash
pip install openbot-data
```

## What is included

Current version: `0.0.1.post2`

- Scan robot video directories
- Read video metadata: duration, fps, resolution, frame count, file size
- Extract preview frames for quick inspection
- Generate `manifest.json`
- Generate `report.json`

Supported video extensions: `.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`.

## CLI

```bash
openbot-data scan ./robot_videos
openbot-data scan ./robot_videos --output scan.json

openbot-data inspect ./robot_videos --out ./openbot_dataset
openbot-data version
```

Inspection output:

```text
openbot_dataset/
  previews/
  metadata/
    manifest.json
    report.json
```

## Python API

```python
from openbot_data import scan_directory, inspect_dataset

scan = scan_directory("./robot_videos")
print(scan["valid_videos"])

result = inspect_dataset(
    video_dir="./robot_videos",
    output_dir="./openbot_dataset",
)

print(result["manifest_path"])
print(result["report_path"])
```

## Development

```bash
pip install -e ".[dev]"
pytest
python -m build
python -m twine check dist/*
```

## Status

OpenBot Data is in early preview. The current release focuses on video
inspection and dataset metadata generation.

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
