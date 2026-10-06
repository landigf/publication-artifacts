# Absorbers publication artifact

This artifact supports the empirical preprint prepared from the retained synthetic procurement experiments. It contains six frozen-cache configurations, generated E8 certificate tables, a certificate tradeoff figure and the E5 canary detection curves. The sole author, Gennaro Francesco Landi (ETH Zurich), approved the prepared manuscripts and AI disclosures and authorized public paper and artifact release on 6 October 2026. No DOI, arXiv identifier, conference submission or acceptance is assigned here.

The canonical artifact destination is [Absorbers in publication-artifacts](https://github.com/landigf/publication-artifacts/tree/main/Absorbers). The [AuditableAgents research repository](https://github.com/landigf/AuditableAgents) records source provenance. Public publication is recorded separately by the publishing workflow. Contact: landig@ethz.ch; [ORCID 0009-0002-9641-8382](https://orcid.org/0009-0002-9641-8382).

Artifact software is licensed under Apache 2.0 in `LICENSE`. Synthetic requests, recorded model outputs, experiment metadata and derived tables/figures are licensed under CC BY 4.0 in `LICENSE-DATA`. These files define the respective scopes and preserve third-party rights and notices. The manuscript distribution authorization is separate; the author approved the arXiv non-exclusive distribution license for an actual arXiv submission.

## Reproduce offline

Use Python 3.12 and the pinned plotting dependency closure:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r publication/requirements-lock.txt
ABSORBERS_PYTHON=.venv/bin/python sh publication/REPRODUCE.sh --offline
```

The exported archive retains the `publication/` directory and provides an additional top-level `REPRODUCE.sh` wrapper. The tools discover the source root from `harness/runner.py` and `chain/runner.py`, without private paths. For an integrity-only check, run `.venv/bin/python publication/tools/reproduce.py --check`.

The script requires **all six** configurations: `results`, `results-reasoner`, `results-local`, `results-chain-deepseek`, `results-chain-gemma3`, and `results-chain-phi4`. Missing configurations fail; optional historical partial sweeps are excluded. Each main or chain configuration uses 65 synthetic requests generated with seed 42.

The reproducer creates a disposable directory, copies frozen caches, reruns all six decision grids from cache, verifies decision files byte-for-byte and analysis JSON by value, checks E8 invariants, regenerates manuscript macros/tables/figures, and runs the complete existing unittest discovery plus seven publication boundary tests. Verified outputs go to `publication/reports/reproduction/`. Historical manuscripts, numbers and figures are not overwritten. Installation downloads dependencies; reproduction performs no model/API/network collection and rejects `--live`.

## Interpreting the certificate experiment

`cert_eq` checks whether the fixed deterministic consumer's decision is identical across enumerated **valid** output classes for candidate feeding steps. The certificate uses the structured request difference, canonical candidate records, fresh noncandidate records and consumer code. Oracle/factor labels are reserved for scoring; they are not certificate inputs. This is a synthetic offline prototype, not a deployable free-text change detector or a proof for arbitrary policies/models.

The equality certificate retained 50.9%, 52.1% and 50.5% of nominal feeding-step opportunities in DeepSeek, Gemma3 and Phi4-mini respectively, with zero observed scored decision changes or introduced unsafe approvals. Saved nominal slots use `reused feeding slots / (2 * all E8 perturbation rows)`; this includes fail-fast rows whose second call did not run. Certified-row fractions also use **all rows**, including unscoreable rows: 852/1,029, 878/1,029 and 768/935. The respective scoreable subsets contain 1,028, 1,029 and 905 rows.

One DeepSeek and 30 Phi4-mini fresh-invalid rows are unscoreable; three Phi4-mini rows include an invalid candidate output. Those cases are not demonstrated safe by the valid-class equality certificate. Gemma3 has no such exclusions. Oracle correctness on certified rows remains below 100%; agreement with recomputation does not imply that the original decision is correct.

The certificate comparison includes `cert_safe_valid` and `cert_safe_all` to show different semantics. The latter includes the invalid-output fail-safe class and prevents certification of approving reuse; it does not certify universal decision equality. All schemes and exact denominators appear in `reports/certificate-table.tex` and `reports/publication-data.json`.

`tools/joint_witness_check.py` compares the production joint witness decision sets with an independently chosen richer numerical grid, including category/compliance combinations, binding cuts and adjacent representable values. It checks all 1,541 distinct retained fixed contexts for parse-only, justify-only and both-step candidates, with 10,277,634 richer-grid evaluations. All 10,052 recorded schema-valid amount/budget fields are finite and nonnegative. `reports/joint-witness-validation.json` records the exact scope. This finite regression supports the frozen policy and workload; it does not prove general completeness.

## Canary model

`figures/canary-detection.pdf` and `reports/canary-detection.json` derive detection probabilities from retained per-request mismatch profiles. Requests are sampled uniformly without replacement. The one-recompute curve uses empirical per-request mismatch probabilities and conditional independence under a stationary configuration; the observed-bad-set scenario assigns mismatch probability one to any request that ever mismatched; it bounds the fitted empirical curve, without providing a statistical confidence bound on future mismatch rates. These curves describe the enumerated workload and assumptions, not future deployment reliability.

## Provenance and release status

Frozen raw caches store synthetic prompts/model responses, call specifications where available, timing/token counts and provider model metadata. Legacy cache entries without stored prompts remain identified by the existing analysis. They contain no API-key fields according to the publication scanner; credentials are never required for replay. `author-confirmations.json` records the author's affirmative publication and redistribution authority, including the rights gate, manuscript approval, responsibility and license choices. This is author-confirmed authority, without a claim of independent legal clearance. `rights-provenance.md` retains the earlier review and a dated addendum.

arXiv account access and cs.DC endorsement readiness remain unverified. An anonymous venue variant remains a separate local submission draft and is excluded from this public artifact. Repository history and historical manuscripts are preserved; the release authorization does not authorize history rewriting or force-pushing.

Generator defaults continue to target the historical paper layout. New `--publication` modes explicitly target the new artifact; do not run the historical root `REPRODUCE.sh` to reproduce this publication package.
