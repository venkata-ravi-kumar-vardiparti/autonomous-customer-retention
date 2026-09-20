"""The FastAPI application entry point: wires Phase 7 (recommend, via
api/routes.py's use of orchestration/bounded.py) and Phase 8 (approve,
execute) into one runnable app, plus one small addition of its own -
GET /traces/{trace_id} - so the Phase 9 UI can render the trace panel's
per-agent latency/tokens/cost against a live backend, not just the static
fixture.

Run with: uvicorn churnguard.api.app:app --reload
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from churnguard.api.routes import router
from churnguard.telemetry.tracer import export_trace

app = FastAPI(title="ChurnGuard", version="0.1.0")
app.include_router(router)


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict[str, Any]:
    """Read-only echo of telemetry/tracer.py's own process-local span store.

    Not part of the Phase 8 brief's three endpoints - added here (not in
    api/routes.py, which is Phase 8's) because the Phase 9 UI's trace panel
    needs it and nothing about it touches approval/execution. Returns an
    empty span list for an unknown trace_id rather than a 404: a trace with
    no recorded spans (e.g. one from before this process started) is a
    valid, if uninteresting, answer - not an error.
    """
    return export_trace(trace_id)


__all__ = ["app"]
