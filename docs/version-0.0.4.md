# OpenBot Data 0.0.4 — robomimic/HDF5 Preflight

> Status: **Planned; documentation contract only**
> Baseline package: `0.0.3`
> Primary compatibility target: robomimic `0.5.x` local single-file HDF5 datasets
> Release rule: no item is implemented or released merely because it appears in
> this document

## Status and scope authority

This document is the source of truth for the planned `openbot-data==0.0.4`
package release. It defines the required user outcomes, public interfaces,
compatibility boundary, non-goals, implementation order, and release gates.

The status terms are normative:

- **released** means the versioned package passed every release gate and is
  publicly available from PyPI;
- **implemented** means code, fixtures, and focused acceptance evidence exist in
  the source tree, but the complete release gate has not necessarily passed;
- **planned** means the behavior is required by this contract but has not yet
  been accepted as implemented;
- **deferred** means the behavior is outside `0.0.4` and cannot silently become a
  release blocker.

Every P0 row below is **planned** at this checkpoint. P1 is optional and must not
block the release unless this contract is explicitly updated before feature
freeze.

## Goal

`0.0.4` should answer six questions before a local robomimic dataset is used for
training, copied, or handed to another team:

1. Is this file readable as a robomimic-compatible HDF5 dataset without loading
   the entire payload into memory?
2. Are the root metadata, environment metadata, demonstrations, observations,
   actions, rewards, terminal flags, and filter keys structurally consistent?
3. Which demonstration, field, or step is affected when lengths, shapes, dtypes,
   references, or payload values are invalid?
4. Was only metadata inspected, were deterministic samples decoded, or was the
   complete payload streamed and checked?
5. Is the dataset compatible with a declared robomimic storage/readiness profile,
   or is the result `BLOCKED` or `PARTIAL`?
6. Does each finding carry bounded evidence coordinates that an optional Rerun
   handoff can open without converting or uploading the complete dataset?

The version theme is:

> **Read-only robomimic/HDF5 compatibility with explicit coverage and portable
> evidence.**

This is not a generic HDF5 browser. It is a format-aware preflight workflow that
extends the deterministic audit, snapshot, diff, readiness, and evidence
contracts released in `0.0.3`.

## Canonical feature map

| ID | P0 feature | User-visible outcome | Primary contract |
|---|---|---|---|
| P0.1 | File-source contract and deterministic format detection | A caller can pass one `.hdf5` or `.h5` file through the existing generic workflow and get an explicit robomimic, generic-HDF5, unsupported, or malformed result | additive source and `--format` contract |
| P0.2 | Read-only, bounded HDF5 inventory | Groups, attributes, datasets, shapes, dtypes, chunks, compression, and references are inventoried without mutating the source or materializing full arrays | internal `robomimic_hdf5` adapter |
| P0.3 | robomimic hierarchy and environment metadata validation | `/data`, `total`, `env_args`, `demo_*`, `num_samples`, and optional `model_file` problems are reported with stable locations | `openbot.dataset_audit.v1` |
| P0.4 | Trajectory and field consistency | Leading dimensions and cross-demonstration field contracts are checked for actions, states, rewards, dones, `obs`, and `next_obs` | collect-all validation layers |
| P0.5 | Image, numeric payload, and mask integrity | Image dtype/layout, non-finite numeric values, compressed reads, and `mask/<filter_key>` references are checked at the requested integrity level | deterministic findings plus coverage |
| P0.6 | Portable snapshot, semantic diff, and readiness | HDF5 metadata and dataset contracts can be snapshotted, compared, and evaluated with a robomimic-specific readiness profile | existing snapshot/diff/readiness v1 artifacts |
| P0.7 | Stable CLI and Python surface | Existing audit/snapshot/readiness workflows accept robomimic files without requiring robomimic, robosuite, MuJoCo, or Rerun in the core install | additive public API and CLI |
| P0.8 | Safety and compatibility regression | Invalid, hostile-looking, externally linked, over-budget, compressed, and partially readable files fail safely while `0.0.3` behavior stays compatible | fixtures and acceptance matrix |
| P0.9 | Official conformance and release evidence | A pinned official robomimic loader agrees with OpenBot on valid fixtures before the package can be released | dedicated CI/release conformance job |

