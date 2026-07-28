# OpenBot Data 0.0.3 artifact examples

These deterministic examples are regenerated with:

```bash
python scripts/generate_v003_examples.py
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
