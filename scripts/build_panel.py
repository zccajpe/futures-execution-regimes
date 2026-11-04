"""Build the research panel from cached minute datasets.

Outputs (workspace, not the platform DB):
  raw/reference/daily_panel.csv    per product x trading_date x window summary
  raw/reference/minute_shapes.csv  per product x window x minute_index volume share
                                   (full-sample mean, for inspection only; the
                                   execution model must use rolling history)

The panel is the compact table all later steps read; raw minute bars stay in the
platform database and are read by id.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.expanduser("~/.dsh-quantstudio/quantskills/database")


def load_tasks() -> dict[str, dict]:
    tasks = {}
    for path in ("/tmp/tasks_min_q1.json", "/tmp/tasks_min_q2.json", "/tmp/tasks_min_q3.json"):
        if os.path.exists(path):
            for t in json.load(open(path, encoding="utf-8")):
                tasks[t["name"]] = t
    return tasks


def main() -> None:
    index = json.load(open(os.path.join(ROOT, "raw", "datasets", "index.json"), encoding="utf-8"))
    meta = json.load(open(os.path.join(ROOT, "raw", "contract_metadata.json"), encoding="utf-8"))
    tasks = load_tasks()
    results = [json.loads(line) for line in open(os.path.join(ROOT, "raw", "datasets", "results.jsonl"), encoding="utf-8") if line.strip()]

    panel_rows = []
    shares = defaultdict(lambda: defaultdict(list))
    seen = set()
    for rec in results:
        t = tasks.get(rec["name"])
        if not t or not rec.get("ok"):
            continue
        key = (t["product"], t["contract"], t["window"], t["start"], t["end"])
        if key in seen:
            continue
        seen.add(key)
        doc = json.load(open(os.path.join(DB, f"{rec['id']}.json"), encoding="utf-8"))
        rows = doc["rows"]
        mult = (meta.get(t["contract"]) or {}).get("contract_multiplier") or 1
        by_date = defaultdict(list)
        for r in rows:
            by_date[str(r.get("trading_date"))].append(r)
        for date, rs in sorted(by_date.items()):
            rs.sort(key=lambda r: r["datetime"])
            vol = sum(r.get("volume") or 0 for r in rs)
            amt = sum(r.get("amount") or 0 for r in rs)
            closes = [r["close"] for r in rs if r.get("close") is not None]
            rv = None
            if len(closes) > 2:
                rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1]]
                rv = math.sqrt(sum(x * x for x in rets)) * 1e4
            zero = sum(1 for r in rs if not r.get("volume")) / len(rs) if rs else None
            panel_rows.append([
                t["product"], date, t["contract"], t["window"], len(rs), vol,
                round(amt, 1), round(amt / (vol * mult), 6) if vol else "",
                closes[0] if closes else "", closes[-1] if closes else "",
                round(rv, 4) if rv is not None else "", round(zero, 4) if zero is not None else "",
            ])
            total = vol or 1
            for i, r in enumerate(rs):
                shares[(t["product"], t["window"])][i].append((r.get("volume") or 0) / total)

    outdir = os.path.join(ROOT, "raw", "reference")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "daily_panel.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "date", "contract", "window", "bars", "volume", "amount", "vwap",
                    "close_first", "close_last", "rv_bp", "zero_share"])
        w.writerows(panel_rows)
    with open(os.path.join(outdir, "minute_shapes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "window", "minute_index", "days", "mean_share"])
        for (product, window), by_idx in sorted(shares.items()):
            for i in sorted(by_idx):
                vals = by_idx[i]
                w.writerow([product, window, i, len(vals), round(sum(vals) / len(vals), 6)])
    print(json.dumps({"panel_rows": len(panel_rows), "shape_cells": len(shares)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
