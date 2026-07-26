# OpenBot Data 0.0.3 — LeRobot Preflight and Dataset Change Control

> Status: Planned; not implemented
> Baseline package: `0.0.2`
> Primary compatibility target: `lerobot==0.6.0`, dataset format `v3.0`
> Research: [reference libraries and differentiation](reference-libraries.md)

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
- optional artifacts for later OpenBot Catalog and Hosted Data registration.

### OpenBot Data does not own

- recording or training policies;
- training, evaluation, policy rollout, annotation, or reward modeling;
- default in-place or destructive mutation;
- guessing how to fill missing action/state data, rewrite timestamps, or alter
  task semantics;
- a duplicate low-level LeRobot editor, merger, migrator, or re-encoder;
- a universal numeric quality score;
- hosted upload, review, approved export, API keys, billing, Workers, or
  production storage.

Official LeRobot tools remain the execution engine for payload-changing edits,
v2.1-to-v3 migration, merge, re-encoding, visualization, annotation, and
training. OpenBot Data must still check their preconditions, emit the operation
plan, and verify the result. Hosted behavior remains in the main OpenBot
repository and is called through `openbot-sdk`.

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

- `snapshot_schema_version` and `fingerprint_version`;
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

Final names may change before implementation only if the JSON schema IDs and
migration notes are updated together.

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

Required interfaces:

```bash
openbot-data readiness ./dataset --profile lerobot-act --out readiness.json
openbot-data readiness ./dataset --policy-config ./policy/config.json
openbot-data readiness hf://datasets/org/name@revision \
  --profile hf-publication --integrity metadata
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

### `0.0.4`: robomimic/HDF5 read-only adapter

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

These package versions are independent from Hosted OpenBot Data product
versions.

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
- hosted Catalog mutation, upload, billing, or production API implementation.

## Compatibility and packaging

- Keep the current Python 3.9–3.12 core support unless a separate packaging
  decision explicitly changes it.
- Keep `lerobot` out of normal runtime dependencies.
- Keep PyArrow and any precise media parser behind documented optional extras.
- Put Hugging Face access behind a small optional `hub` extra.
- Run official LeRobot conformance only in a separate Python 3.12 job pinned to
  `lerobot[dataset]==0.6.0`.
- Run LeRobot `main` compatibility as a non-blocking scheduled job; unreleased
  upstream changes do not block a stable OpenBot Data release.
- Preserve manifest v1 schema, bytes, and fingerprint behavior for unchanged
  fixtures.

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

## Implementation order

1. Fix `0.0.2` correctness debt and freeze v1 fingerprint fixtures.
2. Introduce adapter/rule boundaries without changing public output.
3. Implement the v2.1 and v3.0 readers against stable LeRobot contracts.
4. Add layered schema/data/media/alignment findings.
5. Add the portable snapshot and semantic diff schemas.
6. Add revision-pinned Hub metadata resolution.
7. Add readiness profiles, episode-level triage, canonical Markdown, and stable
   gate exit behavior.
8. Add remediation plans and the conservative copy-on-write repair executor.
9. Add merge compatibility and post-operation verification.
10. Add official LeRobot 0.6.0 conformance, competitor-outcome comparison, and
    negative fixtures.
11. Consider P1 only after the full release gate passes.

## References

- [Reference libraries and differentiation](reference-libraries.md)
- [LeRobotDataset v3.0](https://huggingface.co/docs/lerobot/main/lerobot-dataset-v3)
- [LeRobot Dataset Tools](https://huggingface.co/docs/lerobot/main/using_dataset_tools)
- [LeRobot action representations](https://huggingface.co/docs/lerobot/main/action_representations)
- [LeRobot 0.6.0 release](https://github.com/huggingface/lerobot/releases/tag/v0.6.0)
- [LeRobot dataset-tool gaps and use cases](https://github.com/huggingface/lerobot/issues/2326)
- [LeRobot merge user need](https://github.com/huggingface/lerobot/issues/847)
- [Merged-dataset frame-index failure](https://github.com/huggingface/lerobot/issues/2680)
- [Policy/dataset feature mismatch failure](https://github.com/huggingface/lerobot/issues/2731)
- [`lerobot-doctor`](https://github.com/jashshah999/lerobot-doctor)
- [`robovet`](https://github.com/RonaldSit/robovet)
- [Auditing action-only demonstration curation metrics](https://arxiv.org/abs/2606.05588)
- [Demo-SCORE](https://arxiv.org/abs/2503.03707)
- [robomimic datasets](https://robomimic.github.io/docs/datasets/overview.html)
- [RLDS](https://github.com/google-research/rlds)
- [Open X-Embodiment](https://github.com/google-deepmind/open_x_embodiment)
