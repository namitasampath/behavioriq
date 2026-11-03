"""Stage 1 (real-data variant) - replay the REES46 "eCommerce behavior data from multi category
store" CSV (data/external/*.csv) into our canonical event schema (biq/schema.py), then
re-sessionise it with OUR rule instead of REES46's own `user_session` id.

REES46 column -> canonical mapping:
  event_type   view -> product_view, cart -> add_to_cart, remove_from_cart -> remove_from_cart,
               purchase -> purchase (anything else is dropped, with a count printed)
  category_code  top-level token before the first '.' -> category (missing/empty -> None)
  product_id   -> product_id (str); page is built as "/product/{product_id}"
  user_id      -> anon_user_id, salted SHA-256 hash; never the raw id
  user_session -> the grouping key fed into our own 30-min/purchase re-sessionisation (see
                  resessionize()), which then gets its own salted-hash session_id

REES46 has no device or referrer column at all. There is nothing to sample or infer it from
(unlike the missing event types below, which we can at least name), so every replayed event is
stamped device="desktop", referrer="direct" as a schema-compliance placeholder. This is written
up in docs/CONTRACTS.md - device/referrer-segmented results must be ignored for this dataset.

The CSV is several GB (42M+ rows for one month); it is read through DuckDB's out-of-core CSV
reader (never materialised in memory as one block - this IS the "read in chunks" step, just done
by a columnar query engine instead of a hand-rolled chunk loop) to pick N whole sessions at
random and pull only their rows.

    python -m biq.replay --sessions 30000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from . import config
from .schema import EventMeta, Event, SESSION_TIMEOUT_S, TERMINAL_EVENTS

EVENT_MAP = {"view": "product_view", "cart": "add_to_cart",
             "remove_from_cart": "remove_from_cart", "purchase": "purchase"}
SALT = "biq-rees46-replay-v1"          # local pseudonymisation salt; see docs/CONTRACTS.md
DEVICE_PLACEHOLDER = "desktop"
REFERRER_PLACEHOLDER = "direct"


def find_csv(explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise FileNotFoundError(p)
        return p
    cands = sorted((config.ROOT / "data" / "external").glob("*.csv"))
    if not cands:
        raise FileNotFoundError("no CSV found in data/external/ - place the REES46 CSV there")
    return cands[0]


def _hash(prefix: str, value) -> str:
    return prefix + hashlib.sha256(f"{SALT}:{value}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------- sample + extract (DuckDB, out-of-core)
def sample_and_extract(csv_path: Path, n: int, seed: int) -> pd.DataFrame:
    """Pick n whole sessions at random (reservoir sample over the distinct user_session values,
    computed inside DuckDB so the 9M+ distinct ids never pass through Python), then pull every
    row belonging to those sessions."""
    con = duckdb.connect()
    con.execute(f"PRAGMA threads={max(1, os.cpu_count() or 1)}")
    src = f"read_csv_auto('{csv_path.as_posix()}')"
    con.execute(f"""
        CREATE TEMP TABLE sample_ids AS
        SELECT user_session FROM (SELECT DISTINCT user_session FROM {src})
        USING SAMPLE {int(n)} ROWS (reservoir, {int(seed)})
    """)
    n_total_sessions = con.execute(f"SELECT COUNT(DISTINCT user_session) FROM {src}").fetchone()[0]
    df = con.execute(f"""
        SELECT
            event_time,
            event_type,
            CAST(product_id AS VARCHAR) AS product_id,
            CASE WHEN category_code IS NULL OR category_code = '' THEN NULL
                 ELSE split_part(category_code, '.', 1) END AS category,
            CAST(price AS DOUBLE) AS price,
            CAST(user_id AS VARCHAR) AS user_id,
            user_session
        FROM {src}
        WHERE user_session IN (SELECT user_session FROM sample_ids)
        ORDER BY user_session, event_time
    """).df()
    con.close()
    df["event_time"] = df["event_time"].dt.tz_localize("UTC")
    print(f"sampled {df.user_session.nunique():,} of {n_total_sessions:,} source sessions "
          f"-> {len(df):,} raw rows")
    return df


# ---------------------------------------------------------------- re-sessionisation (OUR rule)
def resessionize(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Split each REES46 `user_session` into one or more final sessions under the SAME rule
    `stream_processor` enforces (30 min inactivity, or a terminal purchase event, closes a
    session). Returns the frame with new `session_id_src`/`seq`/`dwell_ms` columns (one row per
    source event) plus, per source session, how many final segments it became."""
    df = df.sort_values(["user_session", "event_time"]).reset_index(drop=True)
    timeout = pd.Timedelta(seconds=SESSION_TIMEOUT_S)
    seg_count: dict[str, int] = {}
    sid_col = np.empty(len(df), dtype=object)
    seq_col = np.empty(len(df), dtype=np.int64)
    dwell_col = np.zeros(len(df), dtype=np.int64)

    pos = 0
    for src, g in df.groupby("user_session", sort=False):
        times = g["event_time"].to_numpy()
        etypes = g["event_type"].to_numpy()
        seg, k, last_t, last_et = 0, 0, None, None
        start = pos
        for i in range(len(g)):
            t, et = times[i], etypes[i]
            if last_t is not None:
                gap_ms = int((t - last_t) / np.timedelta64(1, "ms"))
                if (t - last_t) > timeout or last_et in TERMINAL_EVENTS:
                    seg += 1
                    k = 0
                else:
                    dwell_col[pos] = gap_ms
            sid_col[pos] = src if seg == 0 else f"{src}#{seg}"
            seq_col[pos] = k
            k += 1
            last_t, last_et = t, et
            pos += 1
        seg_count[src] = seg + 1
        assert pos == start + len(g)

    df = df.assign(session_id_src=sid_col, seq=seq_col, dwell_ms=dwell_col)
    return df, seg_count


