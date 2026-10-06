"""Invariants for harness/absorbers.py, checked against the frozen local sweep.

Run from the repository root:
    PYTHONPATH=. ../.venv/bin/python -m unittest tests.test_absorbers
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import absorbers  # noqa: E402

SWEEPS = [p for p in (ROOT / "results-local", ROOT / "results-reasoner", ROOT / "results") if (p / "decisions.jsonl").exists()]


class AbsorbersInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # check_cache=False keeps the suite fast; REPRODUCE.sh runs the full gate.
        cls.analyses = {p.name: absorbers.analyse(p, check_cache=False) for p in SWEEPS}
        cls.assertTrue(cls.analyses, "no sweep directory found")

    def test_schema_and_variants(self):
        for name, a in self.analyses.items():
            self.assertEqual(a["schema"], absorbers.SCHEMA, name)
            self.assertEqual(a["variants"], ["A0", "A1", "A2", "A3"], name)
            self.assertEqual(a["n_requests"], 65, name)

    def test_recompute_policy_never_diverges(self):
        for name, a in self.analyses.items():
            for v, e3 in a["E3_reuse"].items():
                p0 = e3["policies"]["P0_recompute"]
                self.assertEqual(p0["reused"], 0, (name, v))
                self.assertEqual(p0["diverged"], 0, (name, v))
                self.assertEqual(p0["divergence_frac"], 0.0, (name, v))

    def test_perturbed_requests_regenerate_to_stored_input_hash(self):
        # Every perturbation row's stored input hash must equal the hash of the
        # request regenerated from (seed, factor, index). A3's canonical parse is
        # valid on all 65 requests in every sweep, so all 964 rows are scored; A2
        # may skip requests whose canonical parse failed validation.
        for name, a in self.analyses.items():
            for v, e3 in a["E3_reuse"].items():
                self.assertEqual(e3["input_sha_mismatch"], 0, (name, v))
                self.assertLessEqual(e3["pert_rows"], 964, (name, v))
                self.assertEqual(e3["eligible_requests"] + e3["skipped_invalid_canon"], 65, (name, v))
            self.assertEqual(a["E3_reuse"]["A3"]["pert_rows"], 964, name)

    def test_input_hash_reuse_is_a_subset_of_cone_reuse(self):
        for name, a in self.analyses.items():
            for v, e3 in a["E3_reuse"].items():
                self.assertTrue(e3["nesting_P1_subset_P2"], (name, v))
                p1 = e3["policies"]["P1_input_hash"]["reused"]
                p2 = e3["policies"]["P2_dependency_cone"]["reused"]
                self.assertLessEqual(p1, p2, (name, v))

    def test_a2_parses_every_field_so_structural_reuse_never_fires(self):
        for name, a in self.analyses.items():
            e3 = a["E3_reuse"]["A2"]
            self.assertEqual(e3["policies"]["P1_input_hash"]["reused"], 0, name)
            self.assertEqual(e3["policies"]["P2_dependency_cone"]["reused"], 0, name)
            self.assertEqual(a["E2_functionality_T0"]["A2"]["identical_input_pairs"], 0, name)

    def test_a3_cone_reuse_fires_exactly_on_supplier_side_perturbations(self):
        # 65 requests x (1 approved + 2 risk + 1 sanctioned) = 260 of 964 rows.
        for name, a in self.analyses.items():
            e3 = a["E3_reuse"]["A3"]
            self.assertEqual(e3["policies"]["P2_dependency_cone"]["reused"], 260, name)
            pf = e3["per_factor"]
            for f in absorbers.SUPPLIER_FACTORS:
                self.assertEqual(pf[f]["P2_dependency_cone"]["reused"], pf[f]["P2_dependency_cone"]["rows"], (name, f))
            for f in set(pf) - set(absorbers.SUPPLIER_FACTORS):
                self.assertEqual(pf[f]["P2_dependency_cone"]["reused"], 0, (name, f))

    def test_reuse_divergence_is_bounded_by_record_level_nonfunctionality(self):
        # P1 reuses exactly the identical-input pairs E2 scores. A reused decision
        # can only diverge if the fresh parse record differed, so the conditional
        # divergence rate cannot exceed the record-level non-identical rate.
        for name, a in self.analyses.items():
            e2 = a["E2_functionality_T0"]["A3"]
            e3 = a["E3_reuse"]["A3"]["policies"]["P1_input_hash"]
            if e2["record_comparable_pairs"] == 0:
                continue
            bound = 1.0 - e2["record_identical_frac"]
            self.assertLessEqual(e3["divergence_given_reuse_frac"] or 0.0, bound + 1e-9, name)

    def test_e2_pairs_are_distinct_cache_records(self):
        # Salt is part of the cache identity, so canon and each perturbation are
        # independent calls even when the prompt is identical. Guard against a
        # future change that would make E2 compare a record with itself.
        sw = absorbers.Sweep(SWEEPS[0], check_cache=False)
        bid = sw.bids[0]
        canon = sw.by_key[(bid, "A3", "canon", "canon")]
        ids = {sw.parse_cache_id(canon)}
        for prow in sw.perts[(bid, "A3")]:
            ids.add(sw.parse_cache_id(prow))
        self.assertEqual(len(ids), 1 + len(sw.perts[(bid, "A3")]))

    def test_absorption_ratio_is_consistent_with_its_counts(self):
        for name, a in self.analyses.items():
            for v in ("A2", "A3"):
                e1 = a["E1_absorption"][v]
                self.assertEqual(e1["absorbed"] + e1["leaked"], e1["parse_record_varies"], (name, v))
                if e1["parse_record_varies"]:
                    self.assertAlmostEqual(e1["absorption_ratio"], round(e1["absorbed"] / e1["parse_record_varies"], 4), places=4)

    def test_output_is_byte_stable(self):
        a1 = absorbers.analyse(SWEEPS[0], check_cache=False)
        a2 = absorbers.analyse(SWEEPS[0], check_cache=False)
        self.assertEqual(json.dumps(a1, sort_keys=True), json.dumps(a2, sort_keys=True))

    def test_committed_json_matches_regeneration(self):
        for p in SWEEPS:
            committed = p / "absorbers.json"
            if not committed.exists():
                continue
            fresh = absorbers.analyse(p, check_cache=False)
            self.assertEqual(json.loads(committed.read_text()), fresh, p.name)



class CanaryTests(unittest.TestCase):
    """E5: the cost of noticing that a configuration stopped being functional."""

    MODELS = ("upper_bound", "per_recompute")

    @classmethod
    def setUpClass(cls):
        cls.analyses = {p.name: absorbers.analyse(p, check_cache=False) for p in SWEEPS}

    def test_undefined_exactly_when_no_identical_prompt_pair_exists(self):
        # A2's parse prompt carries the supplier block, so no perturbation leaves it
        # identical. Zero mismatches there must not read as a sound configuration.
        for name, a in self.analyses.items():
            for v, c in a["E5_canary"].items():
                pairs = a["E2_functionality_T0"][v]["record_comparable_pairs"]
                self.assertEqual(c["defined"], pairs > 0, (name, v))
                if not c["defined"]:
                    self.assertIsNone(c["detectable"], (name, v))
                    self.assertIsNone(c["upper_bound"], (name, v))
                    self.assertIsNone(c["per_recompute"], (name, v))

    def test_profile_accounts_for_every_request_in_the_population(self):
        for name, a in self.analyses.items():
            for v, c in a["E5_canary"].items():
                if not c["defined"]:
                    continue
                prof = c["pair_mismatch_profile"]
                self.assertEqual(sum(prof.values()), c["requests"], (name, v))
                bad = sum(n for k, n in prof.items() if int(k.split("/")[0]) > 0)
                self.assertEqual(bad, c["mismatching_requests"], (name, v))
                for k in prof:
                    m, n = (int(x) for x in k.split("/"))
                    self.assertLessEqual(m, n, (name, v, k))

    def test_detection_is_monotone_in_k_under_both_models(self):
        for name, a in self.analyses.items():
            for v, c in a["E5_canary"].items():
                if not c["defined"]:
                    continue
                for m in self.MODELS:
                    at = c[m]["detection_prob_at_k"]
                    ks = sorted(int(k) for k in at)
                    probs = [at[str(k)] for k in ks]
                    self.assertEqual(probs, sorted(probs), (name, v, m))

    def test_the_bound_dominates_the_deployable_model_everywhere(self):
        # Treating "was ever seen to disagree" as certain detection can only
        # overstate what one fresh recompute finds. If this inverts, the two
        # models have been swapped somewhere between the JSON and the macros.
        for name, a in self.analyses.items():
            for v, c in a["E5_canary"].items():
                if not c["defined"]:
                    continue
                ub, pr = c["upper_bound"], c["per_recompute"]
                for k, x in pr["detection_prob_at_k"].items():
                    self.assertLessEqual(x, ub["detection_prob_at_k"][k] + 1e-9, (name, v, k))
                if pr["k_for_95"] is not None:
                    self.assertGreaterEqual(pr["k_for_95"], ub["k_for_95"], (name, v))

    def test_nothing_to_detect_means_no_canary_fires(self):
        for name, a in self.analyses.items():
            for v, c in a["E5_canary"].items():
                if not c["defined"] or c["mismatching_requests"]:
                    continue
                for m in self.MODELS:
                    self.assertTrue(all(x == 0.0 for x in c[m]["detection_prob_at_k"].values()), (name, v, m))
                    self.assertIsNone(c[m]["k_for_95"], (name, v, m))

    def test_k_for_95_is_the_smallest_k_that_reaches_the_threshold(self):
        # Computed from the curve rather than looked up in the reported grid: a
        # k_for_95 outside CANARY_KS is normal and must not make this test raise.
        from fractions import Fraction
        for name, a in self.analyses.items():
            for v, c in a["E5_canary"].items():
                if not c["defined"]:
                    continue
                for m, ind in (("upper_bound", True), ("per_recompute", False)):
                    k = c[m]["k_for_95"]
                    if k is None:
                        continue
                    qs = absorbers._profile_to_qs(c["pair_mismatch_profile"], indicator=ind)
                    curve, _, _ = absorbers._detection_curve(qs, (k - 1, k))
                    self.assertGreaterEqual(curve[str(k)], 0.95, (name, v, m))
                    if k > 1:
                        self.assertLess(curve[str(k - 1)], 0.95, (name, v, m))

    def test_both_models_match_brute_force_enumeration(self):
        # Every k-subset counted directly, so an error in the symmetric-polynomial
        # recurrence or in the hypergeometric it generalises cannot pass silently.
        from fractions import Fraction
        from itertools import combinations
        cases = [{"2/4": 2, "0/4": 3}, {"1/4": 1, "4/4": 1, "0/2": 4}, {"3/4": 3}, {"0/4": 5}]
        for prof in cases:
            for ind in (True, False):
                qs = absorbers._profile_to_qs(prof, indicator=ind)
                R = len(qs)
                curve, _, _ = absorbers._detection_curve(qs, range(1, R + 1))
                for k_s, prob in curve.items():
                    k = int(k_s)
                    subs = list(combinations(range(R), k))
                    exact = sum((Fraction(1) - __import__("math").prod(
                        (qs[i] for i in sub), start=Fraction(1))) for sub in subs) / len(subs)
                    # the reported curve is rounded to 4 decimals by _r
                    self.assertAlmostEqual(prob, float(exact), places=4, msg=(prof, ind, k))


class AliasNormaliserTests(unittest.TestCase):
    """E6: the same analysis under a second deterministic consumer.

    The table maps two out-of-schema spellings the parse emits onto schema keys.
    It is applied where the core reads the record and nowhere earlier, so every
    record-level quantity must be identical under both normalisers and only the
    decision-level ones may move.
    """

    @classmethod
    def setUpClass(cls):
        cls.analyses = {p.name: absorbers.analyse(p, check_cache=False) for p in SWEEPS}

    def test_recompute_path_is_faithful_to_the_recorded_decision(self):
        # decide() over the row's own validated record reproduces the recorded
        # decision on every valid A3 row; this is what lets the alias be inserted
        # offline without touching decisions.jsonl.
        from agents.policy_core import decide
        for p in SWEEPS:
            for line in (p / "decisions.jsonl").read_text().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if r["variant"] != "A3":
                    continue
                audit = r.get("audit") or {}
                if not (audit.get("structured_parse") or {}).get("valid"):
                    continue
                rec = audit.get("structured_record") or {}
                self.assertEqual(decide(rec).decision.value, r["decision"], (p.name, r["request_id"], r["salt"]))

    def test_table_is_an_alias_not_a_guess(self):
        from taskgen.schema import CATEGORY_THRESHOLDS
        for k, v in absorbers.ALIAS_NORMALISER.items():
            self.assertNotIn(k, CATEGORY_THRESHOLDS, k)
            self.assertIn(v, CATEGORY_THRESHOLDS, v)
        for name, a in self.analyses.items():
            for v, e3 in a["E6_alias_normaliser"]["E3_reuse"].items():
                self.assertEqual(e3["alias_wrong_known"], 0, (name, v))
                self.assertLessEqual(e3["rows_changed"], e3["alias_rows"], (name, v))

    def test_e2_has_no_alias_variant_and_record_variance_is_unchanged(self):
        for name, a in self.analyses.items():
            self.assertNotIn("E2_functionality_T0", a["E6_alias_normaliser"], name)
            for v in a["E6_alias_normaliser"]["E1_absorption"]:
                frozen = a["E1_absorption"][v]; alias = a["E6_alias_normaliser"]["E1_absorption"][v]
                self.assertEqual(alias["parse_record_varies"], frozen["parse_record_varies"], (name, v))
                self.assertEqual(alias["varying_fields_hist"], frozen["varying_fields_hist"], (name, v))

    def test_oracle_correctness_is_monotone_non_decreasing_under_the_alias(self):
        # Empirical and pinned, not structural: an alias that mapped to the wrong
        # known category could lower it. test_table_is_an_alias_not_a_guess is what
        # makes this hold.
        for name, a in self.analyses.items():
            for v, e3 in a["E6_alias_normaliser"]["E3_reuse"].items():
                for pol, x in e3["policies"].items():
                    frozen = a["E3_reuse"][v]["policies"][pol]["oracle_correct_frac"]
                    self.assertGreaterEqual(x["oracle_correct_frac"], frozen, (name, v, pol))

    def test_approve_stratum_category_leaks_vanish_under_the_alias(self):
        for name, a in self.analyses.items():
            for v, e1 in a["E6_alias_normaliser"]["E1_absorption"].items():
                ap = e1.get("by_oracle_and_varying_fields", {}).get("approve", {})
                leaks = sum(c["leaked"] for k, c in ap.items() if "category" in k)
                self.assertEqual(leaks, 0, (name, v))


if __name__ == "__main__":
    unittest.main()
