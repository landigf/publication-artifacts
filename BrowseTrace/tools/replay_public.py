#!/usr/bin/env python3
"""Replay only released inputs, without collection, credentials, or network."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

from libcachesim import ARC, GDSF, LFU, LRU, S3FIFO, WTinyLFU, ReaderInitParam, TraceReader, TraceType

ROOT = Path(__file__).resolve().parents[1]
POLICIES = [("LRU", LRU), ("LFU", LFU), ("ARC", ARC), ("S3-FIFO", S3FIFO), ("W-TinyLFU", WTinyLFU), ("GDSF", GDSF)]
SIZES = [1, 5, 10, 25, 50]
INPUTS = [("scripted", "data/traces/full_400_sessions.csv"), ("llm", "data/traces/llm_full_901.csv")]


def replay(path: Path) -> list[dict]:
    params = ReaderInitParam(has_header=True, has_header_set=True, delimiter=",", obj_id_is_num=False, obj_id_is_num_set=True)
    params.time_field, params.obj_id_field, params.obj_size_field = 1, 2, 3
    output = []
    for size in SIZES:
        for label, cls in POLICIES:
            reader = TraceReader(str(path), trace_type=TraceType.CSV_TRACE, reader_init_params=params)
            miss, byte_miss = cls(size * 1024 * 1024, consider_obj_metadata=False).process_trace(reader)
            output.append({"cache_mib": size, "policy": label, "request_hit_ratio": 1 - miss, "byte_hit_ratio": 1 - byte_miss})
    return output


def generate(root: Path = ROOT) -> dict:
    version = importlib.metadata.version("libcachesim")
    if version != "0.3.3.post4":
        raise RuntimeError(f"Use pinned libcachesim==0.3.3.post4, found {version}")
    result = {"schema_version": 2, "environment": {"python": platform.python_version(), "libcachesim": version, "platform": platform.system(), "machine": platform.machine()}, "configuration": {"consider_obj_metadata": False, "default_ttl": 25920000, "hashpower": 24, "origin_http_headers_available": False}, "method": "Cold cache for each policy/size; published CSV row order; default policy parameters except explicit consider_obj_metadata=False; 1 MiB = 1048576 bytes. libCacheSim ignores zero-size rows: request-hit denominator counts positive-size rows, byte-hit denominator sums their object_size_bytes. No HTTP eligibility/freshness semantics.", "workloads": [], "results": []}
    for workload, relative in INPUTS:
        path = root / relative
        labels = set()
        rows = positive = byte_count = 0
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                rows += 1
                size = int(row["object_size_bytes"])
                positive += size > 0
                byte_count += size
                labels.add(row["session_id"])
        result["workloads"].append({"workload": workload, "path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "file_rows": rows, "effective_request_denominator": positive, "ignored_zero_size_rows": rows - positive, "effective_byte_denominator": byte_count, "distinct_legacy_session_labels": len(labels)})
        result["results"].extend({"workload": workload, **row} for row in replay(path))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/public-cache-replay.json")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(generate(), indent=2) + "\n")
    print("Recomputed 60 policy/capacity/workload results from released bytes.")


if __name__ == "__main__":
    main()