## P0.1 — File-source contract and format detection

The current preflight source is directory-oriented. `0.0.4` must extend the
source contract before registering an HDF5 adapter:

- shared preparation plus audit/snapshot/readiness entry points accept either a
  directory or one regular file;
- `--format` accepts `auto|video|lerobot|robomimic`;
- `.hdf5` and `.h5` are hints, not proof of the format;
- `auto` checks the HDF5 signature and robomimic hierarchy before selecting the
  adapter;
- a valid generic HDF5 file without the robomimic contract is reported as
  unsupported, not mislabeled as robomimic;
- an absent optional HDF5 dependency returns a structured installation message,
  not an import traceback;
- symlinks remain skipped by default; an explicitly followed file must still
  resolve inside the declared source root;
- missing, non-regular, unreadable, truncated, or concurrently replaced files
  produce stable findings.

Directory scanning must not begin treating arbitrary nested HDF5 files as one
combined dataset. `0.0.4` supports one local robomimic HDF5 file per preflight
operation.

### Internal architecture boundary

The implementation must not disguise an HDF5 file as a directory:

- introduce one resolved local-source value carrying `path`, `boundary_root`,
  and `file|directory` kind;
- generalize adapter identity to format/contract identity while keeping
  LeRobot-specific major/minor version logic inside the LeRobot adapters;
- add an immutable HDF5 object/array record for HDF5 path, demonstration key,
  shape, dtype, chunks, and compression rather than overloading a media-file
  record;
- keep robomimic validation in its own module and dispatch from the shared
  validation layer;
- prepare the source once; renderers and artifact builders must not reopen or
  rescan the HDF5 file independently.

These changes must preserve the byte-level output of unchanged `0.0.3` fixtures.

## P0.2 — Read-only, bounded HDF5 inventory

The adapter must use `h5py` through a dedicated `openbot-data[robomimic]`
optional extra. The core install must not depend on robomimic, robosuite,
MuJoCo, simulator assets, or Rerun.

Required behavior:

- open the source explicitly in read-only mode;
- record the file size and a source identity before and after inspection;
- enumerate group, attribute, and dataset metadata deterministically;
- record path, shape, rank, dtype, chunk shape, compression/filter metadata,
  storage size where available, and whether the object is external or virtual;
- avoid `dataset[...]` or equivalent full materialization during metadata and
  sample audits;
- read deterministic first/middle/last positions only in sample mode;
- stream full-mode payload checks in bounded chunks;
- expose caller-configurable object, rank, attribute-byte, payload-read, and
  per-read chunk budgets;
- return `PARTIAL` coverage with the exact exhausted budget rather than claiming
  full validation;
- never auto-load filter plugins, execute file-provided code, or instantiate an
  environment.

External links, external storage, and virtual datasets may reference other
files. They must not be followed by default. The audit records the reference and
emits a blocker or explicit skipped capability. A future opt-in policy may allow
root-confined references; it is not required in `0.0.4`.

Read-only mode proves that OpenBot does not intentionally mutate the source. It
does not make arbitrary malicious HDF5 files safe to parse. The documentation
must tell users to inspect untrusted files in an OS/container sandbox and must
not advertise the adapter as a security scanner.

The implementation must freeze and document default budgets before P0.2 can be
marked implemented. Budget values are part of the public reproducibility
contract and cannot vary by machine memory.

## P0.3 — robomimic hierarchy and environment metadata

The adapter validates the official single-file structure without importing the
robomimic runtime.

### Root contract

