# Changelog

## Unreleased

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
