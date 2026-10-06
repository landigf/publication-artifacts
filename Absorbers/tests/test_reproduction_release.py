from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agents import llm_client
from harness import compare_models
from harness.replay_request import (
    ReplayError,
    format_report,
    frozen_backend,
    parse_variants,
    request_for_id,
)
from harness.runner import _row, build_decision_row
from harness.validate_venture import VentureValidationError, validate_competitors


ROOT = Path(__file__).resolve().parents[1]


def _metrics(model: str) -> dict:
    variants = {}
    for variant in compare_models.VARIANTS:
        variants[variant] = {
            metric: 0.0 for metric in compare_models.COMPARED_METRICS
        }
    return {
        "n_requests": 65,
        "metadata": {
            "model": model,
            "backend_label": model,
            "decisions_sha256": model * 8,
        },
        "variants": variants,
    }


def _vendor_text(count: int = 36, *, status: str = "PARTIAL", sources: bool = True) -> str:
    blocks = []
    for index in range(count):
        source_line = (
            f"- **Sources:** <https://example.test/vendor-{index}>\n"
            if sources or index != count - 1
            else ""
        )
        blocks.append(
            f"### Vendor {index:02d}  `adjacent` · verify: {status}\n\n"
            f"- **What they ship:** fixture\n"
            f"{source_line}"
        )
    return "\n".join(blocks)


class OfflineConfigTests(unittest.TestCase):
    def test_offline_config_never_reads_env_or_keychain(self):
        with (
            mock.patch.object(llm_client, "_read_env", side_effect=AssertionError("env read")),
            mock.patch.object(llm_client, "_keychain", side_effect=AssertionError("keychain read")),
        ):
            chat = llm_client.config_for("deepseek", 0.7, offline=True)
            thinking = llm_client.config_for(
                "deepseek-reasoner", 0.7, offline=True,
            )
        self.assertIsNone(chat.api_key)
        self.assertIsNone(thinking.api_key)
        self.assertEqual(chat.model, "deepseek-chat")
        self.assertEqual(thinking.model, "deepseek-reasoner")
        self.assertEqual(thinking.max_tokens, 4000)

    def test_offline_flag_does_not_change_cache_identity(self):
        with (
            mock.patch.object(llm_client, "_read_env", return_value="secret"),
            mock.patch.object(llm_client, "_keychain", return_value=None),
        ):
            live = llm_client.config_for("deepseek", 0.0)
            offline = llm_client.config_for("deepseek", 0.0, offline=True)
        args = ("tag", "salt", "system", "user")
        self.assertEqual(llm_client.call_id(live, *args), llm_client.call_id(offline, *args))

    def test_ollama_backend_config_and_identity(self):
        with (
            mock.patch.object(llm_client, "_read_env", side_effect=AssertionError("env read")),
            mock.patch.object(llm_client, "_keychain", side_effect=AssertionError("keychain read")),
        ):
            live = llm_client.config_for("ollama:gemma3:4b", 0.7)
            offline = llm_client.config_for("ollama:gemma3:4b", 0.7, offline=True)
        self.assertEqual(live.model, "gemma3:4b")
        self.assertEqual(live.label, "local-gemma3_4b")
        self.assertEqual(live.base_url, "http://localhost:11434/v1")
        self.assertIsNone(live.api_key)
        self.assertEqual(live.max_tokens, 700)
        self.assertEqual(live.timeout, 180)
        args = ("tag", "salt", "system", "user")
        self.assertEqual(llm_client.call_id(live, *args), llm_client.call_id(offline, *args))
        with self.assertRaises(ValueError):
            llm_client.config_for("ollama:")


class ReplayTests(unittest.TestCase):
    def test_runner_exposes_same_row_builder(self):
        self.assertIs(_row, build_decision_row)

    def test_replay_input_validation(self):
        self.assertEqual(parse_variants("A0,A3"), ["A0", "A3"])
        with self.assertRaises(ReplayError):
            parse_variants("A0,A0")
        with self.assertRaises(ReplayError):
            parse_variants("A9")
        with self.assertRaises(ReplayError):
            request_for_id("missing", 42)

    def test_only_frozen_backend_identities_are_accepted(self):
        rows = [{"backend_label": "dsr", "model": "deepseek-reasoner"}]
        self.assertEqual(frozen_backend(rows), "deepseek-reasoner")
        with self.assertRaises(ReplayError):
            frozen_backend([{"backend_label": "new", "model": "future"}])

    def test_readable_report_contains_evidence_and_exact_final_pass(self):
        text = format_report(
            {"n_rows": 6716, "n_requests": 65, "active_cache_n": 10073},
            {
                "request_id": "req-063-over_threshold",
                "kind": "adversarial",
                "synthetic_request_text": "request\x1b[31m text",
                "oracle_decision": "escalate",
                "oracle_rule": "over_threshold",
            },
            [
                {
                    "variant": variant,
                    "decision": decision,
                    "oracle_match": decision == "escalate",
                    "firing_rule": rule,
                    "llm_calls": calls,
                    "stored_row_match": "PASS",
                    "cache_ids": [f"cache-{variant}"],
                    "cited_factors": ["amount"],
                    "explanation": "fixture explanation",
                }
                for variant, decision, rule, calls in (
                    ("A0", "approve", "", 1),
                    ("A3", "escalate", "over_threshold", 2),
                )
            ],
        )
        self.assertIn("synthetic_request_text=", text)
        self.assertNotIn("\x1b", text)
        self.assertIn("oracle_decision=escalate oracle_rule=over_threshold", text)
        self.assertIn(
            "A0: decision=approve oracle_match=False firing_rule=<none> "
            "llm_calls=1 stored_row_match=PASS",
            text,
        )
        self.assertIn('cache_ids=["cache-A3"]', text)
        self.assertIn('cited_factors=["amount"]', text)
        self.assertIn('explanation="fixture explanation"', text)
        self.assertEqual(
            text.splitlines()[-1],
            "PASS: offline replay exactly matches 2 stored canonical rows; "
            "no network or files written.",
        )


