"""Absorbers: does a deterministic consumer absorb an LLM step's nondeterminism?

Every reuse and selective-replay system found in the prior-art scan (VisTrails,
LangGraph node caching, Temporal replay) assumes a step is functional: identical
inputs give identical outputs. An LLM step is the case those systems exclude.
This module measures, offline and from the cached sweeps, three things:

  E2  Functionality at T=0. For A3, supplier-side perturbations leave the parse
      prompt byte-identical (the operator re-renders free text, which never
      states supplier status). Canonical parse vs each such perturbation is a
      pair of calls on identical input at the deployed configuration. How often
      is the output identical, at the text level and at the level of the
      validated record the deterministic core consumes?

  E1  Absorption. Over the ten repeated samples per request at T=0.7, how often
      does the parse vary, and how often does that variance reach the terminal
      decision? A deterministic consumer that stops it is an absorber.

  E3  Reuse policies under perturbation. For each (request, perturbation) the
      sweep recomputed the parse (P0). Three reuse policies decide instead
      whether to reuse the canonical parse: input-hash (P1, what VisTrails and
      LangGraph do), dependency cone (P2, reuse when the perturbed factor is not
      one the parse produces), and oracle-irrelevance (P3, an upper bound that
      cheats by knowing which factors the oracle is sensitive to). For each:
      parse calls saved, terminal divergence from P0, and oracle correctness.

No model call is made. Everything is recomputed from decisions.jsonl, raw/ and
the deterministic generator, perturbation operator and policy core. The output
JSON is byte-stable across runs so it can be committed and regenerated.

Usage:
    python -m harness.absorbers --results results-local
    python -m harness.absorbers --summary          # CSV across every results*/
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.policy_core import decide, oracle, perturbations  # noqa: E402
from harness.validate_results import _json_sha256, load_rows, validate_rows  # noqa: E402
from taskgen.generator import generate  # noqa: E402
from taskgen.schema import FACTORS  # noqa: E402

SCHEMA = "absorbers/v3"
PARSE_VARIANTS = ("A2", "A3")
NO_PARSE_VARIANTS = ("A0", "A1")
SUPPLIER_FACTORS = frozenset({"supplier_approved", "supplier_risk", "supplier_sanctioned"})
DECISIONS = ("approve", "reject", "escalate")
INVALID = "__invalid_parse__"


def _r(x: Optional[float], nd: int = 6) -> Optional[float]:
    return None if x is None else round(float(x), nd)


def _frac(num: int, den: int) -> Optional[float]:
    return None if den == 0 else _r(num / den)


def _rule_of_three(n: int) -> Optional[float]:
    """One-sided 95% upper bound on an event rate after n trials with zero events.

    Reported for the zero-event safety cells. n must be the number of independent
    units, which is the request, not the perturbation row or the prompt pair:
    rows and pairs within a request share a canonical call.
    """
    return None if n <= 0 else _r(3.0 / n)


# --------------------------------------------------------------------------- #
# Sweep index
# --------------------------------------------------------------------------- #

class Sweep:
    """Rows, requests and raw records of one results directory."""

    def __init__(self, results_dir: Path, *, seed: int = 42, check_cache: bool = True):
        self.dir = Path(results_dir)
        self.rows = load_rows(self.dir / "decisions.jsonl")
        self.validation = validate_rows(self.rows, self.dir, seed=seed, check_cache=check_cache)
        self.seed = seed
        self.by_key: dict[tuple, dict] = {}
        self.perts: dict[tuple, list[dict]] = defaultdict(list)
        self.repro: dict[tuple, list[dict]] = defaultdict(list)
        for row in self.rows:
            k = (row["base_request_id"], row["variant"])
            self.by_key[(row["base_request_id"], row["variant"], row["phase"], row["salt"])] = row
            if row["phase"] == "pert":
                self.perts[k].append(row)
            elif row["phase"] == "repro":
                self.repro[k].append(row)
        for k in self.perts:
            self.perts[k].sort(key=lambda r: r["salt"])
        for k in self.repro:
            self.repro[k].sort(key=lambda r: r["salt"])
        self.requests = {req.request_id: req for req in generate(seed)}
        self.variants = sorted({r["variant"] for r in self.rows})
        self.bids = sorted({r["base_request_id"] for r in self.rows})
        self._raw: dict[str, Optional[dict]] = {}
        self._pert_cache: dict[str, dict[str, list]] = {}
        self._oracle_sens: dict[str, frozenset] = {}

    # ---- raw records -------------------------------------------------------
    def raw(self, cache_id: str) -> Optional[dict]:
        if cache_id in self._raw:
            return self._raw[cache_id]
        p = self.dir / "raw" / f"{cache_id}.json"
        rec: Optional[dict]
        try:
            text = p.read_text(encoding="utf-8")
            rec = json.loads(text) if text.strip() else None
        except (FileNotFoundError, json.JSONDecodeError):
            rec = None
        self._raw[cache_id] = rec
        return rec

    @staticmethod
    def parse_cache_id(row: dict) -> Optional[str]:
        ids = row.get("cache_ids") or []
        return ids[0] if ids else None

    def parse_record(self, row: dict) -> Optional[dict]:
        cid = self.parse_cache_id(row)
        return self.raw(cid) if cid else None

    @staticmethod
    def prompt_of(rec: Optional[dict]) -> Optional[tuple[str, str]]:
        """(system, user) if the record stores its call spec, else None (legacy v1)."""
        if not rec or "call_spec" not in rec:
            return None
        cs = rec["call_spec"]
        return (cs.get("system", ""), cs.get("user", ""))

    # ---- parse fields ------------------------------------------------------
    @staticmethod
    def parse_valid(row: dict) -> bool:
        sp = (row.get("audit") or {}).get("structured_parse") or {}
        return bool(sp.get("valid")) and not row.get("call_error")

    @staticmethod
    def llm_fields(row: dict) -> Optional[tuple]:
        """The validated fields the LLM parse produced, as a sorted tuple, or None."""
        audit = row.get("audit") or {}
        sp = audit.get("structured_parse") or {}
        if not sp.get("valid"):
            return None
        rec = audit.get("structured_record") or {}
        src = sp.get("source_by_field") or {}
        return tuple(sorted((k, v) for k, v in rec.items() if src.get(k) == "llm_parse"))

    @staticmethod
    def parse_field_names(row: dict) -> frozenset:
        sp = (row.get("audit") or {}).get("structured_parse") or {}
        src = sp.get("source_by_field") or {}
        return frozenset(k for k, s in src.items() if s == "llm_parse")

    # ---- perturbations and oracle sensitivity ------------------------------
    def perturbed_request(self, bid: str, salt: str):
        """Regenerate the perturbed request for salt 'pert:<factor>:<j>'."""
        if bid not in self._pert_cache:
            self._pert_cache[bid] = perturbations(self.requests[bid])
        _, factor, j = salt.split(":")
        return self._pert_cache[bid][factor][int(j)]

    def oracle_sensitivity(self, bid: str) -> frozenset:
        """Factors for which some perturbation changes the oracle decision."""
        if bid in self._oracle_sens:
            return self._oracle_sens[bid]
        req = self.requests[bid]
        base = oracle(req).decision.value
        if bid not in self._pert_cache:
            self._pert_cache[bid] = perturbations(req)
        sens = set()
        for factor, variants in self._pert_cache[bid].items():
            if any(oracle(v).decision.value != base for v in variants):
                sens.add(factor)
        self._oracle_sens[bid] = frozenset(sens)
        return self._oracle_sens[bid]


# --------------------------------------------------------------------------- #
# E2: functionality at T=0
# --------------------------------------------------------------------------- #

def e2_functionality(sw: Sweep, variant: str) -> dict[str, Any]:
    """Canonical parse vs perturbation parses whose prompt is identical."""
    pairs = 0
    text_identical = 0
    record_identical = 0
    record_comparable = 0
    stored = 0
    by_construction = 0
    raw_missing = 0
    per_factor: dict[str, dict[str, int]] = defaultdict(lambda: {"pairs": 0, "text_identical": 0, "record_identical": 0, "record_comparable": 0})
    requests_seen = 0
    requests_with_mismatch = 0   # cluster structure: pairs share a canonical call
    requests_with_pairs = 0      # the population a canary can draw from
    # How often each request's own identical-prompt calls disagreed with its
    # canonical one, as m/n. A fresh recompute of that request is another draw
    # from the same place, so this is what a canary's per-request fire
    # probability is estimated from, and it is not 0 or 1.
    profile: dict[str, int] = defaultdict(int)

    for bid in sw.bids:
        canon = sw.by_key.get((bid, variant, "canon", "canon"))
        if canon is None:
            continue
        crec = sw.parse_record(canon)
        if crec is None:
            raw_missing += 1
            continue
        requests_seen += 1
        cprompt = sw.prompt_of(crec)
        cfields = sw.llm_fields(canon)
        req_mismatch = 0
        req_comparable = 0
        for prow in sw.perts[(bid, variant)]:
            prec = sw.parse_record(prow)
            if prec is None:
                raw_missing += 1
                continue
            pprompt = sw.prompt_of(prec)
            if cprompt is not None and pprompt is not None:
                identical_input = (cprompt == pprompt)
                stored += 1
            else:
                # Legacy v1 records store no prompt. For A3 the operator invariant
                # (validate_prober) guarantees supplier-side perturbations change
                # only supplier fields, which the free text never states, so the
                # parse prompt is identical by construction. For A2 the prompt
                # carries the supplier block, so it is never identical.
                identical_input = (variant == "A3" and prow["factor"] in SUPPLIER_FACTORS)
                by_construction += 1
            if not identical_input:
                continue
            pairs += 1
            f = prow["factor"]
            per_factor[f]["pairs"] += 1
            if (crec.get("text") or "") == (prec.get("text") or ""):
                text_identical += 1
                per_factor[f]["text_identical"] += 1
            pfields = sw.llm_fields(prow)
            if cfields is not None and pfields is not None:
                record_comparable += 1
                req_comparable += 1
                per_factor[f]["record_comparable"] += 1
                if cfields == pfields:
                    record_identical += 1
                    per_factor[f]["record_identical"] += 1
                else:
                    req_mismatch += 1
        if req_mismatch:
            requests_with_mismatch += 1
        if req_comparable:
            requests_with_pairs += 1
            profile[f"{req_mismatch}/{req_comparable}"] += 1

    method = "stored" if by_construction == 0 and stored > 0 else ("by_construction" if stored == 0 else "mixed")
    # Pairs whose canonical record predates the prompt-storing cache format while the
    # perturbation record does not: those two calls were made under different cache
    # schema versions, and under this paper's own thesis they may straddle deployments.
    mixed_vintage = mixed_vintage_identical = 0
    for bid in sw.bids:
        canon = sw.by_key.get((bid, variant, "canon", "canon"))
        crec = sw.parse_record(canon) if canon else None
        if crec is None or "call_spec" in crec:
            continue
        for prow in sw.perts[(bid, variant)]:
            prec = sw.parse_record(prow)
            if prec is None or "call_spec" not in prec:
                continue
            if variant == "A3" and prow["factor"] in SUPPLIER_FACTORS:
                mixed_vintage += 1
                mixed_vintage_identical += ((prec.get("text") or "") == (crec.get("text") or ""))
    return {
        "requests_seen": requests_seen,
        "identical_input_pairs": pairs,
        "text_identical": text_identical,
        "text_identical_frac": _frac(text_identical, pairs),
        "record_comparable_pairs": record_comparable,
        "record_identical": record_identical,
        "record_identical_frac": _frac(record_identical, record_comparable),
        "prompt_identity_method": method,
        "requests_with_mismatch": requests_with_mismatch,
        "requests_with_comparable_pairs": requests_with_pairs,
        "pair_mismatch_profile": dict(sorted(profile.items())),
        "mixed_vintage_pairs": mixed_vintage,
        "mixed_vintage_text_identical": mixed_vintage_identical,
        "requests_all_identical": requests_seen - requests_with_mismatch,
        "record_identical_upper_bound_request_level": (
            _rule_of_three(requests_seen) if requests_with_mismatch == 0 else None),
        "raw_missing": raw_missing,
        "per_factor": {k: dict(v) for k, v in sorted(per_factor.items())},
    }


# --------------------------------------------------------------------------- #
# E5: what it costs to notice that a configuration stopped being functional
# --------------------------------------------------------------------------- #

CANARY_KS = (1, 2, 3, 5, 10, 20)


def _detection_curve(qs: list[Fraction], ks) -> tuple[dict[str, Optional[float]], Optional[int], Optional[float]]:
    """Probability that at least one of k requests drawn without replacement fires.

    qs[i] is the probability that request i does NOT fire when recomputed once.
    Averaging over the uniformly random k-subsets S,

        P(no fire) = E[ prod_{i in S} q_i ] = e_k(q) / C(R, k),

    where e_k is the elementary symmetric polynomial. Computed exactly in
    rationals, so the curve does not depend on floating-point association order.
    """
    R = len(qs)
    e = [Fraction(0)] * (R + 1)
    e[0] = Fraction(1)
    for q in qs:                       # e_k(q_1..q_j) from e_k(q_1..q_{j-1})
        for k in range(R, 0, -1):
            e[k] = e[k] + e[k - 1] * q
    def at(k: int) -> Optional[float]:
        if k <= 0 or k > R:
            return None
        return _r(float(1 - e[k] / Fraction(math.comb(R, k))))
    curve = {str(k): at(k) for k in ks if k <= R}
    k95 = next((k for k in range(1, R + 1) if (at(k) or 0.0) >= 0.95), None)
    return curve, k95, (at(k95) if k95 is not None else None)


def _profile_to_qs(profile: dict[str, int], *, indicator: bool) -> list[Fraction]:
    """Per-request survival probabilities from the m/n profile E2 recorded.

    With indicator=True a request that ever disagreed is treated as firing with
    certainty, which is the upper bound. Otherwise its own measured mismatch
    frequency m/n is used, which is what one fresh recompute of it would face.
    """
    qs: list[Fraction] = []
    for key, count in profile.items():
        m, n = (int(x) for x in key.split("/"))
        p = Fraction(1 if m else 0) if indicator else Fraction(m, n)
        qs.extend([Fraction(1) - p] * count)
    return qs


def e5_canary(e2: dict[str, Any]) -> dict[str, Any]:
    """Cost of a canary that recomputes k requests and compares against the cache.

    E2 says whether a configuration is functional. It does not say what a
    deployment would have to do to find out, which is the question a system asks.
    A canary recomputes the canonical call for k of the requests whose cached
    record it is about to reuse and compares.

    The unit is the request and not the pair: every pair inside a request shares
    one canonical call, so k pairs from one request are one observation.

    Two models, because they answer different questions and differ by a lot.

    *upper_bound* asks how often k draws touch a request that was ever seen to
    disagree. It is what you get if membership in the measured bad set implies
    detection, and it is an upper bound on any single-recompute canary: the
    events it counts include ones where a fresh call would have agreed.

    *per_recompute* asks what one fresh recompute of each drawn request would
    actually find, using that request's own measured mismatch frequency m/n as
    its fire probability. This is the deployable reading. Its per-request rates
    rest on n draws each (n is 4 here), so they are estimates and the aggregate
    is far better determined than any single one.

    Both are exact given their model over the enumerated population. Neither says
    anything about a population we did not enumerate, and neither models a
    configuration that changes between the collection and the canary.
    """
    R = int(e2.get("requests_with_comparable_pairs") or 0)
    M = int(e2.get("requests_with_mismatch") or 0)
    profile = e2.get("pair_mismatch_profile") or {}
    if R == 0:
        # No identical-prompt pair exists to compare, which is A2's situation: its
        # parse prompt carries the supplier block, so every perturbation changes it.
        # Zero mismatches here means nothing was measured, not that reuse is sound,
        # and a canary has no population to draw from.
        return {"requests": 0, "mismatching_requests": 0, "detectable": None, "defined": False,
                "pair_mismatch_profile": {}, "upper_bound": None, "per_recompute": None}

    out: dict[str, Any] = {"requests": R, "mismatching_requests": M, "detectable": M > 0,
                           "defined": True, "pair_mismatch_profile": dict(sorted(profile.items()))}
    for name, ind in (("upper_bound", True), ("per_recompute", False)):
        qs = _profile_to_qs(profile, indicator=ind)
        curve, k95, at95 = _detection_curve(qs, CANARY_KS)
        # Recomputing every request is the most a canary can do. Below one, the
        # unsoundness is intermittent enough that no sampling rate reaches 95%,
        # and k_for_95 is then absent for a different reason than on a sound
        # configuration, where there is simply nothing to detect.
        ceiling, _, _ = _detection_curve(qs, (R,))
        out[name] = {"detection_prob_at_k": curve, "k_for_95": k95,
                     "k_for_95_frac_of_requests": _frac(k95, R) if k95 is not None else None,
                     "detection_prob_at_k95": at95,
                     "detection_ceiling": ceiling.get(str(R)),
                     "unreachable": M > 0 and k95 is None}
    return out


# --------------------------------------------------------------------------- #
# E1: absorption over repeated samples
# --------------------------------------------------------------------------- #

def e1_absorption(sw: Sweep, variant: str, *, table: Optional[dict] = None) -> dict[str, Any]:
    has_parse = variant in PARSE_VARIANTS
    n = 0
    n_repro_expected = None
    parse_text_varies = 0
    parse_record_varies = 0
    decision_varies = 0
    absorbed = 0          # parse record varies AND decision unanimous
    leaked = 0            # parse record varies AND decision varies
    decision_varies_parse_stable = 0
    raw_missing = 0
    distinct_decisions_hist: dict[int, int] = defaultdict(int)
    distinct_records_hist: dict[int, int] = defaultdict(int)
    unsafe_in_samples = 0  # some sample approves while the oracle does not
    varying_hist: dict[str, int] = defaultdict(int)
    absorbed_by_field: dict[str, int] = defaultdict(int)
    leaked_by_field: dict[str, int] = defaultdict(int)
    by_oracle: dict[str, dict[str, int]] = defaultdict(lambda: {"requests": 0, "decision_varies": 0, "parse_record_varies": 0, "absorbed": 0, "leaked": 0})
    # The claim "on approve-oracle requests the misparse leaked every time" needs
    # the oracle class crossed with which field varied, not the two separately.
    cross: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"absorbed": 0, "leaked": 0}))

    for bid in sw.bids:
        rows = sw.repro.get((bid, variant), [])
        if not rows:
            continue
        n += 1
        n_repro_expected = len(rows)
        decisions = {_fresh_decision(r, table) if has_parse else r["decision"] for r in rows}
        oracle_dec = rows[0]["oracle_decision"]
        if "approve" in decisions and oracle_dec != "approve":
            unsafe_in_samples += 1
        dv = len(decisions) > 1
        distinct_decisions_hist[len(decisions)] += 1
        if dv:
            decision_varies += 1
        # First-call output text across the samples: for A0/A1 that is the whole
        # trajectory, for A2/A3 it is the parse. This is how we know the backend
        # actually sampled at T=0.7 rather than returning one answer ten times.
        texts = set()
        missing = False
        for r in rows:
            rec = sw.parse_record(r)
            if rec is None:
                missing = True
                continue
            texts.add(rec.get("text") or "")
        if missing:
            raw_missing += 1
        if len(texts) > 1:
            parse_text_varies += 1
        if not has_parse:
            by_oracle[oracle_dec]["requests"] += 1
            if dv:
                by_oracle[oracle_dec]["decision_varies"] += 1
            continue
        records = set()
        field_maps = []
        for r in rows:
            f = sw.llm_fields(r)
            records.add(f if f is not None else INVALID)
            field_maps.append(dict(f) if f is not None else None)
        rv = len(records) > 1
        # which llm_parse fields differ across the samples, and the oracle class
        if rv:
            varying = set()
            valid_maps = [m for m in field_maps if m is not None]
            if len(valid_maps) < len(field_maps):
                varying.add(INVALID)
            if valid_maps:
                keys = set().union(*(m.keys() for m in valid_maps))
                for k in keys:
                    if len({json.dumps(m.get(k), sort_keys=True) for m in valid_maps}) > 1:
                        varying.add(k)
            vkey = "+".join(sorted(varying))
            varying_hist[vkey] += 1
            strat = by_oracle[oracle_dec]
            strat["parse_record_varies"] += 1
            if dv:
                strat["leaked"] += 1
                leaked_by_field[vkey] += 1
                cross[oracle_dec][vkey]["leaked"] += 1
            else:
                strat["absorbed"] += 1
                absorbed_by_field[vkey] += 1
                cross[oracle_dec][vkey]["absorbed"] += 1
        by_oracle[oracle_dec]["requests"] += 1
        if dv:
            by_oracle[oracle_dec]["decision_varies"] += 1
        distinct_records_hist[len(records)] += 1
        if rv:
            parse_record_varies += 1
            if dv:
                leaked += 1
            else:
                absorbed += 1
        elif dv:
            decision_varies_parse_stable += 1

    out: dict[str, Any] = {
        "requests": n,
        "n_repro": n_repro_expected,
        "decision_varies": decision_varies,
        "decision_varies_frac": _frac(decision_varies, n),
        "unsafe_in_samples": unsafe_in_samples,
        "unsafe_in_samples_frac": _frac(unsafe_in_samples, n),
        "distinct_decisions_hist": {str(k): v for k, v in sorted(distinct_decisions_hist.items())},
        "has_parse_step": has_parse,
        "output_text_varies": parse_text_varies,
        "output_text_varies_frac": _frac(parse_text_varies, n),
        "raw_missing": raw_missing,
    }
    if has_parse:
        out.update({
            "parse_record_varies": parse_record_varies,
            "parse_record_varies_frac": _frac(parse_record_varies, n),
            "absorbed": absorbed,
            "leaked": leaked,
            "absorption_ratio": _frac(absorbed, parse_record_varies),
            "decision_varies_parse_stable": decision_varies_parse_stable,
            "distinct_records_hist": {str(k): v for k, v in sorted(distinct_records_hist.items())},
            "varying_fields_hist": dict(sorted(varying_hist.items())),
            "absorbed_by_varying_fields": dict(sorted(absorbed_by_field.items())),
            "leaked_by_varying_fields": dict(sorted(leaked_by_field.items())),
        })
    out["by_oracle_decision"] = {k: dict(v) for k, v in sorted(by_oracle.items())}
    if has_parse:
        out["by_oracle_and_varying_fields"] = {
            oc: {f: dict(c) for f, c in sorted(fields.items())} for oc, fields in sorted(cross.items())}
    return out


# --------------------------------------------------------------------------- #
# E3: reuse policies under perturbation
# --------------------------------------------------------------------------- #

POLICIES = ("P0_recompute", "P1_input_hash", "P2_dependency_cone", "P3_oracle_irrelevance")

# A second deterministic consumer. The frozen normaliser lowercases and joins on
# underscores and nothing else, so an LLM that answers "hardware" for the schema's
# "it_hardware" lands outside CATEGORY_THRESHOLDS and the core escalates. These two
# entries are the ones the paper named in Limitations on 2026-09-03, before this
# analysis existed; no entry is added after the fact. It is applied where the core
# reads the record and nowhere earlier, so every step-level measurement (E2) is
# untouched by construction, and it lives here rather than in taskgen/schema.py
# because changing normalize_category would regenerate a different decisions.jsonl
# and trip the hash gate that keeps the frozen sweeps frozen.
ALIAS_NORMALISER = {"hardware": "it_hardware", "software": "software_license"}


def apply_normaliser(rec: dict, table: Optional[dict]) -> dict:
    """Copy of rec with category mapped through table on exact match; None is identity."""
    if not table:
        return rec
    out = dict(rec)
    c = out.get("category")
    if isinstance(c, str) and c in table:
        out["category"] = table[c]
    return out


def _fresh_decision(row: dict, table: Optional[dict]) -> str:
    """The decision recompute reaches on this row under the given normaliser.

    With table None this is the recorded decision. Otherwise it is decide() over the
    row's own validated record with the alias applied, which is faithful to the
    recorded decision when the alias does not fire (checked by a test over every
    valid row of every sweep). Rows whose parse did not validate keep their recorded
    fail-safe decision: there is no record for the alias to act on.
    """
    if not table:
        return row["decision"]
    audit = row.get("audit") or {}
    if not (audit.get("structured_parse") or {}).get("valid"):
        return row["decision"]
    rec = audit.get("structured_record") or {}
    if not rec:
        return row["decision"]
    return decide(apply_normaliser(rec, table)).decision.value


def _reused_decision(sw: Sweep, variant: str, canon: dict, preq, *, table: Optional[dict] = None) -> Optional[str]:
    """Decision the core would reach reusing the canonical parse on the perturbed request."""
    audit = canon.get("audit") or {}
    rec = dict(audit.get("structured_record") or {})
    src = (audit.get("structured_parse") or {}).get("source_by_field") or {}
    if not rec:
        return None
    merged = {}
    pstruct = preq.structured()
    for k in FACTORS:
        if src.get(k) == "llm_parse":
            merged[k] = rec.get(k)          # stale if the perturbation touched it
        else:
            merged[k] = pstruct[k]          # authoritative, follows the perturbation
    return decide(apply_normaliser(merged, table)).decision.value


def e3_reuse(sw: Sweep, variant: str, *, table: Optional[dict] = None) -> dict[str, Any]:
    eligible_requests = 0
    alias_rows = 0          # perturbation rows whose recorded category is a table key
    rows_changed = 0        # rows whose recompute decision differs from the recorded one
    alias_wrong_known = 0   # alias rows whose alias target is not the true category
    skipped_invalid_canon = 0
    pert_rows = 0
    sha_mismatch = 0
    raw_missing = 0
    latency_sum = 0.0
    tokens_sum = 0
    cost_n = 0
    stats = {p: {"reused": 0, "diverged": 0, "diverged_given_reuse": 0, "oracle_correct": 0,
                 "unsafe_introduced": 0, "unsafe_removed": 0, "latency_ms_saved": 0.0, "tokens_saved": 0}
             for p in POLICIES}
    per_factor: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: {p: {"rows": 0, "reused": 0, "diverged": 0} for p in POLICIES})
    p1_keys: set[tuple] = set()
    p2_keys: set[tuple] = set()
    p3_keys: set[tuple] = set()

    for bid in sw.bids:
        canon = sw.by_key.get((bid, variant, "canon", "canon"))
        if canon is None:
            continue
        if not sw.parse_valid(canon):
            skipped_invalid_canon += 1
            continue
        eligible_requests += 1
        parse_fields = sw.parse_field_names(canon)
        crec = sw.parse_record(canon)
        cprompt = sw.prompt_of(crec)
        sens = sw.oracle_sensitivity(bid)
        for prow in sw.perts[(bid, variant)]:
            factor = prow["factor"]
            preq = sw.perturbed_request(bid, prow["salt"])
            if _json_sha256(preq.to_json()) != prow["input_sha256"]:
                sha_mismatch += 1
                continue
            pert_rows += 1
            key = (bid, prow["salt"])
            p0 = _fresh_decision(prow, table)
            if p0 != prow["decision"]:
                rows_changed += 1
            if table:
                pcat = ((prow.get("audit") or {}).get("structured_record") or {}).get("category")
                if isinstance(pcat, str) and pcat in table:
                    alias_rows += 1
                    if table[pcat] != preq.category:
                        alias_wrong_known += 1
            odec = prow["oracle_decision"]
            reused = _reused_decision(sw, variant, canon, preq, table=table)
            prec = sw.parse_record(prow)
            if prec is None:
                raw_missing += 1
                lat, tok = None, None
            else:
                lat = prec.get("latency_ms")
                tok = (prec.get("tokens_in") or 0) + (prec.get("tokens_out") or 0)
                if lat is not None:
                    latency_sum += float(lat); tokens_sum += tok; cost_n += 1

            # policy triggers
            pprompt = sw.prompt_of(prec) if prec else None
            if cprompt is not None and pprompt is not None:
                p1 = (cprompt == pprompt)
            else:
                p1 = (variant == "A3" and factor in SUPPLIER_FACTORS)
            p2 = factor not in parse_fields
            p3 = factor not in sens
            fire = {"P0_recompute": False, "P1_input_hash": p1, "P2_dependency_cone": p2, "P3_oracle_irrelevance": p3}
            if p1: p1_keys.add(key)
            if p2: p2_keys.add(key)
            if p3: p3_keys.add(key)

            for pol in POLICIES:
                st = stats[pol]
                pf = per_factor[factor][pol]
                pf["rows"] += 1
                if fire[pol] and reused is not None:
                    dec = reused
                    st["reused"] += 1
                    pf["reused"] += 1
                    if lat is not None:
                        st["latency_ms_saved"] += float(lat); st["tokens_saved"] += tok
                    if dec != p0:
                        st["diverged"] += 1; st["diverged_given_reuse"] += 1; pf["diverged"] += 1
                        if dec == "approve" and odec != "approve":
                            st["unsafe_introduced"] += 1
                        if p0 == "approve" and odec != "approve" and dec != "approve":
                            st["unsafe_removed"] += 1
                else:
                    dec = p0
                if dec == odec:
                    st["oracle_correct"] += 1

    out: dict[str, Any] = {
        "eligible_requests": eligible_requests,
        "skipped_invalid_canon": skipped_invalid_canon,
        "pert_rows": pert_rows,
        "input_sha_mismatch": sha_mismatch,
        "raw_missing": raw_missing,
        "parse_cost_mean_latency_ms": _r(latency_sum / cost_n) if cost_n else None,
        "parse_cost_mean_tokens": _r(tokens_sum / cost_n) if cost_n else None,
        "nesting_P1_subset_P2": p1_keys <= p2_keys,
        "nesting_P2_subset_P3": p2_keys <= p3_keys,
        "normaliser": dict(table) if table else None,
        "alias_rows": alias_rows,
        "rows_changed": rows_changed,
        "alias_wrong_known": alias_wrong_known,
        "policies": {},
        "per_factor": {},
    }
    for pol in POLICIES:
        st = stats[pol]
        out["policies"][pol] = {
            "reused": st["reused"],
            "unsafe_upper_bound_request_level": (
                _rule_of_three(eligible_requests) if st["unsafe_introduced"] == 0 else None),
            "calls_saved_frac": _frac(st["reused"], pert_rows),
            "diverged": st["diverged"],
            "divergence_frac": _frac(st["diverged"], pert_rows),
            "divergence_given_reuse_frac": _frac(st["diverged_given_reuse"], st["reused"]),
            "oracle_correct_frac": _frac(st["oracle_correct"], pert_rows),
            "unsafe_introduced": st["unsafe_introduced"],
            "unsafe_removed": st["unsafe_removed"],
            "latency_ms_saved_total": _r(st["latency_ms_saved"], 1),
            "tokens_saved_total": st["tokens_saved"],
        }
    for factor in sorted(per_factor):
        out["per_factor"][factor] = {pol: dict(v) for pol, v in per_factor[factor].items()}
    return out


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def analyse(results_dir: Path, *, seed: int = 42, check_cache: bool = True) -> dict[str, Any]:
    sw = Sweep(results_dir, seed=seed, check_cache=check_cache)
    meta_path = results_dir / "metrics.json"
    meta = json.loads(meta_path.read_text())["metadata"] if meta_path.exists() else {}
    out: dict[str, Any] = {
        "schema": SCHEMA,
        "results_dir": results_dir.name,
        "backend_label": meta.get("backend_label"),
        "model": meta.get("model"),
        "decisions_sha256": meta.get("decisions_sha256"),
        "policy_sha256": meta.get("policy_sha256"),
        "seed": seed,
        "n_requests": len(sw.bids),
        "variants": sw.variants,
        "E2_functionality_T0": {},
        "E1_absorption": {},
        "E3_reuse": {},
        "E5_canary": {},
    }
    for v in sw.variants:
        out["E1_absorption"][v] = e1_absorption(sw, v)
        if v in PARSE_VARIANTS:
            out["E2_functionality_T0"][v] = e2_functionality(sw, v)
            out["E3_reuse"][v] = e3_reuse(sw, v)
            out["E5_canary"][v] = e5_canary(out["E2_functionality_T0"][v])
    # The same analysis under a second deterministic consumer. E2 compares records
    # before any consumer reads them, so it has no alias variant by construction.
    out["E6_alias_normaliser"] = {
        "table": dict(ALIAS_NORMALISER),
        "E1_absorption": {v: e1_absorption(sw, v, table=ALIAS_NORMALISER) for v in sw.variants if v in PARSE_VARIANTS},
        "E3_reuse": {v: e3_reuse(sw, v, table=ALIAS_NORMALISER) for v in sw.variants if v in PARSE_VARIANTS},
    }
    return out


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


SUMMARY_COLUMNS = [
    "results_dir", "backend_label", "model", "variant",
    "e2_pairs", "e2_text_identical_frac", "e2_record_identical_frac", "e2_method",
    "e1_parse_record_varies_frac", "e1_decision_varies_frac", "e1_absorption_ratio",
    "e3_pert_rows", "e3_P1_saved_frac", "e3_P1_div_frac", "e3_P2_saved_frac", "e3_P2_div_frac",
    "e3_P3_saved_frac", "e3_P3_div_frac", "e3_P0_oracle_correct", "e3_P2_oracle_correct",
    "parse_cost_ms",
]


def summary_rows(analyses: list[dict]) -> list[dict]:
    rows = []
    for a in analyses:
        for v in a["variants"]:
            e1 = a["E1_absorption"].get(v, {})
            e2 = a["E2_functionality_T0"].get(v, {})
            e3 = a["E3_reuse"].get(v, {})
            pol = e3.get("policies", {})
            rows.append({
                "results_dir": a["results_dir"], "backend_label": a["backend_label"], "model": a["model"], "variant": v,
                "e2_pairs": e2.get("identical_input_pairs"), "e2_text_identical_frac": e2.get("text_identical_frac"),
                "e2_record_identical_frac": e2.get("record_identical_frac"), "e2_method": e2.get("prompt_identity_method"),
                "e1_parse_record_varies_frac": e1.get("parse_record_varies_frac"),
                "e1_decision_varies_frac": e1.get("decision_varies_frac"), "e1_absorption_ratio": e1.get("absorption_ratio"),
                "e3_pert_rows": e3.get("pert_rows"),
                "e3_P1_saved_frac": pol.get("P1_input_hash", {}).get("calls_saved_frac"),
                "e3_P1_div_frac": pol.get("P1_input_hash", {}).get("divergence_frac"),
                "e3_P2_saved_frac": pol.get("P2_dependency_cone", {}).get("calls_saved_frac"),
                "e3_P2_div_frac": pol.get("P2_dependency_cone", {}).get("divergence_frac"),
                "e3_P3_saved_frac": pol.get("P3_oracle_irrelevance", {}).get("calls_saved_frac"),
                "e3_P3_div_frac": pol.get("P3_oracle_irrelevance", {}).get("divergence_frac"),
                "e3_P0_oracle_correct": pol.get("P0_recompute", {}).get("oracle_correct_frac"),
                "e3_P2_oracle_correct": pol.get("P2_dependency_cone", {}).get("oracle_correct_frac"),
                "parse_cost_ms": e3.get("parse_cost_mean_latency_ms"),
            })
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, help="one results directory to analyse")
    ap.add_argument("--summary", action="store_true", help="write results/absorbers_summary.csv over every results*/absorbers.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-check-cache", action="store_true", help="skip active-cache validation (diagnosis only)")
    args = ap.parse_args(argv)

    if args.results:
        rd = args.results if args.results.is_absolute() else (ROOT / args.results)
        out = analyse(rd, seed=args.seed, check_cache=not args.no_check_cache)
        write_json(rd / "absorbers.json", out)
        print(f"wrote {rd / 'absorbers.json'}")
        for v in out["variants"]:
            e1 = out["E1_absorption"][v]
            line = f"  {v}: decision_varies={e1['decision_varies_frac']}"
            if v in out["E2_functionality_T0"]:
                e2 = out["E2_functionality_T0"][v]; e3 = out["E3_reuse"][v]["policies"]
                line += (f" | E2 pairs={e2['identical_input_pairs']} text_id={e2['text_identical_frac']} rec_id={e2['record_identical_frac']} ({e2['prompt_identity_method']})"
                         f" | E1 parse_varies={e1['parse_record_varies_frac']} absorption={e1['absorption_ratio']}"
                         f" | E3 P2 saved={e3['P2_dependency_cone']['calls_saved_frac']} div={e3['P2_dependency_cone']['divergence_frac']}")
            print(line)

    if args.summary:
        analyses = []
        for p in sorted(ROOT.glob("results*/absorbers.json")):
            analyses.append(json.loads(p.read_text()))
        rows = summary_rows(analyses)
        out_csv = ROOT / "results" / "absorbers_summary.csv"
        with out_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"wrote {out_csv} ({len(rows)} rows from {len(analyses)} sweeps)")
    if not args.results and not args.summary:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
