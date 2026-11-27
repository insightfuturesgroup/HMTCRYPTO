"""Require migration source changes whenever the frozen manifest changes."""

from __future__ import annotations

import argparse
import subprocess

MANIFEST = "migrations/schema_manifest.json"
VERSION_PREFIX = "migrations/versions/"


def normalized_base(base: str) -> str:
    if base and set(base) == {"0"}:
        return "HEAD^"
    return base


def changed_paths(base: str, head: str) -> tuple[str, ...]:
    base = normalized_base(base)
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in result.stdout.splitlines() if line)


def manifest_change_is_valid(paths: tuple[str, ...]) -> bool:
    if MANIFEST not in paths:
        return True
    return any(path.startswith(VERSION_PREFIX) and path.endswith(".py") for path in paths)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("head", nargs="?", default="HEAD")
    args = parser.parse_args()
    paths = changed_paths(args.base, args.head)
    if manifest_change_is_valid(paths):
        return 0
    print("schema_manifest.json changed without a migration version change")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
