"""Build a self-contained, offline decisions-of-record console.

All artifact-controlled strings are embedded with script-safe JSON encoding and
rendered through DOM textContent. Model output, request text, router labels, and
other artifact values are never interpreted as HTML.

Run: python -m runtime.build_console [--results results]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.audit_record import PACKAGE_SCHEMA, validate_package  # noqa: E402

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AuditableAgents - decisions of record</title>
<style>
  :root { --fg:#1a1a1a; --muted:#667; --line:#e2e2e8; --accent:#4F46E5;
          --ok:#0a7d33; --bad:#c0392b; --warn:#b7791f; --bg:#fafafc; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif;
         color:var(--fg); background:var(--bg); }
  header { padding:14px 22px; background:#fff; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:17px; }
  header p { margin:3px 0 0; color:var(--muted); font-size:12.5px; }
  .wrap { display:flex; height:calc(100vh - 64px); }
  .list { flex:0 0 56%; overflow:auto; border-right:1px solid var(--line); }
  .detail { flex:1; overflow:auto; padding:16px 20px; background:#fff; }
  .filters { position:sticky; top:0; background:var(--bg); padding:10px 14px;
             border-bottom:1px solid var(--line); display:flex; gap:8px; flex-wrap:wrap; }
  select, input { font:inherit; padding:4px 6px; border:1px solid var(--line);
                  border-radius:6px; background:#fff; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  th, td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); }
  th { position:sticky; top:49px; background:var(--bg); font-weight:600;
       color:var(--muted); }
  tr.row { cursor:pointer; }
  tr.row:hover { background:#f0f0ff; }
  tr.row.sel { background:#e7e7ff; }
  .pill { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11.5px;
          border:1px solid var(--line); background:#fff; }
  .approve { color:var(--ok); border-color:var(--ok); }
  .reject { color:var(--bad); border-color:var(--bad); }
  .escalate { color:var(--warn); border-color:var(--warn); }
  .mismatch { background:#fdecea; }
  .detail h2 { margin:2px 0 4px; font-size:15px; }
  .detail h3 { margin:16px 0 4px; font-size:13px; color:var(--accent);
               text-transform:uppercase; letter-spacing:.04em; }
  .kv { display:grid; grid-template-columns:190px 1fr; gap:2px 12px; font-size:13px; }
  .kv .label { color:var(--muted); }
  pre { background:var(--bg); border:1px solid var(--line); border-radius:8px;
        padding:10px; white-space:pre-wrap; overflow-wrap:anywhere; font-size:12px; margin:6px 0; }
  .factor { display:inline-block; margin:2px 4px 2px 0; padding:2px 8px;
            border-radius:6px; font-size:11.5px; border:1px solid var(--line); }
  .f-good { background:#e8f7ee; border-color:var(--ok); }
  .f-bad { background:#fdecea; border-color:var(--bad); }
  .f-miss { background:#fff8e6; border-color:var(--warn); }
  .unavailable { background:#fff8e6; border:1px solid var(--warn);
                 border-radius:6px; padding:8px; }
  .note { color:var(--muted); font-size:12px; font-style:italic; }
  .empty { color:var(--muted); padding:40px; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>AuditableAgents - decisions of record</h1>
  <p id="meta"></p>
</header>
<div class="wrap">
  <div class="list">
    <div class="filters">
      <select id="f-variant"><option value="">variant: all</option></select>
      <select id="f-kind"><option value="">kind: all</option></select>
      <select id="f-decision"><option value="">decision: all</option></select>
      <select id="f-oracle"><option value="">oracle: all</option>
        <option value="match">matches oracle</option>
        <option value="mismatch">differs from oracle</option></select>
      <input id="f-search" placeholder="search id...">
    </div>
    <table>
      <thead><tr><th>request</th><th>kind</th><th>variant</th><th>routed</th>
        <th>decision</th><th>rule</th><th>repro</th><th>oracle</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
  <div class="detail" id="detail"><div class="empty">Select a decision to inspect its audit package.</div></div>
</div>
<script>
"use strict";
const PACKAGES = __PACKAGES__;
const ROUTING = __ROUTING__;
const META = __META__;
const state = { sel: null };

function element(tag, className, text) {
  const out = document.createElement(tag);
  if (className) out.className = className;
  if (text !== undefined && text !== null) out.textContent = String(text);
  return out;
}
function appendText(parent, text) {
  parent.appendChild(document.createTextNode(String(text)));
}
function heading(parent, label) {
  parent.appendChild(element("h3", "", label));
}
function kv(parent, label, value) {
  parent.appendChild(element("div", "label", label));
  parent.appendChild(element("div", "", value));
}
function kvBlock(parent, values) {
  const block = element("div", "kv");
  values.forEach(function (pair) { kv(block, pair[0], pair[1]); });
  parent.appendChild(block);
  return block;
}
function pre(parent, value) {
  parent.appendChild(element("pre", "", value));
}
function pill(decision) {
  return element("span", "pill " + decision, decision);
}
function unique(key) {
  return Array.from(new Set(PACKAGES.map(function (p) { return p[key]; }))).sort();
}
function fill(id, values) {
  const target = document.getElementById(id);
  values.forEach(function (value) {
    const option = element("option", "", value);
    option.value = value;
    target.appendChild(option);
  });
  target.addEventListener("change", render);
}
function visible() {
  const variant = document.getElementById("f-variant").value;
  const kind = document.getElementById("f-kind").value;
  const decision = document.getElementById("f-decision").value;
  const oracle = document.getElementById("f-oracle").value;
  const query = document.getElementById("f-search").value.toLowerCase();
  return PACKAGES.filter(function (item) {
    return (!variant || item.variant === variant)
      && (!kind || item.kind === kind)
      && (!decision || item.decision === decision)
      && (!oracle || (oracle === "match") === item.matches_oracle)
      && (!query || item.request_id.toLowerCase().includes(query));
  });
}
function factorTags(parent, check) {
  if (check.status !== "available") {
    const box = element("div", "unavailable");
    box.appendChild(element("strong", "", "Unavailable: "));
    appendText(box, check.reason || "incomplete evidence");
    box.appendChild(element(
      "div",
      "note",
      String((check.invalid_evidence || []).length) + " invalid canonical/perturbation evidence row(s)"
    ));
    parent.appendChild(box);
    return;
  }
  const block = element("div");
  (check.per_factor || []).forEach(function (factor) {
    let cssClass;
    let label;
    if (factor.omitted) {
      cssClass = "f-miss";
      label = factor.factor + " (sensitive, not cited)";
    } else if (factor.empirically_sensitive) {
      cssClass = "f-good";
      label = factor.factor + " (cited, sensitive)";
    } else {
      cssClass = "f-bad";
      label = factor.factor + " (cited, not sensitive)";
    }
    block.appendChild(element("span", "factor " + cssClass, label));
  });
  if (!block.childNodes.length) block.appendChild(element("span", "note", "no factors"));
  parent.appendChild(block);
}
function showDetail(item) {
  const pkg = item.pkg;
  const dor = pkg.decision_of_record;
  const rep = pkg.reproducibility_attestation;
  const prov = pkg.provenance;
  const ref = pkg.benchmark_reference;
  const input = pkg.decision_input;
  const evidence = pkg.supporting_evidence;
  const routed = ROUTING[item.request_id];
  const target = document.getElementById("detail");
  target.replaceChildren();

  const title = element("h2");
  appendText(title, item.request_id + " / " + item.variant + " ");
  title.appendChild(pill(item.decision));
  target.appendChild(title);
  target.appendChild(element("div", "note", "audit package " + pkg.schema));

  heading(target, "Decision of record");
  const decisionValues = [
    ["authority", dor.decision_authority],
    ["deterministic core", dor.deterministic_core],
    ["firing rule", dor.firing_rule || "(LLM-owned decision)"],
    ["rule factors", (dor.firing_rule_factors || []).join(", ") || "-"],
    ["escalated", dor.escalated],
  ];
  if (routed) {
    decisionValues.push(["router choice", routed.variant + " (rule: " + routed.router_rule + ")"]);
  }
  kvBlock(target, decisionValues);

  heading(target, "Observed input");
  pre(target, pkg.observed_input.free_text);
  kvBlock(target, [
    ["supplier record", JSON.stringify(pkg.observed_input.authoritative_supplier_record)],
    ["supplier record provided to LLM", pkg.observed_input.supplier_record_provided_to_llm],
  ]);

  heading(target, "Actual decision input");
  kvBlock(target, [
    ["type", input.type],
    ["owner", input.decision_owner],
    ["path", input.input_path.join(" -> ")],
    ["validation", input.validation ? JSON.stringify(input.validation) : "not applicable"],
    ["field sources", input.field_sources ? JSON.stringify(input.field_sources) : "not applicable"],
  ]);
  if (input.structured_record) pre(target, JSON.stringify(input.structured_record, null, 2));
  target.appendChild(element("div", "note", input.note));

  heading(target, "Explanation and faithfulness check");
  pre(target, pkg.explanation.text || "(none)");
  factorTags(target, pkg.explanation.faithfulness_check);
  target.appendChild(element(
    "div",
    "note",
    pkg.explanation.faithfulness_check.method
  ));

  heading(target, "Reproducibility attestation");
  kvBlock(target, [
    ["samples", String(rep.n_samples) + " @ T=" + (rep.temperature === undefined ? "-" : rep.temperature)],
    ["modal share", rep.modal_share === undefined ? "-" : rep.modal_share],
    ["unanimous", rep.unanimous === undefined ? "-" : rep.unanimous],
    ["decisions", (rep.decisions || []).join(", ")],
    ["error counts", JSON.stringify(rep.errors)],
  ]);

  const perturbationCount = Object.values(evidence.perturbations).reduce(
    function (total, rows) { return total + rows.length; },
    0
  );
  heading(target, "Supporting evidence and provenance");
  kvBlock(target, [
    ["model", String(prov.model) + " (" + String(prov.backend_label) + ")"],
    ["canonical cache records", evidence.canonical.cache_ids.length],
    ["reproducibility rows", evidence.reproducibility.length],
    ["perturbation rows", perturbationCount],
    ["artifact input sha256", prov.artifact_input_sha256],
    ["policy sha256", pkg.policy.policy_sha256],
  ]);

  heading(target, "Benchmark reference (PoC only)");
  kvBlock(target, [
    ["request kind", ref.request_kind],
    ["oracle decision", ref.oracle_decision],
    ["oracle rule", ref.oracle_rule],
    ["matches oracle", ref.matches_oracle],
  ]);
  pre(target, JSON.stringify(ref.generator_structured_record, null, 2));
  target.appendChild(element("div", "note", ref.note));
}
function render() {
  const body = document.getElementById("rows");
  body.replaceChildren();
  visible().forEach(function (item) {
    const row = element(
      "tr",
      "row" + (item.matches_oracle ? "" : " mismatch")
        + (state.sel === item.key ? " sel" : "")
    );
    const routed = ROUTING[item.request_id];
    const values = [
      item.request_id,
      item.kind,
      item.variant,
      routed ? (routed.variant === item.variant ? "● " + routed.variant : routed.variant) : "-",
    ];
    values.forEach(function (value) { row.appendChild(element("td", "", value)); });
    if (routed) row.childNodes[3].title = String(routed.router_rule || "");
    const decisionCell = element("td");
    decisionCell.appendChild(pill(item.decision));
    row.appendChild(decisionCell);
    row.appendChild(element("td", item.firing_rule ? "" : "note", item.firing_rule || "llm"));
    row.appendChild(element("td", "", item.modal_share === null ? "-" : item.modal_share));
    row.appendChild(element("td", "", item.matches_oracle ? "✓" : "✗"));
    row.addEventListener("click", function () {
      state.sel = item.key;
      render();
      showDetail(item);
    });
    body.appendChild(row);
  });
}

document.getElementById("meta").textContent = META.label;
fill("f-variant", unique("variant"));
fill("f-kind", unique("kind"));
fill("f-decision", unique("decision"));
document.getElementById("f-oracle").addEventListener("change", render);
document.getElementById("f-search").addEventListener("input", render);
render();
</script>
</body>
</html>
"""


