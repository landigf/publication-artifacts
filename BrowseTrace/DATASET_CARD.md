# BrowseTrace dataset card

**Curator:** Gennaro Francesco Landi, ETH Zurich, [ORCID 0009-0002-9641-8382](https://orcid.org/0009-0002-9641-8382), landig@ethz.ch. **Canonical artifact destination:** https://github.com/landigf/publication-artifacts/tree/main/BrowseTrace. **Historical source repository:** https://github.com/landigf/BrowseTrace. **Status:** author-approved empirical research artifact prepared locally on 6 October 2026; public release authorized, with no upload, deposit, submission, acceptance or assigned DOI recorded by this preparation. **Code license:** Apache-2.0. **Data license:** CC-BY-4.0. Manuscript distribution is separate.

## Content and units

| Released component | Rows or observations | Meaning |
|---|---:|---|
| Main scripted census | 400 | All traffic-producing historical attempts |
| Main LLM census | 911 | 813 nonempty attempts and 98 empty attempts |
| Separate local LLM comparison | 300 | Not added to the 1,311-attempt main corpus |
| Human reference | 10 | Ten tasks performed by the author |
| Scripted replay CSV | 82,455 | Positive-size subset of recorded requests |
| LLM replay CSV | 357,782 | Includes 121,709 zero-size rows excluded from replay |
| Liveness-count CSV | 1,611 | Main corpus plus local comparison; excludes human reference |

The session census contains unique artifact IDs, cohort/source/workload/model/region/task labels, request totals, byte totals, and nonempty flags. Liveness counts contain independent artifact IDs, distinct hostname counts and status-200 nonroot request counts. They reproduce the mechanical predicate (at least three hostnames and five status-200 nonroot requests); they do not label semantic task success. Raw URLs are omitted, so predicate count extraction cannot be independently repeated from this artifact.

Replay CSV columns are `timestamp_us`, `cache_key`, `object_size_bytes`, `session_id`, and optional `agent_type`. `cache_key` is an opaque full-key HMAC token. `session_id` is an opaque legacy-label token, not the session-census identifier. LLM legacy labels collide across models/regions; per-row model or region attribution is unavailable. Timestamps preserve historical row order and are not used to synthesize cross-session concurrency. Byte totals are recorded object sizes, not verified on-wire byte counters.

## Collection and confounding

Ten task families are specified in `collection/tasks.yaml`, including API integration, documentation lookup, fact checking, job market, literature review, news aggregation, product comparison, real estate, regulatory lookup, and travel planning. Historical collection spans local/cloud browser substrates and region labels; aliases are not treated as independent experimental treatments.

`data/provenance/collection-metadata-audit.json` reports retained source-level configurations. All 400 scripted attempts use a 15-step scripted-random driver. The separate local LLM comparison uses a 20-step budget. Main LLM attempts include 810 at 20 steps, 100 GPT Zurich attempts at 8 steps and one Qwen smoke attempt at 10 steps. Thus measured amplification depends on the cohort and its configuration.

No balanced factorial design, causal model effect, representative human population, task-success comparison, ethical review determination or current-live-web reproduction is asserted. The current collection code is supplied for methodological inspection and may differ from historical exporters. The stitched replay inputs, census and hashed preparation audit define this evaluated artifact.

The author confirmed that the ten retained human task observations are their own and that publication authority and consent are established. This is an author confirmation, not independent verification of collection authenticity or an ethical-review determination. The author also confirmed authority to publish and redistribute the reviewed manuscript, code and data under the selected licenses; no independent legal clearance is claimed.

## Sanitization

Whole-key anonymization preserves the source cache-key equality relation across both traces, order, timestamps and object sizes. The preparation-time pinned replay comparison found exactly equal request/byte results for all 60 configurations. Cryptographic tokens and aggregate count tables are released; the ephemeral HMAC secret, original URLs, request/response headers/bodies, raw session bundles and original identifiers are omitted. The exported bytes are fixed and hashed; regenerating opaque tokens is not the reproduction procedure.

The collection code can capture sensitive fields. `tools/sanitize_release.py` strips Authorization/Cookie/Set-Cookie and vendor API/token header fields, with a negative fixture. This utility alone is insufficient to publish raw HTTP traces. Historical public repository privacy remediation is tracked separately; this artifact does not claim that older repository history has been cleaned.

## Suitable use and limitations

Use this artifact to inspect descriptive cohort accounting, study fixed-sequence object-cache request/byte tradeoffs, and test offline replay tooling. Do not infer origin HTTP eligibility, freshness or Vary behavior, latency/cost/deployment gains, population agent-versus-human traffic growth, or model/region effects from aggregate replay rows. Synthetic human replay and legacy regressions are excluded.

The preparation and release checks use existing historical data only. Public statistics are reproducible from the released projections. Authenticity and completeness of the original collection remain historical provenance assumptions, explicitly distinguished from verified arithmetic and simulation.

Positive-size object identity is not size-stable: 3,136 of 17,144 scripted keys and 5,637 of 17,931 LLM keys have differing positive sizes. In the pinned policies, a hit does not update the stored object size; new admission uses its incoming size. Byte-hit numerators use current request sizes. Capacity therefore bounds this admission-time accounting, not verified physical HTTP representation bytes. `reports/replay-size-audit.json` reproduces the counts, and six-policy fixtures verify this behavior.
