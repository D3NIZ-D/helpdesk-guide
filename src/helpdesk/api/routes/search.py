"""Search routes: the entry point for every call."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ...core.session import SessionRecorder

router = APIRouter()


def _context_from(request: Request) -> dict[str, Any]:
    """Session context passed as query parameters (``?os=windows``)."""
    allowed = {"os", "asset", "dock", "conn", "site"}
    return {k: v for k, v in request.query_params.items() if k in allowed and v}


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, q: str = Query("", alias="q")) -> HTMLResponse:
    app = request.app
    templates = app.state.templates
    result = None

    if q.strip():
        result = app.state.matcher.search(
            q, limit=8, context=_context_from(request)
        )
        # A search the content could not answer is the most actionable
        # signal the tool produces, so it is recorded from the UI too
        # (section 11.1).  The best score is stored alongside it, so an
        # editor can tell "nothing at all" from "nearly matched".
        if result.is_knowledge_gap:
            SessionRecorder(app.state.db).record_gap(
                result.query.raw, result.query.norm, result.best_score
            )

    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "q": q,
            "result": result,
            "categories": app.state.db.list_categories(),
            "recent": app.state.db.query(
                "SELECT DISTINCT r.code, r.title, r.tier FROM sessions s "
                "JOIN records r ON r.id = s.record_id ORDER BY s.started_at DESC LIMIT 5"
            ),
            "build": app.state.db.build_info(),
        },
    )


@router.get("/api/search")
async def api_search(
    request: Request,
    q: str = Query(..., min_length=1),
    limit: int = Query(5, ge=1, le=25),
) -> dict[str, Any]:
    result = request.app.state.matcher.search(q, limit=limit, context=_context_from(request))
    if result.is_knowledge_gap:
        SessionRecorder(request.app.state.db).record_gap(
            result.query.raw, result.query.norm, result.best_score
        )
    return result.as_dict()
