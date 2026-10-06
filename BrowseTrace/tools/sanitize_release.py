#!/usr/bin/env python3
"""Remove credential header fields from JSON exports before any private review.

This utility alone does not make full HTTP traces suitable for publication:
URLs, bodies and arbitrary metadata may remain sensitive. The publication
artifact uses aggregate provenance and entirely opaque replay keys instead.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collection"))
from privacy import CREDENTIAL_HEADERS, safe_headers


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(item) for key, item in value.items() if key.lower() not in CREDENTIAL_HEADERS}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def credential_fields(value) -> int:
    if isinstance(value, dict):
        return sum(key.lower() in CREDENTIAL_HEADERS for key in value) + sum(credential_fields(item) for item in value.values())
    if isinstance(value, list):
        return sum(credential_fields(item) for item in value)
    return 0


def test_negative_fixture() -> None:
    fixture = {"request_headers": {name.upper(): "synthetic-not-a-secret" for name in CREDENTIAL_HEADERS}, "response_headers": {"Set-Cookie": "synthetic-not-a-secret", "Content-Type": "text/plain"}, "nested": [{"x-api-key": "synthetic-not-a-secret"}], "requests": 1}
    assert credential_fields(fixture) == len(CREDENTIAL_HEADERS) + 2
    cleaned = sanitize(fixture)
    assert credential_fields(cleaned) == 0
    assert cleaned["response_headers"]["Content-Type"] == "text/plain"
    assert cleaned["requests"] == 1
    assert safe_headers(fixture["request_headers"]) == {}
    assert safe_headers(fixture["response_headers"]) == {"Content-Type": "text/plain"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--check", action="store_true", help="Report credential field counts only; do not mutate")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        test_negative_fixture()
        print("Credential header negative fixture passed.")
    failures = 0
    for path in args.paths:
        data = json.loads(path.read_text())
        count = credential_fields(data)
        failures += count
        if not args.check:
            path.write_text(json.dumps(sanitize(data), indent=2) + "\n")
        print(f"{path.name}: {count} credential fields {'found' if args.check else 'removed'}")
    if args.check and failures:
        raise SystemExit(1)
