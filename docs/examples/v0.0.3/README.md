# OpenBot Data 0.0.3 artifact examples

These are generated fixture artifacts for the released `0.0.3` contracts,
not results from a production customer dataset. They remain applicable to the
current source checkout (documentation reviewed 2026-10-02). The source-only
[Review workbench](../../review.md) serves its own local view and does not
generate or import these example artifacts.

These deterministic examples are regenerated with:

```bash
python scripts/generate_v003_examples.py
```

Run from the repository root with the optional Parquet dependency installed:

```bash
python -m pip install -e '.[lerobot]'
```

| File | Schema key | Expected result |
|---|---|---|
| `audit.json` | `audit` | completed full local audit |
| `snapshot.json` | `snapshot` | portable full SHA-256 identity |
| `diff.json` | `diff` | unchanged comparison |
| `readiness.json` | `readiness` | `READY` for `lerobot-core` |
| `catalog-evidence.json` | `catalog_evidence` | score-free Catalog handoff |
| `repair-plan.json` | `repair_plan` | one derived total repair |
| `repair-receipt.json` | `repair_receipt` | structurally verified copy, official loader unavailable |
| `merge-plan.json` | `merge_plan` | directly compatible inputs |
| `merge-receipt.json` | `merge_receipt` | complete verification evidence with deliberately absent external operation record/loader |

The unverified receipts demonstrate that missing external evidence is preserved
as a canonical negative result. The pinned LeRobot conformance gate separately
requires verified repair and merge receipts.
