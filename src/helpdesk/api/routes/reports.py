"""Report routes: what to write next, and what to fix.

Every report answers one question and implies one action.  There is no
per-technician view and there will not be one -- the telemetry exists to
improve the content (design doc section 11).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from ...core import reports as reports_mod

router = APIRouter()

REPORT_NAMES = (
    "knowledge_gaps",
    "dead_ends",
    "escalation_hotspots",
    "skipped_steps",
    "slow_steps",
    "stale_content",
    "root_cause_distribution",
)


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request) -> HTMLResponse:
    db = request.app.state.db
    return request.app.state.templates.TemplateResponse(
        request,
        "reports.html",
        {
            "coverage": reports_mod.coverage(db),
            "reports": {name: getattr(reports_mod, name)(db) for name in REPORT_NAMES},
        },
    )


@router.get("/api/reports")
async def api_reports(request: Request, name: str = Query("")) -> dict[str, Any]:
    db = request.app.state.db
    if not name:
        return reports_mod.all_reports(db)
    if name not in REPORT_NAMES and name != "coverage":
        raise HTTPException(status_code=404, detail=f"unknown report {name}")
    return {name: getattr(reports_mod, name)(db)}
