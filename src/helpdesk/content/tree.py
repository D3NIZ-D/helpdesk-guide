"""Render a runbook's decision tree as ASCII (design doc section 19.4).

Reviewing a decision tree by reading its YAML is miserable -- the branches
are edges scattered across two hundred lines, and a reviewer who cannot
see the shape will not catch the dead end.  CI posts this diagram as a
pull-request comment so the logic can be checked without reading the file
at all::

    N10 Is the monitor's power LED on?
     ├─ No, never lights up ──► N20 Check the power chain
     │                           ├─ LED came on ──► [OK] N21 POWER_CONNECTION
     │                           └─ Still nothing ──► N30 Swap-test the cable
     ├─ Amber / blinking ──► N40 Does it say "No Signal"?
     └─ Steady white ──► N60 Does the OSD open?
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .schema import Record

__all__ = ["render_record_tree", "render_tree"]

_TERMINAL_MARK = {"resolution": "[OK]", "escalation": "[ESC]"}
_MAX_DEPTH = 12


def _label_for(node: Mapping[str, Any]) -> str:
    kind = str(node.get("type", ""))
    mark = _TERMINAL_MARK.get(kind)
    title = str(node.get("title", "")).strip()
    if kind == "resolution" and node.get("root_cause"):
        title = f"{title}  <{node['root_cause']}>"
    if kind == "escalation" and node.get("escalate_to"):
        title = f"{title}  <{node['escalate_to']}>"
    return f"{mark} {title}" if mark else title


def render_tree(
    nodes: Mapping[str, Mapping[str, Any]],
    entry: str,
    *,
    width: int = 96,
) -> str:
    """Render the tree rooted at ``entry``.

    ``nodes`` maps node key -> node mapping with ``type``, ``title`` and an
    ``edges`` list of ``{label, target_key}``.  Repeated subtrees are shown
    once and referenced afterwards, which keeps the diagram readable when
    several branches converge on the same recovery step.
    """
    if entry not in nodes:
        return f"(entry node {entry!r} not found)"

    lines: list[str] = []
    expanded: set[str] = set()

    def truncate(text: str, budget: int) -> str:
        budget = max(24, budget)
        return text if len(text) <= budget else text[: budget - 1].rstrip() + "…"

    def walk(key: str, prefix: str, is_last: bool, depth: int, edge_label: str | None) -> None:
        node = nodes.get(key)
        if node is None:
            lines.append(f"{prefix}{'└─' if is_last else '├─'} (missing node {key})")
            return

        connector = "" if edge_label is None else ("└─ " if is_last else "├─ ")
        label_part = f"[{edge_label}] " if edge_label else ""
        head = f"{prefix}{connector}{label_part}{key} "
        lines.append(truncate(head + _label_for(node), width))

        if key in expanded:
            lines.append(f"{prefix}{'   ' if is_last else '│  '}   ↩ (already shown above)")
            return
        expanded.add(key)

        edges: Sequence[Mapping[str, Any]] = node.get("edges") or []
        if not edges or depth >= _MAX_DEPTH:
            return

        child_prefix = prefix + ("   " if (is_last or edge_label is None) else "│  ")
        for index, edge in enumerate(edges):
            walk(
                str(edge.get("target_key") or edge.get("target") or ""),
                child_prefix,
                index == len(edges) - 1,
                depth + 1,
                str(edge.get("label", "")),
            )

    walk(entry, "", True, 0, None)

    # Nodes that exist but never appear are a validation error; surfacing
    # them here too means a reviewer sees the problem in the diagram.
    orphans = sorted(set(nodes) - expanded)
    if orphans:
        lines.append("")
        lines.append("UNREACHABLE: " + ", ".join(orphans))
    return "\n".join(lines)


def render_record_tree(record: Record, *, width: int = 96) -> str:
    """Render a :class:`Record` loaded from YAML (pre-compilation)."""
    if record.tier != "runbook":
        if record.tier == "guide":
            return "\n".join(
                f"{index + 1}. {step.title}" + (f"\n     {step.note}" if step.note else "")
                for index, step in enumerate(record.steps)
            )
        causes = "\n".join(f"  - {cause}" for cause in record.likely_causes)
        return f"{record.title}\nLikely causes:\n{causes}\n\n{record.fix_summary or ''}".strip()

    nodes = {
        node.key: {
            "type": node.type,
            "title": node.title,
            "root_cause": node.root_cause,
            "escalate_to": node.escalate_to,
            "edges": [{"label": edge.label, "target_key": edge.target} for edge in node.edges],
        }
        for node in record.nodes
    }
    return render_tree(nodes, record.entry or "", width=width)
