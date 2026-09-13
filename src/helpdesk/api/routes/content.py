"""Content routes: browsing records that are not walked.

Guides and reference cards have no tree to execute, so they render as a
page.  They are still full participants in search and still collect "did
this work?" feedback -- a reference card that three people mark as wrong
belongs in the review queue (design doc section 22.5).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from ...content.tree import render_tree

router = APIRouter()


@router.get("/r/{code}", response_class=HTMLResponse)
async def show_record(request: Request, code: str) -> HTMLResponse:
    record = request.app.state.db.get_record(code, include_unpublished=True)
    if not record:
        raise HTTPException(status_code=404, detail=f"no record {code}")

    related = [
        r for r in (
            request.app.state.db.get_record(target, include_unpublished=True)
            for target in filter(None, [record.get("related_runbook"), *record.get("related", [])])
        ) if r
    ]
    return request.app.state.templates.TemplateResponse(
        request, "record.html", {"record": record, "related": related}
    )


@router.get("/r/{code}/tree", response_class=PlainTextResponse)
async def record_tree(request: Request, code: str) -> str:
    record = request.app.state.db.get_record(code, include_unpublished=True)
    if not record:
        raise HTTPException(status_code=404, detail=f"no record {code}")
    if record["tier"] != "runbook":
        return f"{record['code']} is a {record['tier']}; it has no decision tree."
    return render_tree(record["nodes"], record["entry_node"])


@router.get("/browse", response_class=HTMLResponse)
async def browse(
    request: Request,
    category: str = Query(""),
    tier: str = Query(""),
) -> HTMLResponse:
    db = request.app.state.db
    return request.app.state.templates.TemplateResponse(
        request,
        "browse.html",
        {
            "records": db.list_records(
                category=category or None, tier=tier or None, limit=500
            ),
            "categories": db.list_categories(),
            "category": category,
            "tier": tier,
        },
    )


@router.get("/api/records")
async def api_records(
    request: Request,
    category: str = Query(""),
    tier: str = Query(""),
) -> dict[str, Any]:
    records = request.app.state.db.list_records(
        category=category or None, tier=tier or None, limit=1000
    )
    return {"count": len(records), "records": records}


@router.get("/api/records/{code}")
async def api_record(request: Request, code: str) -> dict[str, Any]:
    record = request.app.state.db.get_record(code, include_unpublished=True)
    if not record:
        raise HTTPException(status_code=404, detail=f"no record {code}")
    return record
