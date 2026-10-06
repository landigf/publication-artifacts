"""C3: the deeper chain. parse (LLM) -> validate -> justify (LLM) -> decide v2 -> explain (LLM).

Two nondeterministic steps feed the deterministic core. Each step is one
llm_client.complete call with its own tag, so each has its own cache namespace.
Per-step outputs are recorded under audit["steps"] so a reuse policy can be
evaluated per step offline.
"""
from __future__ import annotations

import json
from pathlib import Path

from agents.base import (
    FACTOR_VOCAB, SYSTEM, ExplanationResult, build_structured, finalize,
    parse_explanation_fields, parse_json_result,
)
from agents.llm_client import LLMConfig, complete
from chain.policy_v2 import JUSTIFICATION_CATEGORIES, POLICY_TEXT_V2, decide_v2
from chain.taskgen import ChainRequest
from taskgen.schema import Decision, DecisionResult

VARIANT = "C3"
STEPS = ("c_parse", "c_justify", "c_explain")
PARSE_KEYS = "category, amount, budget_remaining, data_processing, security_review, urgency"
JUSTIFY_KEYS = ("justification_adequate", "justification_category")
FACTOR_VOCAB_V2 = FACTOR_VOCAB.rstrip() + "\nAdditional factors: justification_adequate, justification_category.\n"


def _parse_user(req: ChainRequest) -> str:
    # Identical wording to A3's parse prompt, on the chain's free text.
    return (
        f"PROCUREMENT REQUEST (free text):\n{req.free_text}\n\n"
        "Extract ONLY the request-side fields as JSON with exactly these keys: "
        f"{PARSE_KEYS}.\n"
        "amount and budget_remaining are numbers (CHF); data_processing and "
        "security_review are booleans; category and urgency are strings. Do not "
        "infer anything about the supplier. Respond with ONLY the JSON object."
    )


def _justify_user(req: ChainRequest) -> str:
    return (
        f"PROCUREMENT REQUEST (free text):\n{req.free_text}\n\n"
        "Extract ONLY the business justification as JSON with exactly these keys: "
        "justification_adequate, justification_category.\n"
        "justification_adequate is a boolean: true only if the text gives a specific, "
        "documented business reason (for example replacing unsupported equipment with an "
        "asset record, an approved plan or statement of work, a regulatory obligation with "
        "an audit finding, or an attached business case); false if no reason is given, the "
        "reason is vague, or it is deferred.\n"
        f"justification_category is one of: {', '.join(JUSTIFICATION_CATEGORIES)}. "
        "Use none when justification_adequate is false.\n"
        "Do not infer anything about the supplier, the amount or the budget. Respond with "
        "ONLY the JSON object."
    )


def _build_justification(parsed) -> tuple[dict | None, list[str], list[str]]:
    """Strict schema: exactly the two keys, correct types. No imputation."""
    errors: list[str] = []
    if not isinstance(parsed, dict):
        return None, ["justify:not_an_object"], []
    raw_keys = sorted(str(k) for k in parsed)
    for k in sorted(set(JUSTIFY_KEYS) - set(parsed)):
        errors.append(f"justify:missing:{k}")
    for k in sorted(set(parsed) - set(JUSTIFY_KEYS)):
        errors.append(f"justify:extra:{k}")
    if errors:
        return None, errors, raw_keys
    adequate = parsed["justification_adequate"]
    category = parsed["justification_category"]
    if not isinstance(adequate, bool):
        errors.append("justify:justification_adequate:not_bool")
    if not isinstance(category, str) or category.strip().lower() not in JUSTIFICATION_CATEGORIES:
        errors.append("justify:justification_category:unknown")
    if errors:
        return None, errors, raw_keys
    return {"justification_adequate": adequate, "justification_category": category.strip().lower()}, [], raw_keys


