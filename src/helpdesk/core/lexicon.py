"""The lexicon: device vocabulary, fault verbs, stopwords and abbreviations.

Design doc section 8.1/8.2.  The v1 decision was "rule-based suffix
stripping plus a broad alias vocabulary" rather than a full morphological
analyser, on the grounds that IT Turkish is a small language: a few
hundred device nouns and under a hundred fault verbs.  This module is the
vocabulary half of that bargain.

Files live under ``content/lexicon/<lang>/`` so the rules can differ per
language -- Turkish dotted/dotless ``i`` logic produces wrong results in
English, and English stopwords are meaningless in Turkish.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .normalize import ascii_fold, is_negated, stem, tr_lower

__all__ = ["Lexicon", "LexiconEntry"]


@dataclass(frozen=True)
class LexiconEntry:
    kind: str
    term: str
    canonical: str | None = None
    weight: float = 1.0


def _fold(text: str) -> str:
    return ascii_fold(tr_lower(text or "")).strip()


@dataclass
class Lexicon:
    """Loaded vocabulary for one language."""

    lang: str = "tr"
    #: folded surface form -> canonical concept ("ekran" -> "monitor")
    canonical: dict[str, str] = field(default_factory=dict)
    #: canonical concept -> every folded surface form that maps onto it
    surface: dict[str, set[str]] = field(default_factory=dict)
    #: folded stopwords, removed before stemming
    stopwords: set[str] = field(default_factory=set)
    #: intent class -> folded trigger phrases ("CALISMIYOR" -> {"calismiyor", ...})
    fault_intents: dict[str, set[str]] = field(default_factory=dict)
    #: abbreviation -> expansion ("bsod" -> "mavi ekran")
    abbreviations: dict[str, str] = field(default_factory=dict)
    #: per-term weight override, used by the matcher
    weights: dict[str, float] = field(default_factory=dict)

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, roots: Sequence[Path], lang: str = "tr") -> Lexicon:
        """Load and merge lexicon files from every root, later roots winning.

        ``roots`` is normally ``[content/, content-local/]`` so a site can
        add its own vocabulary ("Acme VPN", internal server nicknames)
        without touching the upstream files.
        """
        lex = cls(lang=lang)
        for root in roots:
            directory = Path(root) / "lexicon" / lang
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
                lex.merge_file(path)
        return lex

    def merge_file(self, path: Path) -> None:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, Mapping):
            raise ValueError(f"{path}: lexicon file must be a mapping")
        self.merge(data)

    def merge(self, data: Mapping) -> None:
        for term in data.get("stopwords") or []:
            self.stopwords.add(_fold(str(term)))

        # devices: canonical concept with a list of surface synonyms
        for concept, entry in (data.get("devices") or {}).items():
            canon = _fold(concept)
            terms = entry.get("terms", []) if isinstance(entry, Mapping) else list(entry or [])
            weight = float(entry.get("weight", 1.0)) if isinstance(entry, Mapping) else 1.0
            bucket = self.surface.setdefault(canon, set())
            bucket.add(canon)
            self.canonical[canon] = canon
            self.canonical[stem(canon)] = canon
            self.weights[canon] = weight
            for term in terms:
                folded = _fold(str(term))
                if not folded:
                    continue
                bucket.add(folded)
                self.canonical[folded] = canon
                self.canonical[stem(folded)] = canon

        # faults: intent class with a list of trigger phrases
        for intent, entry in (data.get("faults") or {}).items():
            code = str(intent).strip().upper()
            terms = entry.get("terms", []) if isinstance(entry, Mapping) else list(entry or [])
            bucket = self.fault_intents.setdefault(code, set())
            for term in terms:
                folded = _fold(str(term))
                if folded:
                    bucket.add(folded)

        for short, long in (data.get("abbreviations") or {}).items():
            self.abbreviations[_fold(str(short))] = _fold(str(long))

    # -- queries ---------------------------------------------------------

    def is_stopword(self, token: str) -> bool:
        return token in self.stopwords

    def canonicalise(self, token: str) -> str:
        """Map a surface token onto its canonical concept, if known."""
        return self.canonical.get(token) or self.canonical.get(stem(token)) or token

    def expand(self, stems: Iterable[str]) -> list[str]:
        """Expand stems with their canonical concept and abbreviations.

        Each token contributes **at most two** search terms: itself and its
        canonical concept.  Fanning out to every synonym looks helpful and
        is actively harmful: the FTS terms are OR-ed, so one word like
        "ekran" would add eight terms that all land on whichever record
        happens to list the most synonyms, and BM25 would rank that record
        above the one the query actually means.  A query for "mavi ekrana
        düşüyor" was ranking the black-screen runbook first for exactly
        this reason.

        Bridging still works, because the compiler writes each record's
        canonical concepts into the index (see ``_fts_concept_blob``): a
        record that only ever says "display" is still reachable from
        "ekran", through the shared concept rather than through a
        synonym storm.
        """
        out: list[str] = []
        seen: set[str] = set()

        def push(value: str) -> None:
            if value and value not in seen:
                seen.add(value)
                out.append(value)

        for token in stems:
            push(token)
            expansion = self.abbreviations.get(token)
            if expansion:
                for part in expansion.split():
                    push(stem(part))
            canon = self.canonical.get(token)
            if canon:
                push(canon)
                push(stem(canon))
        return out

    def concepts(self, text: str) -> list[str]:
        """Canonical concepts mentioned anywhere in ``text``.

        Used at compile time to write a record's concepts into the search
        index, which is the other half of the bargain struck in
        :meth:`expand`.
        """
        found: list[str] = []
        for raw in _fold(text).split():
            canon = self.canonical.get(raw) or self.canonical.get(stem(raw))
            if canon and canon not in found:
                found.append(canon)
        return found

    def intents(self, tokens: Sequence[str], stems: Sequence[str]) -> list[str]:
        """Infer fault-intent classes (section 8.2) present in the query.

        Two passes.  First a substring match on the folded tokens, because
        Turkish glues the fault verb to its inflection and "calismiyor"
        must also fire for "calismiyormus".

        Then a stemmed match, which is what catches inflections the
        substring pass misses -- but gated on **negation agreement**.
        Stemming "calismiyor" yields "calis", which is also the stem of
        "calisiyor": without that gate, "her sey agir calisiyor" was
        classified as NOT_WORKING, the exact opposite of what it says.
        Every trigger's stems must all be present, so a two-word trigger
        cannot fire on one shared word.
        """
        joined = " ".join(tokens)
        query_stems = set(stems)
        query_negated = is_negated(tokens)
        hits: list[str] = []

        for intent, triggers in self.fault_intents.items():
            if any(trigger in joined for trigger in triggers):
                hits.append(intent)
                continue
            for trigger in triggers:
                trigger_tokens = trigger.split()
                if not trigger_tokens:
                    continue
                if is_negated(trigger_tokens) != query_negated:
                    continue
                if {stem(part) for part in trigger_tokens} <= query_stems:
                    hits.append(intent)
                    break
        return sorted(set(hits))

    # -- introspection ---------------------------------------------------

    def stats(self) -> dict[str, int]:
        return {
            "concepts": len(self.surface),
            "surface_forms": sum(len(v) for v in self.surface.values()),
            "stopwords": len(self.stopwords),
            "intents": len(self.fault_intents),
            "abbreviations": len(self.abbreviations),
        }
