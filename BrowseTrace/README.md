# BrowseTrace publication artifact

This author-approved publication package supports the empirical preprint **BrowseTrace: Reproducible HTTP Traces and Cache Replay for Browser-Mediated AI Workloads**. Public release of the paper and reviewed code/data is authorized. No upload, DOI deposit, arXiv submission or conference acceptance is recorded by this local preparation; no DOI or arXiv identifier has been assigned.

The canonical artifact destination is [BrowseTrace in publication-artifacts](https://github.com/landigf/publication-artifacts/tree/main/BrowseTrace). The [historical BrowseTrace repository](https://github.com/landigf/BrowseTrace) records source provenance; its history is separate from this clean publication package. The author is Gennaro Francesco Landi, ETH Zurich, [ORCID 0009-0002-9641-8382](https://orcid.org/0009-0002-9641-8382), contact landig@ethz.ch.

## Reproduce offline

Use Python 3.12, install `requirements-lock.txt` in a virtual environment, then run:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
BROWSETRACE_PYTHON=.venv/bin/python sh REPRODUCE.sh
```

The verifier checks published file hashes, schemas, corpus denominators, cache-key opacity, source-projection consistency, credential-header removal, and simulator behavior. It reruns all six policies at five capacities for both workloads, compares numerical results, then regenerates figures and TeX inputs under `reports/reproduction/`. Model/API access and private raw bundles are unnecessary. Installation needs package downloads; execution uses local files only.

For a quick integrity check without the 60 replays: `.venv/bin/python tools/verify_publication.py`. The released figures are in `figures/`, generated manuscript numbers/tables in `reports/`, and complete results in `reports/public-cache-replay.json` and `reports/publication-statistics.json`.

## What is measured

The main census comprises 400 scripted attempts and 911 LLM attempts, including 98 zero-request attempts. A separate local comparison comprises 300 LLM attempts; its baseline is the 100 Zurich scripted attempts already inside the main corpus. Ten task observations performed by the author are a descriptive reference, not a population baseline. The author confirmed publication authority and consent for these observations; no ethical-review determination is claimed.

The main LLM mean is 392.7 recorded requests per attempt versus 420.2 for scripted browsing; excluding empty LLM attempts gives 440.1. Ratios of 3.324–4.166 occur only in the separate local comparison against the Zurich scripted mean of 148.3. Models, browser substrates, step budgets, geography and collection batches differ. These comparisons do not isolate model effects.

`data/traces/full_400_sessions.csv` has 82,455 positive-size replay rows; `llm_full_901.csv` has 357,782 rows, of which libCacheSim processes 236,073 positive-size rows. The LLM filename is historical: the census establishes 911 attempts, and its 100 repeated legacy session labels cannot identify models or regions per replay row. The CSV labels and census IDs are separate namespaces and must not be joined.

Cache results use cold caches, stored row order, default libCacheSim settings, and MiB capacities. This is an object-cache experiment; it does not implement HTTP Cache-Control, freshness, Vary or authenticated cache eligibility, and does not measure network latency or serving throughput. Request and byte hit ratios are different objectives: at 5 MiB scripted GDSF beats LRU on request hits (59.5% versus 37.4%) but loses on byte hits (15.8% versus 24.9%). S3-FIFO has higher byte hits than both.

## Provenance and privacy

Entire cache keys are opaque one-to-one HMAC tokens, not URLs. The shared ephemeral secret was discarded. Source-to-export equality and preserved field sequences were verified during export; all 60 source/released replay results agree exactly in the pinned implementation. See `reports/input-transformation.json` and `reports/opaque-replay-equivalence.json`. These reports record the preparation-time comparison; original private inputs are not released.

The released census and liveness-count CSVs are sanitized projections of retained historical summaries/traces. Every retained numerical claim is recomputed from those projections; this does not independently establish collection authenticity or semantic task success. Raw session bundles, HTTP headers/bodies, original keys/identifiers, and secrets are omitted. Hash inventories cover the explicit release files.

`collection/` and `schema/` retain the public collection implementation and task definitions for inspection. The import paths were repaired for this artifact layout and credential header fields are removed from request/response exports. This historical code is separate from offline reproduction; exact historical browser/model dependencies are unavailable, and rerunning it would collect a new live-web workload. No new collection was performed.

Artifact code is Apache 2.0 under `LICENSE`; sanitized traces, census and observation data, provenance records and derived results retain CC BY 4.0 under `LICENSE-DATA`. These grants do not change manuscript licensing. The author selected the arXiv non-exclusive distribution license for the paper; submitting-account access and cs.NI endorsement readiness remain unverified. Publication and redistribution rights are author-confirmed; technical verification does not independently establish legal clearance. See `CITATION.cff` for artifact citation and `release-instructions.md` for release status. `tools/prepare_inputs.py` is the one-time anonymizer; do not rerun it to reproduce the fixed snapshot, because fresh anonymization generates new keys.

Positive-size object identity is not size-stable: 3,136 of 17,144 scripted keys and 5,637 of 17,931 LLM keys have differing positive sizes. In the pinned policies, a hit does not update the stored object size; new admission uses its incoming size. Byte-hit numerators use current request sizes. Capacity therefore bounds this admission-time accounting, not verified physical HTTP representation bytes. `reports/replay-size-audit.json` reproduces the counts, and six-policy fixtures verify this behavior.
