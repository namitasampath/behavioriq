"""Stage 3 - feature store with an online store (SQLite, latest vector per session) and an
offline store (Parquet, full point-in-time history). Same feature definition for both, so
there is no training/serving skew. API mirrors Feast: get_online_features / get_historical_features.
(The Feast + Redis version of this module is the production upgrade path; see README.)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pandas as pd

from . import config


class FeatureStore:
    def __init__(self, reset: bool = False):
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
        self.db.execute("INSERT OR REPLACE INTO online VALUES (?,?,?)",
                        (f["session_id"], f["event_timestamp"], json.dumps(f)))
        self.buffer.append(feats)

    def flush(self):
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
