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
WAREHOUSE_DB = DATA / "warehouse.duckdb"
QDRANT_PATH = DATA / "qdrant"
CATALOGUE = DATA / "catalogue.json"
MLRUNS = ROOT / "mlruns"

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "")      # e.g. localhost:19092 for Redpanda
K_ANON_MIN = int(os.getenv("K_ANON_MIN", "20"))

WAREHOUSE_BACKEND = os.getenv("WAREHOUSE", "duckdb")    # "clickhouse" for ClickHouse (facts) + PostgreSQL (dims)

# ClickHouse / PostgreSQL, reached from this Python process via the published Docker ports
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "biq")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "biq_admin")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "biq_admin_pw")
CLICKHOUSE_RO_USER = os.getenv("CLICKHOUSE_RO_USER", "biq_ro")
CLICKHOUSE_RO_PASSWORD = os.getenv("CLICKHOUSE_RO_PASSWORD", "biq_ro_pw")

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "biq")
POSTGRES_USER = os.getenv("POSTGRES_USER", "biq")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "biq_pw")

# Addresses used only inside SQL text executed BY the ClickHouse server itself (resolves compose
# service names on the Docker network, not "localhost" like the vars above)
POSTGRES_HOST_IN_DOCKER = os.getenv("POSTGRES_HOST_IN_DOCKER", "postgres")
KAFKA_BOOTSTRAP_IN_DOCKER = os.getenv("KAFKA_BOOTSTRAP_IN_DOCKER", "redpanda:9092")

# LLM copilot backend: "anthropic" (default, needs ANTHROPIC_API_KEY) or "ollama" (free, local,
# needs an Ollama server reachable at OLLAMA_BASE_URL with BIQ_LLM_MODEL pulled)
LLM_PROVIDER = os.getenv("BIQ_LLM_PROVIDER", "anthropic")
OLLAMA_BASE_URL = os.getenv("BIQ_OLLAMA_URL", "http://localhost:11434/v1")
LLM_MODEL = os.getenv("BIQ_LLM_MODEL", "qwen2.5:3b" if LLM_PROVIDER == "ollama" else "claude-haiku-5-5")
SEED = int(os.getenv("BIQ_SEED", "42"))
N_SESSIONS = int(os.getenv("BIQ_N_SESSIONS", "6000"))
