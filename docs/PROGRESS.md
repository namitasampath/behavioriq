# Production upgrade progress

## Upgrade 4 — Redis-backed feature store (Feast blocked) — done (2026-11-04)

`FEATURE_STORE=redis` switches `biq/feature_store.py::FeatureStore`'s online store from SQLite to
Redis. With it unset (default `"sqlite"`), `run_all.py` and `pytest` behave exactly as before. The
Parquet offline store (`get_historical_features()`) is identical either way, so there is no
training/serving skew regardless of backend.

### Dependency check first: Feast 0.67.0 vs. this venv's pandas 3.1.0 pin

The upgrade was planned as Feast with a Redis online store. Checked with a dry run **before**
touching anything:

```
uv pip install --dry-run feast --python .venv/Scripts/python.exe
# -> Resolved 65 packages; would install feast==0.67.0, but would also:
#    - pandas==3.1.0  ->  + pandas==2.3.3
#    - prometheus-client==0.26.0  ->  + prometheus-client==0.24.1
```

Feast needs `pandas<3`; this project pins `pandas 3.1.0` (unpinned in `requirements.txt`, but the
installed resolver version), and downgrading it is explicitly against the session rule (never
downgrade pandas in the shared venv — see the Evidently blocker in Upgrade 7 for why: there is no
separate Feast release that fixes this). Feast's resolution would *also* downgrade
`prometheus-client`, which Upgrade 3's `/metrics` instrumentation in `biq/ingest_api.py` and
`biq/serve_api.py` depends on — a second, independent reason not to install it into the main
venv. Unlike Evidently, Feast is not a side CLI script that can be shelled out to from an isolated
venv — it needs to be imported in-process by `biq/stream_processor.py` and `biq/serve_api.py` on
every write/read, so the "separate venv" pattern from Upgrade 7 does not fit here either.

Per the session's own fallback instruction, Feast was **not installed**. Instead:

```
uv pip install --dry-run redis --python .venv/Scripts/python.exe
# -> Resolved 1 package; would install redis==8.1.0 - no other package touched
uv pip install redis --python .venv/Scripts/python.exe
# -> Installed redis==8.1.0 (confirmed pandas/prometheus-client untouched)
```

`redis-py` talks to Redis directly behind the **same `FeatureStore` interface**
(`write()`/`flush()`/`get_online_features()`), so no Feast feature-repo scaffolding
(`streaming/feature_repo/`) was built — there is nothing to point it at. `get_historical_features()`
is untouched.

- **`infra/docker-compose.yml`**: added `redis` (`redis:7.4-alpine`, pinned) under a new `features`
  compose profile — `--maxmemory 200mb --maxmemory-policy allkeys-lru`, `mem_limit: 256m`,
  healthcheck via `redis-cli ping`. Existing services (redpanda/console/qdrant/postgres/clickhouse/
  prometheus/grafana) untouched.
- **`biq/config.py`**: added `FEATURE_STORE_BACKEND` (`FEATURE_STORE`, default `"sqlite"`) and
  `REDIS_URL` (`BIQ_REDIS_URL`, default `redis://localhost:6379/0`).
- **`biq/feature_store.py`**: `FeatureStore.__init__` branches on `config.FEATURE_STORE_BACKEND`.
  SQLite path (`sqlite3`, `INSERT OR REPLACE`) is byte-for-byte what it was before. Redis path uses
  `redis.Redis.from_url(...)`, one `SET biq:online:<session_id> <json>` per `write()` (latest value
  wins, same semantics as `INSERT OR REPLACE`), `GET`+`json.loads` for `get_online_features()`, and
  `SCAN`+`DEL` over `biq:online:*` for `reset=True`. `flush()`'s offline-Parquet logic (the
  `self.buffer` -> `FEATURE_LOG` append) is identical for both backends — only the `self.db.commit()`
  call is skipped for Redis (nothing to commit). `biq/stream_processor.py` and
  `biq/serve_api.py`'s `/features/{session_id}` route needed **no changes** (the point of keeping
  the interface stable).
