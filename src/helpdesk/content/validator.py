"""Content validation: the quality gates that run at compile time.

Implements the rules from design doc sections 7.2, 12.2, 20.3, 22.5 and
22.8.  Everything here is a *build* concern -- a runbook that fails these
checks never reaches the database, so the runtime engine can assume the
graph it walks is well formed.

Findings come in three levels:

``error``    the build stops
``warning``  the build continues, CI prints it
``info``     reported by ``helpdesk lint`` only

The distinction matters for an open-source project: a contributor's first
pull request should fail loudly on a dead end (which would strand a
technician mid-call) but merely nag about a missing ``review_period_days``.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..core.normalize import normalize_term
from .schema import Record
from .secrets import scan_text

__all__ = ["Finding", "ValidationReport", "validate_record", "validate_records"]

#: Design doc section 20.3: a technician on the phone loses patience past
#: seven steps, so the longest root-to-resolution path is capped.
MAX_PATH_LENGTH = 7
#: Section 20.3: enough phrasings that Turkish morphology stops mattering.
MIN_ALIASES = 3
#: Section 22.8: near-duplicate titles confuse search more than they help.
TITLE_SIMILARITY_WARN = 0.85


@dataclass(frozen=True)
class Finding:
    level: str          # error | warning | info
    code: str           # machine-readable rule id
    record: str         # record code, or "" for repository-wide findings
    message: str
    where: str = ""     # node key, alias, file path...

    def __str__(self) -> str:
        location = f" [{self.where}]" if self.where else ""
        prefix = f"{self.record}: " if self.record else ""
        return f"{self.level.upper():7} {self.code:24} {prefix}{self.message}{location}"


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, code: str, record: str, message: str, where: str = "") -> None:
        self.findings.append(Finding(level, code, record, message, where))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def infos(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "info"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: ValidationReport) -> None:
        self.findings.extend(other.findings)


# --------------------------------------------------------------------------
# Graph analysis
# --------------------------------------------------------------------------

def _reachable(record: Record) -> set[str]:
    """Every node key reachable from the entry node."""
    nodes = record.node_map
    if not record.entry or record.entry not in nodes:
        return set()
    seen: set[str] = set()
    stack = [record.entry]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        node = nodes.get(key)
        if not node:
            continue
        for edge in node.edges:
            if edge.target in nodes:
                stack.append(edge.target)
    return seen


def _longest_path(record: Record) -> tuple[int, list[str]]:
    """Longest simple path from entry to any terminal node.

    Cycles are ignored here (they are reported separately); the walk never
    revisits a node on the current path, so it always terminates.
    """
    nodes = record.node_map
    if not record.entry or record.entry not in nodes:
        return 0, []

    best_len = 0
    best_path: list[str] = []

    def walk(key: str, path: list[str]) -> None:
        nonlocal best_len, best_path
        node = nodes.get(key)
        if node is None:
            return
        path = [*path, key]
        if node.is_terminal or not node.edges:
            if len(path) > best_len:
                best_len, best_path = len(path), path
            return
        for edge in node.edges:
            if edge.target in path:       # cycle guard
                continue
            walk(edge.target, path)

    walk(record.entry, [])
    return best_len, best_path


def _find_cycles(record: Record) -> list[list[str]]:
    """Unconditional back-edges that can loop a technician forever.

    An edge carrying a ``condition`` is allowed to point backwards -- that
    is how "try again with the other cable" is expressed -- so only
    unconditional loops are reported.
    """
    nodes = record.node_map
    cycles: list[list[str]] = []
    state: dict[str, int] = defaultdict(int)   # 0 unseen, 1 on stack, 2 done

    def walk(key: str, path: list[str]) -> None:
        if key not in nodes:
            return
        if state[key] == 1:
            start = path.index(key)
            cycles.append([*path[start:], key])
            return
        if state[key] == 2:
            return
        state[key] = 1
        for edge in nodes[key].edges:
            if edge.condition:
                continue
            walk(edge.target, [*path, key])
        state[key] = 2

    if record.entry:
        walk(record.entry, [])
    return cycles


# --------------------------------------------------------------------------
# Per-record validation
# --------------------------------------------------------------------------

_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}(?:-[A-Z0-9]{1,12}){1,3}$")


def validate_record(record: Record, *, strict_secrets: bool = True) -> ValidationReport:
    report = ValidationReport()
    add = lambda level, code, msg, where="": report.add(level, code, record.code, msg, where)

    # -- identity ------------------------------------------------------
    if not _CODE_RE.match(record.code):
        add("error", "code.format",
            f"code {record.code!r} must look like 'DSP-001' or 'ERR-WIN-0X7B'")

    # -- aliases -------------------------------------------------------
    if len(record.aliases) < MIN_ALIASES and record.status == "published":
        add("warning", "alias.too_few",
            f"only {len(record.aliases)} aliases; at least {MIN_ALIASES} phrasings "
            "are needed before search behaves well")

    seen_norm: dict[str, str] = {}
    for alias in record.aliases:
        if not alias.term_norm:
            add("error", "alias.empty", f"alias {alias.term!r} normalises to nothing")
            continue
        if alias.term_norm in seen_norm:
            add("warning", "alias.duplicate",
                f"alias {alias.term!r} duplicates {seen_norm[alias.term_norm]!r} after normalisation")
        seen_norm[alias.term_norm] = alias.term

    # -- secrets (hard gate, section 12.2) -----------------------------
    blobs: list[tuple[str, str]] = [("title", record.title), ("summary", record.summary or "")]
    for node in record.nodes:
        blobs.append((f"node {node.key}", "\n".join(
            filter(None, [node.title, node.body_md, node.verify_text, node.rollback_md])
        )))
    for step in record.steps:
        blobs.append((f"step {step.order_idx + 1}", "\n".join(
            filter(None, [step.title, step.note, step.body_md])
        )))
    if record.fix_summary:
        blobs.append(("fix_summary", record.fix_summary))
    for label, text in blobs:
        for hit in scan_text(text, strict=strict_secrets):
            add("error", f"secret.{hit.rule}", f"possible secret -- {hit.hint}", f"{label}, {hit}")

    # -- tier-specific -------------------------------------------------
    if record.tier == "runbook":
        report.extend(_validate_runbook(record))
    elif record.tier == "guide":
        report.extend(_validate_guide(record))
    else:
        report.extend(_validate_reference(record))

    # -- freshness (section 22.10) -------------------------------------
    if record.review_due:
        try:
            due = dt.date.fromisoformat(record.review_due)
            if due < dt.date.today() and record.status == "published":
                add("warning", "content.stale",
                    f"review was due {record.review_due}; content may be out of date")
        except ValueError:
            add("error", "content.review_due_format",
                f"review_due {record.review_due!r} is not an ISO date")
    elif record.tier in {"runbook", "guide"} and record.status == "published":
        add("info", "content.no_review_period",
            "no review_period_days set; this record will never be flagged as stale")

    # -- verification ladder (section 22.5) ----------------------------
    if record.status == "published" and record.verification == "generated":
        add("error", "verification.unreviewed_published",
            "a 'generated' record cannot be published; a human must review it first")

    return report


def _validate_runbook(record: Record) -> ValidationReport:
    report = ValidationReport()
    add = lambda level, code, msg, where="": report.add(level, code, record.code, msg, where)

    if not record.nodes:
        add("error", "tree.empty", "a runbook must define at least one node")
        return report
    if not record.entry:
        add("error", "tree.no_entry", "missing 'entry' node key")
        return report

    nodes = record.node_map
    if record.entry not in nodes:
        add("error", "tree.entry_missing", f"entry node {record.entry!r} is not defined")
        return report

    # dangling targets
    for node in record.nodes:
        for edge in node.edges:
            if edge.target not in nodes:
                add("error", "edge.dangling",
                    f"edge {edge.label!r} points at undefined node {edge.target!r}", node.key)

    # unreachable nodes
    reachable = _reachable(record)
    for node in record.nodes:
        if node.key not in reachable:
            add("error", "node.unreachable",
                "node cannot be reached from the entry node", node.key)

    # dead ends and terminal shape
    for node in record.nodes:
        if node.is_terminal:
            if node.edges:
                add("error", "node.terminal_with_edges",
                    f"a {node.type} node ends the walk and must not have edges", node.key)
            if node.type == "resolution" and not node.root_cause:
                add("error", "resolution.no_root_cause",
                    "resolution nodes must record a root_cause; reporting depends on it", node.key)
        else:
            if not node.edges:
                add("error", "node.dead_end",
                    f"non-terminal {node.type} node has no outgoing edge -- the "
                    "technician would be stranded here", node.key)
            elif node.type == "question" and len(node.edges) < 2:
                add("warning", "question.single_edge",
                    "a question with one answer is an instruction, not a question", node.key)
            if node.type == "instruction" and not node.verify_text:
                add("warning", "instruction.no_verify",
                    "instruction nodes need verify_text -- 'I did it' is not evidence "
                    "that it worked", node.key)

        # risk handling
        if node.risk == "high" and not node.rollback_md:
            add("error", "risk.no_rollback",
                "a high-risk step must document how to undo it", node.key)
        if node.risk == "high" and record.verification == "generated":
            add("error", "risk.unverified_high",
                "a high-risk step may not come from unreviewed content", node.key)

    # every branch must terminate
    if not any(node.type == "escalation" for node in record.nodes):
        add("warning", "tree.no_escalation",
            "no escalation node; not every fault is fixable on the spot")
    if not any(node.type == "resolution" for node in record.nodes):
        add("error", "tree.no_resolution", "no resolution node; the tree can never succeed")

    # cycles
    for cycle in _find_cycles(record):
        add("error", "tree.cycle",
            "unconditional loop: " + " -> ".join(cycle),
            cycle[0])

    # depth
    depth, path = _longest_path(record)
    if depth > MAX_PATH_LENGTH:
        add("warning", "tree.too_deep",
            f"longest path is {depth} steps (target is {MAX_PATH_LENGTH}): "
            + " -> ".join(path))

    return report


def _validate_guide(record: Record) -> ValidationReport:
    report = ValidationReport()
    add = lambda level, code, msg, where="": report.add(level, code, record.code, msg, where)
    if not record.steps:
        add("error", "guide.empty", "a guide must define at least one step")
    if len(record.steps) > 8:
        add("warning", "guide.too_long",
            f"{len(record.steps)} steps; past eight, this wants to be a runbook "
            "with real branching")
    if record.nodes:
        add("error", "guide.has_nodes",
            "a guide uses 'steps', not 'nodes'; set tier: runbook if it branches")
    for step in record.steps:
        if step.risk == "high" and record.verification == "generated":
            add("error", "risk.unverified_high",
                "a high-risk step may not come from unreviewed content",
                f"step {step.order_idx + 1}")
    return report


def _validate_reference(record: Record) -> ValidationReport:
    report = ValidationReport()
    add = lambda level, code, msg, where="": report.add(level, code, record.code, msg, where)
    if not record.fix_summary and not record.likely_causes:
        add("error", "reference.empty",
            "a reference card needs likely_causes or a fix_summary")
    if record.nodes or record.steps:
        add("error", "reference.has_procedure",
            "a reference card is a single card; use tier guide or runbook for procedures")
    # Section 22.6: cite, do not copy.
    if record.source and not record.source.get("url"):
        add("warning", "reference.source_no_url",
            "source is named but has no url; a citation without a link ages badly")
    if not record.related_runbook and not record.related:
        add("info", "reference.orphan",
            "not linked to any runbook; isolated cards make the knowledge base "
            "a pile instead of a network (section 22.4)")
    return report


# --------------------------------------------------------------------------
# Repository-wide validation
# --------------------------------------------------------------------------

def _trigram_similarity(left: str, right: str) -> float:
    """Jaccard similarity over character trigrams."""
    def grams(text: str) -> set[str]:
        padded = f"  {text}  "
        return {padded[i:i + 3] for i in range(len(padded) - 2)}

    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def validate_records(
    records: Sequence[Record], *, strict_secrets: bool = True
) -> ValidationReport:
    """Validate every record plus the cross-record invariants."""
    report = ValidationReport()

    for record in records:
        report.extend(validate_record(record, strict_secrets=strict_secrets))

    by_code: dict[str, Record] = {}
    for record in records:
        if record.code in by_code:
            report.add("error", "code.duplicate", record.code,
                       f"code is defined twice: {by_code[record.code].source_path} "
                       f"and {record.source_path}")
        by_code[record.code] = record

    # An alias that exactly matches in two records makes the top hit a coin
    # flip (section 7.2 / 22.8).
    alias_owner: dict[str, tuple[str, str]] = {}
    for record in records:
        for alias in record.aliases:
            key = alias.term_norm
            if not key:
                continue
            if key in alias_owner and alias_owner[key][0] != record.code:
                other_code, other_term = alias_owner[key]
                report.add("error", "alias.collision", record.code,
                           f"alias {alias.term!r} also resolves {other_code} "
                           f"(as {other_term!r}); search cannot choose between them",
                           alias.term)
            else:
                alias_owner[key] = (record.code, alias.term)

    # Near-duplicate titles within a category (section 22.8).
    by_category: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        by_category[record.category].append(record)
    for group in by_category.values():
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                score = _trigram_similarity(
                    normalize_term(left.title), normalize_term(right.title)
                )
                if score >= TITLE_SIMILARITY_WARN:
                    report.add("warning", "title.near_duplicate", left.code,
                               f"title is {score:.0%} similar to {right.code} "
                               f"({right.title!r}); consider merging")

    # Three or more records sharing one root cause in one category.
    root_cause_owners: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record in records:
        for node in record.nodes:
            if node.root_cause:
                root_cause_owners[(record.category, node.root_cause)].append(record.code)
    for (category, cause), owners in root_cause_owners.items():
        unique = sorted(set(owners))
        if len(unique) > 3:
            report.add("info", "root_cause.spread", "",
                       f"root cause {cause} appears in {len(unique)} records under "
                       f"{category} ({', '.join(unique)}); they may want merging")

    # Cross-links must resolve.
    for record in records:
        for target in filter(None, [record.related_runbook, record.merged_into, *record.related]):
            if target not in by_code:
                report.add("warning", "link.dangling", record.code,
                           f"links to {target}, which does not exist")

    return report
