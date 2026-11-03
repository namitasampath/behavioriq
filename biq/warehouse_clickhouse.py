"""ClickHouse (facts) + PostgreSQL (dims) backend for the Stage 4 warehouse.

Activated by setting WAREHOUSE=clickhouse (see biq/warehouse.py, which delegates connect()/
build()/spot_check() here). Dimensions live in PostgreSQL; ClickHouse reads them through the
PostgreSQL table engine. fact_user_events is continuously fed by a ClickHouse Kafka-engine table
+ materialized view reading the Redpanda topic "user-events" (no extra Python process needed -
ClickHouse keeps consuming on its own once the objects exist). The 8 whitelisted v_* views are
identical in name and columns to the DuckDB versions so biq/copilot.py and dashboard/app.py are
unaffected.

fact_user_events is append-only across rebuilds (never truncated): unlike DuckDB's full
CREATE OR REPLACE TABLE, truncating here would destroy rows the Kafka materialized view inserted
between runs. Duplicate event_ids (from rerunning build() on the same parquet, or from Kafka's
at-least-once delivery) are deduplicated by ReplacingMergeTree(ingested_at) + FINAL at query time.

    python -m biq.warehouse_clickhouse --latency [n]
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

import pandas as pd

from . import config
from .warehouse import VIEWS

DIM_DEVICE_ROWS = [("desktop",), ("mobile",), ("tablet",)]
DIM_EVENT_ROWS = [
    ("page_view", "navigational"), ("search", "navigational"), ("click", "navigational"),
    ("scroll", "navigational"), ("hover", "navigational"), ("product_view", "consideration"),
    ("review_view", "consideration"), ("compare", "consideration"), ("add_to_cart", "funnel"),
    ("remove_from_cart", "funnel"), ("checkout_start", "funnel"), ("purchase", "funnel"),
    ("exit", "termination"),
]

CH_VIEWS = {
    "v_funnel_daily": """
        SELECT day, session_count, reached_product, reached_cart, reached_checkout, purchased,
               round(purchased / session_count, 4) AS conversion_rate
        FROM (
            SELECT toDate(start_ts) AS day, COUNT(*) AS session_count,
                   SUM(reached_product) AS reached_product, SUM(reached_cart) AS reached_cart,
                   SUM(reached_checkout) AS reached_checkout, SUM(purchased) AS purchased
            FROM biq.fact_sessions GROUP BY 1
        ) ORDER BY day""",
    "v_conversion_by_segment": """
        SELECT device, referrer, coalesce(segment, 'unassigned') AS segment, COUNT(*) AS session_count,
               round(AVG(purchased), 4) AS conversion_rate,
               round(AVG(CASE WHEN outcome='abandon' THEN 1 ELSE 0 END), 4) AS abandonment_rate,
               round(AVG(n_events), 2) AS avg_events
        FROM biq.fact_sessions GROUP BY 1,2,3""",
    "v_segment_summary": """
        SELECT coalesce(segment, 'unassigned') AS segment, COUNT(*) AS session_count,
               round(AVG(purchased), 4) AS conversion_rate,
               round(AVG(CASE WHEN outcome='abandon' THEN 1 ELSE 0 END), 4) AS abandonment_rate,
               round(AVG(n_events), 2) AS avg_events, round(AVG(duration_s), 1) AS avg_duration_s
        FROM biq.fact_sessions GROUP BY 1""",
    "v_product_performance": """
        SELECT e.product_id, p.category AS category, COUNT(DISTINCT e.session_id) AS session_count,
               SUM(CASE WHEN e.event_type='product_view' THEN 1 ELSE 0 END) AS views,
               SUM(CASE WHEN e.event_type='add_to_cart' THEN 1 ELSE 0 END) AS add_to_carts,
               SUM(CASE WHEN e.event_type='compare' THEN 1 ELSE 0 END) AS compares
        FROM (SELECT * FROM biq.fact_user_events FINAL) AS e JOIN biq.dim_product AS p USING (product_id)
        WHERE e.product_id IS NOT NULL GROUP BY 1,2""",
    "v_category_performance": """
        SELECT p.category AS category, COUNT(DISTINCT e.session_id) AS session_count,
               SUM(CASE WHEN e.event_type='product_view' THEN 1 ELSE 0 END) AS views,
               SUM(CASE WHEN e.event_type='add_to_cart' THEN 1 ELSE 0 END) AS add_to_carts,
               round(SUM(CASE WHEN e.event_type='add_to_cart' THEN 1 ELSE 0 END) * 1.0 /
                     nullif(SUM(CASE WHEN e.event_type='product_view' THEN 1 ELSE 0 END), 0), 4) AS cart_rate
        FROM (SELECT * FROM biq.fact_user_events FINAL) AS e JOIN biq.dim_product AS p USING (product_id) GROUP BY 1""",
    "v_session_length_dist": """
        SELECT CASE WHEN n_events < 5 THEN '01-04' WHEN n_events < 10 THEN '05-09'
                    WHEN n_events < 20 THEN '10-19' ELSE '20+' END AS length_bucket,
               outcome, COUNT(*) AS session_count
        FROM biq.fact_sessions GROUP BY 1,2 ORDER BY 1,2""",
    "v_compare_abandonment": """
        SELECT least(n_compare, 4) AS compare_events_capped, COUNT(*) AS session_count,
               round(AVG(CASE WHEN outcome='abandon' THEN 1 ELSE 0 END), 4) AS abandonment_rate,
               round(AVG(purchased), 4) AS conversion_rate
        FROM biq.fact_sessions GROUP BY 1 ORDER BY 1""",
    "v_outcome_summary": """
        SELECT outcome, COUNT(*) AS session_count, round(AVG(n_events), 2) AS avg_events,
               round(AVG(duration_s), 1) AS avg_duration_s FROM biq.fact_sessions GROUP BY 1""",
}
assert set(CH_VIEWS) == set(VIEWS), "ClickHouse views must match the whitelisted view names in warehouse.py"


# ---------------------------------------------------------------- connection wrapper
class _Result:
    def __init__(self, client, sql: str, df: pd.DataFrame | None = None):
        self._client = client
        self._sql = sql
        self._df = df

    def df(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        return self._client.query_df(self._sql)

    def fetchone(self):
        if self._df is not None:
            return tuple(self._df.iloc[0]) if len(self._df) else None
        rows = self._client.query(self._sql).result_rows
        return tuple(rows[0]) if rows else None


class _Connection:
    def __init__(self, client):
        self.client = client

    def execute(self, sql: str) -> _Result:
        stripped = sql.strip()
        if stripped.upper().startswith("DESCRIBE "):
            table = stripped.split(None, 1)[1].strip().rstrip(";")
            raw = self.client.query_df(f"DESCRIBE TABLE {table}")
            df = raw[["name", "type"]].rename(columns={"name": "column_name", "type": "column_type"})
            return _Result(self.client, sql, df=df)
        return _Result(self.client, sql)

    def register(self, name, df):  # pragma: no cover - build() here doesn't use DuckDB-style register
        raise NotImplementedError("ClickHouse backend loads data via insert_df, not register()")

    def close(self):
        self.client.close()


def connect(read_only: bool = False) -> _Connection:
    import clickhouse_connect

    user = config.CLICKHOUSE_RO_USER if read_only else config.CLICKHOUSE_USER
    password = config.CLICKHOUSE_RO_PASSWORD if read_only else config.CLICKHOUSE_PASSWORD
    client = clickhouse_connect.get_client(
        host=config.CLICKHOUSE_HOST, port=config.CLICKHOUSE_PORT,
        username=user, password=password, database=config.CLICKHOUSE_DB,
    )
    return _Connection(client)


# ---------------------------------------------------------------- Postgres dims
def _pg_conn():
    import psycopg2
    return psycopg2.connect(
        host=config.POSTGRES_HOST, port=config.POSTGRES_PORT, dbname=config.POSTGRES_DB,
        user=config.POSTGRES_USER, password=config.POSTGRES_PASSWORD,
    )


def _load_postgres_dims(dim_product_df: pd.DataFrame, dim_time_df: pd.DataFrame):
    from psycopg2.extras import execute_values

    pg = _pg_conn()
    try:
        cur = pg.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS dim_product (product_id TEXT PRIMARY KEY, category TEXT, "
                    "price DOUBLE PRECISION, rating DOUBLE PRECISION, review_count BIGINT)")
        cur.execute("CREATE TABLE IF NOT EXISTS dim_device (device TEXT PRIMARY KEY)")
        cur.execute("CREATE TABLE IF NOT EXISTS dim_event (event_type TEXT PRIMARY KEY, category TEXT)")
        cur.execute("CREATE TABLE IF NOT EXISTS dim_time (day DATE PRIMARY KEY, dow SMALLINT)")

        cur.execute("TRUNCATE dim_product")
        execute_values(cur, "INSERT INTO dim_product (product_id, category, price, rating, review_count) VALUES %s",
                       dim_product_df[["product_id", "category", "price", "rating", "review_count"]].values.tolist())

        cur.execute("TRUNCATE dim_device")
        execute_values(cur, "INSERT INTO dim_device (device) VALUES %s", DIM_DEVICE_ROWS)

        cur.execute("TRUNCATE dim_event")
        execute_values(cur, "INSERT INTO dim_event (event_type, category) VALUES %s", DIM_EVENT_ROWS)

        cur.execute("TRUNCATE dim_time")
        execute_values(cur, "INSERT INTO dim_time (day, dow) VALUES %s", dim_time_df[["day", "dow"]].values.tolist())
        pg.commit()
    finally:
        pg.close()


# ---------------------------------------------------------------- ClickHouse DDL
def _ensure_schema(client):
    pg_host, pg_db, pg_user, pg_pw = (config.POSTGRES_HOST_IN_DOCKER, config.POSTGRES_DB,
                                      config.POSTGRES_USER, config.POSTGRES_PASSWORD)
    client.command(f"CREATE DATABASE IF NOT EXISTS {config.CLICKHOUSE_DB}")

    client.command("""
        CREATE TABLE IF NOT EXISTS biq.fact_user_events (
            event_id String, session_id String, anon_user_id String, event_type String,
            timestamp DateTime64(3, 'UTC'), page String, product_id Nullable(String),
            category Nullable(String), device String, referrer String,
            scroll_depth Nullable(Float64), dwell_ms Nullable(Int64), seq Nullable(Int64),
            ingested_at DateTime64(3) DEFAULT now64()
        ) ENGINE = ReplacingMergeTree(ingested_at)
        PARTITION BY toDate(timestamp) ORDER BY (event_id)""")

    client.command("""
        CREATE TABLE IF NOT EXISTS biq.fact_sessions (
            session_id String, start_ts DateTime64(3, 'UTC'), close_ts DateTime64(3, 'UTC'),
            device String, referrer String, n_events Int64, outcome String, close_reason String,
            out_of_order UInt8, duration_s Float64, purchased UInt8, reached_product UInt8,
            reached_cart UInt8, reached_checkout UInt8, n_compare Int64, segment Nullable(String)
        ) ENGINE = MergeTree PARTITION BY toDate(start_ts) ORDER BY (session_id)""")

    client.command("""
        CREATE TABLE IF NOT EXISTS biq.fact_conversions (
            session_id String, ts DateTime64(3, 'UTC'), device String, referrer String
        ) ENGINE = MergeTree ORDER BY (session_id)""")

    client.command("""
        CREATE TABLE IF NOT EXISTS biq.fact_bandit_decisions (
            ts DateTime64(3, 'UTC'), session_id String, context String, action String, reward Float64
        ) ENGINE = MergeTree ORDER BY (ts)""")

    for table, cols in [("dim_product", "product_id String, category String, price Float64, "
                                         "rating Float64, review_count Int64"),
                        ("dim_device", "device String"),
                        ("dim_event", "event_type String, category String"),
                        ("dim_time", "day Date, dow Int8")]:
        client.command(f"CREATE TABLE IF NOT EXISTS biq.{table} ({cols}) "
                        f"ENGINE = PostgreSQL('{pg_host}:5432', '{pg_db}', '{table}', '{pg_user}', '{pg_pw}')")

    client.command(f"""
        CREATE TABLE IF NOT EXISTS biq.kafka_user_events (
            event_id String, session_id String, anon_user_id String, event_type String,
            timestamp DateTime64(3, 'UTC'), page String, product_id Nullable(String),
            category Nullable(String), device String, referrer String,
            meta Tuple(scroll_depth Float64, dwell_ms Int64, seq Int64)
        ) ENGINE = Kafka
        SETTINGS kafka_broker_list = '{config.KAFKA_BOOTSTRAP_IN_DOCKER}',
                 kafka_topic_list = 'user-events',
                 kafka_group_name = 'biq_clickhouse',
                 kafka_format = 'JSONEachRow',
                 kafka_num_consumers = 1,
                 kafka_skip_broken_messages = 100,
                 input_format_skip_unknown_fields = 1,
                 input_format_json_named_tuples_as_objects = 1,
                 date_time_input_format = 'best_effort'""")

    client.command("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS biq.mv_kafka_to_fact_user_events TO biq.fact_user_events AS
        SELECT event_id, session_id, anon_user_id, event_type, timestamp, page, product_id, category, device, referrer,
               meta.scroll_depth AS scroll_depth, meta.dwell_ms AS dwell_ms, meta.seq AS seq, now64() AS ingested_at
        FROM biq.kafka_user_events""")

    for name, sql in CH_VIEWS.items():
        client.command(f"CREATE OR REPLACE VIEW biq.{name} AS {sql}")

    client.command(f"CREATE USER IF NOT EXISTS {config.CLICKHOUSE_RO_USER} "
                    f"IDENTIFIED WITH plaintext_password BY '{config.CLICKHOUSE_RO_PASSWORD}'")
    client.command(f"GRANT SELECT ON biq.* TO {config.CLICKHOUSE_RO_USER}")


