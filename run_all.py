"""Run the whole BehaviorIQ pipeline end to end (about 10-15 minutes on a laptop CPU).

    python run_all.py                  # everything
    python run_all.py --from encoder   # resume from a stage
    python run_all.py --only copilot   # one stage

Stages: simulate, stream, mining, warehouse, encoder, retrieval, intent, bandit, mlops, copilot
"""
from __future__ import annotations

import argparse
import os
import time
import warnings

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", choices=list(STAGES))
    ap.add_argument("--only", choices=list(STAGES))
    a = ap.parse_args()
    names = list(STAGES)
    if a.only:
        names = [a.only]
    elif a.start:
        names = names[names.index(a.start):]
    t0 = time.time()
    for n in names:
        t = time.time()
        print(f"\n===== {n} =====", flush=True)
        STAGES[n]()
        print(f"----- {n} done in {time.time() - t:.0f}s", flush=True)
    print(f"\nAll done in {(time.time() - t0) / 60:.1f} min. Reports are in ./reports. "
          f"Start the dashboard with:  streamlit run dashboard/app.py")


if __name__ == "__main__":
    main()