- **`requirements.txt`**: added `redis`.
- **`tests/feature_parity.py`** (new, not pytest-collected — no `test_`/`_test` in the name, since
  it needs a store that's already been populated by a replay/live run first): for 50 random
  sessions, compares the online store's latest snapshot against `get_historical_features()`
  evaluated at that exact same timestamp, column by column over all 15 `FEATURE_COLUMNS`.

### Verification run (2026-11-04)

```
docker compose -f infra/docker-compose.yml --profile features up -d --wait
# -> redis container Healthy (redis-cli ping -> PONG); maxmemory 200MB; other services untouched

python -m pytest -q          # FEATURE_STORE unset
# -> 10 passed (default SQLite path unaffected)

$env:FEATURE_STORE = "redis"
python -m biq.stream_processor --replay
# -> processed 74,513 events -> 6,000 closed sessions, 0 out-of-order
docker exec redis redis-cli dbsize
# -> 6000  (one key per session, biq:online:<session_id>)
python tests/feature_parity.py --n 50 --seed 0
# -> backend: redis; 50 sessions x 15 columns = 750 values compared; match: 750/750 (100.00%)

Remove-Item Env:\FEATURE_STORE
python -m biq.stream_processor --replay      # rebuild the default SQLite store
python tests/feature_parity.py --n 50 --seed 0
# -> backend: sqlite; match: 750/750 (100.00%)
python -m pytest -q
# -> 10 passed (FEATURE_STORE unset again - confirms the rebuild didn't change default behaviour)

# Live proof: Kafka -> stream_processor --live -> Redis -> serve_api, while traffic flows
$env:KAFKA_BOOTSTRAP = "localhost:19092"; $env:FEATURE_STORE = "redis"
uvicorn biq.ingest_api:app --port 8010 &
python -m biq.stream_processor --live &
python -m biq.simulator --sessions 150 --seed 999 --http http://127.0.0.1:8010/track --rate 50 --realtime
# -> posted 1,695 events from 150 sessions
docker exec redpanda rpk group describe biq-stream
# -> TOTAL-LAG 0 (consumer group drained the live traffic, after first draining an older backlog
#    left over from a prior session's live-mode testing on the same "biq-stream" group id)
uvicorn biq.serve_api:app --port 8011 &
curl http://127.0.0.1:8011/features/s_000076
# -> {"session_id":"s_000076","event_timestamp":"2026-11-17T04:07:...","event_count":4,...}
#    (read straight out of Redis through the unchanged /features/{session_id} route)
curl http://127.0.0.1:8011/features/s_nonexistent_xyz
# -> 404 (unchanged not-found behaviour)
# background processes stopped after the check; pipeline/data files rebuilt under the default
# SQLite backend (see above) so the repo's checked-in default state is unaffected
```

## Upgrade 7 — Evidently AI drift reports — done (2026-11-04)

`BIQ_EVIDENTLY=1` switches on Evidently AI HTML drift reports + a dataset-drift verdict alongside
the existing PSI/domain-classifier drift tables in `biq/mlops.py`. With it unset, `run_all.py` and
`pytest` behave exactly as before.

### Blocker found and resolved: Evidently 0.7.23 vs. pandas 3

Evidently 0.7.23 (latest on PyPI) failed to import in the main project venv:
`ImportError: cannot import name 'infer_dtype_from_object' from 'pandas.core.dtypes.common'` -
Evidently still calls an internal pandas API that pandas removed in the 3.x line, and this project's
venv runs pandas 3.1.0 (unpinned in `requirements.txt`). No newer Evidently release exists to fix
this. Rather than downgrading pandas for every pipeline stage, Evidently runs in a **separate,
isolated venv**:

- **`requirements-evidently.txt`** (new): pins `evidently==0.7.23`, `pandas>=2.0,<3`, `pyarrow`.
  `uv venv --python 3.11 .venv-evidently` + `uv pip install -r requirements-evidently.txt --python
  .venv-evidently/Scripts/python.exe` installs cleanly (`evidently 0.7.23` imports fine against
  `pandas 2.3.3`, confirmed). `.venv-evidently/` is gitignored. The main venv/`requirements.txt`/
  pandas are untouched (confirmed by uninstalling the first, failed main-venv install - evidently
  plus 32 transitive deps, and a `plotly` downgrade it pulled in - and rerunning `pytest`: 10 passed,
  unaffected).
- **`biq/evidently_reports.py`** (new): a standalone CLI script, runnable only by
  `.venv-evidently`'s python, never imported by the main venv. Reads reference/current feature
  parquet files, builds `evidently.Report([evidently.presets.DataDriftPreset(columns=DRIFT_FEATURES,
  drift_share=0.5)])`, runs it (`Dataset.from_pandas` + `Report.run()` → `Snapshot`), writes the HTML
  report (`snapshot.save_html()`) and a JSON summary (`dataset_drift`, `share_drifted_columns` - from
  the `DriftedColumnsCount` metric's `{"count", "share"}` value; `dataset_drift = share >= 0.5`,
  confirmed against Evidently's own generated test verdict, not guessed). The exact API (`Report`,
  `Dataset`, `DataDefinition`, `presets.DataDriftPreset`, `Snapshot.dict()/.save_html()`) was read
  from the installed 0.7.23 package source and confirmed with a live test run, not assumed from an
  older/remembered Evidently API - the module docstring records what was checked and why.
- **`biq/mlops.py`**: new `evidently_report()` shells out to `.venv-evidently`'s python running that
  script per scenario (writing reference/current parquet to a temp dir), returning `None` with a
  one-line, non-fatal skip message when `BIQ_EVIDENTLY` isn't `"1"` or `.venv-evidently` doesn't
  exist - so `drift_experiment()`/`real_data_drift_experiment()` behave exactly as before by default.
  `_evidently_row()` adds three columns to both functions' drift tables: `evidently_drift` (its own
  dataset-drift verdict), `evidently_share_drifted`, and `agrees_with_our_rule` (whether that verdict
  matches our existing PSI/domain-classifier `detected` flag) - our rule stays the actual pass/fail
  decision; Evidently is reported alongside it, not substituted in. `_evidently_footer()` appends an
  honest agreement summary to `stage10_drift.md`. HTML reports land in `config.REPORTS / "evidently"`
  - which, because `config.REPORTS` is itself redirected by `BIQ_REPORTS` for the REES46 pipeline
    (`run_all.py --dataset rees46`), resolves to `reports/evidently/` for the simulated pipeline and
    `reports/rees46/evidently/` for REES46 with no special-casing needed.
