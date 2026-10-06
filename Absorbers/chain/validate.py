"""Fail-fast integrity gate for a chain sweep, in the spirit of harness/validate_results.py.

    python -m chain.validate --results results-chain-gemma3
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chain.policy_v2 import POLICY_V2_SHA256, decide_v2  # noqa: E402
from chain.taskgen import chain_perturbations, generate_chain  # noqa: E402
from harness.runner import _json_sha256  # noqa: E402

REQUIRED = ("schema_version", "request_id", "base_request_id", "variant", "phase", "salt", "factor",
            "backend_label", "model", "temperature", "input_sha256", "policy_sha256", "decision",
            "firing_rule", "parse_error", "call_error", "cache_ids", "audit", "oracle_decision")


def validate(results_dir: Path, *, seed: int = 42, check_cache: bool = True) -> dict:
    rows = [json.loads(l) for l in (results_dir / "decisions.jsonl").read_text().splitlines() if l.strip()]
    problems: list[str] = []
    for i, r in enumerate(rows):
        missing = [k for k in REQUIRED if k not in r]
        if missing:
            problems.append(f"row {i}: missing {missing}")
    if problems:
        raise SystemExit("\n".join(problems[:20]))
    if {r["variant"] for r in rows} != {"C3"}:
        raise SystemExit("variant set is not exactly {C3}")
    if len({(r["backend_label"], r["model"]) for r in rows}) != 1:
        raise SystemExit("more than one backend/model identity in the file")
    bad_policy = sum(r["policy_sha256"] != POLICY_V2_SHA256 for r in rows)
    if bad_policy:
        raise SystemExit(f"{bad_policy} rows carry a policy hash other than v2 in this checkout")

    # grid completeness and input-hash regeneration; phases come from chain_meta.json when present
    meta_p = results_dir / "chain_meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    phases_expected = set(meta.get("phases") or {"canon", "repro", "pert"})
    reqs = {r.request_id: r for r in generate_chain(seed)}
    n_repro = int(meta.get("n_repro") or (Counter(r["phase"] for r in rows)["repro"] // max(1, len({r["base_request_id"] for r in rows}))))
    expected: dict[tuple, str] = {}
    for bid, req in reqs.items():
        if "canon" in phases_expected:
            expected[(bid, "canon", "canon")] = _json_sha256(req.to_json())
        if "repro" in phases_expected:
            for i in range(n_repro):
                expected[(bid, "repro", f"repro{i}")] = _json_sha256(req.to_json())
        if "pert" in phases_expected:
            for factor, variants in chain_perturbations(req).items():
                for j, v in enumerate(variants):
                    expected[(bid, "pert", f"pert:{factor}:{j}")] = _json_sha256(v.to_json())
    seen = {}
    for r in rows:
        k = (r["base_request_id"], r["phase"], r["salt"])
        if k in seen:
            raise SystemExit(f"duplicate logical row {k}")
        seen[k] = r["input_sha256"]
    present_bids = {r["base_request_id"] for r in rows}
    expected_here = {k: v for k, v in expected.items() if k[0] in present_bids}
    missing = sorted(set(expected_here) - set(seen))
    extra = sorted(set(seen) - set(expected_here))
    if missing or extra:
        raise SystemExit(f"grid mismatch: {len(missing)} missing (e.g. {missing[:3]}), {len(extra)} extra (e.g. {extra[:3]})")
    sha_bad = [k for k, v in seen.items() if expected_here[k] != v]
    if sha_bad:
        raise SystemExit(f"{len(sha_bad)} rows whose input_sha256 does not regenerate, e.g. {sha_bad[:3]}")

    # temperatures
    for r in rows:
        want = 0.7 if r["phase"] == "repro" else 0.0
        if float(r["temperature"]) != want:
            raise SystemExit(f"row {r['base_request_id']}/{r['salt']}: temperature {r['temperature']} != {want}")

    # cache existence and error-freeness
    cache_missing = cache_err = 0
    if check_cache:
        for r in rows:
            for cid in r["cache_ids"]:
                p = results_dir / "raw" / f"{cid}.json"
                if not p.exists() or p.stat().st_size == 0:
                    cache_missing += 1
                    continue
                if json.loads(p.read_text()).get("error"):
                    cache_err += 1
        if cache_missing or cache_err:
            raise SystemExit(f"active cache: {cache_missing} missing/empty, {cache_err} with error")

    # the decision must be the deterministic function of the recorded record
    unexplained = 0
    for r in rows:
        rec = (r.get("audit") or {}).get("structured_record")
        if rec and decide_v2(rec).decision.value != r["decision"]:
            unexplained += 1
    if unexplained:
        raise SystemExit(f"{unexplained} rows whose decision != decide_v2(audit.structured_record)")

    phases = Counter(r["phase"] for r in rows)
    return {"n_rows": len(rows), "n_requests": len(present_bids), "n_repro": n_repro, "phases": dict(phases),
            "policy_sha256": POLICY_V2_SHA256, "cache_checked": check_cache,
            "firing_rules": dict(Counter(r["firing_rule"] for r in rows if r["phase"] == "canon"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-check-cache", action="store_true")
    a = ap.parse_args()
    d = Path(a.results)
    if not d.is_absolute():
        d = ROOT / d
    info = validate(d, seed=a.seed, check_cache=not a.no_check_cache)
    print(json.dumps(info, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
