"""Sweep runner: drive the four variants over the request set and record every
decision to ``results/decisions.jsonl``.

Three measurement phases per (request, variant):
  * ``repro``  : N samples at a stochastic temperature -> reproducibility.
  * ``canon``  : one temperature-0 sample -> the canonical decision used for
                 policy-violation, coverage and as the base for perturbation.
  * ``pert``   : one temperature-0 sample per single-factor perturbation ->
                 the empirical counterfactual-sensitivity set.

Reproducibility notes:
  * Each LLM call is content-cached under ``results/raw/`` (see llm_client).
  * ``--from-cache`` reruns with ``offline=True``: every call is a cache hit, so
    ``decisions.jsonl`` regenerates deterministically with no network. Same
    seed + same cache -> identical output.
  * The run is resumable: interrupt and rerun; completed calls are cache hits.
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
sys.path.insert(0, str(ROOT))

from agents import a0_end_to_end, a1_tools, a2_deterministic_gate, a3_auditchain
from agents.llm_client import config_for
from agents.policy_core import POLICY_TEXT, oracle, perturbations
from taskgen.generator import generate
from taskgen.schema import ProcurementRequest

VARIANTS = {
    "A0": a0_end_to_end.run,
    "A1": a1_tools.run,
    "A2": a2_deterministic_gate.run,
    "A3": a3_auditchain.run,
}

T_REPRO = 0.7   # stochastic temperature for the reproducibility phase
T_DET = 0.0     # temperature for canonical + perturbation decisions
POLICY_SHA256 = hashlib.sha256(POLICY_TEXT.encode("utf-8")).hexdigest()


def _json_sha256(value) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_decision_row(
    variant, phase, salt, factor, req: ProcurementRequest, res, cfg, seed,
):
    """Build the stable schema-v2 row shared by sweeps and read-only replay."""
    orc = oracle(req)
    audit = dict(res.audit or res.raw or {})
    cache_ids = list(audit.get("cache_ids") or [])
    provider_errors = list(audit.get("call_errors") or [])
    return {
        "schema_version": 2,
        "generator_seed": seed,
        "request_id": req.request_id,
        "base_request_id": req.request_id.split("|", 1)[0],
        "kind": req.kind,
        "planted_rule": req.planted_rule,
        "variant": variant,
        "phase": phase,
        "salt": salt,
        "factor": factor,
        "backend_label": cfg.label,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "input_sha256": _json_sha256(req.to_json()),
        "policy_sha256": POLICY_SHA256,
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
        "cache_ids": cache_ids,
        "call_errors": provider_errors,
        "audit": audit,
        "llm_calls": res.llm_calls,
        "tokens_in": res.tokens_in,
        "tokens_out": res.tokens_out,
        "latency_ms": res.latency_ms,
        "oracle_decision": orc.decision.value,
        "oracle_rule": orc.firing_rule,
        "oracle_firing_rule_factors": orc.firing_rule_factors,
        "oracle_gating": orc.gating_factors,
    }


# Compatibility for any local analysis that imported the former private name.
_row = build_decision_row


def build_tasks(reqs, variants, n_repro, phases):
    """Yield (variant, phase, salt, factor, request, use_det_temp) tuples."""
    for req in reqs:
        for v in variants:
            if "canon" in phases:
                yield (v, "canon", "canon", None, req, True)
            if "repro" in phases:
                for i in range(n_repro):
                    yield (v, "repro", f"repro{i}", None, req, False)
            if "pert" in phases:
                for factor, variants_list in perturbations(req).items():
                    for j, preq in enumerate(variants_list):
                        yield (v, "pert", f"pert:{factor}:{j}", factor, preq, True)


def main():
    ap = argparse.ArgumentParser(description="Autonomy-quadrant sweep runner")
    ap.add_argument("--backend", default="deepseek",
                    help="deepseek | litellm-flash | litellm-gpt4o | openai-gpt4o "
                         "| ollama:<model> (local, e.g. ollama:gemma3:4b)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-repro", type=int, default=10)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="use only the first K requests (smoke)")
    ap.add_argument("--variants", default="A0,A1,A2,A3")
    ap.add_argument("--phases", default="canon,repro,pert")
    ap.add_argument("--from-cache", action="store_true",
                    help="offline: recompute from results/raw only (no network)")
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument(
        "--allow-partial-output", action="store_true",
        help="allow --limit/subset phases or variants to write to the main results path",
    )
    args = ap.parse_args()

    out_dir = Path(args.out)
    cache_dir = out_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    reqs = generate(args.seed)
    if args.limit:
        reqs = reqs[: args.limit]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    phases = set(p.strip() for p in args.phases.split(",") if p.strip())
    if len(variants) != len(set(variants)):
        ap.error("--variants contains duplicates")
    unknown_variants = sorted(set(variants) - set(VARIANTS))
    if unknown_variants:
        ap.error(f"unknown variants: {unknown_variants}")
    unknown_phases = sorted(phases - {"canon", "repro", "pert"})
    if unknown_phases:
        ap.error(f"unknown phases: {unknown_phases}")
    if args.n_repro < 2:
        ap.error("--n-repro must be at least 2")
    if args.workers < 1:
        ap.error("--workers must be at least 1")

    full_sweep = (
        not args.limit
        and variants == list(VARIANTS)
        and phases == {"canon", "repro", "pert"}
    )
    if (
        not full_sweep
        and out_dir.resolve() == (ROOT / "results").resolve()
        and not args.allow_partial_output
    ):
        ap.error(
            "refusing to overwrite publishable results with a partial sweep; "
            "use a different --out or pass --allow-partial-output explicitly"
        )

    cfg_det = config_for(
        args.backend, temperature=T_DET, offline=args.from_cache,
    )
    cfg_repro = config_for(
        args.backend, temperature=T_REPRO, offline=args.from_cache,
    )
    needs_key = not args.backend.startswith("ollama:")
    if not args.from_cache and needs_key and not cfg_det.api_key:
        print(f"ERROR: no API key for backend '{args.backend}'.", file=sys.stderr)
        sys.exit(2)

    tasks = list(build_tasks(reqs, variants, args.n_repro, phases))
    task_keys = [(t[0], t[1], t[2], t[3], t[4].request_id) for t in tasks]
    if len(task_keys) != len(set(task_keys)):
        raise RuntimeError("duplicate logical tasks generated")
    total = len(tasks)
    print(f"backend={args.backend} model={cfg_det.model} requests={len(reqs)} "
          f"variants={variants} phases={sorted(phases)} tasks={total} "
          f"workers={args.workers} from_cache={args.from_cache}")

    rows = []
    done = 0
    parse_errors = 0
    call_error_rows = 0
    task_errors = []
    lock = threading.Lock()

    def work(task):
        v, phase, salt, factor, req, use_det = task
        cfg = cfg_det if use_det else cfg_repro
        res = VARIANTS[v](req, cfg, salt, cache_dir, offline=args.from_cache)
        return build_decision_row(v, phase, salt, factor, req, res, cfg, args.seed)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, t): t for t in tasks}
        for fut in as_completed(futs):
            try:
                row = fut.result()
                rows.append(row)
                if row["parse_error"]:
                    parse_errors += 1
                if row["call_error"]:
                    call_error_rows += 1
            except Exception as e:
                t = futs[fut]
                task_errors.append((t, e))
                print(f"  task error {t[0]}/{t[1]}/{t[4].request_id}: "
                      f"{type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            with lock:
                done += 1
                if done % 100 == 0 or done == total:
                    print(
                        f"  {done}/{total} done ({parse_errors} output_parse_errors, "
                        f"{call_error_rows} call_errors, {len(task_errors)} exceptions)",
                          flush=True)

    if task_errors or call_error_rows or len(rows) != total:
        print(
            "ERROR: incomplete sweep; existing decisions.jsonl was left untouched "
            f"(rows={len(rows)}/{total}, call_error_rows={call_error_rows}, "
            f"exceptions={len(task_errors)}). Rerun to resume from successful cache entries.",
            file=sys.stderr,
        )
        sys.exit(1)

    # stable ordering for reproducible diffs
    rows.sort(key=lambda r: (r["base_request_id"], r["variant"], r["phase"],
                             str(r["factor"]), r["salt"]))
    if full_sweep:
        from harness.validate_results import validate_rows
        validate_rows(rows, out_dir, seed=args.seed, check_cache=True)

    out_path = out_dir / "decisions.jsonl"
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=out_dir,
            prefix=".decisions.", suffix=".jsonl.tmp", delete=False,
        ) as fh:
            tmp_name = fh.name
            for row in rows:
                fh.write(json.dumps(
                    row, sort_keys=True, ensure_ascii=False, allow_nan=False,
                ) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, out_path)
        tmp_name = None
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
    print(f"wrote {len(rows)} decision rows -> {out_path}")


if __name__ == "__main__":
    main()
