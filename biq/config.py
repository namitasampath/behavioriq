"""Paths and settings. Override any value with an environment variable of the same name."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("BIQ_DATA", ROOT / "data"))
ARTIFACTS = Path(os.getenv("BIQ_ARTIFACTS", ROOT / "artifacts"))
REPORTS = Path(os.getenv("BIQ_REPORTS", ROOT / "reports"))
for p in (DATA, ARTIFACTS, REPORTS):
    p.mkdir(parents=True, exist_ok=True)

RAW_EVENTS = DATA / "raw_events.parquet"
TRUTH = DATA / "sessions_truth.parquet"          # hidden personas: evaluation only, never a feature
SESSIONS = DATA / "sessions.parquet"              # closed sessions emitted by the stream processor
FEATURE_LOG = DATA / "offline_features.parquet"   # offline feature store
ONLINE_DB = DATA / "online_store.sqlite"          # online feature store
STREAM_LOG = DATA / "stream" / "user-events.jsonl"  # local event bus (Kafka/Redpanda when KAFKA_BOOTSTRAP is set)
WAREHOUSE = DATA / "warehouse.duckdb"
QDRANT_PATH = DATA / "qdrant"
CATALOGUE = DATA / "catalogue.json"
MLRUNS = ROOT / "mlruns"

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "")      # e.g. localhost:19092 for Redpanda
K_ANON_MIN = int(os.getenv("K_ANON_MIN", "20"))
LLM_MODEL = os.getenv("BIQ_LLM_MODEL", "claude-haiku-5-5")
SEED = int(os.getenv("BIQ_SEED", "42"))
N_SESSIONS = int(os.getenv("BIQ_N_SESSIONS", "6000"))