# ---------------------------------------------------------------- canonical events + catalogue
def to_canonical(df: pd.DataFrame) -> pd.DataFrame:
    mapped = df["event_type"].map(EVENT_MAP)
    dropped = int(mapped.isna().sum())
    if dropped:
        print(f"dropping {dropped:,} rows with an event_type outside {sorted(EVENT_MAP)}: "
              f"{df.loc[mapped.isna(), 'event_type'].value_counts().to_dict()}")
    df = df.loc[mapped.notna()].copy()
    df["event_type"] = mapped.loc[df.index]
    df = df.reset_index(drop=True)

    out = pd.DataFrame(index=df.index)
    out["event_id"] = [str(uuid.uuid4()) for _ in range(len(df))]
    out["session_id"] = df["session_id_src"].map(lambda s: _hash("s_", s))
    out["anon_user_id"] = df["user_id"].map(lambda u: _hash("u_", u))
    out["event_type"] = df["event_type"]
    out["timestamp"] = df["event_time"]
    out["page"] = "/product/" + df["product_id"].astype(str)
    out["product_id"] = df["product_id"]
    out["category"] = df["category"]
    out["device"] = DEVICE_PLACEHOLDER
    out["referrer"] = REFERRER_PLACEHOLDER
    out["scroll_depth"] = 0.0           # not captured by REES46; schema default
    out["dwell_ms"] = df["dwell_ms"]
    out["seq"] = df["seq"]
    out["category"] = out["category"].astype(object).where(out["category"].notna(), None)
    return out.sort_values("timestamp").reset_index(drop=True)


def build_catalogue(raw: pd.DataFrame) -> list[dict]:
    g = raw.groupby("product_id").agg(
        category=("category", lambda c: next((x for x in c if pd.notna(x)), None)),
        price=("price", "mean"),
    ).reset_index()
    g["price"] = g["price"].round(2)
    # not in REES46: kept as placeholders so dim_product has the same columns the simulator's
    # catalogue does (biq/warehouse_clickhouse.py's Postgres table requires them; no VIEW ever
    # selects them)
    g["rating"] = np.nan
    g["review_count"] = 0
    g = g.where(pd.notna(g), None)
    return g.to_dict("records")


def validate_sample(events: pd.DataFrame, k: int = 200):
    """Pydantic-validate a random sample of rows against the canonical schema (cheap sanity check,
    not a full per-row pass over the whole frame)."""
    rng = np.random.default_rng(config.SEED)
    idx = rng.choice(len(events), size=min(k, len(events)), replace=False)
    for i in idx:
        r = events.iloc[int(i)]
        Event(event_id=r.event_id, session_id=r.session_id, anon_user_id=r.anon_user_id,
              event_type=r.event_type, timestamp=r.timestamp, page=r.page, product_id=r.product_id,
              category=r.category, device=r.device, referrer=r.referrer,
              meta=EventMeta(scroll_depth=r.scroll_depth, dwell_ms=int(r.dwell_ms), seq=int(r.seq)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=int(os.getenv("BIQ_REES46_SESSIONS", "30000")))
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--csv", default="", help="path to the REES46 CSV (default: first *.csv in data/external/)")
    args = ap.parse_args()

    csv_path = find_csv(args.csv)
    print(f"source file: {csv_path} ({csv_path.stat().st_size / 1e9:.2f} GB)")
    raw = sample_and_extract(csv_path, args.sessions, args.seed)

    resessionized, seg_count = resessionize(raw)
    n_source = len(seg_count)
    n_changed = sum(1 for v in seg_count.values() if v > 1)
    n_final = sum(seg_count.values())
    print(f"re-sessionised with OUR rule (30-min inactivity OR purchase): {n_source:,} source "
          f"sessions -> {n_final:,} final sessions; {n_changed:,} source sessions "
          f"({n_changed / n_source:.1%}) were split into more than one final session.")

    events = to_canonical(resessionized)
    validate_sample(events)
    catalogue = build_catalogue(raw)

    events.to_parquet(config.RAW_EVENTS, index=False)
    config.CATALOGUE.write_text(json.dumps(catalogue, indent=1))
    config.TRUTH.unlink(missing_ok=True)   # no hidden personas for real data - must not be read downstream

    print(f"wrote {len(events):,} events / {events.session_id.nunique():,} sessions "
          f"/ {len(catalogue):,} catalogue products -> {config.RAW_EVENTS}")
    print(events.event_type.value_counts().to_string())


if __name__ == "__main__":
    main()
