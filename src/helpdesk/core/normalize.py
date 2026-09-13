"""Turkish-aware text normalisation.

Turkish is agglutinative: ``monitör``, ``monitörüm``, ``monitörde`` and
``monitörünüzün`` are four different strings for one concept.  A plain
``LIKE`` scan is therefore useless, and so is an English stemmer.

The pipeline implemented here is deliberately *consistent* rather than
linguistically *correct*.  Queries and runbook aliases are pushed through
exactly the same steps, so as long as both sides collapse onto the same
token the match succeeds -- even when that token is not a real Turkish
root.  Residual suffixes are absorbed by FTS5 prefix matching
(``monitor*``), which is why a slightly over-eager stemmer is safe here
and an under-eager one is not.

Pipeline (design doc section 8.1)::

    raw  ->  NFC  ->  Turkish-aware lowercase  ->  error-code extraction
         ->  punctuation strip  ->  ASCII folding  ->  tokenise
         ->  stopword removal  ->  stemming  ->  lexicon expansion
         ->  intent inference
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "MIN_STEM",
    "Normalized",
    "ascii_fold",
    "extract_error_codes",
    "is_negated",
    "normalize_query",
    "normalize_term",
    "stem",
    "strip_punctuation",
    "term_key",
    "tokenize",
    "tr_lower",
]

# --------------------------------------------------------------------------
# Character tables
# --------------------------------------------------------------------------

# str.lower() maps "I" to "i"; in Turkish it must map to dotless "ı", and
# dotted "İ" must map to "i".  Both are wrong under locale-free rules, so
# they are translated before the generic lowercase pass.
_TR_LOWER = str.maketrans({"İ": "i", "I": "ı"})

# ASCII folding.  Both the folded and unfolded forms are indexed so that a
# technician typing "monitorum" on a keyboard without a Turkish layout
# still reaches "monitörüm".
#: Case is preserved: folding removes diacritics, lowercasing is a
#: separate step.  In the normal pipeline ``tr_lower`` runs first so the
#: uppercase rows rarely fire, but ``extract_error_codes`` folds
#: uppercase codes like ``INACCESSIBLE_BOOT_DEVICE`` directly.
_ASCII_FOLD = str.maketrans(
    {
        "ı": "i", "î": "i", "İ": "I",
        "ş": "s", "Ş": "S",
        "ğ": "g", "Ğ": "G",
        "ü": "u", "û": "u", "Ü": "U",
        "ö": "o", "ô": "o", "Ö": "O",
        "ç": "c", "Ç": "C",
        "â": "a", "Â": "A",
        "é": "e", "è": "e",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def tr_lower(text: str) -> str:
    """Lowercase ``text`` using Turkish rules for the dotted/dotless i."""
    return text.translate(_TR_LOWER).lower()


def ascii_fold(text: str) -> str:
    """Fold Turkish diacritics down to ASCII."""
    return text.translate(_ASCII_FOLD)


def strip_punctuation(text: str) -> str:
    """Drop punctuation and collapse whitespace."""
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", text)).strip()


# --------------------------------------------------------------------------
# Error codes
# --------------------------------------------------------------------------

# An error code is the highest-signal token a query can carry: a user who
# types 0x0000007B has told us exactly which record they need.  Codes are
# extracted before punctuation stripping, which would otherwise shred
# "0x..." and "ERR-1234" into meaningless fragments.
_ERROR_CODE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"0x[0-9a-f]{2,8}", re.IGNORECASE),
    re.compile(r"\b[a-z]{2,10}[-_]\d{2,6}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{3,}(?:_[A-Z]+){1,5}\b"),
    re.compile(r"\b\d{2}-\d{2,4}\b"),
)


def extract_error_codes(raw: str) -> tuple[str, ...]:
    """Return normalised error codes found in ``raw``, most specific first.

    Two normalisations matter here and both were once missing:

    * **ASCII folding.** Error codes are ASCII, but Turkish lowercasing
      turns ``INACCESSIBLE_BOOT_DEVICE`` into ``ınaccessıble_boot_devıce``
      with dotless i's, which then matches no alias at all.
    * **Separator variants.** A code appears as ``0x7B``,
      ``INACCESSIBLE_BOOT_DEVICE`` or ``Event ID 4740`` depending on who
      is writing, and an author may reasonably spell the alias with
      spaces where the message uses underscores.  Both spellings are
      emitted so the exact-match path works either way.
    """
    found: list[str] = []

    def push(code: str) -> None:
        if code and code not in found:
            found.append(code)

    for pattern in _ERROR_CODE_RES:
        for match in pattern.finditer(raw or ""):
            code = ascii_fold(tr_lower(match.group(0))).strip()
            push(code.replace(" ", ""))
            separated = re.sub(r"[-_]+", " ", code).strip()
            if separated != code:
                push(separated)

    found.sort(key=len, reverse=True)
    return tuple(found)


# --------------------------------------------------------------------------
# Stemming
# --------------------------------------------------------------------------

#: Minimum surviving stem length.  Below this, stems collide with
#: everything ("ac", "ag", "ek").
MIN_STEM = 3

# Suffixes are matched against the ASCII-folded token, so only folded
# spellings appear here ("-mıyor" folds to "miyor", "-müyor" to "muyor").
# Ordered longest-first; the stripper loops to a fixed point so stacked
# suffixes ("monitor-ler-imiz-den") peel off one at a time.
_SUFFIXES: tuple[str, ...] = (
    # verb inflection -- fault descriptions are almost always verbs
    "miyorlar", "muyorlar", "iyorlar", "uyorlar",
    "miyorum", "muyorum", "iyorum", "uyorum",
    "miyoruz", "muyoruz", "iyoruz", "uyoruz",
    "amiyor", "emiyor",
    "mistir", "mustur", "misti", "musti",
    "miyor", "muyor", "iyor", "uyor",
    "mamis", "memis",
    "madigi", "medigi",
    "acagi", "ecegi", "acak", "ecek",
    "madi", "medi", "meli", "mali",
    "mez", "maz", "mis", "mus",
    "mak", "mek",
    # noun inflection
    "larindan", "lerinden", "larimiz", "lerimiz", "lariniz", "leriniz",
    "lardan", "lerden", "larda", "lerde", "larin", "lerin",
    "lari", "leri", "lara", "lere",
    "lar", "ler",
    "imizin", "umuzun", "imiz", "umuz", "iniz", "unuz",
    "sinden", "sinin", "sinda", "sinde", "sini", "sine",
    "ndan", "nden", "nin", "nun", "nda", "nde",
    "dan", "den", "tan", "ten",
    "yla", "yle",
    "da", "de", "ta", "te",
    "in", "un", "im", "um",
    # Third-person possessive. Without it "bataryasi" never reaches
    # "batarya", so the lexicon entry and every alias built on it stay out
    # of range -- the query and the content simply never meet.
    "si", "su",
    # derivation
    "sizlik", "suzluk", "cilik", "culuk",
    "siz", "suz", "lik", "luk", "lig", "lug",
    "ci", "cu", "li", "lu",
    # bare vowels (accusative / dative / possessive)
    "ma", "me", "yi", "yu", "ya", "ye",
    "i", "u", "e", "a",
)

# Words that must never be stemmed: short IT terms whose tails look like
# Turkish suffixes.  Without this list "veri" loses its "i" and "kablo"
# collapses past anything useful.
_NO_STEM: frozenset[str] = frozenset(
    {
        "usb", "hdmi", "vga", "dvi", "dns", "dhcp", "vpn", "lan", "wan", "wifi",
        "bios", "uefi", "raid", "ahci", "sata", "nvme", "ssd", "hdd", "ram",
        "cpu", "gpu", "psu", "osd", "led", "lcd", "kvm", "rdp", "smb", "nfs",
        "ftp", "ssh", "tls", "ssl", "mfa", "sso", "ntp", "gpo", "pdf", "exe",
        "dll", "url", "api", "pin", "otp", "nic", "poe", "mac", "qr", "cmos",
        "dock", "hub", "port", "mail", "modem", "router", "switch", "printer",
        "driver", "reset", "boot", "error", "signal", "display", "disk",
        "office", "outlook", "teams", "windows", "macos", "linux", "chrome",
        "veri", "kablo", "fare", "ekran", "yazi", "ses", "guc", "sifre",
        "hata", "kart", "agi", "yazici",
    }
)

_VOWELS = frozenset("aeiou")


def stem(token: str) -> str:
    """Strip Turkish inflectional suffixes from an ASCII-folded token.

    Runs to a fixed point (at most four rounds) and refuses to cut below
    :data:`MIN_STEM`.  Over-stripping is tolerable because the search layer
    queries with a prefix wildcard; under-stripping is not, because a query
    stem longer than the indexed one can never match.
    """
    if token in _NO_STEM or len(token) <= MIN_STEM:
        return token

    for _ in range(4):
        if token in _NO_STEM:
            break

        # Longest-first is the usual heuristic, but on its own it cuts too
        # deep whenever two suffixes overlap: "ekranda" matches "-nda"
        # before "-da", giving "ekra" and then "ekr", while plain "ekran"
        # is protected and stays whole.  The two forms would then never
        # meet, which is the exact failure this module exists to avoid.
        # So every candidate is considered, and one that lands on a known
        # word always wins over one that merely removes more characters.
        first_valid: str | None = None
        for suffix in _SUFFIXES:
            if not token.endswith(suffix):
                continue
            candidate = token[: -len(suffix)]
            if len(candidate) < MIN_STEM:
                continue
            # A stem must keep a vowel; a cut leaving a bare consonant
            # cluster is always wrong.
            if not (_VOWELS & set(candidate)):
                continue
            if candidate in _NO_STEM:
                return candidate
            if first_valid is None:
                first_valid = candidate

        if first_valid is None:
            break
        token = first_valid
    return token


# --------------------------------------------------------------------------
# Tokenisation and canonical keys
# --------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    """Split normalised text into tokens."""
    return [tok for tok in strip_punctuation(text).split() if tok]


def normalize_term(term: str) -> str:
    """Canonical surface form of an alias or query.

    Lowercased with Turkish rules, punctuation-free, ASCII-folded and
    whitespace-collapsed.  Stored in ``aliases.term_norm``; this is what an
    *exact* alias match compares against.
    """
    return strip_punctuation(ascii_fold(tr_lower(unicodedata.normalize("NFC", term or ""))))


def term_key(term: str) -> str:
    """Order-insensitive stemmed key for an alias or query.

    "monitör çalışmıyor" and "çalışmıyor monitör" produce the same key, so
    word order never costs a match.  Stored in ``aliases.term_key``.
    """
    return " ".join(sorted({stem(tok) for tok in tokenize(normalize_term(term))}))


# --------------------------------------------------------------------------
# Negation
# --------------------------------------------------------------------------

# Turkish marks negation inside the verb ("çalış-MI-yor"), so it cannot be
# caught with a stopword list.  Nearly every help-desk query is negative,
# which makes the *absence* of negation the informative case ("yazıcı çok
# yavaş" versus "yazıcı yazdırmıyor").
_NEGATION_MARKERS: tuple[str, ...] = (
    "miyor", "muyor", "mez", "maz", "medi", "madi", "memis", "mamis",
    "mayan", "meyen",
)
_NEGATION_WORDS: frozenset[str] = frozenset(
    {"yok", "degil", "hic", "hicbir", "olmuyor", "olmadi"}
)


def is_negated(folded_tokens: Sequence[str]) -> bool:
    """Whether any token carries a negation marker."""
    for token in folded_tokens:
        if token in _NEGATION_WORDS:
            return True
        if any(marker in token for marker in _NEGATION_MARKERS):
            return True
    return False


#: Kept as a private alias so existing call sites read unchanged.
_is_negated = is_negated


# --------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Normalized:
    """Everything the matcher needs to know about one piece of text."""

    raw: str
    norm: str
    tokens: tuple[str, ...] = ()
    stems: tuple[str, ...] = ()
    expanded: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    error_codes: tuple[str, ...] = ()
    negated: bool = False
    key: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.tokens and not self.error_codes

    def fts_query(self) -> str:
        """Build an FTS5 MATCH expression from the expanded stems.

        Every term long enough to be unambiguous gets a trailing ``*`` so
        residual Turkish suffixes on the indexed side still match.  Terms
        are OR-ed: a four-word query must not require all four to appear.
        """
        terms = {t for t in self.expanded if len(t) >= 2}
        terms.update(self.error_codes)
        parts: list[str] = []
        for term in sorted(terms):
            safe = re.sub(r"\W", "", term)
            if not safe:
                continue
            parts.append(f'"{safe}"*' if len(safe) >= 3 else f'"{safe}"')
        return " OR ".join(parts)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def normalize_query(text: str, lexicon=None) -> Normalized:
    """Run the full pipeline over a free-text query.

    ``lexicon`` is optional so the normaliser stays unit-testable and
    usable before any content is compiled; without it, stopword removal,
    synonym expansion and intent inference are skipped.
    """
    raw = text or ""
    codes = extract_error_codes(raw)

    norm = normalize_term(raw)
    tokens = tokenize(norm)
    negated = _is_negated(tokens)

    if lexicon is not None:
        tokens = [t for t in tokens if not lexicon.is_stopword(t)]

    stems = tuple(stem(t) for t in tokens)

    if lexicon is not None:
        expanded = tuple(lexicon.expand(stems))
        intents = tuple(lexicon.intents(tokens, stems))
    else:
        expanded = stems
        intents = ()

    return Normalized(
        raw=raw,
        norm=norm,
        tokens=tuple(tokens),
        stems=stems,
        expanded=expanded,
        intents=intents,
        error_codes=codes,
        negated=negated,
        key=" ".join(sorted(set(stems))),
    )
