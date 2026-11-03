"""Stage 4 - analytical warehouse (star schema) in DuckDB, a columnar OLAP engine that needs no server.
The same DDL ports to ClickHouse (facts) + PostgreSQL (dims); see README "production upgrade".

Whitelisted views (v_*) are the ONLY tables the LLM copilot may query. Every view exposes
session_count so the k-anonymity floor can be enforced.

    python -m biq.warehouse
"""
from __future__ import annotations

import json

import duckdb
import pandas as pd

from . import config

VIEWS = {
    "v_funnel_daily": """
        SELECT CAST(start_ts AS DATE) AS day,
               COUNT(*) AS session_count,
               SUM(reached_product) AS reached_product, SUM(reached_cart) AS reached_cart,
               SUM(reached_checkout) AS reached_checkout, SUM(purchased) AS purchased,
               ROUND(AVG(purchased), 4) AS conversion_rate
        FROM fact_sessions GROUP BY 1 ORDER BY 1""",
    "v_conversion_by_segment": """
        SELECT device, referrer, COALESCE(segment, 'unassigned') AS segment, COUNT(*) AS session_count,
               ROUND(AVG(purchased), 4) AS conversion_rate,
               ROUND(AVG(CASE WHEN outcome='abandon' THEN 1 ELSE 0 END), 4) AS abandonment_rate,
               ROUND(AVG(n_events), 2) AS avg_events
        FROM fact_sessions GROUP BY 1,2,3""",
    "v_segment_summary": """
        SELECT COALESCE(segment, 'unassigned') AS segment, COUNT(*) AS session_count,
               ROUND(AVG(purchased), 4) AS conversion_rate,
               ROUND(AVG(CASE WHEN outcome='abandon' THEN 1 ELSE 0 END), 4) AS abandonment_rate,
               ROUND(AVG(n_events), 2) AS avg_events, ROUND(AVG(duration_s), 1) AS avg_duration_s
        FROM fact_sessions GROUP BY 1""",
    "v_product_performance": """
        SELECT e.product_id, p.category, COUNT(DISTINCT e.session_id) AS session_count,
               SUM(CASE WHEN e.event_type='product_view' THEN 1 ELSE 0 END) AS views,
               SUM(CASE WHEN e.event_type='add_to_cart' THEN 1 ELSE 0 END) AS add_to_carts,
               SUM(CASE WHEN e.event_type='compare' THEN 1 ELSE 0 END) AS compares
        FROM fact_user_events e JOIN dim_product p USING (product_id)
        WHERE e.product_id IS NOT NULL GROUP BY 1,2""",
    "v_category_performance": """
        SELECT p.category, COUNT(DISTINCT e.session_id) AS session_count,
               SUM(CASE WHEN e.event_type='product_view' THEN 1 ELSE 0 END) AS views,
               SUM(CASE WHEN e.event_type='add_to_cart' THEN 1 ELSE 0 END) AS add_to_carts,
               ROUND(SUM(CASE WHEN e.event_type='add_to_cart' THEN 1 ELSE 0 END) * 1.0 /
                     NULLIF(SUM(CASE WHEN e.event_type='product_view' THEN 1 ELSE 0 END), 0), 4) AS cart_rate
        FROM fact_user_events e JOIN dim_product p USING (product_id) GROUP BY 1""",
    "v_session_length_dist": """
        SELECT CASE WHEN n_events < 5 THEN '01-04' WHEN n_events < 10 THEN '05-09'
                    WHEN n_events < 20 THEN '10-19' ELSE '20+' END AS length_bucket,
               outcome, COUNT(*) AS session_count
        FROM fact_sessions GROUP BY 1,2 ORDER BY 1,2""",
    "v_compare_abandonment": """
        SELECT LEAST(n_compare, 4) AS compare_events_capped, COUNT(*) AS session_count,
               ROUND(AVG(CASE WHEN outcome='abandon' THEN 1 ELSE 0 END), 4) AS abandonment_rate,
               ROUND(AVG(purchased), 4) AS conversion_rate
        FROM fact_sessions GROUP BY 1 ORDER BY 1""",
    "v_outcome_summary": """
        SELECT outcome, COUNT(*) AS session_count, ROUND(AVG(n_events), 2) AS avg_events,
               ROUND(AVG(duration_s), 1) AS avg_duration_s FROM fact_sessions GROUP BY 1""",
}

VIEW_DOCS = {
    "v_funnel_daily": "Per day: sessions, how many reached product/cart/checkout/purchase, conversion_rate (0-1).",
    "v_conversion_by_segment": "By device x referrer x behavioral segment: session_count, conversion_rate, abandonment_rate, avg_events.",
    "v_segment_summary": "Per behavioral segment (HDBSCAN persona): session_count, conversion_rate, abandonment_rate, avg_events, avg_duration_s.",
    "v_product_performance": "Per product: category, session_count, views, add_to_carts, compares.",
    "v_category_performance": "Per category: session_count, views, add_to_carts, cart_rate.",
    "v_session_length_dist": "Session-length bucket x outcome: session_count.",
    "v_compare_abandonment": "Number of compare events (capped at 4) vs abandonment_rate and conversion_rate.",
    "v_outcome_summary": "Per outcome (purchase/abandon/browse): session_count, avg_events, avg_duration_s.",
}


