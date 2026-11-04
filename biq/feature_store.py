"""Stage 3 - feature store with an online store (latest vector per session: SQLite by default,
Redis when FEATURE_STORE=redis) and an offline store (Parquet, full point-in-time history). Same
feature definition feeds both, so there is no training/serving skew. API mirrors Feast:
get_online_features / get_historical_features.

The production upgrade path here was meant to be Feast with a Redis online store, but feast
0.67.0 cannot install into this project's venv without downgrading pandas 3.1.0 -> 2.x (and
prometheus-client, which Upgrade 3 depends on) - see docs/PROGRESS.md ("Upgrade 4") for the
dry-run proof. So FEATURE_STORE=redis instead talks to Redis directly via redis-py behind this
same FeatureStore interface; FEATURE_STORE unset (default) keeps the original SQLite online
store. The Parquet offline store and get_historical_features() are identical either way.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pandas as pd

from . import config

_REDIS_KEY_PREFIX = "biq:online:"


class FeatureStore:
    def __init__(self, reset: bool = False):
        self.backend = config.FEATURE_STORE_BACKEND
        if self.backend == "redis":
            import redis
            self.r = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
            if reset:
                keys = list(self.r.scan_iter(match=_REDIS_KEY_PREFIX + "*"))
                if keys:
                    self.r.delete(*keys)
                config.FEATURE_LOG.unlink(missing_ok=True)
        else:
            self.db = sqlite3.connect(config.ONLINE_DB, check_same_thread=False)
            if reset:
                self.db.execute("DROP TABLE IF EXISTS online")
                config.FEATURE_LOG.unlink(missing_ok=True)
            self.db.execute("CREATE TABLE IF NOT EXISTS online (session_id TEXT PRIMARY KEY, ts TEXT, features TEXT)")
        self.buffer: list[dict] = []

    def write(self, feats: dict):
        f = dict(feats)
        ts = f["event_timestamp"]
        f["event_timestamp"] = ts.isoformat() if isinstance(ts, datetime) else str(ts)
        if self.backend == "redis":
            self.r.set(_REDIS_KEY_PREFIX + f["session_id"], json.dumps(f))
        else:
            self.db.execute("INSERT OR REPLACE INTO online VALUES (?,?,?)",
                            (f["session_id"], f["event_timestamp"], json.dumps(f)))
        self.buffer.append(feats)

    def flush(self):
        if self.backend != "redis":
            self.db.commit()
        if not self.buffer:
            return
        new = pd.DataFrame(self.buffer)
        new["event_timestamp"] = pd.to_datetime(new["event_timestamp"], utc=True, format="ISO8601")
        if config.FEATURE_LOG.exists():
            new = pd.concat([pd.read_parquet(config.FEATURE_LOG), new], ignore_index=True)
        new.to_parquet(config.FEATURE_LOG, index=False)
        self.buffer = []

    def get_online_features(self, session_id: str) -> dict | None:
        if self.backend == "redis":
            raw = self.r.get(_REDIS_KEY_PREFIX + session_id)
            return json.loads(raw) if raw else None
        row = self.db.execute("SELECT features FROM online WHERE session_id=?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None


def get_historical_features(entity_df: pd.DataFrame) -> pd.DataFrame:
    """Point-in-time join: for each (session_id, event_timestamp) return the latest feature row at or before it."""
    hist = pd.read_parquet(config.FEATURE_LOG).sort_values("event_timestamp")
    ent = entity_df.copy()
    ent["event_timestamp"] = pd.to_datetime(ent["event_timestamp"], utc=True)
    ent = ent.sort_values("event_timestamp")
    out = pd.merge_asof(ent, hist, on="event_timestamp", by="session_id", direction="backward",
                        suffixes=("", "_feat"))
    return out
