"""Absorbers at depth: E1, E2, E3 per step over a chain (C3) sweep, offline.

Two nondeterministic steps (c_parse, c_justify) feed the deterministic core;
c_explain is a sink. Per step: functionality at T=0 (E2), absorption over
repeated samples (E1), and reuse policies under perturbation (E3), where the
dependency cone (P2) now has a real graph: parse outputs the six request
fields, justify outputs the two justification fields, and a perturbation of
one leaves the other reusable. A fourth measurement, cross-step sensitivity,
asks whether a step's output changes when its prompt changes in a sentence it
should ignore.

    python -m chain.absorbers_chain --results results-chain-gemma3
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chain.policy_v2 import decide_v2, oracle_v2  # noqa: E402
from chain.taskgen import chain_perturbations, generate_chain  # noqa: E402
from chain.validate import validate  # noqa: E402
from harness.absorbers import ALIAS_NORMALISER, _frac, _r, _rule_of_three, apply_normaliser, e5_canary  # noqa: E402

SCHEMA = "absorbers-chain/v5"
FEEDING_STEPS = ("c_parse", "c_justify")
STEP_INDEX = {"c_parse": 0, "c_justify": 1, "c_explain": 2}
PARSE_FIELDS = frozenset({"category", "amount", "budget_remaining", "data_processing", "security_review", "urgency"})
JUSTIFY_FIELDS = frozenset({"justification_adequate", "justification_category"})
SUPPLIER_FIELDS = frozenset({"supplier_approved", "supplier_risk", "supplier_sanctioned"})
STEP_OUTPUTS = {"c_parse": PARSE_FIELDS, "c_justify": JUSTIFY_FIELDS}
INVALID = "__invalid__"

# A perturbation factor is a name in the operator's vocabulary; a step's output set
# is a set of record field names. They coincide for the nine base factors and do NOT
# coincide for the justification toggle, which changes two fields at once. Comparing
# the two vocabularies directly is a bug: it made the dependency cone reuse the
# justify step on exactly the perturbation that changes what that step produces.
FACTOR_TO_FIELDS = {"justification": JUSTIFY_FIELDS}


def factor_fields(factor: str) -> frozenset:
    """The record fields a perturbation of this factor changes."""
    return FACTOR_TO_FIELDS.get(factor, frozenset({factor}))
POLICIES = ("P0_recompute", "P1_input_hash", "P2_dependency_cone", "P3_oracle_irrelevance")


class ChainSweep:
    def __init__(self, results_dir: Path, *, seed: int = 42, check_cache: bool = True):
        self.dir = Path(results_dir)
        self.info = validate(self.dir, seed=seed, check_cache=check_cache)
        self.rows = [json.loads(l) for l in (self.dir / "decisions.jsonl").read_text().splitlines() if l.strip()]
        self.by_key: dict[tuple, dict] = {}
        self.perts: dict[str, list[dict]] = defaultdict(list)
        self.repro: dict[str, list[dict]] = defaultdict(list)
        for r in self.rows:
            self.by_key[(r["base_request_id"], r["phase"], r["salt"])] = r
            if r["phase"] == "pert":
                self.perts[r["base_request_id"]].append(r)
            elif r["phase"] == "repro":
                self.repro[r["base_request_id"]].append(r)
        for k in self.perts:
            self.perts[k].sort(key=lambda r: r["salt"])
        for k in self.repro:
            self.repro[k].sort(key=lambda r: r["salt"])
        self.requests = {r.request_id: r for r in generate_chain(seed)}
        self.bids = sorted({r["base_request_id"] for r in self.rows})
        self._raw: dict[str, Optional[dict]] = {}
        self._perts: dict[str, dict] = {}
        self._sens: dict[str, frozenset] = {}

    def raw(self, cid: Optional[str]) -> Optional[dict]:
        if not cid:
            return None
        if cid in self._raw:
            return self._raw[cid]
        p = self.dir / "raw" / f"{cid}.json"
        try:
            t = p.read_text(encoding="utf-8")
            rec = json.loads(t) if t.strip() else None
        except (FileNotFoundError, json.JSONDecodeError):
            rec = None
        self._raw[cid] = rec
        return rec

    @staticmethod
    def step(row: dict, name: str) -> dict:
        return ((row.get("audit") or {}).get("steps") or {}).get(name) or {}

    def step_record(self, row: dict, name: str) -> Optional[dict]:
        return self.raw(self.step(row, name).get("cache_id"))

    @staticmethod
    def prompt(rec: Optional[dict]) -> Optional[tuple]:
        if not rec or "call_spec" not in rec:
            return None
        cs = rec["call_spec"]
        return (cs.get("system", ""), cs.get("user", ""))

    def step_fields(self, row: dict, name: str) -> Optional[tuple]:
        st = self.step(row, name)
        if not st.get("valid") or st.get("fields") is None:
            return None
        return tuple(sorted(st["fields"].items()))

    def perturbed(self, bid: str, salt: str):
        if bid not in self._perts:
            self._perts[bid] = chain_perturbations(self.requests[bid])
        _, factor, j = salt.split(":")
        return self._perts[bid][factor][int(j)]

    def sensitivity(self, bid: str) -> frozenset:
        if bid in self._sens:
            return self._sens[bid]
        req = self.requests[bid]
        base = oracle_v2(req).decision.value
        if bid not in self._perts:
            self._perts[bid] = chain_perturbations(req)
        self._sens[bid] = frozenset(f for f, vs in self._perts[bid].items() if any(oracle_v2(v).decision.value != base for v in vs))
        return self._sens[bid]


# ---- E2 per step -----------------------------------------------------------

def e2_step(sw: ChainSweep, name: str) -> dict:
    pairs = text_id = rec_cmp = rec_id = missing = 0
    req_with_pairs = req_with_mismatch = 0
    per_factor: dict[str, dict[str, int]] = defaultdict(lambda: {"pairs": 0, "text_identical": 0, "record_comparable": 0, "record_identical": 0})
    profile: dict[str, int] = defaultdict(int)
    for bid in sw.bids:
        canon = sw.by_key.get((bid, "canon", "canon"))
        crec = sw.step_record(canon, name) if canon else None
        if crec is None:
            missing += 1
            continue
        cp, ct, cf = sw.prompt(crec), crec.get("text") or "", sw.step_fields(canon, name)
        req_comparable = req_mismatch = 0
        for prow in sw.perts[bid]:
            prec = sw.step_record(prow, name)
            if prec is None:
                missing += 1
                continue
            if sw.prompt(prec) != cp:
                continue
            f = prow["factor"]
            pairs += 1; per_factor[f]["pairs"] += 1
            if (prec.get("text") or "") == ct:
                text_id += 1; per_factor[f]["text_identical"] += 1
            pf = sw.step_fields(prow, name)
            if cf is not None and pf is not None:
                rec_cmp += 1; req_comparable += 1; per_factor[f]["record_comparable"] += 1
                if cf == pf:
                    rec_id += 1; per_factor[f]["record_identical"] += 1
                else:
                    req_mismatch += 1
        if req_comparable:
            req_with_pairs += 1
            profile[f"{req_mismatch}/{req_comparable}"] += 1
        req_with_mismatch += bool(req_mismatch)
    return {"requests_with_comparable_pairs": req_with_pairs, "requests_with_mismatch": req_with_mismatch,
            "record_identical_upper_bound_request_level": (_rule_of_three(req_with_pairs) if req_with_mismatch == 0 and req_with_pairs else None),
            "pair_mismatch_profile": dict(sorted(profile.items())),
            "identical_input_pairs": pairs, "text_identical": text_id, "text_identical_frac": _frac(text_id, pairs),
            "record_comparable_pairs": rec_cmp, "record_identical": rec_id, "record_identical_frac": _frac(rec_id, rec_cmp),
            "prompt_identity_method": "stored", "raw_missing": missing, "per_factor": {k: dict(v) for k, v in sorted(per_factor.items())}}


# ---- E1 at depth -------------------------------------------------------------

def e1_depth(sw: ChainSweep, *, table: Optional[dict] = None) -> dict:
    n = 0
    dec_var = 0
    step_var = {s: 0 for s in FEEDING_STEPS}
    any_var = 0
    absorbed = {s: 0 for s in FEEDING_STEPS}
    leaked = {s: 0 for s in FEEDING_STEPS}
    absorbed_any = leaked_any = 0
    unsafe = 0
    by_oracle: dict[str, dict[str, int]] = defaultdict(lambda: {"requests": 0, "decision_varies": 0, "any_step_varies": 0, "absorbed": 0, "leaked": 0})
    varying_hist: dict[str, int] = defaultdict(int)
    n_repro = None
    for bid in sw.bids:
        rows = sw.repro.get(bid, [])
        if not rows:
            continue
        n += 1; n_repro = len(rows)
        if table:
            req = sw.requests[bid]
            decisions = set()
            for r in rows:
                d = _decision_with(sw, r, r, req, {s: False for s in FEEDING_STEPS}, table=table)
                decisions.add(d if d is not None else r["decision"])
        else:
            decisions = {r["decision"] for r in rows}
        od = rows[0]["oracle_decision"]
        dv = len(decisions) > 1
        dec_var += dv
        if "approve" in decisions and od != "approve":
            unsafe += 1
        varying_steps = []
        for s in FEEDING_STEPS:
            recs = {sw.step_fields(r, s) or INVALID for r in rows}
            if len(recs) > 1:
                step_var[s] += 1; varying_steps.append(s)
                if dv:
                    leaked[s] += 1
                else:
                    absorbed[s] += 1
        strat = by_oracle[od]; strat["requests"] += 1; strat["decision_varies"] += dv
        if varying_steps:
            any_var += 1; strat["any_step_varies"] += 1
            varying_hist["+".join(varying_steps)] += 1
            if dv:
                leaked_any += 1; strat["leaked"] += 1
            else:
                absorbed_any += 1; strat["absorbed"] += 1
    out: dict[str, Any] = {"requests": n, "n_repro": n_repro, "decision_varies": dec_var, "decision_varies_frac": _frac(dec_var, n),
                           "unsafe_in_samples": unsafe, "unsafe_in_samples_frac": _frac(unsafe, n),
                           "any_step_varies": any_var, "any_step_varies_frac": _frac(any_var, n),
                           "absorbed_any": absorbed_any, "leaked_any": leaked_any, "absorption_ratio_any": _frac(absorbed_any, any_var),
                           "varying_steps_hist": dict(sorted(varying_hist.items())),
                           "by_oracle_decision": {k: dict(v) for k, v in sorted(by_oracle.items())}, "per_step": {}}
    for s in FEEDING_STEPS:
        out["per_step"][s] = {"varies": step_var[s], "varies_frac": _frac(step_var[s], n), "absorbed": absorbed[s], "leaked": leaked[s],
                              "absorption_ratio": _frac(absorbed[s], step_var[s])}
    return out


# ---- E7: is the headline a property of one constant? --------------------------

SHARE_GRID = (0.15, 0.25, 0.35, 0.50)


def share_sensitivity(sw: ChainSweep, shares=SHARE_GRID) -> dict:
    """Re-score recompute and cone reuse at neighbouring values of JUSTIFICATION_SHARE.

    The rule that carries the depth-three result fires when the amount reaches a
    share of the binding ceiling, and that share was fixed at 0.25 before any chain
    sweep was collected. A reviewer asked whether the unsafe approvals are a property
    of that constant. Nothing here needs a model call: the feeding-step outputs are
    recorded, so both the oracle and the decision can be recomputed at any share
    from the same records. The 0.25 row reproduces the frozen E3 unsafe and
    divergence counts. Its row count reconciles with E3's pert_rows through
    rows_skipped_invalid_fresh_step: a row whose fresh feeding-step record is
    invalid is skipped here, where e3_depth keeps it under its recorded decision.
    """
    import chain.policy_v2 as pv2
    saved = pv2.JUSTIFICATION_SHARE
    out = {}
    try:
        for share in shares:
            pv2.JUSTIFICATION_SHARE = share
            rows = fires = skipped = 0
            p0_unsafe = p2_unsafe = p2_div = 0
            p2_req: set = set()
            for bid in sw.bids:
                canon = sw.by_key.get((bid, "canon", "canon"))
                if canon is None or any(sw.step_fields(canon, s) is None for s in FEEDING_STEPS):
                    continue
                for prow in sw.perts[bid]:
                    preq = sw.perturbed(bid, prow["salt"])
                    od = pv2.oracle_v2(preq).decision.value
                    p0 = _decision_with(sw, canon, prow, preq, {s: False for s in FEEDING_STEPS})
                    if p0 is None:
                        skipped += 1
                        continue
                    rows += 1
                    fires += (pv2.oracle_v2(preq).firing_rule == "inadequate_justification")
                    reuse = {s: not (factor_fields(prow["factor"]) & STEP_OUTPUTS[s]) for s in FEEDING_STEPS}
                    p2 = _decision_with(sw, canon, prow, preq, reuse) if any(reuse.values()) else p0
                    if p2 is None:
                        p2 = p0
                    if p0 == "approve" and od != "approve":
                        p0_unsafe += 1
                    if p2 != p0:
                        p2_div += 1
                        if p2 == "approve" and od != "approve":
                            p2_unsafe += 1; p2_req.add(bid)
            out[f"{share:.2f}"] = {"rows": rows, "rows_skipped_invalid_fresh_step": skipped,
                                   "oracle_rule_fires": fires, "p0_unsafe_absolute": p0_unsafe,
                                   "p2_diverged": p2_div, "p2_unsafe_introduced": p2_unsafe, "p2_unsafe_requests": len(p2_req)}
    finally:
        pv2.JUSTIFICATION_SHARE = saved
    return {"shares": list(shares), "frozen_share": saved, "by_share": out}


# ---- E3 at depth -------------------------------------------------------------

def _decision_with(sw: ChainSweep, canon: dict, prow: dict, preq, reuse: dict[str, bool],
                   *, table: Optional[dict] = None) -> Optional[str]:
    """Decision the core reaches if each feeding step is reused (canon) or recomputed (pert row).

    table is a second normaliser applied where the core reads the record (see
    harness.absorbers.ALIAS_NORMALISER); None is the frozen one.
    """
    rec: dict = {}
    pstruct = preq.structured_v2()
    for s in FEEDING_STEPS:
        src = canon if reuse[s] else prow
        f = sw.step_fields(src, s)
        if f is None:
            return None
        rec.update(dict(f))
    for k in SUPPLIER_FIELDS:
        rec[k] = pstruct[k]
    return decide_v2(apply_normaliser(rec, table)).decision.value


def e3_depth(sw: ChainSweep, *, table: Optional[dict] = None) -> dict:
    eligible = skipped = pert_rows = 0
    alias_rows = rows_changed = alias_wrong_known = 0
    cost = {s: [0.0, 0, 0] for s in FEEDING_STEPS}  # latency sum, tokens sum, n
    stats = {p: {"step_calls_saved": {s: 0 for s in FEEDING_STEPS}, "both_saved": 0, "diverged": 0, "diverged_given_any_reuse": 0,
                 "any_reuse": 0, "oracle_correct": 0, "unsafe_introduced": 0, "unsafe_requests": set(),
                            "unsafe_absolute": 0, "unsafe_absolute_requests": set(),
                            "step_calls_triggered": {s: 0 for s in FEEDING_STEPS}, "unscoreable_rows": 0,
                            "latency_ms_saved": 0.0, "tokens_saved": 0} for p in POLICIES}
    per_factor: dict[str, dict[str, dict]] = defaultdict(lambda: {p: {"rows": 0, "parse_reused": 0, "justify_reused": 0, "diverged": 0} for p in POLICIES})
    cross = {"c_parse_on_justification_toggle": {"pairs": 0, "record_identical": 0},
             "c_justify_on_request_side": {"pairs": 0, "record_identical": 0},
             "c_parse_on_supplier_side": {"pairs": 0, "record_identical": 0},
             "c_justify_on_supplier_side": {"pairs": 0, "record_identical": 0}}
    # Which (request, salt) rows each policy fires on, per step. The two-step analysis
    # reports nesting flags; the depth version had not, and the paper's claim that P3
    # is not a bound on P2 (closed-ledger L72) rested on a one-off count.
    fired: dict[str, dict[str, set]] = {p: {s: set() for s in FEEDING_STEPS} for p in POLICIES}
    for bid in sw.bids:
        canon = sw.by_key.get((bid, "canon", "canon"))
        if canon is None or any(sw.step_fields(canon, s) is None for s in FEEDING_STEPS):
            skipped += 1
            continue
        eligible += 1
        cprompts = {s: sw.prompt(sw.step_record(canon, s)) for s in FEEDING_STEPS}
        sens = sw.sensitivity(bid)
        for prow in sw.perts[bid]:
            factor = prow["factor"]
            preq = sw.perturbed(bid, prow["salt"])
            pert_rows += 1
            od = prow["oracle_decision"]
            fresh = {s: sw.step_fields(prow, s) for s in FEEDING_STEPS}
            # Recompute's own decision under the table: all steps fresh. Faithful to
            # the recorded decision when the table is None or does not fire.
            p0 = prow["decision"]
            if table:
                p0t = _decision_with(sw, canon, prow, preq, {s: False for s in FEEDING_STEPS}, table=table)
                if p0t is not None:
                    p0 = p0t
                if p0 != prow["decision"]:
                    rows_changed += 1
                pcat = (fresh["c_parse"] and dict(fresh["c_parse"]).get("category"))
                if isinstance(pcat, str) and pcat in table:
                    alias_rows += 1
                    if table[pcat] != preq.category:
                        alias_wrong_known += 1
            # per-step cost
            for s in FEEDING_STEPS:
                rec = sw.step_record(prow, s)
                if rec and rec.get("latency_ms") is not None:
                    cost[s][0] += float(rec["latency_ms"]); cost[s][1] += (rec.get("tokens_in") or 0) + (rec.get("tokens_out") or 0); cost[s][2] += 1
            # cross-step sensitivity: did a step's record change when only an irrelevant part of its prompt changed?
            cf = {s: sw.step_fields(canon, s) for s in FEEDING_STEPS}
            if factor == "justification" and cf["c_parse"] is not None and fresh["c_parse"] is not None:
                cross["c_parse_on_justification_toggle"]["pairs"] += 1
                cross["c_parse_on_justification_toggle"]["record_identical"] += (cf["c_parse"] == fresh["c_parse"])
            if (factor_fields(factor) & PARSE_FIELDS) and cf["c_justify"] is not None and fresh["c_justify"] is not None:
                cross["c_justify_on_request_side"]["pairs"] += 1
                cross["c_justify_on_request_side"]["record_identical"] += (cf["c_justify"] == fresh["c_justify"])
            if factor_fields(factor) & SUPPLIER_FIELDS:
                for s, key in (("c_parse", "c_parse_on_supplier_side"), ("c_justify", "c_justify_on_supplier_side")):
                    if cf[s] is not None and fresh[s] is not None:
                        cross[key]["pairs"] += 1; cross[key]["record_identical"] += (cf[s] == fresh[s])
            # policy triggers, per step
            trig = {}
            for s in FEEDING_STEPS:
                pp = sw.prompt(sw.step_record(prow, s))
                p1 = (pp is not None and cprompts[s] is not None and pp == cprompts[s])
                p2 = not (factor_fields(factor) & STEP_OUTPUTS[s])
                p3 = factor not in sens
                trig[s] = {"P0_recompute": False, "P1_input_hash": p1, "P2_dependency_cone": p2, "P3_oracle_irrelevance": p3}
                for pol in POLICIES:
                    if trig[s][pol]:
                        fired[pol][s].add((bid, prow["salt"]))
            for pol in POLICIES:
                reuse = {s: trig[s][pol] for s in FEEDING_STEPS}
                st = stats[pol]; pf = per_factor[factor][pol]; pf["rows"] += 1
                # What the policy TRIGGERS is a property of the graph and the
                # perturbation. What it SAVES is smaller, because a row whose
                # recomputed record is missing or invalid cannot be scored and is
                # dropped to recompute below. Conflating the two turns a model's
                # schema-holding failures into an apparent difference in the
                # structural policy, which is the one column that must not carry them.
                for s in FEEDING_STEPS:
                    if reuse[s]:
                        st["step_calls_triggered"][s] += 1
                dec = _decision_with(sw, canon, prow, preq, reuse, table=table) if any(reuse.values()) else p0
                if dec is None:
                    dec = p0; reuse = {s: False for s in FEEDING_STEPS}
                    st["unscoreable_rows"] += 1
                anyr = any(reuse.values())
                st["any_reuse"] += anyr
                st["both_saved"] += all(reuse.values())
                for s in FEEDING_STEPS:
                    if reuse[s]:
                        st["step_calls_saved"][s] += 1
                        rec = sw.step_record(prow, s)
                        if rec and rec.get("latency_ms") is not None:
                            st["latency_ms_saved"] += float(rec["latency_ms"]); st["tokens_saved"] += (rec.get("tokens_in") or 0) + (rec.get("tokens_out") or 0)
                pf["parse_reused"] += reuse["c_parse"]; pf["justify_reused"] += reuse["c_justify"]
                if dec != p0:
                    st["diverged"] += 1; pf["diverged"] += 1
                    if anyr:
                        st["diverged_given_any_reuse"] += 1
                    if dec == "approve" and od != "approve":
                        # Rows cluster by request: two rows of one request are one
                        # request that reuse got wrong, which is the unit the
                        # request-level bounds elsewhere in this analysis use.
                        st["unsafe_introduced"] += 1
                        st["unsafe_requests"].add(bid)
                # Absolute, not relative: P0 never differs from itself, so its own
                # unsafe approvals are invisible to unsafe_introduced.
                if dec == "approve" and od != "approve":
                    st["unsafe_absolute"] += 1
                    st["unsafe_absolute_requests"].add(bid)
                st["oracle_correct"] += (dec == od)
    total_feeding_calls = pert_rows * len(FEEDING_STEPS)
    out: dict[str, Any] = {"eligible_requests": eligible, "skipped_invalid_canon": skipped, "pert_rows": pert_rows,
                           "normaliser": dict(table) if table else None,
                           "alias_rows": alias_rows, "rows_changed": rows_changed, "alias_wrong_known": alias_wrong_known,
                           "feeding_step_calls": total_feeding_calls,
                           "step_cost": {s: {"mean_latency_ms": _r(c[0] / c[2]) if c[2] else None, "mean_tokens": _r(c[1] / c[2]) if c[2] else None} for s, c in cost.items()},
                           "cross_step_sensitivity": {k: {**v, "record_identical_frac": _frac(v["record_identical"], v["pairs"])} for k, v in cross.items()},
                           "nesting_by_step": {s: {"P1_subset_P2": fired["P1_input_hash"][s] <= fired["P2_dependency_cone"][s],
                                                   "P2_subset_P3": fired["P2_dependency_cone"][s] <= fired["P3_oracle_irrelevance"][s]}
                                               for s in FEEDING_STEPS},
                           "nesting_P1_subset_P2": all(fired["P1_input_hash"][s] <= fired["P2_dependency_cone"][s] for s in FEEDING_STEPS),
                           "nesting_P2_subset_P3": all(fired["P2_dependency_cone"][s] <= fired["P3_oracle_irrelevance"][s] for s in FEEDING_STEPS),
                           "policies": {}, "per_factor": {}}
    for pol in POLICIES:
        st = stats[pol]
        saved = sum(st["step_calls_saved"].values())
        triggered = sum(st["step_calls_triggered"].values())
        out["policies"][pol] = {"step_calls_saved": dict(st["step_calls_saved"]), "calls_saved_frac": _frac(saved, total_feeding_calls),
                                "step_calls_triggered": dict(st["step_calls_triggered"]),
                                "calls_triggered_frac": _frac(triggered, total_feeding_calls),
                                "unscoreable_rows": st["unscoreable_rows"],
                                "both_steps_saved": st["both_saved"], "both_steps_saved_frac": _frac(st["both_saved"], pert_rows),
                                "diverged": st["diverged"], "divergence_frac": _frac(st["diverged"], pert_rows),
                                "divergence_given_any_reuse_frac": _frac(st["diverged_given_any_reuse"], st["any_reuse"]),
                                "oracle_correct_frac": _frac(st["oracle_correct"], pert_rows), "unsafe_introduced": st["unsafe_introduced"],
                                "unsafe_requests": len(st["unsafe_requests"]),
                                "unsafe_request_ids": sorted(st["unsafe_requests"]),
                                "unsafe_absolute": st["unsafe_absolute"],
                                "unsafe_absolute_requests": len(st["unsafe_absolute_requests"]),
                                "unsafe_upper_bound_request_level": (
                                    _rule_of_three(eligible) if st["unsafe_introduced"] == 0 else None),
                                "latency_ms_saved_total": _r(st["latency_ms_saved"], 1), "tokens_saved_total": st["tokens_saved"]}
    for f in sorted(per_factor):
        out["per_factor"][f] = {p: dict(v) for p, v in per_factor[f].items()}
    return out


def _e8(sw: ChainSweep) -> dict:
    from chain.certificates import e8_decomposition  # lazy: certificates imports this module's constants
    return e8_decomposition(sw)


def analyse(results_dir: Path, *, seed: int = 42, check_cache: bool = True) -> dict:
    sw = ChainSweep(results_dir, seed=seed, check_cache=check_cache)
    meta_p = results_dir / "chain_meta.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    e2 = {s: e2_step(sw, s) for s in FEEDING_STEPS}
    return {"schema": SCHEMA, "results_dir": results_dir.name, "variant": "C3", "backend_label": meta.get("backend_label"),
            "model": meta.get("model"), "decisions_sha256": meta.get("decisions_sha256"), "policy_sha256": meta.get("policy_sha256"),
            "seed": seed, "n_requests": len(sw.bids), "validation": sw.info,
            "E2_functionality_T0": e2, "E5_canary": {s: e5_canary(e2[s]) for s in FEEDING_STEPS},
            "E1_absorption": e1_depth(sw), "E3_reuse": e3_depth(sw),
            "E7_share_sensitivity": share_sensitivity(sw),
            # Field-level decomposition and the reuse certificates (chain/certificates.py):
            # the first offline test of whether a guard over the consumer's value classes
            # has any measured basis.
            "E8_decomposition": _e8(sw),
            # The same analysis under a second deterministic consumer; E2 has no
            # alias variant by construction, it compares records before any consumer.
            "E6_alias_normaliser": {"table": dict(ALIAS_NORMALISER),
                                    "E1_absorption": e1_depth(sw, table=ALIAS_NORMALISER),
                                    "E3_reuse": e3_depth(sw, table=ALIAS_NORMALISER)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-check-cache", action="store_true")
    a = ap.parse_args()
    d = Path(a.results)
    if not d.is_absolute():
        d = ROOT / d
    out = analyse(d, seed=a.seed, check_cache=not a.no_check_cache)
    (d / "absorbers_chain.json").write_text(json.dumps(out, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n")
    print(f"wrote {d / 'absorbers_chain.json'}")
    e1, e3 = out["E1_absorption"], out["E3_reuse"]["policies"]
    for s, e2 in out["E2_functionality_T0"].items():
        print(f"  E2 {s}: pairs={e2['identical_input_pairs']} text_id={e2['text_identical_frac']} rec_id={e2['record_identical_frac']}")
    print(f"  E1: decision_varies={e1['decision_varies_frac']} any_step_varies={e1['any_step_varies_frac']} absorption_any={e1['absorption_ratio_any']} per_step={ {s: v['absorption_ratio'] for s, v in e1['per_step'].items()} }")
    for p, v in e3.items():
        print(f"  E3 {p:22s} saved={v['calls_saved_frac']} both={v['both_steps_saved_frac']} div={v['divergence_frac']} oracle_ok={v['oracle_correct_frac']}")
    print("  cross-step:", {k: v["record_identical_frac"] for k, v in out["E3_reuse"]["cross_step_sensitivity"].items()})


if __name__ == "__main__":
    main()
