"""Focused integrity and rendering tests for the runtime product surface."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.validate_results import (
    ResultsValidationError,
    audit_value,
    decision_parse_error,
    load_rows,
)
from runtime import audit_record, build_console, router


ROOT = Path(__file__).resolve().parents[1]


class AuditPackageV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = load_rows(ROOT / "results" / "decisions.jsonl")
        cls.canon, cls.repro, cls.pert = audit_record._index_rows(cls.rows)
        cls.requests = audit_record._requests_by_id(42)

    def package(self, request_id: str, variant: str) -> dict:
        key = (request_id, variant)
        return audit_record.build_package(
            self.requests[request_id],
            self.canon[key],
            self.repro[key],
            self.pert[key],
        )

    def test_all_versioned_rows_build_valid_schema_v2_packages(self) -> None:
        packages = {}
        for key, row in self.canon.items():
            packages[key] = audit_record.build_package(
                self.requests[key[0]],
                row,
                self.repro[key],
                self.pert[key],
            )
        audit_record.validate_package_set(packages, self.canon)
        self.assertEqual(260, len(packages))
        self.assertEqual(
            {"audit_package_v2"},
            {package["schema"] for package in packages.values()},
        )

    def test_a2_a3_record_actual_decision_input_and_field_sources(self) -> None:
        request_id = "req-001-clean_approve"
        for variant in ("A2", "A3"):
            with self.subTest(variant=variant):
                package = self.package(request_id, variant)
                row = self.canon[request_id, variant]
                decision_input = package["decision_input"]
                self.assertEqual(
                    audit_value(row, "structured_record"),
                    decision_input["structured_record"],
                )
                self.assertEqual(
                    audit_value(row, "structured_parse")["source_by_field"],
                    decision_input["field_sources"],
                )
                self.assertTrue(decision_input["validation"]["valid"])
                self.assertEqual(
                    "deterministic_policy_core",
                    package["decision_of_record"]["decision_authority"],
                )
                audit_record.validate_package(package)

        a2 = self.package(request_id, "A2")
        a3 = self.package(request_id, "A3")
        self.assertEqual(
            {"llm_parse"},
            set(a2["decision_input"]["field_sources"].values()),
        )
        self.assertEqual(
            "authoritative_supplier_db",
            a3["decision_input"]["field_sources"]["supplier_risk"],
        )

    def test_req_041_a2_keeps_consumed_input_distinct_from_benchmark_truth(self) -> None:
        package = self.package("req-041-over_budget", "A2")
        self.assertEqual(
            "hardware",
            package["decision_input"]["structured_record"]["category"],
        )
        self.assertEqual(
            "it_hardware",
            package["benchmark_reference"]["generator_structured_record"]["category"],
        )
        self.assertNotEqual(
            package["decision_input"]["structured_record"],
            package["benchmark_reference"]["generator_structured_record"],
        )
        audit_record.validate_package(package)

    def test_a0_a1_have_explicit_llm_path_and_no_deterministic_trace(self) -> None:
        request_id = "req-001-clean_approve"
        for variant in ("A0", "A1"):
            with self.subTest(variant=variant):
                package = self.package(request_id, variant)
                decision_input = package["decision_input"]
                decision = package["decision_of_record"]
                self.assertEqual("llm", decision_input["decision_owner"])
                self.assertTrue(decision_input["input_path"])
                self.assertIsNone(decision_input["structured_record"])
                self.assertFalse(decision["deterministic_core"])
                self.assertIsNone(decision["firing_rule"])
                self.assertIsNone(decision["firing_rule_factors"])
                audit_record.validate_package(package)

    def test_invalid_perturbation_makes_probe_unavailable_without_scores(self) -> None:
        package = self.package("req-050-near_threshold_fuzzy", "A0")
        check = package["explanation"]["faithfulness_check"]
        self.assertEqual("unavailable", check["status"])
        self.assertEqual("incomplete_counterfactual_probe", check["reason"])
        self.assertEqual(1, len(check["invalid_evidence"]))
        self.assertEqual("pert", check["invalid_evidence"][0]["phase"])
        self.assertTrue(check["invalid_evidence"][0]["decision_parse_error"])
        for forbidden in (
            "empirical_sensitivity_set",
            "per_factor",
            "precision",
            "cited_factors",
            "unrecognized_citations",
        ):
            self.assertNotIn(forbidden, check)
        audit_record.validate_package(package)

    def test_reproducibility_error_counts_match_supporting_rows(self) -> None:
        request_id = "req-026-compliance_gap"
        package = self.package(request_id, "A2")
        repro_rows = self.repro[request_id, "A2"]
        errors = package["reproducibility_attestation"]["errors"]
        self.assertEqual(
            sum(decision_parse_error(row) for row in repro_rows),
            errors["decision_parse_error_count"],
        )
        self.assertEqual(1, errors["decision_parse_error_count"])
        self.assertEqual(
            len(repro_rows),
            len(package["supporting_evidence"]["reproducibility"]),
        )

    def test_complete_cache_provenance_is_grouped_by_measurement_phase(self) -> None:
        request_id = "req-001-clean_approve"
        package = self.package(request_id, "A3")
        evidence = package["supporting_evidence"]
        self.assertEqual(
            self.canon[request_id, "A3"]["cache_ids"],
            evidence["canonical"]["cache_ids"],
        )
        self.assertEqual(
            [row["cache_ids"] for row in sorted(
                self.repro[request_id, "A3"], key=lambda row: row["salt"]
            )],
            [row["cache_ids"] for row in evidence["reproducibility"]],
        )
        expected_perturbations = sum(
            len(group) for group in self.pert[request_id, "A3"].values()
        )
        actual_perturbations = sum(
            len(group) for group in evidence["perturbations"].values()
        )
        self.assertEqual(expected_perturbations, actual_perturbations)

    def test_validator_rejects_forged_deterministic_input(self) -> None:
        package = copy.deepcopy(self.package("req-001-clean_approve", "A3"))
        package["decision_input"]["structured_record"]["supplier_sanctioned"] = True
        with self.assertRaisesRegex(
            audit_record.AuditPackageValidationError,
            "decision does not follow",
        ):
            audit_record.validate_package(package)

    def test_atomic_directory_writer_replaces_complete_directory(self) -> None:
        package = self.package("req-001-clean_approve", "A0")
        key = ("req-001-clean_approve", "A0")
        index = [{
            "file": "req-001-clean_approve_A0.json",
            "request_id": key[0],
            "variant": key[1],
        }]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "audit_packages"
            output.mkdir()
            (output / "stale.txt").write_text("stale", encoding="utf-8")
            audit_record._write_packages_atomic(
                output, {key: package}, index, "# Flagship\n"
            )
            self.assertFalse((output / "stale.txt").exists())
            written = json.loads(
                (output / "req-001-clean_approve_A0.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("audit_package_v2", written["schema"])
            self.assertFalse((Path(temp) / ".audit_packages.previous").exists())

    def test_export_validation_failure_occurs_before_any_output_write(self) -> None:
        with (
            patch.object(audit_record, "load_rows", return_value=[{}]),
            patch.object(
                audit_record,
                "validate_rows",
                side_effect=ResultsValidationError("invalid sweep"),
            ) as validate,
            patch.object(audit_record, "_write_packages_atomic") as writer,
        ):
            with self.assertRaisesRegex(ResultsValidationError, "invalid sweep"):
                audit_record.export_packages(
                    Path("/does/not/matter"),
                    Path("/must/not/be/written"),
                )
        validate.assert_called_once()
        self.assertTrue(validate.call_args.kwargs["check_cache"])
        writer.assert_not_called()


class ConsoleSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        rows = load_rows(ROOT / "results" / "decisions.jsonl")
        cls.canon, cls.repro, cls.pert = audit_record._index_rows(rows)
        cls.requests = audit_record._requests_by_id(42)

    def package(self, request_id: str, variant: str) -> dict:
        key = (request_id, variant)
        return audit_record.build_package(
            self.requests[request_id],
            self.canon[key],
            self.repro[key],
            self.pert[key],
        )

    def test_script_terminators_and_html_are_inert(self) -> None:
        package = copy.deepcopy(self.package("req-001-clean_approve", "A0"))
        attack = "</script><script>alert('request')</script>"
        html_attack = "<img src=x onerror=alert('explanation')>"
        package["observed_input"]["free_text"] = attack
        package["explanation"]["text"] = html_attack
        item = {
            "key": "malicious",
            "request_id": "req-001-clean_approve",
            "variant": "A0",
            "kind": "clean",
            "decision": "approve",
            "firing_rule": None,
            "matches_oracle": True,
            "modal_share": 1.0,
            "pkg": package,
        }
        html = build_console.build_console_html(
            [item],
            {
                "req-001-clean_approve": {
                    "variant": "A0",
                    "router_rule": "</script><script>alert('router')</script>",
                }
            },
            {"label": "</script><script>alert('meta')</script>"},
        )
        self.assertNotIn(attack, html)
        self.assertNotIn(html_attack, html)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertNotIn("innerHTML", html)
        self.assertIn("textContent", html)

    def test_console_has_explicit_unavailable_rendering(self) -> None:
        package = self.package("req-050-near_threshold_fuzzy", "A0")
        item = {
            "key": "invalid-probe",
            "request_id": "req-050-near_threshold_fuzzy",
            "variant": "A0",
            "kind": "ambiguous",
            "decision": package["decision_of_record"]["decision"],
            "firing_rule": None,
            "matches_oracle": package["benchmark_reference"]["matches_oracle"],
            "modal_share": package["reproducibility_attestation"]["modal_share"],
            "pkg": package,
        }
        html = build_console.build_console_html(
            [item], {}, {"label": "test"}
        )
        self.assertIn('check.status !== "available"', html)
        self.assertIn("Unavailable:", html)


class RouterValidationTests(unittest.TestCase):
    def test_router_runs_full_cache_validation_before_evaluation(self) -> None:
        with (
            patch.object(router, "load_rows", return_value=[{}]),
            patch.object(
                router,
                "validate_rows",
                side_effect=ResultsValidationError("invalid sweep"),
            ) as validate,
            patch.object(router, "generate") as generate,
        ):
            with self.assertRaisesRegex(ResultsValidationError, "invalid sweep"):
                router.build_report(Path("/does/not/matter"))
        validate.assert_called_once()
        self.assertTrue(validate.call_args.kwargs["check_cache"])
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
