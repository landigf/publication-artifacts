"""Domain schema for the Autonomy Quadrant procurement PoC.

One decision task: given a free-text procurement request, decide
``approve`` / ``reject`` / ``escalate`` under a written policy.

Everything downstream (the four agent variants, the deterministic oracle,
the perturbation prober, the metrics) speaks the vocabulary defined here.
The canonical factor names in ``FACTORS`` are the ONLY strings an agent may
cite as load-bearing, so that faithfulness precision/recall are well defined.

No real organisations, people, or countries are modelled. Supplier names are
synthetic and the "sanctioned" flag is an abstract screening-list bit, not a
claim about any real entity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class Decision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


# Canonical factor vocabulary. Recognised citations map to these strings;
# unrecognised emitted strings are retained separately so precision can penalise
# unsupported factor names rather than silently dropping them.
FACTORS = (
    "category",
    "amount",
    "budget_remaining",
    "supplier_approved",
    "supplier_risk",
    "supplier_sanctioned",
    "data_processing",
    "security_review",
    "urgency",
)

# Written policy constants (the "policy document"). Category-specific
# auto-approve ceilings in CHF; above the ceiling a purchase needs sign-off.
CATEGORY_THRESHOLDS = {
    "it_hardware": 10_000,
    "software_license": 15_000,
    "office_supplies": 2_000,
    "consulting": 25_000,
    "travel": 5_000,
    "marketing": 8_000,
}
RISK_HIGH = 0.70          # supplier risk at/above this escalates
FUZZ_BAND = 0.10          # +/-10% around a binding threshold is "near", escalate
RISK_FUZZ = 0.05          # +/-0.05 around RISK_HIGH is "near", escalate

URGENCY_LEVELS = ("low", "normal", "high")  # distractor factor: never load-bearing in policy
URGENCY_ALIASES = {
    "low": "low",
    "none": "low",
    "no": "low",
    "not urgent": "low",
    "no urgency": "low",
    "no rush": "low",
    "no time pressure": "low",
    "no particular time pressure": "low",
    "normal": "normal",
    "standard": "normal",
    "high": "high",
    "urgent": "high",
    "as soon as possible": "high",
}


@dataclass
class Supplier:
    """Authoritative supplier fields for one synthetic benchmark request."""
    name: str
    approved: bool
    risk: float            # 0..1
    sanctioned: bool


@dataclass
class ProcurementRequest:
    """A single procurement request with planted ground truth.

    The free text is the request-side model input. Depending on the variant,
    the wrapper also supplies the written policy, authoritative supplier fields,
    or a precomputed policy report. The remaining structured fields and
    ``planted_rule`` are benchmark truth used by the oracle and prober.
    """
    request_id: str
    kind: str                       # clean | ambiguous | out_of_schema | adversarial
    category: str
    amount: float
    budget_remaining: float
    supplier: Supplier
    data_processing: bool
    security_review: bool
    urgency: str
    free_text: str
    planted_rule: str = ""          # generator's intended firing rule (diagnostic only)
    # Kept separately from free_text so a single-factor perturbation can preserve
    # attacker-controlled text byte-for-byte while re-rendering the request.
    injection: Optional[str] = None

    def structured(self) -> dict:
        """The authoritative structured view used by the deterministic oracle."""
        return {
            "category": self.category,
            "amount": self.amount,
            "budget_remaining": self.budget_remaining,
            "supplier_approved": self.supplier.approved,
            "supplier_risk": self.supplier.risk,
            "supplier_sanctioned": self.supplier.sanctioned,
            "data_processing": self.data_processing,
            "security_review": self.security_review,
            "urgency": self.urgency,
        }

    def to_json(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class DecisionResult:
    """Output of any decider (oracle or agent variant)."""
    decision: Decision
    cited_factors: list[str] = field(default_factory=list)   # from the explanation
    unknown_citations: list[str] = field(default_factory=list)
    raw_cited_factors: list[str] = field(default_factory=list)
    explanation: str = ""
    firing_rule: str = ""             # which policy rule fired (deterministic deciders only)
    # These are the operands of the rule that returned, not a complete causal or
    # architectural driver set. ``gating_factors`` remains as a compatibility
    # alias for the existing harness and is synchronised in __post_init__.
    firing_rule_factors: list[str] = field(default_factory=list)
    gating_factors: list[str] = field(default_factory=list)
    # bookkeeping (filled by the agent wrapper)
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    parse_error: bool = False         # compatibility aggregate of the fields below
    decision_parse_error: bool = False
    explanation_parse_error: bool = False
    call_error: bool = False
    audit: dict = field(default_factory=dict)
    raw: Optional[dict] = None        # compatibility alias for audit/provenance

    def __post_init__(self):
        if self.firing_rule_factors and not self.gating_factors:
            self.gating_factors = list(self.firing_rule_factors)
        elif self.gating_factors and not self.firing_rule_factors:
            self.firing_rule_factors = list(self.gating_factors)


def normalize_category(c) -> str:
    """Canonicalise a category string to a known key when possible.

    Lowercases and joins on underscores, so an LLM that returns "software
    license" or "IT-Hardware" maps to the schema key. A genuinely novel
    category (e.g. "drone_fleet") normalises to itself and therefore stays
    unknown, which is exactly what the out-of-schema cases require.
    """
    if not isinstance(c, str):
        return ""
    return c.strip().lower().replace(" ", "_").replace("-", "_")


def normalize_urgency(value) -> str:
    """Canonicalise the finite set of phrases emitted by the request renderer."""
    if not isinstance(value, str):
        return ""
    return URGENCY_ALIASES.get(" ".join(value.strip().lower().split()), "")


def binding_threshold(category: str, budget_remaining: float) -> Optional[float]:
    """The smaller of the category ceiling and the remaining budget.

    Returns None for an out-of-schema category (no known ceiling).
    """
    ceiling = CATEGORY_THRESHOLDS.get(normalize_category(category))
    if ceiling is None:
        return None
    return min(ceiling, budget_remaining)


@dataclass
class StructuredValidationResult:
    """Validated, canonical policy input plus machine-readable diagnostics."""

    structured: Optional[dict]
    errors: list[str] = field(default_factory=list)
    normalized_fields: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.structured is not None and not self.errors


def validate_structured_record(raw) -> StructuredValidationResult:
    """Strictly validate the nine-factor record consumed by the policy core.

    No coercion or benchmark-value imputation is performed. Category and urgency
    receive transparent lexical canonicalisation; all other types are exact.
    """
    if not isinstance(raw, dict):
        return StructuredValidationResult(None, ["record:not_an_object"])

    errors: list[str] = []
    normalized: dict = {}
    keys = set(raw)
    expected = set(FACTORS)
    for key in sorted(expected - keys):
        errors.append(f"{key}:missing")
    for key in sorted(keys - expected):
        errors.append(f"{key}:unexpected")

    out: dict = {}

    category = raw.get("category")
    if not isinstance(category, str) or not category.strip():
        errors.append("category:expected_nonempty_string")
    else:
        canon = normalize_category(category)
        if not canon:
            errors.append("category:normalizes_to_empty")
        else:
            out["category"] = canon
            if canon != category:
                normalized["category"] = {"raw": category, "canonical": canon}

    for key in ("amount", "budget_remaining"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{key}:expected_number")
        elif not math.isfinite(float(value)):
            errors.append(f"{key}:expected_finite")
        elif float(value) < 0:
            errors.append(f"{key}:expected_nonnegative")
        else:
            out[key] = float(value)

    risk = raw.get("supplier_risk")
    if isinstance(risk, bool) or not isinstance(risk, (int, float)):
        errors.append("supplier_risk:expected_number")
    elif not math.isfinite(float(risk)):
        errors.append("supplier_risk:expected_finite")
    elif not 0.0 <= float(risk) <= 1.0:
        errors.append("supplier_risk:expected_0_to_1")
    else:
        out["supplier_risk"] = float(risk)

    for key in (
        "supplier_approved", "supplier_sanctioned", "data_processing",
        "security_review",
    ):
        value = raw.get(key)
        if type(value) is not bool:
            errors.append(f"{key}:expected_boolean")
        else:
            out[key] = value

    urgency = raw.get("urgency")
    if not isinstance(urgency, str):
        errors.append("urgency:expected_string")
    else:
        canon_urgency = normalize_urgency(urgency)
        if canon_urgency not in URGENCY_LEVELS:
            errors.append("urgency:unknown_value")
        else:
            out["urgency"] = canon_urgency
            if canon_urgency != urgency:
                normalized["urgency"] = {
                    "raw": urgency, "canonical": canon_urgency,
                }

    if errors:
        return StructuredValidationResult(None, errors, normalized)
    return StructuredValidationResult(
        {key: out[key] for key in FACTORS}, [], normalized
    )


def normalize_citations(raw_factors) -> tuple[list[str], list[str]]:
    """Map citations and preserve unrecognised strings for precision penalties.

    Returns ``(recognised, unknown)``. Both lists are de-duplicated in emission
    order. Non-string values are output-schema errors and are handled by the
    caller rather than converted into factor names.
    """
    if not raw_factors:
        return [], []
    alias = {
        "supplier": "supplier_approved",
        "supplier_status": "supplier_approved",
        "approved": "supplier_approved",
        "risk": "supplier_risk",
        "risk_score": "supplier_risk",
        "sanctions": "supplier_sanctioned",
        "sanction": "supplier_sanctioned",
        "budget": "budget_remaining",
        "price": "amount",
        "cost": "amount",
        "value": "amount",
        "compliance": "data_processing",
        "gdpr": "data_processing",
        "data": "data_processing",
        "security": "security_review",
        "review": "security_review",
        "threshold": "amount",
        "priority": "urgency",
    }
    out: list[str] = []
    unknown: list[str] = []
    for f in raw_factors:
        if not isinstance(f, str):
            continue
        key = f.strip().lower().replace(" ", "_").replace("-", "_")
        if key in FACTORS:
            canon = key
        elif key in alias:
            canon = alias[key]
        else:
            label = f.strip()
            if label and label not in unknown:
                unknown.append(label)
            continue
        if canon not in out:
            out.append(canon)
    return out, unknown


def normalize_cited(raw_factors) -> list[str]:
    """Compatibility wrapper returning recognised citations only."""
    return normalize_citations(raw_factors)[0]
