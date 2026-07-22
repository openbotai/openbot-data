"""Validate the package version source, metadata, changelog, and optional tag."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:(?:a|b|rc)(?:0|[1-9]\d*))?(?:\.post(?:0|[1-9]\d*))?"
    r"(?:\.dev(?:0|[1-9]\d*))?$"
)


def fail(message: str) -> None:
    print(f"version check failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", help="Git tag to compare with v<version>")
    args = parser.parse_args()

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(version):
        fail(f"VERSION is not a supported PEP 440 release: {version!r}")

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project_match = re.search(r"(?ms)^\[project\]\s*(.*?)(?=^\[|\Z)", pyproject)
    if project_match is None:
        fail("pyproject.toml has no [project] section")
    project = project_match.group(1)
    if not re.search(r'^dynamic\s*=\s*\[\s*"version"\s*\]\s*$', project, re.MULTILINE):
        fail("pyproject.toml must declare only a dynamic project version")
    if re.search(r"^version\s*=", project, re.MULTILINE):
        fail("pyproject.toml must not declare a static project version")

    hatch_match = re.search(
        r"(?ms)^\[tool\.hatch\.version\]\s*(.*?)(?=^\[|\Z)", pyproject
    )
    if hatch_match is None or not re.search(
        r'^path\s*=\s*"VERSION"\s*$', hatch_match.group(1), re.MULTILINE
    ):
        fail("Hatch version source must be VERSION")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {version}" not in changelog:
        fail(f"CHANGELOG.md has no section for {version}")

    if args.tag and args.tag != f"v{version}":
        fail(f"tag {args.tag!r} does not match v{version}")

    print(f"version {version} is consistent")


if __name__ == "__main__":
    main()
