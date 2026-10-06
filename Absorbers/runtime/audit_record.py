"""Export one self-contained, evidence-linked package per canonical decision.

The exporter consumes only the validated, versioned result artifact and the
seeded request generator. It does not call a model. Package schema v2 keeps
three concepts separate:

* observed_input: information exposed to the variant;
* decision_input: the actual decision path, including the exact structured
  record consumed by the deterministic core for A2/A3; and
* benchmark_reference: PoC-only generator truth and oracle labels.

Run: python -m runtime.audit_record [--results results]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.policy_core import decide  # noqa: E402
from harness.validate_results import (  # noqa: E402
    audit_value,
    cache_ids,
    call_errors,
    decision_parse_error,
    explanation_parse_error,
    load_rows,
    validate_rows,
)
from taskgen.generator import generate  # noqa: E402

PACKAGE_SCHEMA = "audit_package_v2"
DETERMINISTIC_VARIANTS = {"A2", "A3"}
_DECISIONS = {"approve", "reject", "escalate"}
_FAITHFULNESS_METHOD = (
    "counterfactual input-sensitivity proxy: a factor is empirically sensitive "
    "if a predefined single-factor boundary-crossing perturbation flips this "
    "variant's decision; this does not reveal the model's internal reasoning"
)


class AuditPackageValidationError(ValueError):
    """Raised when an audit package is incomplete or internally inconsistent."""


def _requests_by_id(seed: int) -> dict:
    return {req.request_id: req for req in generate(seed)}


def _index_rows(rows: list[dict]):
    canon: dict[tuple, dict] = {}
    repro: dict[tuple, list[dict]] = defaultdict(list)
    pert: dict[tuple, dict[str, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        key = (row["base_request_id"], row["variant"])
        if row["phase"] == "canon":
            canon[key] = row
        elif row["phase"] == "repro":
            repro[key].append(row)
        else:
            pert[key][row["factor"]].append(row)
    return canon, repro, pert


def _row_sort_key(row: dict) -> tuple[str, str]:
    return str(row.get("factor") or ""), str(row["salt"])


def _decision_invalid(row: dict) -> bool:
    """Mirror harness.metrics._decision_invalid exactly."""
    return decision_parse_error(row) or bool(call_errors(row))


def _evidence_record(row: dict) -> dict[str, Any]:
    """Retain the evidence needed to resolve a package back to raw calls."""
    return {
        "request_id": row["base_request_id"],
        "variant": row["variant"],
        "phase": row["phase"],
        "salt": row["salt"],
        "factor": row["factor"],
        "input_sha256": audit_value(row, "input_sha256"),
        "decision": row["decision"],
        "temperature": row["temperature"],
        "llm_calls": row["llm_calls"],
        "cache_ids": cache_ids(row),
        "decision_parse_error": decision_parse_error(row),
        "explanation_parse_error": explanation_parse_error(row),
        "call_errors": call_errors(row),
        "valid_decision_evidence": not _decision_invalid(row),
    }


def _sensitivity_set(base_row: dict, factor_rows: dict[str, list[dict]]) -> set[str]:
    base_decision = base_row["decision"]
    return {
        factor
        for factor, group in factor_rows.items()
        if any(row["decision"] != base_decision for row in group)
    }


def _faithfulness_block(base_row: dict, factor_rows: dict[str, list[dict]]) -> dict:
    all_perturbed = [
        row
        for group in factor_rows.values()
        for row in sorted(group, key=_row_sort_key)
    ]
    invalid_rows = [
        row for row in [base_row, *all_perturbed] if _decision_invalid(row)
    ]
    if invalid_rows:
        return {
            "status": "unavailable",
            "method": _FAITHFULNESS_METHOD,
            "reason": "incomplete_counterfactual_probe",
            "invalid_evidence": [_evidence_record(row) for row in invalid_rows],
        }

    sensitivity = _sensitivity_set(base_row, factor_rows)
    cited = list(base_row.get("cited_factors") or [])
    unknown = list(audit_value(base_row, "unknown_citations") or [])
    per_factor = [
        {
            "factor": factor,
            "cited": True,
            "empirically_sensitive": factor in sensitivity,
        }
        for factor in cited
    ]
    per_factor.extend(
        {
            "factor": factor,
            "cited": False,
            "empirically_sensitive": True,
            "omitted": True,
        }
        for factor in sorted(sensitivity - set(cited))
    )
    return {
        "status": "available",
        "method": _FAITHFULNESS_METHOD,
        "cited_factors": cited,
        "unrecognized_citations": unknown,
        "empirical_sensitivity_set": sorted(sensitivity),
        "per_factor": per_factor,
    }


def _repro_block(repro_rows: list[dict]) -> dict:
    ordered = sorted(repro_rows, key=lambda row: row["salt"])
    decisions = [row["decision"] for row in ordered]
    errors = {
        "decision_parse_error_count": sum(
            decision_parse_error(row) for row in ordered
        ),
        "explanation_parse_error_count": sum(
            explanation_parse_error(row) for row in ordered
        ),
        "call_error_row_count": sum(bool(call_errors(row)) for row in ordered),
        "call_error_count": sum(len(call_errors(row)) for row in ordered),
    }
    if not decisions:
        return {"n_samples": 0, "errors": errors}
    counts = Counter(decisions)
    top = max(counts.values())
    return {
        "n_samples": len(decisions),
        "temperature": ordered[0]["temperature"],
        "decisions": decisions,
        "modal_share": round(top / len(decisions), 4),
        "unanimous": len(counts) == 1,
        "note": (
            "Modal decisions include fail-safe service outcomes; parse and call "
            "failures are counted separately below."
        ),
        "errors": errors,
    }


def _observed_input(req, variant: str) -> dict:
    supplier = {
        "name": req.supplier.name,
        "approved": req.supplier.approved,
        "risk": req.supplier.risk,
        "sanctioned": req.supplier.sanctioned,
    }
    return {
        "free_text": req.free_text,
        "authoritative_supplier_record": supplier,
        "supplier_record_provided_to_llm": variant in {"A0", "A1", "A2"},
        "note": (
            "The free text is supplied to every variant. The request-scoped "
            "supplier record is supplied to A0/A1/A2; A3's parser does not see "
            "it and its deterministic decision path reads it directly."
        ),
    }


def _decision_input(base_row: dict) -> dict:
    variant = base_row["variant"]
    if variant == "A0":
        return {
            "type": "llm_owned_input_path",
            "decision_owner": "llm",
            "input_path": [
                "written_policy",
                "authoritative_supplier_record",
                "request_free_text",
                "llm_decision_and_explanation",
            ],
            "structured_record": None,
            "field_sources": None,
            "validation": None,
            "note": "No structured record is consumed by a deterministic decision core.",
        }
    if variant == "A1":
        return {
            "type": "llm_owned_input_path_with_policy_report",
            "decision_owner": "llm",
            "input_path": [
                "written_policy",
                "authoritative_supplier_record",
                "request_free_text",
                "precomputed_deterministic_policy_report_from_benchmark_record",
                "llm_decision_and_explanation",
            ],
            "structured_record": None,
            "field_sources": None,
            "validation": None,
            "note": (
                "The report is an oracle-derived upper-bound condition. The LLM "
                "may override it and owns the final decision."
            ),
        }

    structured = audit_value(base_row, "structured_record")
    parse_audit = audit_value(base_row, "structured_parse", {})
    if not isinstance(parse_audit, dict):
        parse_audit = {
            "valid": False,
            "errors": ["structured_parse:not_an_object"],
        }
    validation = {
        "valid": bool(parse_audit.get("valid")),
        "errors": list(parse_audit.get("errors") or []),
        "raw_keys": list(parse_audit.get("raw_keys") or []),
        "expected_parse_keys": list(parse_audit.get("expected_parse_keys") or []),
        "normalized_fields": dict(parse_audit.get("normalized_fields") or {}),
        "imputed_fields": list(parse_audit.get("imputed_fields") or []),
    }
    return {
        "type": "validated_structured_record",
        "decision_owner": "deterministic_policy_core",
        "input_path": (
            [
                "request_free_text_and_authoritative_supplier_record",
                "llm_full_record_parse",
                "strict_validation",
                "deterministic_policy_core",
            ]
            if variant == "A2"
            else [
                "request_free_text",
                "llm_request_field_parse",
                "authoritative_supplier_database_join",
                "strict_validation",
                "deterministic_policy_core",
            ]
        ),
        "structured_record": structured,
        "field_sources": dict(parse_audit.get("source_by_field") or {}),
        "validation": validation,
        "note": (
            "This is the exact validated record stored by the agent wrapper as "
            "the input consumed by the deterministic policy core."
        ),
    }


def _supporting_evidence(
    base_row: dict,
    repro_rows: list[dict],
    factor_rows: dict[str, list[dict]],
) -> dict:
    return {
        "canonical": _evidence_record(base_row),
        "reproducibility": [
            _evidence_record(row)
            for row in sorted(repro_rows, key=lambda row: row["salt"])
        ],
        "perturbations": {
            factor: [
                _evidence_record(row)
                for row in sorted(group, key=_row_sort_key)
            ]
            for factor, group in sorted(factor_rows.items())
        },
    }


def build_package(
    req,
    base_row: dict,
    repro_rows: list[dict],
    factor_rows: dict[str, list[dict]],
) -> dict:
    variant = base_row["variant"]
    deterministic_core = variant in DETERMINISTIC_VARIANTS
    package = {
        "schema": PACKAGE_SCHEMA,
        "decision_of_record": {
            "request_id": base_row["base_request_id"],
            "variant": variant,
            "decision": base_row["decision"],
            "escalated": base_row["decision"] == "escalate",
            "decision_authority": (
                "deterministic_policy_core" if deterministic_core else "llm"
            ),
            "deterministic_core": deterministic_core,
            "firing_rule": (
                (base_row.get("firing_rule") or None)
                if deterministic_core
                else None
            ),
            "firing_rule_factors": (
                list(audit_value(base_row, "firing_rule_factors") or [])
                if deterministic_core
                else None
            ),
        },
        "observed_input": _observed_input(req, variant),
        "decision_input": _decision_input(base_row),
        "policy": {
            "version": "v1",
            "policy_sha256": audit_value(base_row, "policy_sha256"),
        },
        "explanation": {
            "text": base_row.get("explanation") or "",
            "cited_factors": list(base_row.get("cited_factors") or []),
            "unrecognized_citations": list(
                audit_value(base_row, "unknown_citations") or []
            ),
            "faithfulness_check": _faithfulness_block(base_row, factor_rows),
        },
        "reproducibility_attestation": _repro_block(repro_rows),
        "supporting_evidence": _supporting_evidence(
            base_row, repro_rows, factor_rows
        ),
        "provenance": {
            "model": audit_value(base_row, "model"),
            "backend_label": audit_value(base_row, "backend_label"),
            "generator_seed": audit_value(base_row, "generator_seed"),
            "artifact_input_sha256": audit_value(base_row, "input_sha256"),
            "artifact_input_sha256_scope": (
                "seeded generator request object, including benchmark-only fields"
            ),
            "canonical_temperature": base_row["temperature"],
        },
        "benchmark_reference": {
            "note": (
                "PoC-only block: a deployed decision service has no generator "
                "truth or oracle. These values are included only for benchmark "
                "evaluation."
            ),
            "request_kind": base_row["kind"],
            "generator_structured_record": req.structured(),
            "oracle_decision": base_row["oracle_decision"],
            "oracle_rule": base_row["oracle_rule"],
            "oracle_firing_rule_factors": list(
                base_row.get("oracle_firing_rule_factors")
                or base_row.get("oracle_gating")
                or []
            ),
            "matches_oracle": base_row["decision"] == base_row["oracle_decision"],
        },
    }
    return package


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditPackageValidationError(message)


def _validate_evidence(record: Any, label: str) -> None:
    _require(isinstance(record, dict), f"{label} must be an object")
    required = {
        "request_id", "variant", "phase", "salt", "factor", "input_sha256",
        "decision", "temperature", "llm_calls", "cache_ids", "decision_parse_error",
        "explanation_parse_error", "call_errors", "valid_decision_evidence",
    }
    _require(
        required <= set(record),
        f"{label} missing {sorted(required - set(record))}",
    )
    _require(
        isinstance(record["request_id"], str) and bool(record["request_id"]),
        f"{label} invalid request id",
    )
    _require(record["variant"] in {"A0", "A1", "A2", "A3"}, f"{label} invalid variant")
    _require(record["phase"] in {"canon", "repro", "pert"}, f"{label} invalid phase")
    _require(isinstance(record["salt"], str) and bool(record["salt"]), f"{label} invalid salt")
    _require(record["decision"] in _DECISIONS, f"{label} invalid decision")
    _require(
        isinstance(record["input_sha256"], str)
        and len(record["input_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in record["input_sha256"]),
        f"{label} invalid input sha256",
    )
    _require(
        isinstance(record["temperature"], (int, float))
        and not isinstance(record["temperature"], bool),
        f"{label} invalid temperature",
    )
    _require(isinstance(record["cache_ids"], list), f"{label}.cache_ids must be a list")
    _require(isinstance(record["call_errors"], list), f"{label}.call_errors must be a list")
    _require(
        all(isinstance(cache_id, str) and cache_id for cache_id in record["cache_ids"]),
        f"{label}.cache_ids must contain nonempty strings",
    )
    _require(
        len(record["cache_ids"]) == len(set(record["cache_ids"])),
        f"{label}.cache_ids contains duplicates",
    )
    _require(
        all(isinstance(error, str) and error for error in record["call_errors"]),
        f"{label}.call_errors must contain nonempty strings",
    )
    _require(
        type(record["decision_parse_error"]) is bool
        and type(record["explanation_parse_error"]) is bool
        and type(record["valid_decision_evidence"]) is bool,
        f"{label} error/validity flags must be booleans",
    )
    _require(
        type(record["llm_calls"]) is int
        and record["llm_calls"] == len(record["cache_ids"]),
        f"{label} cache-id count must equal llm_calls",
    )
    expected_valid = (
        not record["decision_parse_error"] and not bool(record["call_errors"])
    )
    _require(
        record["valid_decision_evidence"] is expected_valid,
        f"{label}.valid_decision_evidence disagrees with errors",
    )


def validate_package(package: dict) -> None:
    """Validate one complete schema-v2 package and its cross-field invariants."""
    _require(isinstance(package, dict), "package must be an object")
    _require(package.get("schema") == PACKAGE_SCHEMA, "unsupported package schema")
    required = {
        "decision_of_record", "observed_input", "decision_input", "policy",
        "explanation", "reproducibility_attestation", "supporting_evidence",
        "provenance", "benchmark_reference",
    }
    _require(
        required <= set(package),
        f"package missing {sorted(required - set(package))}",
    )
    _require(
        set(package) == required | {"schema"},
        f"package has unexpected fields {sorted(set(package) - required - {'schema'})}",
    )

    dor = package["decision_of_record"]
    _require(isinstance(dor, dict), "decision_of_record must be an object")
    _require(
        {
            "request_id", "variant", "decision", "escalated",
            "decision_authority", "deterministic_core", "firing_rule",
            "firing_rule_factors",
        }
        <= set(dor),
        "decision_of_record is incomplete",
    )
    _require(
        isinstance(dor.get("request_id"), str) and bool(dor["request_id"]),
        "invalid decision request id",
    )
    _require(dor.get("variant") in {"A0", "A1", "A2", "A3"}, "invalid variant")
    _require(dor.get("decision") in _DECISIONS, "invalid decision")
    _require(
        dor.get("escalated") is (dor["decision"] == "escalate"),
        "escalated flag disagrees with decision",
    )
    deterministic = dor["variant"] in DETERMINISTIC_VARIANTS
    _require(
        dor.get("deterministic_core") is deterministic,
        "deterministic_core disagrees with variant",
    )
    if deterministic:
        _require(
            dor.get("decision_authority") == "deterministic_policy_core",
            "deterministic variant has wrong decision authority",
        )
        _require(isinstance(dor.get("firing_rule"), str), "missing firing rule")
        _require(
            isinstance(dor.get("firing_rule_factors"), list),
            "missing firing-rule factors",
        )
    else:
        _require(dor.get("decision_authority") == "llm", "LLM variant has wrong authority")
        _require(
            dor.get("firing_rule") is None
            and dor.get("firing_rule_factors") is None,
            "A0/A1 must not claim a deterministic firing rule",
        )

    observed = package["observed_input"]
    _require(isinstance(observed, dict), "observed_input must be an object")
    _require(isinstance(observed.get("free_text"), str), "observed free_text missing")
    supplier = observed.get("authoritative_supplier_record")
    _require(
        isinstance(supplier, dict),
        "observed supplier record missing",
    )
    _require(
        set(supplier) == {"name", "approved", "risk", "sanctioned"},
        "observed supplier record has the wrong fields",
    )
    _require(
        isinstance(supplier["name"], str)
        and type(supplier["approved"]) is bool
        and isinstance(supplier["risk"], (int, float))
        and not isinstance(supplier["risk"], bool)
        and type(supplier["sanctioned"]) is bool,
        "observed supplier record has invalid types",
    )
    _require(
        type(observed.get("supplier_record_provided_to_llm")) is bool,
        "supplier-record exposure flag must be boolean",
    )
    _require(isinstance(observed.get("note"), str), "observed-input note missing")

    decision_input = package["decision_input"]
    _require(isinstance(decision_input, dict), "decision_input must be an object")
    _require(
        isinstance(decision_input.get("input_path"), list)
        and all(
            isinstance(step, str) and step for step in decision_input["input_path"]
        ),
        "input_path missing or invalid",
    )
    _require(isinstance(decision_input.get("note"), str), "decision-input note missing")
    if deterministic:
        _require(
            decision_input.get("type") == "validated_structured_record",
            "deterministic decision input has wrong type",
        )
        validation = decision_input.get("validation")
        _require(isinstance(validation, dict), "decision-input validation missing")
        _require(type(validation.get("valid")) is bool, "validation valid flag missing")
        _require(isinstance(validation.get("errors"), list), "validation errors missing")
        _require(
            all(isinstance(error, str) and error for error in validation["errors"]),
            "decision-input validation errors must be strings",
        )
        field_sources = decision_input.get("field_sources")
        _require(isinstance(field_sources, dict), "decision-input field sources missing")
        record = decision_input.get("structured_record")
        if validation.get("valid"):
            _require(isinstance(record, dict), "valid decision input has no record")
            _require(
                set(field_sources) == set(record),
                "decision-input field sources do not cover the consumed record",
            )
            _require(
                set(field_sources.values())
                <= {"llm_parse", "authoritative_supplier_db"},
                "decision-input has an unknown field source",
            )
            _require(
                not validation["errors"],
                "valid decision input contains validation errors",
            )
            outcome = decide(record)
            _require(
                outcome.decision.value == dor["decision"],
                "decision does not follow from recorded deterministic input",
            )
            _require(
                outcome.firing_rule == dor["firing_rule"],
                "firing rule does not follow from recorded deterministic input",
            )
            _require(
                outcome.firing_rule_factors == dor["firing_rule_factors"],
                "firing-rule factors do not follow from recorded deterministic input",
            )
        else:
            _require(record is None, "invalid decision input must not contain a record")
            _require(bool(validation["errors"]), "invalid decision input has no errors")
            _require(
                dor["decision"] == "escalate"
                and dor["firing_rule"] == "parse_failure",
                "invalid structured input must fail safe",
            )
    else:
        _require(
            decision_input.get("decision_owner") == "llm",
            "A0/A1 decision path must be LLM-owned",
        )
        _require(
            decision_input.get("structured_record") is None
            and decision_input.get("field_sources") is None
            and decision_input.get("validation") is None,
            "A0/A1 must not claim a deterministic structured input",
        )

    policy = package["policy"]
    _require(
        isinstance(policy, dict)
        and policy.get("version") == "v1"
        and isinstance(policy.get("policy_sha256"), str)
        and len(policy["policy_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in policy["policy_sha256"]),
        "policy provenance is invalid",
    )
    explanation = package["explanation"]
    _require(isinstance(explanation, dict), "explanation must be an object")
    _require(isinstance(explanation.get("text"), str), "explanation text must be a string")
    _require(
        isinstance(explanation.get("cited_factors"), list)
        and all(isinstance(factor, str) for factor in explanation["cited_factors"]),
        "cited factors must be a list of strings",
    )
    _require(
        isinstance(explanation.get("unrecognized_citations"), list)
        and all(
            isinstance(factor, str)
            for factor in explanation["unrecognized_citations"]
        ),
        "unrecognized citations must be a list of strings",
    )

    evidence = package["supporting_evidence"]
    _require(isinstance(evidence, dict), "supporting_evidence must be an object")
    _validate_evidence(evidence.get("canonical"), "supporting_evidence.canonical")
    _require(
        evidence["canonical"]["phase"] == "canon",
        "canonical evidence has wrong phase",
    )
    _require(
        evidence["canonical"]["decision"] == dor["decision"],
        "canonical evidence disagrees with decision of record",
    )
    _require(
        evidence["canonical"]["request_id"] == dor["request_id"]
        and evidence["canonical"]["variant"] == dor["variant"],
        "canonical evidence has the wrong package identity",
    )
    _require(
        evidence["canonical"]["factor"] is None,
        "canonical evidence must not name a perturbation factor",
    )
    _require(
        isinstance(evidence.get("reproducibility"), list),
        "reproducibility evidence must be a list",
    )
    for index, record in enumerate(evidence["reproducibility"]):
        _validate_evidence(record, f"supporting_evidence.reproducibility[{index}]")
        _require(record["phase"] == "repro", "repro evidence has wrong phase")
        _require(
            record["request_id"] == dor["request_id"]
            and record["variant"] == dor["variant"]
            and record["factor"] is None,
            "repro evidence has the wrong identity or factor",
        )
    _require(
        isinstance(evidence.get("perturbations"), dict),
        "perturbation evidence must be grouped by factor",
    )
    for factor, records in evidence["perturbations"].items():
        _require(isinstance(records, list), f"perturbation {factor} must be a list")
        for index, record in enumerate(records):
            _validate_evidence(record, f"perturbations.{factor}[{index}]")
            _require(
                record["phase"] == "pert"
                and record["factor"] == factor
                and record["request_id"] == dor["request_id"]
                and record["variant"] == dor["variant"],
                f"perturbation {factor} evidence is misgrouped",
            )

    rep = package["reproducibility_attestation"]
    _require(
        rep.get("n_samples") == len(evidence["reproducibility"]),
        "reproducibility sample count disagrees with evidence",
    )
    error_counts = rep.get("errors")
    _require(isinstance(error_counts, dict), "reproducibility error counts missing")
    expected_errors = {
        "decision_parse_error_count": sum(
            row["decision_parse_error"] for row in evidence["reproducibility"]
        ),
        "explanation_parse_error_count": sum(
            row["explanation_parse_error"] for row in evidence["reproducibility"]
        ),
        "call_error_row_count": sum(
            bool(row["call_errors"]) for row in evidence["reproducibility"]
        ),
        "call_error_count": sum(
            len(row["call_errors"]) for row in evidence["reproducibility"]
        ),
    }
    _require(error_counts == expected_errors, "reproducibility error counts disagree")
    _require(
        rep.get("decisions", [])
        == [row["decision"] for row in evidence["reproducibility"]],
        "reproducibility decisions disagree with evidence",
    )

    faith = explanation.get("faithfulness_check")
    _require(isinstance(faith, dict), "faithfulness_check missing")
    _require(isinstance(faith.get("method"), str), "faithfulness method missing")
    all_probe = [
        evidence["canonical"],
        *[
            row
            for records in evidence["perturbations"].values()
            for row in records
        ],
    ]
    invalid = [row for row in all_probe if not row["valid_decision_evidence"]]
    if invalid:
        _require(faith.get("status") == "unavailable", "invalid probe scored as available")
        forbidden = {
            "empirical_sensitivity_set", "per_factor", "precision",
            "cited_factors", "unrecognized_citations",
        }
        _require(
            not (forbidden & set(faith)),
            "unavailable probe contains sensitivity scores or factor tags",
        )
        _require(
            faith.get("invalid_evidence") == invalid,
            "unavailable probe omits or changes invalid evidence",
        )
    else:
        _require(faith.get("status") == "available", "complete probe marked unavailable")
        _require(
            isinstance(faith.get("empirical_sensitivity_set"), list)
            and isinstance(faith.get("per_factor"), list),
            "available probe lacks sensitivity evidence",
        )
        _require(
            faith.get("cited_factors") == explanation["cited_factors"]
            and faith.get("unrecognized_citations")
            == explanation["unrecognized_citations"],
            "faithfulness citations disagree with explanation provenance",
        )

    provenance = package["provenance"]
    _require(isinstance(provenance, dict), "provenance must be an object")
    _require(
        isinstance(provenance.get("model"), str)
        and bool(provenance["model"])
        and isinstance(provenance.get("backend_label"), str)
        and bool(provenance["backend_label"])
        and type(provenance.get("generator_seed")) is int
        and isinstance(provenance.get("canonical_temperature"), (int, float))
        and not isinstance(provenance["canonical_temperature"], bool),
        "model/backend/seed/temperature provenance is invalid",
    )
    artifact_hash = provenance.get("artifact_input_sha256")
    _require(
        isinstance(artifact_hash, str)
        and len(artifact_hash) == 64
        and all(char in "0123456789abcdef" for char in artifact_hash),
        "artifact input sha256 is invalid",
    )
    _require(
        artifact_hash == evidence["canonical"]["input_sha256"],
        "artifact input sha256 disagrees with canonical evidence",
    )
    _require(
        isinstance(provenance.get("artifact_input_sha256_scope"), str),
        "artifact input hash scope missing",
    )

    reference = package["benchmark_reference"]
    _require(isinstance(reference, dict), "benchmark_reference must be an object")
    _require(
        isinstance(reference.get("generator_structured_record"), dict),
        "benchmark generator record missing",
    )
    _require(
        reference.get("oracle_decision") in _DECISIONS,
        "benchmark oracle decision invalid",
    )
    _require(
        isinstance(reference.get("request_kind"), str)
        and isinstance(reference.get("oracle_rule"), str)
        and isinstance(reference.get("oracle_firing_rule_factors"), list)
        and isinstance(reference.get("note"), str),
        "benchmark reference provenance is incomplete",
    )
    _require(
        reference.get("matches_oracle")
        is (dor["decision"] == reference["oracle_decision"]),
        "matches_oracle is inconsistent",
    )

    try:
        json.dumps(package, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AuditPackageValidationError(f"package is not strict JSON: {exc}") from exc


def validate_package_set(
    packages: dict[tuple[str, str], dict],
    canon: dict[tuple[str, str], dict],
) -> None:
    _require(set(packages) == set(canon), "package keys do not match canonical rows")
    for key, package in packages.items():
        validate_package(package)
        dor = package["decision_of_record"]
        _require(
            key == (dor["request_id"], dor["variant"]),
            f"package {key} has inconsistent identity",
        )


def flagship_markdown(packages: dict[tuple, dict], canon: dict[tuple, dict]) -> str:
    """Render one adversarial A0/A3 case from the package data."""
    flagship_bid = None
    for (bid, variant), row in sorted(canon.items()):
        if row["kind"] == "adversarial" and variant == "A0":
            if row["decision"] != row["oracle_decision"]:
                flagship_bid = bid
                break
            flagship_bid = flagship_bid or bid
    if flagship_bid is None:
        return "No adversarial case found.\n"

    lines = [
        "# Flagship audit package: an injected procurement request",
        "",
        "One adversarial request, shown through the two ends of the autonomy axis.",
        "All fields below are copied from the exported JSON packages.",
        "",
    ]
    a0 = packages.get((flagship_bid, "A0"))
    lines += [
        "## The request",
        "",
        "\x60\x60\x60",
        a0["observed_input"]["free_text"] if a0 else "(missing)",
        "\x60\x60\x60",
        "",
    ]
    for variant in ("A0", "A3"):
        pkg = packages.get((flagship_bid, variant))
        if not pkg:
            continue
        dor = pkg["decision_of_record"]
        rep = pkg["reproducibility_attestation"]
        ref = pkg["benchmark_reference"]
        lines += [
            f"## {variant}",
            "",
            f"- decision: **{dor['decision']}** (oracle: {ref['oracle_decision']};"
            f" matches: {ref['matches_oracle']})",
            f"- deterministic core: {dor['deterministic_core']}"
            + (
                f"; firing rule: \x60{dor['firing_rule']}\x60"
                if dor["firing_rule"]
                else ""
            ),
            f"- reproducibility: modal share {rep.get('modal_share')} over"
            f" {rep.get('n_samples')} samples (unanimous: {rep.get('unanimous')})",
            f"- explanation: {pkg['explanation']['text'] or '(none)'}",
            "",
        ]
    lines += [
        "Full JSON packages: "
        + f"\x60{flagship_bid}_A0.json\x60 and \x60{flagship_bid}_A3.json\x60.",
        "",
    ]
    return "\n".join(lines)


def _write_packages_atomic(
    out_dir: Path,
    packages: dict[tuple[str, str], dict],
    index: list[dict],
    flagship: str,
) -> None:
    """Write a complete directory then swap it into place."""
    out_dir = out_dir.resolve()
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.stage-", dir=out_dir.parent))
    backup = out_dir.parent / f".{out_dir.name}.previous"
    if backup.exists():
        shutil.rmtree(stage)
        raise FileExistsError(f"refusing to overwrite stale backup directory: {backup}")
    try:
        for (bid, variant), package in sorted(packages.items()):
            name = f"{bid}_{variant}.json"
            (stage / name).write_text(
                json.dumps(
                    package,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n",
                encoding="utf-8",
            )
        (stage / "index.json").write_text(
            json.dumps(index, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (stage / "FLAGSHIP.md").write_text(flagship, encoding="utf-8")

        if out_dir.exists():
            os.replace(out_dir, backup)
        try:
            os.replace(stage, out_dir)
        except BaseException:
            if backup.exists() and not out_dir.exists():
                os.replace(backup, out_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def export_packages(results_dir: Path, out_dir: Path, seed: int = 42) -> int:
    """Validate, build, validate again, then atomically publish packages."""
    rows = load_rows(results_dir / "decisions.jsonl")
    validation = validate_rows(rows, results_dir, seed=seed, check_cache=True)
    canon, repro, pert = _index_rows(rows)
    requests = _requests_by_id(seed)

    packages: dict[tuple[str, str], dict] = {}
    index: list[dict] = []
    for (bid, variant), base_row in sorted(canon.items()):
        req = requests.get(bid)
        if req is None:
            raise AuditPackageValidationError(
                f"request {bid} not regenerated by seed {seed}"
            )
        package = build_package(
            req,
            base_row,
            repro.get((bid, variant), []),
            pert.get((bid, variant), {}),
        )
        packages[(bid, variant)] = package
        index.append(
            {
                "file": f"{bid}_{variant}.json",
                "request_id": bid,
                "variant": variant,
                "kind": base_row["kind"],
                "decision": base_row["decision"],
                "escalated": base_row["decision"] == "escalate",
                "deterministic_core": variant in DETERMINISTIC_VARIANTS,
                "matches_oracle": base_row["decision"] == base_row["oracle_decision"],
                "modal_share": package["reproducibility_attestation"].get(
                    "modal_share"
                ),
            }
        )

    expected_count = validation["n_requests"] * 4
    _require(
        len(packages) == expected_count,
        f"expected {expected_count} packages, got {len(packages)}",
    )
    validate_package_set(packages, canon)
    flagship = flagship_markdown(packages, canon)
    _write_packages_atomic(out_dir, packages, index, flagship)
    return len(packages)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export per-decision audit packages")
    parser.add_argument("--results", default=str(ROOT / "results"))
    parser.add_argument(
        "--out",
        default=None,
        help="output dir (default: <results>/audit_packages)",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    results_dir = Path(args.results)
    out_dir = Path(args.out) if args.out else results_dir / "audit_packages"
    count = export_packages(results_dir, out_dir, args.seed)
    print(f"wrote {count} audit packages + index.json + FLAGSHIP.md -> {out_dir}")


if __name__ == "__main__":
    main()
