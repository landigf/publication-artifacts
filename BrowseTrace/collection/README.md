# Historical collection source

These files were copied from the public BrowseTrace snapshot and import paths were adapted to this artifact's `schema/` directory and request/response credential headers are rejected at capture/export. They document task definitions, browser instrumentation and orchestration. They are **not** invoked by `REPRODUCE.sh`.

The runner constructs a task prompt from each name, description, access pattern and starting URLs. Its retained agent path sets `use_vision=False`, at most four actions per step, ten failures and a 60-second step timeout. The runner default is 15 steps; historical summary metadata records source-specific overrides in `data/provenance/collection-metadata-audit.json`. The code is a methodological reference, not evidence that every historical collection used this exact version.

`requirements.txt` is the retained historical collection dependency list, with unpinned browser/model ranges. It does not reconstruct an exact collection environment. Live recollection would require appropriate browser dependencies and provider access and would produce a new workload. No collection/API execution is part of this release's verification.

The tracer can capture credential headers. Raw exports require separate privacy review; the publication artifact releases only opaque-key CSVs and aggregate provenance, not raw HTTP JSON.
