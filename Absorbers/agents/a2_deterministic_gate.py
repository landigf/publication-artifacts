"""A2 - LLM parses the full record; a deterministic gate decides.

The LLM extracts the full structured record (including its own reading of the
supplier's status and risk); a deterministic gate then applies the policy to
that record. The decision cannot be overridden by the LLM, but it rests on the
LLM's parse, so a misread field (or a successful prompt injection on the
supplier fields) propagates into the decision.
"""
from __future__ import annotations

from pathlib import Path

from agents.base import (
    SYSTEM, build_structured, explain, finalize, parse_json_result,
    supplier_db_block,
)
from agents.llm_client import LLMConfig, complete
from agents.policy_core import decide
from taskgen.schema import Decision, DecisionResult, ProcurementRequest

VARIANT = "A2"

_PARSE_KEYS = (
    "category, amount, budget_remaining, supplier_approved, supplier_risk, "
    "supplier_sanctioned, data_processing, security_review, urgency"
)


def _parse_user(req: ProcurementRequest) -> str:
    return (
        f"{supplier_db_block(req)}\n\n"
        f"PROCUREMENT REQUEST (free text):\n{req.free_text}\n\n"
        "Extract the structured decision record as JSON with exactly these keys: "
        f"{_PARSE_KEYS}.\n"
        "amount and budget_remaining are numbers (CHF); supplier_risk is a number "
        "in 0..1; supplier_approved, supplier_sanctioned, data_processing and "
        "security_review are booleans; category and urgency are strings. Use the "
        "authoritative supplier record for the supplier fields. Respond with ONLY the "
        "JSON object."
    )


def run(req: ProcurementRequest, cfg: LLMConfig, salt: str,
        cache_dir: Path, offline: bool = False) -> DecisionResult:
    parse_rec = complete(cfg, SYSTEM, _parse_user(req), tag="a2parse", salt=salt,
                         cache_dir=cache_dir, offline=offline)
    parsed = parse_json_result(parse_rec.get("text"))
    built = build_structured(
        parsed.value, req, use_db_supplier=False, parse_error=parsed.error,
    )
    if not built.valid:
        return finalize(
            Decision.ESCALATE,
            [],
            "Escalated because the structured parse failed validation.",
            [parse_rec],
            decision_parse_error=True,
            firing_rule="parse_failure",
            firing_factors=[],
            audit={
                "structured_record": None,
                "structured_parse": built.audit,
                "fail_safe": "parse_failure_escalation",
            },
        )

    gate = decide(built.structured)
    exp = explain(
        req, gate.decision, built.structured, cfg, salt,
        cache_dir, offline, tag="a2explain",
    )
    return finalize(
        gate.decision, exp.cited, exp.explanation, [parse_rec, exp.record],
        decision_parse_error=gate.decision_parse_error,
        explanation_parse_error=exp.parse_error,
        firing_rule=gate.firing_rule,
        firing_factors=gate.firing_rule_factors,
        audit={
            "structured_record": built.structured,
            "structured_parse": built.audit,
            "policy_core": gate.audit,
            "explanation_output_errors": exp.errors,
        },
    )
