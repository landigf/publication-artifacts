"""Invariants for the chain subpackage that need no model call.

    PYTHONPATH=. ../.venv/bin/python -m unittest tests.test_chain
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.policy_core import POLICY_TEXT, decide, oracle, perturbations  # noqa: E402
from chain.policy_v2 import POLICY_TEXT_V2, POLICY_V2_SHA256, decide_v2, oracle_v2  # noqa: E402
from chain.taskgen import ADEQUATE, INADEQUATE, ChainRequest, chain_perturbations, generate_chain  # noqa: E402
from harness.runner import _json_sha256  # noqa: E402
from taskgen.generator import generate  # noqa: E402

# ---- chain sweeps on disk, analysed once per check_cache flag ------------------
CHAIN_DIRS = [d for d in sorted(ROOT.glob("results-chain-*")) if (d / "decisions.jsonl").exists()]
_CHAIN_CACHE: dict = {}


def chain_analyses(*, check_cache: bool = False) -> dict:
    """{dir name: analyse(dir)} over every chain sweep that passes the gate; SkipTest if none.

    check_cache=True walks the raw cache and costs about half a second per sweep; it is the
    setting main() wrote the committed files with, and chain/validate.py records the flag in
    validation.cache_checked, so a byte comparison needs it.
    """
    if not CHAIN_DIRS:
        raise unittest.SkipTest("no chain sweep collected")
    if check_cache not in _CHAIN_CACHE:
        from chain.absorbers_chain import analyse
        out = {}
        for d in CHAIN_DIRS:
            try:
                out[d.name] = analyse(d, check_cache=check_cache)
            except SystemExit:
                continue   # an artifact that fails the gate is not analysed
        _CHAIN_CACHE[check_cache] = out
    if not _CHAIN_CACHE[check_cache]:
        raise unittest.SkipTest("no chain sweep passes the gate")
    return _CHAIN_CACHE[check_cache]


def _leaf_diff(a, b, path="", out=None):
    """Every leaf at which two parsed JSON values differ, as (path, committed, fresh)."""
    out = [] if out is None else out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append((f"{path}/{k}", "<absent in committed>", b[k]))
            elif k not in b:
                out.append((f"{path}/{k}", a[k], "<absent in fresh>"))
            else:
                _leaf_diff(a[k], b[k], f"{path}/{k}", out)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            _leaf_diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append((path, a, b))
    return out


class PolicyV2Tests(unittest.TestCase):
    def test_v2_text_extends_v1_by_exactly_one_rule(self):
        self.assertIn("7b.", POLICY_TEXT_V2)
        self.assertNotIn("7b.", POLICY_TEXT)
        self.assertIn("(v2)", POLICY_TEXT_V2)
        self.assertEqual(len(POLICY_V2_SHA256), 64)

    def test_v2_equals_v1_whenever_justification_is_adequate(self):
        n = 0
        for r in generate_chain(42):
            for grp in [[r]] + list(chain_perturbations(r).values()):
                for v in grp:
                    s = v.structured_v2(); s["justification_adequate"] = True
                    self.assertEqual(decide_v2(s).decision, decide(v.structured()).decision, v.request_id)
                    n += 1
        self.assertGreater(n, 1000)

    def test_new_rule_only_fires_after_the_first_seven(self):
        for r in generate_chain(42):
            o1, o2 = oracle(r), oracle_v2(r)
            if o2.firing_rule == "inadequate_justification":
                self.assertIn(o1.firing_rule, ("clean_approve", "near_threshold_fuzzy"), r.request_id)
                self.assertFalse(r.justification_adequate)

    def test_invalid_justify_fields_fail_safe(self):
        r = generate_chain(42)[0]
        s = r.structured_v2(); s["justification_adequate"] = "yes"
        self.assertEqual(decide_v2(s).firing_rule, "invalid_input")
        s = r.structured_v2(); s["justification_category"] = "banana"
        self.assertEqual(decide_v2(s).firing_rule, "invalid_input")


class ChainRequestTests(unittest.TestCase):
    def test_frozen_requests_are_untouched(self):
        base = generate(42); chain = generate_chain(42)
        self.assertEqual(len(base), 65); self.assertEqual(len(chain), 65)
        for b, c in zip(base, chain):
            self.assertEqual(b.request_id, c.request_id)
            self.assertTrue(c.free_text.startswith(b.free_text))
            self.assertTrue(c.free_text.endswith(c.justification_text))
            self.assertEqual(b.structured(), c.structured())

    def test_planted_justification_is_consistent(self):
        for c in generate_chain(42):
            if c.justification_adequate:
                self.assertIn(c.justification_text, ADEQUATE[c.justification_category])
            else:
                self.assertEqual(c.justification_category, "none")
                self.assertIn(c.justification_text, INADEQUATE)

    def test_generation_is_seed_deterministic(self):
        a = [(r.request_id, r.justification_adequate, r.justification_text) for r in generate_chain(42)]
        b = [(r.request_id, r.justification_adequate, r.justification_text) for r in generate_chain(42)]
        self.assertEqual(a, b)
        self.assertNotEqual(a, [(r.request_id, r.justification_adequate, r.justification_text) for r in generate_chain(43)])


class ChainPerturbationTests(unittest.TestCase):
    def test_ten_groups_and_sixteen_rows_per_request(self):
        for r in generate_chain(42):
            g = chain_perturbations(r)
            self.assertEqual(set(g), set(perturbations(r)) | {"justification"}, r.request_id)
            self.assertEqual(sum(len(v) for v in g.values()), len([x for v in perturbations(r).values() for x in v]) + 1)

    def test_every_perturbed_request_keeps_justification_and_injection(self):
        for r in generate_chain(42):
            for factor, vs in chain_perturbations(r).items():
                for v in vs:
                    self.assertIsInstance(v, ChainRequest)
                    self.assertTrue(v.free_text.endswith(v.justification_text), (r.request_id, factor))
                    self.assertEqual(v.injection, r.injection)
                    if factor != "justification":
                        self.assertEqual(v.justification_text, r.justification_text)
                        self.assertEqual(v.justification_adequate, r.justification_adequate)

    def test_justification_toggle_changes_only_justification(self):
        for r in generate_chain(42):
            t = chain_perturbations(r)["justification"][0]
            self.assertEqual(t.structured(), r.structured())
            self.assertNotEqual(t.justification_adequate, r.justification_adequate)
            self.assertEqual(t.free_text[: len(t.free_text) - len(t.justification_text) - 1],
                             r.free_text[: len(r.free_text) - len(r.justification_text) - 1])

    def test_input_hash_distinguishes_every_row_of_the_grid(self):
        seen = set()
        for r in generate_chain(42):
            seen.add(_json_sha256(r.to_json()))
            for vs in chain_perturbations(r).values():
                for v in vs:
                    seen.add(_json_sha256(v.to_json()))
        # category has two or three alternatives depending on the current category,
        # so the base operator yields 964 rows over 65 requests, plus 65 toggles.
        n_base = sum(len(v) for r in generate(42) for v in perturbations(r).values())
        self.assertEqual(n_base, 964)
        self.assertEqual(len(seen), 65 + n_base + 65)



class DependencyConeVocabularyTests(unittest.TestCase):
    """Regression for a real bug found on 2026-09-03.

    The perturbation operator names factors; a step's output set names record
    fields. They coincide for the nine base factors and not for the justification
    toggle, which changes two fields at once. Comparing the two vocabularies
    directly made the dependency cone reuse the justify step on exactly the
    perturbation that changes what that step produces, which showed up as five
    spurious unsafe approvals.
    """

    def test_every_factor_maps_to_real_record_fields(self):
        from chain.absorbers_chain import JUSTIFY_FIELDS, PARSE_FIELDS, SUPPLIER_FIELDS, factor_fields
        known = PARSE_FIELDS | SUPPLIER_FIELDS | JUSTIFY_FIELDS
        req = generate_chain(42)[0]
        for factor in chain_perturbations(req):
            fields = factor_fields(factor)
            self.assertTrue(fields, factor)
            self.assertTrue(fields <= known, f"{factor} -> {fields - known} are not record fields")

    def test_the_justification_toggle_is_inside_the_justify_step_output(self):
        from chain.absorbers_chain import JUSTIFY_FIELDS, STEP_OUTPUTS, factor_fields
        self.assertEqual(factor_fields("justification"), JUSTIFY_FIELDS)
        # the cone must NOT reuse c_justify when the justification is perturbed
        self.assertTrue(factor_fields("justification") & STEP_OUTPUTS["c_justify"])
        # and it MUST still reuse c_parse, which does not produce those fields
        self.assertFalse(factor_fields("justification") & STEP_OUTPUTS["c_parse"])

    def test_base_factors_are_their_own_field(self):
        from chain.absorbers_chain import factor_fields
        for f in ("amount", "category", "supplier_risk", "urgency", "security_review"):
            self.assertEqual(factor_fields(f), frozenset({f}))

    def test_every_perturbation_actually_changes_the_fields_it_claims(self):
        from chain.absorbers_chain import factor_fields
        for req in generate_chain(42)[:12]:
            base = req.structured_v2()
            for factor, variants in chain_perturbations(req).items():
                claimed = factor_fields(factor)
                for v in variants:
                    changed = {k for k, val in v.structured_v2().items() if base.get(k) != val}
                    self.assertTrue(changed <= claimed,
                                    f"{req.request_id}/{factor} changed {changed - claimed} beyond {claimed}")


class ChainCanaryPopulationTests(unittest.TestCase):
    """The chain's E2 must report the request-level population the canary draws from.

    Skipped when no chain sweep is on disk, since the collection is optional.
    """

    @classmethod
    def setUpClass(cls):
        cls.analyses = chain_analyses()

    def test_request_counts_bracket_the_pair_counts(self):
        for name, a in self.analyses.items():
            for step, e2 in a["E2_functionality_T0"].items():
                R = e2["requests_with_comparable_pairs"]
                M = e2["requests_with_mismatch"]
                self.assertLessEqual(M, R, (name, step))
                self.assertLessEqual(R, a["n_requests"], (name, step))
                self.assertLessEqual(R, e2["record_comparable_pairs"], (name, step))

    def test_a_mismatching_request_exists_exactly_when_a_pair_disagrees(self):
        for name, a in self.analyses.items():
            for step, e2 in a["E2_functionality_T0"].items():
                if e2["record_comparable_pairs"] == 0:
                    continue
                disagreed = e2["record_identical_frac"] < 1.0
                self.assertEqual(e2["requests_with_mismatch"] > 0, disagreed, (name, step))

    def test_canary_is_defined_for_every_feeding_step(self):
        for name, a in self.analyses.items():
            for step, c in a["E5_canary"].items():
                self.assertTrue(c["defined"], (name, step))
                self.assertEqual(c["requests"], a["E2_functionality_T0"][step]["requests_with_comparable_pairs"])


class ChainAliasTests(unittest.TestCase):
    """E6 at depth: the alias moves oracle correctness and must not move the break."""

    @classmethod
    def setUpClass(cls):
        cls.analyses = chain_analyses()

    def test_recompute_path_is_faithful_to_the_recorded_decision(self):
        from chain.policy_v2 import decide_v2
        from chain.absorbers_chain import ChainSweep, FEEDING_STEPS, SUPPLIER_FIELDS
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[1]
        for name in self.analyses:
            sw = ChainSweep(root / name, check_cache=False)
            for bid in sw.bids:
                req = sw.requests[bid]
                for r in [sw.by_key.get((bid, "canon", "canon"))] + sw.perts[bid] + sw.repro.get(bid, []):
                    if r is None:
                        continue
                    fields = [sw.step_fields(r, s) for s in FEEDING_STEPS]
                    if any(f is None for f in fields):
                        continue
                    rec = {}
                    for f in fields:
                        rec.update(dict(f))
                    pstruct = (sw.perturbed(bid, r["salt"]) if r["phase"] == "pert" else req).structured_v2()
                    for k in SUPPLIER_FIELDS:
                        rec[k] = pstruct[k]
                    self.assertEqual(decide_v2(rec).decision.value, r["decision"], (name, bid, r["salt"]))

    def test_alias_is_an_alias_and_touches_only_alias_rows(self):
        for name, a in self.analyses.items():
            e3 = a["E6_alias_normaliser"]["E3_reuse"]
            self.assertEqual(e3["alias_wrong_known"], 0, name)
            self.assertLessEqual(e3["rows_changed"], e3["alias_rows"], name)

    def test_headline_break_survives_the_alias(self):
        for name, a in self.analyses.items():
            frozen = a["E3_reuse"]["policies"]["P2_dependency_cone"]["unsafe_introduced"]
            alias = a["E6_alias_normaliser"]["E3_reuse"]["policies"]["P2_dependency_cone"]["unsafe_introduced"]
            self.assertEqual(alias, frozen, name)

    def test_oracle_correctness_is_monotone_non_decreasing_under_the_alias(self):
        for name, a in self.analyses.items():
            for pol, x in a["E6_alias_normaliser"]["E3_reuse"]["policies"].items():
                self.assertGreaterEqual(x["oracle_correct_frac"], a["E3_reuse"]["policies"][pol]["oracle_correct_frac"], (name, pol))


class ChainRegressionTests(unittest.TestCase):
    """The three committed absorbers_chain.json files, and the invariants the depth claims rest on.

    The headline of the Absorbers paper lives in these files, and until 2026-09-05 nothing
    regenerated them under test. Mirrors test_absorbers.test_committed_json_matches_regeneration.
    """

    @classmethod
    def setUpClass(cls):
        cls.analyses = chain_analyses()

    def test_committed_chain_json_matches_regeneration(self):
        # In memory, no temp path. Value differences and byte differences are reported apart:
        # the first means the analysis moved, the second means the dumps arguments or a hand
        # edit did.
        from chain.absorbers_chain import analyse
        for d in CHAIN_DIRS:
            committed = d / "absorbers_chain.json"
            if not committed.exists():
                continue
            with self.subTest(chain=d.name):
                fresh = analyse(d, check_cache=True)
                text = committed.read_text()
                relational = _leaf_diff(json.loads(text), fresh)
                rendered = json.dumps(fresh, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n"
                drift = [] if relational or text == rendered else [
                    f"line {i}: committed {a!r} != fresh {b!r}"
                    for i, (a, b) in enumerate(zip(text.splitlines(), rendered.splitlines()), 1) if a != b][:5]
                msg = []
                if relational:
                    msg.append(f"{d.name}: {len(relational)} relational mismatch(es):\n"
                               + "\n".join(f"  {p}: committed={a!r} fresh={b!r}" for p, a, b in relational[:20]))
                if drift:
                    msg.append(f"{d.name}: values equal, serialisation drift only:\n" + "\n".join(f"  {x}" for x in drift))
                self.assertFalse(msg, "\n".join(msg))

    def test_p0_never_reuses_and_never_diverges(self):
        for name, a in self.analyses.items():
            for label, e3 in (("frozen", a["E3_reuse"]), ("alias", a["E6_alias_normaliser"]["E3_reuse"])):
                p0 = e3["policies"]["P0_recompute"]
                self.assertEqual(p0["calls_triggered_frac"], 0.0, (name, label))
                self.assertEqual(p0["calls_saved_frac"], 0.0, (name, label))
                self.assertEqual(sum(p0["step_calls_triggered"].values()), 0, (name, label))
                self.assertEqual(p0["diverged"], 0, (name, label))
                self.assertEqual(p0["unsafe_introduced"], 0, (name, label))
                self.assertEqual(p0["unsafe_requests"], 0, (name, label))

    def test_e7_frozen_share_reproduces_e3(self):
        # share_sensitivity rescores the same rows from the same records and its docstring
        # promises the frozen row reproduces E3. It skips a row whose fresh feeding-step record
        # is invalid, where e3_depth keeps the row with its recorded decision, so the row counts
        # add up only through the skip count. p0_unsafe_absolute equals E3's as long as no
        # skipped row was recorded unsafe, which is asserted here rather than assumed.
        for name, a in self.analyses.items():
            e3, e7 = a["E3_reuse"], a["E7_share_sensitivity"]
            key = f"{e7['frozen_share']:.2f}"
            self.assertIn(key, e7["by_share"], name)
            b = e7["by_share"][key]
            p0, p2 = e3["policies"]["P0_recompute"], e3["policies"]["P2_dependency_cone"]
            self.assertEqual(b["p2_unsafe_introduced"], p2["unsafe_introduced"], name)
            self.assertEqual(b["p2_unsafe_requests"], p2["unsafe_requests"], name)
            self.assertEqual(b["p2_diverged"], p2["diverged"], name)
            self.assertEqual(b["p0_unsafe_absolute"], p0["unsafe_absolute"], name)
            self.assertEqual(b["rows"] + b["rows_skipped_invalid_fresh_step"], e3["pert_rows"], name)

    def test_p1_subset_of_p2_at_depth(self):
        for name, a in self.analyses.items():
            e3 = a["E3_reuse"]
            self.assertTrue(e3["nesting_P1_subset_P2"], name)
            for s, n in e3["nesting_by_step"].items():
                self.assertTrue(n["P1_subset_P2"], (name, s))
                self.assertLessEqual(e3["policies"]["P1_input_hash"]["step_calls_triggered"][s],
                                     e3["policies"]["P2_dependency_cone"]["step_calls_triggered"][s], (name, s))
            # Recorded, not asserted either way: the cone is not nested in oracle irrelevance at
            # depth. On the three chains collected so far it is False on both steps, and on
            # gemma3 P3 introduces more unsafe approvals than P2 (closed-ledger L72). A future
            # sweep may go either way, so the test pins only that the flag exists and is boolean.
            self.assertIn(e3["nesting_P2_subset_P3"], (True, False), name)


class ChainCertificateTests(unittest.TestCase):
    """E8: the value-class enumeration is exact, the certificate reads no oracle data, and
    the guard's own invariants hold on every frozen row."""

    @classmethod
    def setUpClass(cls):
        cls.analyses = chain_analyses()

    def _canonical_records(self, sw):
        from chain.absorbers_chain import FEEDING_STEPS, SUPPLIER_FIELDS
        out = []
        for bid in sw.bids:
            canon = sw.by_key.get((bid, "canon", "canon"))
            if canon is None:
                continue
            fs = [sw.step_fields(canon, s) for s in FEEDING_STEPS]
            if any(f is None for f in fs):
                continue
            rec = {}
            for f in fs:
                rec.update(dict(f))
            truth = sw.requests[bid].structured_v2()
            for k in SUPPLIER_FIELDS:
                rec[k] = truth[k]
            out.append((bid, rec))
        return out

    def test_decision_inert_fields_are_inert(self):
        # urgency and justification_category are read by no rule; the enumeration takes
        # one witness each, which is sound only if this holds on every record.
        from chain.absorbers_chain import ChainSweep
        from chain.certificates import DECISION_INERT_FIELDS, decision, field_witnesses
        for name in self.analyses:
            sw = ChainSweep(ROOT / name, check_cache=False)
            for bid, rec in self._canonical_records(sw):
                d0 = decision(rec)
                for f in DECISION_INERT_FIELDS:
                    for w in field_witnesses(f, rec):
                        self.assertEqual(decision(dict(rec, **{f: w})), d0, (name, bid, f, w))

    def test_numeric_witnesses_cover_every_outcome_a_dense_grid_finds(self):
        # The exactness argument: witnesses at, between and beyond the cuts visit every
        # decision a fine sweep over the same range visits, for amount and budget, with
        # the other fields fixed at each canonical record.
        from chain.absorbers_chain import ChainSweep
        from chain.certificates import decision, field_witnesses
        for name in self.analyses:
            sw = ChainSweep(ROOT / name, check_cache=False)
            for bid, rec in self._canonical_records(sw)[:20]:
                for f in ("amount", "budget_remaining"):
                    ws = field_witnesses(f, rec)
                    hi = max(ws) * 1.5 + 1.0
                    dense = {decision(dict(rec, **{f: hi * i / 400.0})) for i in range(401)}
                    sparse = {decision(dict(rec, **{f: w})) for w in ws}
                    self.assertEqual(dense, sparse, (name, bid, f))

    def test_observable_cone_equals_p2_trigger(self):
        # A field-wise diff of the structured request must reproduce the declared cone's
        # trigger on every row, or the certificate is scoring a different policy.
        for name, a in self.analyses.items():
            c = a["E8_decomposition"]["certificates"]
            self.assertTrue(c["cone_matches_p2_trigger_on_every_row"], (name, c["cone_mismatch_rows"]))

    def test_certificate_never_reads_evaluation_only_inputs(self):
        # The scorer hands the certificate a row that raises on oracle_decision and factor,
        # and a sweep whose sensitivity raises. A certificate that reads either cannot
        # produce the committed numbers.
        from chain.absorbers_chain import ChainSweep
        from chain.certificates import _Guarded, certify
        # positive control: the guard does raise on the access paths certify uses, so a
        # certificate that read either key through them could not have produced the
        # committed numbers
        g = _Guarded({"oracle_decision": "approve", "factor": "amount", "decision": "approve", "audit": {}})
        with self.assertRaises(PermissionError):
            g["factor"]
        with self.assertRaises(PermissionError):
            g.get("oracle_decision")
        self.assertEqual(g["decision"], "approve")
        for name in self.analyses:
            sw = ChainSweep(ROOT / name, check_cache=False)

            def boom(bid):
                raise PermissionError("certificate read the oracle sensitivity set")
            sw.sensitivity = boom
            n = 0
            for bid in sw.bids[:10]:
                canon = sw.by_key.get((bid, "canon", "canon"))
                if canon is None:
                    continue
                base = sw.requests[bid].structured_v2()
                for prow in sw.perts[bid]:
                    pert = sw.perturbed(bid, prow["salt"]).structured_v2()
                    certify(sw, canon, _Guarded(prow), base, pert)   # must not raise
                    n += 1
            self.assertGreater(n, 0, name)

    def test_cells_partition_the_eligible_population(self):
        from chain.absorbers_chain import STEP_OUTPUTS
        for name, a in self.analyses.items():
            d = a["E8_decomposition"]["fields"]
            for s, ps in d["per_step"].items():
                self.assertEqual(ps["field_cells_total"], d["eligible_requests"] * len(STEP_OUTPUTS[s]), (name, s))
                self.assertEqual(sum(ps["cells"].values()), ps["field_cells_total"], (name, s))

    def test_cert_eq_never_changes_a_decision_when_the_fresh_output_was_valid(self):
        # By construction: a valid fresh output is one of the enumerated classes, and
        # cert_eq requires the decision to be identical in all of them. Where the fresh
        # output was invalid, recompute escalated by fail-safe, so a certified approving
        # reuse changes the decision; that residual is bounded by the fresh-invalid count.
        for name, a in self.analyses.items():
            s = a["E8_decomposition"]["certificates"]["schemes"]["cert_eq"]
            self.assertEqual(s["decision_changes_fresh_valid"], 0, name)
            self.assertLessEqual(s["decision_changes"], s["certified_rows_fresh_invalid"], name)
            self.assertLessEqual(s["unsafe_introduced"], s["certified_rows_fresh_invalid"], name)

    def test_cert_safe_never_introduces_an_unsafe_approval(self):
        # The guard's own invariant. cert_safe_valid may change a decision (reuse can be
        # more conservative than recompute) but may never approve where a valid recompute
        # did not; an unsafe approval under it can only sit on a fresh-invalid row.
        # cert_safe_all never certifies an approving reuse, so its unsafe count is zero.
        for name, a in self.analyses.items():
            sch = a["E8_decomposition"]["certificates"]["schemes"]
            self.assertLessEqual(sch["cert_safe_valid"]["unsafe_introduced"], sch["cert_safe_valid"]["certified_rows_fresh_invalid"], name)
            self.assertEqual(sch["cert_safe_all"]["unsafe_introduced"], 0, name)


if __name__ == "__main__":
    unittest.main()
