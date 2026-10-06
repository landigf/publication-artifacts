"""Emit LaTeX macros for the Absorbers paper from the generated JSON, so no result value is typed by hand.

    python -m harness.make_absorbers_numbers            # writes paper/absorbers/numbers.tex

Every macro resolves to a field of results*/absorbers.json or results-chain-*/absorbers_chain.json.
A missing sweep is skipped; a missing field in a present sweep is an error, not a blank.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper" / "absorbers" / "numbers.tex"

SWEEPS = {  # directory -> macro stem
    "results": "Flag", "results-reasoner": "Think", "results-local": "Local", "results-phi4-mini": "Phi",
}
LEGACY_SWEEPS = dict(SWEEPS)
CHAINS = {"results-chain-deepseek": "ChainDs", "results-chain-gemma3": "ChainGm",
          "results-chain-phi4": "ChainPh"}
PUBLICATION_SWEEPS = {"results": "Flag", "results-reasoner": "Think", "results-local": "Local"}
VARS = {"A0": "Azero", "A1": "Aone", "A2": "Atwo", "A3": "Athree"}
POLS = {"P0_recompute": "Pzero", "P1_input_hash": "Pone", "P2_dependency_cone": "Ptwo", "P3_oracle_irrelevance": "Pthree"}
STEPS = {"c_parse": "Parse", "c_justify": "Justify"}


def _need(d: dict, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            raise KeyError("missing " + "/".join(map(str, keys)))
        cur = cur[k]
    return cur


def pct(v) -> str:
    if v is None:
        return "n/a"
    return f"{float(v) * 100:.1f}\\%"


def one(v) -> str:
    return "n/a" if v is None else f"{float(v):.1f}"


def integer(v) -> str:
    return "n/a" if v is None else f"{int(v):,}".replace(",", "{,}")


def secs(ms) -> str:
    return "n/a" if ms is None else f"{float(ms) / 1000:.0f}"


def cmd(name: str, value: str) -> str:
    return f"\\newcommand{{\\abs{name}}}{{{value}}}"


def sweep_macros(stem: str, a: dict) -> list[str]:
    out = [cmd(f"{stem}Model", str(a.get("model") or "")), cmd(f"{stem}Nreq", integer(_need(a, "n_requests")))]
    for v, vs in VARS.items():
        if v not in a["variants"]:
            continue
        e1 = _need(a, "E1_absorption", v)
        out += [cmd(f"{stem}{vs}EoneDecVar", pct(e1["decision_varies_frac"])),
                cmd(f"{stem}{vs}EoneTextVar", pct(e1["output_text_varies_frac"])),
                cmd(f"{stem}{vs}EoneUnsafe", pct(e1["unsafe_in_samples_frac"])),
                cmd(f"{stem}{vs}EoneNrepro", integer(e1["n_repro"]))]
        if v in ("A2", "A3"):
            out += [cmd(f"{stem}{vs}EoneParseVar", pct(e1["parse_record_varies_frac"])),
                    cmd(f"{stem}{vs}EoneParseVarN", integer(e1["parse_record_varies"])),
                    cmd(f"{stem}{vs}EoneAbsorbed", integer(e1["absorbed"])),
                    cmd(f"{stem}{vs}EoneLeaked", integer(e1["leaked"])),
                    cmd(f"{stem}{vs}EoneAbsorb", pct(e1["absorption_ratio"]))]
            for oc in ("approve", "escalate", "reject"):
                s = e1["by_oracle_decision"].get(oc, {"requests": 0, "parse_record_varies": 0, "absorbed": 0, "leaked": 0})
                out += [cmd(f"{stem}{vs}Eone{oc.capitalize()}Var", integer(s["parse_record_varies"])),
                        cmd(f"{stem}{vs}Eone{oc.capitalize()}Abs", integer(s["absorbed"])),
                        cmd(f"{stem}{vs}Eone{oc.capitalize()}Leak", integer(s["leaked"])),
                        # the stratum size itself, so Limitations can state it per
                        # configuration instead of typing one sweep's value for all of them
                        cmd(f"{stem}{vs}Eone{oc.capitalize()}Req", integer(s["requests"]))]
            # Approve stratum crossed with which field varied. The paper's sharpest
            # claim is about category misparses only; urgency is a documented
            # distractor the policy ignores, so it must not be pooled with them.
            ap = e1.get("by_oracle_and_varying_fields", {}).get("approve", {})
            cat_l = sum(v["leaked"] for k, v in ap.items() if "category" in k)
            cat_a = sum(v["absorbed"] for k, v in ap.items() if "category" in k)
            urg_a = sum(v["absorbed"] for k, v in ap.items() if k == "urgency")
            out += [cmd(f"{stem}{vs}EoneApproveCatLeak", integer(cat_l)),
                    cmd(f"{stem}{vs}EoneApproveCatTotal", integer(cat_l + cat_a)),
                    cmd(f"{stem}{vs}EoneApproveUrgencyAbs", integer(urg_a))]
            e2 = _need(a, "E2_functionality_T0", v)
            out += [cmd(f"{stem}{vs}EtwoPairs", integer(e2["identical_input_pairs"])),
                    cmd(f"{stem}{vs}EtwoTextIdN", integer(e2["text_identical"])),
                    cmd(f"{stem}{vs}EtwoTextId", pct(e2["text_identical_frac"])),
                    cmd(f"{stem}{vs}EtwoRecIdN", integer(e2["record_identical"])),
                    cmd(f"{stem}{vs}EtwoRecId", pct(e2["record_identical_frac"])),
                    cmd(f"{stem}{vs}EtwoMethod", e2["prompt_identity_method"].replace("_", " ")),
                    cmd(f"{stem}{vs}EtwoSound", "sound" if e2["record_comparable_pairs"] and e2["record_identical_frac"] == 1.0 else "unsound"),
                    cmd(f"{stem}{vs}EtwoSoundUB", pct(e2.get("record_identical_upper_bound_request_level")) if e2.get("record_identical_upper_bound_request_level") is not None else "n/a"),
                    cmd(f"{stem}{vs}EtwoRecPairs", integer(e2["record_comparable_pairs"])),
                    cmd(f"{stem}{vs}EtwoReqSeen", integer(e2["requests_seen"])),
                    cmd(f"{stem}{vs}EtwoReqMismatch", integer(e2["requests_with_mismatch"]))]
            if v == "A3":   # sweep-level, emitted once
                out += [cmd(f"{stem}VintagePairs", integer(e2.get("mixed_vintage_pairs", 0))),
                        cmd(f"{stem}VintageIdentical", integer(e2.get("mixed_vintage_text_identical", 0)))]
            c5 = (a.get("E5_canary") or {}).get(v) or {}
            if c5.get("defined"):
                # Default macros are the per-recompute model, which is what a
                # deployment would actually see. The bound is named as a bound.
                pr, ub = c5["per_recompute"], c5["upper_bound"]
                # The n behind each per-request rate, read off the profile keys.
                dens = {int(k.split("/")[1]) for k in c5["pair_mismatch_profile"]}
                out += [cmd(f"{stem}{vs}CanaryReq", integer(c5["requests"])),
                        cmd(f"{stem}{vs}CanaryBadReq", integer(c5["mismatching_requests"])),
                        cmd(f"{stem}{vs}EtwoPairsPerReq", integer(min(dens)) if len(dens) == 1 else "n/a")]
                if pr["k_for_95"] and ub["k_for_95"]:
                    out.append(cmd(f"{stem}{vs}CanaryBoundRatio", one(pr["k_for_95"] / ub["k_for_95"])))
                for tag, mdl in (("", pr), ("Bound", ub)):
                    at = mdl["detection_prob_at_k"]
                    k = mdl["k_for_95"]
                    out += [cmd(f"{stem}{vs}Canary{tag}AtOne", pct(at.get("1"))),
                            cmd(f"{stem}{vs}Canary{tag}AtTen", pct(at.get("10"))),
                            cmd(f"{stem}{vs}Canary{tag}K", integer(k) if k is not None else "n/a"),
                            cmd(f"{stem}{vs}Canary{tag}KFrac", pct(mdl["k_for_95_frac_of_requests"]) if k is not None else "n/a"),
                            cmd(f"{stem}{vs}Canary{tag}AtK", pct(mdl["detection_prob_at_k95"]) if k is not None else "n/a"),
                            cmd(f"{stem}{vs}Canary{tag}Ceiling", pct(mdl.get("detection_ceiling")))]
            # E6: the same E1/E3 quantities under the alias normaliser, macro name
            # with "Alias" after the variant stem, so \absThinkAthreeEthreePzeroOracle
            # has the twin \absThinkAthreeAliasEthreePzeroOracle.
            e6 = (a.get("E6_alias_normaliser") or {})
            e6e1 = (e6.get("E1_absorption") or {}).get(v)
            e6e3 = (e6.get("E3_reuse") or {}).get(v)
            if e6e1 and e6e3:
                ap6 = e6e1.get("by_oracle_and_varying_fields", {}).get("approve", {})
                out += [cmd(f"{stem}{vs}AliasEoneDecVar", pct(e6e1["decision_varies_frac"])),
                        cmd(f"{stem}{vs}AliasEoneLeaked", integer(e6e1["leaked"])),
                        cmd(f"{stem}{vs}AliasEoneAbsorbed", integer(e6e1["absorbed"])),
                        cmd(f"{stem}{vs}AliasEoneAbsorb", pct(e6e1["absorption_ratio"])),
                        cmd(f"{stem}{vs}AliasEoneApproveCatLeak", integer(sum(c["leaked"] for k, c in ap6.items() if "category" in k))),
                        cmd(f"{stem}{vs}AliasRows", integer(e6e3["alias_rows"])),
                        cmd(f"{stem}{vs}AliasChanged", integer(e6e3["rows_changed"])),
                        cmd(f"{stem}{vs}AliasWrongKnown", integer(e6e3["alias_wrong_known"]))]
                for p6, ps6 in POLS.items():
                    pol6 = e6e3["policies"][p6]
                    out += [cmd(f"{stem}{vs}AliasEthree{ps6}Oracle", pct(pol6["oracle_correct_frac"])),
                            cmd(f"{stem}{vs}AliasEthree{ps6}Unsafe", integer(pol6["unsafe_introduced"])),
                            cmd(f"{stem}{vs}AliasEthree{ps6}UnsafeAbs", integer(pol6.get("unsafe_absolute", 0))),
                            cmd(f"{stem}{vs}AliasEthree{ps6}Div", pct(pol6["divergence_frac"]))]
            e3 = _need(a, "E3_reuse", v)
            out += [cmd(f"{stem}{vs}EthreeRows", integer(e3["pert_rows"])),
                    cmd(f"{stem}{vs}EthreeCostSec", one((e3["parse_cost_mean_latency_ms"] or 0) / 1000.0)),
                    cmd(f"{stem}{vs}EthreeCostMs", one(e3["parse_cost_mean_latency_ms"])),
                    cmd(f"{stem}{vs}EthreeCostTok", one(e3["parse_cost_mean_tokens"]))]
            for p, ps in POLS.items():
                pol = e3["policies"][p]
                ub = pol.get("unsafe_upper_bound_request_level")
                out += [cmd(f"{stem}{vs}Ethree{ps}UnsafeUB", pct(ub) if ub is not None else "n/a"),
                        cmd(f"{stem}{vs}Ethree{ps}Saved", pct(pol["calls_saved_frac"])),
                        cmd(f"{stem}{vs}Ethree{ps}SavedN", integer(pol["reused"])),
                        cmd(f"{stem}{vs}Ethree{ps}Div", pct(pol["divergence_frac"])),
                        cmd(f"{stem}{vs}Ethree{ps}DivN", integer(pol["diverged"])),
                        cmd(f"{stem}{vs}Ethree{ps}Oracle", pct(pol["oracle_correct_frac"])),
                        cmd(f"{stem}{vs}Ethree{ps}Unsafe", integer(pol["unsafe_introduced"])),
                        cmd(f"{stem}{vs}Ethree{ps}SavedSec", secs(pol["latency_ms_saved_total"])),
                        cmd(f"{stem}{vs}Ethree{ps}SavedSecPerTraj", one((pol["latency_ms_saved_total"] or 0) / 1000.0 / max(1, e3["pert_rows"])))]
    return out


def chain_macros(stem: str, a: dict) -> list[str]:
    out = [cmd(f"{stem}Model", str(a.get("model") or "")), cmd(f"{stem}Nreq", integer(_need(a, "n_requests")))]
    for s, ss in STEPS.items():
        e2 = _need(a, "E2_functionality_T0", s)
        out += [cmd(f"{stem}{ss}EtwoSound", "sound" if e2["record_comparable_pairs"] and e2["record_identical_frac"] == 1.0 else "unsound"),
                cmd(f"{stem}{ss}EtwoSoundUB", pct(e2.get("record_identical_upper_bound_request_level")) if e2.get("record_identical_upper_bound_request_level") is not None else "n/a"),
                cmd(f"{stem}{ss}EtwoRecPairs", integer(e2["record_comparable_pairs"])),
                cmd(f"{stem}{ss}EtwoReqSeen", integer(e2.get("requests_with_comparable_pairs", 0))),
                cmd(f"{stem}{ss}EtwoPairs", integer(e2["identical_input_pairs"])),
                cmd(f"{stem}{ss}EtwoTextId", pct(e2["text_identical_frac"])),
                cmd(f"{stem}{ss}EtwoRecId", pct(e2["record_identical_frac"]))]
        st = _need(a, "E1_absorption", "per_step", s)
        out += [cmd(f"{stem}{ss}EoneVar", pct(st["varies_frac"])), cmd(f"{stem}{ss}EoneAbsorb", pct(st["absorption_ratio"])),
                cmd(f"{stem}{ss}EoneVarN", integer(st["varies"])),
                cmd(f"{stem}{ss}EoneAbsorbedN", integer(st["absorbed"])),
                cmd(f"{stem}{ss}EoneLeakedN", integer(st["leaked"]))]
    e1 = _need(a, "E1_absorption")
    out += [cmd(f"{stem}EoneDecVar", pct(e1["decision_varies_frac"])), cmd(f"{stem}EoneAnyVar", pct(e1["any_step_varies_frac"])),
            cmd(f"{stem}EoneAbsorbAny", pct(e1["absorption_ratio_any"])),
            cmd(f"{stem}EoneLeakedAny", integer(e1["leaked_any"])),
            cmd(f"{stem}EoneDecVarN", integer(e1["decision_varies"]))]
    for oc in ("approve", "escalate", "reject"):
        st = e1.get("by_oracle_decision", {}).get(oc, {})
        out += [cmd(f"{stem}Eone{oc.capitalize()}Req", integer(st.get("requests", 0)))]
    e3 = _need(a, "E3_reuse")
    # Requests whose canonical parse did not validate are excluded from the reuse
    # analysis, so a configuration where the model cannot hold the schema has a
    # smaller denominator and the paper has to say so.
    feeding_ms = sum((c.get("mean_latency_ms") or 0) for c in e3.get("step_cost", {}).values())
    out += [cmd(f"{stem}FeedingSecPerTraj", one(feeding_ms / 1000.0)),
            cmd(f"{stem}EthreeRows", integer(e3["pert_rows"])),
            cmd(f"{stem}EthreeEligible", integer(e3["eligible_requests"])),
            cmd(f"{stem}EthreeSkipped", integer(e3["skipped_invalid_canon"]))]
    for p, ps in POLS.items():
        pol = e3["policies"][p]
        ub = pol.get("unsafe_upper_bound_request_level")
        out += [cmd(f"{stem}Ethree{ps}Saved", pct(pol["calls_saved_frac"])), cmd(f"{stem}Ethree{ps}Both", pct(pol["both_steps_saved_frac"])),
                cmd(f"{stem}Ethree{ps}Triggered", pct(pol.get("calls_triggered_frac"))),
                cmd(f"{stem}Ethree{ps}Unscoreable", integer(pol.get("unscoreable_rows", 0))),
                cmd(f"{stem}Ethree{ps}UnsafeAbs", integer(pol.get("unsafe_absolute", 0))),
                cmd(f"{stem}Ethree{ps}UnsafeAbsReq", integer(pol.get("unsafe_absolute_requests", 0))),
                cmd(f"{stem}Ethree{ps}UnsafeUB", pct(ub) if ub is not None else "n/a"),
                cmd(f"{stem}Ethree{ps}Div", pct(pol["divergence_frac"])), cmd(f"{stem}Ethree{ps}DivN", integer(pol["diverged"])),
                cmd(f"{stem}Ethree{ps}Oracle", pct(pol["oracle_correct_frac"])),
                cmd(f"{stem}Ethree{ps}Unsafe", integer(pol["unsafe_introduced"])),
                cmd(f"{stem}Ethree{ps}UnsafeReq", integer(pol.get("unsafe_requests", 0))),
                cmd(f"{stem}Ethree{ps}SavedSec", secs(pol["latency_ms_saved_total"])),
                cmd(f"{stem}Ethree{ps}SavedSecPerTraj", one((pol["latency_ms_saved_total"] or 0) / 1000.0 / max(1, e3["pert_rows"])))]
    for st, ss in STEPS.items():
        c5 = (a.get("E5_canary") or {}).get(st) or {}
        if c5.get("defined"):
            out += [cmd(f"{stem}{ss}CanaryReq", integer(c5["requests"])),
                    cmd(f"{stem}{ss}CanaryBadReq", integer(c5["mismatching_requests"]))]
            if c5["per_recompute"]["k_for_95"] and c5["upper_bound"]["k_for_95"]:
                out.append(cmd(f"{stem}{ss}CanaryBoundRatio", one(c5["per_recompute"]["k_for_95"] / c5["upper_bound"]["k_for_95"])))
            for tag, mdl in (("", c5["per_recompute"]), ("Bound", c5["upper_bound"])):
                at = mdl["detection_prob_at_k"]
                k = mdl["k_for_95"]
                out += [cmd(f"{stem}{ss}Canary{tag}AtOne", pct(at.get("1"))),
                        cmd(f"{stem}{ss}Canary{tag}AtTen", pct(at.get("10"))),
                        cmd(f"{stem}{ss}Canary{tag}K", integer(k) if k is not None else "n/a"),
                        cmd(f"{stem}{ss}Canary{tag}KFrac", pct(mdl["k_for_95_frac_of_requests"]) if k is not None else "n/a"),
                        cmd(f"{stem}{ss}Canary{tag}AtK", pct(mdl["detection_prob_at_k95"]) if k is not None else "n/a"),
                        cmd(f"{stem}{ss}Canary{tag}Ceiling", pct(mdl.get("detection_ceiling")))]
    e7 = a.get("E7_share_sensitivity") or {}
    for sh, v in (e7.get("by_share") or {}).items():
        # LaTeX macro names cannot carry digits, so the share is spelled out.
        tag = {"0.15": "Fifteen", "0.25": "TwentyFive", "0.35": "ThirtyFive", "0.50": "Fifty"}.get(sh, "S" + sh.replace("0.", "").replace(".", ""))
        out += [cmd(f"{stem}Share{tag}Fires", integer(v["oracle_rule_fires"])), cmd(f"{stem}Share{tag}PtwoUnsafe", integer(v["p2_unsafe_introduced"])),
                cmd(f"{stem}Share{tag}PtwoUnsafeReq", integer(v["p2_unsafe_requests"])), cmd(f"{stem}Share{tag}PtwoDiv", integer(v["p2_diverged"])),
                cmd(f"{stem}Share{tag}PzeroUnsafeAbs", integer(v["p0_unsafe_absolute"]))]
    if e7.get("shares"):
        out.append(cmd(f"{stem}ShareGrid", ", ".join(f"{x:.2f}" for x in e7["shares"])))
    e6 = (a.get("E6_alias_normaliser") or {})
    if e6.get("E3_reuse"):
        e6e3, e6e1 = e6["E3_reuse"], e6["E1_absorption"]
        out += [cmd(f"{stem}AliasRows", integer(e6e3["alias_rows"])),
                cmd(f"{stem}AliasChanged", integer(e6e3["rows_changed"])),
                cmd(f"{stem}AliasWrongKnown", integer(e6e3["alias_wrong_known"])),
                cmd(f"{stem}AliasEoneDecVar", pct(e6e1["decision_varies_frac"]))]
        for p6, ps6 in POLS.items():
            pol6 = e6e3["policies"][p6]
            out += [cmd(f"{stem}AliasEthree{ps6}Oracle", pct(pol6["oracle_correct_frac"])),
                    cmd(f"{stem}AliasEthree{ps6}Unsafe", integer(pol6["unsafe_introduced"])),
                    cmd(f"{stem}AliasEthree{ps6}UnsafeReq", integer(pol6.get("unsafe_requests", 0))),
                    cmd(f"{stem}AliasEthree{ps6}UnsafeAbs", integer(pol6.get("unsafe_absolute", 0))),
                    cmd(f"{stem}AliasEthree{ps6}Div", pct(pol6["divergence_frac"]))]
    cs = e3["cross_step_sensitivity"]
    # the complement: how often a step's record CHANGED when only a sentence it was
    # told to ignore changed. This is the declared-cone-versus-real-cone number.
    contam = cs["c_parse_on_justification_toggle"]["record_identical_frac"]
    for ck, cs_name in (("c_parse_on_justification_toggle", "CrossParseOnJust"), ("c_justify_on_request_side", "CrossJustOnReq"),
                        ("c_parse_on_supplier_side", "CrossParseOnSup"), ("c_justify_on_supplier_side", "CrossJustOnSup")):
        c = cs[ck]
        out += [cmd(f"{stem}{cs_name}Pairs", integer(c["pairs"])), cmd(f"{stem}{cs_name}N", integer(c["record_identical"])),
                cmd(f"{stem}{cs_name}ChangedN", integer(c["pairs"] - c["record_identical"]))]
    out += [cmd(f"{stem}CrossParseOnJustContam", pct(None if contam is None else 1.0 - contam)),
            cmd(f"{stem}CrossParseOnJust", pct(cs["c_parse_on_justification_toggle"]["record_identical_frac"])),
            cmd(f"{stem}CrossJustOnReq", pct(cs["c_justify_on_request_side"]["record_identical_frac"])),
            cmd(f"{stem}CrossParseOnSup", pct(cs["c_parse_on_supplier_side"]["record_identical_frac"])),
            cmd(f"{stem}CrossJustOnSup", pct(cs["c_justify_on_supplier_side"]["record_identical_frac"]))]
    return out


def require_publication_inputs(root: Path) -> None:
    for directory in [*PUBLICATION_SWEEPS, *CHAINS]:
        path = root / directory / ("absorbers_chain.json" if directory in CHAINS else "absorbers.json")
        if not path.is_file() or not (root / directory / "decisions.jsonl").is_file() or not (root / directory / "raw").is_dir():
            raise FileNotFoundError(f"Required publication sweep missing: {directory}")
        data = json.loads(path.read_text())
        if data.get("n_requests") != 65:
            raise ValueError(f"Publication requires all 65 seeded requests in {directory}")
        if directory in CHAINS:
            validate_e8(data)


def validate_e8(data: dict) -> None:
    cert = _need(data, "E8_decomposition", "certificates")
    rows, excluded = int(cert["rows"]), int(cert["unscoreable_rows"])
    if rows != _need(data,"E3_reuse","pert_rows") or not 0 <= excluded <= rows:
        raise ValueError("Invalid E8 row/exclusion denominator")
    if cert["rows_fresh_invalid"] != excluded or cert["unscoreable_rows_where_a_candidate_step_was_invalid"] > excluded:
        raise ValueError("Invalid E8 fresh-invalid accounting")
    if not cert["cone_matches_p2_trigger_on_every_row"] or cert["cone_mismatch_rows"] != 0:
        raise ValueError("E8 observable cone differs from the declared cone")
    for name in ["cert_eq","cert_safe_valid","cert_safe_all"]:
        scheme = _need(cert,"schemes",name)
        certified = scheme["certified_rows"]
        reused = sum(scheme["step_calls_reused"].values())
        if not 0 <= certified <= rows-excluded or not 0 <= reused <= 2*certified:
            raise ValueError("Invalid E8 certified/reused count")
        if abs(scheme["certified_frac"] - certified/rows) > 5.1e-7 or abs(scheme["calls_saved_frac"] - reused/(2*rows)) > 5.1e-7:
            raise ValueError("E8 frozen fraction uses an inconsistent denominator")
        if scheme["certified_rows_fresh_invalid"] or scheme["unsafe_introduced"] or scheme["unsafe_requests"]:
            raise ValueError("E8 retained certificate invariant violated")
        if name == "cert_eq" and (scheme["decision_changes"] or scheme["decision_changes_fresh_valid"]):
            raise ValueError("cert_eq retained valid-witness equality invariant violated")


def e8_macros(stem: str, data: dict) -> list[str]:
    validate_e8(data)
    cert = data["E8_decomposition"]["certificates"]
    scheme = cert["schemes"]["cert_eq"]
    values = {"Rows":integer(cert["rows"]),"Scoreable":integer(cert["rows"]-cert["unscoreable_rows"]),"Unscoreable":integer(cert["unscoreable_rows"]),"FreshInvalid":integer(cert["rows_fresh_invalid"]),"CandidateInvalid":integer(cert["unscoreable_rows_where_a_candidate_step_was_invalid"]),"CertifiedN":integer(scheme["certified_rows"]),"CertifiedPct":pct(scheme["certified_frac"]),"SavedN":integer(sum(scheme["step_calls_reused"].values())),"SavedPct":pct(scheme["calls_saved_frac"]),"Changes":integer(scheme["decision_changes"]),"Unsafe":integer(scheme["unsafe_introduced"])}
    return [cmd(stem+"Eeight"+name,value) for name,value in values.items()]


def publication_tables(root: Path, output: Path) -> None:
    output.mkdir(parents=True,exist_ok=True)
    table = ["% Generated E8 table. Certified fraction uses ALL rows; saved slots use 2*ALL rows.",r"\begin{tabular}{llrrrr}",r"\toprule",r"Model & Scheme & Certified & Saved slots & Changes & Unsafe \\",r"\midrule"]
    eq_table = ["% Generated primary cert_eq table. Invalid/excluded rows remain in fraction denominators.",r"\begin{tabular}{lrrrrrr}",r"\toprule",r"Model & Rows & Excluded & Certified & Saved slots & Changes & Unsafe \\",r"\midrule"]
    report = {"schema_version":1,"seed":42,"sweeps":[],"E8_denominators":{"certified_fraction":"certified_rows / all E8 rows, including unscoreable rows","saved_call_fraction":"sum of reused feeding-step slots / (2 * all E8 rows); nominal slots include fail-fast missing calls","scoreable_rows":"all E8 rows minus unscoreable rows"},"certificate_boundary":"cert_eq enumerates valid value classes of the fixed deterministic consumer. Invalid fresh outputs are unscoreable; no deployment guarantee or oracle-provided runtime certificate is asserted."}
    for directory,stem in PUBLICATION_SWEEPS.items():
        data=json.loads((root/directory/"absorbers.json").read_text())
        report["sweeps"].append({"directory":directory,"model":data["model"],"n_requests":data["n_requests"],"kind":"two-step"})
    for directory,stem in CHAINS.items():
        data=json.loads((root/directory/"absorbers_chain.json").read_text());validate_e8(data)
        cert=data["E8_decomposition"]["certificates"]
        label={"ChainDs":"DeepSeek","ChainGm":"Gemma3","ChainPh":"Phi4-mini"}[stem]
        for name in ["cert_eq","cert_safe_valid","cert_safe_all"]:
            scheme=cert["schemes"][name]
            table.append(f"{label} & {name.replace('_',r'\_')} & {pct(scheme['certified_frac'])} & {pct(scheme['calls_saved_frac'])} & {scheme['decision_changes']} & {scheme['unsafe_introduced']} \\\\")
        scheme=cert["schemes"]["cert_eq"]
        eq_table.append(f"{label} & {integer(cert['rows'])} & {cert['unscoreable_rows']} & {scheme['certified_rows']} & {pct(scheme['calls_saved_frac'])} & {scheme['decision_changes']} & {scheme['unsafe_introduced']} \\\\")
        report["sweeps"].append({"directory":directory,"model":data["model"],"n_requests":data["n_requests"],"kind":"chain","E3_reuse":data["E3_reuse"],"E8_decomposition":data["E8_decomposition"],"E5_canary":data["E5_canary"]})
    for name,lines in [("certificate-table.tex",table),("certificate-eq-table.tex",eq_table)]:
        (output/name).write_text("\n".join([*lines,r"\bottomrule",r"\end{tabular}"])+"\n")
    (output/"publication-data.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")


def main(argv=None) -> int:
    global ROOT,OUT,SWEEPS
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication",action="store_true",help="Require all six complete sweeps and emit E8 publication artifacts")
    parser.add_argument("--root",type=Path,default=ROOT)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args(argv)
    ROOT=args.root.resolve()
    OUT=args.output or (ROOT/"publication/reports/numbers.tex" if args.publication else ROOT/"paper/absorbers/numbers.tex")
    if args.publication:
        require_publication_inputs(ROOT)
        SWEEPS=dict(PUBLICATION_SWEEPS)
    else:
        SWEEPS=dict(LEGACY_SWEEPS)
    lines = ["% Generated by harness/make_absorbers_numbers.py. Do not edit by hand.",
             "% Every macro resolves to a field of results*/absorbers.json or results-chain-*/absorbers_chain.json."]
    present = []
    for d, stem in SWEEPS.items():
        p = ROOT / d / "absorbers.json"
        if p.exists():
            lines += sweep_macros(stem, json.loads(p.read_text())); present.append(d)
    for d, stem in CHAINS.items():
        p = ROOT / d / "absorbers_chain.json"
        if p.exists():
            lines += chain_macros(stem, json.loads(p.read_text())); present.append(d)
            if args.publication:
                lines += e8_macros(stem,json.loads(p.read_text()))
    # One derived quantity used in prose: how much of the flagship saving a policy
    # with perfect oracle knowledge gets that a structural policy cannot.
    fp = ROOT / "results" / "absorbers.json"
    if fp.exists():
        pol = json.loads(fp.read_text())["E3_reuse"]["A3"]["policies"]
        gap = (pol["P3_oracle_irrelevance"]["calls_saved_frac"] or 0) - (pol["P2_dependency_cone"]["calls_saved_frac"] or 0)
        lines.append(cmd("GapPtwoPthree", pct(gap)))
    # Emitted so the "sound for N of the five" sentence cannot drift from the JSON.
    sound = total = 0
    for d in SWEEPS:
        p2 = ROOT / d / "absorbers.json"
        if p2.exists():
            e2 = json.loads(p2.read_text())["E2_functionality_T0"].get("A3")
            if e2 and e2["record_comparable_pairs"]:
                total += 1
                sound += (e2["record_identical_frac"] == 1.0)
    for d in CHAINS:
        p2 = ROOT / d / "absorbers_chain.json"
        if p2.exists():
            for e2 in json.loads(p2.read_text())["E2_functionality_T0"].values():
                if e2["record_comparable_pairs"]:
                    total += 1
                    sound += (e2["record_identical_frac"] == 1.0)
    words = {0: "none", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
             8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}
    lines += [cmd("SoundConfigs", words.get(sound, str(sound))), cmd("TotalConfigs", words.get(total, str(total))),
              cmd("UnsoundConfigs", words.get(total - sound, str(total - sound)))]
    # The depth claim is that the cone reuses strictly more than the input hash. It is a
    # claim about every chain configuration collected, so it is counted here rather than
    # typed: if a later model does not separate, the sentence in the paper changes itself.
    chains = separating = 0
    for d in CHAINS:
        p2 = ROOT / d / "absorbers_chain.json"
        if p2.exists():
            pols = json.loads(p2.read_text())["E3_reuse"]["policies"]
            chains += 1
            separating += (pols["P2_dependency_cone"]["calls_saved_frac"]
                           > pols["P1_input_hash"]["calls_saved_frac"])
    moved = total6 = below = 0
    for d in list(SWEEPS) + list(CHAINS):
        pj = ROOT / d / ("absorbers.json" if d in SWEEPS else "absorbers_chain.json")
        if not pj.exists():
            continue
        a6 = json.loads(pj.read_text())
        pairs = []
        if d in SWEEPS:
            for v, e in (a6.get("E6_alias_normaliser") or {}).get("E3_reuse", {}).items():
                if v == "A3":
                    pairs.append((a6["E3_reuse"][v]["policies"]["P0_recompute"]["oracle_correct_frac"],
                                  e["policies"]["P0_recompute"]["oracle_correct_frac"]))
        else:
            e = (a6.get("E6_alias_normaliser") or {}).get("E3_reuse")
            if e:
                pairs.append((a6["E3_reuse"]["policies"]["P0_recompute"]["oracle_correct_frac"],
                              e["policies"]["P0_recompute"]["oracle_correct_frac"]))
        for f0, g0 in pairs:
            total6 += 1
            if f0 < 1.0:
                below += 1
                moved += (g0 > f0)
    # AliasConfigsBelow is the denominator the sentence "rose on ... of the configurations it was
    # below one hundred on" needs; AliasConfigsTotal counts every configuration with an E6 block.
    lines += [cmd("AliasConfigsMoved", words.get(moved, str(moved))), cmd("AliasConfigsBelow", words.get(below, str(below))),
              cmd("AliasConfigsTotal", words.get(total6, str(total6)))]
    lines += [cmd("ChainConfigs", words.get(chains, str(chains))),
              cmd("ChainSeparating", words.get(separating, str(separating)))]
    # The spread the prose calls "agree to within": generated, so a fourth chain
    # configuration that disagrees widens the sentence instead of falsifying it.
    saves = []
    for d in CHAINS:
        p2 = ROOT / d / "absorbers_chain.json"
        if p2.exists():
            pol = json.loads(p2.read_text())["E3_reuse"]["policies"]["P2_dependency_cone"]
            # The TRIGGERED share is the structural quantity. calls_saved_frac is
            # smaller wherever a recomputed record could not be scored, which is a
            # property of the model and does not belong in a spread the prose calls
            # a property of the graph.
            saves.append(pol.get("calls_triggered_frac") or pol["calls_saved_frac"])
    if len(saves) > 1:
        lines.append(cmd("ChainSavingSpread", pct(max(saves) - min(saves))))
        lines.append(cmd("ChainSavingSpreadPts", f"{100 * (max(saves) - min(saves)):.2f}"))
    # Policy-v2 constants and their effect on the oracle. These are properties of
    # the generator and the policy, not of any sweep, so they are computed here
    # rather than read off a docstring: the paper used to print them by hand and a
    # sed on the constant once left the prose contradicting the code.
    try:
        import dataclasses
        from chain.taskgen import generate_chain
        from chain.policy_v2 import JUSTIFICATION_SHARE, oracle_v2
        import inspect as _inspect
        from chain.taskgen import generate_chain as _gc
        _share_inadequate = _inspect.signature(_gc).parameters["share_inadequate"].default
        from agents.policy_core import oracle as oracle_v1
        reqs = generate_chain(42)
        fires = changed = moved = inadequate = 0
        for r in reqs:
            o2 = oracle_v2(r)
            fires += (getattr(o2.firing_rule, "value", str(o2.firing_rule)) == "inadequate_justification")
            changed += (o2.decision.value != oracle_v1(r).decision.value)
            inadequate += (not r.justification_adequate)
            flipped = dataclasses.replace(r, justification_adequate=not r.justification_adequate)
            moved += (oracle_v2(flipped).decision.value != o2.decision.value)
        lines += [cmd("ChainNreq", integer(len(reqs))),
                  cmd("ChainJustShare", pct(JUSTIFICATION_SHARE)),
                  cmd("ChainRuleFires", integer(fires)),
                  cmd("ChainOracleChanged", integer(changed)),
                  cmd("ChainToggleMoves", integer(moved)),
                  cmd("ChainInadequate", integer(inadequate)),
                  cmd("ChainInadequateShare", pct(_share_inadequate)),
                  cmd("ChainInadequateFrac", pct(inadequate / len(reqs)))]
    except Exception as exc:   # the chain package is optional for the flagship macros
        if args.publication:
            raise
        print(f"chain constants not emitted: {exc}", file=sys.stderr)
    # Coverage, stated as which rules never fired rather than as a pass rate: a
    # result resting on a rule the workload never exercised is not a result.
    import collections
    fired = collections.Counter()
    for d in list(SWEEPS) + list(CHAINS):
        f = ROOT / d / "decisions.jsonl"
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("variant") in ("A3", "C3"):
                fired[r["firing_rule"]] += 1
    if fired:
        FAIL_SAFE = {"parse_failure", "justify_failure"}
        TERMINAL = {"clean_approve"}   # no rule fired: the cascade fell through to approve
        rules = {k: v for k, v in fired.items() if k not in FAIL_SAFE and k not in TERMINAL}
        lines += [cmd("RulesFired", integer(len(rules))),
                  cmd("RulesRarest", str(min(rules, key=rules.get)).replace("_", "\\_")),
                  cmd("RulesRarestN", integer(min(rules.values()))),
                  cmd("FailSafeRows", integer(sum(v for k, v in fired.items() if k in FAIL_SAFE))),
                  cmd("CleanApproveRows", integer(sum(v for k, v in fired.items() if k in TERMINAL)))]
    # Collection dates, from the earliest and latest "created" stamp in each raw cache,
    # so the paper can say when each configuration was collected and a reader can see
    # which comparisons straddle weeks.
    import datetime as _dt, os as _os
    stamps: dict[str, tuple[float, float]] = {}
    for d, stem in list(SWEEPS.items()) + list(CHAINS.items()):
        rawdir = ROOT / d / "raw"
        if not rawdir.is_dir():
            continue
        lo = hi = None
        for ent in _os.scandir(rawdir):
            if not ent.name.endswith(".json"):
                continue
            try:
                c = json.loads(Path(ent.path).read_text()).get("created")
            except Exception:
                continue
            if c is None:
                continue
            c = float(c)
            lo = c if lo is None or c < lo else lo
            hi = c if hi is None or c > hi else hi
        if lo is not None:
            f = lambda t: _dt.datetime.fromtimestamp(t,_dt.timezone.utc).strftime("%-d %B %Y")
            lines += [cmd(f"{stem}CollectedFrom", f(lo)), cmd(f"{stem}CollectedTo", f(hi)),
                      cmd(f"{stem}CollectedSpan", f(lo) if f(lo) == f(hi) else f"{f(lo)} to {f(hi)}")]
            stamps[stem] = (lo, hi)
    # The flagship two-step sweep and the chains were collected weeks apart with the
    # same prompt wording, which is the confound register row S11 names. The interval
    # is derived, so it is generated here rather than typed into the prose.
    if "Flag" in stamps and "ChainDs" in stamps:
        lines.append(cmd("FlagChainGapDays", integer(round((stamps["ChainDs"][0] - stamps["Flag"][1]) / 86400.0))))
    lines.append(cmd("SweepsPresent", ", ".join(present).replace("_", "\\_")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.publication:
        publication_tables(ROOT,OUT.parent)
    print(f"wrote {OUT} ({len(lines)} lines) from {present}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
