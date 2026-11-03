"""Serving API: live features, neighbours, intent, next-best-action, explanation, copilot.

    uvicorn biq.serve_api:app --port 8001
    open http://127.0.0.1:8001/docs

/metrics is Prometheus text format (prometheus-client); scraped by the "monitoring" compose
profile (see infra/docker-compose.yml, infra/prometheus/prometheus.yml).
"""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from . import serving
from .feature_store import FeatureStore

app = FastAPI(title="BehaviorIQ serving API")

REQUESTS = Counter(
    "biq_serve_requests_total", "Requests handled by the serving API", ["endpoint", "method", "status"]
)
LATENCY = Histogram(
    "biq_serve_request_latency_seconds", "Serving API request latency", ["endpoint"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
PREDICTIONS = Counter("biq_predictions_total", "Intent predictions made, by predicted class", ["predicted_class"])
BANDIT_ACTIONS = Counter("biq_bandit_actions_total", "Next-best-action decisions, by chosen action", ["action"])
COPILOT_QUESTIONS = Counter("biq_copilot_questions_total", "Questions answered by the copilot")
COPILOT_BLOCKED_QUERIES = Counter(
    "biq_copilot_blocked_queries_total", "Copilot tool calls blocked by the SQL guardrail"
)


@app.middleware("http")
async def _instrument(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    endpoint = route.path if route else request.url.path
    LATENCY.labels(endpoint=endpoint).observe(time.perf_counter() - start)
    REQUESTS.labels(endpoint=endpoint, method=request.method, status=response.status_code).inc()
    return response


class Question(BaseModel):
    question: str


class Behaviour(BaseModel):
    events: list[str]
    device: str = "desktop"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/features/{session_id}")
def online_features(session_id: str):
    f = FeatureStore().get_online_features(session_id)
    if not f:
        raise HTTPException(404, "no online features for this session")
    return f


@app.get("/predict/{session_id}")
def predict(session_id: str, prefix: int | None = None):
    try:
        out = serving.predict_session(session_id, prefix)
    except KeyError:
        raise HTTPException(404, "unknown session")
    out.pop("X")
    PREDICTIONS.labels(predicted_class=out["predicted"]).inc()
    return out


@app.post("/predict")
def predict_behaviour(b: Behaviour):
    out = serving.predict(b.events, [None] * len(b.events), [10.0] * len(b.events))
    out.pop("X")
    PREDICTIONS.labels(predicted_class=out["predicted"]).inc()
    out["decision"] = serving.decide(out["probabilities"], b.device)
    BANDIT_ACTIONS.labels(action=out["decision"]["action"]).inc()
    return out


@app.get("/decide/{session_id}")
def decide(session_id: str, prefix: int | None = None):
    p = serving.predict_session(session_id, prefix)
    d = serving.decide(p["probabilities"], p["device"])
    BANDIT_ACTIONS.labels(action=d["action"]).inc()
    return {"session_id": session_id, "probabilities": p["probabilities"], **d}


@app.get("/explain/{session_id}")
def explain(session_id: str, prefix: int | None = None):
    p = serving.predict_session(session_id, prefix)
    df = serving.explain(p["X"], n_events=prefix)
    return {"explained_class": df.attrs["explained_class"], "contributions": df.shap.round(4).to_dict()}


@app.post("/ask")
def ask(q: Question):
    from .copilot import ask as copilot_ask
    COPILOT_QUESTIONS.inc()
    r = copilot_ask(q.question)
    blocked = sum(1 for o in r["tool_outputs"] if isinstance(o, dict) and o.get("blocked"))
    if blocked:
        COPILOT_BLOCKED_QUERIES.inc(blocked)
    return {k: r[k] for k in ("answer", "grounded", "sql", "mode")} | {"tool_outputs": r["tool_outputs"]}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
