# OpenBot Data 0.0.2 — Local Dataset Preflight

> Status: Planned
> Current package: `0.0.1.post2`
> Target package: `0.0.2`

This document is the release contract for the local `openbot-data` Python package.
It describes planned work, not functionality available in the current package.

## Goal

`0.0.2` should answer one question before a robot-video dataset is uploaded or
used for training:

> Can this local video directory or local LeRobot dataset be read consistently,
> and what concrete issues must be fixed first?

The package remains a local inspection tool. Hosted uploads, asynchronous jobs,
private artifacts, review, and billing belong to the OpenBot API and
`openbot-sdk`.

## Current `0.0.1.post2` baseline

The current package already provides:

- recursive robot-video discovery and basic video metadata;
- validity checks for readable frames, dimensions, FPS, and duration;
- preview-frame and contact-sheet extraction;
- JSON manifest and aggregate report generation;
- JSON/CSV catalog export;
- CLI commands for `scan`, `inspect`, `catalog`, and `version`.

These capabilities remain supported in `0.0.2`.

## Required `0.0.2` functionality

### 1. Versioned, reproducible manifest

Introduce `openbot.dataset_manifest.v1` as an output schema independent from the
package version. The manifest must include:

- normalized paths relative to the dataset root by default;
- input format, creation tool version, and schema version;
- deterministic ordering of videos, episodes, and camera streams;
- file size and media metadata;
- a stable dataset fingerprint derived from normalized metadata;
- optional SHA-256 file checksums when explicitly requested.

Running the same command twice on unchanged input must produce the same content,
except for fields explicitly documented as timestamps. Absolute local paths must
not be emitted by default.

### 2. Local LeRobot dataset discovery

Add read-only discovery for local LeRobot v2.1 and v3 layouts:

- detect supported local layouts with `--format auto|video|lerobot`;
- list episodes, episode indexes, declared tasks, and available video keys;
- connect episode metadata to its local video files;
- report missing metadata, missing video streams, and invalid episode references;
- expose a Python `read_lerobot(...)` entry point and optional `lerobot` extra.

This release does not download Hugging Face repositories and does not rewrite or
convert LeRobot datasets.

### 3. Deterministic audit findings

Add `audit_dataset(...)` and an `openbot-data audit` CLI command. Findings use
stable codes, `error|warning|info` severity, evidence, and an actionable message.
At minimum, audit:

- unreadable or zero-frame video;
- invalid FPS, duration, or dimensions;
- preview extraction failure;
- missing LeRobot episode metadata or referenced video;
- inconsistent camera resolution or FPS within one stream;
- duplicate content when SHA-256 checking is enabled.

`0.0.2` must not invent an aggregate “quality score” or model confidence. It may
report counts by severity and rule code only.

### 4. CLI and Python contract

The intended public surface is:

```bash
openbot-data inspect ./dataset --format auto --out ./inspection
openbot-data audit ./dataset --format auto --out audit.json --fail-on error
openbot-data inspect ./dataset --checksum sha256 --out ./inspection
```

```python
from openbot_data import audit_dataset, inspect_dataset, read_lerobot

inspection = inspect_dataset("./dataset", "./inspection", input_format="auto")
audit = audit_dataset("./dataset", input_format="auto")
episodes = read_lerobot("./lerobot_dataset")
```

Compatibility requirements:

- existing calls without new arguments keep their `0.0.1` behavior;
- `--fail-on error` exits non-zero only when an error finding exists;
- malformed input produces structured findings instead of an unhandled traceback;
- JSON output is the canonical machine-readable format.

### 5. Documentation and fixtures

Ship small fixtures covering a plain video directory and representative local
LeRobot v2.1/v3 metadata. Document every finding code and provide one complete
manifest and audit example without machine-specific paths.

## Release acceptance criteria

The version is complete only when:

- the same fixture produces byte-stable canonical JSON across two runs;
- video-directory behavior remains backward compatible;
- local LeRobot v2.1 and v3 fixtures discover the expected episodes/video keys;
- missing/corrupt input returns the documented rule code and CLI exit status;
- SHA-256 duplicate detection is opt-in and tested;
- Python 3.9–3.12 CI, package build, and Twine metadata checks pass;
- the README examples run from a clean installation.

## Explicit non-goals

- Hosted upload, job polling, cancellation, artifacts, API keys, or billing;
- Hugging Face remote download or private object-store access;
- RLDS/HDF5 readers or dataset conversion/writing;
- VLM annotation, subtask labeling, or automatic dataset correction;
- an uncalibrated numeric quality score;
- paid features or production SLA claims.

These exclusions keep `openbot-data` a small local preflight tool and avoid
duplicating responsibilities owned by `openbot-sdk` and the hosted API.
