"""Sanity-check the predefined counterfactual intervention operator.

This check establishes narrow, testable invariants: each generated intervention
changes only its named structured factor, adversarial text is preserved, and the
documented urgency distractor does not change the deterministic policy outcome.
It does *not* claim to validate recovery of an LLM's hidden causal drivers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.policy_core import oracle, perturbations
from taskgen.generator import generate
from taskgen.schema import FACTORS


def validate_operator(seed: int) -> tuple[bool, dict]:
    requests = generate(seed)
    errors: list[str] = []
    intervention_n = 0
    adversarial_intervention_n = 0
    urgency_n = 0
    target_only_ok = True
    injection_ok = True
    urgency_ok = True

    for request in requests:
        base_structured = request.structured()
        base_decision = oracle(request).decision
        groups = perturbations(request)
        if set(groups) != set(FACTORS):
            target_only_ok = False
            errors.append(
                f"{request.request_id}: factors={sorted(groups)}, expected={sorted(FACTORS)}"
            )
        for factor, variants in groups.items():
            if not variants:
                errors.append(f"{request.request_id}/{factor}: no intervention values")
                continue
            for index, variant in enumerate(variants):
                intervention_n += 1
                changed = {
                    key for key in FACTORS
                    if variant.structured()[key] != base_structured[key]
                }
                if changed != {factor}:
                    target_only_ok = False
                    errors.append(
                        f"{request.request_id}/{factor}/{index}: changed={sorted(changed)}"
                    )
                if variant.kind != request.kind:
                    target_only_ok = False
                    errors.append(f"{request.request_id}/{factor}/{index}: kind changed")
                if variant.supplier.name != request.supplier.name:
                    target_only_ok = False
                    errors.append(f"{request.request_id}/{factor}/{index}: supplier identity changed")
                if not variant.request_id.startswith(request.request_id + "|pert"):
                    target_only_ok = False
                    errors.append(f"{request.request_id}/{factor}/{index}: invalid request_id")

                injection = getattr(request, "injection", None)
                variant_injection = getattr(variant, "injection", None)
                if injection:
                    adversarial_intervention_n += 1
                    if variant_injection != injection:
                        injection_ok = False
                        errors.append(
                            f"{request.request_id}/{factor}/{index}: injection field changed"
                        )
                    if injection not in request.free_text or injection not in variant.free_text:
                        injection_ok = False
                        errors.append(
                            f"{request.request_id}/{factor}/{index}: injection text not preserved"
                        )
                elif variant_injection:
                    injection_ok = False
                    errors.append(
                        f"{request.request_id}/{factor}/{index}: intervention introduced injection"
                    )

                if factor == "urgency":
                    urgency_n += 1
                    if oracle(variant).decision != base_decision:
                        urgency_ok = False
                        errors.append(
                            f"{request.request_id}/urgency/{index}: oracle decision changed"
                        )

    summary = {
        "requests": len(requests),
        "interventions": intervention_n,
        "adversarial_interventions": adversarial_intervention_n,
        "urgency_interventions": urgency_n,
        "target_only_ok": target_only_ok,
        "injection_ok": injection_ok,
        "urgency_ok": urgency_ok,
        "errors": errors,
    }
    return not errors, summary


def report_results_sanity(results: Path) -> None:
    """Print measured agreement without presenting it as instrument validation."""
    path = results / "metrics.json"
    if not path.exists():
        print("\nMeasured-results sanity: metrics.json not present; skipped.")
        return
    metrics = json.loads(path.read_text())
    if metrics.get("schema_version", 0) < 2:
        print("\nMeasured-results sanity: metrics.json is stale; rerun harness.metrics.")
        return
    print("\nMeasured counterfactual sensitivity agreement (descriptive):")
    for variant in ("A0", "A1", "A2", "A3"):
        record = metrics["variants"][variant]
        agreement = record["counterfactual_sensitivity_agreement_mean"]
        exact = record["counterfactual_exact_match_rate"]
        n = record["counterfactual_probe_n"]
        invalid = record["counterfactual_probe_invalid_n"]
        print(
            f"  {variant}: mean Jaccard={agreement:.3f}, exact={exact:.3f}, "
            f"complete probes={n}, excluded={invalid}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate perturbation invariants")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--results", default=str(ROOT / "results"))
    args = parser.parse_args()
    ok, summary = validate_operator(args.seed)
    print("Counterfactual intervention invariants:")
    print(f"  requests                         : {summary['requests']}")
    print(f"  generated interventions          : {summary['interventions']}")
    print(
        "  target-only structured changes   : "
        f"{'PASS' if summary['target_only_ok'] else 'FAIL'}"
    )
    print(
        "  adversarial text preserved       : "
        f"{'PASS' if summary['injection_ok'] else 'FAIL'} "
        f"(n={summary['adversarial_interventions']})"
    )
    print(
        "  urgency non-pivotal in oracle    : "
        f"{'PASS' if summary['urgency_ok'] else 'FAIL'} "
        f"(n={summary['urgency_interventions']})"
    )
    if summary["errors"]:
        print("\nFirst invariant failures:", file=sys.stderr)
        for error in summary["errors"][:20]:
            print(f"  {error}", file=sys.stderr)
    report_results_sanity(Path(args.results))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
