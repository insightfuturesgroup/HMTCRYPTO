"""Reject fingerprinted environment identifiers without printing their values."""

import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from collections import Counter
from pathlib import Path

PATTERNS = {
    "project-ref": re.compile(rb"(?=([a-z]{20}))"),
    "instance-id": re.compile(rb"(?=(i-[0-9a-f]{17}))"),
    "ipv4": re.compile(rb"(?=([0-9]{1,3}(?:\.[0-9]{1,3}){3}))"),
}
FINGERPRINTS = Path(__file__).with_name("public_identifier_fingerprints.json")


def digest(value: bytes, salt: str) -> str:
    return hashlib.sha256(bytes.fromhex(salt) + b"\0" + value).hexdigest()


def scan_bytes(content: bytes, records: list[dict[str, str]]) -> list[tuple[str, int]]:
    found = set()
    for kind, pattern in PATTERNS.items():
        relevant = [record for record in records if record["kind"] == kind]
        if not relevant:
            continue
        for match in pattern.finditer(content):
            value = match.group(1)
            if any(
                hmac.compare_digest(digest(value, record["salt"]), record["sha256"])
                for record in relevant
            ):
                found.add((kind, content.count(b"\n", 0, match.start(1)) + 1))
    return sorted(found)


def scan_paths(
    root: Path, paths: list[str], records: list[dict[str, str]]
) -> list[tuple[str, str, int]]:
    found = []
    for relative in paths:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid tracked path")
        if path.name == ".env" or path.suffix in {".env", ".crt", ".pem", ".key"}:
            raise ValueError("Tracked operator configuration or CA/key file must be removed")
        target = root / path
        # Inspect a tracked symlink itself, never follow it to operator files.
        content = os.fsencode(os.readlink(target)) if target.is_symlink() else target.read_bytes()
        for kind, line in scan_bytes(content, records):
            safe_path = relative
            if scan_bytes(os.fsencode(relative), records):
                safe_path = "[identifier-bearing path redacted]"
            found.append((safe_path, kind, line))
    return found


def load_records(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text())
    if data.get("version") != 1 or not data.get("fingerprints"):
        raise ValueError("Invalid fingerprint manifest")
    records = data["fingerprints"]
    if Counter(record.get("kind") for record in records) != {
        "instance-id": 1,
        "ipv4": 2,
        "project-ref": 2,
    }:
        raise ValueError("Incomplete fingerprint manifest")
    for record in records:
        if set(record) != {"kind", "salt", "sha256"} or record["kind"] not in PATTERNS:
            raise ValueError("Invalid fingerprint record")
        if not all(re.fullmatch(r"[0-9a-f]{64}", record[key]) for key in ("salt", "sha256")):
            raise ValueError("Invalid fingerprint encoding")
    return records


def regenerate() -> int:
    if not sys.stdin.isatty():
        raise ValueError("Regeneration requires an interactive terminal")
    print("This replaces the fingerprint manifest with five salted hashes. No values are saved.")
    if input("Type REPLACE HASHES to continue: ") != "REPLACE HASHES":
        return 1
    records = []
    for kind, label in (
        ("instance-id", "Instance identifier"),
        ("ipv4", "First Elastic IP"),
        ("ipv4", "Second Elastic IP"),
        ("project-ref", "First project reference"),
        ("project-ref", "Second project reference"),
    ):
        value = getpass.getpass(label + " (hidden): ").strip().encode("ascii")
        if not any(match.group(1) == value for match in PATTERNS[kind].finditer(value)):
            raise ValueError("Invalid identifier format")
        salt = secrets.token_hex(32)
        records.append({"kind": kind, "salt": salt, "sha256": digest(value, salt)})
    FINGERPRINTS.write_text(json.dumps({"version": 1, "fingerprints": records}, indent=2) + "\n")
    print("Fingerprint manifest replaced. Review the hash-only diff and rerun the check.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()
    try:
        if args.regenerate:
            return regenerate()
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
        )
        paths = [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]
        if not paths:
            raise ValueError("No tracked files found")
        found = scan_paths(root, paths, load_records(FINGERPRINTS))
        for path, kind, line in found:
            print(f"{path}:{line}: prohibited {kind}; value withheld")
        print(f"Tracked-file identifier check: {len(found)} matches in {len(paths)} files.")
        return 1 if found else 0
    except Exception:
        print(
            "Identifier check failed; verify Git, tracked file safety and manifest format.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
