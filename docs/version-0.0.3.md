# OpenBot Data 0.0.3 — LeRobot Preflight and Dataset Change Control

> Status: **Released** as `openbot-data==0.0.3` on PyPI; all P0 interfaces and
> release gates passed.
> Baseline package: `0.0.2`
> Primary compatibility target: `lerobot==0.6.0`, dataset format `v3.0`
> Research: [reference libraries and differentiation](reference-libraries.md)

## Status and scope authority

This document is the single source of truth for the `openbot-data==0.0.3`
package release. It defines the required user outcomes, public interfaces,
machine-readable artifacts, compatibility boundary, non-goals, and release
acceptance criteria.

The status terms in this document are normative:

- **released** means a versioned package has passed every release criterion and
  is publicly available;
- **implemented** means code and focused tests exist in the current source tree,
  but the feature
  is not a released `0.0.3` capability until the complete release gate passes;
- **planned** means the behavior is required by this contract but is not yet
  accepted as implemented;
- **deferred** means the behavior is explicitly outside `0.0.3` and cannot
  silently become a release blocker.

PyPI and the source package version are `0.0.3`. P0.1–P0.11, clean-install
artifacts, the supported Python matrix, packaged examples/Schemas, pinned
LeRobot conformance, and the final release checks passed. Tag `v0.0.3` points
to `7c4974c0ce53a93ea101529e6bd9565146da0d78`; GitHub Release workflow
`30333600797` completed successfully and published the wheel and source
distribution.

Every P0 item below is required for `0.0.3`. P1 candidates and later-version
items are not required and must not be pulled into the release gate without an
explicit version-contract update.

## Goal

`0.0.3` should answer six questions before a robot dataset is trained,
migrated, uploaded, or published:

1. Is this local or Hub-hosted dataset structurally compatible with the targeted
   stable LeRobot format?
2. Is it ready for a declared training or publication profile, or was only part
   of the dataset checked?
3. Which episode, frame, feature, camera, or shard is affected?
4. What safe, actionable remediation can resolve each blocker?
5. Can two individually valid datasets be combined without breaking their
   feature, timing, or provenance contracts?
6. What changed from the last approved dataset snapshot?

## Canonical `0.0.3` feature set

This table is the complete release-level feature map. The detailed P0 sections
below define the behavior and acceptance evidence for each row.

| ID | Required feature | User-visible outcome | Primary contract |
|---|---|---|---|
| P0.1 | `0.0.2` correctness closure | Existing local audits are safe to build on: deterministic multi-position media probes, structured malformed-input findings, snapshot-request validation, correct symlink semantics, duplicate episode detection, complete camera relations, valid segment bounds, and bounded Parquet reads | compatible manifest/audit v1 output |
| P0.2 | Explicit LeRobot v2.1 and v3.0 adapters | A user can inspect a known v2.1 dataset or validate a stable LeRobot 0.6.0/v3.0 dataset without installing the full LeRobot package in the core environment | `lerobot_v21` and `lerobot_v30` internal adapters |
| P0.3 | Layered collect-all validation | One audit reports every safely discoverable metadata, schema, data, media, alignment, and provenance problem with stable locations and evidence | `openbot.dataset_audit.v1` |
| P0.4 | Portable dataset snapshot | A user can capture a deterministic, secret-free dataset identity with component-level digests and explicit coverage | `openbot.dataset_snapshot.v1` |
| P0.5 | Semantic dataset diff | A user can compare two snapshots and distinguish breaking, material, non-breaking, and unchanged results | `openbot.dataset_diff.v1` |
| P0.6 | Revision-pinned Hugging Face audit | A branch or tag is resolved to an immutable revision; metadata, sample, and full modes report exactly what was and was not checked | Hub source resolver plus audit/snapshot provenance |
| P0.7 | Adapter and rule architecture | Discovery happens once, rules declare required capabilities, skipped checks are explicit, and all renderers consume the same prepared dataset | internal source/adapter/rule contracts |
| P0.8 | Readiness profiles and deterministic gates | A user can distinguish format validity from `READY`, `BLOCKED`, or `PARTIAL` readiness for a declared training or publication contract | `openbot.dataset_readiness.v1` |
| P0.9 | Evidence triage, advisory signals, and Catalog handoff | Findings are grouped at actionable locations; low-cost signals remain evidence rather than scores; Catalog receives versioned facts without a package-owned scoring formula | canonical JSON/Markdown and `catalog-evidence-v1` |
| P0.10 | Verified remediation and conservative repair | A user can generate a deterministic repair plan, apply only unambiguous derived-value fixes to a new destination, and verify the result without mutating the source | repair plan and receipt v1 artifacts |
| P0.11 | Merge compatibility and post-operation verification | A user can determine whether datasets are directly compatible, need a transform, are incompatible, or remain unknown, then verify an official-tool merge | merge plan and receipt v1 artifacts |

### Implementation checkpoint

As of 2026-07-30, every P0 row has code and acceptance evidence, the complete
`0.0.3` release gate passed, and the package is publicly available. The table
records the released capability set:

| ID | Current state | Existing foundation that may be reused |
|---|---|---|
| P0.1 | Released | deterministic multi-position probes, structured malformed-input findings, symlink/path safety, bounded Parquet reads, snapshot-request validation, and `0.0.2` compatibility regression tests |
| P0.2 | Released | explicit immutable v2.1/v3.0 adapters, unknown-field preservation, stable-version detection, and non-mutating official v2.1 migration guidance |
| P0.3 | Released | collect-all metadata/schema/data/media/alignment/provenance validation, global v3 shard offsets, stored/recomputed statistics, and explicit capability coverage |
| P0.4 | Released | public secret-free `openbot.dataset_snapshot.v1`, strict Schema, 11 component digests, Hub provenance, and byte-stable output |
| P0.5 | Released | strict snapshot validation and `unchanged`/`non_breaking`/`material`/`breaking` semantic diff |
| P0.6 | Released | revision-pinned Hub resolver, credential-safe cache materialization, hard budgets, publication metadata, and metadata/sample/full coverage |
| P0.7 | Released | one prepared adapter result, capability-declaring static rule registry, explicit skipped checks, and shared renderers |
| P0.8 | Released | versioned core/training/publication/ACT/SmolVLA profiles, strict policy-config override, deterministic JSON/Markdown, and READY/BLOCKED/PARTIAL gates |
| P0.9 | Released | actionable-location triage, raw advisory measurements and thresholds, non-executing synchronized idle-trim plans, deterministic Markdown, and score-free Catalog evidence |
| P0.10 | Released | deterministic plan, stale-plan refusal, allowlisted copy-on-write apply, atomic destination publication, re-audit/diff/official-loader receipt |
| P0.11 | Released | four-way compatibility plan, pinned official command template, operation/lineage reconciliation, full post-audit, loader smoke, semantic reconciliation, and diff verification |

