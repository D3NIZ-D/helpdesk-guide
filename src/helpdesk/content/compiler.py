"""Compile YAML content into the SQLite runtime database.

The build is a full rebuild of every content table inside one transaction
(design doc section 4).  Incremental compilation would be faster but the
whole corpus compiles in well under a second even at the 5,000-record
target, and a full rebuild removes an entire class of "stale row that no
file produces any more" bugs.

Telemetry tables are never touched, so sessions, knowledge gaps and
feedback survive a content release -- which is the point: "this query
found nothing" is only useful if it outlives the content that failed it.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..core.lexicon import Lexicon
from ..core.normalize import normalize_term
from ..db.repo import Database
from .loader import LoadError, load_roots
from .schema import Record
from .validator import ValidationReport, validate_records

__all__ = ["CompileResult", "compile_content"]

#: Child tables rebuilt from scratch on every compile.  ``records`` is
#: deliberately absent: its rows are *upserted* by code so that their ids
#: stay stable, because ``sessions.record_id`` points at them and session
#: history is meant to outlive the content release that produced it.
#: Deleting and reinserting would either break the foreign key outright or
#: silently re-point old sessions at whatever record inherited the id.
CHILD_TABLES = (
    "disambiguation",
    "steps",
    "edges",
    "nodes",
    "aliases",
)


@dataclass
class CompileResult:
    records: int = 0
    nodes: int = 0
    aliases: int = 0
    steps: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)
    report: ValidationReport = field(default_factory=ValidationReport)
    load_errors: list[LoadError] = field(default_factory=list)
    written: bool = False

    @property
    def ok(self) -> bool:
        return not self.load_errors and self.report.ok


def _category_name(code: str) -> str:
    """Human label for a slug: ``donanim/goruntu`` -> ``Donanim / Goruntu``."""
    return " / ".join(part.replace("-", " ").replace("_", " ").title() for part in code.split("/"))


def _ensure_categories(conn, codes: Iterable[str]) -> dict[str, int]:
    """Insert every category and its ancestors, returning code -> id."""
    wanted: set[str] = set()
    for code in codes:
        parts = [p for p in str(code).strip("/").split("/") if p]
        for depth in range(1, len(parts) + 1):
            wanted.add("/".join(parts[:depth]))
    ids: dict[str, int] = {}
    for code in sorted(wanted, key=lambda c: (c.count("/"), c)):
        parent_code = code.rsplit("/", 1)[0] if "/" in code else None
        conn.execute(
            "INSERT INTO categories(code, name, parent_id) VALUES(?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET name = excluded.name",
            (code, _category_name(code), ids.get(parent_code) if parent_code else None),
        )
        row = conn.execute("SELECT id FROM categories WHERE code = ?", (code,)).fetchone()
        ids[code] = int(row["id"])
    return ids


def _fts_alias_blob(record: Record) -> str:
    """Alias text for the FTS index, in both original and folded spelling.

    Indexing both forms is what makes ``monitorum`` reach ``monitörüm``
    without the query side having to guess which keyboard the technician
    is using (design doc section 8.1, step 4).
    """
    parts: list[str] = []
    for alias in record.aliases:
        parts.append(alias.term)
        if alias.term_norm and alias.term_norm != alias.term:
            parts.append(alias.term_norm)
        if alias.term_key:
            parts.append(alias.term_key)
    return "\n".join(parts)


def _fts_concept_blob(record: Record, lexicon: Lexicon) -> str:
    """Canonical concepts this record is about.

    Written into the index so that a query can search for one concept term
    instead of fanning out to every synonym (see ``Lexicon.expand``).  A
    record whose prose only ever says "display" becomes reachable from
    "ekran" through the shared concept, without either side paying the
    BM25 distortion that synonym fan-out causes.
    """
    text = " ".join(
        [record.title, record.summary or "", " ".join(a.term for a in record.aliases)]
    )
    return " ".join(lexicon.concepts(text))


def compile_content(
    db: Database,
    roots: Sequence[Path],
    *,
    strict: bool = True,
    strict_secrets: bool = True,
    allow_drafts: bool = False,
) -> CompileResult:
    """Load, validate and write every content record.

    ``strict`` makes warnings fatal -- used by CI.  ``allow_drafts`` keeps
    ``status: draft`` records in the database so an author can walk a
    runbook before publishing it.
    """
    result = CompileResult()

    records, load_errors = load_roots(roots)
    result.load_errors = list(load_errors)

    # One lexicon per content language; loaded once and reused.
    lexicons: dict[str, Lexicon] = {}

    def lexicon_for(lang: str) -> Lexicon:
        if lang not in lexicons:
            lexicons[lang] = Lexicon.load(roots, lang=lang)
        return lexicons[lang]

    result.report = validate_records(records, strict_secrets=strict_secrets)

    if result.load_errors or not result.report.ok:
        return result
    if strict and result.report.warnings:
        return result

    keep = [r for r in records if allow_drafts or r.status != "draft"]

    db.initialise()
    with db.transaction() as conn:
        for table in CHILD_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM fts_records")

        # A record whose file disappeared is retired, not erased: archiving
        # keeps the sessions that reference it readable, and archived rows
        # are already excluded from search by the status filter.
        keep_codes = {r.code for r in keep}
        for row in conn.execute("SELECT id, code FROM records").fetchall():
            if row["code"] not in keep_codes:
                conn.execute(
                    "UPDATE records SET status = 'archived', updated_at = ? WHERE id = ?",
                    (dt.datetime.now().isoformat(timespec="seconds"), row["id"]),
                )

        category_ids = _ensure_categories(conn, (r.category for r in keep))
        now = dt.datetime.now().isoformat(timespec="seconds")

        for record in keep:
            # Bump the version whenever the source file actually changed.
            # Sessions pin the version they started on, so this is what
            # lets a report say "resolved under DSP-001 v2, not v3".
            previous = conn.execute(
                "SELECT id, version, content_hash FROM records WHERE code = ?",
                (record.code,),
            ).fetchone()
            version = 1
            if previous:
                version = int(previous["version"] or 1)
                if previous["content_hash"] != record.content_hash:
                    version += 1

            conn.execute(
                """
                INSERT INTO records(
                    code, tier, lang, title, summary, category_id, severity, status,
                    verification, os_scope, asset_scope, tags, entry_node, fix_summary,
                    likely_causes, source_json, related_runbook, related, merged_into,
                    version, author, reviewed_at, review_due, content_hash,
                    source_path, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(code) DO UPDATE SET
                    tier=excluded.tier, lang=excluded.lang, title=excluded.title,
                    summary=excluded.summary, category_id=excluded.category_id,
                    severity=excluded.severity, status=excluded.status,
                    verification=excluded.verification, os_scope=excluded.os_scope,
                    asset_scope=excluded.asset_scope, tags=excluded.tags,
                    entry_node=excluded.entry_node, fix_summary=excluded.fix_summary,
                    likely_causes=excluded.likely_causes, source_json=excluded.source_json,
                    related_runbook=excluded.related_runbook, related=excluded.related,
                    merged_into=excluded.merged_into, version=excluded.version,
                    author=excluded.author, reviewed_at=excluded.reviewed_at,
                    review_due=excluded.review_due, content_hash=excluded.content_hash,
                    source_path=excluded.source_path, updated_at=excluded.updated_at
                """,
                (
                    record.code, record.tier, record.lang, record.title, record.summary,
                    category_ids[record.category], record.severity, record.status,
                    record.verification,
                    json.dumps(record.os_scope, ensure_ascii=False),
                    json.dumps(record.asset_scope, ensure_ascii=False),
                    json.dumps(record.tags, ensure_ascii=False),
                    record.entry, record.fix_summary,
                    json.dumps(record.likely_causes, ensure_ascii=False),
                    json.dumps(record.source, ensure_ascii=False) if record.source else None,
                    record.related_runbook,
                    json.dumps(record.related, ensure_ascii=False),
                    record.merged_into,
                    version, record.author, record.reviewed_at, record.review_due,
                    record.content_hash, record.source_path, now,
                ),
            )
            record_id = int(
                conn.execute("SELECT id FROM records WHERE code = ?", (record.code,))
                .fetchone()["id"]
            )
            result.records += 1
            result.by_tier[record.tier] = result.by_tier.get(record.tier, 0) + 1

            for alias in record.aliases:
                conn.execute(
                    "INSERT INTO aliases(record_id, term, term_norm, term_key, weight, kind) "
                    "VALUES(?,?,?,?,?,?)",
                    (record_id, alias.term, alias.term_norm, alias.term_key,
                     alias.weight, alias.kind),
                )
                result.aliases += 1

            for order_idx, node in enumerate(record.nodes):
                node_cursor = conn.execute(
                    """
                    INSERT INTO nodes(
                        record_id, key, type, title, body_md, verify_text, risk,
                        requires_admin, requires_user_downtime, est_seconds, rollback_md,
                        media, root_cause, closure_note, followup_md, part_required,
                        escalate_to, summary_template, order_idx)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        record_id, node.key, node.type, node.title, node.body_md,
                        node.verify_text, node.risk, int(node.requires_admin),
                        int(node.requires_user_downtime), node.est_seconds,
                        node.rollback_md,
                        json.dumps(node.media, ensure_ascii=False),
                        node.root_cause, node.closure_note, node.followup_md,
                        node.part_required, node.escalate_to, node.summary_template,
                        order_idx,
                    ),
                )
                node_id = int(node_cursor.lastrowid or 0)
                result.nodes += 1
                for edge in node.edges:
                    conn.execute(
                        "INSERT INTO edges(node_id, label, target_key, order_idx, condition) "
                        "VALUES(?,?,?,?,?)",
                        (node_id, edge.label, edge.target, edge.order_idx, edge.condition),
                    )

            for step in record.steps:
                conn.execute(
                    "INSERT INTO steps(record_id, order_idx, title, note, body_md, risk, "
                    "requires_admin, est_seconds) VALUES(?,?,?,?,?,?,?,?)",
                    (record_id, step.order_idx, step.title, step.note, step.body_md,
                     step.risk, int(step.requires_admin), step.est_seconds),
                )
                result.steps += 1

            for order_idx, entry in enumerate(record.disambiguation):
                question = str(entry.get("question", "")).strip()
                for answer_idx, option in enumerate(entry.get("options") or []):
                    conn.execute(
                        "INSERT INTO disambiguation(record_id, question, answer, "
                        "target_code, order_idx) VALUES(?,?,?,?,?)",
                        (record_id, question, str(option.get("answer", "")).strip(),
                         str(option.get("to", "")).strip().upper(),
                         order_idx * 100 + answer_idx),
                    )

            conn.execute(
                "INSERT INTO fts_records(title, aliases, body, tags, record_id, lang) "
                "VALUES(?,?,?,?,?,?)",
                (
                    f"{record.title}\n{normalize_term(record.title)}",
                    _fts_alias_blob(record)
                    + "\n"
                    + _fts_concept_blob(record, lexicon_for(record.lang)),
                    record.searchable_body(),
                    " ".join(record.tags),
                    record_id,
                    record.lang,
                ),
            )

        conn.execute(
            "INSERT INTO build_info(key, value) VALUES('compiled_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (now,),
        )
        conn.execute(
            "INSERT INTO build_info(key, value) VALUES('record_count', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(result.records),),
        )
        conn.execute(
            "INSERT INTO build_info(key, value) VALUES('roots', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps([str(r) for r in roots]),),
        )

    result.written = True
    return result
