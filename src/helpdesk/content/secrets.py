"""Secret scanning for content (design doc section 12.2).

Knowledge bases are a classic leak vector: someone writes "log in with
Administrator / Summer2024!" into a troubleshooting note, the note gets
committed, and the repository is public.  This scanner is a hard build
gate, not a linting suggestion -- a hit stops ``helpdesk compile``.

Runbooks reference credentials by *vault entry name* instead::

    body_md: |
      Sign in with the local administrator account.
      Credential: vault entry **"Local-Admin-Workstations"**.

The scanner is deliberately noisy in the direction of false positives.
A false positive costs one ``# noqa: secret`` comment; a false negative
costs a credential rotation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

__all__ = ["ALLOW_MARKER", "SecretHit", "scan_many", "scan_text"]

#: Put this marker on the same line to acknowledge a reviewed false
#: positive.  It is intentionally verbose so it shows up in code review.
ALLOW_MARKER = "helpdesk:allow-secret"


@dataclass(frozen=True)
class SecretHit:
    rule: str
    line_no: int
    excerpt: str
    hint: str

    def __str__(self) -> str:
        return f"line {self.line_no}: {self.rule} -- {self.hint}  [{self.excerpt}]"


def _redact(text: str, keep: int = 4) -> str:
    """Never echo a suspected secret back in full, not even into CI logs."""
    text = text.strip()
    if len(text) <= keep * 2:
        return text[:keep] + "..."
    return f"{text[:keep]}...{text[-keep:]}"


# (rule name, pattern, hint, high_confidence)
#
# ``high_confidence`` rules match a structure that is a credential and
# nothing else -- an AWS key id, a PEM block, a JWT.  Those skip the
# safe-context check below, because a line reading "for example,
# AKIA..." is not a false positive; it is a leaked key with an excuse
# next to it.
#
# Turkish inflects the keyword too: "parolası:", "şifresi:", "parolam:".
# A ``\w*`` after each keyword is what makes the gate work on Turkish
# prose rather than only on config-file syntax.
_PATTERNS: list[tuple[str, re.Pattern[str], str, bool]] = [
    (
        "password-assignment",
        re.compile(
            r"(?:password|passwd|pwd|parola|şifre|sifre|pass)\w*\s*[:=]\s*\S{4,}",
            re.IGNORECASE,
        ),
        "a literal password; reference a vault entry name instead",
        False,
    ),
    (
        "aws-access-key",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "an AWS access key id",
        True,
    ),
    (
        "private-key-block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "an embedded private key",
        True,
    ),
    (
        "bearer-token",
        re.compile(r"\b(?:bearer|authorization)\s*[:=]?\s*[A-Za-z0-9._\-]{24,}", re.IGNORECASE),
        "an API token",
        False,
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
        "a JSON Web Token",
        True,
    ),
    (
        "api-key-assignment",
        re.compile(
            r"(?:api[_\- ]?key|apikey|secret|token|client[_\- ]?secret)\w*\s*[:=]\s*"
            r"[A-Za-z0-9._\-]{16,}",
            re.IGNORECASE,
        ),
        "an API key or client secret",
        False,
    ),
    (
        "psk",
        re.compile(
            r"(?:psk|pre[_\- ]?shared[_\- ]?key|onpaylasimli|ön ?paylaşımlı\w*)\w*\s*[:=]\s*\S{6,}",
            re.IGNORECASE,
        ),
        "a VPN pre-shared key",
        False,
    ),
    (
        "license-key",
        re.compile(r"\b[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}\b"),
        "a product/licence key",
        False,
    ),
    (
        "connection-string",
        re.compile(r"\b[a-z]{2,12}://[^\s:@/]{2,}:[^\s:@/]{3,}@", re.IGNORECASE),
        "credentials embedded in a URL",
        True,
    ),
    (
        "long-base64",
        re.compile(r"\b[A-Za-z0-9+/]{60,}={0,2}\b"),
        "a long base64 blob that may encode a credential",
        False,
    ),
    (
        "slack-token",
        re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
        "a Slack token",
        True,
    ),
    (
        "github-token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        "a GitHub token",
        True,
    ),
]

#: Words that make an *assignment-shaped* hit obviously benign -- these
#: are the phrasings contributors are actively told to use.  This never
#: suppresses a high-confidence rule.
_SAFE_CONTEXT = re.compile(
    r"vault|keepass|bitwarden|vaultwarden|1password|lastpass|kasa|parola kasas|"
    r"<[^>]{2,}>|\{\{[^}]+\}\}|\.\.\.|xxx+|\*\*\*+|placeholder|example|örnek|ornek",
    re.IGNORECASE,
)


def scan_text(text: str, *, strict: bool = True) -> list[SecretHit]:
    """Scan one blob of content for credential-shaped strings."""
    hits: list[SecretHit] = []
    if not text:
        return hits
    for line_no, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for rule, pattern, hint, high_confidence in _PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            # "Credential: vault entry Local-Admin-Workstations" is the
            # documented safe phrasing and must not trip the gate -- but
            # only assignment-shaped rules can be defused this way.
            if not high_confidence and _SAFE_CONTEXT.search(line):
                continue
            if rule == "long-base64" and not strict:
                continue
            hits.append(
                SecretHit(
                    rule=rule,
                    line_no=line_no,
                    excerpt=_redact(match.group(0)),
                    hint=hint,
                )
            )
    return hits


def scan_many(blobs: Iterable[tuple[str, str]], *, strict: bool = True) -> Iterator[tuple[str, SecretHit]]:
    """Scan ``(label, text)`` pairs, yielding ``(label, hit)``."""
    for label, text in blobs:
        for hit in scan_text(text, strict=strict):
            yield label, hit
