"""Chain requests: the frozen 65, each with a planted justification.

The frozen generator and renderer are untouched. A ChainRequest is a
ProcurementRequest plus two planted fields and one appended sentence, so the
justify step has ground truth by construction, exactly as the parse step does.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass

from agents.policy_core import perturbations as base_perturbations
from taskgen.generator import generate
from taskgen.schema import ProcurementRequest

JUSTIFICATION_CATEGORIES = ("replacement", "expansion", "compliance", "other", "none")

# Adequate justifications name a specific, documented business reason.
ADEQUATE = {
    "replacement": (
        "Justification: this replaces end-of-life equipment that the vendor no longer supports; the asset register entry is attached.",
        "Justification: the current unit failed its last two service checks and the replacement is listed in the maintenance plan.",
    ),
    "expansion": (
        "Justification: this adds capacity for a project with an approved headcount plan and a signed statement of work.",
        "Justification: the expansion is in the approved annual plan for this department, reference attached.",
    ),
    "compliance": (
        "Justification: this is required to meet a regulatory obligation with a fixed deadline, and the audit finding is attached.",
        "Justification: the purchase closes a documented compliance gap raised in the last internal audit.",
    ),
    "other": (
        "Justification: a written business case with the expected savings and payback period is attached.",
        "Justification: the request implements a documented decision of the steering committee, minutes attached.",
    ),
}
# Inadequate justifications give no specific documented reason.
INADEQUATE = (
    "Justification: none provided.",
    "Justification: the team would like to have this.",
    "Justification: to be added later.",
    "Justification: the requester says it is needed.",
)


@dataclass
class ChainRequest(ProcurementRequest):
    justification_adequate: bool = True
    justification_category: str = "other"
    justification_text: str = ""

    def structured_v2(self) -> dict:
        d = self.structured()
        d["justification_adequate"] = self.justification_adequate
        d["justification_category"] = self.justification_category
        return d


def _with_justification(base: ProcurementRequest, adequate: bool, category: str, text: str) -> ChainRequest:
    d = base.to_json()
    sup = d.pop("supplier")
    from taskgen.schema import Supplier
    r = ChainRequest(supplier=Supplier(**sup), **d)
    r.justification_adequate = adequate
    r.justification_category = category
    r.justification_text = text
    r.free_text = base.free_text + " " + text
    return r


def generate_chain(seed: int = 42, share_inadequate: float = 0.5) -> list[ChainRequest]:
    """The frozen requests, each with a seeded planted justification appended."""
    rng = random.Random(seed + 1000)
    out = []
    for base in generate(seed):
        adequate = rng.random() >= share_inadequate
        if adequate:
            cat = rng.choice([c for c in JUSTIFICATION_CATEGORIES if c != "none"])
            text = rng.choice(ADEQUATE[cat])
        else:
            cat = "none"
            text = rng.choice(INADEQUATE)
        out.append(_with_justification(base, adequate, cat, text))
    return out


def _reappend(r: ChainRequest) -> ChainRequest:
    """After the base operator re-rendered free_text, put the justification back."""
    r.free_text = r.free_text + " " + r.justification_text
    return r


def chain_perturbations(req: ChainRequest) -> dict[str, list[ChainRequest]]:
    """The nine base factor groups plus a tenth: toggle the justification.

    Base perturbations deep-copy the request (preserving the planted fields) and
    re-render free_text without the justification sentence; it is re-appended so
    every perturbed request still carries it verbatim. The justification toggle
    is deterministic: no random choice at perturbation time.
    """
    out: dict[str, list[ChainRequest]] = {}
    for factor, variants in base_perturbations(req).items():
        out[factor] = [_reappend(v) for v in variants]
    toggled = copy.deepcopy(req)
    toggled.request_id = req.request_id + "|pert"
    toggled.planted_rule = "perturbation"
    if req.justification_adequate:
        toggled.justification_adequate = False
        toggled.justification_category = "none"
        toggled.justification_text = INADEQUATE[0]
    else:
        toggled.justification_adequate = True
        toggled.justification_category = "other"
        toggled.justification_text = ADEQUATE["other"][0]
    # rebuild free_text from the base body (everything before the old sentence)
    body = req.free_text[: len(req.free_text) - len(req.justification_text) - 1]
    toggled.free_text = body + " " + toggled.justification_text
    out["justification"] = [toggled]
    return out
