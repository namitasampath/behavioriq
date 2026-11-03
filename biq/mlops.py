"""Stage 10 - MLOps: MLflow experiment tracking + model registry, drift detection, SHAP check.

    python -m biq.mlops            # log runs + register champion + drift injection report
    mlflow ui --backend-store-uri sqlite:///mlflow.db     # browse runs at http://127.0.0.1:5000

Drift detection compares live feature distributions with the training reference using the
Population Stability Index (PSI) and two-sample Kolmogorov-Smirnov tests (the same statistics
Evidently AI reports). A batch is flagged when any feature has PSI > 0.2,
or when a domain classifier separates live from training traffic with ROC-AUC > 0.6.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from . import config, simulator as SIM
from .mining import aggregate_features

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
TRACKING = f"sqlite:///{(config.ROOT / 'mlflow.db').as_posix()}"
DRIFT_FEATURES = ["event_count", "n_product_view", "n_review_view", "n_compare", "n_add_to_cart",
                  "n_remove_from_cart", "n_checkout_start", "duration_s", "is_mobile"]


# ---------------------------------------------------------------- MLflow
def log_to_mlflow():
    import json

    import mlflow
    import xgboost as xgb
    mlflow.set_tracking_uri(TRACKING)
    mlflow.set_experiment("behavioriq-intent")
    runs = pd.read_csv(config.REPORTS / "stage8" / "all_runs.csv")
    for r in runs.itertuples():
        with mlflow.start_run(run_name=f"{r.variant}|prefix={r.prefix}|seed={r.seed}"):
            mlflow.log_params({"variant": r.variant, "prefix": r.prefix, "seed": r.seed, "classifier": "xgboost",
                               "n_estimators": 300, "max_depth": 4})
            mlflow.log_metrics({m: float(getattr(r, m)) for m in ["accuracy", "macro_f1", "roc_auc", "pr_auc", "brier"]})
    clf = xgb.XGBClassifier()
    clf.load_model(str(config.ARTIFACTS / "intent_cr.json"))
    best = runs[(runs.variant == "C+R") & (runs.prefix == "full") & (runs.seed == 0)].iloc[0]
    with mlflow.start_run(run_name="champion C+R full-prefix"):
        mlflow.log_params({"variant": "C+R", "features": len(json.loads((config.ARTIFACTS / "intent_cr_features.json").read_text()))})
        mlflow.log_metrics({m: float(best[m]) for m in ["accuracy", "macro_f1", "roc_auc", "pr_auc", "brier"]})
        for f in ["stage8/intent.md", "stage8/early_curve.png", "stage8/reliability.png"]:
            if (config.REPORTS / f).exists():
                mlflow.log_artifact(str(config.REPORTS / f))
        info = mlflow.xgboost.log_model(clf, name="model", registered_model_name="behavioriq-intent")
    client = mlflow.MlflowClient()
    v = max(client.search_model_versions("name='behavioriq-intent'"), key=lambda m: int(m.version)).version
    client.set_registered_model_alias("behavioriq-intent", "champion", v)
    print(f"logged {len(runs)} runs; registered behavioriq-intent v{v} as @champion ({TRACKING})")


# ---------------------------------------------------------------- drift
def session_features(events: pd.DataFrame) -> pd.DataFrame:
    events = events.sort_values(["session_id", "timestamp"])
    rows = []
    for sid, g in events.groupby("session_id", sort=False):
        ts = g.timestamp.to_numpy()
        gaps = np.r_[0, np.diff(ts).astype("timedelta64[ms]").astype(float) / 1000]
        rows.append({**aggregate_features(g.event_type.tolist(), gaps.tolist(), g.device.iloc[0]), "session_id": sid})
    return pd.DataFrame(rows)


def psi(ref: np.ndarray, cur: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index. Discrete features (few distinct values, e.g. counts or flags) are
    binned by value (counts capped at the 99th percentile); continuous ones by reference deciles."""
    ref, cur = np.asarray(ref, float), np.asarray(cur, float)
    if len(np.unique(ref)) <= 25:
        cap = np.percentile(ref, 99)
        r_v, c_v = np.minimum(ref, cap), np.minimum(cur, cap)
        cats = np.union1d(np.unique(r_v), np.unique(c_v))
        r = np.array([(r_v == c).mean() for c in cats]) + 1e-4
        c = np.array([(c_v == x).mean() for x in cats]) + 1e-4
    else:
        edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        r = np.histogram(ref, edges)[0] / len(ref) + 1e-4
        c = np.histogram(cur, edges)[0] / len(cur) + 1e-4
    return float(np.sum((c - r) * np.log(c / r)))


