# OpenBot Data 0.0.2 — Local Dataset Preflight

> Status: Released and publicly verified
> Previous package: `0.0.1.post2`
> Release version: `0.0.2`

This document is the release contract for the local `openbot-data` Python package.
The functionality below shipped in `openbot-data==0.0.2`. Its successor is
documented in the [`0.0.3` release contract](version-0.0.3.md).

## Goal

`0.0.2` should answer one question before a robot-video dataset is used for
training or publication:

> Can this local video directory or local LeRobot dataset be read consistently,
> and what concrete issues must be fixed first?

The package remains a local inspection tool. Platform authentication, remote
jobs, billing, and server infrastructure are outside its scope. At this release,
OpenBot did not expose a robot-data processing API that this package called.

## `0.0.1.post2` baseline carried into `0.0.2`

Before `0.0.2`, the package already provided:

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
- resolve v3 shared shards from relational video metadata and the declared
  `info.video_path` template rather than assuming v2 filenames;
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
- symlink targets that escape the dataset root.

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

Advanced callers can create one immutable `DatasetSnapshot` with
`prepare_dataset(...)` and pass it to both `inspect_dataset(..., snapshot=...)`
and `audit_dataset(..., snapshot=...)`. Integrity is explicit:
`metadata|sample|full`.

Compatibility requirements:

- existing calls without new arguments keep their `0.0.1` behavior;
- `--fail-on error` exits non-zero only when an error finding exists;
- malformed input produces structured findings instead of an unhandled traceback;
- JSON output is the canonical machine-readable format.
- `path_base` and `path_bases` make dataset and inspection-root paths
  unambiguous without exposing machine-local absolute paths.
- packaged manifest/audit JSON Schemas are available through `schema_path(...)`.

### 5. Documentation and fixtures

Ship small fixtures covering a plain video directory and representative local
LeRobot v2.1/v3 metadata. Document every finding code and provide one complete
manifest and audit example without machine-specific paths.

The implemented code registry is [`audit-findings.md`](audit-findings.md), with
canonical examples under [`examples/`](examples/).

## Release acceptance criteria

The version is complete only when:

- the same fixture produces byte-stable canonical JSON across two runs;
- video-directory behavior remains backward compatible;
- local LeRobot v2.1 and v3 fixtures discover the expected episodes/video keys;
- missing/corrupt input returns the documented rule code and CLI exit status;
- SHA-256 duplicate detection is opt-in and tested;
- Python 3.9–3.12 matrix, package build, and Twine metadata checks pass;
- the README examples run from a clean installation.

## Acceptance coverage

The source acceptance suite is organized as follows:

- `tests/test_openbot_data.py`: backward-compatible scan, inspect, preview, and
  catalog behavior;
- `tests/test_preflight.py`: deterministic manifest, LeRobot v2.1/v3 discovery,
  checksum opt-in behavior, CLI exit policy, and canonical examples;
- `tests/test_acceptance_v002.py`: shipped fixtures, every documented finding
  code, malformed input, stream consistency, all `--fail-on` policies, and the
  runnable public-API demo;
- `examples/local_preflight.py`: end-to-end local preflight demo executed by the
  acceptance suite.

The complete suite is required on every supported Python interpreter.

## Explicit non-goals

- remote platform jobs, API authentication, or billing;
- Hugging Face remote download or private object-store access;
- RLDS/HDF5 readers or dataset conversion/writing;
- VLM annotation, subtask labeling, or automatic dataset correction;
- an uncalibrated numeric quality score;
- paid features or production SLA claims.

These exclusions keep `openbot-data` a small local preflight tool and avoid
mixing local data processing with responsibilities of a generic platform API
client.
