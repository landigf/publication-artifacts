# Absorbers artifact card

**Purpose:** evaluate decision stability under feeding-step reuse and valid-class certificates in one synthetic procurement decision domain. **Status:** author-approved empirical publication package; public paper and artifact release authorized on 6 October 2026. No live collection, DOI deposit, arXiv identifier, conference submission or acceptance is claimed. **Author:** Gennaro Francesco Landi, ETH Zurich; landig@ethz.ch; [ORCID](https://orcid.org/0009-0002-9641-8382). **Licenses:** Apache-2.0 artifact software (`LICENSE`); CC-BY-4.0 synthetic inputs, recorded model outputs and derived data/figures (`LICENSE-DATA`).

**Canonical artifact destination:** [publication-artifacts/Absorbers](https://github.com/landigf/publication-artifacts/tree/main/Absorbers). The [AuditableAgents repository](https://github.com/landigf/AuditableAgents) is source provenance. Publication execution and any assigned identifiers must be recorded by the publishing workflow. An anonymous venue variant remains separate and is excluded from the public artifact.

## Inputs

| Configuration | Task graph | Frozen backend identity | Requests |
|---|---|---|---:|
| `results` | A0–A3 main grid | DeepSeek chat alias | 65 |
| `results-reasoner` | A0–A3 main grid | DeepSeek thinking alias | 65 |
| `results-local` | A0–A3 main grid | Gemma3:4b | 65 |
| `results-chain-deepseek` | C3 chain | DeepSeek chat alias | 65 |
| `results-chain-gemma3` | C3 chain | Gemma3:4b | 65 |
| `results-chain-phi4` | C3 chain | Phi4-mini | 65 |

The six configurations share a seeded synthetic workload; they are not six independent task populations or a balanced randomized model comparison. Historical endpoint labels identify frozen configurations and should not be used for future collection. Raw provider metadata and prompt-storage vintage qualify the historical comparisons.

## Outputs and denominators

- `reports/numbers.tex` contains generated E1–E3 and E8 manuscript macros.
- `reports/certificate-table.tex` compares all three certificate schemes; `certificate-eq-table.tex` gives primary equality-certificate counts.
- `reports/publication-data.json`, `certificate-tradeoff.json` and `canary-detection.json` supply the figure/table data and assumptions.
- `figures/certificate-tradeoff`, `canary-detection` and `fig_e3_depth` are provided as PDF and PNG.

E8 retains 1,029 DeepSeek, 1,029 Gemma3 and 935 Phi4-mini perturbation rows after invalid canonical records exclude requests from reuse analysis. Fresh-invalid/unscoreable rows are 1, zero and 30 respectively. Certified fractions divide by **all retained rows**; saved-slot fractions divide by **twice all retained rows**. Invalid exclusions are separately reported and cannot be described as successful certificate tests. Three unscoreable Phi4-mini rows have invalid candidate outputs.

`cert_eq` considers valid consumer value classes. It preserves the evaluated decision on the scored records, not oracle truth, invalid-output fail-safe behavior or arbitrary deployment conditions. Certificate construction receives runtime-observable structured differences and recorded fields, while oracle/factor labels remain evaluation-only. The policy and witness enumeration are specific to this finite consumer.

Canary curves assume uniformly sampled requests without replacement, empirical per-request mismatch rates, conditional independence and a stationary configuration. The observed-bad-set curve is a hypothetical comparison scenario, not a statistical confidence bound on future mismatch rates. No population extrapolation, temporal drift model or production latency/cost gain is asserted.

## Reproduction and privacy

The new publication script requires all six sweeps, uses an isolated copied tree and rejects live mode. It checks file hashes, byte-identical decision grids, equal analysis JSON, exact E8 denominators and invariants, generated figures/tables, existing offline tests and focused publication tests. It does not overwrite the historical paper.

The joint-witness regression compares production outcome sets with an independently chosen richer amount/budget grid across all 1,541 memoized retained contexts and three candidate-step combinations. Adjacent representable values bracket binding cuts; all category/compliance combinations are included. The 10,277,634 richer-grid evaluations agree with production witness decision sets, and all 10,052 retained schema-valid numeric fields are finite and nonnegative. This checks the assumed numeric domain and frozen policy empirically rather than proving completeness for arbitrary inputs or policies.

Only synthetic cache prompts/responses and necessary source/results files are intended for the artifact. Raw records are screened for nonempty credential fields; output values are not printed if screening fails. The author affirmatively confirmed publication and redistribution authority, approved the manuscripts and AI disclosures, and accepted responsibility; see `author-confirmations.json`. These confirmations provide release authority without a claim of independent legal clearance. Third-party rights and notices remain applicable. Manuscript distribution is separate from the code/data licenses, and arXiv account/endorsement readiness remains unverified.
