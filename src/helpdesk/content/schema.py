"""Content schema: the in-memory model of a compiled content record.

Three record tiers share one schema and one search pool (design doc
section 22.4).  Only the *execution* differs:

===========  ==============================  ==========================
``tier``     Shape                           How the UI runs it
===========  ==============================  ==========================
``runbook``  Branching decision tree         Node-by-node tree walker
``guide``    Linear checklist, 3-8 steps     Tick-off list
``reference``Symptom -> causes -> fix        A single card
===========  ==============================  ==========================

Enumerated values are canonical in English so that a contributor who does
not read Turkish can still review a pull request, but every Turkish
spelling from the original design document is accepted on input and
folded to its English equivalent.  Prose -- titles, bodies, edge labels --
stays in whatever language the file is written in.

The schema is plain dataclasses rather than pydantic on purpose: the
compiler, the matcher and the CLI then depend on nothing but the standard
library plus PyYAML, which keeps ``pipx install helpdesk-guide`` fast and
makes a single-file PyInstaller build straightforward.  The validation
this file would have bought is implemented in ``validator.py`` anyway,
because the expensive checks are graph-shaped, not field-shaped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "TERMINAL_NODE_TYPES",
    "TIER_PRIORITY",
    "VERIFICATION_WEIGHT",
    "Alias",
    "Edge",
    "Node",
    "NodeType",
    "Record",
    "Risk",
    "Severity",
    "Status",
    "Step",
    "Tier",
    "Verification",
    "canonical_enum",
]


# --------------------------------------------------------------------------
# Enumerations, with Turkish input aliases
# --------------------------------------------------------------------------

Tier = str          # runbook | guide | reference
NodeType = str      # question | instruction | measurement | decision | resolution | escalation
Risk = str          # low | medium | high
Severity = str      # low | medium | high | critical
Status = str        # draft | published | archived
Verification = str  # verified | reviewed | draft | generated

#: Every accepted spelling -> the canonical English value.  Turkish keys
#: come straight from the design document so its YAML examples compile
#: unchanged.
_ENUM_ALIASES: dict[str, dict[str, str]] = {
    "tier": {
        "runbook": "runbook", "guide": "guide", "reference": "reference",
        "rehber": "guide", "referans": "reference", "kilavuz": "guide",
        "t1": "runbook", "t2": "guide", "t3": "reference",
    },
    "node_type": {
        "question": "question", "instruction": "instruction",
        "measurement": "measurement", "decision": "decision",
        "resolution": "resolution", "escalation": "escalation",
        "soru": "question", "talimat": "instruction", "olcum": "measurement",
        "ölçüm": "measurement", "karar": "decision", "cozum": "resolution",
        "çözüm": "resolution", "eskalasyon": "escalation",
    },
    "risk": {
        "low": "low", "medium": "medium", "high": "high",
        "dusuk": "low", "düşük": "low", "orta": "medium",
        "yuksek": "high", "yüksek": "high",
    },
    "severity": {
        "low": "low", "medium": "medium", "high": "high", "critical": "critical",
        "dusuk": "low", "düşük": "low", "orta": "medium",
        "yuksek": "high", "yüksek": "high", "kritik": "critical",
    },
    "status": {
        "draft": "draft", "published": "published", "archived": "archived",
        "taslak": "draft", "yayinda": "published", "yayında": "published",
        "arsiv": "archived", "arşiv": "archived",
    },
    "verification": {
        "verified": "verified", "reviewed": "reviewed",
        "draft": "draft", "generated": "generated",
        "dogrulanmis": "verified", "doğrulanmış": "verified",
        "incelendi": "reviewed", "taslak": "draft", "otomatik": "generated",
    },
    "alias_kind": {
        "symptom": "symptom", "error_code": "error_code",
        "product": "product", "abbreviation": "abbreviation",
        "belirti": "symptom", "hata_kodu": "error_code",
        "urun": "product", "ürün": "product",
        "kisaltma": "abbreviation", "kısaltma": "abbreviation",
    },
}


def canonical_enum(field_name: str, value: Any, default: str | None = None) -> str:
    """Fold an enum value onto its canonical English spelling.

    Raises ``ValueError`` on an unknown value so a typo in a runbook fails
    the build instead of silently becoming a node type nobody handles.
    """
    if value is None or value == "":
        if default is None:
            raise ValueError(f"{field_name}: value is required")
        return default
    table = _ENUM_ALIASES[field_name]
    key = str(value).strip().lower()
    if key not in table:
        allowed = ", ".join(sorted(set(table.values())))
        raise ValueError(f"{field_name}: unknown value {value!r} (allowed: {allowed})")
    return table[key]


#: Confidence multiplier applied to the match score (design doc section
#: 22.5).  Unverified content must never outrank content a human has
#: actually stood behind.
VERIFICATION_WEIGHT: dict[str, float] = {
    "verified": 1.00,
    "reviewed": 0.90,
    "draft": 0.70,
    "generated": 0.50,
}

#: Tie-breaker when scores are equal: a decision tree guides a technician
#: further than a reference card does.
TIER_PRIORITY: dict[str, float] = {"runbook": 0.03, "guide": 0.015, "reference": 0.0}

#: Node types that end a walk and therefore must not have outgoing edges.
TERMINAL_NODE_TYPES: frozenset[str] = frozenset({"resolution", "escalation"})


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

@dataclass
class Alias:
    """One way a user might phrase the symptom this record solves."""

    term: str
    kind: str = "symptom"
    weight: float = 1.0
    term_norm: str = ""
    term_key: str = ""


@dataclass
class Edge:
    """A labelled transition out of a node."""

    label: str
    target: str
    order_idx: int = 0
    condition: str | None = None


@dataclass
class Node:
    """A single step of a decision tree."""

    key: str
    type: str
    title: str
    body_md: str | None = None
    verify_text: str | None = None
    risk: str = "low"
    requires_admin: bool = False
    requires_user_downtime: bool = False
    est_seconds: int | None = None
    rollback_md: str | None = None
    media: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    # resolution nodes
    root_cause: str | None = None
    closure_note: str | None = None
    followup_md: str | None = None
    part_required: str | None = None
    # escalation nodes
    escalate_to: str | None = None
    summary_template: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.type in TERMINAL_NODE_TYPES


@dataclass
class Step:
    """One item of a ``guide`` (tier T2) checklist."""

    title: str
    note: str | None = None
    body_md: str | None = None
    risk: str = "low"
    requires_admin: bool = False
    est_seconds: int | None = None
    order_idx: int = 0


@dataclass
class Record:
    """A compiled content record of any tier."""

    code: str
    title: str
    tier: str = "runbook"
    lang: str = "tr"
    summary: str | None = None
    category: str = "genel"
    severity: str = "medium"
    status: str = "draft"
    verification: str = "draft"
    author: str | None = None
    reviewed_at: str | None = None
    review_period_days: int | None = None
    review_due: str | None = None
    os_scope: list[str] = field(default_factory=list)
    asset_scope: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    aliases: list[Alias] = field(default_factory=list)
    # tier == runbook
    entry: str | None = None
    nodes: list[Node] = field(default_factory=list)
    disambiguation: list[dict[str, Any]] = field(default_factory=list)
    # tier == guide
    steps: list[Step] = field(default_factory=list)
    # tier == reference
    likely_causes: list[str] = field(default_factory=list)
    fix_summary: str | None = None
    source: dict[str, Any] | None = None
    # cross-links
    related_runbook: str | None = None
    related: list[str] = field(default_factory=list)
    merged_into: str | None = None
    # provenance
    source_path: str = ""
    content_hash: str = ""
    min_engine_version: str | None = None

    # -- derived ---------------------------------------------------------

    @property
    def node_map(self) -> dict[str, Node]:
        return {node.key: node for node in self.nodes}

    @property
    def verification_weight(self) -> float:
        return VERIFICATION_WEIGHT.get(self.verification, 0.5)

    def searchable_body(self) -> str:
        """Flatten every piece of prose the FTS index should see."""
        chunks: list[str] = [self.title, self.summary or ""]
        chunks.extend(self.tags)
        for node in self.nodes:
            chunks.extend([node.title, node.body_md or "", node.verify_text or ""])
            chunks.extend(edge.label for edge in node.edges)
            if node.root_cause:
                chunks.append(node.root_cause)
        for step in self.steps:
            chunks.extend([step.title, step.note or "", step.body_md or ""])
        chunks.extend(self.likely_causes)
        if self.fix_summary:
            chunks.append(self.fix_summary)
        return "\n".join(chunk for chunk in chunks if chunk)