- `/data` exists and is a group;
- `/data.attrs["total"]` is a non-negative integer;
- `/data.attrs["env_args"]` is valid JSON and resolves to an object;
- `env_args` contains `env_name`, the runtime-compatible `type`, and `env_kwargs`
  with stable types;
- the older/documented `env_type` spelling is accepted as an alias but produces
  an explicit compatibility finding when `type` is absent;
- unknown `env_args` fields are preserved as extensions, not rejected;
- secrets, absolute machine paths, simulator XML, and oversized metadata are not
  copied verbatim into portable artifacts;
- `/data.attrs["total"]` equals the sum of validated demonstration lengths when
  complete coverage makes that comparison possible.

### Demonstration contract

- demonstration groups follow the stable `demo_<index>` naming convention;
- indexes are unique and sorted numerically in canonical output;
- every demonstration has a non-negative integer `num_samples` attribute;
- optional `model_file` is inventoried and bounded but never parsed or loaded as
  a simulator model;
- empty datasets and empty demonstrations are distinguishable from malformed
  metadata;
- unknown groups and attributes are preserved in an extension inventory and do
  not silently become required fields.

## P0.4 — Trajectory and field consistency

For each demonstration, the adapter inventories:

- `actions`;
- `states` when present;
- `rewards` and `dones` when present;
- observation datasets under `obs`;
- next-observation datasets under `next_obs`;
- additional fields as extensions without guessing their semantics.

Validation must:

- compare each time-indexed field's leading dimension with `num_samples`;
- report scalar, empty, ragged, unsupported-object, and unexpected-rank fields;
- compare keys, shapes excluding the leading dimension, and dtypes across
  demonstrations;
- report missing fields as required, optional, or unknown according to the
  selected profile rather than one hard-coded training algorithm;
- preserve action components and declared action metadata without flattening or
  reordering them;
- never infer that an action is absolute, delta, joint-space, Cartesian, or
  normalized solely from numeric range;
- keep raw robomimic datasets without `obs` distinguishable from corrupted
  postprocessed datasets;
- allow missing, empty, or dummy `states` for non-robosuite data when the
  declared environment contract permits it;
- treat absent `next_obs` according to the declared profile because some
  imitation-learning workflows do not require it.

Base storage validity and algorithm-specific training readiness are separate.
A structurally valid file is not automatically ready for BC, Diffusion Policy,
offline RL, simulator replay, or real-robot deployment.

## P0.5 — Images, numeric payloads, compression, and masks

### Image and numeric evidence

- fields explicitly declared or confidently identified as RGB observations are
  checked for `uint8` and channel-last `(H, W, C)` layout;
- depth and other image-like modalities are kept separate from RGB rules;
- heuristic name/shape matches may create advisory evidence but cannot alone
  create a blocking image-type finding;
- sample and full integrity levels check numeric arrays for NaN and infinity;
- `dones` values outside the declared boolean/`0|1` contract are reported;
- observed action ranges are reported as raw evidence only;
- an action-range blocker requires an explicit user or readiness-profile
  contract;
- sample locations are deterministic and recorded in coverage;
- full mode streams every supported array in bounded chunks and records any
  unreadable chunk or compression-filter failure.

### Filter-key integrity

When `/mask` exists:

- every child is a one-dimensional string-like dataset of demonstration names;
- every referenced demonstration exists;
- duplicate references within one filter key are reported;
- empty filter keys are reported without being silently removed;
- overlapping train/validation-style keys are evidence, not automatically an
  error unless a profile forbids the overlap;
- invalid encoding, unsupported object/reference dtype, or unreadable payload is
  reported with the exact mask path.

## P0.6 — Artifacts, snapshot, diff, and readiness

`0.0.4` reuses the existing versioned artifacts instead of creating a parallel
robomimic-only report format.

### `openbot.dataset_audit.v1`

Additive robomimic findings use stable codes, severity, layer, capability,
location, evidence, and remediation. At minimum the finding registry must cover:

