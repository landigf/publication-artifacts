#!/usr/bin/env python3
"""One-time full-key anonymization of supplied canonical CSVs.

Offline reproduction does NOT run this tool. The random HMAC secret and URL
mapping are discarded. Published CSV bytes, not a fresh anonymization, define
the evaluated snapshot. No original keys or session labels are printed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripted-source", required=True, type=Path)
    parser.add_argument("--llm-source", required=True, type=Path)
    args = parser.parse_args()
    secret = secrets.token_bytes(32)
    raw_to_opaque: dict[str, str] = {}
    opaque_to_raw: dict[str, str] = {}
    result = {"schema_version": 1, "method": "HMAC-SHA256 of complete cache keys with a shared ephemeral key; session labels enumerated per workload; secret and mappings discarded", "files": []}
    for workload, source, filename in [("scripted", args.scripted_source, "full_400_sessions.csv"), ("llm", args.llm_source, "llm_full_901.csv")]:
        destination = ROOT / "data/traces" / filename
        labels: dict[str, str] = {}
        count = positive = byte_count = 0
        source_nonkey = hashlib.sha256()
        export_nonkey = hashlib.sha256()
        with source.open(newline="") as handle, destination.open("w", newline="") as output:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            if not fields or fields[:3] != ["timestamp_us", "cache_key", "object_size_bytes"]:
                raise ValueError("Unexpected canonical CSV columns")
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for row in reader:
                count += 1
                size = int(row["object_size_bytes"])
                if size < 0:
                    raise ValueError("Negative object size")
                positive += size > 0
                byte_count += size
                key = row["cache_key"]
                token = raw_to_opaque.get(key)
                if token is None:
                    token = "btkey-" + hmac.new(secret, key.encode("utf-8"), hashlib.sha256).hexdigest()
                    if token in opaque_to_raw and opaque_to_raw[token] != key:
                        raise ValueError("Cache-key collision")
                    raw_to_opaque[key] = token
                    opaque_to_raw[token] = key
                retained = [row[c] for c in fields if c not in {"cache_key", "session_id"}]
                source_nonkey.update(json.dumps(retained, separators=(",", ":")).encode() + b"\n")
                row["cache_key"] = token
                label = row.get("session_id", "")
                if label not in labels:
                    labels[label] = f"btlabel-{workload}-{len(labels) + 1:04d}"
                row["session_id"] = labels[label]
                writer.writerow(row)
                retained = [row[c] for c in fields if c not in {"cache_key", "session_id"}]
                export_nonkey.update(json.dumps(retained, separators=(",", ":")).encode() + b"\n")
        assert source_nonkey.digest() == export_nonkey.digest()
        result["files"].append({"workload": workload, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "released_path": str(destination.relative_to(ROOT)), "released_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "request_rows": count, "positive_size_rows": positive, "object_size_bytes": byte_count, "unique_legacy_session_labels": len(labels), "retained_nonkey_sequence_sha256": source_nonkey.hexdigest(), "retained_nonkey_fields_identical": True})
    result["distinct_source_keys"] = len(raw_to_opaque)
    result["distinct_released_keys"] = len(opaque_to_raw)
    result["cache_key_equality_preserved_across_both_inputs"] = len(raw_to_opaque) == len(opaque_to_raw)
    (ROOT / "reports/input-transformation.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Exported two CSVs with {sum(x['request_rows'] for x in result['files']):,} rows; {len(raw_to_opaque):,} bijectively mapped cache keys.")


if __name__ == "__main__":
    main()