def drift_report(ref: pd.DataFrame, cur: pd.DataFrame) -> pd.DataFrame:
    from scipy.stats import ks_2samp
    rows = []
    for f in DRIFT_FEATURES:
        p = psi(ref[f].to_numpy(float), cur[f].to_numpy(float))
        rows.append(dict(feature=f, psi=p, ks_pvalue=ks_2samp(ref[f], cur[f]).pvalue, drifted=p > 0.2))
    return pd.DataFrame(rows)


def domain_classifier_auc(ref: pd.DataFrame, cur: pd.DataFrame) -> float:
    """Multivariate drift: can a classifier tell live traffic from training traffic? AUC ~0.5 = no drift."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import cross_val_score
    n = min(len(ref), len(cur))
    X = pd.concat([ref[DRIFT_FEATURES].sample(n, random_state=0), cur[DRIFT_FEATURES].sample(n, random_state=0)])
    y = np.r_[np.zeros(n), np.ones(n)]
    return float(cross_val_score(HistGradientBoostingClassifier(max_iter=100), X, y, cv=5, scoring="roc_auc").mean())


def _simulate(n, seed, persona_override=None, mobile=None, dwell_mult=None):
    saved = {k: dict(v) for k, v in SIM.PERSONAS.items()}
    try:
        for p in SIM.PERSONAS.values():
            if mobile is not None:
                p["mobile"] = mobile
            if dwell_mult is not None:
                p["dwell"] = p["dwell"] * dwell_mult
        ev, _, _ = SIM.generate(n, seed, persona_override=persona_override)
    finally:
        SIM.PERSONAS.clear(); SIM.PERSONAS.update(saved)
    return ev


def _dominant(persona: str, share: float = 0.85) -> dict:
    others = [p for p in SIM.PERSONAS if p != persona]
    return {persona: share, **{p: (1 - share) / len(others) for p in others}}


def drift_experiment(n: int = 1500):
    raw = pd.read_parquet(config.RAW_EVENTS)
    split = pd.read_parquet(config.DATA / "sessions_split.parquet")
    ref = session_features(raw[raw.session_id.isin(split.session_id[split.split == "train"])])
    scenarios = {
        "control (no drift, new traffic)": dict(),
        "mobile surge (80% mobile)": dict(mobile=0.8),
        "comparer surge (85% heavy comparers)": dict(persona_override=_dominant("heavy_comparer")),
        "deal-hunter influx (85% deal hunters)": dict(persona_override=_dominant("deal_hunter")),
        "slow sessions (dwell x3)": dict(dwell_mult=3.0),
        "abandoner wave (85% abandoners)": dict(persona_override=_dominant("abandoner")),
    }
    rows, detail = [], []
    for i, (name, kw) in enumerate(scenarios.items()):
        cur = session_features(_simulate(n, 1000 + i, **kw))
        rep = drift_report(ref, cur)
        auc = domain_classifier_auc(ref, cur)
        flagged = bool(rep.drifted.any()) or auc > 0.6
        rows.append(dict(scenario=name, injected=name.startswith("control") is False, drifted_features=int(rep.drifted.sum()),
                         max_psi=rep.psi.max(), top_feature=rep.sort_values("psi").feature.iloc[-1],
                         domain_auc=auc, detected=flagged))
        detail.append(rep.assign(scenario=name))
    df = pd.DataFrame(rows)
    inj = df[df.injected]
    report = ("# Stage 10 - Drift detection (PSI + KS against the training reference)\n\n"
              f"Detected {int(inj.detected.sum())}/{len(inj)} injected drifts; "
              f"false alarm on control: {bool(df[~df.injected].detected.iloc[0])}.\n\n"
              + df.round(3).to_markdown(index=False) + "\n\nRule: drift when any monitored feature has PSI > 0.2 (univariate) OR a domain classifier separates live from training traffic with ROC-AUC > 0.6 (multivariate; catches mixture shifts that move each feature only slightly).\n")
    (config.REPORTS / "stage10_drift.md").write_text(report, encoding="utf-8")
    pd.concat(detail).to_csv(config.REPORTS / "stage10_drift_detail.csv", index=False)
    print(report)


# ---------------------------------------------------------------- drift, real (non-simulated) data
def _weighted_resample(feats: pd.DataFrame, weight: np.ndarray, n: int, rng: np.random.Generator) -> pd.DataFrame:
    p = weight / weight.sum()
    idx = rng.choice(len(feats), size=n, replace=True, p=p)
    return feats.iloc[idx].reset_index(drop=True)


def _primary_category(events: pd.DataFrame) -> pd.Series:
    """Most frequent category per session (for the category-mix-shift scenario); sessions with no
    categorised event map to 'none'."""
    cat = events.dropna(subset=["category"]).groupby("session_id")["category"] \
        .agg(lambda s: s.value_counts().idxmax())
    return cat.rename("primary_category")


def real_data_drift_experiment(seed: int = 1000):
    """Drift detection for a real (logged, not simulated) dataset. Reference = train-split
    sessions; control = held-out test-split sessions AS LOGGED (should NOT be flagged - it's the
    sanity check that two slices of the same real traffic don't look drifted); injected scenarios
    = the SAME test-split sessions resampled or perturbed to synthesize a known shift.

    This deliberately never compares real traffic against `biq.simulator` output: the simulator's
    fabricated event types (review/compare), personas and dwell distribution differ from any real
    log structurally, so EVERY scenario - including a true no-shift control - would come back
    "drifted" without saying anything about whether the detector catches a genuine shift.
    """
    raw = pd.read_parquet(config.RAW_EVENTS)
    split = pd.read_parquet(config.DATA / "sessions_split.parquet")
    train_ids = set(split.session_id[split.split == "train"])
    test_ids = set(split.session_id[split.split == "test"])
    ref = session_features(raw[raw.session_id.isin(train_ids)])
    test = session_features(raw[raw.session_id.isin(test_ids)]).reset_index(drop=True)
    test = test.merge(_primary_category(raw[raw.session_id.isin(test_ids)]), on="session_id", how="left")
    test["primary_category"] = test["primary_category"].fillna("none")
    n = len(test)
    rng = np.random.default_rng(seed)

    device_varies = test["is_mobile"].nunique() > 1
    if device_varies:
        device_w = np.where(test.is_mobile == 1, 4.0, 1.0)
        device_label = "mobile-heavy resample (4x weight on mobile sessions)"
    else:
        long_thr = test.event_count.median()
        device_w = np.where(test.event_count > long_thr, 4.0, 1.0)
        device_label = "long-session-heavy resample (4x weight on above-median-length sessions; no device signal to resample on)"
    cart_w = np.where(test.n_add_to_cart >= 1, 4.0, 1.0)
    top_cat = test.primary_category.value_counts().idxmax()
    cat_w = np.where(test.primary_category == top_cat, 4.0, 1.0)
    dwell_cur = test.copy()
    dwell_cur["duration_s"] *= 3
    dwell_cur["mean_gap_s"] *= 3

    scenarios = {
        "control (held-out test traffic, no shift)": test,
        device_label: _weighted_resample(test, device_w, n, rng),
        "cart-heavy resample (4x weight on sessions with >=1 add_to_cart)": _weighted_resample(test, cart_w, n, rng),
        "dwell/time-gaps x3 (duration_s, mean_gap_s scaled)": dwell_cur,
        f"category mix shift (4x weight on top category '{top_cat}')": _weighted_resample(test, cat_w, n, rng),
    }
    rows, detail = [], []
    for name, cur in scenarios.items():
        injected = not name.startswith("control")
        rep = drift_report(ref, cur)
        auc = domain_classifier_auc(ref, cur)
        flagged = bool(rep.drifted.any()) or auc > 0.6
        rows.append(dict(scenario=name, injected=injected, drifted_features=int(rep.drifted.sum()),
                         max_psi=rep.psi.max(), top_feature=rep.sort_values("psi").feature.iloc[-1],
                         domain_auc=auc, detected=flagged))
        detail.append(rep.assign(scenario=name))
    df = pd.DataFrame(rows)
    inj = df[df.injected]
    report = (
        "# Stage 10 - Drift detection, real data (PSI + KS against the training-split reference)\n\n"
        "Reference = train-split sessions; every scenario below is built from the SAME held-out "
        "test-split sessions (no `biq.simulator` traffic is used - comparing real logs against "
        "simulated traffic is always flagged as 'drifted', which tests nothing about the detector).\n\n"
        f"Detected {int(inj.detected.sum())}/{len(inj)} injected drifts; "
        f"false alarm on control: {bool(df[~df.injected].detected.iloc[0])}.\n\n"
        + df.round(3).to_markdown(index=False)
        + "\n\nRule: drift when any monitored feature has PSI > 0.2 (univariate) OR a domain "
          "classifier separates live from training traffic with ROC-AUC > 0.6 (multivariate; "
          "catches mixture shifts that move each feature only slightly).\n"
    )
    (config.REPORTS / "stage10_drift.md").write_text(report, encoding="utf-8")
    pd.concat(detail).to_csv(config.REPORTS / "stage10_drift_detail.csv", index=False)
    print(report)


def main():
    log_to_mlflow()
    drift_experiment()


if __name__ == "__main__":
    main()