# ---------------------------------------------------------------- build / ETL
def build(segments: pd.DataFrame | None = None):
    import clickhouse_connect

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

    events_dedup = events.drop_duplicates(subset="event_id", keep="last")

    dim_time_df = pd.DataFrame({
        "day": fact_sessions_df["start_ts"].dt.date,
        "dow": fact_sessions_df["start_ts"].dt.day_of_week,
    }).drop_duplicates()

    _load_postgres_dims(catalogue, dim_time_df)

    admin = clickhouse_connect.get_client(
        host=config.CLICKHOUSE_HOST, port=config.CLICKHOUSE_PORT,
        username=config.CLICKHOUSE_USER, password=config.CLICKHOUSE_PASSWORD, database="default",
    )
    try:
        _ensure_schema(admin)
        biq = clickhouse_connect.get_client(
            host=config.CLICKHOUSE_HOST, port=config.CLICKHOUSE_PORT,
            username=config.CLICKHOUSE_USER, password=config.CLICKHOUSE_PASSWORD, database=config.CLICKHOUSE_DB,
        )
        try:
            # fact_user_events is append-only: truncating would destroy rows the Kafka
            # materialized view inserted between build() runs. ReplacingMergeTree(ingested_at)
            # dedups duplicate event_ids (reruns, or Kafka at-least-once) at query time via FINAL.
            biq.insert_df("fact_user_events", events_dedup[[
                "event_id", "session_id", "anon_user_id", "event_type", "timestamp", "page",
                "product_id", "category", "device", "referrer", "scroll_depth", "dwell_ms", "seq",
            ]])
            biq.command("TRUNCATE TABLE biq.fact_sessions")
            biq.insert_df("fact_sessions", fact_sessions_df[[
                "session_id", "start_ts", "close_ts", "device", "referrer", "n_events", "outcome",
                "close_reason", "out_of_order", "duration_s", "purchased", "reached_product",
                "reached_cart", "reached_checkout", "n_compare", "segment",
            ]])
            biq.command("TRUNCATE TABLE biq.fact_conversions")
            biq.command("INSERT INTO biq.fact_conversions SELECT session_id, close_ts AS ts, device, referrer "
                        "FROM biq.fact_sessions WHERE outcome = 'purchase'")
            n_e = biq.query("SELECT COUNT(*) FROM biq.fact_user_events FINAL").result_rows[0][0]
            n_s = biq.query("SELECT COUNT(*) FROM biq.fact_sessions").result_rows[0][0]
        finally:
            biq.close()
    finally:
        admin.close()
    print(f"warehouse built (clickhouse): {n_e:,} distinct events, {n_s:,} sessions, "
          f"{len(CH_VIEWS)} views -> {config.CLICKHOUSE_HOST}:{config.CLICKHOUSE_PORT}/{config.CLICKHOUSE_DB}")


