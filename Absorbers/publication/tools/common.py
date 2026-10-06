"""Resolve nested and exported publication layouts without private paths."""
from __future__ import annotations

import hashlib
from pathlib import Path

PUBLICATION = Path(__file__).resolve().parents[1]


def find_root() -> Path:
    for parent in [PUBLICATION,*PUBLICATION.parents]:
        if (parent/"harness/runner.py").is_file() and (parent/"chain/runner.py").is_file():
            return parent
    raise FileNotFoundError("Publication artifact lacks harness and chain source")


ROOT = find_root()
SWEEPS = {"results":"deepseek","results-reasoner":"deepseek-reasoner","results-local":"ollama:gemma3:4b","results-chain-deepseek":"deepseek","results-chain-gemma3":"ollama:gemma3:4b","results-chain-phi4":"ollama:phi4-mini"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
