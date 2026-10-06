#!/usr/bin/env python3
"""Validate the released artifact and optionally reproduce its complete results."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

from generate_publication import ROOT, audit_sizes, derive, generate, load_csv
from sanitize_release import credential_fields, test_negative_fixture


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def integrity(root: Path = ROOT) -> dict:
    snapshot = json.loads((root / "artifact_snapshot.json").read_text())
    require(snapshot["files"], "Empty manifest")
    seen = set()
    for entry in snapshot["files"]:
        relative = Path(entry["path"])
        require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe manifest path")
        require(entry["path"] not in seen, "Duplicate manifest path")
        seen.add(entry["path"])
        path = root / relative
        require(path.is_file() and not path.is_symlink(), "Missing or symbolic-linked artifact file")
        require(path.stat().st_size == entry["bytes"] and sha(path) == entry["sha256"], f"Hash mismatch: {entry['path']}")
    census = load_csv(root / "data/provenance/session_census.csv")
    live = load_csv(root / "data/provenance/reached_content_sessions.csv")
    require(len(census) == len({row["artifact_session_id"] for row in census}), "Duplicate census identifier")
    require(len(live) == len({row["artifact_session_id"] for row in live}), "Duplicate liveness identifier")
    for row in census + live:
        require(row["requests"] >= 0 and row["positive_traffic"] == (row["requests"] > 0), "Invalid request/nonempty accounting")
        require(not any("/" in str(value) or "\\" in str(value) or "http" in str(value).lower() for value in row.values()), "Unexpected URL/path in numeric provenance")
    for row in live:
        require(row["distinct_hostname_count"] >= 0 and row["status_200_nonroot_count"] >= 0, "Negative liveness count")
        require(row["reached_content"] == (row["distinct_hostname_count"] >= 3 and row["status_200_nonroot_count"] >= 5), "Liveness predicate disagreement")
        require(row["status_200_nonroot_count"] <= row["requests"], "Liveness count exceeds requests")
    for census_cohort, workload, live_cohort in [("paper-corpus", "scripted", "paper-scripted"), ("paper-corpus", "llm", "paper-llm"), ("table4-reference", "llm", "legacy-llm")]:
        signature = lambda row: (row["model"], row["region"], row["task"], row["requests"], row["positive_traffic"])
        a = Counter(signature(row) for row in census if row["cohort"] == census_cohort and row["workload"] == workload)
        b = Counter(signature(row) for row in live if row["cohort"] == live_cohort)
        require(a == b, "Census/liveness cohort projections disagree")
    metadata = json.loads((root / "data/provenance/collection-metadata-audit.json").read_text())
    for source in metadata["sources"]:
        count = sum(row["cohort"] == source["cohort"] and row["source"] == source["source"] for row in census)
        require(count == source["attempts"] == sum(config["attempts"] for config in source["configurations"]), "Collection-metadata denominator mismatch")
    projection = json.loads((root / "data/provenance/scripted-replay-projection-audit.json").read_text())
    require(projection["positive_size_multiset_equals_released_replay"], "Scripted projection source audit missing")
    for source in projection["sources"]:
        items = [row for row in census if row["source"] == source["source"] and row["cohort"] == "paper-corpus"]
        require(len(items) == source["sessions"] and sum(row["requests"] for row in items) == source["requests"] and sum(row["bytes"] for row in items) == source["object_size_bytes"] and source["summary_agrees_with_complete_json"], "Scripted complete-source/census audit disagreement")
    stats = derive(root)
    require(stats == json.loads((root / "reports/publication-statistics.json").read_text()), "Derived statistics are stale")
    require(audit_sizes(root) == json.loads((root / "reports/replay-size-audit.json").read_text()), "Replay-size consistency audit is stale")
    expected = {"scripted": (82455, 82455, 400), "llm": (357782, 236073, 100)}
    trace_checks = []
    replay = json.loads((root / "reports/public-cache-replay.json").read_text())
    for workload, filename in [("scripted", "full_400_sessions.csv"), ("llm", "llm_full_901.csv")]:
        path = root / "data/traces" / filename
        count = positive = byte_count = 0
        labels = set()
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            require(reader.fieldnames in [["timestamp_us", "cache_key", "object_size_bytes", "session_id"], ["timestamp_us", "cache_key", "object_size_bytes", "session_id", "agent_type"]], "Unexpected replay columns")
            for row in reader:
                count += 1
                require(bool(re.fullmatch(r"btkey-[0-9a-f]{64}", row["cache_key"])), "Nonopaque replay cache key")
                require(bool(re.fullmatch(r"btlabel-(scripted|llm)-[0-9]{4}", row["session_id"])), "Nonopaque legacy session label")
                require(bool(re.fullmatch(r"[0-9]+", row["timestamp_us"])), "Invalid replay timestamp")
                size = int(row["object_size_bytes"])
                require(size >= 0, "Negative replay size")
                positive += size > 0
                byte_count += size
                labels.add(row["session_id"])
        require((count, positive, len(labels)) == expected[workload], "Replay request/session-label accounting differs from pinned snapshot")
        entry = next(row for row in replay["workloads"] if row["workload"] == workload)
        require(entry["sha256"] == sha(path) and entry["file_rows"] == count and entry["effective_request_denominator"] == positive and entry["effective_byte_denominator"] == byte_count, "Replay metadata disagrees with released CSV")
        require(byte_count == stats["paper_corpus"][workload]["bytes"], "Census/replay byte totals disagree")
        if workload == "scripted":
            with path.open(newline="") as handle:
                sizes = Counter(int(row["object_size_bytes"]) for row in csv.DictReader(handle))
            require(hashlib.sha256(json.dumps(sorted(sizes.items()),separators=(",",":")).encode()).hexdigest() == projection["positive_size_multiset_sha256"], "Scripted source size multiset differs from replay")
        if workload == "llm":
            require(count == stats["paper_corpus"]["llm"]["requests"], "LLM census/replay request totals disagree")
        trace_checks.append({"workload":workload,"rows":count,"effective_requests":positive,"effective_bytes":byte_count})
    combinations = {(r["workload"],r["cache_mib"],r["policy"]) for r in replay["results"]}
    wanted = {(w,s,p) for w in ["scripted","llm"] for s in [1,5,10,25,50] for p in ["LRU","LFU","ARC","S3-FIFO","W-TinyLFU","GDSF"]}
    require(len(replay["results"]) == 60 and combinations == wanted, "Incomplete or duplicate policy matrix")
    for row in replay["results"]:
        require(all(math.isfinite(row[metric]) and 0 <= row[metric] <= 1 for metric in ["request_hit_ratio","byte_hit_ratio"]), "Invalid cache ratio")
    transform = json.loads((root / "reports/input-transformation.json").read_text())
    require(transform["cache_key_equality_preserved_across_both_inputs"] and transform["distinct_source_keys"] == transform["distinct_released_keys"], "Transformation bijection evidence missing")
    for entry in transform["files"]:
        require(entry["released_sha256"] == sha(root / entry["released_path"]) and entry["retained_nonkey_fields_identical"], "Transformation evidence does not identify released bytes")
    equivalent = json.loads((root / "reports/opaque-replay-equivalence.json").read_text())
    require(equivalent["all_results_equal"] and len(equivalent["comparisons"]) == 60, "Source/opaque replay verification missing")
    for entry in equivalent["comparisons"]:
        require(entry["request_hit_delta"] == 0 and entry["byte_hit_delta"] == 0, "Source/opaque replay result disagreement")
    credential_count = 0
    for folder in ["data", "reports"]:
        for path in (root / folder).rglob("*.json"):
            if "reproduction" not in path.parts:
                credential_count += credential_fields(json.loads(path.read_text()))
    require(credential_count == 0, "Credential fields detected in numeric artifact")
    test_negative_fixture()
    return {"hashed_files":len(snapshot["files"]),"census_rows":len(census),"liveness_rows":len(live),"credential_fields":credential_count,"trace_accounting":trace_checks,"checks":["explicit file hashes","unique census and liveness IDs","opaque keys/session labels","nonnegative sizes and positive-size denominators","census/liveness projection agreement","collection metadata denominators","derived statistics","complete six-policy/five-capacity matrix","source/export equality evidence","credential-removal negative fixture"]}


def simulator_fixture() -> None:
    from libcachesim import LRU, Request, ReaderInitParam, TraceReader, TraceType
    from replay_public import POLICIES
    with tempfile.TemporaryDirectory(prefix="browsetrace-fixture-") as temporary:
        path = Path(temporary) / "tiny.csv"
        params = ReaderInitParam(has_header=True, has_header_set=True, delimiter=",", obj_id_is_num=False, obj_id_is_num_set=True)
        params.time_field, params.obj_id_field, params.obj_size_field = 1,2,3
        path.write_text("timestamp_us,cache_key,object_size_bytes\n1,A,60\n2,B,60\n3,A,60\n")
        reader = TraceReader(str(path),trace_type=TraceType.CSV_TRACE,reader_init_params=params)
        miss,byte_miss = LRU(100,consider_obj_metadata=False).process_trace(reader)
        require(miss == 1 and byte_miss == 1, "Bounded-capacity LRU fixture failed")
        path.write_text("timestamp_us,cache_key,object_size_bytes\n" + "".join(f"{index + 1},K{index % 10},60\n" for index in range(20)))
        for policy,cls in POLICIES:
            reader = TraceReader(str(path),trace_type=TraceType.CSV_TRACE,reader_init_params=params)
            miss,byte_miss = cls(100,consider_obj_metadata=False).process_trace(reader)
            require(1-miss <= .05 + 1e-12 and 1-byte_miss <= .05 + 1e-12, f"Bounded-capacity repeated-scan fixture failed for {policy}")
        path.write_text("timestamp_us,cache_key,object_size_bytes\n1,A,0\n2,A,60\n3,A,60\n")
        reader = TraceReader(str(path),trace_type=TraceType.CSV_TRACE,reader_init_params=params)
        miss,byte_miss = LRU(100,consider_obj_metadata=False).process_trace(reader)
        require(miss == .5 and byte_miss == .5, "Zero-size exclusion denominator fixture failed")
        for policy,cls in POLICIES:
            cache = cls(1000,consider_obj_metadata=False)
            hits, occupied = [], []
            for index,(key,size) in enumerate([(1,1),(2,1),(1,9),(3,2),(1,1)]):
                request = Request()
                request.obj_id,request.obj_size,request.clock_time = key,size,index+1
                hits.append(cache.get(request))
                occupied.append(cache.get_occupied_byte())
            require(hits == [False,False,True,False,True] and occupied == [1,2,2,4,4], f"Resident-size-on-hit fixture changed for {policy}")
            path.write_text("timestamp_us,cache_key,object_size_bytes\n1,A,1\n2,B,1\n3,A,9\n")
            reader = TraceReader(str(path),trace_type=TraceType.CSV_TRACE,reader_init_params=params)
            miss,byte_miss = cls(1000,consider_obj_metadata=False).process_trace(reader)
            require(abs((1-miss)-1/3) <= 1e-12 and abs((1-byte_miss)-9/11) <= 1e-12, f"Current-request byte-weight fixture changed for {policy}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recompute",action="store_true")
    parser.add_argument("--output-dir",type=Path,default=ROOT / "reports/reproduction")
    args = parser.parse_args()
    checks = integrity()
    if args.recompute:
        from replay_public import generate as replay_generate
        simulator_fixture()
        fresh = replay_generate()
        committed = json.loads((ROOT / "reports/public-cache-replay.json").read_text())
        require(fresh["workloads"] == committed["workloads"], "Recomputed replay denominators/hashes differ")
        require(fresh["configuration"] == committed["configuration"], "Recomputed policy configuration differs")
        for a,b in zip(fresh["results"],committed["results"]):
            require(all(abs(a[m]-b[m]) <= 1e-12 for m in ["request_hit_ratio","byte_hit_ratio"]), "Recomputed cache result differs")
            require(all(a[m] == b[m] for m in ["workload","cache_mib","policy"]), "Recomputed cache matrix order differs")
        output = args.output_dir.resolve()
        output.mkdir(parents=True,exist_ok=True)
        (output / "public-cache-replay.json").write_text(json.dumps(fresh,indent=2)+"\n")
        generate(output_dir=output,replay=fresh)
        for name in ["publication-statistics.json","replay-size-audit.json","numbers.tex","cohort-table.tex","model-table.tex","amplification-table.tex","cache-5mib-table.tex"]:
            require((output / name).read_bytes() == (ROOT / "reports" / name).read_bytes(), f"Generated statistics/table drift: {name}")
        for name in ["cache-policy-tradeoff","model-request-counts","task-amplification"]:
            for suffix in ["pdf","png"]:
                require(sha(output / "figures" / f"{name}.{suffix}") == sha(ROOT / "figures" / f"{name}.{suffix}"), f"Generated figure drift: {name}.{suffix}; use locked plotting dependencies")
        checks["checks"].extend(["bounded-capacity six-policy repeated-scan fixture","bounded-capacity LRU fixture","zero-size exclusion fixture","six-policy resident-size-on-hit fixture","six-policy current-request byte-weight fixture","all 60 cache replays","all generated macros/tables/statistics match","all six PDF/PNG figure hashes match"])
        checks["environment"] = fresh["environment"]
        checks["status"] = "passed"
        (output / "verification.json").write_text(json.dumps(checks,indent=2)+"\n")
    print(f"Verified {checks['hashed_files']} hashed files, {checks['census_rows']} census rows and {checks['liveness_rows']} liveness rows" + ("; all 60 replays and generated tables/figures match." if args.recompute else "."))


if __name__ == "__main__":
    try:
        main()
    except (ValueError,RuntimeError) as error:
        print(f"Verification failed: {error}",file=sys.stderr)
        raise SystemExit(1)
