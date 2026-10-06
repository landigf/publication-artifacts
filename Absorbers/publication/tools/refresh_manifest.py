#!/usr/bin/env python3
"""Pin the six complete frozen inputs and publication generation sources."""
from __future__ import annotations

import json
from pathlib import Path

from common import PUBLICATION,ROOT,SWEEPS,sha


def inventory(root: Path = ROOT, publication: Path = PUBLICATION) -> list[dict]:
    files=set()
    for folder in ["agents","taskgen","chain","harness","tests","runtime"]:
        for path in (root/folder).rglob("*"):
            if path.is_file() and path.suffix in {".py",".css",".html"} and "__pycache__" not in path.parts:
                files.add(path)
    for directory in SWEEPS:
        folder=root/directory
        names=["decisions.jsonl","absorbers_chain.json","chain_meta.json"] if "chain" in directory else ["decisions.jsonl","absorbers.json","metrics.json"]
        for name in names:
            path=folder/name
            if not path.is_file():raise FileNotFoundError(f"Required frozen input missing: {directory}/{name}")
            files.add(path)
        for path in (folder/"raw").glob("*.json"):
            files.add(path)
    for folder in ["tools","reports","figures"]:
        for path in (publication/folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and "reproduction" not in path.parts and path.name not in {"verification.json","verification.md"}:
                files.add(path)
    for name in ["REPRODUCE.sh","README.md","ARTIFACT_CARD.md","requirements.txt","requirements-lock.txt"]:
        if (publication/name).is_file():files.add(publication/name)
    return [{"path":str(path.relative_to(root)),"bytes":path.stat().st_size,"sha256":sha(path)} for path in sorted(files)]


if __name__ == "__main__":
    entries=inventory()
    result={"schema_version":1,"release":"publication-2026-10-06","scope":"Six complete frozen-cache sweeps; synthetic seeded procurement workload; offline execution and analysis only","required_sweeps":list(SWEEPS),"n_requests_per_sweep":65,"seed":42,"files":entries}
    (PUBLICATION/"artifact_snapshot.json").write_text(json.dumps(result,indent=2)+"\n")
    print(f"Pinned {len(entries):,} files across all six required sweeps.")
