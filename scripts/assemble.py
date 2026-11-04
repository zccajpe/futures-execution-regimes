"""Assemble minute blocks into per (product, window) aligned float64 arrays (v3).

Main price proxy is the bar CLOSE for every product. The amount-derived VWAP is
stored as `vwap_raw` with its own `vwap_valid` flag and is used only in a
sensitivity run on cases whose whole window is VWAP-consistent.

Stored per series: dates, labels, contracts, volume, close, high, low,
vwap_raw, vwap_valid, mask.  mask = bar present and price/volume legal;
it is independent of vwap_valid.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_grid import window_grid  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.expanduser("~/.dsh-quantstudio/quantskills/database")
WINDOWS = {"am": ("09:00", "11:30"), "pm": ("13:00", "15:15"),
           "night1h": ("21:00", "23:00"), "night2": ("22:01", "23:00")}
STORE = {"am": "am", "pm": "pm", "night1h": "night", "night2": "night"}
CANON = {"am": ("09:00", "11:30"), "pm": ("13:00", "15:15"), "night": ("21:00", "23:00")}
TOL = 1e-4


def load_tasks() -> dict:
    tasks = {}
    for name in ("tasks_min_q1.json", "tasks_min_q2.json", "tasks_min_q3.json",
                 "tasks_night2.json", "tasks_night_oct.json"):
        path = os.path.join(ROOT, "raw", "tasks", name)
        if os.path.exists(path):
            for t in json.load(open(path, encoding="utf-8")):
                tasks[t["name"]] = t
    return tasks


def main() -> None:
    meta = json.load(open(os.path.join(ROOT, "raw", "contract_metadata.json"), encoding="utf-8"))
    index = json.load(open(os.path.join(ROOT, "raw", "datasets", "index.json"), encoding="utf-8"))
    results = [json.loads(l) for l in open(os.path.join(ROOT, "raw", "datasets", "results.jsonl"), encoding="utf-8") if l.strip()]
    tasks = load_tasks()
    tradable = {}
    with open(os.path.join(ROOT, "raw", "reference", "dominant_map.csv"), encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["tradable"]:
                tradable[(row["product"], row["date"])] = row["tradable"]

    seen: dict[tuple, dict] = {}
    conflict_examples = []
    stats = defaultdict(int)
    cells = defaultdict(lambda: defaultdict(dict))
    contracts_by_date = defaultdict(dict)
    grid_len = {}
    for rec in results:
        t = tasks.get(rec["name"])
        if not t or not rec.get("ok"):
            continue
        product, contract, window = t["product"], t["contract"], t["window"]
        if window not in WINDOWS:
            continue
        key_window = STORE[window]
        hours = (meta.get(contract) or {}).get("trading_hours")
        if not hours:
            continue
        mult = float((meta.get(contract) or {}).get("contract_multiplier") or 1.0)
        grid = window_grid(hours, *CANON[key_window])
        pos = {lab: i for i, lab in enumerate(grid)}
        grid_len[(product, key_window)] = max(grid_len.get((product, key_window), 0), len(grid))
        doc = json.load(open(os.path.join(DB, f"{rec['id']}.json"), encoding="utf-8"))
        for r in doc["rows"]:
            date = str(r["trading_date"])
            if tradable.get((product, date)) != contract:
                stats["rows_from_non_tradable_contract"] += 1
                continue
            key = (r.get("symbol"), date, r["datetime"])
            prev = seen.get(key)
            if prev is not None:
                stats["duplicate_rows"] += 1
                if any(prev.get(f) != r.get(f) for f in ("open", "high", "low", "close", "volume", "amount")):
                    stats["conflicting_duplicates"] += 1
                    if len(conflict_examples) < 5:
                        conflict_examples.append({"key": list(key),
                                                  "a": {k: prev.get(k) for k in ("volume", "amount", "close")},
                                                  "b": {k: r.get(k) for k in ("volume", "amount", "close")}})
                continue
            seen[key] = r
            lab = f"{r['datetime'][11:13]}:{r['datetime'][14:16]}"
            if lab not in pos:
                stats["rows_outside_grid"] += 1
                continue
            vol = float(r.get("volume") or 0.0)
            amt = float(r.get("amount") or 0.0)
            close = float(r.get("close") or 0.0)
            high = float(r.get("high") or 0.0)
            low = float(r.get("low") or 0.0)
            if vol < 0 or amt < 0:
                stats["negative_volume_or_amount"] += 1
                continue
            vwap_raw = amt / (vol * mult) if vol > 0 else close
            legal = close > 0 and low * (1 - TOL) <= close <= high * (1 + TOL) and vol >= 0
            vv = bool(legal and (vol == 0 or (low * (1 - TOL) <= vwap_raw <= high * (1 + TOL))))
            if legal and not vv:
                stats["vwap_invalid_bars"] += 1
            if not legal:
                stats["illegal_bars"] += 1
            cells[(product, key_window)][date][pos[lab]] = (vol, close, high, low, vwap_raw, vv, legal)
            contracts_by_date[(product, key_window)][date] = contract
            stats["rows_used"] += 1

    outdir = os.path.join(ROOT, "raw", "derived")
    os.makedirs(outdir, exist_ok=True)
    summary = {}
    for (product, window), by_date in sorted(cells.items()):
        dates = sorted(by_date)
        nb = grid_len.get((product, window), 0)
        if not dates or nb == 0:
            continue
        arrays = {k: np.zeros((len(dates), nb), dtype=np.float64)
                  for k in ("volume", "close", "high", "low", "vwap_raw")}
        mask = np.zeros((len(dates), nb), dtype=bool)
        vwap_valid = np.zeros((len(dates), nb), dtype=bool)
        for k, d in enumerate(dates):
            for i, (v, c, hi, lo, vw, vv, legal) in by_date[d].items():
                if i >= nb:
                    continue
                arrays["volume"][k, i] = v
                arrays["close"][k, i] = c
                arrays["high"][k, i] = hi
                arrays["low"][k, i] = lo
                arrays["vwap_raw"][k, i] = vw
                mask[k, i] = bool(legal)
                vwap_valid[k, i] = bool(vv)
        hours_ref = next((meta.get(t["contract"]) or {}).get("trading_hours") for t in tasks.values()
                         if t["product"] == product and STORE.get(t["window"]) == window
                         and (meta.get(t["contract"]) or {}).get("trading_hours"))
        labels = window_grid(hours_ref, *CANON[window])
        np.savez_compressed(
            os.path.join(outdir, f"bars_{product}_{window}.npz"),
            dates=np.array(dates), labels=np.array(labels[:nb]),
            contracts=np.array([contracts_by_date[(product, window)].get(d, "") for d in dates]),
            mask=mask, vwap_valid=vwap_valid, **arrays)
        summary[f"{product}_{window}"] = {
            "days": len(dates), "bars": nb,
            "missing_cells": int((~mask).sum()),
            "vwap_invalid_cells": int((mask & ~vwap_valid).sum()),
            "zero_volume_cells": int(((arrays["volume"] == 0) & mask).sum()),
            "first": dates[0], "last": dates[-1],
        }
    with open(os.path.join(outdir, "assembly.json"), "w", encoding="utf-8") as fh:
        json.dump({"series": summary, "checks": dict(stats), "conflict_examples": conflict_examples},
                  fh, ensure_ascii=False, indent=1)
    print(json.dumps({"series": len(summary), "checks": dict(stats),
                      "missing_cells": sum(v["missing_cells"] for v in summary.values()),
                      "vwap_invalid_cells": sum(v["vwap_invalid_cells"] for v in summary.values())},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
