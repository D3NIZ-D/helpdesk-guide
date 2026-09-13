"""Runtime configuration and filesystem layout.

Paths follow platform conventions so that an installed copy keeps its
database out of the source tree, while a checkout used for development
keeps everything local and disposable.  ``HELPDESK_HOME`` overrides both.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Settings", "default_home", "project_root", "settings"]

APP_NAME = "helpdesk-guide"


#: Core content shipped inside the wheel (see the hatch force-include in
#: pyproject.toml).  This is what makes ``pipx install helpdesk-guide &&
#: helpdesk compile`` work: an installed copy has no repository checkout to
#: read content from, and a tool that installs but has nothing to search
#: fails the five-minute rule on the first command.
BUNDLED_CONTENT = Path(__file__).resolve().parent / "_bundled_content"


def project_root() -> Path:
    """The repository root when running from a checkout, else the cwd.

    Detected by walking up from this file looking for a ``content``
    directory next to ``pyproject.toml``.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "content").is_dir():
            return parent
    return Path.cwd()


def is_checkout() -> bool:
    """Whether we are running from a source checkout rather than an install."""
    root = project_root()
    return (root / "pyproject.toml").is_file() and (root / "content").is_dir()


def default_home() -> Path:
    """Where the compiled database and logs live."""
    override = os.environ.get("HELPDESK_HOME")
    if override:
        return Path(override).expanduser()

    if is_checkout():
        # Development checkout: keep the build next to the source so a
        # stale database is obvious and `git clean` removes it.
        return project_root() / "data"

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / APP_NAME
    if os.uname().sysname == "Darwin":  # pragma: no cover - platform specific
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_NAME


@dataclass
class Settings:
    """Everything the application needs to know about its environment."""

    home: Path = field(default_factory=default_home)
    root: Path = field(default_factory=project_root)
    lang: str = field(default_factory=lambda: os.environ.get("HELPDESK_LANG", "tr"))
    host: str = "127.0.0.1"
    port: int = field(default_factory=lambda: int(os.environ.get("HELPDESK_PORT", "8756")))
    #: Data retention for local telemetry, in days (section 12.3).
    retention_days: int = field(
        default_factory=lambda: int(os.environ.get("HELPDESK_RETENTION_DAYS", "365"))
    )
    agent_ref: str | None = field(default_factory=lambda: os.environ.get("HELPDESK_AGENT_REF"))

    def __post_init__(self) -> None:
        self.home = Path(self.home)
        self.root = Path(self.root)

    # -- paths -----------------------------------------------------------

    @property
    def db_path(self) -> Path:
        override = os.environ.get("HELPDESK_DB")
        return Path(override).expanduser() if override else self.home / "helpdesk.db"

    @property
    def content_root(self) -> Path:
        """Public content: the checkout's ``content/``, or the bundled copy.

        An installed copy has no repository next to it, so it falls back to
        the content shipped inside the wheel.  A checkout always wins, so
        editing a runbook and recompiling behaves the way an author expects.
        """
        override = os.environ.get("HELPDESK_CONTENT")
        if override:
            return Path(override).expanduser()
        local = self.root / "content"
        if local.is_dir():
            return local
        return BUNDLED_CONTENT

    @property
    def local_content_root(self) -> Path:
        """Site-private content, git-ignored (section 19.1)."""
        override = os.environ.get("HELPDESK_CONTENT_LOCAL")
        return Path(override).expanduser() if override else self.root / "content-local"

    @property
    def content_roots(self) -> list[Path]:
        """Content roots in compile order; later roots override earlier ones."""
        roots = [self.content_root]
        local = self.local_content_root
        if local.is_dir() and local != self.content_root:
            roots.append(local)
        return roots

    @property
    def media_root(self) -> Path:
        return self.content_root / "media"

    @property
    def token_path(self) -> Path:
        return self.home / "session.token"

    # -- server token ----------------------------------------------------

    def issue_token(self) -> str:
        """Create a fresh loopback access token.

        The local server binds to 127.0.0.1, but that is not by itself a
        boundary: any process on the machine, including a web page's
        JavaScript in a browser on the same host, can reach it.  The token
        in the launch URL is what stops a random page from driving the API
        (section 12.1).
        """
        token = secrets.token_urlsafe(32)
        self.home.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(token, encoding="utf-8")
        # Best effort: POSIX permissions are a no-op on some filesystems.
        with contextlib.suppress(OSError):
            os.chmod(self.token_path, 0o600)
        return token

    def read_token(self) -> str | None:
        try:
            return self.token_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None


#: Process-wide settings.  Tests construct their own instance instead of
#: mutating this one.
settings = Settings()
