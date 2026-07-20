"""FastAPI webhook server for Alertmanager → AutoSRE."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from autosre.config import AutoSREConfig
from autosre.logging import TraceContext, setup_logging
from autosre.store import IncidentStore

logger = logging.getLogger(__name__)

# Serial incident queue: only one agent run at a time.
_queue: Optional[asyncio.Queue] = None
_worker_task: Optional[asyncio.Task] = None


def _map_alertmanager_to_scenario(payload: dict) -> Optional[str]:
    """Best-effort map of an Alertmanager payload onto a known scenario name."""
    from scenarios import SCENARIOS

    alerts = payload.get("alerts") or []
    if not alerts:
        return None

    alert = alerts[0]
    labels = alert.get("labels") or {}
    annotations = alert.get("annotations") or {}

    # Explicit label wins.
    for key in ("scenario", "autosre_scenario", "fault"):
        if labels.get(key) in SCENARIOS:
            return labels[key]

    service = labels.get("service") or labels.get("job") or ""
    summary = (
        annotations.get("summary")
        or annotations.get("description")
        or alert.get("annotations", {}).get("summary")
        or ""
    ).lower()
    alertname = (labels.get("alertname") or "").lower()

    for name, scenario in SCENARIOS.items():
        if service and scenario.get("service") == service:
            return name

    # Keyword heuristics
    blob = f"{alertname} {summary} {service}".lower()
    if "disk" in blob or "filesystem" in blob:
        return "disk" if "disk" in SCENARIOS else None
    if "network" in blob or "packet" in blob or "partition" in blob:
        return "network" if "network" in SCENARIOS else None
    if "db" in blob or "database" in blob or "pool" in blob or "latency" in blob:
        return "db" if "db" in SCENARIOS else None
    return None


async def _incident_worker(queue: asyncio.Queue, cfg: AutoSREConfig) -> None:
    """Process queued incidents one at a time."""
    from autosre.agent import run_agent

    while True:
        item = await queue.get()
        try:
            scenario = item.get("scenario")
            logger.info("Worker picked up scenario=%s", scenario)
            with TraceContext(item.get("trace_id")):
                # run_agent is sync/blocking — offload to a thread.
                await asyncio.to_thread(run_agent, scenario, False)
        except Exception as exc:
            logger.exception("Worker failed for item %s: %s", item, exc)
        finally:
            queue.task_done()


def create_app(cfg: Optional[AutoSREConfig] = None) -> FastAPI:
    """Build the FastAPI application."""
    cfg = cfg or AutoSREConfig.from_env()
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _queue, _worker_task
        _queue = asyncio.Queue(maxsize=1)
        _worker_task = asyncio.create_task(_incident_worker(_queue, cfg))
        app.state.queue = _queue
        app.state.cfg = cfg
        app.state.store = IncidentStore(cfg.db_path)
        logger.info("Webhook server started on configured port %s", cfg.port)
        yield
        if _worker_task:
            _worker_task.cancel()
            try:
                await _worker_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="AutoSRE", version="0.2.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "autosre"}

    @app.get("/incidents")
    async def list_incidents(limit: int = 50) -> dict[str, Any]:
        store: IncidentStore = app.state.store
        return {"incidents": store.get_history(limit=limit)}

    @app.post("/webhook/alertmanager")
    async def alertmanager_webhook(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc

        scenario = _map_alertmanager_to_scenario(payload)
        if not scenario:
            raise HTTPException(
                status_code=422,
                detail="unable to map alert to a known AutoSRE scenario",
            )

        queue: asyncio.Queue = app.state.queue
        if queue.full():
            return JSONResponse(
                status_code=429,
                content={
                    "status": "busy",
                    "detail": "an incident is already being processed",
                },
            )

        trace = TraceContext()
        item = {"scenario": scenario, "payload": payload, "trace_id": trace.trace_id}
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            return JSONResponse(
                status_code=429,
                content={
                    "status": "busy",
                    "detail": "an incident is already being processed",
                },
            )

        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "scenario": scenario,
                "trace_id": trace.trace_id,
            },
        )

    return app


# ASGI app entry for uvicorn: ``uvicorn autosre.webhook:app``
app = create_app()
