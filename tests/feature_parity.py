"""Feature store training/serving-parity check (Upgrade 4).

For N random sessions, compares the online store's latest feature snapshot against
get_historical_features()'s point-in-time join evaluated at that exact snapshot timestamp. Both
reads must agree exactly: write() feeds the SAME feature dict into the online store and the
offline Parquet buffer in biq/feature_store.py, so any mismatch means the two stores have drifted
apart, not that the feature definition itself differs.

Not named test_*.py on purpose: it needs a store already populated (by
`python -m biq.stream_processor --replay` or `--live`) and is a one-shot verification report, not
a pytest unit test, so pytest's default collection does not pick it up.

    python tests/feature_parity.py [--n 50] [--seed 0]
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from biq import config  # noqa: E402
from biq.feature_store import FeatureStore, get_historical_features  # noqa: E402
from biq.stream_processor import FEATURE_COLUMNS  # noqa: E402


def _equal(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return a == b


def check(n: int = 50, seed: int = 0) -> float:
    store = FeatureStore()
    sessions = pd.read_parquet(config.SESSIONS)["session_id"].tolist()
    sample = random.Random(seed).sample(sessions, min(n, len(sessions)))

    online_rows = [f for f in (store.get_online_features(sid) for sid in sample) if f is not None]
    missing = len(sample) - len(online_rows)
    online = pd.DataFrame(online_rows)
    online["event_timestamp"] = pd.to_datetime(online["event_timestamp"], utc=True)

    hist = get_historical_features(online[["session_id", "event_timestamp"]]).set_index("session_id")

    matches = total = 0
    mismatches = []
    for _, orow in online.iterrows():
        hrow = hist.loc[orow.session_id]
        hrow = hrow.iloc[-1] if isinstance(hrow, pd.DataFrame) else hrow
        for col in FEATURE_COLUMNS:
            total += 1
            if _equal(orow[col], hrow[col]):
                matches += 1
            else:
                mismatches.append((orow.session_id, col, orow[col], hrow[col]))

    pct = 100 * matches / total if total else 0.0
    print(f"backend: {store.backend}")
    print(f"sessions sampled: {len(sample)} (missing from online store: {missing})")
    print(f"values compared: {len(online)} sessions x {len(FEATURE_COLUMNS)} columns = {total}")
    print(f"match: {matches}/{total} ({pct:.2f}%)")
    for sid, col, a, b in mismatches[:10]:
        print(f"  MISMATCH {sid}.{col}: online={a!r} historical={b!r}")
    return pct


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    result = check(args.n, args.seed)
    sys.exit(0 if result == 100.0 else 1)