- file open/signature/dependency failure;
- unsupported generic HDF5;
- external or virtual reference blocked;
- missing or invalid `/data`, `total`, or `env_args`;
- invalid demonstration name or `num_samples`;
- total and leading-dimension mismatch;
- missing or incompatible action/state/reward/done/observation fields;
- image dtype/layout mismatch;
- invalid or dangling mask reference;
- non-finite numeric payload;
- unreadable chunk or unsupported compression/filter;
- object, rank, attribute-byte, or payload-read budget exhaustion.

Exact finding codes, severity, and remediation text must be frozen in
`audit-findings.md` before implementation acceptance.

### `openbot.dataset_snapshot.v1`

The portable snapshot adds, without breaking existing LeRobot snapshots:

- `input_format=robomimic` and adapter contract version;
- source file identity and optional explicit SHA-256 coverage;
- normalized environment identity plus a digest of the bounded raw metadata;
- demonstration inventory and total samples;
- field key/shape/dtype contracts;
- filter-key membership digests and counts;
- HDF5 layout/filter inventory;
- explicit skipped capabilities and integrity coverage.

Portable artifacts must not expose absolute paths, credentials, raw simulator
XML, or arbitrary environment metadata values that may contain secrets.

### Semantic diff

The existing diff contract classifies at least:

- added/removed/renamed demonstrations;
- sample-count changes;
- environment identity changes;
- added/removed fields;
- shape, dtype, action-contract, or observation-contract changes;
- mask membership changes;
- HDF5 layout/filter changes separately from semantic payload changes;
- coverage changes that prevent a conclusive comparison.

Changing compression or chunking alone is not automatically a semantic breaking
change. Changing time-indexed shapes, action contracts, or declared environment
identity may be breaking.

### Readiness

Ship one built-in `robomimic-core` profile, stored as
`robomimic-core-v1.json`. It gates only the declared storage and loader contract:
readable file, valid hierarchy, valid environment metadata, at least one valid
demonstration, usable actions, consistent time-indexed fields, valid masks, and
complete required coverage.

`metadata` or `sample` integrity may return useful audit evidence but cannot
return `READY` for `robomimic-core`; incomplete required payload coverage is
`PARTIAL`.

It must not claim algorithm performance, simulator replayability, task success,
or deployment safety. Algorithm-specific requirements remain explicit policy
overrides until evidence justifies additional built-in profiles.

## P0.7 — Public CLI and Python contract

Planned CLI surface:

```bash
openbot-data audit ./dataset.hdf5 \
  --format robomimic \
  --integrity sample \
  --out ./audit.json \
  --fail-on error

openbot-data snapshot ./dataset.hdf5 \
  --format robomimic \
  --integrity metadata \
  --out ./snapshot.json

openbot-data readiness ./dataset.hdf5 \
  --format robomimic \
  --profile robomimic-core \
  --integrity full \
  --out ./readiness.json

openbot-data diff ./baseline.json ./candidate.json --out ./diff.json
```

Planned Python surface:

```python
from openbot_data import (
    audit_dataset,
    build_dataset_snapshot,
    evaluate_dataset_readiness,
    read_robomimic,
)

dataset = read_robomimic("./dataset.hdf5", integrity="metadata")
audit = audit_dataset(
    "./dataset.hdf5",
    input_format="robomimic",
    integrity="sample",
)
snapshot = build_dataset_snapshot(
    "./dataset.hdf5",
    input_format="robomimic",
    integrity="metadata",
)
readiness = evaluate_dataset_readiness(
    "./dataset.hdf5",
    profile="robomimic-core",
    input_format="robomimic",
    integrity="full",
)
```

Contract requirements:

- `auto` and explicit `robomimic` produce the same canonical result for one
  valid fixture;
- public results remain JSON-compatible and deterministic;
- `--fail-on` keeps the existing exit-code semantics;
- budget exhaustion produces a valid artifact with `PARTIAL` coverage;
- an unavailable optional extra produces one actionable error;
- existing video and LeRobot calls remain source compatible;
- `diff` remains format-neutral and consumes snapshots, not live HDF5 files.