- **`biq/config.py`**: added `EVIDENTLY_ENABLED` (`BIQ_EVIDENTLY`, default off) and `EVIDENTLY_PYTHON`
  (`BIQ_EVIDENTLY_PYTHON`, default `.venv-evidently/Scripts/python.exe`).
- **`dashboard/app.py`**: Monitoring tab gained a scenario selector (`st.selectbox`) over
  `config.REPORTS / "evidently" / *.html`, embedding the chosen report via
  `streamlit.components.v1.html(..., height=800, scrolling=True)`; shows a setup hint when no reports
  exist yet. Everything else on the tab (PSI table, bar chart, copilot report) is unchanged.

### Honest result: Evidently vs. our PSI/domain-classifier rule

Our rule stays the pass/fail decision in both experiments; Evidently's own dataset-drift verdict
(share of drifted columns >= 0.5, its own preset default) is reported alongside it, honestly:

| Run | Scenarios | Evidently agrees with our rule |
| --- | --- | --- |
| Simulated (`reports/stage10_drift.md`) | 6 | **2/6** |
| REES46 real data (`reports/rees46/stage10_drift.md`) | 5 | **3/5** |

Evidently's per-column voting rule missed 4/5 injected drifts our rule caught in the simulated run
(each shifted only 1-4 of 9 columns individually - under its 50% cutoff - even where the shift was
large and real, e.g. `duration_s` tripling with PSI 1.05 in `slow sessions`). This is the expected,
honest disagreement pattern, not a bug: our domain-classifier check exists specifically to catch
shifts spread thinly across several correlated features, which a per-column vote can structurally
miss. Full breakdown and why: `docs/RESULTS_FOR_REPORT.md` ("Upgrade 7").