Changing a row to **implemented** requires linked code, packaged schemas where
applicable, positive and negative fixtures, public-interface documentation, and
green supported-version CI. Changing the release status to **released** requires
every criterion in this document, not merely every row in this checkpoint.

The version theme is:

> **LeRobot-first preflight with diagnostic outcome parity, plus OpenBot-owned
> evidence, snapshots, provenance, semantic diff, and verified remediation.**

OpenBot Data must reach core LeRobot preflight outcome parity. Its
differentiation is not fewer essential checks. It is a stable evidence contract,
revision provenance, deterministic snapshots, semantic diff, conservative
copy-on-write repair, and a verified remediation loop that can later serve raw
robot video and additional dataset formats.

## Product principle: outcome parity, implementation differentiation

A capability is not excluded merely because LeRobot, `lerobot-doctor`, or
`robovet` already provides it. If a capability is required to complete the
preflight workflow safely, `openbot-data 0.0.3` must provide the user outcome
directly or through a stable, verifiable handoff.

Outcome parity means a user can:

1. audit a local or revision-pinned Hub dataset;
2. distinguish format compatibility, profile-specific training readiness,
   publication readiness, and partial coverage;
3. locate and prioritize affected episodes, frames, features, cameras, or
   shards;
4. block CI, training, or release deterministically;
5. produce and, where safe, apply an actionable remediation plan;
6. re-audit the result and prove what changed.

Implementation differentiation means OpenBot owns the evidence, snapshot,
semantic diff, provenance, policy/readiness profiles, safety preconditions, and
post-operation verification. Low-level editing, migration, merging,
re-encoding, visualization, and training may be delegated to a pinned official
LeRobot tool instead of being reimplemented.

## Product boundary

### OpenBot Data owns

- read-only local and Hugging Face dataset discovery;
- stable format-conformance and train-readiness findings;
- explicit training and publication readiness profiles;
- episode-level triage and deterministic CI/release gates;
- deterministic, portable dataset snapshots;
- baseline-to-candidate semantic diff;
- merge compatibility checks;
- structured remediation plans and narrowly scoped copy-on-write repair;
- before/after verification of delegated LeRobot operations;
- evidence locations that another CLI, viewer, Catalog, or API can consume;
- a versioned Catalog evidence handoff that OpenBot Catalog can score without
  copying scoring rules into this package;

### OpenBot Data does not own

- recording or training policies;
- training, evaluation, policy rollout, annotation, or reward modeling;
- default in-place or destructive mutation;
- guessing how to fill missing action/state data, rewrite timestamps, or alter
  task semantics;
- a duplicate low-level LeRobot editor, merger, migrator, or re-encoder;
- a universal numeric quality score;
- platform authentication, remote jobs, billing, Workers, or production
  storage.

Official LeRobot tools remain the execution engine for payload-changing edits,
v2.1-to-v3 migration, merge, re-encoding, visualization, annotation, and
training. OpenBot Data must still check their preconditions, emit the operation
plan, and verify the result. Any future platform integration requires a
separately published OpenBot API and an opt-in adapter; none is part of `0.0.3`.

## Public contract surface

All canonical artifacts are deterministic JSON and ship with Draft 2020-12 JSON
Schemas. Markdown and console output are projections of the same prepared
result; they are never separate sources of truth.

| Artifact | Producer | Required role in `0.0.3` |
|---|---|---|
| `openbot.dataset_manifest.v1` | `inspect_dataset` / `openbot-data inspect` | Preserved byte- and fingerprint-compatible for unchanged `0.0.2` fixtures |
| `openbot.dataset_audit.v1` | `audit_dataset` / `openbot-data audit` | Extended additively with layers, locations, coverage, impact, fixability, and remediation references |
| `openbot.dataset_snapshot.v1` | `build_dataset_snapshot` / `openbot-data snapshot` | Portable identity, provenance, normalized contracts, inventories, component digests, and skipped capabilities |
| `openbot.dataset_diff.v1` | `diff_dataset_snapshots` / `openbot-data diff` | Stable semantic change classification between two snapshots |
| `openbot.dataset_readiness.v1` | `evaluate_dataset_readiness` / `openbot-data readiness` | Profile-specific `READY`, `BLOCKED`, or `PARTIAL` result with coverage and blockers |
| `catalog-evidence-v1` | `build_catalog_evidence` / `openbot-data catalog-evidence` | Score-free facts and evidence for server-side Catalog evaluation |
| `openbot.dataset_repair_plan.v1` | `plan_dataset_repair` / `openbot-data repair plan` | Preconditions, risk, deterministic steps, and delegated/manual actions |
| `openbot.dataset_repair_receipt.v1` | `apply_dataset_repair`, `verify_dataset_repair` / `repair apply`, `verify` | Before/after identity, executed steps, re-audit, diff, and verification result |
| `openbot.dataset_merge_plan.v1` | `check_merge_compatibility` / `openbot-data merge-check` | `direct`, `transform_required`, `incompatible`, or `unknown` decision with evidence |
| `openbot.dataset_merge_receipt.v1` | `verify_dataset_merge` / `openbot-data verify-merge` | Input lineage, official operation record, full post-merge audit, loader smoke, and diff |

These artifact and API names are the intended `0.0.3` public contract. A name or
schema ID may change before release only through an explicit update to this
document, its packaged schema, canonical example, tests, and migration notes in
the same change.

The complete command surface added or extended by `0.0.3` is:

```text
openbot-data inspect
openbot-data audit
openbot-data snapshot
openbot-data diff
openbot-data readiness
openbot-data catalog-evidence
openbot-data repair plan
openbot-data repair apply
openbot-data verify
openbot-data merge-check
openbot-data verify-merge
```

