"""Fail-fast integrity checks for ``results/decisions.jsonl``.

The metrics are only meaningful for a complete, homogeneous sweep.  This module
therefore validates the exact task grid, temperatures, provenance, and active
cache records before aggregation.  Malformed model output is a measured service
outcome and is not fatal; provider errors, offline misses, and missing cache
records are fatal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from agents.policy_core import POLICY_TEXT, oracle, perturbations
from taskgen.generator import generate
from taskgen.schema import Decision, FACTORS

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ("A0", "A1", "A2", "A3")
PHASES = ("canon", "repro", "pert")
EXPECTED_TEMPERATURES = {"canon": 0.0, "repro": 0.7, "pert": 0.0}
STRICT_PROVENANCE_FIELDS = {
    "schema_version", "generator_seed", "backend_label", "model",
    "input_sha256", "policy_sha256", "cache_ids", "call_errors",
    "call_error", "decision_parse_error", "explanation_parse_error",
    "raw_cited_factors", "unknown_citations", "audit",
    "oracle_firing_rule_factors",
}


class ResultsValidationError(ValueError):
    """Raised when the decision artifact is incomplete or internally invalid."""


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResultsValidationError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ResultsValidationError(f"{path}:{lineno}: row must be a JSON object")
        rows.append(row)
    if not rows:
        raise ResultsValidationError(f"{path}: no decision rows")
    return rows


def audit_value(row: dict, key: str, default=None):
    """Read a provenance field from either the row or its nested audit record."""
    if key in row:
        return row[key]
    audit = row.get("audit")
    if isinstance(audit, dict) and key in audit:
        return audit[key]
    raw = row.get("raw")
    if isinstance(raw, dict) and key in raw:
        return raw[key]
    return default


def decision_parse_error(row: dict) -> bool:
    explicit = audit_value(row, "decision_parse_error")
    if explicit is not None:
        return bool(explicit)
    # Legacy rows only had one aggregate parse-error bit.
    return bool(row.get("parse_error", False))


def explanation_parse_error(row: dict) -> bool:
    explicit = audit_value(row, "explanation_parse_error")
    return bool(explicit) if explicit is not None else False


def call_errors(row: dict) -> list[str]:
    errors = audit_value(row, "call_errors", [])
    if isinstance(errors, str):
        errors = [errors]
    if not isinstance(errors, list):
        errors = [f"invalid call_errors payload: {errors!r}"]
    if audit_value(row, "call_error", False) and not errors:
        errors = ["row call_error=true"]
    return [str(err) for err in errors if err]


def cache_ids(row: dict) -> list[str]:
    ids = audit_value(row, "cache_ids", [])
    if ids is None:
        return []
    if not isinstance(ids, list):
        raise ResultsValidationError(f"cache_ids must be a list, got {ids!r}")
    return [str(cid) for cid in ids if cid]


def _row_key(row: dict) -> tuple:
    return (
        row.get("base_request_id"), row.get("variant"), row.get("phase"),
        row.get("salt"), row.get("factor"),
    )


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _expected_keys(
    seed: int, n_repro: int,
) -> tuple[set[tuple], dict[str, str], dict[tuple, tuple], dict[tuple, str]]:
    expected: set[tuple] = set()
    kinds: dict[str, str] = {}
    expected_oracle: dict[tuple, tuple] = {}
    expected_input_hash: dict[tuple, str] = {}

    def register_input(bid: str, phase: str, salt: str, factor, request) -> None:
        logical = (bid, phase, salt, factor)
        result = oracle(request)
        expected_oracle[logical] = (
            result.decision.value, result.firing_rule,
            tuple(result.firing_rule_factors), tuple(result.gating_factors),
        )
        expected_input_hash[logical] = _json_sha256(request.to_json())

    for req in generate(seed):
        bid = req.request_id
        kinds[bid] = req.kind
        register_input(bid, "canon", "canon", None, req)
        for i in range(n_repro):
            register_input(bid, "repro", f"repro{i}", None, req)
        for factor, values in perturbations(req).items():
            for j, value in enumerate(values):
                register_input(bid, "pert", f"pert:{factor}:{j}", factor, value)
        for variant in VARIANTS:
            expected.add((bid, variant, "canon", "canon", None))
            for i in range(n_repro):
                expected.add((bid, variant, "repro", f"repro{i}", None))
            for factor, values in perturbations(req).items():
                for j, _ in enumerate(values):
                    expected.add((bid, variant, "pert", f"pert:{factor}:{j}", factor))
    return expected, kinds, expected_oracle, expected_input_hash


def _require_fields(rows: list[dict]) -> None:
    required = {
        "request_id", "base_request_id", "kind", "variant", "phase", "salt",
        "factor", "temperature", "decision", "cited_factors", "parse_error",
        "llm_calls", "tokens_in", "tokens_out", "latency_ms",
        "oracle_decision", "oracle_rule", "oracle_gating",
    }
    allowed_decisions = {d.value for d in Decision}
    for i, row in enumerate(rows, 1):
        missing = sorted(required - set(row))
        if missing:
            raise ResultsValidationError(f"row {i}: missing fields {missing}")
        if row["decision"] not in allowed_decisions:
            raise ResultsValidationError(f"row {i}: invalid decision {row['decision']!r}")
        if row["oracle_decision"] not in allowed_decisions:
            raise ResultsValidationError(
                f"row {i}: invalid oracle_decision {row['oracle_decision']!r}"
            )
        if not isinstance(row["cited_factors"], list):
            raise ResultsValidationError(f"row {i}: cited_factors must be a list")
        if not isinstance(row["oracle_gating"], list):
            raise ResultsValidationError(f"row {i}: oracle_gating must be a list")
        if not isinstance(row["llm_calls"], int) or row["llm_calls"] < 1:
            raise ResultsValidationError(f"row {i}: invalid llm_calls={row['llm_calls']!r}")
        for key in ("tokens_in", "tokens_out", "latency_ms"):
            value = row[key]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ResultsValidationError(f"row {i}: invalid {key}={value!r}")


def validate_rows(
    rows: list[dict],
    results_dir: Path,
    *,
    seed: int = 42,
    check_cache: bool = True,
) -> dict[str, Any]:
    """Validate a complete sweep and return aggregation metadata.

    ``check_cache=False`` is intended only for unit tests and artifact diagnosis;
    the reproduction pipeline always checks active cache records.
    """
    _require_fields(rows)

    if check_cache:
        for i, row in enumerate(rows, 1):
            missing = sorted(STRICT_PROVENANCE_FIELDS - set(row))
            if missing:
                raise ResultsValidationError(
                    f"row {i}: missing schema-v2 provenance fields {missing}"
                )
            if type(row["schema_version"]) is not int or row["schema_version"] != 2:
                raise ResultsValidationError(
                    f"row {i}: schema_version must be integer 2"
                )
            if type(row["generator_seed"]) is not int or row["generator_seed"] != seed:
                raise ResultsValidationError(
                    f"row {i}: generator_seed must be integer {seed}"
                )
            for field in ("backend_label", "model", "input_sha256", "policy_sha256"):
                if not isinstance(row[field], str) or not row[field]:
                    raise ResultsValidationError(
                        f"row {i}: {field} must be a nonempty string"
                    )
            for field in (
                "cache_ids", "call_errors", "raw_cited_factors",
                "unknown_citations", "oracle_firing_rule_factors",
            ):
                if not isinstance(row[field], list):
                    raise ResultsValidationError(f"row {i}: {field} must be a list")
            for field in (
                "call_error", "decision_parse_error", "explanation_parse_error",
            ):
                if type(row[field]) is not bool:
                    raise ResultsValidationError(f"row {i}: {field} must be boolean")
            if type(row["parse_error"]) is not bool:
                raise ResultsValidationError(f"row {i}: parse_error must be boolean")
            aggregate_error = bool(
                row["decision_parse_error"]
                or row["explanation_parse_error"]
                or row["call_error"]
            )
            if row["parse_error"] != aggregate_error:
                raise ResultsValidationError(
                    f"row {i}: parse_error disagrees with the schema-v2 error flags"
                )
            if row["call_error"] != bool(row["call_errors"]):
                raise ResultsValidationError(
                    f"row {i}: call_error disagrees with call_errors"
                )
            if not isinstance(row["audit"], dict):
                raise ResultsValidationError(f"row {i}: audit must be an object")
            for field in (
                "cache_ids", "call_errors", "decision_parse_error",
                "explanation_parse_error", "raw_cited_factors", "unknown_citations",
            ):
                if field not in row["audit"] or row["audit"][field] != row[field]:
                    raise ResultsValidationError(
                        f"row {i}: top-level {field} disagrees with audit provenance"
                    )

    schema_values = {row.get("schema_version") for row in rows if "schema_version" in row}
    if schema_values and schema_values != {2}:
        raise ResultsValidationError(f"row schema_version must be 2; got {schema_values}")
    seed_values = {row.get("generator_seed") for row in rows if "generator_seed" in row}
    if seed_values and seed_values != {seed}:
        raise ResultsValidationError(f"generator_seed must be {seed}; got {seed_values}")
    for field in ("backend_label", "model", "policy_sha256"):
        values = {row.get(field) for row in rows if field in row}
        if values and (None in values or "" in values or len(values) != 1):
            raise ResultsValidationError(f"rows mix {field} values: {values}")
    expected_policy_sha256 = hashlib.sha256(POLICY_TEXT.encode("utf-8")).hexdigest()
    policy_values = {row.get("policy_sha256") for row in rows if "policy_sha256" in row}
    if policy_values and policy_values != {expected_policy_sha256}:
        raise ResultsValidationError(
            "policy_sha256 does not match the policy used by this checkout: "
            f"got {policy_values}, expected {expected_policy_sha256}"
        )

    variants = {row["variant"] for row in rows}
    if variants != set(VARIANTS):
        raise ResultsValidationError(
            f"variants must be exactly {list(VARIANTS)}; got {sorted(variants)}"
        )
    phases = {row["phase"] for row in rows}
    if phases != set(PHASES):
        raise ResultsValidationError(
            f"phases must be exactly {list(PHASES)}; got {sorted(phases)}"
        )

    keys = [_row_key(row) for row in rows]
    duplicates = [key for key, n in Counter(keys).items() if n != 1]
    if duplicates:
        raise ResultsValidationError(f"duplicate logical rows: {duplicates[:5]}")

    canon_counts = Counter(
        (row["base_request_id"], row["variant"])
        for row in rows if row["phase"] == "canon"
    )
    bad_canon = [key for key, n in canon_counts.items() if n != 1]
    if bad_canon:
        raise ResultsValidationError(f"expected one canonical row per request/variant: {bad_canon[:5]}")

    repro_counts = Counter(
        (row["base_request_id"], row["variant"])
        for row in rows if row["phase"] == "repro"
    )
    n_values = set(repro_counts.values())
    if len(n_values) != 1 or not n_values or next(iter(n_values)) < 2:
        raise ResultsValidationError(f"non-uniform reproducibility sample counts: {sorted(n_values)}")
    n_repro = next(iter(n_values))

    expected, expected_kinds, expected_oracle, expected_input_hash = _expected_keys(
        seed, n_repro
    )
    actual = set(keys)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise ResultsValidationError(
            f"task grid mismatch: missing={missing[:5]} extra={extra[:5]} "
            f"(missing_n={len(missing)}, extra_n={len(extra)})"
        )

    for row in rows:
        bid, phase = row["base_request_id"], row["phase"]
        logical = (bid, phase, row["salt"], row["factor"])
        if row["kind"] != expected_kinds[bid]:
            raise ResultsValidationError(
                f"{_row_key(row)}: kind={row['kind']!r}, expected {expected_kinds[bid]!r}"
            )
        expected_temp = EXPECTED_TEMPERATURES[phase]
        if not math.isclose(float(row["temperature"]), expected_temp, abs_tol=1e-12):
            raise ResultsValidationError(
                f"{_row_key(row)}: temperature={row['temperature']}, expected {expected_temp}"
            )
        if phase == "pert" and row["factor"] not in FACTORS:
            raise ResultsValidationError(f"{_row_key(row)}: unknown perturbation factor")
        if phase != "pert" and row["factor"] is not None:
            raise ResultsValidationError(f"{_row_key(row)}: factor must be null")
        observed_oracle = (
            row["oracle_decision"], row["oracle_rule"],
            tuple(row.get("oracle_firing_rule_factors", row["oracle_gating"])),
            tuple(row["oracle_gating"]),
        )
        if observed_oracle != expected_oracle[logical]:
            raise ResultsValidationError(
                f"{_row_key(row)}: oracle metadata {observed_oracle!r} does not match "
                f"regenerated oracle {expected_oracle[logical]!r}"
            )
        if "input_sha256" in row and row["input_sha256"] != expected_input_hash[logical]:
            raise ResultsValidationError(
                f"{_row_key(row)}: input_sha256 does not match the regenerated request"
            )

    # Oracle labels and request kind must agree across all variants for an
    # identical logical input.
    oracle_groups: dict[tuple, set[tuple]] = defaultdict(set)
    for row in rows:
        oracle_groups[(row["base_request_id"], row["phase"], row["salt"], row["factor"])].add(
            (
                row["oracle_decision"], row["oracle_rule"],
                tuple(row.get("oracle_firing_rule_factors", row["oracle_gating"])),
                tuple(row["oracle_gating"]),
            )
        )
    inconsistent = [key for key, values in oracle_groups.items() if len(values) != 1]
    if inconsistent:
        raise ResultsValidationError(f"cross-variant oracle disagreement: {inconsistent[:5]}")

    input_hash_groups: dict[tuple, set[str]] = defaultdict(set)
    for row in rows:
        if "input_sha256" in row:
            input_hash_groups[
                (row["base_request_id"], row["phase"], row["salt"], row["factor"])
            ].add(row["input_sha256"])
    bad_input_hashes = [key for key, values in input_hash_groups.items() if len(values) != 1]
    if bad_input_hashes:
        raise ResultsValidationError(
            f"cross-variant input hash disagreement: {bad_input_hashes[:5]}"
        )

    raw_dir = results_dir / "raw"
    active_ids: set[str] = set()
    provider_errors: list[str] = []
    missing_provenance: list[tuple] = []
    if check_cache:
        if not raw_dir.is_dir():
            raise ResultsValidationError(f"missing raw cache directory: {raw_dir}")
        for row in rows:
            row_errors = call_errors(row)
            if row_errors:
                provider_errors.extend(f"{_row_key(row)}: {err}" for err in row_errors)
            ids = cache_ids(row)
            if not ids:
                missing_provenance.append(_row_key(row))
                continue
            if len(ids) != row["llm_calls"]:
                raise ResultsValidationError(
                    f"{_row_key(row)}: {len(ids)} cache_ids for {row['llm_calls']} calls"
                )
            for cid in ids:
                if cid in active_ids:
                    raise ResultsValidationError(f"cache id reused by multiple rows: {cid}")
                active_ids.add(cid)
                path = raw_dir / f"{cid}.json"
                if not path.is_file():
                    raise ResultsValidationError(f"active cache record missing: {path}")
                try:
                    record = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError) as exc:
                    raise ResultsValidationError(f"invalid active cache record {path}: {exc}") from exc
                if record.get("cache_id") != cid:
                    raise ResultsValidationError(
                        f"active cache id mismatch: filename={cid}, record={record.get('cache_id')}"
                    )
                if row.get("model") and record.get("model") != row["model"]:
                    raise ResultsValidationError(
                        f"active cache model mismatch for {cid}: "
                        f"row={row['model']!r}, record={record.get('model')!r}"
                    )
                if record.get("error"):
                    provider_errors.append(f"{cid}: {record['error']}")
        if missing_provenance:
            raise ResultsValidationError(
                "rows lack active cache_ids provenance; regenerate decisions.jsonl with the "
                f"current runner (examples: {missing_provenance[:3]})"
            )
        if provider_errors:
            raise ResultsValidationError(
                "active provider/call errors are not a complete sweep: " + "; ".join(provider_errors[:5])
            )

    base_ids = sorted(expected_kinds)
    hard_ids = [bid for bid in base_ids if expected_kinds[bid] in {"ambiguous", "out_of_schema", "adversarial"}]
    adversarial_ids = [bid for bid in base_ids if expected_kinds[bid] == "adversarial"]
    decisions_path = results_dir / "decisions.jsonl"
    decisions_sha256 = (
        hashlib.sha256(decisions_path.read_bytes()).hexdigest()
        if decisions_path.is_file() else None
    )
    return {
        "seed": seed,
        "n_rows": len(rows),
        "n_requests": len(base_ids),
        "n_repro": n_repro,
        "hard_n": len(hard_ids),
        "adversarial_n": len(adversarial_ids),
        "temperatures": {
            "canonical": EXPECTED_TEMPERATURES["canon"],
            "sampled": EXPECTED_TEMPERATURES["repro"],
            "perturbation": EXPECTED_TEMPERATURES["pert"],
        },
        "active_cache_n": len(active_ids) if check_cache else None,
        "cache_checked": check_cache,
        "decisions_sha256": decisions_sha256,
        "backend_label": next(iter({
            row.get("backend_label") for row in rows if row.get("backend_label")
        }), None),
        "model": next(iter({row.get("model") for row in rows if row.get("model")}), None),
        "policy_sha256": next(iter({
            row.get("policy_sha256") for row in rows if row.get("policy_sha256")
        }), None),
        "base_ids": base_ids,
        "kinds": expected_kinds,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a complete decision sweep")
    parser.add_argument("--results", default=str(ROOT / "results"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--no-cache-check", action="store_true",
        help="diagnostic only: skip active-cache provenance and provider-error checks",
    )
    args = parser.parse_args()
    results_dir = Path(args.results)
    rows = load_rows(results_dir / "decisions.jsonl")
    meta = validate_rows(rows, results_dir, seed=args.seed, check_cache=not args.no_cache_check)
    print(
        "PASS: "
        f"rows={meta['n_rows']} requests={meta['n_requests']} variants={len(VARIANTS)} "
        f"n_repro={meta['n_repro']} hard_n={meta['hard_n']} "
        f"adversarial_n={meta['adversarial_n']} active_cache_n={meta['active_cache_n']}"
    )


if __name__ == "__main__":
    main()