### Verification run (2026-11-04)

```
uv pip install evidently --python .venv/Scripts/python.exe
# -> installs evidently 0.7.23 cleanly, but downgrades plotly 7.1.0 -> 5.24.1
python -c "import evidently"
# -> ImportError: cannot import name 'infer_dtype_from_object' from 'pandas.core.dtypes.common'
#    (pandas 3.1.0 in this venv; confirmed no newer evidently release exists on PyPI)
uv pip uninstall evidently <32 transitive deps> --python .venv/Scripts/python.exe
uv pip install "plotly==7.1.0" --python .venv/Scripts/python.exe   # restore
pytest
# -> 10 passed (main venv/requirements.txt/pandas confirmed untouched)

uv venv --python 3.11 .venv-evidently
uv pip install -r requirements-evidently.txt --python .venv-evidently/Scripts/python.exe
# -> evidently 0.7.23 + pandas 2.3.3 (not pandas 3)
.venv-evidently/Scripts/python.exe -c "import evidently; print(evidently.__version__)"
# -> OK 0.7.23

BIQ_EVIDENTLY=1 python -c "from biq import mlops; mlops.drift_experiment()"
# -> 6/6 scenarios ran Evidently successfully; reports/evidently/*.html (6 files, ~4MB each);
#    stage10_drift.md table includes evidently_drift/evidently_share_drifted/agrees_with_our_rule;
#    footer: "Evidently AI agreement: 2/6 scenarios ... matched our PSI/domain-classifier rule"
python -c "from biq import mlops; mlops.drift_experiment()"   # BIQ_EVIDENTLY unset
# -> identical PSI/domain-classifier table; evidently_* columns blank; skip message printed;
#    confirms default behaviour is unchanged

BIQ_EVIDENTLY=1 BIQ_DATA=.../data/rees46 BIQ_REPORTS=.../reports/rees46 \
  python -c "from biq import mlops; mlops.real_data_drift_experiment()"
# -> 5/5 scenarios ran; reports/rees46/evidently/*.html (5 files); agreement footer: "3/5 scenarios"
#    (config.REPORTS redirection alone put the files under reports/rees46/evidently/, as intended)

pytest
# -> 10 passed, BIQ_EVIDENTLY unset (default path unaffected)

# Dashboard, live (streamlit run dashboard/app.py, QDRANT_URL set to avoid the pre-existing
# embedded-Qdrant file-lock limitation noted in Upgrade 6 - unrelated to this upgrade):
# Monitoring tab -> scenario selector lists all 6 simulated scenarios; selecting
# "comparer_surge_85_heavy_comparers" embeds its Evidently HTML report showing "Dataset Drift is
# detected ... 5 Drifted Columns ... 0.556 Share of Drifted Columns", matching the stage10 table row;
# switching to another scenario re-embeds its own report - confirmed via claude-in-chrome screenshots
```

## Upgrade 6 — local LLM copilot via Ollama — done (2026-11-03)

`BIQ_LLM_PROVIDER=ollama` switches `biq/copilot.py::ask()` to `ask_ollama()`, which talks to a local
Ollama server (model `qwen2.5:3b`, free, already pulled; no other model was pulled - disk space is
tight) through its OpenAI-compatible endpoint (`http://localhost:11434/v1`, `BIQ_OLLAMA_URL`) using
the `openai` Python package. With `BIQ_LLM_PROVIDER` unset (default `"anthropic"`), `ask()`'s
dispatch logic is unchanged: `ANTHROPIC_API_KEY` set → `ask_llm()`, otherwise the offline router —
so `run_all.py` and `pytest` behave exactly as before.

