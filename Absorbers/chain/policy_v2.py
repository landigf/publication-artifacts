"""Policy v2: the nine v1 rules plus one that consumes the justify step.

The new rule sits after over_threshold and before the confidence gate: a
purchase at or above JUSTIFICATION_SHARE of its binding ceiling without an
adequate documented justification escalates. With justification_adequate True,
decide_v2 equals decide on every record; tests assert this over all 65 requests
and every perturbation.

JUSTIFICATION_SHARE is 0.25, chosen so the new rule is load-bearing on a
reportable number of requests without dominating the cascade. At 0.50 it fired
on 2 of the 65 canonical requests and changed the v1 oracle on 1; at 0.25 it
fires on 5 and changes the oracle on 4, and the justification toggle moves the
v2 oracle on 9 of 65. Both settings were computed before any sweep was
collected. The share is not swept across a collected sweep, which the paper
states as a limitation.
"""
from __future__ import annotations

import hashlib

from agents.policy_core import POLICY_TEXT
from taskgen.schema import (
    CATEGORY_THRESHOLDS, FUZZ_BAND, RISK_FUZZ, RISK_HIGH, Decision, DecisionResult,
    binding_threshold, validate_structured_record,
)

JUSTIFICATION_SHARE = 0.25
JUSTIFICATION_CATEGORIES = ("replacement", "expansion", "compliance", "other", "none")

POLICY_TEXT_V2 = POLICY_TEXT.replace("PROCUREMENT DECISION POLICY (v1)", "PROCUREMENT DECISION POLICY (v2)").replace(
    "8. Confidence gate:",
    f"7b. If the amount is at least {int(JUSTIFICATION_SHARE * 100)}% of the binding ceiling and no adequate,\n"
    "   documented business justification is given -> escalate (inadequate justification).\n"
    "8. Confidence gate:",
)
assert "7b." in POLICY_TEXT_V2
POLICY_V2_SHA256 = hashlib.sha256(POLICY_TEXT_V2.encode("utf-8")).hexdigest()

V2_KEYS = ("justification_adequate", "justification_category")


def validate_v2_fields(s: dict) -> list[str]:
    errors = []
    if not isinstance(s.get("justification_adequate"), bool):
        errors.append("justification_adequate:not_bool")
    if s.get("justification_category") not in JUSTIFICATION_CATEGORIES:
        errors.append("justification_category:unknown")
    return errors


def decide_v2(s: dict) -> DecisionResult:
    """Apply policy v2 to a structured record carrying the two justify fields."""
    def R(dec, rule, factors, audit=None, *, input_error=False):
        return DecisionResult(
            decision=dec, firing_rule=rule, firing_rule_factors=list(factors),
            gating_factors=list(factors), parse_error=input_error,
            decision_parse_error=input_error, audit=dict(audit or {}),
        )

    base = {k: v for k, v in s.items() if k not in V2_KEYS}
    validation = validate_structured_record(base)
    v2_errors = validate_v2_fields(s)
    if not validation.valid or v2_errors:
        return R(Decision.ESCALATE, "invalid_input", [],
                 {"structured_validation_errors": list(validation.errors) + v2_errors}, input_error=True)
    b = validation.structured

    if b["supplier_sanctioned"]:
        return R(Decision.REJECT, "sanctioned", ["supplier_sanctioned"])
    if not b["supplier_approved"]:
        return R(Decision.REJECT, "unapproved_supplier", ["supplier_approved"])
    if b["data_processing"] and not b["security_review"]:
        return R(Decision.ESCALATE, "compliance_gap", ["data_processing", "security_review"])
    risk = b["supplier_risk"]
    if risk >= RISK_HIGH:
        return R(Decision.ESCALATE, "high_risk", ["supplier_risk"])
    if RISK_HIGH - RISK_FUZZ <= risk < RISK_HIGH:
        return R(Decision.ESCALATE, "near_risk_fuzzy", ["supplier_risk"])
    category, amount, budget = b["category"], b["amount"], b["budget_remaining"]
    threshold = binding_threshold(category, budget)
    if threshold is None:
        return R(Decision.ESCALATE, "unknown_category", ["category"])
    if amount > budget:
        return R(Decision.ESCALATE, "over_budget", ["amount", "budget_remaining"])
    ceiling = CATEGORY_THRESHOLDS[category]
    if amount > ceiling:
        return R(Decision.ESCALATE, "over_threshold", ["amount", "category"])
    # 7b: the rule the justify step feeds
    if amount >= JUSTIFICATION_SHARE * threshold and not s["justification_adequate"]:
        return R(Decision.ESCALATE, "inadequate_justification", ["amount", "justification_adequate"])
    if amount >= threshold * (1.0 - FUZZ_BAND):
        if budget < ceiling:
            boundary = ["budget_remaining"]
        elif ceiling < budget:
            boundary = ["category"]
        else:
            boundary = ["category", "budget_remaining"]
        return R(Decision.ESCALATE, "near_threshold_fuzzy", ["amount", *boundary])
    return R(Decision.APPROVE, "clean_approve", [])


def oracle_v2(req) -> DecisionResult:
    return decide_v2(req.structured_v2())
