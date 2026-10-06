"""Read-only, offline replay of canonical rows from a validated frozen cache.

This command never collects a provider response and never writes an artifact.
It reconstructs the selected canonical decision through the normal agent code,
builds the row through the sweep runner's shared builder, and requires exact
equality with the versioned row before printing a readable, terminal-safe
report of the synthetic request and stored decision evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents.llm_client import (
    CachedCallError,
    InvalidCacheRecord,
    OfflineCacheMiss,
    config_for,
)
from harness.runner import VARIANTS, build_decision_row
from harness.validate_results import ROOT, load_rows, validate_rows
from agents.policy_core import oracle
from taskgen.generator import generate


class ReplayError(RuntimeError):
    """Raised when a requested replay cannot be proven exact."""


FROZEN_BACKENDS = {
    ("deepseek", "deepseek-chat"): "deepseek",
    ("dsr", "deepseek-reasoner"): "deepseek-reasoner",
}


def parse_variants(value: str) -> list[str]:
    variants = [item.strip() for item in value.split(",") if item.strip()]
    if not variants:
        raise ReplayError("at least one variant is required")
    if len(variants) != len(set(variants)):
        raise ReplayError("variants must not contain duplicates")
    unknown = sorted(set(variants) - set(VARIANTS))
    if unknown:
        raise ReplayError(f"unknown variants: {unknown}")
    return variants


def frozen_backend(rows: list[dict]) -> str:
    identities = {(row.get("backend_label"), row.get("model")) for row in rows}
    if len(identities) != 1:
        raise ReplayError(f"artifact mixes backend identities: {sorted(identities)!r}")
    identity = next(iter(identities))
    try:
        return FROZEN_BACKENDS[identity]
    except KeyError as exc:
        raise ReplayError(
            "no read-only frozen-cache configuration for "
            f"backend_label={identity[0]!r}, model={identity[1]!r}"
        ) from exc


def request_for_id(request_id: str, seed: int):
    matches = [request for request in generate(seed) if request.request_id == request_id]
    if not matches:
        raise ReplayError(f"unknown canonical request id: {request_id}")
    return matches[0]


def replay(
    results_dir: Path,
    request_id: str,
    variants: list[str],
    *,
    seed: int = 42,
) -> tuple[dict, dict, list[dict]]:
    """Validate, replay, and compare selected canonical rows without writes."""
    rows = load_rows(results_dir / "decisions.jsonl")
    metadata = validate_rows(rows, results_dir, seed=seed, check_cache=True)
    backend = frozen_backend(rows)
    request = request_for_id(request_id, seed)
    oracle_result = oracle(request)
    cfg = config_for(backend, temperature=0.0, offline=True)
    cache_dir = results_dir / "raw"

    replayed = []
    for variant in variants:
        stored_matches = [
            row for row in rows
            if row["base_request_id"] == request_id
            and row["variant"] == variant
            and row["phase"] == "canon"
        ]
        if len(stored_matches) != 1:
            raise ReplayError(
                f"expected one stored canonical row for {request_id}/{variant}; "
                f"found {len(stored_matches)}"
            )
        result = VARIANTS[variant](
            request, cfg, "canon", cache_dir, offline=True,
        )
        rebuilt = build_decision_row(
            variant, "canon", "canon", None, request, result, cfg, seed,
        )
        stored = stored_matches[0]
        if rebuilt != stored:
            differing = sorted(
                key for key in set(rebuilt) | set(stored)
                if rebuilt.get(key) != stored.get(key)
            )
            raise ReplayError(
                f"exact row mismatch for {request_id}/{variant}; "
                f"differing fields: {differing}"
            )
        replayed.append({
            "request_id": request_id,
            "variant": variant,
            "decision": rebuilt["decision"],
            "oracle_decision": rebuilt["oracle_decision"],
            "oracle_match": rebuilt["decision"] == rebuilt["oracle_decision"],
            "firing_rule": rebuilt["firing_rule"],
            "cited_factors": rebuilt["cited_factors"],
            "explanation": rebuilt["explanation"],
            "llm_calls": rebuilt["llm_calls"],
            "cache_ids": rebuilt["cache_ids"],
            "stored_row_match": "PASS",
        })
    request_report = {
        "request_id": request.request_id,
        "kind": request.kind,
        "synthetic_request_text": request.free_text,
        "oracle_decision": oracle_result.decision.value,
        "oracle_rule": oracle_result.firing_rule,
    }
    return metadata, request_report, replayed


def format_report(metadata: dict, request: dict, records: list[dict]) -> str:
    """Render replay evidence without emitting raw control characters."""
    lines = [
        "OFFLINE CANONICAL REPLAY (validated frozen cache)",
        (
            f"artifact_rows={metadata['n_rows']} "
            f"requests={metadata['n_requests']} "
            f"active_cache_n={metadata['active_cache_n']}"
        ),
        f"request_id={request['request_id']} kind={request['kind']}",
        "synthetic_request_text=" + json.dumps(
            request["synthetic_request_text"], ensure_ascii=False,
        ),
        (
            f"oracle_decision={request['oracle_decision']} "
            f"oracle_rule={request['oracle_rule']}"
        ),
        "",
    ]
    for record in records:
        firing_rule = record["firing_rule"] or "<none>"
        lines.extend([
            (
                f"{record['variant']}: decision={record['decision']} "
                f"oracle_match={record['oracle_match']} "
                f"firing_rule={firing_rule} "
                f"llm_calls={record['llm_calls']} "
                f"stored_row_match={record['stored_row_match']}"
            ),
            "  cache_ids=" + json.dumps(
                record["cache_ids"], ensure_ascii=False,
            ),
            "  cited_factors=" + json.dumps(
                record["cited_factors"], ensure_ascii=False,
            ),
            "  explanation=" + json.dumps(
                record["explanation"], ensure_ascii=False,
            ),
            "",
        ])
    lines.append(
        "PASS: offline replay exactly matches "
        f"{len(records)} stored canonical rows; no network or files written."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only exact replay of canonical rows from the frozen cache",
    )
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--variants", default="A0,A3")
    parser.add_argument("--results", default=str(ROOT / "results"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        variants = parse_variants(args.variants)
        metadata, request, records = replay(
            Path(args.results), args.request_id, variants, seed=args.seed,
        )
    except (
        OSError,
        ValueError,
        ReplayError,
        OfflineCacheMiss,
        CachedCallError,
        InvalidCacheRecord,
    ) as exc:
        parser.exit(1, f"ERROR: {exc}\n")

    print(format_report(metadata, request, records))


if __name__ == "__main__":
    main()
