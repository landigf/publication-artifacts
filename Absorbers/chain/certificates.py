"""E8: what a recorded feeding-step field is (functional, correct, load-bearing), and
whether a reuse certificate over the consumer's value classes has any measured basis.

Offline. No model call. No frozen artifact touched. Everything here is recomputed
from the recorded step outputs and the deterministic consumer, chain.policy_v2.

Two questions, both raised by the 2026-09-05 Codex proposals (P06) after the paper's
headline: cone reuse introduced unsafe approvals on the one chain configuration whose
two feeding steps were both perfectly functional, because one recorded output was
consistent, wrong, and load-bearing.

1. Decomposition. For every field a feeding step outputs, on the canonical record of
   every eligible request: is it FUNCTIONAL (identical across that request's repeated
   samples), CORRECT (equal to the regenerated task truth), and LOAD-BEARING (does
   changing that field alone, to any other value class the consumer distinguishes,
   change the terminal decision)? Eight cells per step. The headline break should sit
   in functional & wrong & load-bearing.

2. Certificates. On every perturbation row where the declared cone would reuse a step,
   enumerate every value class the consumer distinguishes for that step's output
   fields (jointly over both steps when both are candidates) and ask whether reuse is
   invariant. Three scopes, scored against recompute:
     cert_eq         valid classes only; the decision must be identical in every class.
     cert_safe_valid valid classes only; reuse may not approve where any valid fresh
                     output would not have.
     cert_safe_all   every class including invalid output, which the consumer always
                     escalates. Under it an approving reuse can never be certified, so
                     the scheme collapses to "the recorded decision is not approve".
   The collapse is the point of scoring it: it is what quantifying over the invalid
   path costs, stated as a number rather than argued.

Why the value classes are exact. chain.policy_v2.decide_v2 is a straight-line cascade
with a three-value output and exactly one approving rule, the fallthrough. Booleans
and enumerations are finite. category is a seven-class quotient through
taskgen.schema.normalize_category (six known ceilings and unknown). amount and
budget_remaining enter only through monotone comparisons against cuts that are
functions of the other fields (budget, ceiling, 0.9 and 0.25 of the binding
threshold), so a witness at each cut, between each pair of cuts, at zero and beyond
the largest cut visits every outcome. urgency and justification_category are read by
no rule and take one witness each; tests/test_chain.py pins that.

EVALUATION-ONLY BOUNDARY. The certificate reads the recorded canonical step outputs,
the fresh perturbed step outputs, and the structured request pair (base and
perturbed). It never reads prow["oracle_decision"], prow["factor"], or
ChainSweep.sensitivity. Those are inputs to the SCORING, which compares certified
reuse against recompute and the oracle, and a test constructs the certificate through
a guard that raises on any of them. The cone is a field-wise diff of the structured
request, which is what this harness can observe (the paper's Section 4 says a
free-text deployment would have to recover it rather than read it).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Optional

from chain.policy_v2 import JUSTIFICATION_CATEGORIES, JUSTIFICATION_SHARE, decide_v2
from taskgen.schema import CATEGORY_THRESHOLDS, FUZZ_BAND, URGENCY_LEVELS, normalize_category

UNKNOWN_CATEGORY_WITNESS = "unknown_category_witness"
DECISION_INERT_FIELDS = ("urgency", "justification_category")
SCHEMES = ("cert_eq", "cert_safe_valid", "cert_safe_all")


# ---- value classes -------------------------------------------------------------

def _around(cuts: Iterable[float]) -> list[float]:
    """Witnesses at zero, at every cut, between consecutive cuts, and beyond the last."""
    cs = sorted({float(c) for c in cuts if c >= 0.0})
    w = [0.0]
    prev = 0.0
    for c in cs:
        if c > prev:
            w.append((prev + c) / 2.0)
        w.append(c)
        prev = c
    w.append(prev * 2.0 if prev > 0.0 else 1.0)
    return sorted(set(w))


def _ceiling(category) -> Optional[float]:
    c = CATEGORY_THRESHOLDS.get(normalize_category(category))
    return None if c is None else float(c)


def amount_witnesses(category, budget: float) -> list[float]:
    """Every amount class the cascade distinguishes for a fixed category and budget."""
    ceiling = _ceiling(category)
    if ceiling is None:
        return [0.0, 1.0]  # unknown category escalates before amount is read
    thr = min(ceiling, float(budget))
    return _around({float(budget), ceiling, (1.0 - FUZZ_BAND) * thr, JUSTIFICATION_SHARE * thr})


def budget_witnesses(category, amount: float) -> list[float]:
    """Every budget class for a fixed category and amount: over_budget flips at amount, the
    binding threshold flips at the ceiling, and the 0.9 and 0.25 comparisons flip where
    amount equals that share of the budget."""
    ceiling = _ceiling(category)
    if ceiling is None:
        return [0.0, 1.0]
    a = float(amount)
    return _around({a, ceiling, a / (1.0 - FUZZ_BAND), a / JUSTIFICATION_SHARE})


def field_witnesses(field: str, rec: dict) -> list:
    """Every value class for one field, the other fields fixed at rec."""
    if field == "category":
        return list(CATEGORY_THRESHOLDS) + [UNKNOWN_CATEGORY_WITNESS]
    if field in ("data_processing", "security_review", "justification_adequate"):
        return [True, False]
    if field == "urgency":
        return list(URGENCY_LEVELS)
    if field == "justification_category":
        return list(JUSTIFICATION_CATEGORIES)
    if field == "amount":
        return amount_witnesses(rec["category"], rec["budget_remaining"])
    if field == "budget_remaining":
        return budget_witnesses(rec["category"], rec["amount"])
    raise KeyError(field)


def step_witness_records(step: str, rec: dict) -> Iterable[dict]:
    """Records varying every output field of `step` jointly over its classes, the other
    fields fixed at rec. Decision-inert fields keep rec's value."""
    if step == "c_parse":
        for cat in field_witnesses("category", rec):
            ceiling = _ceiling(cat)
            # three budget classes: zero (nothing approves), below the ceiling (budget
            # binds), at or above it (the ceiling binds); unknown category needs one
            budgets = [0.0, 1.0] if ceiling is None else [0.0, ceiling / 2.0, ceiling * 2.0]
            for dp in (True, False):
                for sr in (True, False):
                    for b in budgets:
                        base = dict(rec, category=cat, data_processing=dp, security_review=sr, budget_remaining=b)
                        for a in amount_witnesses(cat, b):
                            yield dict(base, amount=a)
    elif step == "c_justify":
        for adq in (True, False):
            yield dict(rec, justification_adequate=adq)
    else:
        raise KeyError(step)


