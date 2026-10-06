#!/usr/bin/env python3
"""Refresh the explicit publication component's checksum inventory."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inventory(root: Path = ROOT) -> list[dict]:
    files = []
    for folder in ["tools", "data", "reports", "figures", "collection", "schema"]:
        for path in sorted((root / folder).rglob("*")):
            relative = path.relative_to(root)
            if path.is_file() and "__pycache__" not in relative.parts and "reproduction" not in relative.parts and path.name not in {"verification.json", "verification.md"}:
                files.append(path)
    files.extend(root / name for name in ["README.md", "DATASET_CARD.md", "CITATION.cff", "LICENSE", "LICENSE-DATA", "requirements.txt", "requirements-lock.txt", "REPRODUCE.sh"] if (root / name).is_file())
    return [{"path":str(path.relative_to(root)), "bytes":path.stat().st_size, "sha256":hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(files)]


if __name__ == "__main__":
    data = {"schema_version":2, "release":"publication-2026-10-06", "source_repository":"https://github.com/landigf/BrowseTrace", "artifact_repository":"https://github.com/landigf/publication-artifacts/tree/main/BrowseTrace", "code_license":"Apache-2.0", "data_license":"CC-BY-4.0", "scope":"Sanitized offline artifact; no raw HTTP traces, model credentials, API calls, or private collection bundles required", "files":inventory()}
    (ROOT / "artifact_snapshot.json").write_text(json.dumps(data,indent=2)+"\n")
    print(f"Pinned {len(data['files'])} artifact files.")