- **`biq/config.py`**: added `LLM_PROVIDER` (`BIQ_LLM_PROVIDER`, default `"anthropic"`) and
  `OLLAMA_BASE_URL` (`BIQ_OLLAMA_URL`, default `http://localhost:11434/v1`); `LLM_MODEL` now
  defaults to `qwen2.5:3b` when the provider is `"ollama"` and to `claude-haiku-5-5` otherwise
  (`BIQ_LLM_MODEL` still overrides either).
- **`biq/copilot.py`**: new `ask_ollama()` reuses `TOOLS`/`IMPL`/`guard_sql`/`run_sql`/`grounded()`
  unchanged - only `OPENAI_TOOLS` (a thin reshape of `TOOLS` into OpenAI's
  `{"type": "function", "function": {...}}` tool-calling format) and the chat-completions loop are
  new. Same regenerate-once-then-refuse flow as `ask_llm()`: on an ungrounded answer, the bad
  numbers are reported back to the model and it gets one retry before the honest refusal. `ask()`
  now checks `config.LLM_PROVIDER == "ollama"` first (falling back to offline mode if the server/model
  is unreachable, same pattern as the existing Anthropic network-error fallback), otherwise keeps the
  original `ANTHROPIC_API_KEY` check. `evaluate()` writes `reports/stage9_copilot_ollama.md` when the
  provider is `ollama` and `reports/stage9_copilot.md` otherwise, so neither report overwrites the
  other.
- **`dashboard/app.py`**: the "Ask BehaviorIQ" tab's mode caption now shows "local LLM via Ollama
  (qwen2.5:3b)" when `BIQ_LLM_PROVIDER=ollama`, "Claude (...)" when `ANTHROPIC_API_KEY` is set, and
  the offline-mode message otherwise - it calls the same `biq.copilot.ask()`, so no other dashboard
  change was needed. `biq/serve_api.py`'s `/ask` route already only calls `copilot.ask()`, so it
  picks up the new provider automatically too.
- **`requirements.txt`**: added `openai`.

### Honest result: `reports/stage9_copilot_ollama.md`

Ran `python -m biq.copilot --eval` with `BIQ_LLM_PROVIDER=ollama` against the simulated DuckDB
warehouse (`QDRANT_URL=http://localhost:6333` to avoid the pre-existing embedded-Qdrant file-lock
limitation, same workaround as the ClickHouse-era eval runs):

- **Correct: 2/16 (12%)** — far below the offline router's 16/16 and the >=90% DoD target.
- **Unfounded numbers that reached the user: 0** (1 question failed the grounding check twice and
  was correctly refused rather than shown).
- **Malicious/non-whitelisted SQL blocked: 4/4.**

