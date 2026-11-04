"""Summarize minute-data coverage from fetch results and cached datasets.

Reads raw/datasets/results.jsonl (+ index.json for ids) and each cached dataset,
then reports per product x window x month: rows, distinct trading days, expected
days from the task plan, and missing-day counts. Also verifies each dataset
against its real session grid at the requested frequency.

usage: python3 coverage_report.py [--out raw/reference/coverage.csv]
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_grid import window_grid_freq  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.expanduser("~/.dsh-quantstudio/quantskills/database")


def main() -> None:
    out_path = os.path.join(ROOT, "raw", "reference", "coverage.csv")
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]
    meta = json.load(open(os.path.join(ROOT, "raw", "contract_metadata.json"), encoding="utf-8"))
    index = json.load(open(os.path.join(ROOT, "raw", "datasets", "index.json"), encoding="utf-8"))
    results = [json.loads(line) for line in open(os.path.join(ROOT, "raw", "datasets", "results.jsonl"), encoding="utf-8") if line.strip()]
    tasks = {}
    for path in ("/tmp/tasks_min_q1.json", "/tmp/tasks_min_q2.json", "/tmp/tasks_min_q3.json"):
        if os.path.exists(path):
            for t in json.load(open(path, encoding="utf-8")):
                tasks[t["name"]] = t

    rows_out = []
    agg = defaultdict(lambda: {"datasets": 0, "rows": 0, "days": 0, "expected": 0, "gap_ds": 0, "failed": 0})
    for rec in results:
        t = tasks.get(rec["name"])
        if not t:
            continue
        product = t["product"]
        window = t.get("window", "?")
        contract = t.get("contract", "?")
        month = (t.get("start") or "")[:6]
        key = (product, window, month)
        if not rec.get("ok"):
            agg[key]["failed"] += 1
            rows_out.append([product, window, month, contract, rec["name"], 0, 0, t.get("expected_days", 0), "failed", rec.get("error", "")[:80]])
            continue
        ds_id = index.get(rec["name"]) or rec.get("id")
        doc = json.load(open(os.path.join(DB, f"{ds_id}.json"), encoding="utf-8")) if ds_id else None
        data_rows = doc["rows"] if doc else []
        days = {str(r.get("trading_date")) for r in data_rows}
        hours = (meta.get(contract) or {}).get("trading_hours")
        tz = t.get("tz") or []
        grid_status = "unknown"
        if hours and len(tz) == 2:
            expected = set(window_grid_freq(hours, tz[0], tz[1], t.get("frequency", "1m")))
            per_day = defaultdict(set)
            for r in data_rows:
                m = str(r.get("minute") or "").zfill(6)
                per_day[str(r.get("trading_date"))].add(f"{m[0:2]}:{m[2:4]}")
            gaps = sum(1 for d, got in per_day.items() if expected - got)
            grid_status = "ok" if gaps == 0 else f"gap:{gaps}"
        rows_out.append([product, window, month, contract, rec["name"], len(data_rows), len(days),
                         t.get("expected_days", 0), grid_status, ""])
        a = agg[key]
        a["datasets"] += 1
        a["rows"] += len(data_rows)
        a["days"] += len(days)
        a["expected"] += t.get("expected_days", 0)
        if grid_status != "ok":
            a["gap_ds"] += 1

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "window", "month", "contract", "task", "rows", "trading_days", "expected_days", "grid", "note"])
        w.writerows(rows_out)

    summary = {"by_product": {}, "totals": {"datasets": 0, "rows": 0, "days": 0}}
    for (product, window, month), a in sorted(agg.items()):
        p = summary["by_product"].setdefault(product, {"rows": 0, "datasets": 0, "months": set(), "windows": set(), "gap_ds": 0, "failed": 0})
        p["rows"] += a["rows"]
        p["datasets"] += a["datasets"]
        p["gap_ds"] += a["gap_ds"]
        p["failed"] += a["failed"]
        p["months"].add(month)
        p["windows"].add(window)
        summary["totals"]["datasets"] += a["datasets"]
        summary["totals"]["rows"] += a["rows"]
    for p in summary["by_product"].values():
        p["months"] = sorted(p["months"])
        p["windows"] = sorted(p["windows"])
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
