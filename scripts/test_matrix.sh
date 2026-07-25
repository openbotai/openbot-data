#!/usr/bin/env bash
set -euo pipefail

for version in 3.9 3.10 3.11 3.12; do
  echo "Testing openbot-data on Python ${version}"
  uv run --isolated --python "${version}" --extra dev pytest -q
done