This was **not tuned to the model** — same 16 eval questions as every prior copilot run. The failures
break down as: 9/14 picked the wrong view or guessed a non-existent column and never recovered
within the 6-tool-call budget (e.g. looked for `segment` in `v_funnel_daily` instead of
`v_segment_summary`); 4/14 picked the right view but wrote wrong SQL (e.g. `COUNT(*)` on an
already-aggregated view instead of reading its `session_count` column, or an exact-string filter
that didn't match the real label); 1/14 failed grounding twice and was refused. The 2 correct answers
both needed only a single `find_similar_sessions` tool call with no SQL authoring. Full breakdown,
and why this is model-size-limited rather than a guardrail problem (zero unfounded numbers reached
the user across both the Claude and Ollama runs, and both SQL-attack tests are 4/4 blocked
regardless of model): `docs/RESULTS_FOR_REPORT.md` ("Upgrade 6").

### Verification run (2026-11-03)

```
curl http://localhost:11434/api/tags
# -> qwen2.5:3b present, capabilities: ["completion","tools"]; no other model pulled

uv pip install openai --python .venv/Scripts/python.exe
# -> openai 3.42.1 (installing into the venv, not the global interpreter pip resolved to)

$env:BIQ_LLM_PROVIDER = "ollama"
python -m biq.copilot "Which segment has the highest abandonment rate?"
# -> real tool-calling round trip against DuckDB confirmed (SELECT segment, session_count,
#    abandonment_rate FROM v_segment_summary ORDER BY abandonment_rate DESC LIMIT 1 -> "cart
#    abandoners (c3)", 100.00%, 1,005 sessions), mode: "qwen2.5:3b"

$env:QDRANT_URL = "http://localhost:6333"   # embedded-Qdrant file lock, same as prior eval runs
python -m biq.copilot --eval
# -> reports/stage9_copilot_ollama.md: 2/16 (12%) correct, 0 unfounded numbers reached the user,
#    4/4 attacks blocked (full per-question table and failure analysis above)

# serve_api /ask, live:
uvicorn biq.serve_api:app --port 8011   # (BIQ_LLM_PROVIDER=ollama, QDRANT_URL set)
curl -X POST http://127.0.0.1:8011/ask -d '{"question":"How many sessions ended in a purchase?"}'
# -> {"mode": "qwen2.5:3b", "answer": "...", "grounded": true, "sql": [...]}  (24s wall time)

# dashboard "Ask BehaviorIQ" tab, live (streamlit run dashboard/app.py, same env):
# -> caption reads "Mode: local LLM via Ollama (qwen2.5:3b)"; asked "What do sessions similar to
#    users who compared multiple products usually end in?" -> answered correctly ("browse 90%,
#    abandon 10%, mean similarity 0.9286") with "every number above was checked" grounding note -
#    screenshotted via claude-in-chrome

pytest
# -> 10 passed, BIQ_LLM_PROVIDER/BIQ_OLLAMA_URL/ANTHROPIC_API_KEY all unset (default anthropic-gated
#    path unaffected)
```

### Follow-up (2026-11-03) — schema-in-prompt for the Ollama path

The 2/16 result above was dominated by one fixable pattern: the model mostly never called
`describe_schema` before guessing a view/column name. Added `biq/copilot.py::_ollama_system_prompt()`
(Ollama path only - `SYSTEM`, `TOOLS`, `guard_sql`, `run_sql`, `grounded()`, the regenerate-once
logic, and the Anthropic path are all untouched), which appends the `describe_schema()` output (all
8 view names, descriptions, exact column names/types) plus one worked `run_sql` example on
`v_outcome_summary` (not one of the 16 eval questions) directly into `ask_ollama()`'s system message,
so the model sees the real schema before writing SQL instead of needing an extra tool round trip to
look it up. `evaluate()` gained an optional `out_name` parameter (default unchanged: still
`stage9_copilot_ollama.md`/`stage9_copilot.md` based on `LLM_PROVIDER`) so this run could be saved
alongside the original without overwriting it.

Result, same 16 questions, not tuned to the model, same `QDRANT_URL` workaround:

| Run | File | Correct | Unfounded numbers reaching the user | Attacks blocked |
| --- | --- | --- | --- | --- |
| v1 (no schema in prompt) | `reports/stage9_copilot_ollama.md` | 2/16 (12%) | 0 | 4/4 |
| v2 (schema in prompt) | `reports/stage9_copilot_ollama_v2.md` | **11/16 (69%)** | 0 | 4/4 |

Still below the >=90% DoD target and the offline router's 16/16, but a single prompt change took the
local copilot from 12% to 69% with no retraining, no bigger model, and no extra tool call. The 5
remaining v2 failures: an unweighted-average conversion-rate mistake (26.30% vs. the true 26.2%),
two wrong-aggregation-level mistakes on `v_conversion_by_segment` (read one device x referrer x
segment row instead of aggregating across the device), one honest refusal after failing the
grounding check twice, and one data-*value* mistake (filtered on `'cart_abandoners'` instead of the
real label `'cart abandoners (c3)'` - the schema block gives column names/types, not the string
values stored in them, so this failure mode is structurally out of its reach). Full per-question
breakdown: `docs/RESULTS_FOR_REPORT.md` ("Upgrade 6" → "Follow-up: schema-in-prompt").

```
python -m pytest -q
# -> 10 passed, no env vars set (default anthropic-gated path still unaffected)

python -c "from biq import copilot; print(len(copilot._ollama_system_prompt()))"
# -> 3,160 chars: SYSTEM + all 8 view docs/columns + the v_outcome_summary worked example

$env:BIQ_LLM_PROVIDER = "ollama"; $env:QDRANT_URL = "http://localhost:6333"
python -c "from biq import copilot; copilot.evaluate(out_name='stage9_copilot_ollama_v2.md')"
# -> reports/stage9_copilot_ollama_v2.md: 11/16 (69%) correct, 0 unfounded numbers reached the
#    user, 4/4 attacks blocked; reports/stage9_copilot_ollama.md (v1, 2/16) left untouched on disk
```

## Upgrade 5 — REES46 real-data replay — done (2026-11-03)

`python run_all.py --dataset rees46 [--sessions N]` replays the REES46 "eCommerce behavior data
from multi category store" CSV (`data/external/2019-Oct.csv`) through the same pipeline instead of
`biq.simulator`, writing to `data/rees46/`, `reports/rees46/`, `artifacts/rees46/` (via
`BIQ_DATA`/`BIQ_REPORTS`/`BIQ_ARTIFACTS`) - the simulated outputs under `data/`, `reports/`,
`artifacts/` are never touched, and with `--dataset` unset (default `simulated`) `run_all.py` and
`pytest` behave exactly as before.

- **`biq/replay.py`** (new): maps REES46's `view`/`cart`/`remove_from_cart`/`purchase` onto
  `product_view`/`add_to_cart`/`remove_from_cart`/`purchase`, `category_code`'s top-level token
  onto `category`, builds `page` from `product_id`, and salts+SHA-256-hashes `user_id` and the
  session grouping key so nothing traces back to the source id. The 5.7GB CSV (42.4M rows, 9.24M
  distinct `user_session` ids) is never loaded as one block: a reservoir sample of N session ids is
  taken entirely inside DuckDB's out-of-core CSV reader, then only that sample's rows are pulled -
  this is the "read in chunks" step, done by a columnar engine instead of a hand-rolled chunk loop.
  REES46's own `user_session` grouping is discarded and **re-sessionised with OUR rule** (30 min
  inactivity or a purchase closes a session); `docs/RESULTS_FOR_REPORT.md` has the measured split
  rate. No device/referrer column exists in REES46 at all, so every event is stamped
  `device="desktop"`, `referrer="direct"` as a documented placeholder (see `docs/CONTRACTS.md`).
