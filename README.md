# Research preprints and reproducible artifacts

Gennaro Francesco Landi, ETH Zurich. [ORCID](https://orcid.org/0009-0002-9641-8382). Contact: landig@ethz.ch.

This repository accompanies two empirical research preprints. They have not been peer reviewed. No arXiv identifier, conference acceptance, or artifact DOI has been assigned.

| Paper | Preprint | Complete reproducible artifact | Scope |
| --- | --- | --- | --- |
| BrowseTrace | [PDF](papers/BrowseTrace-preprint.pdf) | [Artifact ZIP](https://github.com/landigf/publication-artifacts/releases/download/v2026-10-06/BrowseTrace-artifact.zip) | Sanitized browser-workload census and HTTP object-cache replay; conditional traffic comparisons. |
| Absorbers | [PDF](papers/Absorbers-preprint.pdf) | [Artifact ZIP](https://github.com/landigf/publication-artifacts/releases/download/v2026-10-06/Absorbers-artifact.zip) | Decision-equivalence certificates and a reuse counterexample in frozen synthetic agent workflows. |

## Reproduce

Download the complete artifact ZIP for the paper from [release v2026-10-06](https://github.com/landigf/publication-artifacts/releases/tag/v2026-10-06), check it against [SHA256SUMS](SHA256SUMS), and extract it into a separate directory. Follow the artifact's README to create the pinned Python 3.12 environment. BrowseTrace uses `sh REPRODUCE.sh`; Absorbers uses `sh REPRODUCE.sh --offline`. Both execute offline from fixed inputs without model credentials. The release also includes compiling arXiv source archives for both papers.

BrowseTrace's complete sanitized artifact is browsable in [BrowseTrace](BrowseTrace). [Absorbers](Absorbers) provides code, results, figures and documentation for inspection; its 53,632 frozen cache records are available in the complete artifact ZIP. Use that ZIP for full Absorbers reproduction and manifest verification.

## Measured limits

BrowseTrace's 3.3–4.2× comparison applies to one local configuration. Its pooled main corpus does not show that amplification. The replay measures an object-cache model, not deployable HTTP cache correctness or population traffic growth.

Absorbers evaluates six frozen configurations of a synthetic policy. The E8 certificate saves nominal feeding-step call slots; runtime latency and certificate overhead are unmeasured. Invalid-output and unscoreable cases remain explicit exclusions from equivalence guarantees.

## Licensing and provenance

Software and data grants are scoped separately in each artifact's `LICENSE` and `LICENSE-DATA`: Apache-2.0 for software, CC-BY-4.0 for the retained data and derived results. Absorbers stores these files under `publication/`. Dependency licenses remain their own. Manuscripts are copyright Gennaro Francesco Landi; their distribution license is separate from the artifact licenses.

The archives contain preparation-time metadata and checksum manifests. The actual public release status is recorded in the GitHub release and `RELEASE-STATUS.json`. The historical source repositories are provenance references; they are not the verified release download path. This repository begins with an allowlisted snapshot, without importing historical repository commits.
