"""Stage 5 - behavioral data mining: PrefixSpan sequential patterns, FP-Growth association rules,
HDBSCAN density-based clustering (with K-Means for comparison). Also exposes the hand-crafted
sequence features used by intent Model B.

    python -m biq.mining
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

EVENTS_OF_INTEREST = ["search", "product_view", "review_view", "compare", "add_to_cart",
                      "remove_from_cart", "checkout_start"]


# ---------------------------------------------------------------- hand-crafted features (Model B)
def sequence_features(events: list[str]) -> dict:
    ev = list(events)
    n = len(ev)
    pairs = list(zip(ev, ev[1:]))
    f = {
        "t_view_review": sum(a == "product_view" and b == "review_view" for a, b in pairs),
        "t_view_compare": sum(a == "product_view" and b == "compare" for a, b in pairs),
        "t_compare_compare": sum(a == "compare" and b == "compare" for a, b in pairs),
        "t_view_cart": sum(a == "product_view" and b == "add_to_cart" for a, b in pairs),
        "t_cart_remove": sum(a == "add_to_cart" and b == "remove_from_cart" for a, b in pairs),
        "t_cart_checkout": sum(a == "add_to_cart" and b == "checkout_start" for a, b in pairs),
        "t_cart_view": sum(a == "add_to_cart" and b == "product_view" for a, b in pairs),
        "consideration_ratio": sum(e in ("review_view", "compare") for e in ev) / max(n, 1),
        "last_is_cart": int(n > 0 and ev[-1] == "add_to_cart"),
        "last_is_checkout": int(n > 0 and ev[-1] == "checkout_start"),
        "events_since_cart": (n - 1 - max(i for i, e in enumerate(ev) if e == "add_to_cart")) if "add_to_cart" in ev else -1,
        "compare_ge2": int(ev.count("compare") >= 2),
        "revisits": n - len(set(zip(ev, range(0)))) if False else sum(1 for i in range(2, n) if ev[i] == ev[i - 2]),
    }
    return f


# ---------------------------------------------------------------- aggregate features (Model A / clustering)
def aggregate_features(events: list[str], gaps: list[float], device: str) -> dict:
    ev = list(events)
    n = len(ev)
    d = {f"n_{e}": ev.count(e) for e in EVENTS_OF_INTEREST}
    d.update(event_count=n, duration_s=float(np.sum(gaps)), mean_gap_s=float(np.mean(gaps)) if n else 0.0,
             is_mobile=int(device == "mobile"),
             funnel_stage=max([{"product_view": 1, "review_view": 2, "compare": 2, "add_to_cart": 3,
                                "checkout_start": 4}.get(e, 0) for e in ev] or [0]))
    return d


# ---------------------------------------------------------------- mining report
def run(sessions: pd.DataFrame | None = None, truth: pd.DataFrame | None = None) -> pd.DataFrame:
    from mlxtend.frequent_patterns import association_rules, fpgrowth
    from prefixspan import PrefixSpan
    from sklearn.cluster import HDBSCAN, KMeans
    from sklearn.metrics import adjusted_rand_score, davies_bouldin_score, silhouette_score
    from sklearn.preprocessing import StandardScaler

    s = sessions if sessions is not None else pd.read_parquet(config.SESSIONS)
    truth = truth if truth is not None else pd.read_parquet(config.TRUTH)
    out = [f"# Stage 5 - Behavioral mining report\n\nSessions analysed: {len(s):,}\n"]
    base_abandon = (s.outcome == "abandon").mean()

    # 1. sequential patterns (collapse consecutive duplicates, drop noise-only events)
    def clean(ev):
        x = [e for e in ev if e in EVENTS_OF_INTEREST + ["exit", "purchase"]]
        return [e for i, e in enumerate(x) if i == 0 or e != x[i - 1]]
    seqs = s.events.apply(clean).tolist()
    ps = PrefixSpan(seqs)
    ps.minlen, ps.maxlen = 3, 5
    top = ps.topk(60, closed=True)
    rows = []
    for sup, pat in top:
        mask = [_is_subseq(pat, q) for q in seqs]
        ab = s.outcome[mask].eq("abandon").mean()
        rows.append(dict(pattern=" -> ".join(pat), support=sup / len(seqs), abandon_rate=ab,
                         lift_abandon=ab / base_abandon))
    pat_df = pd.DataFrame(rows).sort_values("support", ascending=False).head(10)
    out.append("## Top 10 frequent sequential patterns (PrefixSpan, closed, length 3-5)\n\n"
               + pat_df.round(3).to_markdown(index=False) + "\n")
    lift_df = pd.DataFrame(rows).query("support > 0.02").sort_values("lift_abandon", ascending=False).head(10)
    out.append("## Patterns most associated with abandonment\n\n" + lift_df.round(3).to_markdown(index=False) + "\n")

    # 2. association rules on item sets
    items = pd.DataFrame([{**{f"has_{e}": e in ev for e in EVENTS_OF_INTEREST},
                           "compare_ge2": list(ev).count("compare") >= 2,
                           "long_session": len(ev) >= 15, "mobile": d == "mobile",
                           "OUT_abandon": o == "abandon", "OUT_purchase": o == "purchase"}
                          for ev, d, o in zip(s.events, s.device, s.outcome)])
    fi = fpgrowth(items, min_support=0.03, use_colnames=True)
    rules = association_rules(fi, metric="lift", min_threshold=1.0)
    rules = rules[rules.consequents.apply(lambda c: len(c) == 1 and next(iter(c)).startswith("OUT_"))
                  & rules.antecedents.apply(lambda a: not any(x.startswith("OUT_") for x in a))]
    rules = rules.assign(antecedents=rules.antecedents.apply(lambda x: ", ".join(sorted(x))),
                         consequents=rules.consequents.apply(lambda x: ", ".join(x)))
    top_rules = rules.query("lift > 2.0").sort_values("lift", ascending=False).head(10)
    if top_rules.empty:
        top_rules = rules.sort_values("lift", ascending=False).head(10)
    out.append("## Top association rules (FP-Growth, lift > 2.0)\n\n"
               + top_rules[["antecedents", "consequents", "support", "confidence", "lift"]].round(3).to_markdown(index=False) + "\n")

    # 3. clustering
    feats = pd.DataFrame([aggregate_features(e, g, d) for e, g, d in zip(s.events, s.gaps, s.device)])
    from sklearn.decomposition import PCA
    behaviour = ["n_product_view", "n_review_view", "n_compare", "n_add_to_cart", "n_remove_from_cart",
                 "n_checkout_start", "funnel_stage"]
    X = PCA(n_components=3, random_state=0).fit_transform(StandardScaler().fit_transform(np.log1p(feats[behaviour])))
    np.save(config.DATA / "cluster_xy.npy", X[:, :2])
    hdb = HDBSCAN(min_cluster_size=400, min_samples=30, copy=True).fit(X)
    labels = hdb.labels_
    mask = labels >= 0
    km = KMeans(n_clusters=5, n_init=10, random_state=0).fit(X)
    merged = s[["session_id"]].merge(truth[["session_id", "persona"]], on="session_id")
    metrics = pd.DataFrame([
        dict(method="HDBSCAN", clusters=len(set(labels[mask])), noise_share=float((~mask).mean()),
             silhouette=silhouette_score(X[mask], labels[mask]) if mask.sum() > 50 and len(set(labels[mask])) > 1 else np.nan,
             davies_bouldin=davies_bouldin_score(X[mask], labels[mask]) if len(set(labels[mask])) > 1 else np.nan,
             ari_vs_persona=adjusted_rand_score(merged.persona[mask], labels[mask])),
        dict(method="K-Means (k=5)", clusters=5, noise_share=0.0, silhouette=silhouette_score(X, km.labels_),
             davies_bouldin=davies_bouldin_score(X, km.labels_), ari_vs_persona=adjusted_rand_score(merged.persona, km.labels_)),
    ])
    out.append("## Clustering quality\n\n" + metrics.round(3).to_markdown(index=False) +
               "\n\nARI compares clusters with the simulator's hidden personas (evaluation only).\n")

    feats["cluster"] = labels
    feats["outcome"] = s.outcome.values
    prof = feats[feats.cluster >= 0].groupby("cluster").agg(
        sessions=("event_count", "size"), events=("event_count", "mean"), compares=("n_compare", "mean"),
        reviews=("n_review_view", "mean"), carts=("n_add_to_cart", "mean"), removes=("n_remove_from_cart", "mean"),
        checkout=("n_checkout_start", "mean"),
        conversion=("outcome", lambda o: (o == "purchase").mean()),
        abandonment=("outcome", lambda o: (o == "abandon").mean()))
    prof["label"] = prof.apply(_name_cluster, axis=1)
    out.append("## Behavioral personas discovered (HDBSCAN cluster profiles)\n\n" + prof.round(2).to_markdown() + "\n")
    names = prof["label"].to_dict()
    seg = pd.DataFrame({"session_id": s.session_id,
                        "segment": [names.get(c, "unclustered") for c in labels],
                        "cluster": labels})
    seg.to_parquet(config.DATA / "segments.parquet", index=False)
    (config.REPORTS / "stage5_mining.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out[:1]) + metrics.round(3).to_string(index=False))
    print(prof[["sessions", "conversion", "abandonment", "label"]].round(2).to_string())
    return seg


def _name_cluster(r) -> str:
    if r.checkout >= 0.5 and r.conversion >= 0.5:
        base = "deliberate buyers" if (r.compares + r.reviews) >= 1.0 else "fast converters"
    elif r.compares >= 1.5:
        base = "heavy comparers"
    elif r.removes >= 0.4 or r.abandonment >= 0.6:
        base = "cart abandoners"
    elif r.carts < 0.3:
        base = "researchers" if (r.compares + r.reviews) >= 1.0 else ("bouncers" if r.events < 5 else "browsers")
    else:
        base = "considerers"
    return f"{base} (c{r.name})"


def _is_subseq(pat, seq) -> bool:
    it = iter(seq)
    return all(any(x == p for x in it) for p in pat)


if __name__ == "__main__":
    run()