- **`biq/mining.py`**: `run()`'s ARI-vs-persona row is now conditional on `config.TRUTH` existing -
  real data has no hidden persona label, so it's omitted instead of crashing on a missing file.
- **`biq/mlops.py`**: added `real_data_drift_experiment()`. The first version of this upgrade
  reused `drift_experiment()`, which compares the training reference against `biq.simulator`
  traffic - that is meaningless on real data, because REES46 and the simulator differ structurally
  regardless of any injected shift, so it flagged *every* scenario as drifted, including a true
  no-shift control (false alarm on control = True). `real_data_drift_experiment()` instead uses
  only real traffic: reference = train split, control = the held-out test split as-is, and four
  injected scenarios are the SAME test-split sessions resampled/perturbed (long-session-heavy
  resample as a fallback for "mobile-heavy" - REES46 has no device signal to resample on;
  cart-heavy 4x resample; dwell/time-gaps x3; category-mix 4x resample on the top category).
  `drift_experiment()` itself is untouched and still runs for the simulated pipeline.
- **`biq/encoder.py` / `biq/intent.py`**: the TF-IDF+SVD-64 text baseline assumed at least 64
  distinct n-gram features; REES46's event vocabulary is small enough (3 behavioural event types
  observed) that the train split can have far fewer. Both now cap `n_components` at
  `min(DIM, n_features - 1)` instead of hardcoding 64 - a real-data robustness fix, not a REES46
  special case.