def joint_witness_records(steps: list[str], rec: dict) -> Iterable[dict]:
    """The product of the candidate steps' classes."""
    if not steps:
        yield rec
        return
    first, rest = steps[0], steps[1:]
    for r in step_witness_records(first, rec):
        yield from joint_witness_records(rest, r)


def decision(rec: dict) -> str:
    return decide_v2(rec).decision.value


# ---- the runtime-observable cone ---------------------------------------------

def observable_cone(base_struct: dict, pert_struct: dict) -> dict[str, bool]:
    """Which steps a runtime could reuse from a field-wise diff of the structured request.

    Mirrors P2 (chain.absorbers_chain.e3_depth) without the perturbation label."""
    from chain.absorbers_chain import FEEDING_STEPS, STEP_OUTPUTS
    changed = {k for k in set(base_struct) | set(pert_struct) if base_struct.get(k) != pert_struct.get(k)}
    return {s: not (changed & STEP_OUTPUTS[s]) for s in FEEDING_STEPS}


# ---- E8 part 1: the decomposition --------------------------------------------

def decompose(sw) -> dict:
    from chain.absorbers_chain import FEEDING_STEPS, STEP_OUTPUTS, SUPPLIER_FIELDS
    per_step: dict[str, dict[str, Any]] = {
        s: {"cells": defaultdict(int), "per_field": {f: {"functional": 0, "correct": 0, "load_bearing": 0} for f in sorted(STEP_OUTPUTS[s])},
            "functional_wrong_loadbearing_requests": []} for s in FEEDING_STEPS}
    eligible = 0
    for bid in sw.bids:
        canon = sw.by_key.get((bid, "canon", "canon"))
        if canon is None:
            continue
        cf = {s: sw.step_fields(canon, s) for s in FEEDING_STEPS}
        if any(f is None for f in cf.values()):
            continue
        repro = sw.repro.get(bid, [])
        if not repro:
            continue
        eligible += 1
        truth = sw.requests[bid].structured_v2()
        rec: dict = {}
        for s in FEEDING_STEPS:
            rec.update(dict(cf[s]))
        for k in SUPPLIER_FIELDS:
            rec[k] = truth[k]
        d0 = decision(rec)
        for s in FEEDING_STEPS:
            canon_f = dict(cf[s])
            samples = [sw.step_fields(r, s) for r in repro]
            for f in sorted(STEP_OUTPUTS[s]):
                v = canon_f[f]
                functional = all(sm is not None and dict(sm).get(f) == v for sm in samples)
                t = truth[f]
                correct = (normalize_category(v) == normalize_category(t)) if f == "category" else (v == t)
                load_bearing = any(decision(dict(rec, **{f: w})) != d0 for w in field_witnesses(f, rec) if w != v)
                ps = per_step[s]
                ps["per_field"][f]["functional"] += functional
                ps["per_field"][f]["correct"] += correct
                ps["per_field"][f]["load_bearing"] += load_bearing
                cell = f"{'functional' if functional else 'varying'}_{'correct' if correct else 'wrong'}_{'loadbearing' if load_bearing else 'inert'}"
                ps["cells"][cell] += 1
                if functional and not correct and load_bearing:
                    # Necessary for the break, not sufficient. Reuse diverges from
                    # recompute only if the fresh output on the perturbed prompt moved
                    # away from the recorded one on a row where the cone reused this
                    # step, which is cross-step sensitivity, not functionality. Count it.
                    cone_rows = moved = 0
                    for prow in sw.perts.get(bid, []):
                        pert = sw.perturbed(bid, prow["salt"]).structured_v2()
                        if not observable_cone(truth, pert)[s]:
                            continue
                        cone_rows += 1
                        moved += (sw.step_fields(prow, s) != cf[s])
                    ps["functional_wrong_loadbearing_requests"].append(
                        {"request": bid, "field": f, "cone_reuse_rows": cone_rows, "fresh_output_moved_rows": moved})
    for s in FEEDING_STEPS:
        ps = per_step[s]
        ps["cells"] = dict(sorted(ps["cells"].items()))
        ps["field_cells_total"] = sum(ps["cells"].values())
        ps["functional_wrong_loadbearing_requests"].sort(key=lambda x: (x["request"], x["field"]))
    return {"eligible_requests": eligible, "per_step": per_step}


