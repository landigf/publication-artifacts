"""A0 - LLM end to end.

The model gets the policy, request-scoped authoritative supplier fields and the free-text
request, and it both decides and explains. Maximum autonomy: nothing about the
decision is deterministic.
"""
from __future__ import annotations

from pathlib import Path

from agents.base import (
    FACTOR_VOCAB, SYSTEM, finalize, parse_explanation_fields,
    parse_json_result, supplier_db_block, to_decision,
)
from agents.llm_client import LLMConfig, complete
from agents.policy_core import POLICY_TEXT
from taskgen.schema import DecisionResult, ProcurementRequest

VARIANT = "A0"


def _user(req: ProcurementRequest) -> str:
    return (
        f"{POLICY_TEXT}\n"
        f"{supplier_db_block(req)}\n\n"
        f"PROCUREMENT REQUEST (free text):\n{req.free_text}\n\n"
        f"{FACTOR_VOCAB}\n"
        "Decide the request under the policy above. Respond with ONLY this JSON:\n"
        '{"decision": "approve|reject|escalate", '
        '"cited_factors": ["<factors that drove the decision>"], '
        '"explanation": "one or two sentences"}'
    )


def run(req: ProcurementRequest, cfg: LLMConfig, salt: str,
        cache_dir: Path, offline: bool = False) -> DecisionResult:
    rec = complete(cfg, SYSTEM, _user(req), tag="a0", salt=salt,
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
            "decision_output_errors": decision_errors,
            "explanation_output_errors": explanation_errors,
        },
    )
