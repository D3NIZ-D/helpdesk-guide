"""Session routes: starting, walking and closing a runbook.

Walk state lives in the database rather than in process memory, so a
browser refresh, a second tab or a server restart never loses a call that
is already six steps in.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from ...core.engine import Engine, EngineError, WalkState
from ...core.session import SessionRecorder

router = APIRouter()

_ALLOWED_CONTEXT = {"os", "asset", "dock", "conn", "site"}


def _recorder(request: Request) -> SessionRecorder:
    return SessionRecorder(request.app.state.db, agent_ref=request.app.state.cfg.agent_ref)


def _engine_for(request: Request, code: str) -> tuple[Engine, dict[str, Any]]:
    record = request.app.state.db.get_record(code, include_unpublished=True)
    if not record:
        raise HTTPException(status_code=404, detail=f"no record {code}")
    try:
        return Engine(record), record
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _load(request: Request, session_id: int) -> tuple[Engine, WalkState, dict[str, Any], SessionRecorder]:
    recorder = _recorder(request)
    state = recorder.load_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="session not found or already closed")
    engine, record = _engine_for(request, state.code)
    return engine, state, record, recorder


def _outcome_for(node_type: str) -> str:
    return {"resolution": "resolved", "escalation": "escalated"}.get(node_type, "abandoned")


# --------------------------------------------------------------------------
# Start
# --------------------------------------------------------------------------

@router.post("/run/{code}")
async def start_walk(request: Request, code: str, q: str = Form("")) -> RedirectResponse:
    engine, record = _engine_for(request, code)
    context = {k: v for k, v in request.query_params.items() if k in _ALLOWED_CONTEXT and v}

    try:
        state = engine.start(context)
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recorder = _recorder(request)
    recorder.start(
        query_raw=q or record["title"],
        query_norm=(q or record["title"]).lower(),
        record=record,
        state=state,
        match_method="manual" if not q else "ui",
    )
    recorder.save_state(state)
    return RedirectResponse(f"/s/{state.session_id}", status_code=303)


# --------------------------------------------------------------------------
# Walk
# --------------------------------------------------------------------------

@router.get("/s/{session_id}", response_class=HTMLResponse)
async def show_step(request: Request, session_id: int) -> HTMLResponse:
    engine, state, record, _ = _load(request, session_id)
    view = engine.view(state)
    return request.app.state.templates.TemplateResponse(
        request,
        "walk.html",
        {
            "record": record,
            "view": view,
            "state": state,
            "steps": engine.steps_taken(state),
            "can_go_back": len(state.path) > 1,
        },
    )


@router.post("/s/{session_id}/answer")
async def answer(
    request: Request,
    session_id: int,
    label: str = Form(...),
    skip: str = Form(""),
) -> RedirectResponse:
    engine, state, _, recorder = _load(request, session_id)
    view = engine.view(state)
    skipped = bool(skip)

    recorder.record_step(
        state,
        node_key=view.key,
        node_title=view.title,
        answer=label,
        skipped=skipped,
    )
    try:
        state = engine.skip(state, label) if skipped else engine.answer(state, label)
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    recorder.save_state(state)

    # A terminal node closes the session immediately: an unclosed session
    # would otherwise be counted as abandoned and distort every report.
    final = engine.view(state)
    if final.is_terminal:
        recorder.record_step(
            state, node_key=final.key, node_title=final.title, answer=None
        )
        recorder.finish(
            state,
            outcome=_outcome_for(final.type),
            root_cause=final.root_cause,
            resolution_node=final.key,
        )
    return RedirectResponse(f"/s/{session_id}", status_code=303)


@router.post("/s/{session_id}/back")
async def go_back(request: Request, session_id: int) -> RedirectResponse:
    engine, state, _, recorder = _load(request, session_id)
    state = engine.back(state)
    recorder.save_state(state)
    return RedirectResponse(f"/s/{session_id}", status_code=303)


@router.post("/s/{session_id}/abandon")
async def abandon(request: Request, session_id: int) -> RedirectResponse:
    _, state, _, recorder = _load(request, session_id)
    recorder.finish(state, outcome="abandoned")
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------
# Summary and feedback
# --------------------------------------------------------------------------

@router.get("/s/{session_id}/summary", response_class=PlainTextResponse)
async def summary(request: Request, session_id: int) -> str:
    engine, state, _, recorder = _load(request, session_id)
    view = engine.view(state)
    return recorder.summary(
        engine,
        state,
        query_raw=recorder.query_of(session_id),
        outcome=_outcome_for(view.type),
        lang=request.app.state.cfg.lang,
    )


@router.post("/api/feedback")
async def feedback(request: Request) -> dict[str, Any]:
    payload = await request.json()
    kind = str(payload.get("kind", ""))
    if kind not in {"worked", "did_not_work", "content_error"}:
        raise HTTPException(status_code=400, detail="unknown feedback kind")

    db = request.app.state.db
    code = str(payload.get("code", "")).upper()
    record_id = db.scalar("SELECT id FROM records WHERE code = ?", (code,)) if code else None

    from datetime import datetime
    feedback_id = db.add_feedback(
        record_id,
        payload.get("node_key"),
        kind,
        (str(payload.get("note", "")) or None),
        datetime.now().isoformat(timespec="seconds"),
    )
    return {"ok": True, "id": feedback_id}


# --------------------------------------------------------------------------
# JSON API for the same lifecycle
# --------------------------------------------------------------------------

@router.post("/api/session")
async def api_start(request: Request) -> dict[str, Any]:
    payload = await request.json()
    code = str(payload.get("code", "")).upper()
    engine, record = _engine_for(request, code)
    context = {k: v for k, v in (payload.get("context") or {}).items() if k in _ALLOWED_CONTEXT}

    state = engine.start(context)
    recorder = _recorder(request)
    query = str(payload.get("query") or record["title"])
    recorder.start(
        query_raw=query, query_norm=query.lower(), record=record,
        state=state, match_method="api",
    )
    recorder.save_state(state)
    return {"state": state.as_dict(), "node": engine.view(state).as_dict()}


@router.get("/api/session/{session_id}")
async def api_state(request: Request, session_id: int) -> dict[str, Any]:
    engine, state, _, _ = _load(request, session_id)
    return {"state": state.as_dict(), "node": engine.view(state).as_dict()}


@router.post("/api/session/{session_id}/answer")
async def api_answer(request: Request, session_id: int) -> dict[str, Any]:
    payload = await request.json()
    engine, state, _, recorder = _load(request, session_id)
    view = engine.view(state)
    skipped = bool(payload.get("skip"))
    label = str(payload.get("label", ""))

    recorder.record_step(
        state, node_key=view.key, node_title=view.title,
        answer=label, skipped=skipped,
    )
    try:
        state = engine.skip(state, label) if skipped else engine.answer(state, label)
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    recorder.save_state(state)

    final = engine.view(state)
    if final.is_terminal:
        recorder.finish(
            state, outcome=_outcome_for(final.type),
            root_cause=final.root_cause, resolution_node=final.key,
        )
    return {"state": state.as_dict(), "node": final.as_dict()}
