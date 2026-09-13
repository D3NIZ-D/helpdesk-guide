"""SQLite access layer.

No ORM (design doc section 13.1).  Every query here touches either FTS5 or
a hand-tuned index, and an ORM would hide exactly the part that needs to
stay visible.  The whole file is plain ``sqlite3`` with row factories.

Content rows are rewritten wholesale by the compiler; telemetry rows are
append-only and survive recompilation, which is what makes "which searches
found nothing" a usable signal across content releases.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = ["Database", "row_to_dict"]

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _json_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


class Database:
    """A thin connection manager with per-thread connections.

    Uvicorn serves requests from a thread pool, and SQLite connections are
    not safely shared across threads, so each thread gets its own.  WAL
    mode keeps concurrent readers from blocking the compiler's writer.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._local = threading.local()

    # -- connection management -------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connection
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    def executescript(self, sql: str) -> None:
        self.connection.executescript(sql)
        self.connection.commit()

    #: Columns added to *telemetry* tables after the first release.
    #:
    #: Content tables are dropped and rebuilt by every compile, so they
    #: need no migration -- but sessions, knowledge gaps and feedback are
    #: deliberately preserved across content releases, which means an
    #: existing database can be older than the schema file.  ``CREATE TABLE
    #: IF NOT EXISTS`` silently leaves such a table with its old columns,
    #: so each addition is listed here and applied idempotently.
    MIGRATIONS: tuple[tuple[str, str, str], ...] = (
        ("sessions", "state_json", "ALTER TABLE sessions ADD COLUMN state_json TEXT"),
    )

    def initialise(self) -> None:
        """Create the schema if it is not there yet, then migrate it."""
        self.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.migrate()

    def migrate(self) -> list[str]:
        """Apply any missing column additions. Returns what was applied."""
        applied: list[str] = []
        for table, column, ddl in self.MIGRATIONS:
            try:
                columns = {
                    row["name"] for row in self.query(f"PRAGMA table_info({table})")
                }
            except sqlite3.DatabaseError:
                continue
            if not columns or column in columns:
                continue
            with self.transaction() as conn:
                conn.execute(ddl)
            applied.append(f"{table}.{column}")
        return applied

    def query(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> Any:
        row = self.query_one(sql, params)
        return row[0] if row else None

    # -- build metadata --------------------------------------------

    def set_build_info(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO build_info(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def build_info(self) -> dict[str, str]:
        try:
            return {row["key"]: row["value"] for row in self.query("SELECT key, value FROM build_info")}
        except sqlite3.OperationalError:
            return {}

    @property
    def is_initialised(self) -> bool:
        try:
            return bool(
                self.query_one(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'"
                )
            )
        except sqlite3.DatabaseError:
            return False

    # -- content reads ---------------------------------------------

    def get_record(self, code: str, *, include_unpublished: bool = False) -> dict[str, Any] | None:
        clause = "" if include_unpublished else " AND r.status = 'published'"
        row = self.query_one(
            f"""
            SELECT r.*, c.code AS category_code, c.name AS category_name
            FROM records r JOIN categories c ON c.id = r.category_id
            WHERE r.code = ?{clause}
            """,
            (code.upper(),),
        )
        if row is None:
            return None
        record = dict(row)
        record["os_scope"] = _json_list(record.get("os_scope"))
        record["asset_scope"] = _json_list(record.get("asset_scope"))
        record["tags"] = _json_list(record.get("tags"))
        record["likely_causes"] = _json_list(record.get("likely_causes"))
        record["related"] = _json_list(record.get("related"))
        record["source"] = json.loads(record["source_json"]) if record.get("source_json") else None
        record["nodes"] = self.get_nodes(record["id"])
        record["steps"] = [dict(r) for r in self.query(
            "SELECT * FROM steps WHERE record_id = ? ORDER BY order_idx", (record["id"],)
        )]
        record["disambiguation"] = [dict(r) for r in self.query(
            "SELECT * FROM disambiguation WHERE record_id = ? ORDER BY order_idx", (record["id"],)
        )]
        return record

    def get_nodes(self, record_id: int) -> dict[str, dict[str, Any]]:
        nodes: dict[str, dict[str, Any]] = {}
        for row in self.query(
            "SELECT * FROM nodes WHERE record_id = ? ORDER BY order_idx", (record_id,)
        ):
            node = dict(row)
            node["media"] = _json_list(node.get("media"))
            node["edges"] = [
                dict(edge) for edge in self.query(
                    "SELECT label, target_key, order_idx, condition FROM edges "
                    "WHERE node_id = ? ORDER BY order_idx",
                    (node["id"],),
                )
            ]
            nodes[node["key"]] = node
        return nodes

    def list_records(
        self,
        *,
        tier: str | None = None,
        category: str | None = None,
        lang: str | None = None,
        status: str | None = "published",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        sql = [
            "SELECT r.code, r.title, r.summary, r.tier, r.lang, r.severity, r.status,",
            "       r.verification, r.review_due, c.code AS category_code",
            "FROM records r JOIN categories c ON c.id = r.category_id WHERE 1=1",
        ]
        params: list[Any] = []
        if status:
            sql.append("AND r.status = ?")
            params.append(status)
        if tier:
            sql.append("AND r.tier = ?")
            params.append(tier)
        if lang:
            sql.append("AND r.lang = ?")
            params.append(lang)
        if category:
            sql.append("AND c.code LIKE ?")
            params.append(f"{category}%")
        sql.append("ORDER BY c.code, r.code LIMIT ?")
        params.append(limit)
        return [dict(row) for row in self.query(" ".join(sql), params)]

    def list_categories(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.query(
                """
                SELECT c.code, c.name, COUNT(r.id) AS record_count
                FROM categories c
                LEFT JOIN records r ON r.category_id = c.id AND r.status = 'published'
                GROUP BY c.id ORDER BY c.code
                """
            )
        ]

    # -- telemetry writes ------------------------------------------

    def record_knowledge_gap(self, query_norm: str, query_raw: str, best_score: float, now: str) -> None:
        if not query_norm:
            return
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_gaps(query_norm, query_raw, hit_count, best_score,
                                           first_seen, last_seen)
                VALUES(?, ?, 1, ?, ?, ?)
                ON CONFLICT(query_norm) DO UPDATE SET
                    hit_count  = hit_count + 1,
                    last_seen  = excluded.last_seen,
                    best_score = MAX(COALESCE(knowledge_gaps.best_score, 0), excluded.best_score)
                """,
                (query_norm, query_raw, best_score, now, now),
            )

    def add_feedback(self, record_id: int | None, node_key: str | None,
                     kind: str, note: str | None, now: str) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO feedback(record_id, node_key, kind, note, created_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (record_id, node_key, kind, note, now),
            )
            return int(cursor.lastrowid or 0)

    # -- scoring inputs --------------------------------------------

    def success_history(self, days: int = 90) -> dict[int, float]:
        """Per-record share of sessions that ended in a resolution.

        Feeds the ``success_history`` term of the score (section 8.3):
        content that actually resolves calls drifts upward over time, and
        content that always escalates drifts down.
        """
        rows = self.query(
            """
            SELECT record_id,
                   SUM(CASE WHEN outcome = 'resolved' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) AS rate,
                   COUNT(*) AS n
            FROM sessions
            WHERE record_id IS NOT NULL
              AND started_at >= datetime('now', ?)
            GROUP BY record_id
            """,
            (f"-{int(days)} days",),
        )
        # Shrink towards 0.5 until there is enough evidence, so one lucky
        # session cannot pin a record to the top of every result list.
        out: dict[int, float] = {}
        for row in rows:
            n = row["n"] or 0
            rate = row["rate"] or 0.0
            confidence = min(n / 10.0, 1.0)
            out[int(row["record_id"])] = 0.5 + (rate - 0.5) * confidence
        return out
