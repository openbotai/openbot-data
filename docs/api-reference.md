# API and CLI reference

All Python functions below are importable from `openbot_data`. The released
PyPI package is `0.0.3`; the source checkout still declares the same version
but includes later changes. The `review` CLI command described below is
source-only and absent from that published wheel (checked 2026-10-02).

## Coverage and result semantics

`integrity` is always one of:

- `metadata`: metadata, schema, identity, and explicitly skipped payload checks;
- `sample`: deterministic first/middle/last shard and media selection;
- `full`: every required data row and media stream.

Canonical gates return completed negative results instead of raising:

- readiness: `READY`, `BLOCKED`, or `PARTIAL`;
- diff: `unchanged`, `non_breaking`, `material`, or `breaking`;
- merge: `direct`, `transform_required`, `incompatible`, or `unknown`;
- repair/merge receipts: verified or unverified/failed.

Failure behavior depends on the entry point. Preparation, snapshot, readiness,
repair, and merge operations normally raise `OpenBotDataError` or `ValueError`
for invalid configuration or unusable inputs. `audit_dataset` instead converts
`DatasetArgumentError` and `DatasetNotFoundError` into canonical error findings;
`inspect_dataset` returns an `error` field for those two failure classes.
Malformed artifacts and other runtime failures are not universally converted
into findings. Check both the entry point's result and its documented gate.

## Discovery, inspection, and audit

### `prepare_dataset`

```python
prepare_dataset(
    path: str,
    input_format: str = "auto",
    checksum: str | None = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
) -> DatasetSnapshot
```

Discovers one local video directory or LeRobot dataset exactly once. The
immutable result contains adapter output, validation measurements, episodes,
videos, artifacts, relations, and capability coverage. Pass it to compatible
renderers to avoid rescanning.

### `inspect_dataset`

```python
inspect_dataset(
    video_dir: str,
    output_dir: str,
    input_format: str = "video",
    checksum: str | None = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
    snapshot: DatasetSnapshot | None = None,
) -> dict
```

Writes `metadata/manifest.json`, `metadata/report.json`, and preview images.
Manifest v1 identity remains compatible with `0.0.2`.
On an invalid preparation request or missing directory it returns
`{"error": "...", "videos": []}` before writing inspection output.

### `audit_dataset`

```python
audit_dataset(
    path: str,
    input_format: str = "auto",
    checksum: str | None = None,
    output_path: str | None = None,
    *,
    integrity: str = "sample",
    follow_symlinks: bool = False,
    snapshot: DatasetSnapshot | None = None,
) -> dict
```

Returns `openbot.dataset_audit.v1`. Validation collects safely discoverable
metadata, schema, data, media, alignment, statistics, and provenance findings.
Every registered finding carries stable impact, fixability, and remediation
metadata; skipped checks remain explicit.
Missing directories and invalid preparation requests are represented by
`DATASET_NOT_FOUND` and `DATASET_INVALID_ARGUMENT` findings respectively.

### `detect_input_format` and `read_lerobot`

```python
detect_input_format(path: str, input_format: str = "auto") -> str
read_lerobot(path: str, *, follow_symlinks: bool = False) -> dict
```

`read_lerobot` uses explicit read-only `lerobot_v21` and `lerobot_v30`
adapters. V2.1 produces an official migration recommendation and is never
rewritten. V3 follows shared data/video shard relations and dataset-global
episode offsets.

## Portable snapshot and semantic diff

### `build_dataset_snapshot`

```python
build_dataset_snapshot(
    path: str,
    *,
    input_format: str = "auto",
    checksum: str | None = "sha256",
    integrity: str = "sample",
    follow_symlinks: bool = False,
    source_kind: str = "local",
    source_locator: str | None = None,
    requested_revision: str | None = None,
    resolved_revision: str | None = None,
    source_coverage: Mapping[str, Any] | None = None,
    snapshot: DatasetSnapshot | None = None,
    output_path: str | None = None,
) -> dict
```

Returns `openbot.dataset_snapshot.v1`: a secret-free source identity, format
contract, features, tasks, episodes, stream contracts, inventory, totals,
coverage, 11 component digests, and one top-level fingerprint. Hub snapshots
require a 40-character immutable commit.