No `convert`, `rewrite`, `repair`, `merge`, or simulator command is added for
HDF5 in `0.0.4`.

`inspect`, `scan`, `catalog`, `repair`, `verify`, `merge-check`, and
`verify-merge` keep their existing released scope. A robomimic file passed to an
unsupported command returns an explicit format/configuration error rather than
an empty video or LeRobot result.

## P0.8 — Safety and fixture matrix

The acceptance suite must include small, redistributable fixtures for:

### Positive fixtures

- low-dimensional robomimic dataset;
- RGB observation dataset in `uint8` channel-last layout;
- raw dataset with states/actions but no observations;
- valid train/validation masks;
- gzip or another built-in portable compression filter;
- unknown extension fields that are preserved safely.

### Negative fixtures

- non-HDF5 bytes with an `.hdf5` suffix;
- valid generic HDF5 without robomimic structure;
- missing `/data`, `total`, or `env_args`;
- invalid `env_args` JSON or missing required keys;
- malformed demo name or `num_samples`;
- mismatched action, reward, done, state, `obs`, or `next_obs` length;
- cross-demonstration shape/dtype mismatch;
- invalid RGB dtype or channel layout;
- NaN/infinity in sampled and non-sampled positions;
- mask with a missing, duplicate, invalidly encoded, or empty demo reference;
- external link, external storage, or virtual dataset;
- unreadable/truncated payload and unsupported filter;
- object, rank, attribute, and read-byte budget exhaustion;
- symlink outside the allowed root and source replacement during inspection.

Safety assertions:

- source bytes, size, mtime, and permissions do not change;
- no code path opens the source in a write-capable mode;
- no external path is dereferenced by default;
- metadata/sample mode never materializes a whole unbounded dataset;
- full mode has a fixed peak read chunk bound;
- errors contain portable paths and do not leak absolute local paths;
- the complete `0.0.3` test suite remains green.

## P0.9 — Official robomimic conformance

CI and Release must add an isolated conformance job that:

- installs the pinned supported robomimic version separately from the core test
  matrix;
- uses an official writer, official fixture, or official loader path that does
  not require launching a simulator;
- proves a valid OpenBot-accepted fixture can be opened by the official loader;
- proves OpenBot reports the same demonstration count, sample count, field keys,
  shapes, dtypes, and mask membership;
- includes at least one compressed image observation fixture;
- records the supported robomimic and h5py versions in release evidence;
- runs on every supported Python version where the optional HDF5 dependency is
  supported, with any narrower official-robomimic matrix documented explicitly.

The existing official LeRobot conformance job remains required. Supporting a
second format must not weaken the released LeRobot contract.

## P1 candidate — bounded Rerun evidence handoff

Rerun is a visualization target, not a validator or storage dependency. After
P0 is complete, an optional `rerun` extra may:

- open one selected demonstration, observation key, and step range from an audit
  finding;
- use an explicit `step` timeline and stable entity paths;
- log bounded RGB/depth frames, actions, states, rewards, dones, and text
  metadata when their contracts are known;
- preserve the audit finding code and location as annotation context;
- avoid loading or converting the complete HDF5 file;
- write a local `.rrd` only when the user supplies an explicit output path;
- never auto-spawn a viewer, open a gRPC connection, or upload to Rerun Cloud;
- fail without affecting the audit/snapshot/readiness result.

This handoff is P1 and does not block `0.0.4` unless promoted to P0 through an
explicit contract change.

## Explicit non-goals

