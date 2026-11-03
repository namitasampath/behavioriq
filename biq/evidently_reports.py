"""Evidently AI data-drift report generator for Upgrade 7.

Runs ONLY inside the isolated `.venv-evidently` environment (see `requirements-evidently.txt`) -
never imported by the main project venv or by `biq/mlops.py` directly. Evidently 0.7.23 (current on
PyPI) calls an internal pandas API that pandas 3.x removed, so it cannot be installed alongside
this project's pandas 3.1.0 without downgrading a dependency every pipeline stage shares. Instead
`biq/mlops.py` shells out to this script with `.venv-evidently`'s python (see
`mlops.py::evidently_report()`), passing reference/current feature parquet files and getting back
an HTML report plus a JSON summary.

API used (Evidently 0.7.23, confirmed by reading the installed package source and a live test run
- the Evidently API changed across versions, so this was not guessed):
    evidently.Report([evidently.presets.DataDriftPreset(columns=[...], drift_share=0.5)])
        .run(current_dataset, reference_dataset) -> Snapshot
    snapshot.save_html(path)
    snapshot.dict()["metrics"] -> list of {"config": {"type": ...}, "value": ...}; the
        "evidently:metric_v2:DriftedColumnsCount" entry's value is {"count": N, "share": frac}.
Evidently's own dataset-drift verdict (confirmed from its generated test: "Share of Drifted
Columns: Less <threshold>", FAIL when share >= threshold) is share_of_drifted_columns >=
drift_share (0.5 is Evidently's own preset default - not our PSI>0.2 threshold).

    python biq/evidently_reports.py --reference ref.parquet --current cur.parquet \
        --columns event_count,n_product_view,... --html-out out.html --json-out out.json
"""
import argparse
import json

import pandas as pd
from evidently import Dataset, DataDefinition, Report
from evidently.presets import DataDriftPreset

DRIFT_SHARE_THRESHOLD = 0.5  # Evidently's own DataDriftPreset default for the dataset-level verdict


def run(reference_path: str, current_path: str, columns: list[str], html_out: str, json_out: str) -> dict:
    ref = pd.read_parquet(reference_path)[columns]
    cur = pd.read_parquet(current_path)[columns]

    report = Report([DataDriftPreset(columns=columns, drift_share=DRIFT_SHARE_THRESHOLD)])
    ref_ds = Dataset.from_pandas(ref, data_definition=DataDefinition())
    cur_ds = Dataset.from_pandas(cur, data_definition=DataDefinition())
    snapshot = report.run(cur_ds, ref_ds)
    snapshot.save_html(html_out)

    drifted_count = next(
        m for m in snapshot.dict()["metrics"]
        if m["config"]["type"] == "evidently:metric_v2:DriftedColumnsCount"
    )
    share = float(drifted_count["value"]["share"])
    summary = {
        "n_columns": len(columns),
        "n_drifted_columns": int(drifted_count["value"]["count"]),
        "share_drifted_columns": share,
        "drift_share_threshold": DRIFT_SHARE_THRESHOLD,
        "dataset_drift": bool(share >= DRIFT_SHARE_THRESHOLD),
    }
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", required=True)
    p.add_argument("--current", required=True)
    p.add_argument("--columns", required=True, help="comma-separated column names")
    p.add_argument("--html-out", required=True)
    p.add_argument("--json-out", required=True)
    args = p.parse_args()
    summary = run(args.reference, args.current, args.columns.split(","), args.html_out, args.json_out)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