### `diff_dataset_snapshots`

```python
diff_dataset_snapshots(
    baseline: Mapping[str, Any] | str | Path,
    candidate: Mapping[str, Any] | str | Path,
    *,
    output_path: str | None = None,
) -> dict
```

Strictly validates both snapshot Schemas and every component fingerprint before
returning `openbot.dataset_diff.v1`. Feature removal or dtype/shape changes are
breaking; episode/payload changes are material; additive metadata and coverage
changes are non-breaking.

## Revision-pinned Hugging Face audit

### Source syntax and budgets

```python
from openbot_data import HubDownloadBudget

budget = HubDownloadBudget(
    max_bytes=2_000_000_000,
    max_shards=12,
    max_episodes=64,
    max_media_shards=9,
)
```

Hub sources use `hf://datasets/org/name@revision`. A branch or tag is resolved
once to an immutable commit. Standard Hugging Face credentials are read by
`huggingface_hub`; credentials are never serialized.

### `audit_hub_dataset`

```python
audit_hub_dataset(
    source: str,
    *,
    checksum: str | None = "sha256",
    integrity: str = "metadata",
    follow_symlinks: bool = False,
    budget: HubDownloadBudget | None = None,
    cache_dir: str | None = None,
    local_dir: str | None = None,
    output_path: str | None = None,
) -> dict
```

### `snapshot_hub_dataset`

```python
snapshot_hub_dataset(
    source: str,
    *,
    checksum: str | None = "sha256",
    integrity: str = "metadata",
    follow_symlinks: bool = False,
    budget: HubDownloadBudget | None = None,
    cache_dir: str | None = None,
    local_dir: str | None = None,
    output_path: str | None = None,
) -> dict
```

### `evaluate_hub_dataset_readiness`

```python
evaluate_hub_dataset_readiness(
    source: str,
    *,
    profile: str = "lerobot-core",
    policy_config: Mapping[str, Any] | str | Path | None = None,
    checksum: str | None = "sha256",
    integrity: str = "metadata",
    follow_symlinks: bool = False,
    budget: HubDownloadBudget | None = None,
    cache_dir: str | None = None,
    local_dir: str | None = None,
    output_path: str | None = None,
) -> dict
```

All three functions share the resolved revision and bounded checkout contract.
Metadata-only or budget-limited readiness is `PARTIAL`, never `READY`.
`resolver`, `revision_resolver`, `downloader`, and `viewer_validator` are
test/integration injection points and are not needed for normal use.
They are omitted from the signatures above. Install the `hub` extra for network
access and `lerobot` for Parquet metadata/payload reads.

`parse_hub_source(source=None, *, repo_id=None, revision=None)` returns a
`HubSourceRequest`. `resolve_hub_dataset(...)` returns a `HubResolution` with
the resolved commit, provenance, coverage, findings, and optional local checkout.
Pass `download=False` for resolution without a payload checkout. These lower-level
types and functions are also exported from `openbot_data`.

## Readiness, triage, and advisory evidence

### `load_readiness_profile`

```python
load_readiness_profile(profile: str) -> dict
```

Built-ins are `lerobot-core`, `training-common`, `hf-publication`,
`lerobot-act`, and `lerobot-smolvla`. They are versioned package data.

### `evaluate_dataset_readiness`

```python
evaluate_dataset_readiness(
    path: str,
    *,
    profile: str = "lerobot-core",
    policy_config: Mapping[str, Any] | str | Path | None = None,
    input_format: str = "auto",
    checksum: str | None = "sha256",
    integrity: str = "full",
    follow_symlinks: bool = False,
    prepared: DatasetSnapshot | None = None,
    dataset_snapshot: Mapping[str, Any] | None = None,
    audit_result: Mapping[str, Any] | None = None,
    source_kind: str = "local",
    source_locator: str | None = None,
    requested_revision: str | None = None,
    resolved_revision: str | None = None,
    measurements: Mapping[str, Any] | None = None,
    publication_metadata: Mapping[str, Any] | None = None,
    output_path: str | None = None,
) -> dict
```

Returns `openbot.dataset_readiness.v1`. An actual policy config replaces the
built-in feature/action/camera/normalization/delta-horizon contract. Externally
supplied snapshots and audits are schema-, fingerprint-, identity-, and
coverage-validated before use.

