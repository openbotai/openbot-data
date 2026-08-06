# Reference Libraries and OpenBot Data Differentiation

> Research snapshot: 2026-07-26
> Scope: planning input for `openbot-data 0.0.3`
> Evidence boundary: upstream stable source, official documentation, and published
> package metadata were reviewed. The critical stale-counter workflow was also
> executed on 2026-07-28 against pinned `lerobot-doctor==0.2.0`,
> `robovet==0.2.2`, and the source that shipped as `openbot-data==0.0.3`.

## Decision

`openbot-data` must not differentiate itself by omitting an essential workflow
because LeRobot, `lerobot-doctor`, or `robovet` already implements part of it.
Repeated implementation by independent tools is evidence that the user outcome
may be necessary; it is not by itself a reason either to copy or to exclude the
feature.

The package's focus is:

> **LeRobot-first preflight with diagnostic outcome parity, plus OpenBot-owned
> evidence, readiness, provenance, snapshots, semantic diff, and verified
> remediation.**

### Outcome parity and implementation differentiation

For the core LeRobot workflow, outcome parity means a user can:

1. audit a local or revision-pinned Hub dataset;
2. distinguish format compatibility, policy readiness, publication readiness,
   partial coverage, and an operational scan failure;
3. locate a blocker at its episode, frame, feature, camera, shard, and evidence;
4. block CI, a merge, or publication deterministically;
5. obtain a finding-level remediation or a deterministic repair plan;
6. apply a safe copy-on-write metadata repair, or hand an edit to the official
   LeRobot tool, and then re-audit the result;
7. prove the resulting change with a snapshot and semantic diff.

Implementation differentiation means OpenBot owns the stable artifacts and
decision contract. It does not need to duplicate every mutation engine. LeRobot
remains the executor for dataset edit, merge, migration, conversion, and video
re-encoding; OpenBot performs the precondition check, records the plan, and
re-validates the produced dataset.

The durable OpenBot differentiation is the portable contract shared by raw
video, LeRobot, and later robot-data formats:

- deterministic source identity and component fingerprints;
- normalized feature, episode, task, stream, and shard inventory;
- semantic dataset diff between an approved baseline and a candidate;
- stable, evidence-addressable findings and a versioned readiness artifact;
- deterministic copy-on-write repair with pre/post evidence for the narrow
  metadata cases that have one authoritative reconstruction;
- portable artifacts that the OpenBot Catalog or a future explicitly published
  platform API can consume without putting credentials or service code in this
  package.

### Capability inclusion rule

A capability belongs in `0.0.3` P0 when it is required to prevent a common
loader, training, merge, or publication failure, or when omitting it breaks the
audit-to-remediation-to-verification loop. OpenBot may satisfy that outcome
through its own implementation or a verified handoff to an official tool.

A capability is P1 when it is useful for prioritization but depends on
task-specific thresholds, subjective quality judgment, or human review. It is
excluded only when neither a direct implementation nor an integration is needed
to complete the supported workflow.

## Direct landscape

