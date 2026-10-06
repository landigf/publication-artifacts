"""A1 - LLM decides after seeing a precomputed deterministic policy report.

The report is evaluated from the authoritative benchmark record and includes a
recommended decision. The model MAY override it, but this is an oracle-report
upper bound, not an implementation of optional model-selected tool use. This
sits one notch below A2, where the deterministic gate decides directly.

The distinction is intentional and must remain visible in labels and analysis:
there is no tool-call loop, argument generation, tool-selection failure, or
tool latency in this condition.
"""
from __future__ import annotations

from pathlib import Path

from agents.base import (
    FACTOR_VOCAB, SYSTEM, finalize, parse_explanation_fields,
    parse_json_result, supplier_db_block, to_decision,
)
from agents.llm_client import LLMConfig, complete
from agents.policy_core import POLICY_TEXT, decide
from taskgen.schema import (
    CATEGORY_THRESHOLDS, FUZZ_BAND, RISK_FUZZ, RISK_HIGH,
    DecisionResult, ProcurementRequest, binding_threshold, normalize_category,
)

VARIANT = "A1"
CONDITION = "precomputed_deterministic_policy_report_upper_bound"


def _policy_report_block(req: ProcurementRequest) -> str:
    s = req.structured()
    category = normalize_category(s["category"])
    threshold = binding_threshold(category, s["budget_remaining"])
    ceiling = CATEGORY_THRESHOLDS.get(category)
    near = False
    if threshold is not None:
        near = s["amount"] >= threshold * (1.0 - FUZZ_BAND)
    near = near or (RISK_HIGH - RISK_FUZZ <= s["supplier_risk"] < RISK_HIGH)
    rec = decide(s)
    return (
        "PRECOMPUTED DETERMINISTIC POLICY REPORT (upper-bound condition; "
        "built from the authoritative benchmark record):\n"
        f"  supplier_on_sanctions_list: {str(s['supplier_sanctioned']).lower()}\n"
        f"  supplier_on_approved_list: {str(s['supplier_approved']).lower()}\n"
        f"  involves_personal_data: {str(s['data_processing']).lower()}\n"
        f"  security_review_done: {str(s['security_review']).lower()}\n"
        f"  supplier_risk_score: {s['supplier_risk']:.2f} (escalation threshold {RISK_HIGH:.2f})\n"
        f"  category_known: {str(ceiling is not None).lower()}\n"
        f"  amount: {s['amount']:.0f}; remaining_budget: {s['budget_remaining']:.0f}; "
        f"category_ceiling: {ceiling if ceiling is not None else 'unknown'}\n"
        f"  amount_over_budget: {str(s['amount'] > s['budget_remaining']).lower()}\n"
        f"  amount_over_ceiling: {str(ceiling is not None and s['amount'] > ceiling).lower()}\n"
        f"  near_a_boundary: {str(near).lower()}\n"
        f"  report_recommended_decision: {rec.decision.value}"
    )


# Compatibility for callers that imported the old private helper.
_tool_block = _policy_report_block


def _user(req: ProcurementRequest) -> str:
    return (
        f"{POLICY_TEXT}\n"
        f"{supplier_db_block(req)}\n\n"
        f"PROCUREMENT REQUEST (free text):\n{req.free_text}\n\n"
        f"{_policy_report_block(req)}\n\n"
        f"{FACTOR_VOCAB}\n"
        "The report above is precomputed, not the result of tools you selected. "
        "You may override its recommendation and make the final decision. "
        "Respond with ONLY this "
        'JSON:\n{"decision": "approve|reject|escalate", '
        '"cited_factors": ["<factors that drove the decision>"], '
        '"explanation": "one or two sentences"}'
    )


def run(req: ProcurementRequest, cfg: LLMConfig, salt: str,
        cache_dir: Path, offline: bool = False) -> DecisionResult:
    rec = complete(cfg, SYSTEM, _user(req), tag="a1", salt=salt,
                   cache_dir=cache_dir, offline=offline)
    parsed = parse_json_result(rec.get("text"))
    obj = parsed.value
    decision = to_decision(obj.get("decision")) if obj else None
    cited, expl, explanation_errors = parse_explanation_fields(obj)
    decision_errors = []
    if parsed.error:
        decision_errors.append(parsed.error)
    if obj is not None and decision is None:
        decision_errors.append("decision:unknown_value")
    if parsed.error and parsed.error not in explanation_errors:
        explanation_errors.insert(0, parsed.error)
    return finalize(
        decision, cited, expl, [rec],
        decision_parse_error=bool(decision_errors),
        explanation_parse_error=bool(explanation_errors),
        audit={
            "condition": CONDITION,
            "decision_output_errors": decision_errors,
            "explanation_output_errors": explanation_errors,
        },
    )