def _script_json(value: Any) -> str:
    """Encode JSON without allowing data to terminate the containing script."""
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_console_html(
    packages: list[dict],
    routing: dict[str, dict],
    meta: dict[str, str],
) -> str:
    for item in packages:
        validate_package(item["pkg"])
    return (
        TEMPLATE.replace("__PACKAGES__", _script_json(packages))
        .replace("__ROUTING__", _script_json(routing))
        .replace("__META__", _script_json(meta))
    )


def load_console_data(results_dir: Path) -> tuple[list[dict], dict, dict]:
    packages_dir = results_dir / "audit_packages"
    index_path = packages_dir / "index.json"
    if not index_path.is_file():
        raise SystemExit(
            "run python -m runtime.audit_record first "
            "(no audit_packages/index.json)"
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(index, list):
        raise ValueError("audit package index must be a list")

    packages: list[dict] = []
    seen: set[str] = set()
    for entry in index:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise ValueError("invalid audit package index entry")
        if entry["file"] in seen:
            raise ValueError(f"duplicate audit package index entry: {entry['file']}")
        seen.add(entry["file"])
        package = json.loads(
            (packages_dir / entry["file"]).read_text(encoding="utf-8")
        )
        validate_package(package)
        dor = package["decision_of_record"]
        ref = package["benchmark_reference"]
        expected = {
            "request_id": dor["request_id"],
            "variant": dor["variant"],
            "decision": dor["decision"],
            "kind": ref["request_kind"],
            "matches_oracle": ref["matches_oracle"],
        }
        for key, value in expected.items():
            if entry.get(key) != value:
                raise ValueError(
                    f"{entry['file']}: index {key} disagrees with package"
                )
        packages.append(
            {
                "key": entry["file"],
                "request_id": dor["request_id"],
                "variant": dor["variant"],
                "kind": ref["request_kind"],
                "decision": dor["decision"],
                "firing_rule": dor["firing_rule"],
                "matches_oracle": ref["matches_oracle"],
                "modal_share": package["reproducibility_attestation"].get(
                    "modal_share"
                ),
                "pkg": package,
            }
        )

    routing: dict[str, dict] = {}
    router_note = "router evaluation not built"
    router_path = results_dir / "router_eval.json"
    if router_path.is_file():
        router = json.loads(router_path.read_text(encoding="utf-8"))
        raw_routing = router.get("per_request_routing", {})
        if not isinstance(raw_routing, dict):
            raise ValueError("router per_request_routing must be an object")
        for request_id, record in raw_routing.items():
            if (
                not isinstance(request_id, str)
                or not isinstance(record, dict)
                or record.get("variant") not in {"A0", "A1", "A2", "A3"}
                or not isinstance(record.get("router_rule"), str)
            ):
                raise ValueError(f"invalid router record for {request_id!r}")
        routing = raw_routing
        distribution = router.get("routing_distribution", {})
        if not isinstance(distribution, dict):
            raise ValueError("router routing_distribution must be an object")
        router_note = "router distribution " + ", ".join(
            f"{key}:{value}" for key, value in sorted(distribution.items())
        )

    first = packages[0]["pkg"]["provenance"] if packages else {}
    meta = {
        "label": (
            f"{len(packages)} audit packages | schema {PACKAGE_SCHEMA} | "
            f"model {first.get('model', '?')} | policy v1 | {router_note} | "
            "generated offline from the versioned artifact"
        )
    }
    return packages, routing, meta


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the decisions-of-record console")
    parser.add_argument("--results", default=str(ROOT / "results"))
    parser.add_argument(
        "--out",
        default=None,
        help="output html (default: <results>/console.html)",
    )
    args = parser.parse_args()
    results_dir = Path(args.results)
    out_path = Path(args.out) if args.out else results_dir / "console.html"
    packages, routing, meta = load_console_data(results_dir)
    html = build_console_html(packages, routing, meta)
    _write_text_atomic(out_path, html)
    print(
        f"wrote {out_path} "
        f"({out_path.stat().st_size // 1024} KiB, {len(packages)} packages)"
    )


if __name__ == "__main__":
    main()
