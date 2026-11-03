# BehaviorIQ

A real-time, LLM-augmented behavioural intelligence platform: clickstream events are sessionised
as they arrive, each session becomes a learned behavioural embedding, similar past sessions are
retrieved from a vector database, intent is predicted with a retrieval-augmented classifier, a
contextual bandit picks the next-best action, and an LLM copilot answers plain-English questions
grounded in the warehouse.

This repository is the **laptop edition**: every one of the 13 stages runs in pure Python with no
Docker, so the full pipeline reproduces on Windows in about 15 minutes. The "Production upgrade"
section maps each component to its server-grade counterpart from the project report.

## Quick start (Windows, PowerShell)

```powershell
cd C:\Users\svi\Downloads\dev\behavioriq
uv venv --python 3.11
.venv\Scripts\activate
uv pip install -r requirements.txt
python run_all.py
streamlit run dashboard/app.py
```

`run_all.py` writes all results to `reports/`. The dashboard opens at http://localhost:8501.

Optional, for open-ended copilot questions:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

Without a key the copilot runs in a labelled offline mode, using the same tools and guardrails.

## What each stage is and where it lives

| Stage | What it does | Code | Output |
| --- | --- | --- | --- |
| 0 Contracts | Event schema, 13-event vocabulary, session and label rules | `biq/schema.py`, `docs/CONTRACTS.md` | |
| 1 Data | Persona-driven Markov simulator with overlap and 10% noise | `biq/simulator.py` | `data/raw_events.parquet` |
| 2 Ingestion | FastAPI `/track`, schema validation, dead-letter queue, event bus | `biq/ingest_api.py`, `biq/bus.py` | |
| 3 Streaming + features | Keyed session state, watermark, 30-min timers; online (SQLite) and offline (Parquet) feature store | `biq/stream_processor.py`, `biq/feature_store.py` | |
| 4 Warehouse | Star schema, 8 whitelisted views, hand spot-check | `biq/warehouse.py` | `data/warehouse.duckdb` |
| 5 Mining | PrefixSpan, FP-Growth, HDBSCAN vs K-Means | `biq/mining.py` | `reports/stage5_mining.md` |
| 6 Encoder | Transformer, InfoNCE contrastive pretraining, text baseline | `biq/encoder.py` | `reports/stage6_encoder.md` |
| 7 Retrieval | Qdrant collections, no-self / no-future filter, P@K, MRR, NDCG, latency | `biq/retrieval.py` | `reports/stage7_retrieval.md` |
| 8 Intent + bandit | A / B / C_base / C / C+R / C-ft, early-prediction curve, bootstrap; Thompson Sampling vs LinUCB vs rules | `biq/intent.py`, `biq/bandit.py` | `reports/stage8/` |
| 9 Copilot | Claude tool use, sqlglot guard, k-anonymity floor, grounding check, eval set | `biq/copilot.py` | `reports/stage9_copilot.md` |
| 10 MLOps | MLflow runs + registry (`@champion`), PSI/KS drift with 5 injections, SHAP | `biq/mlops.py`, `biq/serving.py` | `reports/stage10_drift.md` |
| 11 Dashboard | Overview, session journey with live re-prediction, segments, research, copilot, monitoring | `dashboard/app.py` | |
| 12 Packaging | One-command pipeline, tests, CI | `run_all.py`, `tests/`, `.github/workflows/ci.yml` | |

## Live demo (three terminals)

```powershell
# 1  ingestion API
uvicorn biq.ingest_api:app --port 8000
# 2  stream processor tailing the event bus
python -m biq.stream_processor --live
# 3  send live traffic
python -m biq.simulator --sessions 200 --http http://127.0.0.1:8000/track --rate 50 --realtime
```

Serving API (intent, next-best action, SHAP, copilot):
`uvicorn biq.serve_api:app --port 8001` then open http://127.0.0.1:8001/docs

MLflow UI: `mlflow ui --backend-store-uri sqlite:///mlflow.db`

## Running on real data (REES46)

```powershell
python run_all.py --dataset rees46 --sessions 30000
```

Replays the REES46 "eCommerce behavior data from multi category store" CSV
(`data/external/*.csv`) through `biq/replay.py` instead of the simulator - same stages (minus
`bandit`, which needs counterfactual outcomes a logged dataset doesn't have, and `copilot`, not
run for this dataset), writing to `data/rees46/`, `reports/rees46/`, `artifacts/rees46/` without
touching the simulated outputs. See `docs/CONTRACTS.md` ("Real-data replay (REES46)") for what the
source data can't give us (missing event types, no device/referrer, no hidden personas), and
`docs/RESULTS_FOR_REPORT.md` ("Upgrade 5") for the full comparison against the simulated run.

## Production upgrade (what the report's architecture swaps in)

| Laptop edition | Production component | How to switch |
| --- | --- | --- |
| JSONL event bus | Kafka / Redpanda | set `KAFKA_BOOTSTRAP=localhost:19092`, `pip install kafka-python`; `bus.py` already uses it |
| Python session processor | Apache Flink (PyFlink) | port `SessionState.update` into a `KeyedProcessFunction`; the logic is identical |
| SQLite + Parquet feature store | Feast with Redis online store | `FEATURE_COLUMNS` becomes a Feast FeatureView |
| DuckDB | ClickHouse (facts) + PostgreSQL (dims) | `pip install clickhouse-connect psycopg2-binary`; `docker compose -f infra/docker-compose.yml --profile warehouse up -d --wait`; set `WAREHOUSE=clickhouse`; `warehouse.py` already uses it |
| Qdrant local mode | Qdrant server | set `QDRANT_URL=http://localhost:6333` |
| PSI/KS drift | Evidently AI | same statistics; Evidently adds HTML reports |
| `/metrics` endpoints | Prometheus + Grafana | `biq/ingest_api.py` and `biq/serve_api.py` already serve `/metrics` via `prometheus-client`; `docker compose -f infra/docker-compose.yml --profile monitoring up -d --wait`; Prometheus at http://localhost:9090, Grafana (anonymous viewer, "BehaviorIQ" dashboard provisioned) at http://localhost:3000 |

## Honest limitations

* Most data is synthetic. The simulator's personas overlap and it adds noise. Confirmed against
  real data: `python run_all.py --dataset rees46` (see above) replays the REES46 Kaggle dataset -
  the representation/retrieval ranking holds (count-based features beat the learned contrastive
  embedding on both), but REES46's outcome mix is far more skewed (89.5% browse-only vs. the
  simulator's balanced 3-way split) and it has no device/referrer/review/compare/search events at
  all, so several conclusions (device segmentation, the early-prediction curve, anything touching
  consideration events) don't transfer - see `docs/RESULTS_FOR_REPORT.md`.
* The bandit can only be evaluated in simulation, because logged data does not show the outcomes of actions that were not taken.
* Heavy infrastructure (Kafka, Flink, Feast, ClickHouse) is replaced by in-process equivalents, as listed above.
* In `WAREHOUSE=clickhouse` mode, `fact_user_events` is append-only across warehouse rebuilds (never truncated, unlike DuckDB's full `CREATE OR REPLACE TABLE`) because a ClickHouse Kafka-engine materialized view keeps inserting into it continuously; `ReplacingMergeTree(ingested_at)` + `FINAL` dedup duplicate `event_id`s at query time instead. Raw (pre-merge) row count grows with every rebuild as a result.
