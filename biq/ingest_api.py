"""Stage 2 - ingestion API.

    uvicorn biq.ingest_api:app --port 8000

POST /track accepts one event or a batch; each is validated against the canonical schema.
Valid -> event bus (topic user-events, keyed by session_id). Invalid -> dead-letter queue.

/metrics is Prometheus text format (prometheus-client); scraped by the "monitoring" compose
profile (see infra/docker-compose.yml, infra/prometheus/prometheus.yml).
"""
from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import ValidationError

from .bus import EventBus
from .schema import Event

app = FastAPI(title="BehaviorIQ ingestion API")
bus = EventBus()

REQUESTS = Counter(
    "biq_ingest_requests_total", "Requests handled by the ingestion API", ["endpoint", "method", "status"]
)
LATENCY = Histogram(
    "biq_ingest_request_latency_seconds", "Ingestion API request latency", ["endpoint"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
EVENTS_ACCEPTED = Counter("biq_events_accepted_total", "Events accepted via /track")
EVENTS_REJECTED = Counter("biq_events_rejected_total", "Events rejected via /track (schema validation failure)")


@app.middleware("http")
async def _instrument(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    endpoint = route.path if route else request.url.path
    LATENCY.labels(endpoint=endpoint).observe(time.perf_counter() - start)
    REQUESTS.labels(endpoint=endpoint, method=request.method, status=response.status_code).inc()
    return response


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
    EVENTS_ACCEPTED.inc(accepted)
    EVENTS_REJECTED.inc(rejected)
    return {"accepted": accepted, "rejected": rejected}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
