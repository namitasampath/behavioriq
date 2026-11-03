"""Stage 2 - ingestion API.

    uvicorn biq.ingest_api:app --port 8000

POST /track accepts one event or a batch; each is validated against the canonical schema.
Valid -> event bus (topic user-events, keyed by session_id). Invalid -> dead-letter queue.
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Request
from pydantic import ValidationError

from .bus import EventBus
from .schema import Event

app = FastAPI(title="BehaviorIQ ingestion API")
bus = EventBus()
STATS = {"accepted": 0, "rejected": 0, "started": time.time()}


@app.post("/track")
async def track(request: Request):
    body = await request.json()
    batch = body if isinstance(body, list) else [body]
    accepted = rejected = 0
    for raw in batch:
        try:
            ev = Event.model_validate(raw)
            bus.publish(ev.model_dump(mode="json"))
            accepted += 1
        except ValidationError as e:
            bus.dead_letter(raw, str(e.errors()[:3]))
            rejected += 1
    bus.flush()
    STATS["accepted"] += accepted
    STATS["rejected"] += rejected
    return {"accepted": accepted, "rejected": rejected}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    """Prometheus text format."""
    up = time.time() - STATS["started"]
    return_text = (
        f"biq_events_accepted_total {STATS['accepted']}\n"
        f"biq_events_rejected_total {STATS['rejected']}\n"
        f"biq_ingest_uptime_seconds {up:.0f}\n"
    )
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(return_text)
