"""Serving API: live features, neighbours, intent, next-best-action, explanation, copilot.

    uvicorn biq.serve_api:app --port 8001
    open http://127.0.0.1:8001/docs
"""
from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from . import serving
from .feature_store import FeatureStore

app = FastAPI(title="BehaviorIQ serving API")
M = {"predictions": 0, "decisions": 0, "copilot": 0, "latency_ms_sum": 0.0}


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
    t = time.perf_counter()
    try:
        out = serving.predict_session(session_id, prefix)
    except KeyError:
        raise HTTPException(404, "unknown session")
    out.pop("X")
    M["predictions"] += 1; M["latency_ms_sum"] += (time.perf_counter() - t) * 1000
    return out


@app.post("/predict")
def predict_behaviour(b: Behaviour):
    out = serving.predict(b.events, [None] * len(b.events), [10.0] * len(b.events))
    out.pop("X")
    out["decision"] = serving.decide(out["probabilities"], b.device)
    return out


@app.get("/decide/{session_id}")
def decide(session_id: str, prefix: int | None = None):
    p = serving.predict_session(session_id, prefix)
    M["decisions"] += 1
    return {"session_id": session_id, "probabilities": p["probabilities"], **serving.decide(p["probabilities"], p["device"])}


@app.get("/explain/{session_id}")
def explain(session_id: str, prefix: int | None = None):
    p = serving.predict_session(session_id, prefix)
    df = serving.explain(p["X"], n_events=prefix)
    return {"explained_class": df.attrs["explained_class"], "contributions": df.shap.round(4).to_dict()}


@app.post("/ask")
def ask(q: Question):
    from .copilot import ask as copilot_ask
    M["copilot"] += 1
    r = copilot_ask(q.question)
    return {k: r[k] for k in ("answer", "grounded", "sql", "mode")} | {"tool_outputs": r["tool_outputs"]}


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    avg = M["latency_ms_sum"] / max(M["predictions"], 1)
    return (f"biq_predictions_total {M['predictions']}\nbiq_decisions_total {M['decisions']}\n"
            f"biq_copilot_questions_total {M['copilot']}\nbiq_predict_latency_ms_avg {avg:.2f}\n")
