"""A3 - AuditChain architecture. LLM only parses the request and explains.

The LLM extracts the request-side fields it can read from the free text
(category, amount, budget, personal-data and security-review flags, urgency).
Supplier approval, risk and sanctions come from the authoritative database, not
the LLM, so they cannot be corrupted by the request text. A deterministic core
then decides. The LLM writes the explanation afterwards. This is the most
constrained variant: the LLM owns none of the decision logic and none of the
authoritative supplier facts.
"""
from __future__ import annotations

from pathlib import Path

from agents.base import (
    SYSTEM, build_structured, explain, finalize, parse_json_result,
)
from agents.llm_client import LLMConfig, complete
from taskgen.schema import Decision, DecisionResult, ProcurementRequest
from agents.policy_core import decide

VARIANT = "A3"

_PARSE_KEYS = "category, amount, budget_remaining, data_processing, security_review, urgency"


def _parse_user(req: ProcurementRequest) -> str:
    return (
        f"PROCUREMENT REQUEST (free text):\n{req.free_text}\n\n"
        "Extract ONLY the request-side fields as JSON with exactly these keys: "
        f"{_PARSE_KEYS}.\n"
        "amount and budget_remaining are numbers (CHF); data_processing and "
        "security_review are booleans; category and urgency are strings. Do not "
        "infer anything about the supplier. Respond with ONLY the JSON object."
    )


def run(req: ProcurementRequest, cfg: LLMConfig, salt: str,
        cache_dir: Path, offline: bool = False) -> DecisionResult:
    parse_rec = complete(cfg, SYSTEM, _parse_user(req), tag="a3parse", salt=salt,
                         cache_dir=cache_dir, offline=offline)
    parsed = parse_json_result(parse_rec.get("text"))
    # supplier fields come from the authoritative DB (use_db_supplier=True)
    built = build_structured(
        parsed.value, req, use_db_supplier=True, parse_error=parsed.error,
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

    core = decide(built.structured)
    exp = explain(
        req, core.decision, built.structured, cfg, salt,
        cache_dir, offline, tag="a3explain",
    )
    return finalize(
        core.decision, exp.cited, exp.explanation, [parse_rec, exp.record],
        decision_parse_error=core.decision_parse_error,
        explanation_parse_error=exp.parse_error,
        firing_rule=core.firing_rule,
        firing_factors=core.firing_rule_factors,
        audit={
            "structured_record": built.structured,
            "structured_parse": built.audit,
            "policy_core": core.audit,
            "explanation_output_errors": exp.errors,
        },
    )
