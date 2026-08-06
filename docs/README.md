# OpenBot Data documentation

OpenBot Data is a local, open-source Python library. It inspects robot-video
directories and local LeRobot v2.1/v3 repositories before training or
publication. It does not call the OpenBot platform by default.

## Start here

- [Getting started](getting-started.md): installation, CLI, Python workflow, and
  the runnable demo.
- [API reference](api-reference.md): public Python functions, CLI commands,
  parameters, return values, and failure behavior.
- [Audit finding codes](audit-findings.md): stable machine-readable validation
  codes and severities.
- [0.0.2 release contract](version-0.0.2.md): scope and acceptance criteria.
- [0.0.3 feature and release contract](version-0.0.3.md): the complete required
  feature map, public artifacts and commands, LeRobot compatibility, readiness,
  remediation, merge safety, snapshots, semantic diff, current implementation
  checkpoint, and release acceptance criteria.
- [0.0.4 planned version contract](version-0.0.4.md): the planned read-only
  robomimic/HDF5 preflight, safety boundary, artifact reuse, test matrix, and
  release gates. It is not implemented or released.
- [Reference libraries and differentiation](reference-libraries.md):
  LeRobot, `lerobot-doctor`, `robovet`, and adjacent-library decisions.
- [Canonical manifest](examples/manifest.json) and
  [canonical audit](examples/audit.json): released v1 examples without local
  machine paths.
- [0.0.3 artifact examples](examples/v0.0.3/README.md): generated snapshot,
  diff, readiness, Catalog evidence, repair, and merge contracts.

## Capability boundary

This repository owns local and revision-pinned Hub preflight, media inspection,
deterministic manifests/audits/snapshots/diffs, readiness gates, conservative
copy-on-write metadata repair, official-operation verification, catalog export,
and the score-free Catalog evidence handoff. Catalog scoring, candidate review,
publication, API authentication, asynchronous platform work, retention, and
billing are outside this library. The main OpenBot repository provides an
API-first platform framework; `openbot-sdk` calls APIs that are actually
published. A future opt-in robot/ego-data adapter requires a real platform API
contract and is not current functionality.