# ---------------------------------------------------------------- spot check
def spot_check(n: int = 10, seed: int = 0) -> int:
    """Rebuild n random sessions from raw events and compare against fact tables (Stage 4 DoD)."""
    from .schema import label_session

    raw = pd.read_parquet(config.RAW_EVENTS)
    con = connect(read_only=True)
    sample_df = con.execute(f"SELECT session_id, n_events, outcome FROM fact_sessions "
                             f"ORDER BY rand() LIMIT {n}").df()  # not a true reservoir sample; fine for a spot check
    con.close()
    ok = 0
    for r in sample_df.itertuples():
        ev = raw[raw.session_id == r.session_id].sort_values("seq").event_type.tolist()
        ok += int(len(ev) == r.n_events and label_session(ev) == r.outcome)
    print(f"spot check (clickhouse): {ok}/{n} sessions match raw logs")
    return ok


# ---------------------------------------------------------------- latency measurement
def measure_latency(n: int = 30, timeout_s: float = 10.0) -> float:
    """Publish n probe events to Kafka and measure p95 time until they're queryable in ClickHouse.

    Requires KAFKA_BOOTSTRAP set (host-side) so EventBus actually publishes to Redpanda, and the
    "warehouse" compose profile's ClickHouse Kafka-engine table + materialized view already
    created by build(). Also reports fact_user_events row-count delta and any system.kafka_consumers
    parse errors, since a clean p95 alone doesn't prove events actually landed.
    """
    from . import bus
    from .schema import Event, EventMeta

    if not config.KAFKA_BOOTSTRAP:
        raise RuntimeError("measure_latency requires KAFKA_BOOTSTRAP set (e.g. localhost:19092)")

    con = connect(read_only=False)
    count_before = con.execute("SELECT COUNT(*) FROM fact_user_events").fetchone()[0]

    eb = bus.EventBus()
    published = {}
    for i in range(n):
        eid = str(uuid.uuid4())
        ev = Event(event_id=eid, session_id=f"latency-probe-{uuid.uuid4()}", anon_user_id="latency-probe",
                   event_type="page_view", timestamp=datetime.now(timezone.utc), page="/probe",
                   device="desktop", referrer="direct", meta=EventMeta(scroll_depth=0.0, dwell_ms=0, seq=0))
        t0 = time.monotonic()
        eb.publish(ev.model_dump(mode="json"))
        published[eid] = t0
    eb.flush()

    latencies = {}
    deadline = time.monotonic() + timeout_s * max(1, n // 5)
    remaining = set(published)
    while remaining and time.monotonic() < deadline:
        ids = ",".join(f"'{e}'" for e in remaining)
        rows = con.execute(f"SELECT event_id FROM fact_user_events FINAL WHERE event_id IN ({ids})").df()
        now = time.monotonic()
        for eid in rows["event_id"].tolist() if len(rows) else []:
            if eid in remaining:
                latencies[eid] = now - published[eid]
                remaining.discard(eid)
        if remaining:
            time.sleep(0.2)

    count_after = con.execute("SELECT COUNT(*) FROM fact_user_events").fetchone()[0]
    try:
        kafka_errors = con.execute(
            "SELECT table, last_exception FROM system.kafka_consumers "
            "WHERE database = 'biq' AND last_exception != ''"
        ).df()
    except Exception:
        kafka_errors = None
    con.close()

    if not latencies:
        print(f"measure_latency: 0/{n} probes observed within {timeout_s * max(1, n // 5):.0f}s; "
              f"fact_user_events count {count_before:,} -> {count_after:,}")
        if kafka_errors is not None and len(kafka_errors):
            print("system.kafka_consumers errors:\n", kafka_errors.to_string(index=False))
        return float("nan")

    vals = sorted(latencies.values())
    p95 = vals[max(0, int(len(vals) * 0.95) - 1)]
    print(f"measure_latency: {len(latencies)}/{n} probes observed; fact_user_events count "
          f"{count_before:,} -> {count_after:,} (+{count_after - count_before:,}); "
          f"p95 end-to-end latency = {p95:.3f}s (target < 2s)")
    if kafka_errors is not None and len(kafka_errors):
        print("system.kafka_consumers errors:\n", kafka_errors.to_string(index=False))
    return p95


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--latency", nargs="?", const=30, type=int, metavar="N",
                     help="publish N probe events and measure p95 event-to-warehouse latency")
    a = ap.parse_args()
    if a.latency:
        measure_latency(a.latency)
    else:
        build()
        spot_check()
