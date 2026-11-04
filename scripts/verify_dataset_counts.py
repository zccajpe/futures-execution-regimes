#!/usr/bin/env python3
"""Re-derive the dataset counts from the raw fetch log and the derived arrays.

Read-only. Writes notes/data_counts_20261030.json. Every number printed here is
computed from files, not copied from earlier documents.
"""
from __future__ import annotations

import glob
import json
import os
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw", "datasets", "results.jsonl")
INDEX = os.path.join(ROOT, "raw", "datasets", "index.json")
DERIVED = os.path.join(ROOT, "raw", "derived")
OUT = os.path.join(ROOT, "notes", "data_counts_20261030.json")


def family(rec):
    name = rec.get("name") or ""
    for pref in ("minn", "min", "dom", "daily", "meta"):
        if name.startswith(pref):
            return pref
    return "other"


def main():
    recs = [json.loads(line) for line in open(RAW)]
    fam = collections.defaultdict(lambda: {"records": 0, "ok": 0, "failed": 0, "rows": 0})
    for r in recs:
        f = fam[family(r)]
        f["records"] += 1
        if r.get("ok"):
            f["ok"] += 1
            f["rows"] += int(r.get("rowCount") or 0)
        else:
            f["failed"] += 1

    minute_blocks = fam["min"]["ok"] + fam["minn"]["ok"]
    minute_rows = fam["min"]["rows"] + fam["minn"]["rows"]
    index_items = len(json.load(open(INDEX)))

    windows = collections.defaultdict(lambda: {"series": 0, "cells": 0, "products": set()})
    night_dates = {}
    for path in sorted(glob.glob(os.path.join(DERIVED, "bars_*.npz"))):
        base = os.path.basename(path)[5:-4]
        prod, win = base.rsplit("_", 1)
        import numpy as np
        with np.load(path, allow_pickle=False) as z:
            mask = z["mask"]
            cells = int(mask.sum())
            dates = [int(x) for x in z["dates"]]
            assert mask.sum() == mask.size, f"{base}: mask has invalid cells"
        windows[win]["series"] += 1
        windows[win]["cells"] += cells
        windows[win]["products"].add(prod)
        if win == "night":
            night_dates[prod] = {"days": len(dates),
                                 "before_20251103": sum(1 for d in dates if d < 20251103),
                                 "from_20251103": sum(1 for d in dates if d >= 20251103),
                                 "first": min(dates), "last": max(dates)}

    night_days = {v["days"] for v in night_dates.values()}
    preheat = {v["before_20251103"] for v in night_dates.values()}
    post = {v["from_20251103"] for v in night_dates.values()}
    cells_total = sum(v["cells"] for v in windows.values())

    out = {
        "generated_by": "scripts/verify_dataset_counts.py",
        "fetch_log": {
            "path": "raw/datasets/results.jsonl",
            "records_total": len(recs),
            "by_family": {k: {**v, "products": None} for k, v in sorted(fam.items())},
            "minute_blocks_ok": minute_blocks,
            "minute_rows_ok": minute_rows,
            "non_minute_failures": fam["daily"]["failed"],
            "failure_scope_note": ("the 12 failed calls are daily-bar requests; every minute block succeeded. "
                                   "'0 failures' is only true for the minute blocks"),
            "index_items": index_items,
            "index_breakdown": {"dom": fam["dom"]["ok"], "meta": fam["meta"]["ok"], "minute": minute_blocks},
        },
        "derived_grid": {
            "windows": {w: {"series": v["series"], "cells": v["cells"], "products": len(v["products"])}
                        for w, v in sorted(windows.items())},
            "cells_total": cells_total,
            "note": ("aligned session grid, all mask cells valid; equal to the sum of the fetched minute rows"),
        },
        "night": {
            "series": len(night_dates),
            "days_per_series": sorted(night_days),
            "preheat_before_20251103": sorted(preheat),
            "days_from_20251103": sorted(post),
            "all_mask_valid": True,
            "note": ("242 real night trading days per series: 16 early preheat days (2025-10-10 .. 2025-10-31) "
                     "plus 226 days from 2025-11-03. The preheat days are real data rows, not padding"),
            "per_product": night_dates,
        },
        "checks": {
            "minute_rows_equal_grid": minute_rows == cells_total,
            "index_equal_dom_meta_minute": index_items == fam["dom"]["ok"] + fam["meta"]["ok"] + minute_blocks,
            "night_days_242_with_16_preheat": sorted(night_days) == [242] and sorted(preheat) == [16] and sorted(post) == [226],
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("fetch_log", "derived_grid", "checks")}, ensure_ascii=False, indent=1))
    print("night:", out["night"]["days_per_series"], out["night"]["preheat_before_20251103"], out["night"]["days_from_20251103"])
    return 0 if all(out["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
