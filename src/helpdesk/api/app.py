"""The local HTTP application.

One FastAPI app serving both the JSON API and the server-rendered UI.  The
UI is intentionally plain: server-rendered HTML with a small amount of
vanilla JavaScript for keyboard handling, no build step, no framework, no
bundler.  A technician on a phone call needs the page to be on screen in
under a second; a single-page app would buy nothing and cost a toolchain.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..config import Settings
from ..config import settings as default_settings
from ..core.lexicon import Lexicon
from ..core.matcher import Matcher
from ..db.repo import Database
from .security import SecurityMiddleware

__all__ = ["create_app"]

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class Locale:
    """UI strings, addressable as ``t.key_name`` or ``t['key_name']``.

    Deliberately not a dict.  Jinja2 resolves ``t.copy`` by trying
    ``getattr`` first, so a plain dict hands the template its built-in
    ``copy`` *method* and the page renders
    ``<built-in method copy of dict...>`` where the button label should be.
    The same trap is waiting for ``get``, ``items``, ``keys`` and
    ``values``.  A plain object has none of those attributes, so every
    lookup falls through to ``__getattr__`` and reaches the translation.

    A missing key returns an empty string, which is falsy, so the
    ``{{ t.key or "fallback" }}`` idiom used throughout the templates
    keeps working.
    """

    __slots__ = ("_data",)

    def __init__(self, data: dict[str, str]) -> None:
        object.__setattr__(self, "_data", dict(data))

    def __getattr__(self, name: str) -> str:
        if name.startswith("__"):
            raise AttributeError(name)
        return self._data.get(name, "")

    def __getitem__(self, key: str) -> str:
        return self._data.get(key, "")

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def as_dict(self) -> dict[str, str]:
        return dict(self._data)


def _load_locale(lang: str) -> Locale:
    path = WEB_DIR / "locales" / f"{lang}.json"
    if not path.is_file():
        path = WEB_DIR / "locales" / "tr.json"
    try:
        return Locale(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Locale({})


def _render_markdown(text: str | None) -> str:
    """Render runbook Markdown with HTML disabled.

    Content is data.  ``html=False`` means a runbook that contains a
    ``<script>`` tag renders it as visible text instead of executing it --
    the first line of defence, with the CSP behind it as the second.
    """
    if not text:
        return ""
    try:
        from markdown_it import MarkdownIt
    except ImportError:  # pragma: no cover - the web extra is optional
        from html import escape
        return f"<pre>{escape(text)}</pre>"
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable("table")
    return md.render(text)


def create_app(cfg: Settings | None = None, *, token: str | None = None) -> FastAPI:
    cfg = cfg or default_settings
    token = token or cfg.read_token() or cfg.issue_token()

    app = FastAPI(
        title="helpdesk-guide",
        version=__version__,
        docs_url=None,     # no interactive docs: another surface, no benefit here
        redoc_url=None,
        openapi_url=None,
    )

    db = Database(cfg.db_path)
    # A database compiled by an older release may be missing columns that
    # only the telemetry tables carry; those tables survive recompilation,
    # so serving is the other place the migration has to run.
    if db.is_initialised:
        db.migrate()
    lexicon = Lexicon.load(cfg.content_roots, lang=cfg.lang)

    app.state.cfg = cfg
    app.state.db = db
    app.state.lexicon = lexicon
    app.state.matcher = Matcher(db, lexicon, lang=cfg.lang)
    app.state.locale = _load_locale(cfg.lang)

    app.add_middleware(
        SecurityMiddleware,
        token=token,
        allowed_hosts={
            f"{cfg.host}:{cfg.port}",
            f"localhost:{cfg.port}",
            f"127.0.0.1:{cfg.port}",
        },
    )

    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    templates.env.globals.update(
        version=__version__,
        lang=cfg.lang,
        t=app.state.locale,
    )
    templates.env.filters["markdown"] = _render_markdown
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    # Media lives under content/, not under the package, and must never be
    # allowed to escape that directory (section 12.1, path traversal).
    media_root = cfg.media_root
    if media_root.is_dir():
        app.mount("/media", StaticFiles(directory=str(media_root)), name="media")

    from .routes import content as content_routes
    from .routes import reports as report_routes
    from .routes import search as search_routes
    from .routes import session as session_routes

    app.include_router(search_routes.router)
    app.include_router(session_routes.router)
    app.include_router(content_routes.router)
    app.include_router(report_routes.router)

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Any) -> HTMLResponse:
        if request.url.path.startswith("/api/"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "not_found"}, status_code=404)
        return templates.TemplateResponse(
            request, "error.html",
            {"code": 404, "message": app.state.locale["not_found"] or "Bulunamadı"},
            status_code=404,
        )

    return app