`scan`, `catalog`, and `version` remain backward compatible. Remote platform
job, authentication, and billing commands are not added to this package.

## Required `0.0.3` functionality

### P0.1 Correctness debt from `0.0.2`

New compatibility work must not be built on ambiguous preflight behavior.
Before release:

- `sample` integrity probes deterministic start, middle, and end positions
  instead of accepting a video after only its first frame decodes;
- malformed LeRobot timestamps, lengths, paths, and relation fields always
  produce structured findings instead of uncaught conversion exceptions;
- a caller-supplied immutable snapshot is rejected when its root, input format,
  checksum, integrity, or symlink policy does not match the rendering request;
- symlinks skipped under the default policy are not mislabeled as escape errors;
- duplicate episode indexes are detected;
- every declared camera is checked for every episode;
- shared-video segment bounds are finite, ordered, non-overlapping where
  required, and inside the referenced video duration;
- catalog rendering consumes the same prepared snapshot as manifest/audit
  rendering and clearly declares its output schema;
- large Parquet inputs are processed in bounded batches rather than loaded into
  memory as one unbounded table.

### P0.2 Explicit LeRobot adapters

Internally separate:

- `lerobot_v21`: read-only support for the episode-per-file v2.1 layout;
- `lerobot_v30`: conformance with the stable LeRobot 0.6.0 `v3.0` layout.

Detection uses `meta/info.json.codebase_version`:

- known v2.1 and v3.0 contracts are audited explicitly;
- an unknown minor version may be inspected with a compatibility warning;
- an unknown major version is never reported as compatible;
- unknown feature and metadata fields are retained for forward compatibility.

The v3.0 adapter must understand:

```text
meta/info.json
meta/stats.json
meta/tasks.parquet
meta/episodes/chunk-*/file-*.parquet
data/chunk-*/file-*.parquet
videos/{video_key}/chunk-*/file-*.mp4
```

Legacy JSONL task/episode metadata may be accepted only when it matches a known
older contract. The canonical v3 path is `meta/tasks.parquet`, even though one
upstream prose page still mentions JSONL.

`openbot-data` must not require the full `lerobot` package at runtime. It should
read the stable storage contract directly and use the official package only in a
separate Python 3.12 conformance environment.

### P0.3 Layered, collect-all validation

Findings are grouped without hiding the existing stable rule code:

| Layer | Required checks |
|---|---|
| `metadata` | Required files, readable JSON/JSONL/Parquet, valid path templates, supported format version |
| `schema` | Declared and actual columns, dtype, shape, nullable behavior, task/feature references |
| `data` | Finite numeric values, episode/frame/global indexes, row counts, empty episodes |
| `media` | Decode, codec metadata, dimensions, FPS, frame count, declared camera coverage |
| `alignment` | Episode ranges, shard relations, timestamps, video windows, task foreign keys |
| `provenance` | Source type, requested revision, resolved revision, fingerprint algorithm |

The validator collects all safely discoverable findings in one pass. One corrupt
shard must not hide independent failures in another shard.

Every finding may carry an optional structured location:

```json
{
  "code": "LEROBOT_TIMESTAMP_OFF_GRID",
  "severity": "error",
  "layer": "alignment",
  "message": "Timestamp does not match frame_index / fps within tolerance.",
  "path": "data/chunk-000/file-000.parquet",
  "location": {
    "episode_index": 12,
    "frame_index": 315,
    "timestamp": 10.507,
    "feature_key": "timestamp"
  },
  "evidence": {
    "expected_timestamp": 10.5,
    "tolerance_seconds": 0.0001
  }
}
```

`layer` and `location` are additive finding fields. Existing `code`, `severity`,
`message`, `path`, and `evidence` behavior remains compatible.

The base rules must include:

- Parquet footer, schema, and row-group readability;
- unfinished/unfinalized Parquet represented as a specific finding;
- `info.json` totals against discovered episode, frame, task, and shard totals;
- unique and continuous episode, frame, and global indexes;
- `dataset_from_index`/`dataset_to_index` length, gap, and overlap checks;
- episode row counts against declared lengths;
- task indexes against the task table;
- actual feature columns, dtypes, and shapes against `info.json.features`;
- NaN/Inf checks for numeric action, state, timestamp, and index data;
- monotonically increasing timestamps and
  `timestamp ~= frame_index / fps` with an explicit tolerance;
- data and video shard references against declared path templates;
- per-episode, per-camera video coverage;
- declared versus actual resolution, FPS, channels, and segment bounds;
- stats field, dimension, count, and finite-value validation;
- in `full` integrity mode, deterministic recomputation of standard normalization
  statistics and comparison against stored values with documented tolerances.

Every P0 rule is added to the finding registry before implementation is accepted.
The registry row freezes the stable code, layer, default severity, applicability,
required capabilities, evidence fields, location fields, impact, fixability,
remediation reference, deterministic ordering key, and positive/negative
fixtures. Numeric rules also freeze the tolerance, unit, comparison method, and
threshold source. Undocumented heuristics cannot block readiness or release.

Stats absence is a readiness warning, not automatically a malformed dataset,
when the official loader permits missing stats.

Action values are not universally required to be in `[-1, 1]`. Range rules run
only when a selected profile or source contract explicitly declares normalized
actions.

### P0.4 Portable dataset snapshot

Preserve `openbot.dataset_manifest.v1` and its current fingerprint algorithm.
`0.0.3` must not silently change a v1 fingerprint for unchanged input.

Add a separate `openbot.dataset_snapshot.v1` artifact for change control. It
contains:

- `schema_version` (`openbot.dataset_snapshot.v1`) and
  `fingerprint_version`;
- source kind (`local` or `hf_hub`) and format adapter;
- requested and resolved source revision when available;
- LeRobot package compatibility target and dataset format version;
- normalized feature schema;
- task inventory;
- episode ranges and per-camera relations;
- data, metadata, and media shard inventory;
- component digests that show which part changed;
- scan mode and explicitly skipped validation capabilities;
- no access token, absolute private path, or secret-bearing URL.

The snapshot fingerprint is computed only from documented normalized fields.
Timestamps describing when the scan ran are excluded from identity.