### `render_readiness_markdown`

```python
render_readiness_markdown(readiness: Mapping[str, Any]) -> str
```

Produces a deterministic human projection of the canonical JSON.

### `triage_findings` and `analyze_advisory_signals`

```python
triage_findings(findings: Iterable[Mapping[str, Any]]) -> list[dict]
analyze_advisory_signals(
    snapshot: Mapping[str, Any],
    *,
    thresholds: Mapping[str, Any] | None = None,
    measurements: Mapping[str, Any] | None = None,
) -> list[dict]
```

Triage groups by episode, feature, camera, shard, or dataset. Advisory signals
expose raw values, thresholds, threshold sources, applicability, and coverage;
they never produce a total score.

## Catalog handoff

### `export_catalog`

```python
export_catalog(
    video_dir: str,
    output_path: str,
    fmt: str = "json",
    *,
    input_format: str = "auto",
    checksum: str | None = None,
    integrity: str = "sample",
    follow_symlinks: bool = False,
    snapshot: DatasetSnapshot | None = None,
) -> dict
```

JSON output uses `openbot.dataset_catalog.v1`; CSV compatibility is unchanged.

### `build_catalog_evidence`

```python
build_catalog_evidence(
    path: str,
    *,
    dataset_id: str,
    checked_at: str,
    source_kind: str = "local",
    source_locator: str | None = None,
    resolved_revision: str | None = None,
    input_format: str = "auto",
    checksum: str | None = "sha256",
    integrity: str = "sample",
    follow_symlinks: bool = False,
    profile_id: str = "lerobot-core",
    rule_pack_version: str = "openbot.dataset_audit.rules.v1",
    output_path: str | None = None,
) -> dict
```

Produces deterministic `catalog-evidence-v1` facts, findings, readiness, and
coverage for server-side Catalog evaluation. `checked_at` must be an explicit
timezone-aware RFC 3339 timestamp. Caller-supplied scores or publication
decisions are outside this contract.

## Conservative repair

```python
plan_dataset_repair(
    path: str,
    *,
    input_format: str = "auto",
    checksum: str | None = "sha256",
    integrity: str = "metadata",
    follow_symlinks: bool = False,
    output_path: str | None = None,
) -> dict

apply_dataset_repair(
    path: str,
    plan: Mapping[str, Any] | str | Path,
    *,
    output_path: str,
    loader_runner: Callable | None = None,
) -> dict

verify_dataset_repair(
    path: str,
    *,
    against: Mapping[str, Any] | str | Path,
    loader_runner: Callable | None = None,
    output_path: str | None = None,
) -> dict
```

The plan is `openbot.dataset_repair_plan.v1`; apply/verify return
`openbot.dataset_repair_receipt.v1`. P0 automatically changes only uniquely
derived integer totals in `meta/info.json`. Apply refuses stale plans, stages a
copy, validates its exact expected tree hash, and atomically reveals a new
destination. It never mutates the source. Payload edits, task remaps,
timestamps, NaN/Inf values, trimming, and ambiguous relations remain delegated.
The automatic allowlist is exactly `total_episodes`, `total_frames`,
`total_tasks`, and `total_videos`; episode boundaries and stored normalization
statistics are not rewritten. A field is eligible only when its derivation is
unambiguous at the plan's coverage level.
Apply refuses a destination that already exists, even if a previous run created
it; choose a new destination rather than expecting an in-place or idempotent rerun.

The CLI automatically runs the pinned official loader smoke when
`lerobot[dataset]==0.6.0` is installed; otherwise the receipt remains
unverified.

## Merge compatibility and verification

```python
check_merge_compatibility(
    inputs: Sequence[Mapping[str, Any] | str | Path],
    *,
    profile: str = "lerobot-act",
    task_remap: Mapping[str, str] | None = None,
    output_path: str | None = None,
) -> dict

verify_dataset_merge(
    merged: Mapping[str, Any] | str | Path,
    *,
    input_snapshots: Sequence[Mapping[str, Any] | str | Path],
    profile: str = "lerobot-act",
    operation_record: Mapping[str, Any] | None = None,
    loader_runner: Callable | None = None,
    output_path: str | None = None,
) -> dict
```

