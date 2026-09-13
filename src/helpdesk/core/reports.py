"""Content-quality reports (design doc section 11.1).

Every report here answers one question and implies one action.  A report
that does not change what somebody writes next is not worth computing, so
there is no "sessions per day" chart: it would measure the technicians,
and section 11 is explicit that the telemetry exists to improve the
content, not to rank the staff.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

__all__ = [
    "all_reports",
    "coverage",
    "dead_ends",
    "escalation_hotspots",
    "knowledge_gaps",
    "root_cause_distribution",
    "skipped_steps",
    "slow_steps",
    "stale_content",
]


def knowledge_gaps(db, limit: int = 50) -> list[dict[str, Any]]:
    """Searches that found nothing. -> Write these runbooks next."""
    return [
        dict(row)
        for row in db.query(
            """
            SELECT query_norm, query_raw, hit_count, best_score, first_seen, last_seen, status
            FROM knowledge_gaps
            WHERE status = 'open'
            ORDER BY hit_count DESC, last_seen DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]


def dead_ends(db, limit: int = 25) -> list[dict[str, Any]]:
    """Nodes where sessions get abandoned. -> Rewrite that branch."""
    return [
        dict(row)
        for row in db.query(
            """
            SELECT r.code, r.title, ss.node_key, ss.node_title,
                   COUNT(*) AS abandoned_count
            FROM session_steps ss
            JOIN sessions s ON s.id = ss.session_id
            LEFT JOIN records r ON r.id = s.record_id
            WHERE s.outcome = 'abandoned'
              AND ss.order_idx = (
                    SELECT MAX(order_idx) FROM session_steps WHERE session_id = s.id
              )
            GROUP BY r.code, ss.node_key
            HAVING abandoned_count >= 1
            ORDER BY abandoned_count DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]


def escalation_hotspots(db, limit: int = 25) -> list[dict[str, Any]]:
    """Runbooks that escalate often. -> Deepen those branches."""
    return [
        dict(row)
        for row in db.query(
            """
            SELECT r.code, r.title,
                   COUNT(*) AS sessions,
                   SUM(CASE WHEN s.outcome = 'escalated' THEN 1 ELSE 0 END) AS escalations,
                   ROUND(SUM(CASE WHEN s.outcome = 'escalated' THEN 1.0 ELSE 0 END)
                         / COUNT(*), 3) AS escalation_rate
            FROM sessions s JOIN records r ON r.id = s.record_id
            GROUP BY r.id
            HAVING sessions >= 3 AND escalations > 0
            ORDER BY escalation_rate DESC, escalations DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]


def skipped_steps(db, limit: int = 25) -> list[dict[str, Any]]:
    """Steps technicians always skip. -> Delete them if they are noise."""
    return [
        dict(row)
        for row in db.query(
            """
            SELECT r.code, ss.node_key, ss.node_title,
                   SUM(ss.skipped) AS skips, COUNT(*) AS visits,
                   ROUND(SUM(ss.skipped) * 1.0 / COUNT(*), 3) AS skip_rate
            FROM session_steps ss
            JOIN sessions s ON s.id = ss.session_id
            LEFT JOIN records r ON r.id = s.record_id
            GROUP BY r.code, ss.node_key
            HAVING visits >= 3 AND skip_rate >= 0.5
            ORDER BY skip_rate DESC, visits DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]


def slow_steps(db, limit: int = 25) -> list[dict[str, Any]]:
    """Steps that take far longer than estimated. -> Clarify the wording."""
    return [
        dict(row)
        for row in db.query(
            """
            SELECT r.code, ss.node_key, ss.node_title,
                   ROUND(AVG(ss.dwell_s)) AS avg_dwell_s,
                   n.est_seconds,
                   COUNT(*) AS visits
            FROM session_steps ss
            JOIN sessions s ON s.id = ss.session_id
            LEFT JOIN records r ON r.id = s.record_id
            LEFT JOIN nodes n ON n.record_id = r.id AND n.key = ss.node_key
            WHERE ss.dwell_s IS NOT NULL AND n.est_seconds IS NOT NULL
            GROUP BY r.code, ss.node_key
            HAVING visits >= 3 AND avg_dwell_s > n.est_seconds * 1.5
            ORDER BY (avg_dwell_s - n.est_seconds) DESC
            LIMIT ?
            """,
            (limit,),
        )
    ]


def stale_content(db, limit: int = 100) -> list[dict[str, Any]]:
    """Records past their review date. -> Schedule a review."""
    today = dt.date.today().isoformat()
    return [
        dict(row)
        for row in db.query(
            """
            SELECT code, title, tier, verification, review_due, reviewed_at
            FROM records
            WHERE status = 'published' AND review_due IS NOT NULL AND review_due < ?
            ORDER BY review_due
            LIMIT ?
            """,
            (today, limit),
        )
    ]


def root_cause_distribution(db, days: int = 90, limit: int = 25) -> list[dict[str, Any]]:
    """Most frequent root causes. -> Fix the cause, not the symptom.

    This is the report that pays for the project twice: "23 cases of
    VIDEO_CABLE_FAULT in 90 days" is not a troubleshooting fact, it is a
    purchasing decision (section 11.1).
    """
    return [
        dict(row)
        for row in db.query(
            """
            SELECT s.root_cause, COUNT(*) AS occurrences,
                   COUNT(DISTINCT s.record_id) AS records
            FROM sessions s
            WHERE s.root_cause IS NOT NULL
              AND s.started_at >= datetime('now', ?)
            GROUP BY s.root_cause
            ORDER BY occurrences DESC
            LIMIT ?
            """,
            (f"-{int(days)} days", limit),
        )
    ]


def coverage(db) -> dict[str, Any]:
    """Headline numbers for the success metrics table (section 16)."""
    totals = db.query_one(
        """
        SELECT COUNT(*) AS sessions,
               SUM(CASE WHEN outcome = 'resolved'  THEN 1 ELSE 0 END) AS resolved,
               SUM(CASE WHEN outcome = 'escalated' THEN 1 ELSE 0 END) AS escalated,
               SUM(CASE WHEN outcome = 'abandoned' THEN 1 ELSE 0 END) AS abandoned,
               ROUND(AVG(duration_s)) AS avg_duration_s
        FROM sessions WHERE outcome IS NOT NULL
        """
    )
    by_tier = {
        row["tier"]: row["n"]
        for row in db.query(
            "SELECT tier, COUNT(*) AS n FROM records WHERE status='published' GROUP BY tier"
        )
    }
    by_verification = {
        row["verification"]: row["n"]
        for row in db.query(
            "SELECT verification, COUNT(*) AS n FROM records "
            "WHERE status='published' GROUP BY verification"
        )
    }
    gaps = db.scalar("SELECT COUNT(*) FROM knowledge_gaps WHERE status='open'") or 0
    sessions = (totals["sessions"] if totals else 0) or 0
    resolved = (totals["resolved"] if totals else 0) or 0

    return {
        "records": sum(by_tier.values()),
        "by_tier": by_tier,
        "by_verification": by_verification,
        "sessions": sessions,
        "resolved": resolved,
        "escalated": (totals["escalated"] if totals else 0) or 0,
        "abandoned": (totals["abandoned"] if totals else 0) or 0,
        "first_contact_resolution": round(resolved / sessions, 3) if sessions else None,
        "avg_duration_s": (totals["avg_duration_s"] if totals else None),
        "open_knowledge_gaps": gaps,
        "stale_records": len(stale_content(db)),
    }


def all_reports(db) -> dict[str, Any]:
    return {
        "coverage": coverage(db),
        "knowledge_gaps": knowledge_gaps(db),
        "dead_ends": dead_ends(db),
        "escalation_hotspots": escalation_hotspots(db),
        "skipped_steps": skipped_steps(db),
        "slow_steps": slow_steps(db),
        "stale_content": stale_content(db),
        "root_cause_distribution": root_cause_distribution(db),
    }