# ---- E8 part 2: the certificates ----------------------------------------------

class _Guarded(dict):
    """A perturbation row that raises if the certificate reads an evaluation-only key."""
    FORBIDDEN = ("oracle_decision", "factor")

    def __getitem__(self, k):
        if k in self.FORBIDDEN:
            raise PermissionError(f"certificate read evaluation-only key {k!r}")
        return super().__getitem__(k)

    def get(self, k, default=None):
        if k in self.FORBIDDEN:
            raise PermissionError(f"certificate read evaluation-only key {k!r}")
        return super().get(k, default)


def certify(sw, canon: dict, prow: dict, base_struct: dict, pert_struct: dict, memo: Optional[dict] = None) -> Optional[dict]:
    """Certify reuse on one perturbation row from runtime-observable inputs only.

    Returns None when the row cannot be scored (a fresh step output the cone does not
    cover is invalid, so no full record exists). Otherwise a dict with the candidate
    steps, the decision under reuse, and one boolean per scheme."""
    from chain.absorbers_chain import FEEDING_STEPS, STEP_OUTPUTS, SUPPLIER_FIELDS
    cand = observable_cone(base_struct, pert_struct)
    cf = {s: sw.step_fields(canon, s) for s in FEEDING_STEPS}
    ff = {s: sw.step_fields(prow, s) for s in FEEDING_STEPS}
    steps = [s for s in FEEDING_STEPS if cand[s] and cf[s] is not None]
    if not steps:
        return {"steps": [], "decision": None, "cert_eq": False, "cert_safe_valid": False, "cert_safe_all": False}
    rec: dict = {}
    for s in FEEDING_STEPS:
        f = cf[s] if s in steps else ff[s]
        if f is None:
            return None
        rec.update(dict(f))
    for k in SUPPLIER_FIELDS:
        rec[k] = pert_struct[k]
    d_reuse = decision(rec)
    # the witness set depends only on the candidate steps and the fields they do not
    # output, so rows that share those share the enumeration
    varied = set().union(*(STEP_OUTPUTS[s] for s in steps))
    fixed = tuple(sorted((k, v) for k, v in rec.items() if k not in varied))
    key = (tuple(steps), fixed)
    if memo is not None and key in memo:
        decisions = memo[key]
    else:
        decisions = frozenset(decision(w) for w in joint_witness_records(steps, rec))
        if memo is not None:
            memo[key] = decisions
    cert_eq = decisions == frozenset({d_reuse})
    cert_safe_valid = d_reuse != "approve" or decisions == frozenset({"approve"})
    cert_safe_all = d_reuse != "approve"
    return {"steps": steps, "decision": d_reuse, "classes": len(decisions),
            "cert_eq": cert_eq, "cert_safe_valid": cert_safe_valid, "cert_safe_all": cert_safe_all}


