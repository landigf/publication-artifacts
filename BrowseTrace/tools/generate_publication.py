#!/usr/bin/env python3
"""Derive descriptive publication statistics and figures from released bytes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_csv(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for field in ["requests", "bytes", "distinct_hostname_count", "status_200_nonroot_count"]:
            if field in row:
                row[field] = int(row[field])
        for field in ["positive_traffic", "reached_content"]:
            if field in row:
                if row[field] not in {"true", "false"}:
                    raise ValueError("Invalid boolean in released census")
                row[field] = row[field] == "true"
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    positive = sum(row["requests"] > 0 for row in rows)
    requests = sum(row["requests"] for row in rows)
    return {"attempts": n, "positive_traffic": positive, "zero_requests": n - positive, "requests": requests, "bytes": sum(row.get("bytes", 0) for row in rows), "mean_requests_per_attempt": requests / n if n else None, "mean_requests_per_positive_attempt": requests / positive if positive else None, "median_requests_per_attempt": statistics.median(row["requests"] for row in rows) if n else None, "minimum_requests": min((row["requests"] for row in rows), default=None), "maximum_requests": max((row["requests"] for row in rows), default=None)}


def group(rows: list[dict], field: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row[field]].append(row)
    return {label: summarize(items) for label, items in sorted(groups.items())}


def liveness_summary(rows: list[dict]) -> dict:
    n = len(rows)
    positive = sum(row["requests"] > 0 for row in rows)
    reached = sum(row["distinct_hostname_count"] >= 3 and row["status_200_nonroot_count"] >= 5 for row in rows)
    return {"attempts": n, "positive_traffic": positive, "reached_content": reached, "percent_all_attempts": reached / n * 100, "percent_positive_attempts": reached / positive * 100}


def audit_sizes(root: Path = ROOT) -> dict:
    workloads = []
    for workload, filename in [("scripted", "full_400_sessions.csv"), ("llm", "llm_full_901.csv")]:
        path = root / "data/traces" / filename
        ranges: dict[str, list[int]] = {}
        rows = []
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                size = int(row["object_size_bytes"])
                if size > 0:
                    key = row["cache_key"]
                    rows.append((key,size))
                    if key in ranges:
                        ranges[key][0] = min(ranges[key][0],size)
                        ranges[key][1] = max(ranges[key][1],size)
                    else:
                        ranges[key] = [size,size]
        variable = {key for key,(minimum,maximum) in ranges.items() if minimum != maximum}
        workloads.append({"workload":workload,"path":str(path.relative_to(root)),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"positive_size_keys":len(ranges),"varying_positive_size_keys":len(variable),"varying_positive_size_key_percent":100 * len(variable)/len(ranges),"positive_size_requests":len(rows),"requests_on_varying_keys":sum(key in variable for key,size in rows),"current_request_bytes_on_varying_keys":sum(size for key,size in rows if key in variable)})
    return {"schema_version":1,"definition":"A varying-size key has at least two different positive object_size_bytes values in its released sequence. Zero-size rows are excluded consistently with native replay.","cache_accounting":"The pinned policies admit using the incoming size and do not update resident-size accounting on a cache hit. Byte-hit ratios weight the current request's supplied size. Capacity bounds this admission-time accounting, not verified physical HTTP representation bytes.","workloads":workloads}


def derive(root: Path = ROOT) -> dict:
    census = load_csv(root / "data/provenance/session_census.csv")
    liveness = load_csv(root / "data/provenance/reached_content_sessions.csv")
    paper = [row for row in census if row["cohort"] == "paper-corpus"]
    scripted = [row for row in paper if row["workload"] == "scripted"]
    llm = [row for row in paper if row["workload"] == "llm"]
    comparison = [row for row in census if row["cohort"] == "table4-reference"]
    zurich = [row for row in scripted if row["source"] == "scripted-zurich"]
    human = [row for row in census if row["cohort"] == "human-reference"]
    baseline_mean = summarize(zurich)["mean_requests_per_attempt"]
    conditional = group(comparison, "model")
    for value in conditional.values():
        value["ratio_to_scripted_zurich_mean"] = value["mean_requests_per_attempt"] / baseline_mean
    task_ratios = {}
    for task in sorted({row["task"] for row in comparison}):
        base = summarize([row for row in zurich if row["task"] == task])
        models = group([row for row in comparison if row["task"] == task], "model")
        for value in models.values():
            value["ratio_to_task_scripted_zurich_mean"] = value["mean_requests_per_attempt"] / base["mean_requests_per_attempt"]
        task_ratios[task] = {"scripted_zurich": base, "models": models}
    scripted_mean = summarize(scripted)["mean_requests_per_attempt"]
    llm_summary = summarize(llm)
    llm_live = [row for row in liveness if row["cohort"] == "paper-llm"]
    return {"schema_version": 1, "source_hashes": {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest() for relative in ["data/provenance/session_census.csv", "data/provenance/reached_content_sessions.csv", "data/provenance/collection-metadata-audit.json"]}, "interpretation": "Descriptive retained cohorts with different browser substrates, step budgets, models, regions, dates and sample sizes. Ratios are cohort-conditional and are not causal model effects. No semantic task-success labels or population human baseline.", "census_rows": len(census), "paper_corpus": {"total": summarize(paper), "scripted": summarize(scripted), "llm": llm_summary, "llm_all_attempt_mean_ratio_to_scripted": llm_summary["mean_requests_per_attempt"] / scripted_mean, "llm_positive_attempt_mean_ratio_to_scripted": llm_summary["mean_requests_per_positive_attempt"] / scripted_mean}, "llm_by_model": group(llm, "model"), "paper_by_source": group(paper, "source"), "paper_by_region_label": group(paper, "region"), "paper_by_task": group(paper, "task"), "comparison_cohort": {"total": summarize(comparison), "scripted_zurich": summarize(zurich), "by_model": conditional, "by_task": task_ratios}, "human_reference": {"participants": 1, **summarize(human)}, "liveness": {"predicate": "distinct_hostname_count >= 3 AND status_200_nonroot_count >= 5", "meaning": "Mechanical trace liveness; not semantic success. Predicate counts are supplied sanitized projections, not recomputed from omitted raw URLs.", "scripted": liveness_summary([row for row in liveness if row["cohort"] == "paper-scripted"]), "llm": liveness_summary(llm_live), "legacy_comparison": liveness_summary([row for row in liveness if row["cohort"] == "legacy-llm"]), "llm_by_model": {model: liveness_summary([row for row in llm_live if row["model"] == model]) for model in sorted({row["model"] for row in llm_live})}}}


def tex_tables(stats: dict, replay: dict, destination: Path, sizes: dict) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    corpus = stats["paper_corpus"]
    macros = {"BTCorpusAttempts": f"{corpus['total']['attempts']:,}", "BTScriptedAttempts": str(corpus["scripted"]["attempts"]), "BTLLMAttempts": str(corpus["llm"]["attempts"]), "BTLLMPositive": str(corpus["llm"]["positive_traffic"]), "BTLLMEmpty": str(corpus["llm"]["zero_requests"]), "BTScriptedRecorded": f"{corpus['scripted']['requests']:,}", "BTLLMRecorded": f"{corpus['llm']['requests']:,}", "BTScriptedMean": f"{corpus['scripted']['mean_requests_per_attempt']:.1f}", "BTLLMMean": f"{corpus['llm']['mean_requests_per_attempt']:.1f}", "BTLLMPositiveMean": f"{corpus['llm']['mean_requests_per_positive_attempt']:.1f}", "BTOverallRatio": f"{corpus['llm_all_attempt_mean_ratio_to_scripted']:.3f}", "BTPositiveRatio": f"{corpus['llm_positive_attempt_mean_ratio_to_scripted']:.3f}", "BTScriptedLiveness": str(stats["liveness"]["scripted"]["reached_content"]), "BTLLMLiveness": str(stats["liveness"]["llm"]["reached_content"]), "BTScriptedLivenessPct": f"{stats['liveness']['scripted']['percent_all_attempts']:.1f}", "BTLLMLivenessPct": f"{stats['liveness']['llm']['percent_all_attempts']:.1f}", "BTLLMPositiveLivenessPct": f"{stats['liveness']['llm']['percent_positive_attempts']:.1f}"}
    for row in replay["workloads"]:
        prefix = "BTScripted" if row["workload"] == "scripted" else "BTLLM"
        macros[prefix + "Replay"] = f"{row['effective_request_denominator']:,}"
        macros[prefix + "ZeroSize"] = f"{row['ignored_zero_size_rows']:,}"
    for row in replay["results"]:
        if row["cache_mib"] == 5 and row["policy"] in {"LRU", "GDSF"}:
            prefix = "BTScripted" if row["workload"] == "scripted" else "BTLLM"
            macros[prefix + row["policy"] + "RequestPct"] = f"{100 * row['request_hit_ratio']:.1f}"
            macros[prefix + row["policy"] + "BytePct"] = f"{100 * row['byte_hit_ratio']:.1f}"
    for row in sizes["workloads"]:
        prefix = "BTScripted" if row["workload"] == "scripted" else "BTLLM"
        macros[prefix + "PositiveKeys"] = f"{row['positive_size_keys']:,}"
        macros[prefix + "VaryingKeys"] = f"{row['varying_positive_size_keys']:,}"
    (destination / "numbers.tex").write_text("% Generated from released inputs; do not edit.\n" + "".join(f"\\newcommand{{\\{key}}}{{{value}}}\n" for key, value in macros.items()))

    def table(name: str, columns: str, heading: str, rows: list[str]) -> None:
        (destination / name).write_text("% Generated from released inputs; do not edit.\n" + f"\\begin{{tabular}}{{{columns}}}\n\\toprule\n{heading} \\\\\n\\midrule\n" + "\n".join(row + r" \\" for row in rows) + "\n\\bottomrule\n\\end{tabular}\n")

    table("cohort-table.tex", "lrrrr", "Cohort & Attempts & Nonempty & Requests & Mean", [f"{label} & {v['attempts']:,} & {v['positive_traffic']:,} & {v['requests']:,} & {v['mean_requests_per_attempt']:.1f}" for label, v in [("Scripted (main)", corpus["scripted"]), ("LLM (main)", corpus["llm"]), ("LLM (local comparison)", stats["comparison_cohort"]["total"]), ("Human (one participant)", stats["human_reference"])]])
    table("model-table.tex", "lrrrrrr", "Model & Attempts & Nonempty & Empty & Mean & Median & Liveness", [f"{model} & {v['attempts']} & {v['positive_traffic']} & {v['zero_requests']} & {v['mean_requests_per_attempt']:.1f} & {v['median_requests_per_attempt']:.1f} & {stats['liveness']['llm_by_model'][model]['reached_content']}/{v['attempts']}" for model, v in stats["llm_by_model"].items()])
    table("amplification-table.tex", "lrrr", "Local cohort & Attempts & Mean requests & Ratio", [f"Scripted Zurich & {stats['comparison_cohort']['scripted_zurich']['attempts']} & {stats['comparison_cohort']['scripted_zurich']['mean_requests_per_attempt']:.1f} & 1.000" ] + [f"{model} & {v['attempts']} & {v['mean_requests_per_attempt']:.1f} & {v['ratio_to_scripted_zurich_mean']:.3f}" for model, v in stats["comparison_cohort"]["by_model"].items()])
    by_key = {(r["workload"], r["policy"]): r for r in replay["results"] if r["cache_mib"] == 5}
    table("cache-5mib-table.tex", "lrrrr", "Policy & Scripted request & Scripted byte & LLM request & LLM byte", [policy + " & " + " & ".join(f"{100 * by_key[workload,policy][metric]:.1f}\\%" for workload in ["scripted", "llm"] for metric in ["request_hit_ratio", "byte_hit_ratio"]) for policy in ["LRU", "LFU", "ARC", "S3-FIFO", "W-TinyLFU", "GDSF"]])


def plot(stats: dict, replay: dict, destination: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    destination.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False, "pdf.fonttype": 42, "ps.fonttype": 42, "savefig.bbox": "tight"})
    policies = ["LRU", "LFU", "ARC", "S3-FIFO", "W-TinyLFU", "GDSF"]
    colors = ["#1f77b4", "#9467bd", "#8c564b", "#2ca02c", "#ff7f0e", "#d62728"]
    fig, axes = plt.subplots(2, 2, figsize=(7.0, 4.6), sharex=True, sharey=True)
    for i, workload in enumerate(["scripted", "llm"]):
        for j, metric in enumerate(["request_hit_ratio", "byte_hit_ratio"]):
            ax = axes[i,j]
            for policy, color in zip(policies, colors):
                rows = [r for r in replay["results"] if r["workload"] == workload and r["policy"] == policy]
                ax.plot([r["cache_mib"] for r in rows], [100 * r[metric] for r in rows], marker="o", markersize=3, linewidth=1.3, color=color, label=policy)
            ax.set_title(f"{workload.capitalize()}: {'request' if j == 0 else 'byte'} hits")
            ax.set_xscale("log"); ax.set_xticks([1,5,10,25,50], ["1","5","10","25","50"]); ax.set_ylim(0,100); ax.grid(alpha=.2)
            if i == 1: ax.set_xlabel("Capacity (MiB)")
            if j == 0: ax.set_ylabel("Hit ratio (%)")
    fig.legend(*axes[0,0].get_legend_handles_labels(), loc="lower center", bbox_to_anchor=(.5,-.015), ncol=6, frameon=False)
    fig.tight_layout(rect=(0,.035,1,1));fig.savefig(destination / "cache-policy-tradeoff.pdf", metadata={"CreationDate":None,"ModDate":None});fig.savefig(destination / "cache-policy-tradeoff.png",dpi=200);plt.close(fig)
    models = list(stats["llm_by_model"])
    fig, ax = plt.subplots(figsize=(3.4,3.8))
    x = np.arange(len(models)); v=stats["llm_by_model"]
    ax.barh(x-.18,[v[m]["mean_requests_per_attempt"] for m in models],height=.36,label="All attempts",color="#1f77b4")
    ax.barh(x+.18,[v[m]["mean_requests_per_positive_attempt"] for m in models],height=.36,label="Nonempty attempts",color="#ff7f0e")
    ax.axvline(stats["paper_corpus"]["scripted"]["mean_requests_per_attempt"],color="#444444",linestyle="--",label="Main scripted mean")
    ax.set_yticks(x,[m.replace("claude-haiku-","claude-haiku\n").replace("qwen-2.5-coder-7b","qwen-2.5\ncoder-7b") for m in models],fontsize=8)
    ax.invert_yaxis();ax.set_xlabel("Mean recorded requests\nper attempt",fontsize=8);ax.tick_params(axis="x",labelsize=8);ax.grid(axis="x",alpha=.2);fig.legend(*ax.get_legend_handles_labels(),frameon=False,fontsize=8,ncol=1,loc="upper center");fig.tight_layout(rect=(0,0,1,.82));fig.savefig(destination / "model-request-counts.pdf",metadata={"CreationDate":None,"ModDate":None});fig.savefig(destination / "model-request-counts.png",dpi=200);plt.close(fig)
    tasks = list(stats["comparison_cohort"]["by_task"])
    fig,ax=plt.subplots(figsize=(3.4,4.2))
    for model,color in zip(stats["comparison_cohort"]["by_model"],["#2ca02c","#ff7f0e","#1f77b4"]):
        ax.plot([stats["comparison_cohort"]["by_task"][t]["models"][model]["ratio_to_task_scripted_zurich_mean"] for t in tasks],range(len(tasks)),marker="o",markersize=3,label=model,color=color)
    ax.axvline(1,color="#444444",linestyle="--",linewidth=1);ax.set_xscale("log");ax.set_xlabel("Mean requests / local\nscripted mean",fontsize=8);ax.set_yticks(range(len(tasks)),[t.removesuffix("-1").replace("-","\n") for t in tasks],fontsize=8);ax.invert_yaxis();ax.tick_params(axis="x",labelsize=8);ax.grid(alpha=.2);fig.legend(*ax.get_legend_handles_labels(),frameon=False,fontsize=8,ncol=1,loc="upper center");fig.tight_layout(rect=(0,0,1,.82));fig.savefig(destination / "task-amplification.pdf",metadata={"CreationDate":None,"ModDate":None});fig.savefig(destination / "task-amplification.png",dpi=200);plt.close(fig)


def generate(root: Path = ROOT, output_dir: Path | None = None, replay: dict | None = None) -> dict:
    stats = derive(root)
    replay = replay or json.loads((root / "reports/public-cache-replay.json").read_text())
    reports = output_dir or root / "reports"
    figures = output_dir / "figures" if output_dir else root / "figures"
    reports.mkdir(parents=True,exist_ok=True)
    (reports / "publication-statistics.json").write_text(json.dumps(stats,indent=2,sort_keys=True)+"\n")
    sizes = audit_sizes(root)
    (reports / "replay-size-audit.json").write_text(json.dumps(sizes,indent=2,sort_keys=True)+"\n")
    tex_tables(stats,replay,reports,sizes)
    plot(stats,replay,figures)
    return stats


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path)
    args=parser.parse_args()
    generate(output_dir=args.output_dir)
    print("Derived census statistics, five TeX inputs, and three figures from released inputs.")