- **`biq/retrieval.py`**: Qdrant collection names are now `f"{COLLECTION_PREFIX}_custom"` /
  `f"{COLLECTION_PREFIX}_baseline"` (`BIQ_QDRANT_PREFIX`, default `"biq"`, unchanged). `run_all.py`
  sets it to `"biq_rees46"` for the real-data pipeline so a run against a *shared Qdrant server*
  (`QDRANT_URL` set) can never overwrite the simulated collections; local-mode Qdrant was already
  isolated via the separate `BIQ_DATA` path.
- **`run_all.py`**: new `--dataset {simulated,rees46}` and `--sessions` flags; `REES46_STAGES` =
  `replay, stream, mining, warehouse, encoder, retrieval, intent, bandit (skipped with a message -
  no counterfactual outcomes in logged data), mlops (drift only, not MLflow registration)`. Copilot
  isn't run for this dataset. The original `STAGES` dict (used by `--dataset simulated`, the
  default) is unchanged.

### Verification run (2026-11-03)

```
python run_all.py --dataset rees46 --sessions 30000
# -> replay:    sampled 30,000 of 9,244,421 source sessions -> 137,327 raw rows
#    re-sessionised: 30,000 source -> 32,066 final sessions; 1,657 (5.5%) split
#    wrote 137,327 events / 32,066 sessions / 27,236 catalogue products
#    event_type counts: product_view 131,871, add_to_cart 3,018, purchase 2,438
#      (this file has no remove_from_cart rows at all)
# -> stream:    137,327 events -> 32,066 closed sessions, 0 lost; out-of-order 59
# -> mining:    HDBSCAN found 14 clusters (noise 0.3%), incl. a 860-session "cart abandoners"
#               cluster at 62% abandonment vs ~5% elsewhere; no ari_vs_persona (no ground truth)
# -> warehouse: 137,327 events, 32,066 sessions, 8 views; spot check 10/10
# -> encoder:   vocab 7 tokens (train 22,447 / val 4,810 / test 4,809); baseline fallback
#               auto-capped to SVD-14 (see biq/encoder.py fix above); neighbour-outcome agreement:
#               custom 0.829, baseline 0.912, random 0.820 (pretrain stopped after 15 epochs)
# -> retrieval: indexed 22,447 sessions into biq_rees46_custom / biq_rees46_baseline;
#               precision@10 0.894 / 0.886; abandoning-neighbour share 0.30 (pool base rate 0.03)
# -> intent:    full-prefix macro-F1: A 0.669, B 0.708, B+R 0.708, C_base 0.711, C 0.588, C+R 0.573
#               (same ranking as the simulator run: count/hand-crafted features beat the learned
#               contrastive embedding; retrieval augmentation is not statistically significant)
# -> bandit:    skipped with the explanatory message (see run_all.py::stage_bandit_skip)
# -> mlops:     real_data_drift_experiment(): 2/4 injected drifts detected, NO false alarm on
#               control (long-session-heavy and dwell-x3 caught; cart-heavy and category-mix missed
#               - see docs/RESULTS_FOR_REPORT.md for why both misses are explainable, not bugs)
# -> All done in 86.3 min (plus a separate 59s re-run of just --only mlops after the drift-method
#    fix landed mid-run; nothing upstream of mlops needed re-running)

pytest
# -> 10 passed, BIQ_DATA/BIQ_REPORTS/BIQ_ARTIFACTS/KAFKA_BOOTSTRAP/QDRANT_URL/WAREHOUSE all unset
#    (default simulated path unaffected)

python run_all.py --only warehouse        # spot-check the simulated pipeline is still untouched
# -> warehouse built: 74,513 events, 6,000 sessions, 8 views -> data/warehouse.duckdb (unchanged)
```

Full tables and the side-by-side comparison with the simulated run: `docs/RESULTS_FOR_REPORT.md`
("Upgrade 5" section). What REES46 can and can't give us (missing event types, no device/referrer,
no hidden personas): `docs/CONTRACTS.md` ("Real-data replay (REES46)" section).

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
