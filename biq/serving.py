"""Online serving logic shared by the API and the dashboard: embed a (possibly in-progress) session,
retrieve neighbours from Qdrant, predict intent with the champion C+R model, choose an action with
the bandit, and explain the prediction with SHAP."""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd
import xgboost as xgb

from . import bandit as BD, config, encoder as E, retrieval as R
from .intent import retrieval_features
from .schema import OUTCOMES


@lru_cache(maxsize=1)
def models():
    bundle = E.EncoderBundle.load("encoder")
    clf = xgb.XGBClassifier()
    clf.load_model(str(config.ARTIFACTS / "intent_cr.json"))
    cols = json.loads((config.ARTIFACTS / "intent_cr_features.json").read_text())
    return bundle, clf, cols


PREFIX_MODELS = [3, 5, 10, 20]


@lru_cache(maxsize=8)
def prefix_model(n_events: int | None):
    """Model trained on prefixes of the closest length, so early predictions are calibrated for early
    evidence (the full-session model is overconfident after only a few events)."""
    name = "full"
    if n_events is not None:
        for L in PREFIX_MODELS:
            if n_events <= L:
                name = str(L)
                break
    path = config.ARTIFACTS / f"intent_cr_{name}.json"
    if not path.exists():
        path = config.ARTIFACTS / "intent_cr.json"
    clf = xgb.XGBClassifier()
    clf.load_model(str(path))
    return clf


@lru_cache(maxsize=1)
def sessions() -> pd.DataFrame:
    return pd.read_parquet(config.DATA / "sessions_split.parquet")


def session_prefix(session_id: str, length: int | None = None):
    s = sessions()
    r = s[s.session_id == session_id]
    if r.empty:
        raise KeyError(session_id)
    row = next(r.itertuples())
    ev, cats, gaps = E.prefix_view(row, length)
    return ev, cats, gaps, row


def features(events, cats, gaps, as_of: float | None = None, exclude: str | None = None):
    bundle, _, cols = models()
    vec = bundle.embed(events, cats, gaps)
    nb = R.neighbors(vec, 10, "biq_custom", as_of=as_of, exclude_session=exclude)
    f = {f"e{i}": float(v) for i, v in enumerate(vec)}
    f.update(retrieval_features(nb))
    return pd.DataFrame([f])[cols], nb


def predict(events, cats, gaps, as_of=None, exclude=None, complete: bool = False) -> dict:
    """complete=True when the whole prediction prefix is known (closed session); otherwise the model
    matching the number of events seen so far is used."""
    clf = prefix_model(None if complete else len(events))
    X, nb = features(events, cats, gaps, as_of, exclude)
    p = clf.predict_proba(X.values.astype(np.float32))[0]
    return {"probabilities": dict(zip(OUTCOMES, map(float, p))), "predicted": OUTCOMES[int(p.argmax())],
            "neighbour_outcomes": R.outcome_summary(nb), "X": X}


def predict_session(session_id: str, length: int | None = None) -> dict:
    ev, cats, gaps, row = session_prefix(session_id, length)
    out = predict(ev, cats, gaps, as_of=pd.Timestamp(row.start_ts).timestamp(), exclude=session_id,
                  complete=length is None or length >= len(E.prefix_view(row)[0]))
    out.update(session_id=session_id, events_seen=ev, actual_outcome=row.outcome, device=row.device)
    return out


def decide(probs: dict, device: str, seed: int | None = None) -> dict:
    """Thompson-sample an action from the learned bandit posterior."""
    state = json.loads((config.ARTIFACTS / "bandit_state.json").read_text())
    p = np.array([probs[o] for o in OUTCOMES])
    ctx = BD.context_key(p, device)
    rng = np.random.default_rng(seed)
    scores = {}
    for a in BD.ACTIONS:
        alpha, beta = state.get(f"{ctx}::{a}", [1.0, 1.0])
        scores[a] = float(rng.beta(alpha, beta) - BD.COST[a])
    return {"context": ctx, "action": max(scores, key=scores.get), "sampled_values": scores}


def explain(X: pd.DataFrame, cls: str | None = None, top: int = 8, n_events: int | None = None) -> pd.DataFrame:
    """SHAP contributions for one prediction; the 64 embedding dims are grouped into one feature."""
    import shap
    _, _, cols = models()
    clf = prefix_model(n_events)
    sv = shap.TreeExplainer(clf).shap_values(X.values.astype(np.float32))
    sv = np.asarray(sv)
    p = clf.predict_proba(X.values.astype(np.float32))[0]
    k = OUTCOMES.index(cls) if cls else int(p.argmax())
    if sv.ndim == 3 and sv.shape[0] == len(OUTCOMES):      # (classes, n, features)
        contrib = sv[k][0]
    elif sv.ndim == 3:                                      # (n, features, classes)
        contrib = sv[0, :, k]
    else:
        contrib = sv[0]
    s = pd.Series(contrib, index=cols)
    emb = s[[c for c in cols if c.startswith("e") and c[1:].isdigit()]].sum()
    rest = s[[c for c in cols if not (c.startswith("e") and c[1:].isdigit())]]
    nice = {"r_frac_purchase": "neighbours: share purchased", "r_frac_abandon": "neighbours: share abandoned",
            "r_frac_browse": "neighbours: share browsed", "r_wfrac_purchase": "neighbours: weighted share purchased",
            "r_wfrac_abandon": "neighbours: weighted share abandoned", "r_wfrac_browse": "neighbours: weighted share browsed",
            "r_sim_mean": "neighbours: mean similarity", "r_sim_max": "neighbours: max similarity"}
    out = pd.concat([pd.Series({"session embedding (64 dims)": emb}), rest.rename(index=nice)])
    df = out.rename("shap").to_frame()
    df["abs"] = df.shap.abs()
    df = df.sort_values("abs", ascending=False).head(top).drop(columns="abs")
    df.attrs["explained_class"] = OUTCOMES[k]
    return df
