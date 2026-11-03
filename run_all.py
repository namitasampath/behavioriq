"""Run the whole BehaviorIQ pipeline end to end (about 10-15 minutes on a laptop CPU).

    python run_all.py                  # everything, on the synthetic simulator (default, unchanged)
    python run_all.py --from encoder   # resume from a stage
    python run_all.py --only copilot   # one stage
    python run_all.py --dataset rees46 [--sessions 30000]   # real-data replay (REES46), see below

Stages (simulated): simulate, stream, mining, warehouse, encoder, retrieval, intent, bandit, mlops, copilot
Stages (--dataset rees46): replay, stream, mining, warehouse, encoder, retrieval, intent, mlops
  (replay stands in for simulate; bandit is skipped - real logs have no counterfactual outcomes;
  mlops only runs drift detection, not MLflow registration; copilot isn't run for this dataset.)
  Writes to data/rees46/ and reports/rees46/ (and artifacts/rees46/) - the simulated outputs under
  data/, reports/, artifacts/ are never touched.
"""
from __future__ import annotations

import argparse
import os
import time
import warnings
from pathlib import Path

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
warnings.filterwarnings("ignore")


def stage_simulate():
    import sys
    from biq import simulator
    sys.argv = ["simulator"]
    simulator.main()


def stage_stream():
    from biq import stream_processor
    stream_processor.replay()


def stage_mining():
    from biq import mining
    mining.run()


def stage_warehouse():
    import pandas as pd
    from biq import config, warehouse
    warehouse.build(pd.read_parquet(config.DATA / "segments.parquet"))
    warehouse.spot_check()


def stage_encoder():
    from biq import encoder
    encoder.main()


def stage_retrieval():
    import shutil
    from biq import config, retrieval
    shutil.rmtree(config.QDRANT_PATH, ignore_errors=True)
    retrieval.build_index()
    retrieval.evaluate()


def stage_intent():
    from biq import intent
    intent.main()


def stage_bandit():
    from biq import bandit
    bandit.main()


def stage_mlops():
    from biq import mlops
    mlops.main()


def stage_copilot():
    from biq import copilot
    copilot.evaluate()


STAGES = {n[6:]: f for n, f in globals().items() if n.startswith("stage_")}


# ---------------------------------------------------------------- real-data (REES46) stages
def stage_replay():
    import sys
    from biq import replay
    sys.argv = ["replay"]
    replay.main()


def stage_mlops_drift():
    from biq import mlops
    mlops.real_data_drift_experiment()


def stage_bandit_skip():
    print("skipping bandit: it needs counterfactual outcomes (what would have happened under an "
          "action that wasn't taken), which the simulator's hidden-persona response model supplies "
          "but a real, logged dataset like REES46 never can. See biq/bandit.py and README "
          "'Honest limitations'.")


REES46_STAGES = {
    "replay": stage_replay, "stream": stage_stream, "mining": stage_mining,
    "warehouse": stage_warehouse, "encoder": stage_encoder, "retrieval": stage_retrieval,
    "intent": stage_intent, "bandit": stage_bandit_skip, "mlops": stage_mlops_drift,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start")
    ap.add_argument("--only")
    ap.add_argument("--dataset", choices=["simulated", "rees46"], default="simulated")
    ap.add_argument("--sessions", type=int, default=None,
                    help="--dataset rees46 only: how many REES46 sessions to sample (default 30000)")
    a = ap.parse_args()

    if a.dataset == "rees46":
        root = Path(__file__).resolve().parent
        os.environ["BIQ_DATA"] = str(root / "data" / "rees46")
        os.environ["BIQ_REPORTS"] = str(root / "reports" / "rees46")
        os.environ.setdefault("BIQ_ARTIFACTS", str(root / "artifacts" / "rees46"))
        os.environ.setdefault("BIQ_QDRANT_PREFIX", "biq_rees46")  # separate Qdrant collections if
                                                                   # QDRANT_URL points at a shared server
        if a.sessions:
            os.environ["BIQ_REES46_SESSIONS"] = str(a.sessions)
        stages = REES46_STAGES
    else:
        stages = STAGES

    for flag, val in (("--only", a.only), ("--from", a.start)):
        if val and val not in stages:
            ap.error(f"{flag} {val} is not valid for --dataset {a.dataset}; choices: {list(stages)}")

    names = list(stages)
    if a.only:
        names = [a.only]
    elif a.start:
        names = names[names.index(a.start):]
    t0 = time.time()
    for n in names:
        t = time.time()
        print(f"\n===== {n} ({a.dataset}) =====", flush=True)
        stages[n]()
        print(f"----- {n} done in {time.time() - t:.0f}s", flush=True)
    reports_dir = os.environ.get("BIQ_REPORTS", "./reports")
    print(f"\nAll done in {(time.time() - t0) / 60:.1f} min. Reports are in {reports_dir}."
          + ("  Start the dashboard with:  streamlit run dashboard/app.py" if a.dataset == "simulated" else ""))


if __name__ == "__main__":
    main()
