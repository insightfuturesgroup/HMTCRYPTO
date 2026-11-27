"""Read JUnit without printing payloads, environment values, paths or tracebacks."""

import argparse
import collections
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def safe_class(problem: ET.Element) -> str:
    text = problem.get("message", "").splitlines()
    first = text[0] if text else ""
    match = re.search(r"\b(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception)\b", first)
    candidate = match.group() if match else problem.get("type", "unknown")
    return candidate if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,100}", candidate) else "unknown"


def safe_first_line(first: str, exception: str) -> str:
    # Only this grammar is emitted verbatim; all arbitrary values remain private.
    match = re.search(r"\b\d+ validation errors? for [A-Za-z_]\w*\b", first)
    if match:
        return f"{exception}: {match.group()}"[:160]
    return f"{exception}: [first-line values redacted]"[:160]


def summarize(root: ET.Element, top: int = 15) -> dict[str, object]:
    counts: collections.Counter[str] = collections.Counter()
    phases: collections.Counter[str] = collections.Counter()
    groups: collections.Counter[tuple[str, str, str]] = collections.Counter()
    skips = []
    for case in root.iter("testcase"):
        problems = case.findall("failure") + case.findall("error")
        skipped = case.find("skipped")
        if not problems and skipped is None:
            counts["passed"] += 1
        for problem in problems:
            counts[problem.tag] += 1
            message = problem.get("message", "")
            first = message.splitlines()[0] if message else ""
            groups[(problem.tag, safe_class(problem), first)] += 1
            if problem.tag == "error":
                phase = (
                    "setup"
                    if "setup" in first.lower()
                    else "teardown"
                    if "teardown" in first.lower()
                    else "collection_or_unspecified"
                )
                phases[phase] += 1
        if skipped is not None:
            counts["skipped"] += 1
            # Parametrized values and free-form reason text must never be printed.
            raw_id = case.get("classname", "") + "::" + case.get("name", "").split("[", 1)[0]
            test_id = re.sub(r"[^A-Za-z0-9_.:]", "_", raw_id)[:240]
            reason = skipped.get("message", "")
            category = (
                "expected_scoped_fk"
                if "Patch 1c" in reason
                else "testnet_opt_in"
                if "testnet" in reason.lower()
                else "postgres_gate"
                if "postgres" in reason.lower()
                else "reason_redacted"
            )
            skips.append({"test": test_id, "category": category})
    return {
        "counts": dict(counts),
        "error_phases": dict(phases),
        "top_groups": [
            {
                "group": i,
                "kind": kind,
                "exception": exception,
                "count": count,
                "first_message_line": safe_first_line(first, exception),
            }
            for i, ((kind, exception, first), count) in enumerate(groups.most_common(top), 1)
        ],
        "skips": skips,
        "note": "Groups use exact first lines internally; only redacted first lines are printed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="JUnit XML file or directory")
    args = parser.parse_args()
    try:
        paths = sorted(args.artifact.rglob("*.xml")) if args.artifact.is_dir() else [args.artifact]
        if not paths:
            raise ValueError()
        for number, path in enumerate(paths, 1):
            print(
                json.dumps(
                    {"artifact_number": number, **summarize(ET.parse(path).getroot())}, indent=2
                )
            )
    except Exception:
        print("JUnit summary failed; check the supplied XML path and format.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
