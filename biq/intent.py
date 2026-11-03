"""Stage 8a - intent prediction and the research experiments.

Variants (same XGBoost classifier and budget, so differences come from representation):
  A      aggregate session features
  B      A + hand-crafted sequence/transition features (from mining.py)
  C_base generic text embedding of the stringified sequence (baseline)
  C      contrastively pretrained Transformer embedding
  C+R    C + retrieval features: outcomes of top-k nearest *earlier* sessions in Qdrant
  C-ft   the Transformer fine-tuned end-to-end (classification head), full prefix only

Experiments: representation comparison, retrieval ablation (+Brier, k sweep), early-prediction
curve over prefixes of 3/5/10/20 events, 3 seeds, paired bootstrap C+R vs C.

    python -m biq.intent
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, average_precision_score, f1_score, roc_auc_score

from . import config, encoder as E, retrieval as R
from .mining import aggregate_features, sequence_features
from .schema import OUTCOMES

LENGTHS = [3, 5, 10, 20, None]          # None = full prediction prefix
SEEDS = [0, 1, 2]
K = 10
LABEL = {o: i for i, o in enumerate(OUTCOMES)}


def lname(L):
    return "full" if L is None else str(L)


# ---------------------------------------------------------------- feature builders
class Baseline:
    """Generic text-embedding baseline fitted on train texts only."""

    def __init__(self, train_texts):
        try:
            from sentence_transformers import SentenceTransformer
            self.st = SentenceTransformer("all-MiniLM-L6-v2"); self.name = "MiniLM-L6-v2"
        except Exception:
            from sklearn.decomposition import TruncatedSVD
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.vec = TfidfVectorizer(ngram_range=(1, 3), token_pattern=r"[^ ]+").fit(train_texts)
            X_train = self.vec.transform(train_texts)
            # see biq/encoder.py::baseline_embed - low-diversity real-data text can have fewer
            # n-gram features than E.DIM
            n_comp = max(1, min(E.DIM, X_train.shape[1] - 1))
            self.st = None; self.name = f"TF-IDF(1-3)+SVD-{n_comp}"
            self.svd = TruncatedSVD(n_comp, random_state=config.SEED).fit(X_train)

    def __call__(self, texts):
        from sklearn.preprocessing import normalize
        if self.st is not None:
            return self.st.encode(texts, batch_size=128, normalize_embeddings=True)
        return normalize(self.svd.transform(self.vec.transform(texts)))


def retrieval_features(nbrs: list[dict]) -> dict:
    f = {}
    sims = np.array([n["score"] for n in nbrs]) if nbrs else np.array([])
    w = np.clip(sims, 0, None) + 1e-6 if len(sims) else sims
    for o in OUTCOMES:
        hit = np.array([n["outcome"] == o for n in nbrs], dtype=float)
        f[f"r_frac_{o}"] = hit.mean() if len(hit) else np.nan
        f[f"r_wfrac_{o}"] = (hit * w).sum() / w.sum() if len(hit) else np.nan
    f["r_sim_mean"] = sims.mean() if len(sims) else np.nan
    f["r_sim_max"] = sims.max() if len(sims) else np.nan
    return f


def build_features(s: pd.DataFrame, bundle: E.EncoderBundle, base: Baseline, L, k: int = K):
    pre = [E.prefix_view(r, L) for r in s.itertuples()]
    A = pd.DataFrame([aggregate_features(e, g, d) for (e, _, g), d in zip(pre, s.device)])
    B = pd.concat([A, pd.DataFrame([sequence_features(e) for e, _, _ in pre])], axis=1)
    items = [bundle.item(e, c, g) for e, c, g in pre]
    emb = E.embed_items(bundle.model, items)
    texts = [" ".join(E.tokens_of(e, c)) for e, c, _ in pre]
    bemb = base(texts)
    as_of = [float(pd.Timestamp(t).timestamp()) for t in s.start_ts]
    nbrs = R.exact_neighbors_batch(emb, k, as_of=as_of, exclude=list(s.session_id))
    Rf = pd.DataFrame([retrieval_features(n) for n in nbrs])
    Cdf = pd.DataFrame(emb, columns=[f"e{i}" for i in range(emb.shape[1])])
    return {
        "A": A, "B": B,
        "C_base": pd.DataFrame(bemb, columns=[f"b{i}" for i in range(bemb.shape[1])]),
        "C": Cdf, "C+R": pd.concat([Cdf, Rf], axis=1), "B+R": pd.concat([B, Rf], axis=1), "R_only": Rf,
    }, items, emb


# ---------------------------------------------------------------- model + metrics
def fit_xgb(X, y, seed: int):
    m = xgb.XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8,
                          colsample_bytree=0.8, objective="multi:softprob", eval_metric="mlogloss",
                          random_state=seed, n_jobs=2, tree_method="hist")
    m.fit(X, y)
    return m


def metrics(y, p) -> dict:
    yhat = p.argmax(1)
    onehot = np.eye(len(OUTCOMES))[y]
    return dict(accuracy=accuracy_score(y, yhat), macro_f1=f1_score(y, yhat, average="macro"),
                roc_auc=roc_auc_score(y, p, multi_class="ovr", average="macro"),
                pr_auc=average_precision_score(onehot, p, average="macro"),
                brier=float(((p - onehot) ** 2).sum(1).mean()))


def paired_bootstrap(y, p_a, p_b, n: int = 1000, seed: int = 0):
    """P(model b is NOT better than a) on macro-F1 and ROC-AUC via paired bootstrap."""
    rng = np.random.default_rng(seed)
    d_f1, d_auc = [], []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(set(y[i])) < len(OUTCOMES):
            continue
        d_f1.append(f1_score(y[i], p_b[i].argmax(1), average="macro") - f1_score(y[i], p_a[i].argmax(1), average="macro"))
        d_auc.append(roc_auc_score(y[i], p_b[i], multi_class="ovr") - roc_auc_score(y[i], p_a[i], multi_class="ovr"))
    d_f1, d_auc = np.array(d_f1), np.array(d_auc)
    return dict(delta_f1=d_f1.mean(), f1_ci=(np.percentile(d_f1, 2.5), np.percentile(d_f1, 97.5)), p_f1=(d_f1 <= 0).mean(),
                delta_auc=d_auc.mean(), auc_ci=(np.percentile(d_auc, 2.5), np.percentile(d_auc, 97.5)), p_auc=(d_auc <= 0).mean())


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t0 = time.time()
    out = config.REPORTS / "stage8"
    out.mkdir(exist_ok=True)
    s = pd.read_parquet(config.DATA / "sessions_split.parquet")
    y = np.array([LABEL[o] for o in s.outcome.astype(str)])
    tr, va, te = (s.split == "train").values, (s.split == "val").values, (s.split == "test").values
    trv = tr | va
    bundle = E.EncoderBundle.load("encoder")
    base = Baseline([" ".join(E.tokens_of(*E.prefix_view(r)[:2])) for r in s[tr].itertuples()])
    R.build_index(("train",))
    overlap = R.check_equivalence()
    print(f"exact numpy retrieval vs Qdrant top-10 overlap: {overlap:.3f}")

    rows, preds = [], {}
    for L in LENGTHS:
        feats, items, emb = build_features(s, bundle, base, L)
        print(f"[{time.time() - t0:5.0f}s] prefix {lname(L)}: features ready")
        for variant in ["A", "B", "B+R", "C_base", "C", "C+R"]:
            X = feats[variant].values.astype(np.float32)
            for seed in SEEDS:
                m = fit_xgb(X[trv], y[trv], seed)
                p = m.predict_proba(X[te])
                preds[(lname(L), variant, seed)] = p
                rows.append(dict(prefix=lname(L), variant=variant, seed=seed, **metrics(y[te], p)))
                if variant == "C+R" and seed == 0:  # one serving model per prefix length
                    m.save_model(str(config.ARTIFACTS / f"intent_cr_{lname(L)}.json"))
                if L is None and variant == "C+R" and seed == 0:
                    m.save_model(str(config.ARTIFACTS / "intent_cr.json"))
                    (config.ARTIFACTS / "intent_cr_features.json").write_text(json.dumps(list(feats["C+R"].columns)))
                    np.save(config.DATA / "test_pred_cr.npy", p)
                    full_feats = feats
        if L is None:  # retrieval k sweep (ablation)
            for k in (5, 20, 50):
                fk, _, _ = build_features(s, bundle, base, None, k=k)
                X = fk["C+R"].values.astype(np.float32)
                p = fit_xgb(X[trv], y[trv], 0).predict_proba(X[te])
                rows.append(dict(prefix="full", variant=f"C+R (k={k})", seed=0, **metrics(y[te], p)))
            # fine-tuned transformer head
            import copy
            ft = copy.deepcopy(bundle.model)
            E.finetune(ft, [i for i, t in zip(items, tr) if t], y[tr], [i for i, v in zip(items, va) if v], y[va])
            p = E.predict_proba(ft, [i for i, t in zip(items, te) if t])
            rows.append(dict(prefix="full", variant="C-ft (Transformer head)", seed=0, **metrics(y[te], p)))
            E.EncoderBundle(ft, bundle.vocab).save("encoder_ft")

    res = pd.DataFrame(rows)
    res.to_csv(out / "all_runs.csv", index=False)
    agg = res.groupby(["prefix", "variant"]).agg(["mean", "std"]).drop(columns="seed", level=0)
    full = res[res.prefix == "full"].groupby("variant")[["accuracy", "macro_f1", "roc_auc", "pr_auc", "brier"]].agg(["mean", "std"])
    fmt = full.apply(lambda c: c.map(lambda v: f"{v:.3f}"))
    table = pd.DataFrame({m: fmt[(m, "mean")] + " ± " + fmt[(m, "std")].replace("nan", "0") for m in
                          ["accuracy", "macro_f1", "roc_auc", "pr_auc", "brier"]})
    order = ["A", "B", "B+R", "C_base", "C", "C+R", "C+R (k=5)", "C+R (k=20)", "C+R (k=50)", "C-ft (Transformer head)"]
    table = table.reindex([o for o in order if o in table.index])

    p_c = np.mean([preds[("full", "C", sd)] for sd in SEEDS], axis=0)
    p_cr = np.mean([preds[("full", "C+R", sd)] for sd in SEEDS], axis=0)
    p_b = np.mean([preds[("full", "B", sd)] for sd in SEEDS], axis=0)
    bs = paired_bootstrap(y[te], p_c, p_cr)
    bs_b = paired_bootstrap(y[te], p_b, p_cr)
    p_br = np.mean([preds[("full", "B+R", sd)] for sd in SEEDS], axis=0)
    bs_br = paired_bootstrap(y[te], p_b, p_br)

    # early-prediction curve
    curve = res[res.variant.isin(["A", "B", "B+R", "C_base", "C", "C+R"])].groupby(["variant", "prefix"])[["macro_f1", "roc_auc"]].mean().reset_index()
    xs = ["3", "5", "10", "20", "full"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for ax, met in zip(axes, ["macro_f1", "roc_auc"]):
        for v in ["A", "B", "B+R", "C_base", "C", "C+R"]:
            c = curve[curve.variant == v].set_index("prefix").reindex(xs)
            ax.plot(xs, c[met], marker="o", label=v)
        ax.set_xlabel("events observed (prefix length)"); ax.set_ylabel(met); ax.grid(alpha=.3)
    axes[0].legend(); fig.suptitle("Early-prediction curve: intent quality vs. how much of the session is seen")
    fig.tight_layout(); fig.savefig(out / "early_curve.png", dpi=130); plt.close(fig)

    # reliability diagram for C vs C+R (purchase class)
    from sklearn.calibration import calibration_curve
    fig, ax = plt.subplots(figsize=(5, 4.5))
    for name, p in (("C", p_c), ("C+R", p_cr)):
        fy, fx = calibration_curve(y[te] == LABEL["purchase"], p[:, LABEL["purchase"]], n_bins=10)
        ax.plot(fx, fy, marker="o", label=name)
    ax.plot([0, 1], [0, 1], "--", color="grey"); ax.set_xlabel("predicted P(purchase)"); ax.set_ylabel("observed")
    ax.legend(); ax.set_title("Reliability (purchase class, test)"); fig.tight_layout()
    fig.savefig(out / "reliability.png", dpi=130); plt.close(fig)

    curve_tbl = curve.pivot(index="variant", columns="prefix", values="macro_f1")[xs].round(3)
    report = [
        "# Stage 8 - Intent prediction results (time-based test split, 3 seeds, mean ± std)\n",
        f"Baseline text encoder: {base.name}. Retrieval: top-{K} earlier train sessions (no self, no future); "
        f"bulk retrieval uses exact numpy cosine search, top-10 overlap with Qdrant = {overlap:.3f}.\n",
        "## Representation comparison and retrieval ablation (full prediction prefix)\n",
        table.to_markdown(), "",
        "## C+R vs C, paired bootstrap (1,000 resamples)\n",
        f"- macro-F1 delta {bs['delta_f1']:+.4f} (95% CI {bs['f1_ci'][0]:+.4f} to {bs['f1_ci'][1]:+.4f}), p = {bs['p_f1']:.3f}",
        f"- ROC-AUC delta {bs['delta_auc']:+.4f} (95% CI {bs['auc_ci'][0]:+.4f} to {bs['auc_ci'][1]:+.4f}), p = {bs['p_auc']:.3f}",
        f"- C+R vs B: macro-F1 delta {bs_b['delta_f1']:+.4f} (p = {bs_b['p_f1']:.3f}), ROC-AUC delta {bs_b['delta_auc']:+.4f} (p = {bs_b['p_auc']:.3f})",
        f"- B+R vs B (retrieval on top of hand-crafted features): macro-F1 delta {bs_br['delta_f1']:+.4f} "
        f"(95% CI {bs_br['f1_ci'][0]:+.4f} to {bs_br['f1_ci'][1]:+.4f}, p = {bs_br['p_f1']:.3f}), "
        f"ROC-AUC delta {bs_br['delta_auc']:+.4f} (p = {bs_br['p_auc']:.3f})\n",
        "## Early-prediction curve (macro-F1 by events observed)\n",
        curve_tbl.to_markdown(), "",
        "Figures: early_curve.png, reliability.png. Raw runs: all_runs.csv.\n",
    ]
    (out / "intent.md").write_text("\n".join(report), encoding="utf-8")
    agg.to_csv(out / "summary.csv")
    print("\n".join(report))
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