The existing inspection manifest remains supported for current
`openbot-sdk`/OpenBot integrations. A later migration may adopt the snapshot
artifact, but `0.0.3` cannot silently redefine the v1 manifest contract.

### P0.5 Semantic dataset diff

Add `openbot.dataset_diff.v1`.

The diff compares two dataset snapshots and reports stable changes at these
levels:

- source revision and format contract;
- feature added, removed, dtype changed, or shape changed;
- task added, removed, or remapped;
- episode added, removed, reordered, or content-changed;
- stream/camera added, removed, or metadata-changed;
- shard added, removed, or content-changed;
- totals and component fingerprints.

Each change is classified as:

- `breaking`: an existing loader/training contract can no longer be assumed;
- `material`: dataset content or membership changed and requires review;
- `non_breaking`: additive metadata that does not change existing sample
  interpretation;
- `unchanged`.

This is contract/change classification, not a dataset quality score.

Required interfaces:

```bash
openbot-data snapshot ./dataset --format lerobot --out snapshot.json
openbot-data diff baseline.json candidate.json --out diff.json
openbot-data diff baseline.json candidate.json --fail-on breaking
```

```python
from openbot_data import build_dataset_snapshot, diff_dataset_snapshots

baseline = build_dataset_snapshot("./baseline", input_format="lerobot")
candidate = build_dataset_snapshot("./candidate", input_format="lerobot")
diff = diff_dataset_snapshots(baseline, candidate)
```

