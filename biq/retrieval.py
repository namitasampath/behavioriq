"""Stage 7 - vector retrieval engine on Qdrant.

Runs Qdrant in *local mode* (qdrant-client with a folder path) so no server is needed; point
QDRANT_URL at a Qdrant container (http://localhost:6333) and the exact same code uses the server.

Leakage guard: a query only ever sees sessions that CLOSED BEFORE its `as_of` time and never itself.

    python -m biq.retrieval      # index embeddings + evaluate Precision@K / Recall@K / MRR / NDCG / latency
"""
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
from qdrant_client import QdrantClient, models

from . import config

COLLECTIONS = {"biq_custom": "emb_custom.npy", "biq_baseline": "emb_baseline.npy"}
_client: QdrantClient | None = None


def client() -> QdrantClient:
    global _client
    if _client is None:
        url = os.getenv("QDRANT_URL")
        _client = QdrantClient(url=url) if url else QdrantClient(path=str(config.QDRANT_PATH))
    return _client


def build_index(pool_splits=("train",)):
    """Index the retrieval pool (train sessions only) with outcome metadata."""
    idx = pd.read_parquet(config.DATA / "embedding_index.parquet")
    pool = idx[idx.split.isin(pool_splits)]
    c = client()
    for name, f in COLLECTIONS.items():
        emb = np.load(config.DATA / f)
        if c.collection_exists(name):
            c.delete_collection(name)
        c.create_collection(name, vectors_config=models.VectorParams(size=emb.shape[1], distance=models.Distance.COSINE))
        points = [models.PointStruct(id=int(i), vector=emb[i].tolist(),
                                     payload=dict(session_id=r.session_id, outcome=r.outcome, device=r.device,
                                                  close_ts=float(pd.Timestamp(r.close_ts).timestamp())))
                  for i, r in zip(pool.index, pool.itertuples())]
        for b in range(0, len(points), 1000):
            c.upsert(name, points[b:b + 1000])
        for field, schema in (("outcome", "keyword"), ("device", "keyword"), ("close_ts", "float")):
            try:
                c.create_payload_index(name, field, schema)
            except Exception:
                pass  # local mode ignores payload indexes
    print(f"indexed {len(pool):,} sessions into {list(COLLECTIONS)}")


def _filter(as_of: float | None, exclude_session: str | None, device: str | None):
    must, must_not = [], []
    if as_of is not None:
        must.append(models.FieldCondition(key="close_ts", range=models.Range(lt=as_of)))
    if device:
        must.append(models.FieldCondition(key="device", match=models.MatchValue(value=device)))
    if exclude_session:
        must_not.append(models.FieldCondition(key="session_id", match=models.MatchValue(value=exclude_session)))
    return models.Filter(must=must or None, must_not=must_not or None)


def neighbors(vector, k: int = 10, collection: str = "biq_custom", as_of: float | None = None,
              exclude_session: str | None = None, device: str | None = None) -> list[dict]:
    res = client().query_points(collection, query=list(map(float, vector)), limit=k,
                                query_filter=_filter(as_of, exclude_session, device), with_payload=True)
    return [dict(score=p.score, **p.payload) for p in res.points]


def neighbors_batch(vectors, k: int, collection: str, as_of: list[float] | None = None,
                    exclude: list[str] | None = None) -> list[list[dict]]:
    reqs = [models.QueryRequest(query=list(map(float, v)), limit=k, with_payload=True,
                                filter=_filter(as_of[i] if as_of else None, exclude[i] if exclude else None, None))
            for i, v in enumerate(vectors)]
    out = []
    for b in range(0, len(reqs), 256):
        res = client().query_batch_points(collection, reqs[b:b + 256])
        out.extend([[dict(score=p.score, **p.payload) for p in r.points] for r in res])
    return out


