"""Shared scaffolding for the four agent variants.

Each variant is a callable ``run(req, cfg, salt, cache_dir, offline) ->
DecisionResult``. They differ in decision authority and also in prompt shape,
call count, explanation protocol, and authoritative-data path. All variants use
the same decision labels and factor vocabulary, but the comparison is
descriptive rather than a controlled estimate of an autonomy effect.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from taskgen.schema import (
    FACTORS,
    Decision,
    DecisionResult,
    ProcurementRequest,
    normalize_citations,
    validate_structured_record,
)

SYSTEM = (
    "You are a procurement decision assistant. You must output exactly one JSON "
    "object and nothing else: no prose, no markdown code fences, no commentary "
    "before or after."
)

FACTOR_VOCAB = (
    "Allowed factor names (use ONLY these in cited_factors): "
    + ", ".join(FACTORS)
    + "."
)


@dataclass(frozen=True)
class JSONParseResult:
    value: Optional[dict]
    error: Optional[str] = None


def _reject_non_json_constant(value: str):
    raise ValueError(f"non-JSON numeric constant: {value}")


def parse_json_result(text: Optional[str]) -> JSONParseResult:
    """Parse exactly one top-level JSON object and retain a diagnostic on failure.

    A single complete Markdown fence is tolerated for provider compatibility,
    but prose extraction and brace guessing are intentionally not performed.
    """
    if not isinstance(text, str) or not text.strip():
        return JSONParseResult(None, "empty_response")
    s = text.strip()
    if s.startswith("```"):
        match = re.fullmatch(
            r"```(?:json)?\s*\n?(.*?)\n?```", s,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return JSONParseResult(None, "incomplete_or_invalid_markdown_fence")
        s = match.group(1).strip()
    try:
        value = json.loads(s, parse_constant=_reject_non_json_constant)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, json.JSONDecodeError):
            return JSONParseResult(None, f"json_decode_error:{exc.pos}")
        return JSONParseResult(None, f"json_decode_error:{type(exc).__name__}")
    if not isinstance(value, dict):
        return JSONParseResult(
            None, f"top_level_{type(value).__name__}_is_not_object"
        )
    return JSONParseResult(value)


def parse_json(text: Optional[str]) -> Optional[dict]:
    """Compatibility wrapper returning only a strictly parsed JSON object."""
    return parse_json_result(text).value


def to_decision(value) -> Optional[Decision]:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    aliases = {
        "approve": Decision.APPROVE,
        "approved": Decision.APPROVE,
        "accept": Decision.APPROVE,
        "accepted": Decision.APPROVE,
        "reject": Decision.REJECT,
        "rejected": Decision.REJECT,
        "deny": Decision.REJECT,
        "denied": Decision.REJECT,
        "decline": Decision.REJECT,
        "escalate": Decision.ESCALATE,
        "escalated": Decision.ESCALATE,
        "manual_review": Decision.ESCALATE,
        "human_review": Decision.ESCALATE,
    }
    return aliases.get(v)


REQUEST_PARSE_KEYS = (
    "category", "amount", "budget_remaining", "data_processing",
    "security_review", "urgency",
)
SUPPLIER_PARSE_KEYS = (
    "supplier_approved", "supplier_risk", "supplier_sanctioned",
)


@dataclass
class StructuredBuildResult:
    structured: Optional[dict]
    errors: list[str] = field(default_factory=list)
    audit: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.structured is not None and not self.errors


def build_structured(
    parsed: Optional[dict],
    req: ProcurementRequest,
    use_db_supplier: bool,
    *,
    parse_error: Optional[str] = None,
) -> StructuredBuildResult:
    """Build and strictly validate the policy record without truth imputation.

    Request fields always come from the LLM parse. A3's supplier fields come
    from the authoritative supplier record; A2's come from the parse. Missing,
    malformed, or extra fields invalidate the entire record.
    """
    expected = set(REQUEST_PARSE_KEYS)
    if not use_db_supplier:
        expected.update(SUPPLIER_PARSE_KEYS)

    errors: list[str] = []
    candidate: dict = {}
    raw_keys: list[str] = []
    if not isinstance(parsed, dict):
        errors.append(parse_error or "parse:not_an_object")
    else:
        raw_keys = sorted(str(k) for k in parsed)
        for key in sorted(expected - set(parsed)):
            errors.append(f"{key}:missing_from_parse")
        for key in sorted(set(parsed) - expected):
            errors.append(f"{key}:unexpected_in_parse")
        for key in expected:
            if key in parsed:
                candidate[key] = parsed[key]

    source_by_field = {
        key: "llm_parse" for key in candidate
    }
    if use_db_supplier:
        db_values = {
            "supplier_approved": req.supplier.approved,
            "supplier_risk": req.supplier.risk,
            "supplier_sanctioned": req.supplier.sanctioned,
        }
        candidate.update(db_values)
        source_by_field.update({key: "authoritative_supplier_db" for key in db_values})

    validation = validate_structured_record(candidate)
    errors.extend(validation.errors)
    errors = list(dict.fromkeys(errors))
    structured = validation.structured if not errors else None
    audit = {
        "valid": structured is not None,
        "errors": errors,
        "raw_keys": raw_keys,
        "expected_parse_keys": sorted(expected),
        "source_by_field": source_by_field,
        "normalized_fields": validation.normalized_fields,
        "imputed_fields": [],
    }
    return StructuredBuildResult(structured, errors, audit)


def supplier_db_block(req: ProcurementRequest) -> str:
    """The request-scoped authoritative supplier record shown to the agent."""
    s = req.supplier
    return (
        "AUTHORITATIVE SUPPLIER RECORD FOR THIS REQUEST:\n"
        f"  name: {s.name}\n"
        f"  on_approved_vendor_list: {str(s.approved).lower()}\n"
        f"  risk_score: {s.risk:.2f}\n"
        f"  on_sanctions_list: {str(s.sanctioned).lower()}"
    )


@dataclass
class ExplanationResult:
    cited: list[str]
    explanation: str
    record: dict
    parse_error: bool = False
    errors: list[str] = field(default_factory=list)


def parse_explanation_fields(obj: Optional[dict]) -> tuple[list[str], str, list[str]]:
    """Validate the common cited-factor/explanation output fields."""
    if not isinstance(obj, dict):
        return [], "", ["output:not_an_object"]
    errors: list[str] = []
    raw_cited = obj.get("cited_factors")
    if not isinstance(raw_cited, list):
        errors.append("cited_factors:expected_list")
        cited: list[str] = []
    elif not all(isinstance(value, str) for value in raw_cited):
        errors.append("cited_factors:expected_strings")
        cited = [value for value in raw_cited if isinstance(value, str)]
    else:
        cited = list(raw_cited)
    raw_explanation = obj.get("explanation")
    if not isinstance(raw_explanation, str) or not raw_explanation.strip():
        errors.append("explanation:expected_nonempty_string")
        explanation = ""
    else:
        explanation = raw_explanation.strip()
    return cited, explanation, errors


def explain(
    req: ProcurementRequest,
    decision: Decision,
    structured: dict,
    cfg,
    salt: str,
    cache_dir: Path,
    offline: bool,
    tag: str,
) -> ExplanationResult:
    """LLM explanation step for the gated variants (A2/A3).

    The explainer receives the common written policy and the exact validated
    record consumed by the deterministic core, but NOT the free-text request,
    firing rule, or firing-rule factors. This grounds the explanation in the
    policy input actually used for the decision without leaking the rule trace.
    """
    from agents.llm_client import complete  # local import avoids any cycle
    from agents.policy_core import POLICY_TEXT

    structured_json = json.dumps(
        structured, sort_keys=True, ensure_ascii=False, allow_nan=False,
    )
    user = (
        f"{POLICY_TEXT}\n"
        "VALIDATED DECISION RECORD (exact input consumed by the deterministic "
        f"policy core):\n{structured_json}\n\n"
        f"A decision has already been made for this request: {decision.value.upper()}.\n"
        "Explain briefly why this decision follows from the request and policy, "
        "and list the factors that drove it.\n"
        f"{FACTOR_VOCAB}\n"
        'Respond with ONLY this JSON: {"cited_factors": ["..."], '
        '"explanation": "one or two sentences"}'
    )
    rec = complete(cfg, SYSTEM, user, tag=tag, salt=salt,
                   cache_dir=cache_dir, offline=offline)
    parsed = parse_json_result(rec.get("text"))
    cited, expl, errors = parse_explanation_fields(parsed.value)
    if parsed.error:
        errors.insert(0, parsed.error)
    return ExplanationResult(cited, expl, rec, bool(errors), errors)


def _sum_calls(records: list[dict]) -> tuple[int, int, int, float, bool]:
    calls = len(records)
    ti = sum(int(r.get("tokens_in", 0) or 0) for r in records)
    to = sum(int(r.get("tokens_out", 0) or 0) for r in records)
    lat = sum(float(r.get("latency_ms", 0.0) or 0.0) for r in records)
    err = any(r.get("error") for r in records)
    return calls, ti, to, lat, err


def finalize(
    decision: Optional[Decision],
    cited,
    explanation: str,
    records: list[dict],
    *,
    parse_error: bool = False,
    decision_parse_error: Optional[bool] = None,
    explanation_parse_error: bool = False,
    firing_rule: str = "",
    firing_factors: Optional[list] = None,
    gating: Optional[list] = None,
    audit: Optional[dict] = None,
) -> DecisionResult:
    calls, ti, to, lat, call_err = _sum_calls(records)
    decision_parse_error = (
        parse_error if decision_parse_error is None else decision_parse_error
    )
    if decision is None:
        decision_parse_error = True
    factors = list(firing_factors if firing_factors is not None else (gating or []))
    raw_cited = [
        value if isinstance(value, str) else repr(value)
        for value in (cited if isinstance(cited, list) else [])
    ]
    known, unknown = normalize_citations(raw_cited)
    cache_ids = [r.get("cache_id") for r in records]
    requested_cache_ids = [
        r.get("requested_cache_id", r.get("cache_id")) for r in records
    ]
    call_errors = [
        {"cache_id": r.get("cache_id"), "error": r.get("error")}
        for r in records if r.get("error")
    ]
    provenance = dict(audit or {})
    # NOTE: no cache_sources / cached flags here. Those describe the execution
    # mode of a particular run (live call vs cache replay), not the decision,
    # and embedding them broke live-vs-offline byte identity of the artifact.
    # Mode provenance lives in the cache records themselves.
    provenance.update({
        "cache_ids": cache_ids,
        "requested_cache_ids": requested_cache_ids,
        "call_errors": call_errors,
        "decision_parse_error": bool(decision_parse_error),
        "explanation_parse_error": bool(explanation_parse_error),
        "raw_cited_factors": raw_cited,
        "unknown_citations": unknown,
    })
    aggregate_error = bool(
        decision_parse_error or explanation_parse_error or call_err
    )
    return DecisionResult(
        decision=decision if decision is not None else Decision.ESCALATE,
        cited_factors=known,
        unknown_citations=unknown,
        raw_cited_factors=raw_cited,
        explanation=explanation or "",
        firing_rule=firing_rule,
        firing_rule_factors=factors,
        gating_factors=factors,
        llm_calls=calls,
        tokens_in=ti,
        tokens_out=to,
        latency_ms=round(lat, 1),
        parse_error=aggregate_error,
        decision_parse_error=bool(decision_parse_error),
        explanation_parse_error=bool(explanation_parse_error),
        call_error=call_err,
        audit=provenance,
        raw=provenance,
    )