The public names and schema ID follow the contract-freeze rule in
[Public contract surface](#public-contract-surface).

### P0.6 Revision-pinned Hugging Face audit

Support a read-only Hub source without turning the package into an uploader:

```bash
openbot-data audit hf://datasets/org/name@revision \
  --format lerobot \
  --integrity metadata \
  --out audit.json
```

Requirements:

- use the standard Hugging Face credential chain; never accept or echo a token
  as a normal positional CLI argument;
- resolve a branch or tag to a commit SHA and store both requested and resolved
  revisions;
- fetch repository metadata and the `meta/` contract first;
- do not download all Parquet or video shards in `metadata` mode;
- report “metadata validated” rather than “dataset valid” when payload checks
  were skipped;
- emit a stable partial-coverage finding listing every skipped rule capability;
- make sample/full downloads explicit, bounded, and resumable;
- expose and document byte, shard, episode, and media-download budgets; record
  effective budgets, cache/resume state, and exhausted limits in the canonical
  coverage result;
- stop before exceeding a budget and emit a stable coverage/budget finding
  rather than silently reducing coverage; a readiness projection becomes
  `PARTIAL` unless the budget itself was invalid configuration;
- in sample mode, deterministically select and report episode, data-shard, and
  camera coverage instead of downloading arbitrary first files;
- check version refs against `info.json.codebase_version`;
- treat missing tags, task category, or dataset-card fields as publication
  warnings, not structural format errors;
- optionally record the Hugging Face Dataset Viewer `/is-valid` result as a
  separate Hub capability signal, never as a substitute for LeRobot validation.

### P0.7 Internal adapter and rule architecture

Introduce internal boundaries inspired by format-oriented dataset tools:

```text
SourceResolver
  -> FormatProbe
  -> DatasetReader
  -> DatasetSnapshot
  -> AuditRule[]
  -> Manifest / Audit / Snapshot / Diff renderers
```

Rules:

- discovery and parsing happen once per requested integrity level;
- renderers cannot rescan the dataset independently;
- a rule declares the capabilities it requires;
- skipped rules explain the missing capability or cost boundary;
- deterministic sorting is applied at every output boundary;
- arbitrary third-party plugin discovery is out of scope for `0.0.3`.

### P0.8 Readiness profiles and deterministic gates

Format validity and target readiness are separate results. A dataset can satisfy
the LeRobot storage contract and still be unusable by a selected policy because
its features, action dimension, camera inputs, normalization statistics, or
temporal windows do not match.

`0.0.3` adds `openbot.dataset_readiness.v1` with these P0 targets:

- `lerobot-core`: stable LeRobot 0.6.0/v3.0 loader compatibility;
- `training-common`: requirements shared by the official training pipeline;
- `lerobot-act`: versioned ACT requirements from the pinned LeRobot release;
- `lerobot-smolvla`: versioned SmolVLA requirements from the pinned release;
- `hf-publication`: format, provenance, license, and Hub metadata needed before
  publication.

A caller may instead supply an actual policy config or checkpoint metadata.
That declared contract takes precedence over a built-in profile. The gate reads
required input/output features, action dimensions, camera and language inputs,
normalization mode, delta horizons, and other deterministic constraints from
the policy contract; it must not infer them from the policy name alone.

The result contains:

```json
{
  "schema_version": "openbot.dataset_readiness.v1",
  "profile": {
    "id": "lerobot-act",
    "source_version": "lerobot==0.6.0"
  },
  "status": "READY | BLOCKED | PARTIAL",
  "coverage": {},
  "blocking_findings": [],
  "warnings": [],
  "triage": [],
  "remediation": [],
  "snapshot_fingerprint": "..."
}
```

Rules:

- `PARTIAL` can never be interpreted as `READY`;
- metadata-only Hub inspection always produces `PARTIAL` for any profile that
  requires payload or media validation;
- sample mode lists the selected episodes, cameras, shards, and coverage;
- only a scan satisfying every capability required by a profile may return
  `READY`;
- policy-independent format truth is not changed by a policy profile;
- deterministic incompatibilities are blockers, while empirical advice such as
  a recommended episode count remains a recommendation;
- each built-in profile is versioned and tested against its pinned official
  policy configuration.

Each built-in profile ships as versioned package data and declares:

- compatible dataset format and source-tool versions;
- required and optional feature keys, dtypes, shapes, and semantics;
- action dimensions and coordinate/normalization contracts;
- camera, language, state, timestamp, task, and delta-horizon requirements;
- required audit capabilities and minimum integrity level;
- deterministic blocker rules and advisory recommendations;
- the official configuration or documentation revision from which each
  requirement was derived.

Profiles cannot override base format truth or silently change a stable finding
severity. A profile change that can change a readiness result requires a new
profile version, fixtures for the old and new behavior, and release notes.

Required interfaces:

```bash
openbot-data readiness ./dataset --profile lerobot-act --out readiness.json
openbot-data readiness ./dataset --policy-config ./policy/config.json
openbot-data readiness hf://datasets/org/name@revision \
  --profile hf-publication --integrity metadata
```

```python
from openbot_data import evaluate_dataset_readiness

readiness = evaluate_dataset_readiness(
    "./dataset",
    profile="lerobot-act",
    integrity="full",
)
```

Canonical JSON is the machine contract. Console and Markdown reports summarize
the same prepared result. Stable exit behavior distinguishes a completed gate
that reached its `--fail-on` threshold from command, configuration, or access
failure.

### P0.9 Evidence triage and quality signals

P0 triage groups findings by episode, frame range, feature, camera, and shard,
then orders deterministic blockers before warnings and advisory signals. A
global error count is not sufficient.

The initial low-cost advisory signals include:

- episode duration and explicitly configured short-episode thresholds;
- static/idle action spans, with raw span bounds and the action semantics used;
- constant or near-zero-variance action/state dimensions;
- exact duplicate episode content;
- task and camera coverage imbalance;
- action saturation only when a selected policy/source contract declares a
  valid range.

Each signal carries its raw value, unit, threshold, threshold source,
applicability, coverage, and evidence location. Heuristic signals are labeled
`advisory`; they do not become base-format errors, automatically drop an
episode, or imply task success.

This provides the required outcome behind a “quality score” — prioritization and
evidence — without presenting an uncalibrated universal 0–100 number as truth.
Any later aggregate score must be profile-specific, decomposable, versioned, and
validated against labeled downstream outcomes.

#### Scoring decision and Open Data Eval reference

[Open Data Eval](https://github.com/Varun-Nair/open-data-eval) is an open-source
evaluation project and interactive scorecard, not a peer-reviewed paper. Its
progressive `metadata -> file -> frame -> content -> downstream` evidence
model, explicit missing values, and declared-versus-observed file checks are
useful references for future reporting.

Its public scorecard helps users compare known catalog datasets. OpenBot Data
`0.0.3` has a different primary job: determine whether an arbitrary local or
revision-pinned robot dataset satisfies a declared format, policy, training, or
publication contract. Therefore `0.0.3` does not add:

- a dataset total score, percentage, grade, badge, leaderboard, or ranking;
- general-purpose normalized dimension scores;
- a scorecard-specific schema or an independent scoring scan;
- a fixed use-case-fit percentage inferred only from modality presence.

The required outputs remain deterministic findings, `READY`/`BLOCKED`/`PARTIAL`
readiness, explicit scan coverage, raw advisory measurements, and evidence
locations. A fatal compatibility issue cannot be averaged away by strong values
in unrelated dimensions.

`0.0.3` does not adopt its universal-looking presentation thresholds or
metadata heuristics as OpenBot truth. In particular:

- `30 FPS` or a `1080p` short edge does not by itself establish robot-training
  quality;
- video hours do not replace episode, transition, task, success, embodiment, or
  environment coverage;
- modality presence does not establish temporal alignment, semantic validity,
  annotation accuracy, or policy compatibility;
- pool-relative diversity and download-efficiency rankings are not stable
  portable scores;
- ISO/IEC 5259-2 field mappings are treated as design references, not
  certification or evidence that the heuristic thresholds are calibrated.

#### Shared dynamic scoring boundary with OpenBot Catalog

The website and `openbot-data` must not maintain separate scoring formulas.
`openbot-data` owns the dynamic audit evidence; the OpenBot Catalog API owns the
only public score calculation.

The `0.0.3` handoff is a versioned `catalog-evidence-v1` artifact derived from a
completed audit. It carries:

- immutable dataset identity and resolved Hub commit or local snapshot
  fingerprint;
- audit profile, rule-pack version, integrity level, checked time, and actual
  coverage;
- schema, manifest, signal, annotation, scale, sample, integrity, and
  profile-readiness facts;
- evidence maturity (`official_claim`, `metadata_verified`,
  `sample_verified`, or `pipeline_tested`);
- source/evidence locators and unresolved checks.

It deliberately does not carry an Overall score or normalized dimension score.
Catalog ingestion converts this evidence into a reviewable candidate and runs
the server-owned `CatalogEvaluationV1` evaluator. Import, candidate edit, and
publish must overwrite any caller-supplied evaluation.

The dynamic lifecycle is:

```text
local audit or revision-pinned official-source refresh
-> openbot-data catalog-evidence
-> versioned facts/evidence
-> Catalog candidate and server-side score recomputation
-> human review and published revision
-> website reads the published API evaluation
```

This makes a score change traceable to changed evidence, a rule-pack version,
or a new reviewed revision. It does not permit a background scan to overwrite a
published Catalog entry. It also does not replace or change the future
P0.8 `openbot.dataset_readiness.v1` `READY`/`BLOCKED`/`PARTIAL` result.

The handoff implementation is available through:

```bash
openbot-data catalog-evidence ./dataset \
  --dataset-id org/dataset \
  --checked-at 2026-07-28T12:00:00Z \
  --out catalog-evidence.json
```

and:

```python
from openbot_data import build_catalog_evidence

evidence = build_catalog_evidence(
    "./dataset",
    dataset_id="org/dataset",
    checked_at="2026-07-28T12:00:00Z",
    output_path="catalog-evidence.json",
)
```

The local handoff defaults to SHA-256 content identity, sanitizes the public
source locator, and packages a Draft 2020-12 JSON Schema. A Hub handoff is
accepted only with an immutable resolved revision. The evidence fingerprint
excludes the audit timestamp but includes dataset identity, coverage, facts,
findings, and unresolved checks. This lets the same evidence be refreshed
without inventing a score change.

For parity across Python and the Catalog API, the evidence fingerprint hashes a
canonical typed tree rather than relying on one runtime's JSON number or object
ordering behavior. Every value has a null, boolean, string, number, array, or
object tag; object keys are sorted by UTF-8 bytes; safe integers use decimal
strings; and non-integral numbers use IEEE-754 float64 big-endian hexadecimal
bytes. Non-finite values and integers outside the JavaScript safe range are
invalid evidence. The compact, non-ASCII-preserving JSON tree is hashed with
SHA-256. The existing dataset manifest fingerprint remains unchanged.

### P0.10 Verified remediation and conservative repair

Every actionable finding includes:

- impact and affected artifact;
- fixability (`automatic`, `delegated`, `manual`, or `not_repairable`);
- preconditions and risk;
- one or more remediation steps;
- the rule and evidence that justify each step.

Required interfaces:

```bash
openbot-data repair plan ./dataset --out repair-plan.json
openbot-data repair apply ./dataset \
  --plan repair-plan.json \
  --output ./dataset.fixed
openbot-data verify ./dataset.fixed \
  --against repair-plan.json \
  --out repair-receipt.json
```

```python
from openbot_data import (
    apply_dataset_repair,
    plan_dataset_repair,
    verify_dataset_repair,
)

plan = plan_dataset_repair("./dataset")
applied = apply_dataset_repair("./dataset", plan, output_path="./dataset.fixed")
receipt = verify_dataset_repair("./dataset.fixed", against=plan)
```

`openbot.dataset_repair_plan.v1` and
`openbot.dataset_repair_receipt.v1` are deterministic artifacts. The P0 repair
executor is intentionally narrow. It may only rebuild derived values that can
be uniquely recomputed from already validated payload:

- `info.json` totals and counters;
- episode lengths, offsets, and relation ledgers when their source ranges are
  unambiguous;
- normalization statistics from finite, validated feature values;
- task references when exactly one valid mapping exists.

Safety is part of the contract:

- planning is read-only, and apply always writes a distinct new dataset;
- no source file is modified in place;
- the source snapshot fingerprint is checked immediately before apply;
- unknown fields are preserved;
- work is completed in a staging directory before the new output is made
  visible;
- the receipt contains before/after snapshots, hashes, semantic diff, resolved
  and unresolved findings, tool versions, and every executed step;
- the output is re-audited; the official LeRobot loader smoke test is required
  before it can be reported as fully verified;
- ambiguous or semantically meaningful repairs are refused.

The P0 executor never fills or interpolates action/state NaNs, rewrites
timestamps, fabricates missing frames, changes action semantics, deletes
episodes, trims payload, or re-encodes video. Those findings still receive a
complete plan. Where an official LeRobot operation exists, the plan contains a
pinned command and OpenBot verifies its preconditions and output rather than
reimplementing the editor.

Idle-span detection and an evidence-rich trim plan are P0. Applying a trim is
not: a correct transform must synchronize Parquet rows, video windows,
frame/global indexes, episode relations, and statistics, and must require human
approval because a stationary hold can be task-relevant.

### P0.11 Merge compatibility and post-operation verification

Semantic diff answers “what changed”; merge compatibility answers whether
separate inputs can be combined safely. Both are required.

Required interfaces:

```bash
openbot-data merge-check ./dataset-a ./dataset-b \
  --profile lerobot-act \
  --out merge-plan.json
openbot-data verify-merge ./merged \
  --inputs dataset-a.snapshot.json dataset-b.snapshot.json \
  --out merge-receipt.json
```

```python
from openbot_data import check_merge_compatibility, verify_dataset_merge

plan = check_merge_compatibility(
    ["./dataset-a", "./dataset-b"],
    profile="lerobot-act",
)
receipt = verify_dataset_merge(
    "./merged",
    input_snapshots=[
        "dataset-a.snapshot.json",
        "dataset-b.snapshot.json",
    ],
)
```

`openbot.dataset_merge_plan.v1` and
`openbot.dataset_merge_receipt.v1` are deterministic artifacts.

The pre-merge check covers:

- dataset format and compatible version;
- feature keys, dtypes, shapes, and declared semantics;
- action/state coordinate and normalization contracts;
- FPS, delta horizons, cameras, codec metadata, and media coverage;
- task identity and remapping;
- robot/embodiment provenance;
- episode/content duplication;
- index, shard, and video-range collision risks;
- license and source lineage for the selected publication profile.

The result is `direct`, `transform_required`, `incompatible`, or `unknown`, with
evidence for every non-direct result. Actual physical merge remains an official
LeRobot operation. OpenBot records the input snapshots and planned command,
then performs a full post-merge audit, loader smoke test, lineage check, and
semantic diff before issuing a verified receipt.

## P1 candidates after P0

P1 work may ship only after every P0 acceptance criterion passes. Unfinished P1
items move to a later release instead of weakening the P0 contract.

- sampled stats/quantile recomputation and explicit coverage estimates;
- depth metadata and quantization consistency checks;
- optional language columns, event timestamps, camera references, and tool
  schema validation;
- deterministic incremental cache keyed by component digest;
- bounded hash/decode concurrency with deterministic output ordering;
- a lightweight Rerun handoff that opens a finding at its episode/timestamp;
- a documented DVC hook that records the snapshot artifact without duplicating
  DVC storage;
- near-duplicate, jerk, distribution-shift, blur, darkness, and frozen-frame
  signals, kept separate and explainable rather than collapsed into a generic
  score;
- review-approved, copy-on-write episode deletion and synchronized idle-span
  trimming with full Parquet/video/index/stats rebuild and verification;
- optional orchestration of pinned official LeRobot edit, merge, migration, and
  re-encoding commands after the P0 plan/handoff contract is proven.

Policy profiles may add requirements but cannot redefine base LeRobot format
validity.

## Later versions

### [`0.0.4`: robomimic/HDF5 read-only preflight](version-0.0.4.md)

- HDF5 structure and `env_args`;
- trajectory length and leading-dimension consistency;
- state/action/reward/done/observation references;
- image dtype/layout and mask integrity;
- no conversion, simulator replay, or universal normalized-action assumption.

### `0.0.5`: RLDS/Open X read-only adapter

- episode/step lifecycle markers;
- consistent step fields and truncated-episode handling;
- source embodiment and action-semantics provenance;
- no TensorFlow dependency in the core install;
- no automatic action-coordinate conversion.

### Later

- ROS bag/MCAP reader;
- Foxglove evidence handoff;
- explicit external validator interoperability after a stable report contract
  exists.

These package versions are independent from OpenBot platform and
`openbot-sdk` versions.

## Explicit non-goals

- excluding a required user outcome solely because LeRobot or a competing tool
  already implements it;
- depending on `lerobot-doctor` or `robovet` as a required runtime engine;
- copying every third-party finding or severity;
- default in-place or destructive source-data modification;
- a duplicate low-level delete, split, merge, feature-edit, migration,
  conversion, or re-encoding engine;
- silently applying a delegated official LeRobot operation;
- automatic payload repair when the intended result is ambiguous;
- automatic idle-frame trimming in P0;
- an opaque or universal aggregate 0–100 quality score;
- claiming training or publication readiness without a declared profile and
  complete required coverage;
- action-range assumptions without an explicit semantic profile;
- a custom dataset viewer;
- TensorFlow, Pandera, Datumaro, DVC, Rerun, or Foxglove as core dependencies;
- RLDS/HDF5/MCAP conversion;
- VLM annotations, task-success judgment, policy training, or evaluation;
- remote Catalog mutation, platform authentication, billing, or production API
  implementation.

## Compatibility and packaging

- Keep the current Python 3.9–3.12 core support unless a separate packaging
  decision explicitly changes it.
- Keep `lerobot` out of normal runtime dependencies.
- Keep PyArrow and any precise media parser behind documented optional extras.
- Put Hugging Face access behind a small optional `hub` extra.
- Keep core audit, snapshot, diff, readiness evaluation, repair planning, and
  merge checking usable without the full LeRobot package.
- Commands that verify an official LeRobot operation use an explicitly selected
  Python 3.12 conformance environment pinned to `lerobot[dataset]==0.6.0`.
  If that runner is unavailable, the command may emit a canonical unverified
  receipt and exit `2`, but it must not claim verification.
- Run official LeRobot conformance only in a separate Python 3.12 job pinned to
  `lerobot[dataset]==0.6.0`.
- Run LeRobot `main` compatibility as a non-blocking scheduled job; unreleased
  upstream changes do not block a stable OpenBot Data release.
- Preserve manifest v1 schema, bytes, and fingerprint behavior for unchanged
  fixtures.

## Integrity, coverage, and exit behavior

Integrity is a declared coverage contract, not a quality label:

| Integrity | Required work | Result limitation |
|---|---|---|
| `metadata` | Resolve source identity; validate required metadata, JSON/Parquet readability, declared schemas, path templates, totals discoverable without payload reads, and provenance | Must report every skipped data/media/statistics capability; cannot return `READY` for a profile that requires one of them |
| `sample` | Everything in `metadata`, plus deterministic episode/data-shard/camera selection, bounded row/value checks, and start/middle/end media probes for every selected video | Must include selected and total coverage; cannot be represented as full-dataset validation |
| `full` | Read every required data row and media stream, validate all relations, and recompute required standard statistics under documented tolerances | May return `READY` only when every capability required by the selected profile completed successfully |

Every audit, snapshot, readiness result, repair/merge plan, and receipt records
the requested integrity, completed capabilities, skipped capabilities, selected
sample, and total known population. A renderer must never infer stronger
coverage than the prepared result contains.

All CLI commands use the same process-exit classes:

| Exit | Meaning |
|---|---|
| `0` | The command completed and the selected gate accepted the canonical result |
| `1` | Invocation, configuration, source access, authentication, dependency, or unexpected runtime failure prevented a canonical result |
| `2` | The command completed and produced a canonical negative result that reached the selected gate threshold |

Command-specific gate behavior is:

- `audit --fail-on none|warning|error` exits `2` only when the completed audit
  reaches the selected non-`none` threshold;
- `diff --fail-on none|material|breaking` exits `2` when the completed diff
  reaches the selected non-`none` change class;
- `readiness` requires `READY` by default, so `BLOCKED` and `PARTIAL` exit `2`;
  `--allow-partial` permits a completed `PARTIAL` result to exit `0`;
- `merge-check` exits `0` only for `direct`; `transform_required`,
  `incompatible`, and `unknown` are completed negative results and exit `2`;
- `repair apply`, `verify`, and `verify-merge` exit `2` when a canonical receipt
  is produced but verification is not complete or successful.

JSON artifacts are written for completed results before exit `2`. Exit `1` must
not be disguised as a dataset finding or readiness result.

## Release acceptance criteria

`0.0.3` is complete only when all of the following pass.

### Official compatibility

- A fixture created and finalized with `lerobot[dataset]==0.6.0` passes the
  OpenBot audit, receives `READY` for `lerobot-core` and `training-common`, and
  loads through the official `LeRobotDataset`.
- The official loader can read the first, middle, and last sample after the
  OpenBot read-only audit.
- A representative v2.1 fixture remains readable and produces an official
  migration recommendation rather than being rewritten.
- `tasks.parquet`, shared data shards, shared video shards, quantile stats, and
  unknown optional fields are covered.

### Readiness, triage, and report behavior

- the pinned clean fixtures satisfy the versioned ACT and SmolVLA profiles;
- an actual policy config overrides the built-in profile and its required
  feature/action/camera/normalization contract is enforced;
- metadata-only Hub audit returns `PARTIAL`, even when every downloaded metadata
  file passes;
- skipped capabilities and sample coverage cannot be rendered as `READY`;
- deterministic blockers, empirical recommendations, and advisory quality
  signals are visually and structurally distinct;
- no report or canonical artifact emits a dataset total score, normalized
  dimension grade, badge, leaderboard rank, or modality-only fitness percentage;
- the `catalog-evidence-v1` artifact is deterministic, contains
  evidence and coverage rather than scores, and changes when a covered audited
  fact changes;
- Catalog handoff fixtures prove that caller-supplied evaluation fields are not
  part of the `openbot-data` artifact contract;
- every finding has a stable code, location, evidence, impact, fixability, and
  remediation reference;
- structural findings are grouped by affected episode, frame range, feature,
  camera, and shard;
- idle/static spans and constant dimensions expose their raw values, thresholds,
  threshold sources, applicability, and coverage;
- the same result produces deterministically ordered JSON and Markdown;
- exit status distinguishes gate failure from command/configuration/access
  failure.

### Negative fixtures

Stable findings cover:

- missing or corrupt Parquet footer;
- wrong info totals;
- duplicate/gapped episode indexes;
- invalid task foreign key;
- episode offset gap/overlap;
- declared length versus data-row mismatch;
- wrong feature dtype/shape;
- NaN/Inf action, state, timestamp, or stats;
- stale standard normalization statistics in `full` integrity mode;
- off-grid or non-monotonic timestamps;
- missing camera relation;
- segment outside video duration;
- declared versus actual FPS/resolution/channel mismatch;
- policy feature, action-dimension, camera, normalization, or delta-horizon
  mismatch;
- flatline action, zero-variance state, exact duplicate episode, and camera/task
  coverage imbalance;
- a middle or final video corruption missed by a first-frame-only probe.

### Repair and delegated-operation loop

- a stale metadata fixture completes
  `BLOCKED -> repair plan -> copy-on-write apply -> re-audit READY`;
- applying a plan against a source fingerprint that changed is refused;
- the source dataset remains byte-identical after both successful and failed
  repairs;
- an ambiguous episode relation, task mapping, NaN action/state value, or
  timestamp correction is never automatically repaired;
- a repair failure cannot expose a partially written destination;
- the receipt proves before/after hashes, snapshots, diff, executed operations,
  resolved findings, unresolved findings, and tool versions;
- the repaired result loads through the pinned official `LeRobotDataset`;
- a payload-changing official edit is represented by an OpenBot plan, executed
  outside the core repair engine, then re-audited and diffed before verification;
- an idle-span finding produces a reviewable trim plan but is not silently
  applied.

### Merge safety

- two directly compatible datasets pass pre-merge checks, merge through the
  pinned official tool, and pass a full post-merge audit and loader smoke test;
- feature dtype/shape, camera/FPS, action semantics, task mapping, or policy
  incompatibilities block a direct merge;
- `transform_required` identifies the precise mapping or conversion needed
  without claiming it was applied;
- post-merge episode/shard/video ranges and lineage reconcile to the input
  snapshots;
- a merged-dataset frame/timestamp regression is caught before the receipt can
  be marked verified.

### Snapshot and diff

- unchanged input produces byte-stable snapshot and diff JSON;
- manifest v1 and its fingerprint remain unchanged for the existing fixtures;
- feature removal/dtype/shape changes classify as breaking;
- episode or shard content changes classify as material;
- additive non-semantic metadata is non-breaking;
- source revisions and component digests identify the changed layer;
- no absolute local path, Hugging Face token, signed URL, or private cache path
  appears in output.

### Hub behavior

- branch/tag input records the resolved immutable commit;
- metadata mode does not download all payload shards;
- gated/private access uses standard credentials without logging them;
- metadata-only results cannot be mistaken for a full audit;
- retrying the same resolved revision produces the same canonical output.

### Engineering quality

- all existing `0.0.2` tests remain green;
- each new documented finding code has a positive and negative fixture;
- package schemas validate every canonical example;
- core tests pass on Python 3.9–3.12;
- the pinned official LeRobot conformance job passes on Python 3.12;
- clean-install demos run outside the source checkout;
- build, Twine metadata, Ruff, Mypy, and deterministic-output checks pass.
- pinned `lerobot-doctor` and `robovet` comparison fixtures demonstrate
  critical workflow outcome parity without requiring matching rule names,
  severity labels, scores, or mutation behavior;
- every deliberately unsupported competitor capability documents the
  substitute user outcome, why the workflow remains complete, and its
  acceptance evidence.

The comparison gate is implemented by
`tests/fixtures/competitor-outcomes-v003.json` and
`tests/test_competitor_outcomes.py`. It freezes a real 2026-07-28 run of
`lerobot-doctor==0.2.0` and `robovet==0.2.2` against the same official LeRobot
0.6.0 v3.0 stale-counter scenario, then reconstructs the case to enforce the
OpenBot blocking outcome.

## Implementation order

1. Fix `0.0.2` correctness debt and freeze v1 fingerprint fixtures.
2. Introduce adapter/rule boundaries without changing public output.
3. Implement the v2.1 and v3.0 readers against stable LeRobot contracts.
4. Add layered schema/data/media/alignment findings.
5. Add the portable snapshot and semantic diff schemas.
6. Add revision-pinned Hub metadata resolution.
7. Add readiness profiles, episode-level triage, canonical Markdown, and stable
   gate exit behavior.
8. Revalidate and integrate the implemented deterministic
   `catalog-evidence-v1` handoff against the final snapshot, audit, coverage, and
   readiness contracts without adding a package scoring formula.
9. Add remediation plans and the conservative copy-on-write repair executor.
10. Add merge compatibility and post-operation verification.
11. Add official LeRobot 0.6.0 conformance, competitor-outcome comparison, and
    negative fixtures.
12. Consider P1 only after the full release gate passes.

## References

- [Reference libraries and differentiation](reference-libraries.md)
- [LeRobotDataset v3.0](https://huggingface.co/docs/lerobot/v0.6.0/lerobot-dataset-v3)
- [LeRobot Dataset Tools](https://huggingface.co/docs/lerobot/v0.6.0/using_dataset_tools)
- [LeRobot action representations](https://huggingface.co/docs/lerobot/v0.6.0/action_representations)
- [LeRobot 0.6.0 release](https://github.com/huggingface/lerobot/releases/tag/v0.6.0)
- [LeRobot dataset-tool gaps and use cases](https://github.com/huggingface/lerobot/issues/2326)
- [LeRobot merge user need](https://github.com/huggingface/lerobot/issues/847)
- [Merged-dataset frame-index failure](https://github.com/huggingface/lerobot/issues/2680)
- [Policy/dataset feature mismatch failure](https://github.com/huggingface/lerobot/issues/2731)
- [`lerobot-doctor`](https://github.com/jashshah999/lerobot-doctor)
- [`robovet`](https://github.com/RonaldSit/robovet)
- [Open Data Eval scorecard](https://varun-nair.github.io/open-data-eval/scorecard/)
- [Open Data Eval metadata evaluator](https://github.com/Varun-Nair/open-data-eval/blob/main/eval/metadata_eval.py)
- [Open Data Eval file evaluator](https://github.com/Varun-Nair/open-data-eval/blob/main/eval/file_eval.py)
- [Auditing action-only demonstration curation metrics](https://arxiv.org/abs/2606.05588)
- [Demo-SCORE](https://arxiv.org/abs/2503.03707)
- [robomimic datasets](https://robomimic.github.io/docs/datasets/overview.html)
- [RLDS](https://github.com/google-research/rlds)
- [Open X-Embodiment](https://github.com/google-deepmind/open_x_embodiment)
