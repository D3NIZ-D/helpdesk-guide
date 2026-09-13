"""Symptom -> record matching and confidence scoring.

Implements design doc sections 8.3 to 8.5, with the two multipliers that
section 22 adds for scale:

    base  = 0.40 * bm25          full-text relevance
          + 0.25 * alias_exact   a curated phrasing matched
          + 0.15 * intent        fault verb class agrees
          + 0.10 * context       OS / asset type agrees
          + 0.10 * history       this record actually resolves calls

    score = max(base, alias_floor) * verification_weight

The verification multiplier (section 22.5) is the important one once the
corpus grows: at 5,000 records, unreviewed content outranking a verified
runbook is how a knowledge base becomes worse than no knowledge base.

Tier is deliberately absent from the formula -- section 22.9 asks for a
tree to win *on an equal score*, so it lives in the sort key instead.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..content.schema import TIER_PRIORITY, VERIFICATION_WEIGHT
from .lexicon import Lexicon
from .normalize import Normalized, normalize_query, stem

__all__ = ["Candidate", "MatchResult", "Matcher", "Thresholds"]


def _json_strings(value: Any) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


@dataclass(frozen=True)
class Thresholds:
    """Confidence bands from design doc section 8.4."""

    direct: float = 0.75      # open the record straight away
    shortlist: float = 0.40   # show the top candidates
    clarify: float = 0.20     # ask a narrowing question
    # below clarify: no result -> record a knowledge gap


@dataclass
class Candidate:
    code: str
    title: str
    tier: str
    score: float
    method: str
    record_id: int
    summary: str | None = None
    category: str | None = None
    verification: str = "draft"
    severity: str = "medium"
    lang: str = "tr"
    signals: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "tier": self.tier,
            "score": round(self.score, 4),
            "confidence": round(min(self.score, 1.0) * 100),
            "method": self.method,
            "summary": self.summary,
            "category": self.category,
            "verification": self.verification,
            "severity": self.severity,
            "lang": self.lang,
            "signals": {k: round(v, 3) for k, v in self.signals.items()},
        }


@dataclass
class MatchResult:
    query: Normalized
    candidates: list[Candidate]
    action: str                       # open | shortlist | clarify | none
    clarification: dict[str, Any] | None = None

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def best_score(self) -> float:
        return self.candidates[0].score if self.candidates else 0.0

    @property
    def is_knowledge_gap(self) -> bool:
        """Whether this search failed to answer the question.

        Not the same as "returned nothing".  Because every FTS term is a
        prefix query, almost any Turkish sentence scrapes a weak match off
        some record, so a strict no-results test would leave the
        knowledge-gap report permanently empty -- and that report is the
        most actionable thing the tool produces.  Anything that did not
        reach the shortlist band is a question the content could not
        answer, which is exactly what an editor needs to see.
        """
        return self.action in {"none", "clarify"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.raw,
            "query_norm": self.query.norm,
            "intents": list(self.query.intents),
            "error_codes": list(self.query.error_codes),
            "action": self.action,
            "clarification": self.clarification,
            "candidates": [c.as_dict() for c in self.candidates],
        }


#: Minimum score guaranteed by an alias match of each strength, before the
#: verification multiplier.  ``error_code`` and ``alias_exact`` clear the
#: 0.75 "open it" band; ``alias_key`` (same words, different order or
#: inflection) lands in the shortlist band instead.
_ALIAS_FLOOR: dict[str, float] = {
    "error_code": 0.88,
    "alias_exact": 0.82,
    "alias_key": 0.64,
    # Containment is real evidence but weaker than equality: the words the
    # user added on top of the alias might have changed what they meant.
    "alias_contains": 0.50,
}

#: Which fault-intent classes a category is plausibly about.  A coarse
#: prior, but enough to stop "internete bağlanamıyorum" from surfacing a
#: printer runbook just because both mention "bağlantı".
_INTENT_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "NO_DISPLAY": ("donanim/goruntu", "hardware/display"),
    "NO_POWER": ("donanim/guc", "hardware/power"),
    "NO_BOOT": ("donanim/onyukleme", "hardware/boot"),
    "NO_NETWORK": ("ag", "network"),
    "NO_SOUND": ("donanim/ses", "hardware/audio"),
    "PRINTING": ("yazici", "printer"),
    "IDENTITY": ("hesap", "account"),
    "SLOW": ("performans", "performance"),
    "SECURITY": ("guvenlik", "security"),
}


class Matcher:
    """Scores records against a normalised query."""

    def __init__(
        self,
        db,
        lexicon: Lexicon | None = None,
        thresholds: Thresholds | None = None,
        *,
        lang: str = "tr",
    ) -> None:
        self.db = db
        self.lexicon = lexicon
        self.thresholds = thresholds or Thresholds()
        self.lang = lang

    # -- public ---------------------------------------------------------

    def search(
        self,
        text: str,
        *,
        limit: int = 5,
        context: Mapping[str, Any] | None = None,
        lang: str | None = None,
    ) -> MatchResult:
        query = normalize_query(text, self.lexicon)
        if query.is_empty:
            return MatchResult(query=query, candidates=[], action="none")

        lang = lang or self.lang
        history = self.db.success_history()

        scored: dict[int, Candidate] = {}
        self._score_alias_matches(query, scored, lang)
        self._score_fts(query, scored, lang)
        self._score_alias_containment(query, scored)

        scopes = self._scopes(list(scored)) if context else {}
        for candidate in scored.values():
            self._finalise(candidate, query, context or {}, history, scopes)

        candidates = sorted(
            scored.values(),
            # Score first; tier only separates records the score cannot.
            key=lambda c: (-c.score, -TIER_PRIORITY.get(c.tier, 0.0), c.code),
        )[:limit]
        action, clarification = self._decide(candidates, query)
        return MatchResult(
            query=query, candidates=candidates, action=action, clarification=clarification
        )

    # -- signal collection ----------------------------------------------

    def _candidate_from_row(self, row: sqlite3.Row) -> Candidate:
        return Candidate(
            code=row["code"],
            title=row["title"],
            tier=row["tier"],
            score=0.0,
            method="fts",
            record_id=int(row["record_id"] if "record_id" in row.keys() else row["id"]),
            summary=row["summary"] if "summary" in row.keys() else None,
            category=row["category_code"] if "category_code" in row.keys() else None,
            verification=row["verification"] if "verification" in row.keys() else "draft",
            severity=row["severity"] if "severity" in row.keys() else "medium",
            lang=row["lang"] if "lang" in row.keys() else "tr",
        )

    def _score_alias_matches(
        self, query: Normalized, scored: dict[int, Candidate], lang: str
    ) -> None:
        """Exact and order-insensitive alias hits.

        A curated alias is the strongest signal the content author can
        give us, so it is scored separately from BM25 rather than being
        folded into it.
        """
        norm = query.norm
        key = query.key

        # SQLite has no array parameter, so the IN list is built from one
        # placeholder per code.  Only the *count* is interpolated; every
        # value still goes through a bound parameter.
        codes = query.error_codes or ("",)
        placeholders = ",".join("?" for _ in codes)

        rows = self.db.query(
            f"""
            SELECT a.term_norm, a.term_key, a.weight, a.kind,
                   r.id AS record_id, r.code, r.title, r.summary, r.tier, r.lang,
                   r.verification, r.severity, c.code AS category_code
            FROM aliases a
            JOIN records r ON r.id = a.record_id
            JOIN categories c ON c.id = r.category_id
            WHERE r.status = 'published' AND r.lang = ?
              AND (a.term_norm = ? OR a.term_key = ? OR a.term_norm IN ({placeholders}))
            """,
            (lang, norm, key, *codes),
        )

        for row in rows:
            candidate = scored.get(int(row["record_id"])) or self._candidate_from_row(row)
            weight = float(row["weight"] or 1.0)
            exact = row["term_norm"] == norm or row["term_norm"] in query.error_codes

            if exact and row["kind"] == "error_code":
                # An error code is unambiguous by construction: the author
                # declared this alias to *be* a code, so a match on it is
                # the strongest signal the system can get.  Honouring the
                # declared kind is what makes 0x0000007B open its card
                # even when that card is only 'reviewed' rather than
                # 'verified' -- the verification multiplier still applies,
                # it just starts from a higher floor.
                strength, method = 1.0, "error_code"
            elif exact:
                strength, method = 1.0, "alias_exact"
            else:
                strength, method = 0.8, "alias_key"

            signal = min(strength * weight, 1.5)
            if signal > candidate.signals.get("alias_exact", 0.0):
                candidate.signals["alias_exact"] = signal
                candidate.method = method
            scored[int(row["record_id"])] = candidate

    def _score_alias_containment(self, query: Normalized, scored: dict[int, Candidate]) -> None:
        """Credit an alias whose words all appear somewhere in the query.

        ``term_key`` matching requires the two stem *sets* to be equal, so
        a user who adds a filler word loses the alias entirely: "printer
        bir türlü basmıyor" contains every word of the alias "yazıcı
        basmıyor" and was scoring no alias signal at all, leaving three
        printer records separated by two points of BM25 noise.

        Containment is weaker evidence than equality -- the extra words
        might have changed the meaning -- so it earns a lower floor.
        Query stems are canonicalised first, which is what lets "printer"
        satisfy an alias written with "yazıcı".

        Only the records FTS already surfaced are examined, so this reads
        a few hundred alias rows rather than the whole table.
        """
        if not scored:
            return

        candidate_stems = set(query.stems)
        if self.lexicon is not None:
            for token in query.stems:
                canon = self.lexicon.canonicalise(token)
                if canon:
                    candidate_stems.add(canon)
                    candidate_stems.add(stem(canon))

        ids = list(scored)
        placeholders = ",".join("?" for _ in ids)
        rows = self.db.query(
            f"SELECT record_id, term, term_key, weight, kind FROM aliases "
            f"WHERE record_id IN ({placeholders})",
            tuple(ids),
        )

        for row in rows:
            key_stems = set((row["term_key"] or "").split())
            if not key_stems or not key_stems <= candidate_stems:
                continue
            # A single everyday word is not evidence on its own; an error
            # code or an abbreviation is, because nobody types those by
            # accident.
            if len(key_stems) < 2 and row["kind"] not in {"error_code", "abbreviation"}:
                continue

            strength = min(0.60 * float(row["weight"] or 1.0), 1.0)
            candidate = scored[int(row["record_id"])]
            if strength > candidate.signals.get("alias_exact", 0.0):
                candidate.signals["alias_exact"] = strength
                candidate.method = "alias_contains"

    def _score_fts(self, query: Normalized, scored: dict[int, Candidate], lang: str) -> None:
        """BM25 relevance over title, aliases, body and tags."""
        expression = query.fts_query()
        if not expression:
            return
        try:
            rows = self.db.query(
                """
                -- Column weights: title, aliases, body, tags.
                -- Body is deliberately near-zero. It indexes instruction
                -- prose, not symptom descriptions, and a long runbook body
                -- accumulates enough incidental matches on generic verbs to
                -- outrank a title: the MFA runbook was winning a query
                -- about a slow computer because one of its escalation
                -- notes contains the word "bekletildigini". Body still
                -- earns its place for terms that appear nowhere else
                -- ("AHCI", "gpresult"), just not enough to overrule a
                -- curated alias.
                SELECT f.record_id, bm25(fts_records, 8.0, 6.0, 0.3, 3.0) AS rank,
                       r.code, r.title, r.summary, r.tier, r.lang, r.verification,
                       r.severity, c.code AS category_code
                FROM fts_records f
                JOIN records r ON r.id = f.record_id
                JOIN categories c ON c.id = r.category_id
                WHERE fts_records MATCH ? AND r.status = 'published' AND r.lang = ?
                ORDER BY rank LIMIT 50
                """,
                (expression, lang),
            )
        except sqlite3.OperationalError:
            # A malformed MATCH expression must degrade to "no full-text
            # signal", never to a 500.
            return

        if not rows:
            return

        # bm25() returns a negative number, better matches being more
        # negative.  Map it into 0..1 with a soft curve so that the top hit
        # does not automatically saturate the term.
        for row in rows:
            raw = -float(row["rank"] or 0.0)
            normalised = 1.0 - math.exp(-raw / 6.0)
            record_id = int(row["record_id"])
            candidate = scored.get(record_id) or self._candidate_from_row(row)
            candidate.signals["bm25"] = max(candidate.signals.get("bm25", 0.0), normalised)
            scored[record_id] = candidate

    # -- scoring --------------------------------------------------------

    def _intent_signal(self, candidate: Candidate, query: Normalized) -> float:
        """How well the query's fault class agrees with the record's category.

        Three outcomes, not two.  "No opinion" has to be distinguishable
        from "disagrees": generic intents like NOT_WORKING carry no
        category hint at all, and scoring those as a mismatch would punish
        every correct record whose user happened to say "çalışmıyor".
        """
        if not query.intents:
            return 0.5
        category = (candidate.category or "").lower()
        had_opinion = False
        for intent in query.intents:
            hints = _INTENT_CATEGORY_HINTS.get(intent, ())
            if not hints:
                continue
            had_opinion = True
            if any(category.startswith(hint) for hint in hints):
                return 1.0
        return 0.25 if had_opinion else 0.5

    def _scopes(self, record_ids: Sequence[int]) -> dict[int, tuple[list[str], list[str]]]:
        """Fetch just the scope columns for a batch of records.

        Deliberately not ``get_record``: that loads every node, edge and
        step, which is a lot of work to answer "is this runbook about
        Windows?" for fifty candidates.
        """
        if not record_ids:
            return {}
        placeholders = ",".join("?" * len(record_ids))
        rows = self.db.query(
            f"SELECT id, os_scope, asset_scope FROM records WHERE id IN ({placeholders})",
            tuple(record_ids),
        )
        out: dict[int, tuple[list[str], list[str]]] = {}
        for row in rows:
            out[int(row["id"])] = (
                _json_strings(row["os_scope"]),
                _json_strings(row["asset_scope"]),
            )
        return out

    def _context_signal(
        self,
        candidate: Candidate,
        context: Mapping[str, Any],
        scopes: Mapping[int, tuple[list[str], list[str]]],
    ) -> float:
        """Agreement between the session context and the record's scope.

        Absent context is neutral (0.5), not negative: most calls start
        without an asset tag, and punishing that would bury every
        OS-specific runbook.
        """
        os_scope, asset_scope = scopes.get(candidate.record_id, ([], []))
        signals: list[float] = []
        for key, scope in (("os", os_scope), ("asset", asset_scope)):
            wanted = context.get(key)
            if not wanted or not scope:
                continue
            signals.append(1.0 if str(wanted).lower() in {s.lower() for s in scope} else 0.0)
        return sum(signals) / len(signals) if signals else 0.5

    def _finalise(
        self,
        candidate: Candidate,
        query: Normalized,
        context: Mapping[str, Any],
        history: Mapping[int, float],
        scopes: Mapping[int, tuple[list[str], list[str]]],
    ) -> None:
        bm25 = candidate.signals.get("bm25", 0.0)
        alias = min(candidate.signals.get("alias_exact", 0.0), 1.0)
        intent = self._intent_signal(candidate, query)
        ctx = self._context_signal(candidate, context, scopes) if context else 0.5
        hist = history.get(candidate.record_id, 0.5)

        candidate.signals.update(
            {"bm25": bm25, "alias_exact": alias, "intent": intent,
             "context": ctx, "history": hist}
        )

        base = 0.40 * bm25 + 0.25 * alias + 0.15 * intent + 0.10 * ctx + 0.10 * hist

        # A curated alias that matched exactly is a direct statement by the
        # content author that this query means this record, and section 8.4
        # says such a match should open the runbook straight away.  The
        # linear formula alone cannot express that: alias_exact is capped
        # at 0.25 of the total, so a perfect alias hit could never reach
        # the 0.75 band on its own.  BM25 normally carries the rest, but it
        # collapses on a small corpus -- with few documents the IDF term
        # goes to nearly zero and every score looks equally mediocre.
        # The floor makes the guarantee hold at twenty records and at five
        # thousand alike.
        floor = _ALIAS_FLOOR.get(candidate.method, 0.0) * min(alias, 1.0)
        base = max(base, floor)

        weight = VERIFICATION_WEIGHT.get(candidate.verification, 0.5)
        # Tier is deliberately NOT added to the score. Section 22.9 asks for
        # a tree to win "on an equal score", and a bonus large enough to
        # matter is also large enough to overturn a real relevance
        # difference -- it was pushing a runbook above a reference card that
        # BM25 scored higher. It belongs in the sort key, where it breaks
        # ties and nothing else.
        candidate.score = base * weight
        candidate.signals["verification_weight"] = weight
        candidate.signals["floor"] = floor

    # -- decision -------------------------------------------------------

    def _decide(
        self, candidates: Sequence[Candidate], query: Normalized
    ) -> tuple[str, dict[str, Any] | None]:
        if not candidates:
            return "none", None

        best = candidates[0]
        runner_up = candidates[1].score if len(candidates) > 1 else 0.0

        if best.score >= self.thresholds.direct:
            # A near-tie is not confidence, however high the top score is.
            if runner_up and best.score - runner_up < 0.05:
                return "shortlist", None
            return "open", None
        if best.score >= self.thresholds.shortlist:
            return "shortlist", None
        if best.score >= self.thresholds.clarify:
            return "clarify", self._clarification(candidates)
        return "none", None

    def _clarification(self, candidates: Sequence[Candidate]) -> dict[str, Any] | None:
        """Pick a hand-written disambiguation block (section 8.5).

        v1 uses authored questions rather than derived ones: a question
        generated from the candidates' first nodes is unpredictable, and an
        unpredictable clarifying question is worse than none at all.
        """
        for candidate in candidates:
            record = self.db.get_record(candidate.code)
            if not record or not record.get("disambiguation"):
                continue
            rows = record["disambiguation"]
            question = rows[0]["question"]
            options = [
                {"answer": row["answer"], "code": row["target_code"]}
                for row in rows
                if row["question"] == question
            ]
            if options:
                return {"question": question, "options": options, "source": candidate.code}
        return None
