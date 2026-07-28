# Changelog

## 0.0.3 - 2026-07-28

### Added
- Explicit immutable LeRobot v2.1 and v3.0 adapters, including read-only v2.1
  migration guidance and direct stable-contract validation without importing
  the full LeRobot package.
- Layered collect-all metadata, schema, data, media, alignment, statistics, and
  provenance validation with declared capability coverage.
- Portable `openbot.dataset_snapshot.v1` identities and strict semantic
  `openbot.dataset_diff.v1` classification.
- Revision-pinned Hugging Face resolution with hard byte/shard/episode/media
  budgets and explicit metadata/sample/full coverage.
- Built-in LeRobot core, common training, Hub publication, ACT, and SmolVLA
  readiness profiles plus strict policy-config overrides and deterministic
  JSON/Markdown gates.
- Actionable-location triage and raw advisory evidence for flatline actions,
  zero-variance state, duplicate episodes, and camera/task imbalance, including
  non-executing synchronized idle-trim plans and no aggregate dataset score.
- Deterministic repair plans, copy-on-write allowlisted metadata repair, and
  verified receipts with stale-source and partial-destination protection.
- Four-way merge compatibility plans and post-operation verification for the
  pinned official `lerobot-edit-dataset` merge.
- Optional pinned official LeRobot loader smoke tests in repair and merge CLI
  verification.
- Pinned `lerobot-doctor==0.2.0` and `robovet==0.2.2` outcome-comparison
  evidence for the critical stale-frame-counter workflow.
- Python 3.9–3.12 CI and local matrix coverage for the Parquet and Hub extras,
  preventing optional-feature tests from being silently skipped.
- A separate Python 3.12 conformance job that creates and finalizes a real
  shared-shard video dataset with `lerobot[dataset]==0.6.0`, then proves full
  audit, ACT/SmolVLA readiness, quantile/unknown-field compatibility, and
  official first/middle/last loader access.
- `build_catalog_evidence()` and the `catalog-evidence` CLI command for a
  score-free, versioned handoff from one prepared local audit to OpenBot
  Catalog.
- The packaged `catalog-evidence-v1` JSON Schema, deterministic cross-language
  typed-tree fingerprint, portable source identity, coverage, evidence maturity,
  facts, findings, and unresolved-check reporting.
- A versioned `openbot.dataset_catalog.v1` JSON catalog schema and shared
  immutable-snapshot rendering across inspect, audit, and catalog output.
- Structured findings for malformed episode lengths, duplicate indexes, invalid
  video relations and paths, segment bounds/overlap/duration, and explicit
  symlink skip/broken states.

### Changed
- V3 data-shard episode ranges are validated as dataset-global offsets, matching
  the official multi-shard LeRobot 0.6.0 writer and merge tool.
- Canonical video features are no longer incorrectly required as data-Parquet
  columns; LeRobot v3 stores them in `videos/**`.
- Snapshot Schema accepts the official writer's `names: null` standard-column
  contract while retaining strict component and top-level fingerprints.
- `sample` integrity now probes deterministic start, middle, and end positions
  while preserving the existing manifest v1 decoded-count projection and
  fingerprint behavior.
- LeRobot v2.1 and v3 discovery now checks every declared camera for every
  episode and validates shared-video segment bounds with a one-frame duration
  tolerance.
- Caller-supplied snapshots are validated against the requested root, resolved
  input format, checksum coverage, integrity coverage, and symlink policy before
  any renderer writes output.

### Documentation
- Defined the complete `0.0.3` feature and release contract, including the
  canonical P0 feature map, public artifacts and commands, implementation
  checkpoint, integrity/coverage semantics, uniform exit classes, scope
  boundaries, implementation order, and release acceptance criteria.

## 0.0.2

### Added
- Single-source package versioning with CI and release-tag validation.
- Versioned, reproducible `openbot.dataset_manifest.v1` output with relative paths,
  dataset fingerprints, and optional SHA-256 checksums.
- Read-only local LeRobot v2.1/v3 discovery for episodes and video streams.
- Deterministic dataset audit findings, duplicate detection, and `audit` CLI command.
- Runnable local preflight demo, developer documentation, API/CLI reference, and
  shipped LeRobot acceptance fixtures.
- Acceptance scenarios for every documented finding code and a local Python
  3.9–3.12 test-matrix command.

### Changed
- `inspect` now supports `--format auto|video|lerobot` and optional checksums.
- Unreadable media now continues through the specific FPS, duration, dimensions,
  and preview-decode audit rules instead of stopping at the generic finding.

## 0.0.1.post2

### Added
- `export_catalog()` Python API and `openbot-data catalog` CLI command for JSON/CSV catalog export.
- SEO-optimized README and `pyproject.toml` keywords (`robot data`, `ego data`, `egocentric data`, `embodied AI`, etc.).
- GitHub Actions CI workflow for Python 3.9–3.12.
- This changelog.

## 0.0.1.post1

### Added
- Initial preview release.
- Video directory scanning and metadata extraction.
- Preview frame extraction.
- Manifest and report generation.
- `openbot-data` CLI with `scan`, `inspect`, and `version` commands.