| Capability | LeRobot official tools | `lerobot-doctor` | `robovet` | `openbot-data 0.0.2` | `openbot-data 0.0.3` decision |
|---|---|---|---|---|---|
| Authoritative LeRobot format and loader | Yes | No | No | Partial reader | Treat stable LeRobot source as the contract |
| Local LeRobot v2.1/v3 inspection | Loader and info tools | Yes | Yes | Yes, discovery-focused | Deep, read-only conformance |
| Hugging Face Hub source | Native | Yes | Metadata-first | No | Revision-pinned metadata/sample/full audit with explicit coverage |
| Stable JSON/Markdown and CI gate | Not a standalone validation contract | Yes | JSON and exit gate | JSON audit and fail-on | P0 outcome parity with a versioned readiness artifact |
| Finding-level triage and remediation | Loader errors and manual tools | Per-episode findings and suggested fixes | Episode/rule evidence | Basic findings | P0 structured location, evidence, remediation, and re-verification |
| Action/state/timestamp diagnostics | Loader/training surfaces failures | Yes | Yes | No | P0 deterministic format and readiness checks |
| Policy readiness | Training exposes incompatibility | ACT/Diffusion/VLA gate | Unsafe-to-train verdict | No | P0 explicit policy profile; never conflate it with base validity |
| Deterministic metadata repair | Dataset tools can recompute/edit some outputs | Backup plus auto-repair | Dry-run, metadata-only apply, backups | No | P0 copy-on-write repair for authoritative, lossless reconstructions |
| Edit, merge, convert, re-encode | Yes | Repair/trim/merge checks | Delegates merge/split/delete to LeRobot | No | Delegate execution to official LeRobot, then re-audit and diff |
| Pre/post merge compatibility | Merge requires compatible features | Yes | CI can gate dataset changes | No | P0 snapshot-based compatibility verdict; OpenBot does not merge |
| Idle-frame trim | Delete/edit primitives | Yes | Scoring identifies idle stretches | No | P1 synchronized, copy-on-write apply only after human review |
| Episode quality prioritization | Visualization and reward tooling | Generic score | Score explicitly described as triage | No | P1 transparent signals and ranked evidence; no opaque universal score |
| Readiness artifact | No standalone versioned contract | Report/gate result | Unsafe-to-train result | No | Core `openbot.dataset_readiness.v1` differentiator |
| Portable deterministic manifest | Not documented as a standalone contract | JSON reports | JSON reports | Yes | Preserve and extend through a new snapshot contract |
| Semantic baseline-to-candidate diff | Dataset operations, not a release diff contract | Merge check | Not documented as a general diff | No | Core differentiator |
| Raw robot-video directories | Not the dataset contract | Not documented | Not documented | Yes | Keep as a first-class adapter |
| Multi-format contract | LeRobot-specific | LeRobot-specific | LeRobot-specific | Video + partial LeRobot | Adapter architecture; add formats deliberately |
| OpenBot Catalog/release lineage | No | No | No | Catalog export | Produce portable artifacts; any platform registration requires a separately published API |

“Not documented” means the capability was not found in the reviewed public
contract. It is not a claim that no private or unreleased implementation exists.

## Why the overlapping capabilities exist

The overlap is not accidental. The same failures recur at the boundaries between
recording, storage, editing, and training:

