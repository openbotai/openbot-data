# OpenBot Data documentation

OpenBot Data is a local, open-source Python library. It inspects robot-video
directories and local LeRobot v2.1/v3 repositories before training or upload. It
does not call the Hosted OpenBot API.

## Start here

- [Getting started](getting-started.md): installation, CLI, Python workflow, and
  the runnable demo.
- [API reference](api-reference.md): public Python functions, CLI commands,
  parameters, return values, and failure behavior.
- [Audit finding codes](audit-findings.md): stable machine-readable validation
  codes and severities.
- [0.0.2 release contract](version-0.0.2.md): scope and acceptance criteria.
- [Canonical manifest](examples/manifest.json) and
  [canonical audit](examples/audit.json): complete JSON examples without local
  machine paths.

## Capability boundary

This repository owns local discovery, media inspection, preview generation,
deterministic manifests, audits, and catalog export. Uploads, API keys,
asynchronous processing, review, hosted artifacts, retention, and billing belong
to the main OpenBot service and are called through `openbot-sdk`.
