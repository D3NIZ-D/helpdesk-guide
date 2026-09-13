"""The decision-tree executor (design doc section 9).

Execution is deterministic by design: the same answers always produce the
same path.  That is what makes a session record meaningful evidence
("these six steps were taken, in this order, and the fault survived all
of them") and what lets an escalation summary be trusted by whoever picks
the ticket up.

The engine is pure with respect to persistence: it computes the next
state from the current state plus an answer, and :mod:`session` is what
writes that to SQLite.  Keeping the two apart means the tree can be
exercised in tests without a database and replayed from a stored path.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Engine", "EngineError", "NodeView", "WalkState"]

#: Section 9.3: revisiting one node repeatedly means the content has a
#: loop the validator did not catch, or the technician is going in
#: circles.  Either way, say so rather than letting it continue.
MAX_VISITS_PER_NODE = 2


class EngineError(RuntimeError):
    """The requested transition is not valid for the current state."""


@dataclass
class WalkState:
    """Everything needed to resume a walk, and nothing else."""

    code: str
    version: int
    current: str
    path: list[str] = field(default_factory=list)
    answers: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    session_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "record": self.code,
            "version": self.version,
            "current_node": self.current,
            "path": list(self.path),
            "answers": dict(self.answers),
            "skipped": list(self.skipped),
            "context": dict(self.context),
            "started_at": self.started_at,
            "elapsed_s": self.elapsed_s,
        }

    @property
    def elapsed_s(self) -> int:
        try:
            started = dt.datetime.fromisoformat(self.started_at)
        except ValueError:
            return 0
        return max(0, int((dt.datetime.now() - started).total_seconds()))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WalkState:
        return cls(
            code=str(data["record"]),
            version=int(data.get("version", 1)),
            current=str(data["current_node"]),
            path=[str(p) for p in data.get("path", [])],
            answers={str(k): str(v) for k, v in (data.get("answers") or {}).items()},
            skipped=[str(s) for s in data.get("skipped", [])],
            context=dict(data.get("context") or {}),
            started_at=str(data.get("started_at") or dt.datetime.now().isoformat(timespec="seconds")),
            session_id=data.get("session_id"),
        )


@dataclass
class NodeView:
    """A node rendered for presentation, with everything the UI needs."""

    key: str
    type: str
    title: str
    body_md: str | None
    verify_text: str | None
    risk: str
    requires_admin: bool
    requires_user_downtime: bool
    est_seconds: int | None
    rollback_md: str | None
    media: list[str]
    options: list[dict[str, Any]]
    is_terminal: bool
    root_cause: str | None = None
    closure_note: str | None = None
    followup_md: str | None = None
    part_required: str | None = None
    escalate_to: str | None = None
    # progress
    step_number: int = 1
    est_remaining_steps: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "title": self.title,
            "body_md": self.body_md,
            "verify_text": self.verify_text,
            "risk": self.risk,
            "requires_admin": self.requires_admin,
            "requires_user_downtime": self.requires_user_downtime,
            "est_seconds": self.est_seconds,
            "rollback_md": self.rollback_md,
            "media": self.media,
            "options": self.options,
            "is_terminal": self.is_terminal,
            "root_cause": self.root_cause,
            "closure_note": self.closure_note,
            "followup_md": self.followup_md,
            "part_required": self.part_required,
            "escalate_to": self.escalate_to,
            "step_number": self.step_number,
            "est_remaining_steps": self.est_remaining_steps,
            "warnings": self.warnings,
        }


class Engine:
    """Walks one compiled record."""

    def __init__(self, record: Mapping[str, Any]) -> None:
        if record.get("tier") != "runbook":
            raise EngineError(
                f"{record.get('code')} is a {record.get('tier')}, not a runbook; "
                "guides and reference cards are rendered, not walked"
            )
        self.record = record
        self.nodes: dict[str, dict[str, Any]] = record.get("nodes") or {}
        if not self.nodes:
            raise EngineError(f"{record.get('code')} has no nodes")

    # -- lifecycle ------------------------------------------------------

    def start(self, context: Mapping[str, Any] | None = None) -> WalkState:
        entry = self.record.get("entry_node")
        if not entry or entry not in self.nodes:
            raise EngineError(f"{self.record.get('code')}: entry node {entry!r} is missing")
        state = WalkState(
            code=str(self.record["code"]),
            # Pinning the version freezes the tree for the duration of the
            # call: recompiling content mid-session must not reroute a
            # technician who is already three steps in (section 9.3).
            version=int(self.record.get("version", 1)),
            current=entry,
            path=[entry],
            context=dict(context or {}),
        )
        return self._auto_advance(state)

    # -- transitions ----------------------------------------------------

    def answer(self, state: WalkState, label: str) -> WalkState:
        """Take the edge whose label matches ``label``."""
        node = self._node(state.current)
        if node["type"] in {"resolution", "escalation"}:
            raise EngineError(f"node {state.current} is terminal; there is nothing to answer")

        edges = node.get("edges") or []
        chosen = next((e for e in edges if e["label"] == label), None)
        if chosen is None:
            # Accept a 1-based index too, so the CLI and the keyboard
            # shortcuts in the web UI can send "2" instead of the label.
            if str(label).isdigit() and 1 <= int(label) <= len(edges):
                chosen = edges[int(label) - 1]
            else:
                available = ", ".join(repr(e["label"]) for e in edges)
                raise EngineError(f"no edge {label!r} on node {state.current} (have: {available})")

        state.answers[state.current] = chosen["label"]
        state.current = chosen["target_key"]
        state.path.append(state.current)
        return self._auto_advance(state)

    def skip(self, state: WalkState, label: str) -> WalkState:
        """Mark the current step as already tried and follow an edge anyway.

        Skips are tracked separately from answers because they change what
        an escalation summary means: "we did not check the cable" and "we
        checked the cable and it was fine" are very different handovers
        (section 9.3).
        """
        node = self._node(state.current)
        if node["type"] in {"resolution", "escalation"}:
            raise EngineError(f"node {state.current} is terminal; there is nothing to skip")
        state.skipped.append(state.current)
        return self.answer(state, label)

    def back(self, state: WalkState) -> WalkState:
        """Rewind one step, invalidating every answer after it."""
        if len(state.path) <= 1:
            return state
        state.path.pop()
        # Silently dropping answers that are no longer on the path keeps
        # the session record honest -- a rewound branch was not taken.
        state.current = state.path[-1]
        on_path = set(state.path)
        # The node we land back on has not been answered *or* skipped yet,
        # so both marks are dropped for it. Leaving the skip behind would
        # make the escalation summary claim a step was passed over when
        # the technician is about to perform it.
        state.answers = {
            k: v for k, v in state.answers.items() if k in on_path and k != state.current
        }
        state.skipped = [k for k in state.skipped if k in on_path and k != state.current]
        return state

    def goto(self, state: WalkState, node_key: str) -> WalkState:
        """Jump to a node directly (used when resuming a stored session)."""
        self._node(node_key)
        state.current = node_key
        if not state.path or state.path[-1] != node_key:
            state.path.append(node_key)
        return state

    # -- presentation ---------------------------------------------------

    def view(self, state: WalkState) -> NodeView:
        node = self._node(state.current)
        warnings: list[str] = []

        visits = state.path.count(state.current)
        if visits > MAX_VISITS_PER_NODE:
            warnings.append("loop_detected")
        if node["risk"] == "high":
            warnings.append("high_risk")
        if node.get("requires_admin"):
            warnings.append("requires_admin")
        if node.get("requires_user_downtime"):
            warnings.append("user_downtime")

        options = [
            {"label": edge["label"], "target": edge["target_key"], "index": index + 1}
            for index, edge in enumerate(node.get("edges") or [])
            if self._condition_holds(edge.get("condition"), state.context)
        ]

        return NodeView(
            key=node["key"],
            type=node["type"],
            title=node["title"],
            body_md=node.get("body_md"),
            verify_text=node.get("verify_text"),
            risk=node.get("risk") or "low",
            requires_admin=bool(node.get("requires_admin")),
            requires_user_downtime=bool(node.get("requires_user_downtime")),
            est_seconds=node.get("est_seconds"),
            rollback_md=node.get("rollback_md"),
            media=node.get("media") or [],
            options=options,
            is_terminal=node["type"] in {"resolution", "escalation"},
            root_cause=node.get("root_cause"),
            closure_note=node.get("closure_note"),
            followup_md=node.get("followup_md"),
            part_required=node.get("part_required"),
            escalate_to=node.get("escalate_to"),
            step_number=len(state.path),
            est_remaining_steps=self._remaining_depth(state.current),
            warnings=warnings,
        )

    def steps_taken(self, state: WalkState) -> list[dict[str, Any]]:
        """The walk so far, as the escalation summary wants to read it."""
        out: list[dict[str, Any]] = []
        for index, key in enumerate(state.path):
            node = self.nodes.get(key)
            if not node:
                continue
            out.append(
                {
                    "order": index + 1,
                    "key": key,
                    "type": node["type"],
                    "title": node["title"],
                    "answer": state.answers.get(key),
                    "skipped": key in state.skipped,
                }
            )
        return out

    # -- internals ------------------------------------------------------

    def _node(self, key: str) -> dict[str, Any]:
        node = self.nodes.get(key)
        if node is None:
            raise EngineError(f"node {key!r} does not exist in {self.record.get('code')}")
        return node

    def _auto_advance(self, state: WalkState) -> WalkState:
        """Pass silently through ``decision`` nodes.

        A decision node branches on context the system already knows (the
        asset's OS, whether a dock is present), so showing it to the
        technician would be asking a question we can answer ourselves.
        """
        for _ in range(16):
            node = self.nodes.get(state.current)
            if node is None or node["type"] != "decision":
                break
            edges = node.get("edges") or []
            target = None
            for edge in edges:
                if self._condition_holds(edge.get("condition"), state.context):
                    target = edge["target_key"]
                    break
            if target is None:
                if not edges:
                    raise EngineError(f"decision node {state.current} has no edges")
                # No condition matched: fall through the last edge, which
                # is the documented "otherwise" branch.
                target = edges[-1]["target_key"]
            state.answers[state.current] = "(auto)"
            state.current = target
            state.path.append(target)
        return state

    @staticmethod
    def _condition_holds(condition: str | None, context: Mapping[str, Any]) -> bool:
        """Evaluate an edge condition against the session context.

        Conditions are a tiny hand-parsed language -- ``os == "windows"``,
        ``dock != true`` -- and never Python.  Content is data; letting it
        reach ``eval`` would turn every runbook into arbitrary code
        execution (section 12.1).
        """
        if not condition:
            return True
        text = condition.strip()
        for operator in ("!=", "=="):
            if operator not in text:
                continue
            left, right = (part.strip() for part in text.split(operator, 1))
            right = right.strip("\"'").lower()
            actual = context.get(left)
            actual_str = (
                "true" if actual is True else
                "false" if actual is False else
                "" if actual is None else str(actual).lower()
            )
            if operator == "==":
                return actual_str == right
            return actual_str != right
        # An unparseable condition must not silently hide a branch.
        return True

    def _remaining_depth(self, key: str) -> int:
        """Longest remaining distance to a terminal node, for the progress bar."""
        seen: set[str] = set()

        def walk(node_key: str, depth: int) -> int:
            if node_key in seen or depth > 24:
                return 0
            node = self.nodes.get(node_key)
            if node is None:
                return 0
            if node["type"] in {"resolution", "escalation"} or not node.get("edges"):
                return 0
            seen.add(node_key)
            best = max(
                (walk(edge["target_key"], depth + 1) for edge in node["edges"]),
                default=0,
            )
            seen.discard(node_key)
            return best + 1

        return walk(key, 0)