The plan is `openbot.dataset_merge_plan.v1` and never executes its command.
Physical merge is delegated to `lerobot-edit-dataset` from
`lerobot[dataset]==0.6.0`. Verification requires direct preconditions, a full
SHA-256 post-snapshot, error-free full audit, official loader smoke, exact
operation lineage, semantic reconciliation, and non-regressive diffs before
issuing `openbot.dataset_merge_receipt.v1` as verified.

### Operation record

`--operation-record` consumes caller-captured JSON evidence of an actual
successful external merge. OpenBot does not execute that command or generate
this record automatically; the official CLI must not be assumed to emit the
OpenBot record format.

| Field | Required value |
|---|---|
| `tool` | `lerobot-edit-dataset` |
| `package` | `lerobot==0.6.0` |
| `operation` | `merge` |
| `command` | Actual argument-token list containing `lerobot-edit-dataset`, `--new_repo_id`, `--new_root`, `--operation.type`, `merge`, `--operation.repo_ids`, and `--operation.roots`; legacy `--repo_id` is rejected |
| `exit_code` | Integer `0` from the real invocation |
| `input_snapshot_fingerprints` | Fingerprints of the input snapshots in the order expected by the compatibility plan |
| `output_snapshot_fingerprint` | Fingerprint of the actual merged output's full SHA-256 snapshot |

Without that record or an official loader result, verification writes an
unverified receipt. Record validation checks consistency with the snapshots;
it cannot independently prove that caller-supplied command evidence is truthful.

## Video helpers

The `0.0.2` helpers remain public:

```python
scan_video(video_path: str) -> VideoInfo
scan_directory(directory: str, *, absolute_paths: bool = False) -> dict
extract_preview_frames(video_path, output_dir, max_frames=10, output_id=None) -> dict
extract_timestamped_frames(video_path, output_dir, sample_fps=1.0, max_frames=32, max_edge=640) -> dict
build_contact_sheets(frames, output_dir, columns=5, rows=4, tile_width=320) -> dict
```

## Packaged JSON Schemas

```python
from openbot_data import schema_path

with schema_path("snapshot") as path:
    ...
```

Accepted keys are:

| Key | Artifact |
|---|---|
| `manifest` | `openbot.dataset_manifest.v1` |
| `audit` | `openbot.dataset_audit.v1` |
| `catalog` | `openbot.dataset_catalog.v1` |
| `catalog_evidence` | `catalog-evidence-v1` |
| `snapshot` | `openbot.dataset_snapshot.v1` |
| `diff` | `openbot.dataset_diff.v1` |
| `readiness` | `openbot.dataset_readiness.v1` |
| `repair_plan` / `repair_receipt` | repair artifacts |
| `merge_plan` / `merge_receipt` | merge artifacts |

## CLI commands and exit classes

| Command | Purpose |
|---|---|
| `scan`, `inspect`, `audit`, `catalog`, `catalog-evidence` | Discovery and projections |
| `snapshot`, `diff` | Portable identity and change classification |
| `review` | Source-only local read-only LeRobot v3 episode and audit workbench |
| `readiness` | Local or Hub profile gate |
| `repair plan`, `repair apply`, `verify` | Copy-on-write repair loop |
| `merge-check`, `verify-merge` | Official merge handoff and verification |
| `version` | Installed package version |

Artifact-producing commands use application exit `0` for an accepted completed
result, `2` for a completed negative gate result, and `1` for handled
configuration/access/runtime failure. A completed negative gate writes its
canonical JSON first. Parser errors such as a missing required option or an
unknown command may also exit `2`, without an artifact; exit code alone does not
establish that an audit completed.

`review` is a long-running local server. It binds to `127.0.0.1`, exits with
`Ctrl-C`, and does not write to the dataset. It needs `openbot-data[lerobot]`;
video preview additionally needs `ffmpeg` on `PATH`. Use `--port` to select a
port or `--no-open` to leave browser opening to the caller.
The default port is `8766`; `--port 0` selects an available port. It exposes a
startup metadata audit rather than a full readiness gate and writes no canonical
audit/readiness artifact. See [Review limits and troubleshooting](review.md).