- generic validation of arbitrary HDF5 schemas;
- HDF5 conversion, writing, repair, repacking, rechunking, or recompression;
- conversion between robomimic, LeRobot, RLDS, Open X, ROS bag, or MCAP;
- simulator, robosuite, or MuJoCo instantiation and trajectory replay;
- training, policy evaluation, reward relabeling, or task-success judgment;
- automatic action-coordinate conversion or universal normalization rules;
- silently flattening action dictionaries or observation groups;
- following external links, virtual sources, or external storage by default;
- Hub download of remote HDF5 files;
- multi-file training-set balancing, merging, or deduplication;
- a custom viewer or a required Rerun dependency;
- an opaque aggregate quality score;
- platform authentication, remote jobs, billing, Workers, or storage.

RLDS/Open X remains planned for `0.0.5`; it is not part of this version.

## Implementation order

1. **Freeze source and safety contracts**: file source, format probe, symlink and
   external-reference policy, optional dependency, budgets, and finding matrix.
2. **Build the metadata adapter**: hierarchy, environment metadata,
   demonstrations, dataset contracts, masks, and immutable adapter output.
3. **Add layered payload validation**: metadata/sample/full coverage, numeric
   checks, images, compression failures, and bounded streaming.
4. **Integrate existing artifacts**: audit, snapshot, semantic diff, readiness,
   JSON Schemas, and Markdown renderers.
5. **Expose public interfaces**: `--format robomimic`, generic Python functions,
   `read_robomimic`, optional-extra errors, and stable exit behavior.
6. **Close acceptance evidence**: positive/negative fixtures, determinism,
   source immutability, memory/read budgets, Python matrix, and regressions.
7. **Add official conformance**: pinned robomimic loader agreement in CI and
   Release.
8. **Evaluate P1 Rerun handoff** only after every P0 release gate passes.

## Release acceptance criteria

`0.0.4` is release-ready only when:

- every P0 row is implemented with linked code, positive and negative fixtures,
  documentation, and acceptance evidence;
- all finding codes, severities, locations, coverage states, and remediation
  messages are documented and tested;
- repeated audits and snapshots of unchanged fixtures are byte-stable except for
  explicitly excluded timestamps;
- metadata, sample, full, budget-exhausted, and unsupported-filter paths are all
  distinguishable in machine-readable output;
- source immutability and default external-reference blocking are proven;
- the `robomimic-core` profile cannot return `READY` with missing required
  coverage;
- the pinned official robomimic loader agrees with OpenBot on valid fixtures;
- the existing video and LeRobot acceptance/conformance suites remain green;
- core-only and `[robomimic]` clean installations both pass their documented smoke
  tests;
- Python 3.9–3.12, Ruff, mypy, package build, wheel-content, JSON Schema,
  examples, and Twine checks pass;
- `VERSION`, `CHANGELOG.md`, README, API reference, examples, and release notes
  agree on the shipped scope;
- tag, GitHub Release, and PyPI publication remain separate explicit actions.

Passing unit tests or registering an adapter is not enough to mark the version
released.

## Research basis

The plan follows the current official contracts rather than treating file-name
conventions as authoritative:

- [robomimic 0.5 dataset structure and conventions](https://robomimic.github.io/docs/datasets/overview.html)
- [robomimic v0.5.0 runtime environment metadata reader](https://github.com/ARISE-Initiative/robomimic/blob/v0.5.0/robomimic/utils/file_utils.py)
- [robomimic v0.5 release](https://github.com/ARISE-Initiative/robomimic/releases/tag/v0.5.0)
- [h5py dataset metadata, slicing, chunks, compression, external and virtual storage](https://docs.h5py.org/en/stable/high/dataset.html)
- [h5py group and link traversal](https://docs.h5py.org/en/stable/high/group.html)
- [HDF5 security expectations and limitations](https://github.com/HDFGroup/hdf5/security)
- [Rerun timelines](https://rerun.io/docs/concepts/logging-and-ingestion/timelines)
- [Rerun file ingestion boundary](https://rerun.io/docs/getting-started/data-in/open-any-file)

These package versions are independent from OpenBot platform and
`openbot-sdk` versions. A future API adapter requires an actually published
platform contract and remains outside `0.0.4`.
