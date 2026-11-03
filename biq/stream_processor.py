"""Stage 3 - real-time session processor + feature store writer.

Plays the role Apache Flink plays in the full architecture: keyed per-session state, rolling
features updated on every event, an event-time watermark, and session closing on 30 min of
inactivity or a purchase/exit event. Features go to the online store (SQLite) and the offline
store (Parquet) from ONE definition, which guarantees training/serving parity.

    python -m biq.stream_processor --replay      # batch-replay data/raw_events.parquet as a stream
    python -m biq.stream_processor --live        # tail the event bus fed by the ingestion API
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from . import config
from .feature_store import FeatureStore
from .schema import SESSION_TIMEOUT_S, TERMINAL_EVENTS, label_session

FUNNEL = {"page_view": 0, "search": 1, "product_view": 2, "review_view": 3, "compare": 3,
          "add_to_cart": 4, "checkout_start": 5, "purchase": 6}

FEATURE_COLUMNS = ["event_count", "distinct_products", "n_search", "n_product_view", "n_review_view",
                   "n_compare", "n_add_to_cart", "n_remove_from_cart", "n_checkout_start",
                   "funnel_stage", "seconds_since_last_event", "total_dwell_ms", "session_duration_s",
                   "mean_scroll_depth", "is_mobile"]


@dataclass
class SessionState:
    session_id: str
    device: str
    referrer: str
    start: datetime
    last: datetime
    events: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    products: set = field(default_factory=set)
    counts: dict = field(default_factory=dict)
    dwell: int = 0
    scroll_sum: float = 0.0
    stage: int = 0
    last_seq: int = -1
    out_of_order: int = 0

    def update(self, ev: dict) -> dict:
        ts = _ts(ev["timestamp"])
        self.gaps.append(max(0.0, (ts - self.last).total_seconds()) if self.events else 0.0)
        gap = self.gaps[-1]
        seq = ev.get("meta", {}).get("seq", ev.get("seq", self.last_seq + 1))
        if seq <= self.last_seq:
            self.out_of_order += 1
        self.last_seq = seq
        et = ev["event_type"]
        self.events.append(et)
        self.categories.append(ev.get("category"))
        self.counts[et] = self.counts.get(et, 0) + 1
        if ev.get("product_id"):
            self.products.add(ev["product_id"])
        meta = ev.get("meta", ev)
        self.dwell += int(meta.get("dwell_ms", 0))
        self.scroll_sum += float(meta.get("scroll_depth", 0.0))
        self.stage = max(self.stage, FUNNEL.get(et, 0))
        self.last = ts
        return self.features(gap)

    def features(self, gap: float = 0.0) -> dict:
        c = self.counts
        n = len(self.events)
        return dict(
            session_id=self.session_id, event_timestamp=self.last, event_count=n,
            distinct_products=len(self.products), n_search=c.get("search", 0),
            n_product_view=c.get("product_view", 0), n_review_view=c.get("review_view", 0),
            n_compare=c.get("compare", 0), n_add_to_cart=c.get("add_to_cart", 0),
            n_remove_from_cart=c.get("remove_from_cart", 0), n_checkout_start=c.get("checkout_start", 0),
            funnel_stage=self.stage, seconds_since_last_event=gap, total_dwell_ms=self.dwell,
            session_duration_s=(self.last - self.start).total_seconds(),
            mean_scroll_depth=self.scroll_sum / max(n, 1), is_mobile=int(self.device == "mobile"))

    def close(self, reason: str) -> dict:
        return dict(session_id=self.session_id, start_ts=self.start, close_ts=self.last, device=self.device,
                    referrer=self.referrer, n_events=len(self.events), events=list(self.events),
                    categories=[c or "none" for c in self.categories], gaps=list(self.gaps),
                    outcome=label_session(self.events), close_reason=reason,
                    out_of_order=self.out_of_order)


def _ts(x) -> datetime:
    return x if isinstance(x, datetime) else pd.Timestamp(x).to_pydatetime()


class SessionProcessor:
    def __init__(self, store: FeatureStore | None = None, snapshot_every: int = 1):
        self.state: dict[str, SessionState] = {}
        self.closed: list[dict] = []
        self.store = store
        self.watermark: datetime | None = None
        self.snapshot_every = snapshot_every

    def on_event(self, ev: dict) -> dict:
        sid = ev["session_id"]
        ts = _ts(ev["timestamp"])
        st = self.state.get(sid)
        if st and (ts - st.last).total_seconds() > SESSION_TIMEOUT_S:  # same id after timeout -> new session
            self._close(sid, "timeout")
            st = None
        if st is None:
            st = self.state[sid] = SessionState(sid, ev["device"], ev["referrer"], ts, ts)
        feats = st.update(ev)
        if self.store and len(st.events) % self.snapshot_every == 0:
            self.store.write(feats)
        if ev["event_type"] in TERMINAL_EVENTS:
            self._close(sid, ev["event_type"])
        self._advance_watermark(ts)
        return feats

    def _advance_watermark(self, ts: datetime):
        if self.watermark is None or ts > self.watermark:
            self.watermark = ts
        # fire inactivity timers occasionally (cheap sweep)
        if len(self.state) > 500 or (len(self.closed) % 200 == 0):
            self.sweep()

    def sweep(self):
        if self.watermark is None:
            return
        expired = [s for s, st in self.state.items()
                   if (self.watermark - st.last).total_seconds() > SESSION_TIMEOUT_S]
        for s in expired:
            self._close(s, "timeout")

    def _close(self, sid: str, reason: str):
        st = self.state.pop(sid, None)
        if st:
            self.closed.append(st.close(reason))

    def flush_all(self):
        for s in list(self.state):
            self._close(s, "end_of_stream")


def replay():
    events = pd.read_parquet(config.RAW_EVENTS).sort_values("timestamp")
    store = FeatureStore(reset=True)
    proc = SessionProcessor(store)
    for row in events.to_dict("records"):
        proc.on_event(row)
    proc.flush_all()
    store.flush()
    sessions = pd.DataFrame(proc.closed)
    sessions.to_parquet(config.SESSIONS, index=False)
    print(f"processed {len(events):,} events -> {len(sessions):,} closed sessions; "
          f"out-of-order events: {int(sessions.out_of_order.sum())}")
    print(sessions.close_reason.value_counts().to_string())


def live():
    from .bus import consume
    store = FeatureStore()
    proc = SessionProcessor(store)
    n = 0
    print("listening on the event bus... (Ctrl+C to stop)")
    try:
        for ev in consume(follow=True, from_start=False):
            proc.on_event(ev)
            n += 1
            if n % 50 == 0:
                store.flush()
                print(f"{n} events, {len(proc.state)} open sessions, {len(proc.closed)} closed")
    except KeyboardInterrupt:
        store.flush()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--replay", action="store_true")
    g.add_argument("--live", action="store_true")
    a = ap.parse_args()
    replay() if a.replay else live()
