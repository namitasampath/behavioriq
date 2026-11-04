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

Or, free and fully local, via [Ollama](https://ollama.com) (no API key, no cloud calls):

```powershell
ollama pull qwen2.5:3b          # one-time, ~2GB
$env:BIQ_LLM_PROVIDER = "ollama"   # BIQ_LLM_MODEL defaults to qwen2.5:3b, BIQ_OLLAMA_URL to http://localhost:11434/v1
```

## What each stage is and where it lives

| Stage | What it does | Code | Output |
| --- | --- | --- | --- |
| 0 Contracts | Event schema, 13-event vocabulary, session and label rules | `biq/schema.py`, `docs/CONTRACTS.md` | |
| 1 Data | Persona-driven Markov simulator with overlap and 10% noise; or real browser traffic from the storefront + tracking SDK | `biq/simulator.py`; `storefront/`, `tracker-sdk/` | `data/raw_events.parquet` |
| 2 Ingestion | FastAPI `/track`, schema validation, dead-letter queue, event bus | `biq/ingest_api.py`, `biq/bus.py` | |
| 3 Streaming + features | Keyed session state, watermark, 30-min timers; online (SQLite, or Redis via `FEATURE_STORE=redis`) and offline (Parquet) feature store | `biq/stream_processor.py`, `biq/feature_store.py` | |
| 4 Warehouse | Star schema, 8 whitelisted views, hand spot-check | `biq/warehouse.py` | `data/warehouse.duckdb` |
| 5 Mining | PrefixSpan, FP-Growth, HDBSCAN vs K-Means | `biq/mining.py` | `reports/stage5_mining.md` |
| 6 Encoder | Transformer, InfoNCE contrastive pretraining, text baseline | `biq/encoder.py` | `reports/stage6_encoder.md` |
| 7 Retrieval | Qdrant collections, no-self / no-future filter, P@K, MRR, NDCG, latency | `biq/retrieval.py` | `reports/stage7_retrieval.md` |
| 8 Intent + bandit | A / B / C_base / C / C+R / C-ft, early-prediction curve, bootstrap; Thompson Sampling vs LinUCB vs rules | `biq/intent.py`, `biq/bandit.py` | `reports/stage8/` |
| 9 Copilot | Claude tool use (or a free local LLM via Ollama), sqlglot guard, k-anonymity floor, grounding check, eval set | `biq/copilot.py` | `reports/stage9_copilot.md` (`_ollama.md` variant) |
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

Add `$env:FEATURE_STORE = "redis"` (with the `features` compose profile up) before step 2 to write
the online store to Redis instead of SQLite - `GET /features/{session_id}` on the serving API is
unchanged either way.

MLflow UI: `mlflow ui --backend-store-uri sqlite:///mlflow.db`

## Storefront + tracking SDK (real browser traffic, Stage 1)

A small Next.js storefront (`storefront/`) and a TypeScript tracking SDK (`tracker-sdk/`) generate
*real* clickstream events from an actual browser session instead of the simulator - same event
vocabulary, same canonical schema (`biq/schema.py`), same ingestion pipeline.

```powershell
# 1  ingestion API (CORS-open to the storefront's origin only)
uvicorn biq.ingest_api:app --port 8000
# 2  stream processor tailing the event bus
$env:KAFKA_BOOTSTRAP = "localhost:19092"; python -m biq.stream_processor --live
# 3  storefront, on port 3001 (3000 is Grafana)
cd storefront; npm install; npm run dev
```

Open http://localhost:3001, click through home -> search -> a product -> reviews/compare -> cart ->
checkout -> confirmation, and watch `biq_events_accepted_total` climb at
http://127.0.0.1:8000/metrics and sessions close in the stream processor's own output
(`session closed: <id> outcome=<purchase|abandon|browse> ...`).

`tracker-sdk/` is a standalone package (`cd tracker-sdk; npm install; npm test` - Vitest) exporting
`BehaviorTracker`, a browser-only class with `track(eventType, payload)`: generates `event_id`
(uuid v4), persists `anon_user_id`/`session_id` in `localStorage` (session rotates after 30 min
idle, matching `SESSION_TIMEOUT_S`), stamps `meta.seq`/`scroll_depth`/`dwell_ms`, batches and
flushes every 3s or 10 events, retries with backoff, and flushes immediately via
`navigator.sendBeacon` on page hide. `track()`'s payload is a closed, named field list - there is
no field for search query text anywhere in it, so a caller can never leak one into a tracked event
even by accident (`tracker-sdk/test/tracker.test.ts` asserts this directly).

The storefront imports `tracker-sdk/` as a local `file:` dependency (`behavior-tracker` in
`storefront/package.json`, resolved as an npm symlink - no duplicated `node_modules`) and reads
`data/catalogue.json` straight from the repo (`storefront/lib/catalogue.ts`, server-only) instead
of duplicating product data. Both dev servers run on webpack (`--webpack`) rather than Turbopack -
Turbopack's resolver can't currently find a `file:`-linked local package
(`Module not found: Can't resolve 'behavior-tracker'`), confirmed with a plain `next build`;
webpack resolves it fine via `transpilePackages`.

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
| SQLite + Parquet feature store | Redis online store (Feast blocked - see below) + Parquet offline store | `docker compose -f infra/docker-compose.yml --profile features up -d --wait`; `pip install redis`; set `FEATURE_STORE=redis`; `feature_store.py` already uses it |
| DuckDB | ClickHouse (facts) + PostgreSQL (dims) | `pip install clickhouse-connect psycopg2-binary`; `docker compose -f infra/docker-compose.yml --profile warehouse up -d --wait`; set `WAREHOUSE=clickhouse`; `warehouse.py` already uses it |
| Qdrant local mode | Qdrant server | set `QDRANT_URL=http://localhost:6333` |
| PSI/KS drift | Evidently AI | `uv venv --python 3.11 .venv-evidently; uv pip install -r requirements-evidently.txt --python .venv-evidently/Scripts/python.exe` (separate venv - Evidently 0.7.23 is incompatible with this project's pandas 3.1.0); set `BIQ_EVIDENTLY=1`; `biq/mlops.py` already shells out to it for an HTML report + dataset-drift verdict per scenario, alongside the existing PSI/domain-classifier result |
| `/metrics` endpoints | Prometheus + Grafana | `biq/ingest_api.py` and `biq/serve_api.py` already serve `/metrics` via `prometheus-client`; `docker compose -f infra/docker-compose.yml --profile monitoring up -d --wait`; Prometheus at http://localhost:9090, Grafana (anonymous viewer, "BehaviorIQ" dashboard provisioned) at http://localhost:3000 |
| Hosted Claude API copilot | Free, local LLM (Ollama) | `ollama pull qwen2.5:3b`; set `BIQ_LLM_PROVIDER=ollama`; `copilot.py`'s `ask_ollama()` already uses it through Ollama's OpenAI-compatible endpoint, with the same tools/guardrails/grounding check |

Feast (the originally planned online-store backend) cannot install into this venv without
downgrading pandas 3.1.0 -> 2.x and prometheus-client (Upgrade 3 depends on the latter) - see
`docs/PROGRESS.md` ("Upgrade 4") for the dry-run proof. `FEATURE_STORE=redis` talks to Redis
directly via `redis-py` behind the unchanged `FeatureStore` interface instead.

## Honest limitations

* Most data is synthetic. The simulator's personas overlap and it adds noise. Confirmed against
  real data: `python run_all.py --dataset rees46` (see above) replays the REES46 Kaggle dataset -
  the representation/retrieval ranking holds (count-based features beat the learned contrastive
  embedding on both), but REES46's outcome mix is far more skewed (89.5% browse-only vs. the
  simulator's balanced 3-way split) and it has no device/referrer/review/compare/search events at
  all, so several conclusions (device segmentation, the early-prediction curve, anything touching
  consideration events) don't transfer - see `docs/RESULTS_FOR_REPORT.md`.
* The bandit can only be evaluated in simulation, because logged data does not show the outcomes of actions that were not taken.
* The free local-LLM copilot (`BIQ_LLM_PROVIDER=ollama`, `qwen2.5:3b`) scores 2/16 (12%) on the Stage 9
  eval set with a bare system prompt, well below the offline router's 16/16 and the >=90% DoD target -
  it reliably calls the one tool that needs no SQL, but mostly picks the wrong view or writes wrong SQL
  against the 8-view schema. Inlining the `describe_schema()` output and one worked `run_sql` example
  into the system prompt (Ollama path only) raises this to 11/16 (69%) with no other change - still
  short of the target, but the dominant failure mode shifts from "wrong view" to smaller aggregation
  mistakes. The guardrails caught every unfounded/malicious case in both runs (0 unfounded numbers
  reached the user, 4/4 attacks blocked) - see `docs/RESULTS_FOR_REPORT.md` ("Upgrade 6") for the full
  breakdown. Treat it as a free, private, honestly-labelled demo mode, not a Claude replacement.
* Heavy infrastructure (Kafka, Flink, Feast, ClickHouse) is replaced by in-process equivalents, as listed above.
* In `WAREHOUSE=clickhouse` mode, `fact_user_events` is append-only across warehouse rebuilds (never truncated, unlike DuckDB's full `CREATE OR REPLACE TABLE`) because a ClickHouse Kafka-engine materialized view keeps inserting into it continuously; `ReplacingMergeTree(ingested_at)` + `FINAL` dedup duplicate `event_id`s at query time instead. Raw (pre-merge) row count grows with every rebuild as a result.