def explain_v2(req, decision: Decision, record: dict, cfg, salt, cache_dir, offline) -> ExplanationResult:
    """base.explain with policy v2 and the eleven-field record. Same information boundary."""
    record_json = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False)
    user = (
        f"{POLICY_TEXT_V2}\n"
        "VALIDATED DECISION RECORD (exact input consumed by the deterministic "
        f"policy core):\n{record_json}\n\n"
        f"A decision has already been made for this request: {decision.value.upper()}.\n"
        "Explain briefly why this decision follows from the request and policy, "
        "and list the factors that drove it.\n"
        f"{FACTOR_VOCAB_V2}\n"
        'Respond with ONLY this JSON: {"cited_factors": ["..."], '
        '"explanation": "one or two sentences"}'
    )
    rec = complete(cfg, SYSTEM, user, tag="c_explain", salt=salt, cache_dir=cache_dir, offline=offline)
    parsed = parse_json_result(rec.get("text"))
    cited, expl, errors = parse_explanation_fields(parsed.value)
    if parsed.error:
        errors.insert(0, parsed.error)
    return ExplanationResult(cited, expl, rec, bool(errors), errors)


def run(req: ChainRequest, cfg: LLMConfig, salt: str, cache_dir: Path, offline: bool = False) -> DecisionResult:
    steps: dict = {}

    # step 1: parse
    parse_rec = complete(cfg, SYSTEM, _parse_user(req), tag="c_parse", salt=salt, cache_dir=cache_dir, offline=offline)
    parsed = parse_json_result(parse_rec.get("text"))
    built = build_structured(parsed.value, req, use_db_supplier=True, parse_error=parsed.error)
    steps["c_parse"] = {"cache_id": parse_rec.get("cache_id"), "valid": bool(built.valid),
                        "fields": ({k: v for k, v in built.structured.items()
                                    if built.audit.get("source_by_field", {}).get(k) == "llm_parse"} if built.valid else None),
                        "errors": list(built.audit.get("errors") or [])}
    if not built.valid:
        return finalize(Decision.ESCALATE, [], "Escalated because the structured parse failed validation.", [parse_rec],
                        decision_parse_error=True, firing_rule="parse_failure", firing_factors=[],
                        audit={"structured_record": None, "structured_parse": built.audit, "steps": steps,
                               "source_by_field": None, "policy_version": "v2", "fail_safe": "parse_failure_escalation"})

    # step 2: justify
    just_rec = complete(cfg, SYSTEM, _justify_user(req), tag="c_justify", salt=salt, cache_dir=cache_dir, offline=offline)
    jparsed = parse_json_result(just_rec.get("text"))
    jfields, jerrors, jraw = _build_justification(jparsed.value)
    if jparsed.error:
        jerrors = [jparsed.error] + jerrors
    steps["c_justify"] = {"cache_id": just_rec.get("cache_id"), "valid": jfields is not None,
                          "fields": jfields, "errors": jerrors, "raw_keys": jraw}
    if jfields is None:
        return finalize(Decision.ESCALATE, [], "Escalated because the justification parse failed validation.", [parse_rec, just_rec],
                        decision_parse_error=True, firing_rule="justify_failure", firing_factors=[],
                        audit={"structured_record": None, "structured_parse": built.audit, "steps": steps,
                               "source_by_field": None, "policy_version": "v2", "fail_safe": "justify_failure_escalation"})

    # step 3: decide (policy v2, deterministic)
    record = dict(built.structured)
    record.update(jfields)
    source = dict(built.audit.get("source_by_field") or {})
    source.update({k: "llm_justify" for k in JUSTIFY_KEYS})
    core = decide_v2(record)

    # step 4: explain (a sink: nothing consumes it)
    exp = explain_v2(req, core.decision, record, cfg, salt, cache_dir, offline)
    steps["c_explain"] = {"cache_id": exp.record.get("cache_id"), "valid": not exp.parse_error, "errors": list(exp.errors)}

    return finalize(core.decision, exp.cited, exp.explanation, [parse_rec, just_rec, exp.record],
                    decision_parse_error=core.decision_parse_error, explanation_parse_error=exp.parse_error,
                    firing_rule=core.firing_rule, firing_factors=core.firing_rule_factors,
                    audit={"structured_record": record, "structured_parse": built.audit, "steps": steps,
                           "source_by_field": source, "policy_core": core.audit, "policy_version": "v2",
                           "explanation_output_errors": exp.errors})
