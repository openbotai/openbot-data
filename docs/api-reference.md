# API and CLI reference

All functions below are importable from `openbot_data`. The released PyPI
package and current source tree are `0.0.3`.

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

Invalid configuration, inaccessible sources, malformed artifact inputs, and
runtime failures raise an `OpenBotDataError` or `ValueError`.

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

### `detect_input_format` and `read_lerobot`

```python
detect_input_format(path: str, input_format: str = "auto") -> str
read_lerobot(path: str) -> dict
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
snapshot_hub_dataset(source: str, *, integrity="metadata", budget=None, ...) -> dict
```

### `evaluate_hub_dataset_readiness`

```python
evaluate_hub_dataset_readiness(
    source: str,
    *,
    profile: str = "lerobot-core",
    policy_config: Mapping[str, Any] | str | Path | None = None,
    integrity: str = "metadata",
    budget: HubDownloadBudget | None = None,
    output_path: str | None = None,
    ...
) -> dict
```

All three functions share the resolved revision and bounded checkout contract.
Metadata-only or budget-limited readiness is `PARTIAL`, never `READY`.
`resolver`, `revision_resolver`, `downloader`, and `viewer_validator` are
test/integration injection points and are not needed for normal use.

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

## Video helpers

The `0.0.2` helpers remain public:

```python
scan_video(video_path: str) -> VideoInfo
scan_directory(directory: str) -> dict
extract_preview_frames(video_path, output_dir, max_frames=10, output_id=None) -> dict
extract_timestamped_frames(video_path, output_dir, sample_fps=1.0, max_frames=32, max_edge=640) -> dict
build_contact_sheets(frames, output_dir, columns=5, rows=4, tile_width=320) -> dict
```

## Packaged JSON Schemas

```python
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
| `readiness` | Local or Hub profile gate |
| `repair plan`, `repair apply`, `verify` | Copy-on-write repair loop |
| `merge-check`, `verify-merge` | Official merge handoff and verification |
| `version` | Installed package version |

All commands use exit `0` for an accepted completed result, `2` for a completed
negative gate result, and `1` for invocation/configuration/access/runtime
failure. A completed exit-2 result writes its canonical JSON first.