- **Repair/remediation:** the LeRobot maintainers describe post-collection
  deletion, value/metadata correction, video replacement, merge, and split as
  an important tooling gap; without tools, users rely on ad hoc scripts or
  re-export the dataset
  ([LeRobot #2326](https://github.com/huggingface/lerobot/issues/2326)).
  Hugging Face's own dataset-quality review found very short episodes, manually
  deleted Parquet files without reindexing, inconsistent action/state
  dimensions, and ambiguous feature names
  ([community dataset challenges](https://github.com/huggingface/blog/blob/main/lerobot-datasets.md#challenges-with-current-community-datasets)).
  A validator that only reports these defects but cannot produce a safe next
  action leaves the workflow incomplete.
- **Merge check:** users explicitly need to combine many roughly 100-episode
  recordings into larger training sets
  ([LeRobot #847](https://github.com/huggingface/lerobot/issues/847)).
  Even an official-tool merge has been reported to produce frame/timestamp
  failures during training while each input trained independently
  ([LeRobot #2680](https://github.com/huggingface/lerobot/issues/2680)).
  This makes both pre-merge compatibility and post-merge verification P0.
- **Policy gate:** loadable data can still disagree with the selected policy.
  An official LeRobot failure shows camera feature names in the
  dataset/environment not matching the policy config and stopping execution
  with a `ValueError`
  ([LeRobot #2731](https://github.com/huggingface/lerobot/issues/2731)).
  Readiness therefore has to read the declared policy contract rather than rely
  only on storage-format validity.
- **Trim and quality prioritization:** demonstration curation can improve policy
  outcomes, so detecting suspect episodes is a real user need
  ([Demo-SCORE](https://arxiv.org/abs/2503.03707)). However, controlled research
  also finds that action-only scores miss structural defects and that detection
  accuracy does not guarantee downstream policy improvement
  ([curation metric audit](https://arxiv.org/abs/2606.05588)). The required
  outcome is transparent evidence and reviewable prioritization, not a universal
  score or unattended deletion.

These observations justify capability parity while also explaining the OpenBot
boundary: own deterministic decisions and the repair/verification envelope;
delegate mature payload operations; require human approval where task semantics
cannot be reconstructed from the data contract.

## 1. Hugging Face LeRobot

### Why it is the primary compatibility target

[LeRobot](https://github.com/huggingface/lerobot) owns the format, loader,
recording, editing, migration, visualization, training, and Hub workflow.
The latest stable package at this research snapshot is
[`lerobot 0.6.0`](https://pypi.org/project/lerobot/), while the stable dataset
format constant remains `v3.0`.

The important v3 properties are:

- multiple episodes share Parquet and MP4 shards;
- episode boundaries are resolved through relational metadata, not filenames;
- `meta/info.json` declares feature schemas, FPS, totals, and path templates;
- `meta/tasks.parquet`, `meta/episodes/**/*.parquet`, `data/**/*.parquet`, and
  `videos/**/*.mp4` form one cross-referenced dataset;
- `meta/stats.json` is used for normalization;
- Parquet writers must be finalized before the files are safe to load.

Sources:

- [LeRobotDataset v3.0](https://huggingface.co/docs/lerobot/v0.6.0/lerobot-dataset-v3)
- [LeRobot v3 porting guide](https://huggingface.co/docs/lerobot/v0.6.0/porting_datasets_v3)
- [LeRobot 0.6.0 release](https://github.com/huggingface/lerobot/releases/tag/v0.6.0)
- [LeRobot 0.6.0 dataset paths and metadata types](https://github.com/huggingface/lerobot/blob/v0.6.0/src/lerobot/datasets/utils.py#L82-L160)
- [LeRobot Dataset Tools](https://huggingface.co/docs/lerobot/v0.6.0/using_dataset_tools)

### Contract drift to handle explicitly

One current format page still mentions `meta/tasks.jsonl`, while the stable
`0.6.0` source, loader, and porting guide use `meta/tasks.parquet`. The v2.1
layout used JSONL metadata.

The `0.0.3` rule is:

1. stable tagged source and its loader behavior take precedence over prose drift;
2. the v3.0 adapter uses Parquet as the canonical task layout;
3. the reader may accept documented legacy JSONL layouts with an explicit
   compatibility finding;
4. unknown optional fields are preserved rather than rejected;
5. an unknown major format is never reported as compatible.

Optional language columns, tool schemas, depth metadata, intervention fields,
and reward/progress annotations must not be mistaken for a new hard directory
format unless the stable LeRobot format constant changes.

### Dependency decision

`lerobot` must not become a required runtime dependency:

- stable `lerobot 0.6.0` requires Python 3.12 or newer;
- `openbot-data` currently supports Python 3.9–3.12;
- the full package brings training, hardware, and model dependencies that a
  read-only validator does not need.

`openbot-data` should parse JSON, JSONL, Parquet, and media directly. A separate
Python 3.12 conformance job should install a pinned stable LeRobot release,
generate real fixtures, finalize them, and prove that accepted outputs load with
the official `LeRobotDataset`.

Official edit, migration, merge, conversion, re-encoding, visualization,
annotation, training, and evaluation remain LeRobot execution responsibilities.
The official
[`lerobot-edit-dataset`](https://huggingface.co/docs/lerobot/v0.6.0/using_dataset_tools)
already exposes delete, split, merge, add/remove feature, image-to-video, and
re-encode operations, including copy-to-a-new-dataset forms. OpenBot therefore
must not reimplement those payload mutations, but it does own their safety
envelope: validate preconditions, emit the exact operation plan, require a new
target for destructive changes, re-audit the output, and record a semantic diff.

## 2. `lerobot-doctor`

[`lerobot-doctor`](https://github.com/jashshah999/lerobot-doctor) is a direct
diagnostic comparison. Its published contract covers LeRobot v2/v3, local and
Hub inputs, JSON/Markdown output, CI exit behavior, metadata, temporal, action,
video, statistics, episode, consistency, training, anomaly, portability, and
per-episode checks. It also documents fix, trim, score, gate, and merge-check
commands. The reviewed PyPI release is `0.2.0` (2026-07-01), requires Python
3.10 or newer, and declares Apache-2.0.

Source: [PyPI project description](https://pypi.org/project/lerobot-doctor/).

### Required outcome parity in `0.0.3` P0

- group findings by metadata, schema, data, media, alignment, and readiness;
- collect multiple independent failures in one run;
- attach the affected episode, frame, feature, stream, timestamp, and path;
- offer stable JSON, Markdown, and CI-friendly exit behavior;
- make partial Hub coverage and sample limits explicit;
- gate a declared policy profile separately from base LeRobot validity;
- check feature, camera, FPS, task, and action-contract compatibility before an
  official merge, and re-audit the official merged output;
- attach a remediation to every actionable finding.

These are necessary outcomes, not optional imitation. The
[`gate` command](https://github.com/jashshah999/lerobot-doctor#gate-pre-training-check)
exists because a dataset that is loadable can still have the wrong dimensions,
episode length, or normalization inputs for a selected policy. The
[`merge-check` command](https://github.com/jashshah999/lerobot-doctor#merge-check)
exists because two individually valid datasets can be incompatible with each
other. LeRobot's
[official merge documentation](https://huggingface.co/docs/lerobot/v0.6.0/using_dataset_tools#merge-datasets)
requires identical features, so OpenBot must check that precondition before
delegating the actual merge.

### Repair is part of the workflow, with a narrower implementation

Finding an error without a safe next action leaves the most important user loop
incomplete. `lerobot-doctor` exposes
[dry-run and backup-based repair](https://github.com/jashshah999/lerobot-doctor#fix-auto-repair),
and `robovet` deliberately limits
[`fix` to metadata and creates backups](https://github.com/RonaldSit/robovet#what-fix-will-never-do-to-your-data).
`0.0.3` P0 must therefore provide:

- a finding-level remediation plan by default;
- deterministic copy-on-write repair only for metadata counters, episode
  lengths/relations, and statistics that can be reconstructed from one
  authoritative payload;
- an explicit output directory, with no default in-place mode;
- pre-repair and post-repair snapshots, semantic diff, and re-audit;
- idempotence and preservation of unknown metadata fields;
- refusal when more than one plausible repair exists.

Parquet/video surgery, episode deletion, merge, conversion, migration, and
re-encoding remain official LeRobot operations. OpenBot generates and verifies
the handoff instead of silently mutating those payloads.

### Do not blindly copy trim or a universal score

`lerobot-doctor` offers
[idle-frame trim](https://github.com/jashshah999/lerobot-doctor#trim-remove-idle-frames),
but trimming changes synchronized tabular rows, timestamps, episode relations,
statistics, and every camera window in the relational
[LeRobot v3 storage model](https://huggingface.co/docs/lerobot/v0.6.0/lerobot-dataset-v3).
Whether an idle interval is useless also depends on task phase and action
semantics. P0 therefore detects candidate spans and produces a non-executing,
evidence-rich plan that names every synchronized artifact affected. P1 may
allow copy-on-write apply only after explicit human approval and a full
re-audit.

A generic 0–100 score is not P0 validity. `robovet` itself describes its
[score as a triage list, not a judge](https://github.com/RonaldSit/robovet#how-youll-actually-use-it).
Signals such as range, smoothness, or idleness cannot be interpreted identically
for joint-space, end-effector, absolute, relative, and delta actions; LeRobot
documents these distinct
[action representations](https://huggingface.co/docs/lerobot/v0.6.0/action_representations).
P0 may expose a bounded set of low-cost signals such as exact duplication,
constant dimensions, and static spans, but only as raw, non-ranking advisories.
P1 may add advanced per-signal metrics, thresholds, coverage, and ranked
episodes. Both must retain the raw evidence and must not turn one opaque number
into format validity or automatic deletion.

## 3. `robovet`

[`robovet`](https://github.com/RonaldSit/robovet) is the closest architecture
comparison. It documents stable rule families for structure, data, time,
statistics, video, and metadata; a Hub metadata-only mode; JSON CI output;
episode triage; and metadata-only repair with backups. The reviewed PyPI release
is `0.2.2` (2026-06-10), requires Python 3.10 or newer, and declares Apache-2.0.

Source: [PyPI project description](https://pypi.org/project/robovet/).

### Adopt as design input

- metadata-first checks before downloading large data/video shards;
- rule codes tied to concrete failure classes;
- distinguish “metadata clean” from “fully validated”;
- show which expensive checks were skipped and why;
- make repair dry-run-first, copy-on-write, narrowly scoped, and independently
  re-verifiable;
- expose an explicit readiness verdict that CI can gate without reducing the
  result to a numeric score.

The
[published workflow](https://github.com/RonaldSit/robovet#how-youll-actually-use-it)
demonstrates why both parts belong in P0: a finding names the affected episode,
a narrow metadata fix is applied with a backup, and the doctor is run again to
prove that the failure is gone. The remote workflow also states that a
metadata-only pass is “META CLEAN”, never a full clean result. OpenBot should
preserve those outcomes in its own versioned readiness and remediation
artifacts rather than copy Robovet's wording or rule identifiers.

### Do not add as a runtime dependency

Both `robovet` and `lerobot-doctor` are young, overlapping tools with their own
output and severity contracts. Depending on either one in `0.0.3` would:

- make OpenBot's stable finding contract depend on another alpha contract;
- duplicate or contradict findings when both engines run;
- narrow the package to LeRobot instead of a format-neutral snapshot layer;
- raise the minimum Python version for current Python 3.9 users;
- make the primary differentiation harder to explain.

Their implementations can inform rules. Code reuse, if any, requires an explicit
Apache-2.0 license and attribution review. A later external-engine adapter is only
worthwhile after a stable machine-readable interoperability contract exists.

This dependency decision does not remove capability scope: policy readiness,
merge compatibility, finding-level triage/remediation, and deterministic
copy-on-write repair are OpenBot P0 requirements implemented behind OpenBot's
own contracts.

## Pinned outcome-comparison evidence

The release comparison uses one official LeRobot 0.6.0 v3.0 dataset, then
changes only `meta/info.json#/total_frames` from the actual 12 rows to 999. The
result is normalized to the user decision rather than tool-specific rule names:

| Tool | Pinned version | Clean fixture | Stale counter | Critical evidence |
|---|---:|---|---|---|
| `lerobot-doctor` | `0.2.0` | exit 0, non-blocking | exit 1, blocking | actual 12 frames vs declared 999 |
| `robovet` | `0.2.2` | exit 0, non-blocking | exit 1, blocking | `META-501`, `META-502` |
| `openbot-data` | `0.0.3` | no error findings | exit 2 with `--fail-on error` | `LEROBOT_FRAME_COUNT_MISMATCH`, `LEROBOT_STATS_COUNT_MISMATCH` |

The portable evidence is frozen in
`tests/fixtures/competitor-outcomes-v003.json`; its test reconstructs the clean
and corrupted inputs and proves that the released OpenBot package keeps the same
blocking outcome. Counts and labels intentionally differ because outcome parity
does not mean cloning another package's rules.

### Deliberately different or unsupported behavior

| Competitor behavior | OpenBot substitute in `0.0.3` | Acceptance evidence |
|---|---|---|
| in-place repair plus adjacent backups | deterministic plan, distinct copy-on-write destination, source fingerprint check, post-audit, snapshot diff, and receipt | repair schemas, `tests/test_repair.py`, official-loader repair conformance |
| automatic or directly executable idle trimming | raw span evidence plus `openbot.idle_trim_plan.v1`; human review is mandatory and the plan enumerates every synchronized contract | `tests/test_readiness.py`; readiness trim-plan evidence |
| universal episode score or automatic ranking | raw duplicate, constant-dimension, range, duration, coverage, and idle-span measurements with explicit thresholds; no aggregate validity score | `tests/test_readiness.py`, `openbot_data/triage.py` |
| package-owned merge executor | exact pinned official LeRobot command, pre-merge compatibility, full post-audit, loader smoke, lineage reconciliation, and semantic diff | merge schemas, `tests/test_merge.py`, official 0.6.0 merge conformance |
| HTML as the primary report | stable versioned JSON plus deterministic Markdown projections suitable for CI and review | schema-validated examples and CLI projection tests |
| competitor package as a runtime engine | OpenBot-owned adapters/rules and a fixed external comparison fixture; competitors remain isolated release-test inputs | `tests/test_competitor_outcomes.py` and this document |

## `0.0.3` incorporation boundary

### P0: required

1. **Deterministic copy-on-write repair**
   - plan is the default;
   - apply requires an explicit output directory and never overwrites the source;
   - only an unambiguous metadata/statistics reconstruction is eligible;
   - every plan records input fingerprint, expected edits, rule codes, and
     preconditions;
   - apply verifies the input fingerprint, preserves unknown fields, emits
     pre/post snapshots and a diff, and runs the audit again;
   - the same repair run twice is idempotent.
2. **Policy readiness**
   - base `lerobot-core` validity remains separate from declared ACT, Diffusion,
     VLA, or other policy profiles;
   - a profile checks required features, action/state dimensions and semantics,
     episode/chunk windows, delta timestamps, and normalization inputs;
   - unknown action semantics or skipped payload checks produce `partial`, not
     `ready`.
3. **Merge compatibility**
   - compare normalized feature, dtype/shape, camera, FPS, task mapping, action
     semantics, format, and revision contracts before an official merge;
   - classify incompatible inputs as blocking;
   - delegate the merge to `lerobot-edit-dataset`, then audit and diff its output.
4. **Finding-level triage and remediation**
   - every actionable finding identifies its episode/frame/feature/camera/shard
     when available;
   - low-cost duplicate, constant-dimension, and static-span advisories expose
     raw values, thresholds, applicability, and coverage without ranking or
     automatic deletion;
   - a static-span finding can emit a non-executing trim plan that identifies
     the synchronized Parquet, timestamp, relation, statistics, and camera
     changes that a later approved transform would require;
   - remediation declares whether OpenBot can copy-on-write repair it, an
     official LeRobot operation is required, or human judgment is required;
   - reports provide stable JSON, Markdown, human summary, and exit behavior.
5. **Versioned readiness artifact**
   - add `openbot.dataset_readiness.v1`;
   - include source and resolved revision, profile, `ready|blocked|partial`,
     validation coverage, blockers, warnings, triage, remediation references,
     snapshot fingerprint, and tool/contract versions;
   - prohibit a metadata-only or sampled audit from claiming full readiness.
6. **Verified official-tool handoff**
   - actual delete, split, merge, migration, conversion, and re-encoding execute
     through the official LeRobot tool;
   - OpenBot records the proposed command without secrets, validates the target
     is separate where data may be removed, and performs a full post-operation
     audit plus semantic diff.

### P1: useful, but judgment-dependent

- advanced transparent quality signals: smoothness, action jumps, distribution
  shift, visual quality, duration outliers, and ranked episode triage;
- every signal exposes its raw metric, threshold, profile, sampling coverage,
  and evidence instead of only a composite score;
- copy-on-write trim apply only after explicit human approval of the P0 plan
  and visual evidence, with synchronized changes across Parquet rows,
  timestamps, episode relations, statistics, and every camera window, followed
  by official-loader conformance, a full audit, and a semantic diff;
- no unattended trim and no automatic episode deletion based on a score.

## 4. robomimic

[robomimic](https://robomimic.github.io/docs/datasets/overview.html) provides a
clear HDF5 trajectory contract and utilities for dataset information and
trajectory playback.

Useful rules:

- arrays belonging to one trajectory must agree on their leading time dimension;
- image dtype and shape are explicit;
- action-range checks apply only when the format/profile declares normalized
  actions;
- dataset structure and playback are separate concerns.

For `0.0.3`, translate only the format-neutral ideas into LeRobot rules. A
read-only robomimic/HDF5 adapter belongs to a later release. The robomimic
`[-1, 1]` action convention must not become a universal LeRobot rule.

Sources:

- [robomimic dataset structure](https://robomimic.github.io/docs/datasets/overview.html)
- [robomimic dataset visualization](https://robomimic.github.io/docs/tutorials/dataset_contents.html)

## 5. RLDS and Open X-Embodiment

[RLDS](https://github.com/google-research/rlds) defines episodic datasets as
episodes containing steps, with lifecycle markers such as `is_first`,
`is_last`, and `is_terminal`. The repository was archived in November 2025 and
the TensorFlow dependency is too heavy for the core package.

[Open X-Embodiment](https://github.com/google-deepmind/open_x_embodiment)
demonstrates why action semantics cannot be inferred from shape alone. Depending
on the source, a dimension may be absolute position, delta, velocity, or another
control representation.

Planning decision:

- keep finite, dtype, shape, timestamp, and declared-schema checks generic;
- apply range or control-semantic rules only under an explicit profile;
- plan RLDS/Open X read-only adapters after the LeRobot and HDF5 contracts are
  stable;
- do not add TensorFlow to the core dependency set.

## 6. Pandera and Datumaro

[Pandera](https://pandera.readthedocs.io/en/latest/dataframe_schemas.html)
provides useful schema/data separation, custom checks, structured reason codes,
and lazy error collection.

[Datumaro](https://open-edge-platform.github.io/datumaro/latest/docs/explanation/architecture)
separates format import, a normalized dataset model, validation, transformation,
and export.

Planning decision:

- adopt the architectural ideas, not the runtime dependencies;
- introduce internal `FormatProbe`, `DatasetReader`, `AuditRule`, and renderer
  boundaries;
- return all deterministic findings that can be collected safely in one pass;
- do not expose arbitrary third-party plugin discovery in `0.0.3`;
- do not treat an inferred schema as an authoritative contract.

## 7. DVC, Rerun, and Foxglove

[DVC diff](https://dvc.org/doc/command-reference/diff) is a useful reference for
added, deleted, modified, and renamed change reporting. OpenBot Data should add
episode-, feature-, task-, stream-, and shard-aware semantic diff, but it should
not implement remote storage, cache, checkout, push, or pull.

[Rerun](https://rerun.io/docs/getting-started/data-in/open-any-file) and
[Foxglove](https://docs.foxglove.dev/docs/visualization/connecting/local-data)
are visualization targets, not validation dependencies. LeRobot already offers
Rerun and Foxglove visualization. A later handoff may open a finding at an
episode/timestamp; `0.0.3` should not build another viewer or convert datasets
into a visualization-specific storage format.

## Version placement

| Version | Planned focus | Explicitly deferred |
|---|---|---|
| `0.0.3` P0 | LeRobot 0.6/v3.0 conformance, v2.1 read compatibility, Hub metadata/sample/full audit, policy readiness, finding-level triage/remediation, deterministic copy-on-write repair, merge compatibility, `openbot.dataset_readiness.v1`, portable snapshot, semantic diff, and verified official-tool handoff | In-place mutation, OpenBot-owned edit/merge/re-encode engines, opaque score, custom viewer |
| `0.0.3` P1 | Transparent advanced quality signals and ranked episode evidence; human-reviewed synchronized copy-on-write trim apply | Unattended trim, score-driven deletion, task-success judgment |
| [`0.0.4`](version-0.0.4.md) | Read-only robomimic/HDF5 preflight and optional P1 Rerun handoff | Generic HDF5 validation, HDF5 conversion, and simulator replay |
| `0.0.5` | RLDS/Open X read-only adapter and cross-format provenance profiles | TensorFlow in core, action-coordinate conversion |
| Later | ROS bag/MCAP adapter, Foxglove handoff, explicit DVC hooks | Replacing ROS, DVC, Rerun, or Foxglove |

Package versions in this table are independent from OpenBot platform and
`openbot-sdk` versions. The main OpenBot repository currently provides the API
framework, while `openbot-sdk` is only its thin client. No Hosted Data product or
robot-data processing API is implied by these local-library plans.
