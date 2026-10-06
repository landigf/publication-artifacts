"""Side-by-side comparison of two completed inference-mode sweeps.

Reads the ``metrics.json`` of two results directories (default: the flagship
``results/`` chat sweep and the ``results-reasoner/`` sweep), writes a long-form
``comparison.csv`` and a machine-readable ``comparison.json`` into the second
directory, and reports whether the five configured headline orderings across
the autonomy axis hold in both sweeps. Values are never averaged across modes;
each sweep keeps its own numbers.

Run after both sweeps have passed the full pipeline:
  python -m harness.metrics --results results-reasoner
  python -m harness.compare_models
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ("A0", "A1", "A2", "A3")

COMPARED_METRICS = [
    "repro_modal_share_mean",
    "repro_unanimous_frac",
    "faith_precision_macro",
    "faith_recall_macro",
    "counterfactual_sensitivity_agreement_mean",
    "canonical_policy_mismatch_rate",
    "canonical_unsafe_approval_rate",
    "canonical_unsafe_approval_on_injected_cases_rate",
    "sampled_policy_mismatch_rate",
    "sampled_unsafe_approval_rate",
    "sampled_unsafe_approval_on_injected_cases_rate",
    "hard_escalate_rate",
    "mean_llm_calls",
    "mean_tokens_total",
    "mean_latency_ms",
]

# Headline orderings the exposé narrates for the chat sweep. Each is checked in
# both sweeps; the report states plainly where the second model agrees or not.
ORDERINGS = [
    ("repro_modal_share_mean", "A3 >= A0", lambda v: v["A3"] >= v["A0"]),
    ("counterfactual_sensitivity_agreement_mean", "A3 >= A0", lambda v: v["A3"] >= v["A0"]),
    ("canonical_policy_mismatch_rate", "A0 >= A3", lambda v: v["A0"] >= v["A3"]),
    ("canonical_unsafe_approval_on_injected_cases_rate", "A0 >= A3", lambda v: v["A0"] >= v["A3"]),
    ("faith_precision_macro", "A0 >= A3 (the honest non-monotone finding)", lambda v: v["A0"] >= v["A3"]),
]


def _load(results_dir: Path) -> dict:
    path = results_dir / "metrics.json"
    if not path.is_file():
        raise SystemExit(f"missing {path}; run harness.metrics --results {results_dir} first")
    return json.loads(path.read_text())


def comparison_rows(base: dict, other: dict) -> list[dict]:
    """Return long-form metric rows without averaging across sweeps."""
    rows = []
    for metric in COMPARED_METRICS:
        for variant in VARIANTS:
            rows.append({
                "metric": metric,
                "variant": variant,
                "baseline_value": base["variants"][variant].get(metric),
                "other_value": other["variants"][variant].get(metric),
            })
    return rows


def ordering_report(base: dict, other: dict) -> dict:
    """Evaluate the five configured orderings and count changed verdicts."""
    verdicts = []
    for metric, label, check in ORDERINGS:
        values_base = {v: base["variants"][v].get(metric) for v in VARIANTS}
        values_other = {v: other["variants"][v].get(metric) for v in VARIANTS}
        missing = (
            any(value is None for value in values_base.values())
            or any(value is None for value in values_other.values())
        )
        hold_base = None if missing else bool(check(values_base))
        hold_other = None if missing else bool(check(values_other))
        reversed_verdict = (
            hold_base is not None
            and hold_other is not None
            and hold_base != hold_other
        )
        verdicts.append({
            "metric": metric,
            "ordering": label,
            "baseline_holds": hold_base,
            "other_holds": hold_other,
            "verdict_reversed": reversed_verdict,
            "baseline_values": values_base,
            "other_values": values_other,
        })
    comparable = [
        item for item in verdicts
        if item["baseline_holds"] is not None and item["other_holds"] is not None
    ]
    reversal_count = sum(item["verdict_reversed"] for item in comparable)
    return {
        "schema_version": 1,
        "baseline": {
            "model": base.get("metadata", {}).get("model", "baseline"),
            "backend_label": base.get("metadata", {}).get("backend_label"),
            "n_requests": base.get("n_requests"),
            "decisions_sha256": base.get("metadata", {}).get("decisions_sha256"),
        },
        "other": {
            "model": other.get("metadata", {}).get("model", "other"),
            "backend_label": other.get("metadata", {}).get("backend_label"),
            "n_requests": other.get("n_requests"),
            "decisions_sha256": other.get("metadata", {}).get("decisions_sha256"),
        },
        "ordering_verdicts": verdicts,
        "ordering_count": len(verdicts),
        "comparable_ordering_count": len(comparable),
        "reversal_count": reversal_count,
        "agreement_count": len(comparable) - reversal_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two sweeps side by side")
    parser.add_argument("--baseline", default=str(ROOT / "results"))
    parser.add_argument("--other", default=str(ROOT / "results-reasoner"))
    args = parser.parse_args()
    base_dir, other_dir = Path(args.baseline), Path(args.other)
    base, other = _load(base_dir), _load(other_dir)

    base_model = base.get("metadata", {}).get("model", "baseline")
    other_model = other.get("metadata", {}).get("model", "other")

    rows = comparison_rows(base, other)
    out_path = other_dir / "comparison.csv"
    with out_path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["metric", "variant", base_model, other_model],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "metric": row["metric"],
                "variant": row["variant"],
                base_model: (
                    f"{row['baseline_value']:.4f}"
                    if isinstance(row["baseline_value"], float)
                    else row["baseline_value"]
                ),
                other_model: (
                    f"{row['other_value']:.4f}"
                    if isinstance(row["other_value"], float)
                    else row["other_value"]
                ),
            })

    report = ordering_report(base, other)
    json_path = other_dir / "comparison.json"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print(f"baseline={base_model} (n={base['n_requests']})  "
          f"other={other_model} (n={other['n_requests']})")
    print(f"{'ordering':55s} {'baseline':>9s} {'other':>9s}")
    for item in report["ordering_verdicts"]:
        metric = item["metric"]
        label = item["ordering"]
        if item["baseline_holds"] is None:
            print(f"{metric} [{label}]: n/a (missing values)")
            continue
        print(f"{metric + ' [' + label + ']':55s} "
              f"{str(item['baseline_holds']):>9s} "
              f"{str(item['other_holds']):>9s}")
    print(
        "ordering verdict reversals: "
        f"{report['reversal_count']}/{report['comparable_ordering_count']}"
    )
    print(f"wrote {out_path}")
    print(f"wrote {json_path}")


if __name__ == "__main__":
    main()
