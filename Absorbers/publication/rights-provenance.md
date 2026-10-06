# Absorbers rights and provenance review

The original review below is preserved as the preparation record. The **author-confirmed release addendum dated 6 October 2026** at the end records the subsequent authorization and operative artifact licenses; earlier pending-status statements describe the state before that confirmation.

Prepared 6 October 2026. This report distinguishes source facts from release authority. It grants no license and does not establish contractual or NDA clearance. No employer, client or internship material was obtained or copied for this review.

## Established from the inspected sources

The research repository describes an ASL proof of concept and a later reuse experiment. Its initial commit is `683a98e8` (19 July 2026); the E8 certificate module first appears in `4ba00b1e` (5 September 2026). The inspected reachable history has 39 commits under one recorded committer identity. This identifies repository metadata, not sole authorship, exclusive copyright ownership or absence of other contributors.

The benchmark requests are constructed locally. `taskgen/generator.py` uses `random.Random(seed)`, fixed fictional supplier-name choices, planted policy conditions and a text renderer. Running seed 42 gives exactly 65 requests; every planted rule agrees with the implemented oracle. `taskgen/schema.py` expressly describes the supplier names and screening-list flags as synthetic, with no assertion about real entities. The thresholds and policy cascade are benchmark code, not demonstrated customer policy.

`chain/taskgen.py` extends those same 65 records with seeded justification categories and sentences. Independent generation preserves every base structured field; 31 of the 65 seed-42 justifications are adequate by construction. The measured outputs are cached responses to this synthetic task, rather than observed procurement decisions. Reproduction of the six intended namespaces is a separate numerical gate.

The inspected Python components (`taskgen`, `agents`, `harness`, `chain`, `runtime`) import local research modules, standard-library modules and the documented SDK/plotting dependencies. No external customer-code dependency was found in that import inspection. This is limited evidence about the current source tree, not an exhaustive derivation or confidentiality audit. `agents/llm_client.py` excludes the API key from its public call specification; the specification includes the actual endpoint, prompts and model label so recorded inference provenance can be checked. Secret-free call specifications do not themselves establish redistribution rights for model outputs.

`CLAUDE.md` explicitly says the earlier AuditChain prototype came from a four-person hackathon team; the applicant's architecture, fuzzy-threshold and run-recording contributions are self-reported. `venture/wedge.md` likewise calls the present proof of concept a reimplementation with an added measurement harness. Those statements establish the documented lineage and attribution boundary. They do not prove a clean-room implementation, identify the other three contributors, waive their rights or settle the new papers' author lists. Neither a prize nor a deployment claim follows.

The local project instructions exclude employer and internship material. The publication preparation should continue that exclusion and omit outreach, venture applications and unrelated employer/client repositories from artifact allowlists. This reviewer did not examine or reproduce contract terms. An applicable NDA or employment agreement cannot be described as cleared from this inspection.

No tracked `LICENSE`, `COPYING` or `COPYRIGHT` file was present at review time. A public repository or a single recorded committer is insufficient evidence of a license grant. Third-party dependencies retain their own licenses. Any root-prepared code/data licensing text remains a proposal until the relevant owner confirms authority; this review installs no final license.

## Confirmations reserved for the author or rights owner

- Confirm the actual contributors to the present code, benchmark, experiments and manuscripts. Determine whether earlier team members require acknowledgment, permission or coauthorship based on their actual contributions; do not infer their status from this report.
- Confirm that no employer, internship or client code, confidential policy, proprietary data or restricted derivative material is present in the proposed release. Resolve any institutional, employment, hackathon or other contractual restrictions that apply. This review is not a legal interpretation of those agreements.
- Confirm authority to release the newly authored code, synthetic inputs, cached model responses and derived outputs under the proposed licenses. Check relevant service/output terms and preserve dependency notices where redistribution requires them.
- Confirm the manuscript author list and affiliation, approve the AI disclosure and accept responsibility for the contents. AI assistance is not authorship or evidence of rights clearance.
- Select the paper distribution license and satisfy account/category endorsement separately. [arXiv's moderation policy](https://info.arxiv.org/help/moderation/index.html#rights-to-submit-material) requires original work and/or authority to grant the selected license.

## Release boundary

Technical preparation may finish with compiling papers and reproducible, allowlisted archives while these confirmations remain pending. Public upload, DOI deposit and repository publication are separate release decisions. Keep the historical draft intact, publish only the reviewed source/results allowlist, and identify unresolved rights explicitly. A proposed license must not be presented as already granted.

## Author-confirmed release addendum — 6 October 2026

Gennaro Francesco Landi confirmed sole authorship of the current manuscripts, supplied landig@ethz.ch and ORCID 0009-0002-9641-8382, and confirmed the public ETH Zurich affiliation. The ORCID checksum and matching public record were independently checked; record entries are self-asserted and are not an institutional verification of status or legal authority.

The author affirmatively answered the release-rights question covering authority to publish and redistribute the reviewed materials, including applicable contributor, institutional, contractual, confidentiality and model-output restrictions. This is an author representation of authority, not an independent legal opinion or a reproduced third-party waiver. The documented four-person AuditChain lineage and the limited inspection findings above remain part of provenance.

The author approved the prepared PDFs and AI disclosures, accepted responsibility for the work, accepted the proposed arXiv non-exclusive distribution license, authorized Apache-2.0 for the reviewed artifact software and CC-BY-4.0 for its synthetic inputs, recorded model outputs and derived data, and explicitly instructed public release. `LICENSE` now contains the operative scoped software grant and full Apache 2.0 text. `LICENSE-DATA` contains the operative data grant and full CC BY 4.0 legal text. Retain existing third-party notices; these grants do not relicense third-party material or unrelated repository contents. `author-confirmations.json` records the confirmations separately from frozen empirical inputs.

The canonical public artifact destination is [publication-artifacts/Absorbers](https://github.com/landigf/publication-artifacts/tree/main/Absorbers); the earlier [AuditableAgents repository](https://github.com/landigf/AuditableAgents) remains source provenance. Authorization does not establish that publication or submission has occurred. No DOI, arXiv identifier, venue submission or acceptance is claimed. arXiv submitting-account access and cs.DC endorsement readiness remain unverified. An anonymous venue variant remains separate; its submission authorization is not established. Historical manuscripts and repository history remain preserved; no history rewrite or force push is authorized.
