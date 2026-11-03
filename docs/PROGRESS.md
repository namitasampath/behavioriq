# Production upgrade progress

## Upgrade 3 — Prometheus + Grafana observability — done (2026-11-03)

The `monitoring` compose profile (Prometheus + Grafana) is purely additive: with it not started, the
apps behave exactly as before except that `/metrics` is now real Prometheus text format instead of
hand-written strings — no env var gates this, since the metrics format change has no effect on
`run_all.py` or `pytest` either way (neither touches `ingest_api`/`serve_api`), and `/metrics` is
dead code until something scrapes it.

- `biq/ingest_api.py` and `biq/serve_api.py` now use `prometheus-client` instead of hand-built text:
  a `prometheus_client.Counter`/`Histogram`-based middleware records `..._requests_total{endpoint,
  method, status}` and `..._request_latency_seconds{endpoint}` for every route (endpoint label is the
  route *template*, e.g. `/predict/{session_id}`, not the literal path — using the literal path would
  blow up cardinality one label-series per session). Domain counters: `biq_events_accepted_total`,
  `biq_events_rejected_total` (ingest); `biq_predictions_total{predicted_class}`,
  `biq_bandit_actions_total{action}`, `biq_copilot_questions_total`,
  `biq_copilot_blocked_queries_total` (serve — the last one counts copilot tool calls where
  `run_sql()`'s guardrail returned `blocked: True`).
- `infra/docker-compose.yml`: added `prometheus` (`prom/prometheus:v2.54.1`) and `grafana`
  (`grafana/grafana:11.2.0`) under a new `monitoring` profile, pinned, healthchecked, memory-limited.
  Prometheus scrapes `host.docker.internal:8000`/`:8001` (the FastAPI apps run on the Windows host,
  not in Docker) plus `redpanda:9644/public_metrics` and `clickhouse:9363/metrics` over the compose
  network — no port needed to be published on redpanda/console/qdrant (untouched, per the rules) since
  container-to-container traffic doesn't need the host port mapping.
- ClickHouse's built-in Prometheus exporter is enabled via a config.d override
  (`infra/clickhouse/prometheus.xml`, mounted read-only) exposing `:9363/metrics`; this only adds a
  listener, so `WAREHOUSE=clickhouse` behaviour is unchanged.
- `infra/grafana/provisioning/{datasources,dashboards}` + `infra/grafana/dashboards/behavioriq.json`
  provision a pinned-uid Prometheus datasource and the "BehaviorIQ" dashboard on startup, no clicking
  required. Anonymous viewer access is on (`GF_AUTH_ANONYMOUS_ENABLED`); admin credentials come from
  `.env` (`.env.example` added at repo root, `GF_SECURITY_ADMIN_USER`/`GF_SECURITY_ADMIN_PASSWORD`).
  Panels: events/sec (accepted vs rejected), rejected events total, ingest `/track` p95 latency,
  Redpanda consumer lag (`redpanda_kafka_max_offset` joined against
  `redpanda_kafka_consumer_group_committed_offset` on topic+partition — Redpanda's metrics don't
  expose lag directly, there's no single `..._lag` series), predictions by class, bandit action mix,
  copilot questions (+ blocked queries).

### Verification run (2026-11-03)

```
docker compose -f infra/docker-compose.yml --profile warehouse --profile monitoring up -d --wait
# -> qdrant, postgres, redpanda, console, clickhouse, prometheus, grafana all Healthy

uv pip install prometheus-client

$env:KAFKA_BOOTSTRAP = "localhost:19092"; $env:QDRANT_URL = "http://localhost:6333"
uvicorn biq.ingest_api:app --port 8000 &
uvicorn biq.serve_api:app --port 8001 &

python -m biq.simulator --sessions 600 --http http://127.0.0.1:8000/track --rate 50 --realtime
# -> ~2 minutes of live traffic; ingest_api /metrics: biq_events_accepted_total 7709,
#    biq_events_rejected_total 0, biq_ingest_requests_total{endpoint="/track",...} 771

# hit /predict/{id}, /decide/{id} x6, POST /predict, POST /ask x2
curl http://127.0.0.1:8001/predict/s_005773 ... (purchase/abandon/browse mix)
curl http://127.0.0.1:8001/decide/s_005773  ... (compare_helper/social_proof/discount_nudge mix)
curl -X POST http://127.0.0.1:8001/ask -d '{"question":"Which device has the highest conversion rate?"}'
# -> serve_api /metrics: biq_predictions_total{predicted_class="purchase"} 1, {"abandon"} 4,
#    {"browse"} 2; biq_bandit_actions_total{action="compare_helper"} 5, {"social_proof"} 1,
#    {"discount_nudge"} 1; biq_copilot_questions_total 2
# -> endpoint labels confirmed as route templates: biq_serve_requests_total{endpoint="/predict/{session_id}",...} 6
#    (not one series per session_id)

# blocked-query counter verified directly (no ANTHROPIC_API_KEY in this run, so the rule-based
# offline copilot router never emits a blocked tool call organically): TestClient + a monkeypatched
# copilot.ask() returning {"blocked": True} in tool_outputs -> biq_copilot_blocked_queries_total 1.0

# Prometheus API proof (http://localhost:9090):
curl 'http://localhost:9090/api/v1/targets'
# -> ingest_api, serve_api, redpanda, clickhouse all health "up"
curl --data-urlencode 'query=biq_events_accepted_total' http://localhost:9090/api/v1/query
# -> 7709
curl --data-urlencode 'query=histogram_quantile(0.95, sum(rate(biq_ingest_request_latency_seconds_bucket{endpoint="/track"}[5m])) by (le))' ...
# -> 0.0282s p95
curl --data-urlencode 'query=sum by (redpanda_group, redpanda_topic) (redpanda_kafka_max_offset - on(redpanda_topic, redpanda_partition) group_right redpanda_kafka_consumer_group_committed_offset)' ...
# -> biq_clickhouse lag 0 (ClickHouse's Kafka-engine MV keeps draining it); biq-stream lag 13079
#    (no stream_processor was running in this test, so that consumer group's lag just grows - expected)
curl --data-urlencode 'query=ClickHouseMetrics_TCPConnection' ...
# -> ClickHouse's own Prometheus endpoint confirmed live through Prometheus

# Grafana proof (http://localhost:3000, anonymous viewer):
curl http://localhost:3000/api/health            # -> {"database":"ok","version":"11.2.0"}
curl http://localhost:3000/api/datasources       # -> Prometheus, uid "prometheus"
curl http://localhost:3000/api/search            # -> uid "behavioriq", title "BehaviorIQ"
curl 'http://localhost:3000/api/datasources/proxy/uid/prometheus/api/v1/query?query=biq_events_accepted_total'
# -> 7709, proving the dashboard's own datasource proxy resolves real data

pytest
# -> 10 passed, with KAFKA_BOOTSTRAP/QDRANT_URL/WAREHOUSE all unset (default path unaffected)
```

Grafana dashboard: http://localhost:3000/d/behavioriq/behavioriq (anonymous viewer access, no login
needed). Prometheus: http://localhost:9090.

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