def connect(read_only: bool = False):
    return duckdb.connect(str(config.WAREHOUSE), read_only=read_only)


def build(segments: pd.DataFrame | None = None):
    events = pd.read_parquet(config.RAW_EVENTS)
    sessions = pd.read_parquet(config.SESSIONS)
    catalogue = pd.DataFrame(json.loads(config.CATALOGUE.read_text()))
    s = sessions.copy()
    s["duration_s"] = (s.close_ts - s.start_ts).dt.total_seconds()
    s["purchased"] = (s.outcome == "purchase").astype(int)
    for name, et in [("reached_product", "product_view"), ("reached_cart", "add_to_cart"),
                     ("reached_checkout", "checkout_start")]:
        s[name] = s.events.apply(lambda ev, et=et: int(et in list(ev)))
    s["n_compare"] = s.events.apply(lambda ev: int(sum(e == "compare" for e in ev)))
    s["segment"] = None
    if segments is not None:
        s = s.drop(columns="segment").merge(segments[["session_id", "segment"]], on="session_id", how="left")
    fact_sessions_df = s.drop(columns=["events", "categories", "gaps"])
    con = connect()
    # register DataFrames under distinct names: `FROM fact_sessions` would otherwise resolve to the
    # existing table and silently rebuild it from stale data
    con.register("src_events", events)
    con.register("src_sessions", fact_sessions_df)
    con.register("src_catalogue", catalogue)
    con.execute("CREATE OR REPLACE TABLE dim_product AS SELECT * FROM src_catalogue")
    con.execute("CREATE OR REPLACE TABLE dim_device AS SELECT * FROM (VALUES ('desktop'),('mobile'),('tablet')) t(device)")
    con.execute("""CREATE OR REPLACE TABLE dim_event AS SELECT * FROM (VALUES
        ('page_view','navigational'),('search','navigational'),('click','navigational'),('scroll','navigational'),
        ('hover','navigational'),('product_view','consideration'),('review_view','consideration'),
        ('compare','consideration'),('add_to_cart','funnel'),('remove_from_cart','funnel'),
        ('checkout_start','funnel'),('purchase','funnel'),('exit','termination')) t(event_type, category)""")
    con.execute("CREATE OR REPLACE TABLE fact_user_events AS SELECT DISTINCT ON (event_id) * FROM src_events")  # dedup
    con.execute("CREATE OR REPLACE TABLE fact_sessions AS SELECT * FROM src_sessions")
    con.execute("CREATE OR REPLACE TABLE dim_time AS SELECT DISTINCT CAST(start_ts AS DATE) AS day, "
                "dayofweek(start_ts) AS dow FROM fact_sessions")
    con.execute("CREATE OR REPLACE TABLE fact_conversions AS SELECT session_id, close_ts AS ts, device, referrer "
                "FROM fact_sessions WHERE outcome='purchase'")
    con.execute("CREATE TABLE IF NOT EXISTS fact_bandit_decisions (ts TIMESTAMP, session_id VARCHAR, "
                "context VARCHAR, action VARCHAR, reward DOUBLE)")
    for name, sql in VIEWS.items():
        con.execute(f"CREATE OR REPLACE VIEW {name} AS {sql}")
    n_e = con.execute("SELECT COUNT(*) FROM fact_user_events").fetchone()[0]
    n_s = con.execute("SELECT COUNT(*) FROM fact_sessions").fetchone()[0]
    con.close()
    print(f"warehouse built: {n_e:,} events, {n_s:,} sessions, {len(VIEWS)} views -> {config.WAREHOUSE}")


def spot_check(n: int = 10, seed: int = 0) -> int:
    """Rebuild n random sessions from raw events and compare against fact tables (Stage 4 DoD)."""
    raw = pd.read_parquet(config.RAW_EVENTS)
    con = connect(read_only=True)
    sample = con.execute(f"SELECT session_id, n_events, outcome FROM fact_sessions USING SAMPLE {n} ROWS (reservoir, {seed})").df()
    ok = 0
    from .schema import label_session
    for r in sample.itertuples():
        ev = raw[raw.session_id == r.session_id].sort_values("seq").event_type.tolist()
        ok += int(len(ev) == r.n_events and label_session(ev) == r.outcome)
    con.close()
    print(f"spot check: {ok}/{n} sessions match raw logs")
    return ok


if __name__ == "__main__":
    build()
    spot_check()
