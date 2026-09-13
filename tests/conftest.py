"""Shared fixtures.

Every test that needs content compiles the *real* ``content/`` tree into a
temporary database.  Using fixture content instead would let the shipped
runbooks drift away from what the tests assert, and the shipped runbooks
are the part users actually depend on.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from helpdesk.config import Settings
from helpdesk.content.compiler import compile_content
from helpdesk.core.lexicon import Lexicon
from helpdesk.core.matcher import Matcher
from helpdesk.db.repo import Database

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT_ROOT = REPO_ROOT / "content"


@pytest.fixture(scope="session")
def content_root() -> Path:
    assert CONTENT_ROOT.is_dir(), "content/ is missing; run from a checkout"
    return CONTENT_ROOT


@pytest.fixture(scope="session")
def lexicon(content_root: Path) -> Lexicon:
    return Lexicon.load([content_root], lang="tr")


@pytest.fixture(scope="session")
def compiled_db(tmp_path_factory, content_root: Path) -> Database:
    """The real content, compiled once for the whole test session."""
    path = tmp_path_factory.mktemp("db") / "helpdesk.db"
    db = Database(path)
    result = compile_content(db, [content_root], strict=False)
    assert result.written, (
        "shipped content failed to compile:\n"
        + "\n".join(str(e) for e in result.load_errors)
        + "\n".join(str(f) for f in result.report.errors)
    )
    return db


@pytest.fixture(scope="session")
def matcher(compiled_db: Database, lexicon: Lexicon) -> Matcher:
    return Matcher(compiled_db, lexicon, lang="tr")


@pytest.fixture
def fresh_db(tmp_path) -> Database:
    """An empty, initialised database for tests that write."""
    db = Database(tmp_path / "fresh.db")
    db.initialise()
    return db


@pytest.fixture
def temp_content(tmp_path, content_root: Path) -> Path:
    """A writable copy of the content tree, for compiler/validator tests."""
    target = tmp_path / "content"
    shutil.copytree(content_root, target)
    return target


@pytest.fixture
def client(tmp_path, content_root: Path, monkeypatch):
    """A TestClient with a token already exchanged for a cookie."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from helpdesk.api.app import create_app

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HELPDESK_HOME", str(home))
    monkeypatch.setenv("HELPDESK_CONTENT", str(content_root))

    cfg = Settings(home=home)
    db = Database(cfg.db_path)
    assert compile_content(db, [content_root], strict=False).written

    token = cfg.issue_token()
    test_client = TestClient(create_app(cfg, token=token))
    test_client.get(f"/?t={token}")          # exchange token for the cookie
    return test_client