def exact_neighbors_batch(vectors, k: int, as_of: list[float] | None = None, exclude: list[str] | None = None,
                          emb_file: str = "emb_custom.npy") -> list[list[dict]]:
    """Exact cosine top-k with the same no-self / no-future filter as `neighbors_batch`, in numpy.
    Used for bulk offline experiments (tens of thousands of queries), where local-mode Qdrant is slow.
    `check_equivalence()` verifies it returns the same neighbours as Qdrant."""
    idx = pd.read_parquet(config.DATA / "embedding_index.parquet")
    pool = idx[idx.split == "train"]
    P = np.load(config.DATA / emb_file)[pool.index]
    close = np.array([pd.Timestamp(t).timestamp() for t in pool.close_ts])
    sid = pool.session_id.to_numpy()
    outc = pool.outcome.astype(str).to_numpy()
    V = np.asarray(vectors, dtype=np.float32)
    out = []
    for b in range(0, len(V), 512):
        S = V[b:b + 512] @ P.T
        for j in range(S.shape[0]):
            i = b + j
            sc = S[j].copy()
            if as_of is not None:
                sc[close >= as_of[i]] = -np.inf
            if exclude is not None:
                sc[sid == exclude[i]] = -np.inf
            top = np.argpartition(-sc, min(k, len(sc) - 1))[:k]
            top = top[np.argsort(-sc[top])]
            out.append([dict(score=float(sc[t]), session_id=sid[t], outcome=outc[t]) for t in top if np.isfinite(sc[t])])
    return out


def check_equivalence(n: int = 50, k: int = 10) -> float:
    idx = pd.read_parquet(config.DATA / "embedding_index.parquet")
    test = idx[idx.split == "test"].head(n)
    V = np.load(config.DATA / "emb_custom.npy")[test.index]
    a = [{x["session_id"] for x in r} for r in neighbors_batch(V, k, "biq_custom")]
    b = [{x["session_id"] for x in r} for r in exact_neighbors_batch(V, k)]
    return float(np.mean([len(x & y) / k for x, y in zip(a, b)]))


def outcome_summary(nbrs: list[dict]) -> dict:
    s = pd.Series([n["outcome"] for n in nbrs]).value_counts()
    return {k: int(v) for k, v in s.items()}


def evaluate(k: int = 10):
    idx = pd.read_parquet(config.DATA / "embedding_index.parquet")
    test = idx[idx.split == "test"]
    y = np.asarray(test.outcome.astype(str))
    pool_counts = idx[idx.split == "train"].outcome.value_counts()
    rows = []
    for name, f in COLLECTIONS.items():
        emb = np.load(config.DATA / f)
        q = emb[test.index]
        t0 = time.perf_counter()
        lat = []
        res = []
        for v in q[:200]:  # latency on single queries
            t1 = time.perf_counter(); res.append(neighbors(v, k, name)); lat.append((time.perf_counter() - t1) * 1000)
        res += neighbors_batch(q[200:], k, name)
        rel = np.array([[n["outcome"] == yy for n in r] + [False] * (k - len(r)) for r, yy in zip(res, y)])
        prec = rel.mean(1)
        recall = rel.sum(1) / np.array([pool_counts[yy] for yy in y])
        first = np.array([np.argmax(r) + 1 if r.any() else np.inf for r in rel])
        mrr = np.mean(1 / first)
        disc = 1 / np.log2(np.arange(2, k + 2))
        ndcg = (rel * disc).sum(1) / disc.sum()
        rows.append(dict(collection=name, **{f"precision@{k}": prec.mean(), f"recall@{k}": recall.mean(),
                                            "MRR": mrr, f"NDCG@{k}": ndcg.mean(),
                                            "p50_ms": np.percentile(lat, 50), "p95_ms": np.percentile(lat, 95)}))
    # brute-force cosine baseline (same results as exact search, used to sanity-check Qdrant + latency)
    emb = np.load(config.DATA / "emb_custom.npy")
    pool = emb[idx.split == "train"]
    lat = []
    for v in emb[test.index][:200]:
        t1 = time.perf_counter(); np.argsort(-(pool @ v))[:k]; lat.append((time.perf_counter() - t1) * 1000)
    rows.append(dict(collection="numpy brute force (custom)", p50_ms=np.percentile(lat, 50), p95_ms=np.percentile(lat, 95)))
    df = pd.DataFrame(rows)
    # abandoning-session check (Stage 7 DoD)
    ab = test[test.outcome == "abandon"].index[:50]
    emb_c = np.load(config.DATA / "emb_custom.npy")
    share = np.mean([np.mean([n["outcome"] == "abandon" for n in neighbors(emb_c[i], 10)]) for i in ab])
    report = ("# Stage 7 - Retrieval evaluation (held-out test sessions, relevance = same outcome)\n\n"
              + df.round(3).to_markdown(index=False)
              + f"\n\nShare of abandoning neighbours for 50 abandoning test sessions: {share:.2f} "
                f"(base rate in pool {pool_counts['abandon'] / pool_counts.sum():.2f}).\n")
    (config.REPORTS / "stage7_retrieval.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    build_index()
    evaluate()


import atexit  # noqa: E402


@atexit.register
def _close():
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
        _client = None
