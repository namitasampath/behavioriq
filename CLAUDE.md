# BehaviorIQ

A real-time, LLM-augmented behavioural intelligence platform: clickstream events are sessionised
as they arrive, each session becomes a learned behavioural embedding, similar past sessions are
retrieved from a vector database, intent is predicted with a retrieval-augmented classifier, a
contextual bandit picks the next-best action, and an LLM copilot answers plain-English questions
grounded in the warehouse. See `README.md` for the full stage-by-stage breakdown, `docs/CONTRACTS.md`
for the canonical event schema and privacy rules, and `docs/report.pdf` for the project report.

This repo is being incrementally upgraded from its pure-Python "laptop edition" toward the
production architecture described in the report, one component per session.

## Rules for every upgrade session

- **One component per session.** Each session upgrades exactly one piece of infrastructure.
- **Env-var-gated, default unchanged.** Every upgrade is switched on by an environment variable;
  with it unset, `python run_all.py` and `pytest` must still pass exactly as before.
- **Docker Compose profiles for new services.** New infra goes into `infra/docker-compose.yml`
  under a dedicated compose profile (e.g. `profiles: ["warehouse"]`), with pinned image versions,
  healthchecks, and memory limits. Never modify the existing redpanda/console/qdrant services.
- **Helper scripts: Python or PowerShell only**, never bash (`.gitattributes` forces LF).
- **Run it and prove it.** After building, actually run it and show proof (counts, query output,
  health checks) before claiming success — don't just claim tests would pass.
- **Keep the public interface stable.** Where an upgrade changes a backend (e.g. the warehouse
  connection), the modules that consume it (`biq/copilot.py`, `dashboard/app.py`) must keep
  working unchanged.
- **Update docs each session.** Update the README "Production upgrade" table row for whatever was
  wired up, and log progress in `docs/PROGRESS.md`.
- **Stop after the upgrade.** Don't start the next upgrade in the same session.

## Upgrades done so far

1. Redpanda (Kafka) + Qdrant server, via `infra/docker-compose.yml` — `KAFKA_BOOTSTRAP`, `QDRANT_URL`.
2. ClickHouse (facts) + PostgreSQL (dims) warehouse — `WAREHOUSE=clickhouse`.

See `docs/PROGRESS.md` for details and verification output.