class ComparisonTests(unittest.TestCase):
    def test_report_has_five_verdicts_and_three_reversals(self):
        base, other = _metrics("base"), _metrics("other")
        for metric in (
            "repro_modal_share_mean",
            "counterfactual_sensitivity_agreement_mean",
        ):
            base["variants"]["A3"][metric] = 1.0
            other["variants"]["A0"][metric] = 1.0
        base["variants"]["A0"]["canonical_policy_mismatch_rate"] = 1.0
        other["variants"]["A3"]["canonical_policy_mismatch_rate"] = 1.0
        base["variants"]["A0"][
            "canonical_unsafe_approval_on_injected_cases_rate"
        ] = 1.0
        other["variants"]["A0"][
            "canonical_unsafe_approval_on_injected_cases_rate"
        ] = 1.0
        base["variants"]["A0"]["faith_precision_macro"] = 1.0
        other["variants"]["A0"]["faith_precision_macro"] = 1.0

        report = compare_models.ordering_report(base, other)
        self.assertEqual(report["ordering_count"], 5)
        self.assertEqual(len(report["ordering_verdicts"]), 5)
        self.assertEqual(report["reversal_count"], 3)
        compared = {row["metric"] for row in compare_models.comparison_rows(base, other)}
        self.assertIn("sampled_policy_mismatch_rate", compared)
        self.assertIn("sampled_unsafe_approval_rate", compared)
        self.assertIn("sampled_unsafe_approval_on_injected_cases_rate", compared)

    def test_cli_writes_csv_and_machine_readable_verdicts(self):
        base, other = _metrics("base-model"), _metrics("other-model")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline, comparison = root / "base", root / "other"
            baseline.mkdir()
            comparison.mkdir()
            (baseline / "metrics.json").write_text(json.dumps(base))
            (comparison / "metrics.json").write_text(json.dumps(other))
            argv = [
                "compare_models", "--baseline", str(baseline),
                "--other", str(comparison),
            ]
            with mock.patch("sys.argv", argv):
                compare_models.main()
            report = json.loads((comparison / "comparison.json").read_text())
            self.assertEqual(len(report["ordering_verdicts"]), 5)
            csv_path = comparison / "comparison.csv"
            self.assertNotIn(b"\r\n", csv_path.read_bytes())
            with csv_path.open(newline="") as handle:
                metrics = {row["metric"] for row in csv.DictReader(handle)}
            self.assertIn("sampled_policy_mismatch_rate", metrics)


class VentureValidatorTests(unittest.TestCase):
    def test_accepts_36_final_entries_with_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "competitors.md"
            path.write_text(_vendor_text())
            summary = validate_competitors(path)
        self.assertEqual(summary["vendors"], 36)
        self.assertEqual(summary["source_urls"], 36)
        self.assertEqual(summary["statuses"], {"PARTIAL": 36})

    def test_rejects_wrong_count_unchecked_or_missing_sources(self):
        fixtures = (
            _vendor_text(count=35),
            _vendor_text(status="unchecked"),
            _vendor_text(sources=False),
        )
        for content in fixtures:
            with self.subTest(content_length=len(content)):
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "competitors.md"
                    path.write_text(content)
                    with self.assertRaises(VentureValidationError):
                        validate_competitors(path)


class ReproduceScriptTests(unittest.TestCase):
    def _run(self, argument: str, temp_dir: str):
        env = dict(os.environ)
        env["TMPDIR"] = temp_dir
        return subprocess.run(
            ["bash", str(ROOT / "REPRODUCE.sh"), argument],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )

    def test_help_succeeds_and_live_or_unknown_fail_before_temp_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            help_result = self._run("--help", tmp)
            self.assertEqual(help_result.returncode, 0)
            self.assertIn("--offline", help_result.stdout)
            for argument in ("--live", "--unexpected"):
                result = self._run(argument, tmp)
                self.assertEqual(result.returncode, 2)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
