"""Generate denominator- and regime-labelled figures from ``metrics.json``.

Four figures (PNG + PDF, 300 dpi) into ``figures/``:
  * reproducibility_faithfulness : modal stability + cited-factor precision/recall
  * coverage_robustness          : hard accuracy/unsafe + unsafe on injected cases
  * auditability_gap             : counterfactual sensitivity agreement + citations
  * cost                         : canonical LLM-call footprint + latency

Styling borrows the academic rcParams used elsewhere in the repo.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
ORDER = ["A0", "A1", "A2", "A3"]
LABELS = {
    "A0": "A0\nend-to-end", "A1": "A1\npolicy report",
    "A2": "A2\ngate", "A3": "A3\nAuditChain",
}

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.grid": True, "grid.alpha": 0.3, "grid.linestyle": "--",
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 11, "axes.titlesize": 12, "axes.labelsize": 11,
    "savefig.dpi": 300, "savefig.bbox": "tight",
})


def _series(metrics, key):
    xs, ys = [], []
    for v in ORDER:
        if v in metrics["variants"]:
            if key not in metrics["variants"][v]:
                raise KeyError(f"{v} missing required metric {key}")
            val = metrics["variants"][v][key]
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ValueError(f"{v}.{key} is missing or non-finite: {val!r}")
            xs.append(v)
            ys.append(val)
    return xs, ys


def _save(fig, out_dir: Path, name: str):
    for ext in ("png", "pdf"):
        # Strip the PDF creation timestamp so a rerun of REPRODUCE.sh leaves the
        # tree clean. Without this every reproduce run rewrites four identical
        # figures with a new date and they show up as modified.
        kw = {"metadata": {"CreationDate": None, "Producer": None, "Creator": None}} if ext == "pdf" else {}
        fig.savefig(out_dir / f"{name}.{ext}", **kw)
    plt.close(fig)


def _bars(ax, metrics, keys, labels, colors):
    import numpy as np
    xs = [v for v in ORDER if v in metrics["variants"]]
    x = np.arange(len(xs))
    w = 0.8 / len(keys)
    for i, (k, lab, col) in enumerate(zip(keys, labels, colors)):
        _, ys = _series(metrics, k)
        ax.bar(x + (i - (len(keys) - 1) / 2) * w, ys, w, label=lab, color=col)
    ax.set_xticks(x)
    ax.set_xticklabels([LABELS[v] for v in xs])
    ax.legend(frameon=False, fontsize=9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(ROOT / "results"))
    ap.add_argument("--figures", default=str(ROOT / "figures"))
    args = ap.parse_args()
    metrics = json.loads((Path(args.results) / "metrics.json").read_text())
    if metrics.get("schema_version", 0) < 2:
        raise ValueError("metrics.json is stale; rerun harness.metrics")
    out = Path(args.figures)
    out.mkdir(parents=True, exist_ok=True)
    n = metrics["n_requests"]
    n_repro = metrics["n_repro"]
    canonical_temperature = metrics["metadata"]["temperatures"]["canonical"]
    sampled_temperature = metrics["metadata"]["temperatures"]["sampled"]
    hard_n = metrics["hard_n"]
    adversarial_n = metrics["adversarial_n"]
    probe_ns = [metrics["variants"][variant]["counterfactual_probe_n"] for variant in ORDER]
    probe_label = str(probe_ns[0]) if len(set(probe_ns)) == 1 else f"{min(probe_ns)}-{max(probe_ns)}"

    # 1. reproducibility + faithfulness
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    _bars(ax, metrics,
          ["repro_modal_share_mean", "faith_precision_macro", "faith_recall_macro"],
          [f"modal share (T={sampled_temperature:g}, N={n_repro})", "citation precision (macro)",
           "citation recall (macro)"],
          ["#2c7fb8", "#7fcdbb", "#c7e9b4"])
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("rate")
    ax.set_title(f"Service stability and cited-factor alignment (requests={n})")
    _save(fig, out, "reproducibility_faithfulness")

    # 2. coverage / robustness on hard requests
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    _bars(ax, metrics,
          ["hard_policy_accuracy_rate", "hard_unsafe_rate",
           "canonical_unsafe_approval_on_injected_cases_rate"],
          [f"policy accuracy (hard n={hard_n})", f"unsafe approval (hard n={hard_n})",
           f"unsafe approval (injected n={adversarial_n})"],
          ["#41ab5d", "#f16913", "#cb181d"])
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("rate")
    ax.set_title(f"Canonical T={canonical_temperature:g} hard-case outcomes")
    _save(fig, out, "coverage_robustness")

    # 3. tested sensitivity agreement vs cited-factor precision
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    _bars(ax, metrics,
          ["counterfactual_sensitivity_agreement_mean", "faith_precision_macro"],
          ["sensitivity agreement (mean Jaccard)", "citation precision (macro)"],
          ["#54278f", "#9e9ac8"])
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("agreement")
    ax.set_title(f"Counterfactual and citation alignment (complete probes n={probe_label})")
    _save(fig, out, "auditability_gap")

    # 4. cost per decision
    import numpy as np
    fig, ax1 = plt.subplots(figsize=(6.4, 4.0))
    xs = [v for v in ORDER if v in metrics["variants"]]
    x = np.arange(len(xs))
    _, calls = _series(metrics, "mean_llm_calls")
    _, lat = _series(metrics, "mean_latency_ms")
    ax1.bar(x - 0.2, calls, 0.4, color="#2171b5", label="LLM calls/decision")
    ax1.set_xticks(x)
    ax1.set_xticklabels([LABELS[v] for v in xs])
    ax1.set_ylabel("LLM calls per decision", color="#2171b5")
    ax2 = ax1.twinx()
    ax2.bar(x + 0.2, lat, 0.4, color="#d94801", label="latency (ms)")
    ax2.set_ylabel("latency ms per decision", color="#d94801")
    ax2.grid(False)
    ax1.set_title(
        f"Canonical T={canonical_temperature:g} LLM-call footprint (requests={n})"
    )
    _save(fig, out, "cost")

    print(f"wrote 4 figures (png+pdf) -> {out}")


if __name__ == "__main__":
    main()
