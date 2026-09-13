"""Session recording (design doc sections 9.2, 11 and 12.3).

Every walk is written to the local database as it happens, because the
telemetry is what turns the tool from a static document viewer into
something that improves: dead-end nodes, always-skipped steps and queries
that found nothing are all read back out of these two tables.

Privacy is a design constraint, not a footnote (section 12.3).  End-user
names and e-mail addresses are never stored; free-text queries are masked
before they are written, because "ahmet yılmaz outlook açamıyor" is
personal data and a knowledge gap report does not need the name to be
useful.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from typing import Any

from .engine import Engine, WalkState
from .summary import build_summary

__all__ = ["SessionRecorder", "mask_pii"]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# Two capitalised words in a row, in a query that is otherwise lowercase
# help-desk prose, is almost always a person's name.  Turkish given names
# are too numerous and too overlapping with ordinary nouns for a word
# list, so shape is used instead.
_NAME_RE = re.compile(
    r"\b[A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,}\s+[A-ZÇĞİÖŞÜ][a-zçğıöşü]{2,}\b"
)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+90[\s-]?)?0?5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}\b")


def mask_pii(text: str) -> str:
    """Replace personal identifiers in a free-text query."""
    if not text:
        return text
    masked = _EMAIL_RE.sub("[EPOSTA]", text)
    masked = _PHONE_RE.sub("[TELEFON]", masked)
    masked = _NAME_RE.sub("[KULLANICI]", masked)
    return masked


class SessionRecorder:
    """Persists a walk and produces its closing artefacts."""

    def __init__(self, db, *, agent_ref: str | None = None) -> None:
        self.db = db
        self.agent_ref = agent_ref

    # -- lifecycle ------------------------------------------------------

    def start(
        self,
        *,
        query_raw: str,
        query_norm: str,
        record: Mapping[str, Any],
        state: WalkState,
        match_score: float | None = None,
        match_method: str | None = None,
    ) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions(started_at, query_raw, query_norm, record_id,
                                     record_version, match_score, match_method,
                                     context_json, agent_ref)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    state.started_at,
                    mask_pii(query_raw),
                    query_norm,
                    record.get("id"),
                    state.version,
                    match_score,
                    match_method,
                    json.dumps(state.context, ensure_ascii=False),
                    self.agent_ref,
                ),
            )
            state.session_id = int(cursor.lastrowid or 0)
        return state.session_id

    def record_step(
        self,
        state: WalkState,
        *,
        node_key: str,
        node_title: str,
        answer: str | None,
        skipped: bool = False,
        dwell_s: int | None = None,
    ) -> None:
        if not state.session_id:
            return
        with self.db.transaction() as conn:
            order_idx = int(
                conn.execute(
                    "SELECT COALESCE(MAX(order_idx), 0) + 1 FROM session_steps WHERE session_id = ?",
                    (state.session_id,),
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO session_steps(session_id, node_key, node_title, answer,
                                          skipped, entered_at, dwell_s, order_idx)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (state.session_id, node_key, node_title, answer, int(skipped),
                 _now(), dwell_s, order_idx),
            )

    def finish(
        self,
        state: WalkState,
        *,
        outcome: str,
        root_cause: str | None = None,
        resolution_node: str | None = None,
    ) -> None:
        if not state.session_id:
            return
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET ended_at = ?, outcome = ?, duration_s = ?, root_cause = ?,
                    resolution_node = ?, context_json = ?
                WHERE id = ?
                """,
                (_now(), outcome, state.elapsed_s, root_cause, resolution_node,
                 json.dumps(state.context, ensure_ascii=False), state.session_id),
            )

    # -- artefacts ------------------------------------------------------

    def summary(
        self,
        engine: Engine,
        state: WalkState,
        *,
        query_raw: str,
        outcome: str,
        lang: str = "tr",
    ) -> str:
        node = engine.nodes.get(state.current)
        return build_summary(
            record=engine.record,
            state_dict=state.as_dict(),
            steps=engine.steps_taken(state),
            outcome=outcome,
            query_raw=mask_pii(query_raw),
            node=node,
            lang=lang,
        )

    # -- knowledge gaps -------------------------------------------------

    def record_gap(self, query_raw: str, query_norm: str, best_score: float) -> None:
        """Log a search that found nothing useful.

        This is the single most actionable report the tool produces: it is
        a ranked list of runbooks that do not exist yet, written by the
        people who needed them (section 11.1).
        """
        self.db.record_knowledge_gap(query_norm, mask_pii(query_raw), best_score, _now())

    # -- resumable state -------------------------------------------------

    def save_state(self, state: WalkState) -> None:
        """Persist the walk so it survives a refresh or a restart.

        A technician is on the phone; losing six steps of context because
        the browser reloaded would be worse than not having the tool.
        """
        if not state.session_id:
            return
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE sessions SET state_json = ?, context_json = ? WHERE id = ?",
                (
                    json.dumps(state.as_dict(), ensure_ascii=False),
                    json.dumps(state.context, ensure_ascii=False),
                    state.session_id,
                ),
            )

    def load_state(self, session_id: int) -> WalkState | None:
        row = self.db.query_one(
            "SELECT id, state_json FROM sessions WHERE id = ?", (session_id,)
        )
        if not row or not row["state_json"]:
            return None
        state = WalkState.from_dict(json.loads(row["state_json"]))
        state.session_id = int(row["id"])
        return state

    def record_code(self, session_id: int) -> str | None:
        return self.db.scalar(
            "SELECT r.code FROM sessions s JOIN records r ON r.id = s.record_id "
            "WHERE s.id = ?",
            (session_id,),
        )

    def query_of(self, session_id: int) -> str:
        return self.db.scalar(
            "SELECT query_raw FROM sessions WHERE id = ?", (session_id,)
        ) or ""
