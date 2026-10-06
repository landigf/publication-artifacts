"""Sweep driver for the chain variant C3. Mirrors harness/runner.py in every provenance field.

    python -m chain.runner --backend ollama:gemma3:4b --out results-chain-gemma3
    python -m chain.runner --backend ollama:gemma3:4b --out results-chain-gemma3 --from-cache
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.llm_client import config_for  # noqa: E402
from chain import agent as chain_agent  # noqa: E402
from chain.policy_v2 import POLICY_V2_SHA256, oracle_v2  # noqa: E402
from chain.taskgen import ChainRequest, chain_perturbations, generate_chain  # noqa: E402
from harness.runner import T_DET, T_REPRO, _json_sha256  # noqa: E402

VARIANT = chain_agent.VARIANT


def build_decision_row(phase, salt, factor, req: ChainRequest, res, cfg, seed):
    orc = oracle_v2(req)
    audit = dict(res.audit or res.raw or {})
    return {
        "schema_version": 2,
        "generator_seed": seed,
        "request_id": req.request_id,
        "base_request_id": req.request_id.split("|", 1)[0],
        "kind": req.kind,
        "planted_rule": req.planted_rule,
        "variant": VARIANT,
        "phase": phase,
        "salt": salt,
        "factor": factor,
        "backend_label": cfg.label,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "input_sha256": _json_sha256(req.to_json()),
        "policy_sha256": POLICY_V2_SHA256,
        "policy_version": "v2",
        "decision": res.decision.value,
        "cited_factors": res.cited_factors,
        "raw_cited_factors": res.raw_cited_factors,
        "unknown_citations": res.unknown_citations,
        "explanation": res.explanation,
        "firing_rule": res.firing_rule,
        "firing_rule_factors": res.firing_rule_factors,
        "gating_factors": res.gating_factors,
        "parse_error": res.parse_error,
        "decision_parse_error": res.decision_parse_error,
        "explanation_parse_error": res.explanation_parse_error,
        "call_error": res.call_error,
        "cache_ids": list(audit.get("cache_ids") or []),
        "call_errors": list(audit.get("call_errors") or []),
        "audit": audit,
        "llm_calls": res.llm_calls,
        "tokens_in": res.tokens_in,
        "tokens_out": res.tokens_out,
        "latency_ms": res.latency_ms,
        "oracle_decision": orc.decision.value,
        "oracle_rule": orc.firing_rule,
        "oracle_firing_rule_factors": orc.firing_rule_factors,
        "oracle_gating": orc.gating_factors,
        "planted_justification_adequate": req.justification_adequate,
        "planted_justification_category": req.justification_category,
    }


def build_tasks(reqs, n_repro, phases):
    for req in reqs:
        if "canon" in phases:
            yield ("canon", "canon", None, req, True)
        if "repro" in phases:
            for i in range(n_repro):
                yield ("repro", f"repro{i}", None, req, False)
        if "pert" in phases:
            for factor, variants in chain_perturbations(req).items():
                for j, preq in enumerate(variants):
                    yield ("pert", f"pert:{factor}:{j}", factor, preq, True)


def main():
    ap = argparse.ArgumentParser(description="Chain (C3) sweep runner")
    ap.add_argument("--backend", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-repro", type=int, default=10)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--phases", default="canon,repro,pert")
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    cache_dir = out_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    phases = {p.strip() for p in args.phases.split(",") if p.strip()}
    if args.n_repro < 2:
        ap.error("--n-repro must be at least 2")

    reqs = generate_chain(args.seed)
    if args.limit:
        reqs = reqs[: args.limit]
    cfg_det = config_for(args.backend, temperature=T_DET, offline=args.from_cache)
    cfg_repro = config_for(args.backend, temperature=T_REPRO, offline=args.from_cache)
    if not args.from_cache and not args.backend.startswith("ollama:") and not cfg_det.api_key:
        print(f"ERROR: no API key for backend '{args.backend}'.", file=sys.stderr)
        sys.exit(2)

    tasks = list(build_tasks(reqs, args.n_repro, phases))
    keys = [(t[0], t[1], t[2], t[3].request_id) for t in tasks]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate logical tasks")
    total = len(tasks)
    print(f"chain C3 backend={args.backend} model={cfg_det.model} requests={len(reqs)} phases={sorted(phases)} "
          f"tasks={total} workers={args.workers} from_cache={args.from_cache}")

    rows, errors = [], []
    done = parse_errors = call_error_rows = 0
    lock = threading.Lock()

    def work(task):
        phase, salt, factor, req, use_det = task
        cfg = cfg_det if use_det else cfg_repro
        res = chain_agent.run(req, cfg, salt, cache_dir, offline=args.from_cache)
        return build_decision_row(phase, salt, factor, req, res, cfg, args.seed)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, t): t for t in tasks}
        for fut in as_completed(futs):
            try:
                row = fut.result()
            except Exception as e:  # noqa: BLE001
                t = futs[fut]
                errors.append((t[0], t[1], t[3].request_id, f"{type(e).__name__}: {e}"))
                continue
            with lock:
                rows.append(row)
                done += 1
                parse_errors += bool(row["parse_error"])
                call_error_rows += bool(row["call_error"])
                if done % 200 == 0 or done == total:
                    print(f"  {done}/{total} done ({parse_errors} output_parse_errors, {call_error_rows} call_errors, {len(errors)} exceptions)", flush=True)

    if errors:
        print(f"ERROR: {len(errors)} task exceptions; decisions.jsonl not written", file=sys.stderr)
        for e in errors[:10]:
            print("  ", e, file=sys.stderr)
        sys.exit(1)

    rows.sort(key=lambda r: (r["base_request_id"], r["phase"], str(r["factor"]), r["salt"]))
    payload = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n" for r in rows)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".decisions-", suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, out_dir / "decisions.jsonl")
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    meta = {"variant": VARIANT, "backend_label": cfg_det.label, "model": cfg_det.model, "seed": args.seed,
            "n_requests": len(reqs), "n_repro": args.n_repro, "n_rows": len(rows), "phases": sorted(phases),
            "policy_version": "v2", "policy_sha256": POLICY_V2_SHA256, "decisions_sha256": sha,
            "temperatures": {"canonical": T_DET, "sampled": T_REPRO, "perturbation": T_DET}}
    (out_dir / "chain_meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(rows)} rows -> {out_dir / 'decisions.jsonl'} sha256={sha[:16]}")


if __name__ == "__main__":
    main()
