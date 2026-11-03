"""Stage 1 - synthetic traffic generator.

Five latent personas drive a Markov chain over the event vocabulary. Persona parameters are
jittered per session and 10% of transitions are random noise, so personas overlap and the
outcome is only partly determined by persona (otherwise every model would score ~100%).

Usage:
    python -m biq.simulator --sessions 6000                 # write parquet (offline / training)
    python -m biq.simulator --sessions 200 --http http://127.0.0.1:8000/track --rate 50
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import config
from .schema import DEVICES, REFERRERS, Event, label_session

CATEGORIES = ["electronics", "fashion", "home", "beauty", "sports", "books"]

# per-persona behaviour knobs (probabilities applied while browsing a product)
PERSONAS = {
    "fast_converter": dict(w=0.18, cart=0.45, review=0.08, compare=0.04, exit=0.05, checkout=0.75, buy=0.85, remove=0.05, dwell=4000, mobile=0.35),
    "heavy_comparer": dict(w=0.22, cart=0.12, review=0.25, compare=0.28, exit=0.07, checkout=0.45, buy=0.65, remove=0.15, dwell=9000, mobile=0.30),
    "explorer":       dict(w=0.25, cart=0.05, review=0.15, compare=0.06, exit=0.12, checkout=0.35, buy=0.55, remove=0.10, dwell=7000, mobile=0.50),
    "deal_hunter":    dict(w=0.15, cart=0.20, review=0.10, compare=0.15, exit=0.10, checkout=0.50, buy=0.45, remove=0.20, dwell=5000, mobile=0.65),
    "abandoner":      dict(w=0.20, cart=0.30, review=0.15, compare=0.12, exit=0.09, checkout=0.30, buy=0.20, remove=0.30, dwell=6000, mobile=0.45),
}
NOISE = 0.10
MAX_EVENTS = 60


def make_catalogue(rng: np.random.Generator, n: int = 200) -> list[dict]:
    cat = []
    for i in range(n):
        c = CATEGORIES[i % len(CATEGORIES)]
        cat.append(dict(product_id=f"product_{i}", category=c,
                        price=round(float(rng.lognormal(3.5, 0.8)), 2),
                        rating=round(float(rng.uniform(2.5, 5.0)), 1),
                        review_count=int(rng.integers(0, 2000))))
    return cat


def _jitter(p: dict, rng) -> dict:
    q = dict(p)
    for k in ("cart", "review", "compare", "exit", "checkout", "buy", "remove"):
        q[k] = float(np.clip(p[k] * rng.lognormal(0, 0.35), 0.01, 0.95))
    return q


def simulate_session(rng, persona: str, start: datetime, catalogue: list[dict], idx: int) -> list[dict]:
    p = _jitter(PERSONAS[persona], rng)
    sid, uid = f"s_{idx:06d}", f"u_{uuid.UUID(int=int(rng.integers(0, 2**63))).hex[:8]}"
    device = "mobile" if rng.random() < p["mobile"] else ("tablet" if rng.random() < 0.15 else "desktop")
    referrer = REFERRERS[rng.choice(len(REFERRERS), p=[0.3, 0.3, 0.15, 0.1, 0.15])]
    fav_cat = CATEGORIES[rng.integers(len(CATEGORIES))]
    cat_products = [x for x in catalogue if x["category"] == fav_cat]
    events, t, state, product, in_cart = [], start, "start", None, 0

    def emit(et: str, prod=None, page=None):
        nonlocal t
        dwell = int(rng.exponential(p["dwell"])) + 300
        t = t + timedelta(milliseconds=dwell)
        events.append(dict(
            event_id=str(uuid.UUID(int=int(rng.integers(0, 2**63)))), session_id=sid, anon_user_id=uid,
            event_type=et, timestamp=t, page=page or ("/product/" + prod["product_id"].split("_")[1] if prod else "/" + et),
            product_id=prod["product_id"] if prod else None, category=prod["category"] if prod else None,
            device=device, referrer=referrer, scroll_depth=round(float(rng.beta(2, 3)), 2),
            dwell_ms=dwell, seq=len(events)))

    emit("page_view", page="/")
    while len(events) < MAX_EVENTS:
        r = rng.random()
        if r < NOISE:  # noise transition
            et = rng.choice(["search", "click", "scroll", "hover", "product_view", "review_view", "compare"])
            product = cat_products[rng.integers(len(cat_products))] if et in ("product_view", "review_view", "compare") else product
            emit(et, product if et in ("product_view", "review_view", "compare") else None)
            continue
        if state in ("start", "browse"):
            if rng.random() < 0.35:
                emit(rng.choice(["search", "scroll", "click", "hover"]))
            pool = cat_products if rng.random() < 0.8 else catalogue
            product = pool[rng.integers(len(pool))]
            emit("product_view", product)
            state = "product"
        elif state == "product":
            u = rng.random()
            acc = 0.0
            for et, key in (("add_to_cart", "cart"), ("review_view", "review"), ("compare", "compare"), ("exit", "exit")):
                acc += p[key]
                if u < acc:
                    break
            else:
                et = "browse"
            if et == "add_to_cart":
                emit("add_to_cart", product); in_cart += 1; state = "cart"
            elif et in ("review_view", "compare"):
                emit(et, product)
            elif et == "exit":
                emit("exit"); break
            else:
                state = "browse"
        elif state == "cart":
            u = rng.random()
            if u < p["checkout"]:
                emit("checkout_start", page="/checkout"); state = "checkout"
            elif u < p["checkout"] + p["remove"]:
                emit("remove_from_cart", product); in_cart = max(0, in_cart - 1); state = "browse"
            elif u < p["checkout"] + p["remove"] + p["exit"]:
                emit("exit"); break
            else:
                state = "browse"
        elif state == "checkout":
            if rng.random() < p["buy"]:
                emit("purchase", page="/confirmation"); break
            emit("exit" if rng.random() < 0.6 else "product_view", product)
            if events[-1]["event_type"] == "exit":
                break
            state = "product"
    return events


def daily_start(rng, day0: datetime, days: int) -> datetime:
    d = int(rng.integers(days))
    hour = float(np.clip(rng.normal(15, 4.5), 0, 23.9))  # afternoon/evening peak
    return day0 + timedelta(days=d, hours=hour)


def generate(n_sessions: int, seed: int = config.SEED, days: int = 14, persona_override: dict | None = None):
    rng = np.random.default_rng(seed)
    catalogue = make_catalogue(np.random.default_rng(0))
    names = list(PERSONAS)
    weights = np.array([PERSONAS[k]["w"] for k in names])
    if persona_override:
        weights = np.array([persona_override.get(k, PERSONAS[k]["w"]) for k in names])
    weights = weights / weights.sum()
    day0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows, truth = [], []
    for i in range(n_sessions):
        persona = names[rng.choice(len(names), p=weights)]
        ev = simulate_session(rng, persona, daily_start(rng, day0, days), catalogue, i)
        rows.extend(ev)
        truth.append(dict(session_id=ev[0]["session_id"], persona=persona,
                          outcome=label_session([e["event_type"] for e in ev])))
    events = pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)
    return events, pd.DataFrame(truth), catalogue


def to_event_dict(row: dict) -> dict:
    """Flat dataframe row -> canonical JSON event (validated)."""
    row = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()}
    ev = Event(event_id=row["event_id"], session_id=row["session_id"], anon_user_id=row["anon_user_id"],
               event_type=row["event_type"], timestamp=row["timestamp"], page=row["page"],
               product_id=row["product_id"], category=row["category"], device=row["device"],
               referrer=row["referrer"],
               meta=dict(scroll_depth=row["scroll_depth"], dwell_ms=row["dwell_ms"], seq=row["seq"]))
    return json.loads(ev.model_dump_json())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=config.N_SESSIONS)
    ap.add_argument("--seed", type=int, default=config.SEED)
    ap.add_argument("--http", default="", help="POST events to this ingestion URL instead of writing parquet")
    ap.add_argument("--rate", type=float, default=100.0, help="events/second when posting")
    ap.add_argument("--realtime", action="store_true", help="stamp events with the current time (live demo)")
    args = ap.parse_args()
    events, truth, catalogue = generate(args.sessions, args.seed)
    if args.http:
        import requests
        if args.realtime:  # shift so the stream looks live
            events["timestamp"] = events["timestamp"] - events["timestamp"].min() + datetime.now(timezone.utc)
        batch, sent = [], 0
        for row in events.to_dict("records"):
            batch.append(to_event_dict(row))
            if len(batch) >= 10:
                r = requests.post(args.http, json=batch, timeout=10); r.raise_for_status()
                sent += len(batch); batch = []
                time.sleep(10 / args.rate)
        if batch:
            requests.post(args.http, json=batch, timeout=10).raise_for_status(); sent += len(batch)
        print(f"posted {sent} events from {args.sessions} sessions")
        return
    events.to_parquet(config.RAW_EVENTS, index=False)
    truth.to_parquet(config.TRUTH, index=False)
    config.CATALOGUE.write_text(json.dumps(catalogue, indent=1))
    print(f"wrote {len(events):,} events / {len(truth):,} sessions -> {config.RAW_EVENTS}")
    print(truth.groupby("persona").outcome.value_counts(normalize=True).unstack().round(2))


if __name__ == "__main__":
    main()
