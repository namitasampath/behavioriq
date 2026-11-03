# Production upgrade progress

## Upgrade 1 — Redpanda (Kafka) + Qdrant server — done

- `KAFKA_BOOTSTRAP=localhost:19092` switches `biq/bus.py` from the local JSONL event log to a
  real Kafka producer/consumer against Redpanda.
- `QDRANT_URL=http://localhost:6333` switches `biq/retrieval.py` from embedded-mode Qdrant to a
  Qdrant server.
- Services added to `infra/docker-compose.yml`: `redpanda`, `console`, `qdrant`.

## Upgrade 2 — ClickHouse (facts) + PostgreSQL (dims) warehouse — done (2026-11-03)

`WAREHOUSE=clickhouse` switches `biq/warehouse.py`'s `connect()`/`build()`/`spot_check()` to
`biq/warehouse_clickhouse.py`. Facts live in ClickHouse; dimensions live in PostgreSQL and are
read from ClickHouse via the PostgreSQL table engine. `fact_user_events` is continuously fed by a
ClickHouse Kafka-engine table + materialized view reading the Redpanda topic `user-events` — no
extra Python process needed. The 8 whitelisted `v_*` views were ported to ClickHouse SQL with the
same names/columns, so `biq/copilot.py` and `dashboard/app.py` work unchanged. A read-only
ClickHouse user (`biq_ro`) is created for the copilot/dashboard; `build()`/ETL use an admin user.

Services added to `infra/docker-compose.yml` under the `warehouse` compose profile:
`clickhouse` (`clickhouse/clickhouse-server:24.8.7.41`), `postgres` (`postgres:16.4`). Existing
services (redpanda/console/qdrant) untouched.

### Design note: fact_user_events is append-only

Unlike DuckDB's `build()`, which does a full `CREATE OR REPLACE TABLE fact_user_events` on every
run, the ClickHouse version never truncates `fact_user_events` — truncating would destroy rows
the Kafka materialized view inserted between `build()` runs, defeating the point of continuous
ETL. Instead, `fact_user_events` uses `ReplacingMergeTree(ingested_at)` ordered by `event_id`, and
the two views that read it directly (`v_product_performance`, `v_category_performance`) query
`FROM biq.fact_user_events FINAL` to dedup at query time. `fact_sessions`/`fact_conversions` are
truncated and reloaded each run (nothing else writes to them, so it's safe).

### Fixed: copilot eval SQL that only DuckDB tolerated

Two of `biq/copilot.py`'s hand-written offline-mode routes ("overall conversion rate" and
"conversion rate on mobile/desktop") built SQL that reused an aggregate's output alias inside
another aggregate in the same `SELECT` list (e.g. `SUM(purchased) AS purchased` then
`AVG(purchased)`). DuckDB silently tolerates this; ClickHouse's `ILLEGAL_AGGREGATION` check
rejects it. A ClickHouse client setting (`prefer_column_name_to_alias`) was tried first but only
traded this failure for a different one (`ORDER BY` then bound to the source view's same-named
column instead of the query's own output alias, since the two failing query shapes need opposite
alias-resolution behavior — no single global ClickHouse setting satisfies both). The actual fix:
rewrite the two offending queries in `biq/copilot.py` as `SELECT ... FROM (SELECT ... GROUP BY
...)` — an inner aggregation with non-colliding inner aliases, then an outer `SELECT` that renames
to the original output columns (`session_count`, `conversion_rate`) so the answer text, grounding
check, and k-anonymity filter are unaffected. This is portable SQL that both DuckDB and ClickHouse
execute identically. `copilot --eval` now scores 16/16 (100%) on both backends.

### Verification run (2026-11-03)

```
docker compose -f infra/docker-compose.yml --profile warehouse up -d --wait
# -> postgres Healthy, clickhouse Healthy (redpanda/console/qdrant already running, untouched)

uv pip install clickhouse-connect psycopg2-binary

$env:WAREHOUSE = "clickhouse"
python run_all.py --only warehouse
# -> warehouse built (clickhouse): 74,543 distinct events, 6,000 sessions, 8 views -> localhost:8123/biq
# -> spot check (clickhouse): 10/10 sessions match raw logs

$env:KAFKA_BOOTSTRAP = "localhost:19092"
python -m biq.warehouse_clickhouse --latency 30
# -> measure_latency: 30/30 probes observed; fact_user_events count 76,919 -> 76,949 (+30);
#    p95 end-to-end latency = 1.687s (target < 2s)
# -> system.kafka_consumers: 0 exceptions

python -m biq.copilot --eval        # (QDRANT_URL=http://localhost:6333 to avoid the dashboard's
                                     #  embedded-Qdrant file lock, a pre-existing, unrelated
                                     #  local-concurrency limitation also present in DuckDB mode)
# -> Correct: 16/16 (100%); unfounded numbers: 0; guardrails blocked 4/4

pytest
# -> 10 passed

Remove-Item Env:\WAREHOUSE
python run_all.py --only warehouse
# -> warehouse built: 74,513 events, 6,000 sessions, 8 views -> ...\data\warehouse.duckdb (unchanged)
python -m biq.copilot --eval
# -> Correct: 16/16 (100%) on DuckDB too (same SQL rewrite, same result)
pytest
# -> 10 passed (DuckDB default path unaffected)
```