def score_certificates(sw) -> dict:
    from chain.absorbers_chain import FEEDING_STEPS, STEP_OUTPUTS, factor_fields
    from harness.absorbers import _frac
    memo: dict = {}
    st = {sch: {"certified_rows": 0, "step_calls_reused": {s: 0 for s in FEEDING_STEPS}, "decision_changes": 0,
                "decision_changes_fresh_valid": 0, "unsafe_introduced": 0, "unsafe_requests": set(),
                "certified_rows_fresh_invalid": 0, "oracle_correct": 0} for sch in SCHEMES}
    rows = unscoreable = fresh_invalid_rows = 0
    # The residual scenario cert_eq cannot see: a candidate step whose fresh output was
    # invalid, so a certified reuse would have stood in for a fail-safe escalation. Such
    # rows are unscoreable here because the chain's parse fail-safe stops before the
    # justify step runs (chain/agent.py), so the non-candidate step has no output at all.
    unscoreable_candidate_invalid = 0
    cone_mismatch = 0
    candidate_rows = {s: 0 for s in FEEDING_STEPS}
    for bid in sw.bids:
        canon = sw.by_key.get((bid, "canon", "canon"))
        if canon is None or any(sw.step_fields(canon, s) is None for s in FEEDING_STEPS):
            continue
        base_struct = sw.requests[bid].structured_v2()
        for prow in sw.perts[bid]:
            preq = sw.perturbed(bid, prow["salt"])
            pert_struct = preq.structured_v2()
            # the certificate sees a guarded row: reading the oracle or the factor raises
            c = certify(sw, canon, _Guarded(prow), base_struct, pert_struct, memo)
            rows += 1
            # scoring, and only scoring, reads the evaluation-only fields
            p0, od, factor = prow["decision"], prow["oracle_decision"], prow["factor"]
            p2 = {s: not (factor_fields(factor) & STEP_OUTPUTS[s]) for s in FEEDING_STEPS}
            if observable_cone(base_struct, pert_struct) != p2:
                cone_mismatch += 1
            fresh_valid = all(sw.step_fields(prow, s) is not None for s in FEEDING_STEPS)
            fresh_invalid_rows += (not fresh_valid)
            if c is None:
                unscoreable += 1
                cand = observable_cone(base_struct, pert_struct)
                # "invalid" is a step that ran and failed validation (a record with a
                # cache id and valid False); a step the fail-safe never reached is absent,
                # which is a different state and not the residual scenario
                if any(cand[s] and sw.step(prow, s) and not sw.step(prow, s).get("valid") for s in FEEDING_STEPS):
                    unscoreable_candidate_invalid += 1
                continue
            for s in c["steps"]:
                candidate_rows[s] += 1
            for sch in SCHEMES:
                if not c[sch] or not c["steps"]:
                    continue
                x = st[sch]
                x["certified_rows"] += 1
                for s in c["steps"]:
                    x["step_calls_reused"][s] += 1
                dec = c["decision"]
                if dec != p0:
                    x["decision_changes"] += 1
                    if fresh_valid:
                        x["decision_changes_fresh_valid"] += 1
                    if dec == "approve" and od != "approve":
                        x["unsafe_introduced"] += 1
                        x["unsafe_requests"].add(bid)
                if not fresh_valid:
                    x["certified_rows_fresh_invalid"] += 1
                x["oracle_correct"] += (dec == od)
    feeding_calls = rows * len(FEEDING_STEPS)
    out: dict[str, Any] = {"rows": rows, "unscoreable_rows": unscoreable, "rows_fresh_invalid": fresh_invalid_rows,
                           "unscoreable_rows_where_a_candidate_step_was_invalid": unscoreable_candidate_invalid,
                           "candidate_rows_by_step": candidate_rows,
                           "cone_matches_p2_trigger_on_every_row": cone_mismatch == 0, "cone_mismatch_rows": cone_mismatch,
                           "decision_inert_fields": list(DECISION_INERT_FIELDS), "schemes": {}}
    for sch in SCHEMES:
        x = st[sch]
        reused = sum(x["step_calls_reused"].values())
        out["schemes"][sch] = {"certified_rows": x["certified_rows"], "certified_frac": _frac(x["certified_rows"], rows),
                               "step_calls_reused": dict(x["step_calls_reused"]), "calls_saved_frac": _frac(reused, feeding_calls),
                               "decision_changes": x["decision_changes"], "decision_changes_fresh_valid": x["decision_changes_fresh_valid"],
                               "unsafe_introduced": x["unsafe_introduced"], "unsafe_requests": len(x["unsafe_requests"]),
                               "unsafe_request_ids": sorted(x["unsafe_requests"]),
                               "certified_rows_fresh_invalid": x["certified_rows_fresh_invalid"],
                               "oracle_correct_frac": _frac(x["oracle_correct"], x["certified_rows"])}
    return out


def e8_decomposition(sw) -> dict:
    return {"fields": decompose(sw), "certificates": score_certificates(sw)}
