"""Inventory cached PandaData datasets and check minute blocks against real grids.

Checks are grid-based: the expected bar-end labels come from each contract's
`trading_hours` metadata, so a missing bar is detected even if every day misses
the same one. Datasets whose contract/time window cannot be resolved are marked
`grid_check: unknown` instead of being reported as complete.

Outputs:
  raw/probe_index.csv      one row per cached dataset
  notes/data_checks.json   per-dataset structure checks
Prints a compact summary only.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_grid import window_grid_freq  # noqa: E402

DB = os.path.expanduser("~/.dsh-quantstudio/quantskills/database")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META_PATH = os.path.join(ROOT, "raw", "contract_metadata.json")


def load_meta() -> dict:
    if os.path.exists(META_PATH):
        return json.load(open(META_PATH, encoding="utf-8"))
    return {}


def label_of(row: dict) -> str | None:
    minute = row.get("minute")
    if minute:
        s = str(minute).zfill(6)
        return f"{s[0:2]}:{s[2:4]}"
    dt = row.get("datetime")
    if isinstance(dt, str) and len(dt) >= 16:
        return dt[11:16]
    return None


def check_minute(rows: list[dict], params: dict, meta: dict) -> dict:
    symbol = params.get("symbol") or (rows[0].get("symbol") if rows else None)
    hours = (meta.get(symbol) or {}).get("trading_hours")
    tz = params.get("time_zone")
    window = None
    if isinstance(tz, (list, tuple)) and len(tz) == 2:
        window = (str(tz[0]), str(tz[1]))
    elif isinstance(tz, str) and len(tz) > 5:
        try:
            a, b = json.loads(tz.replace("'", '"'))
            window = (str(a), str(b))
        except Exception:
            window = None

    freq = str(params.get("frequency") or "1m")
    expected = None
    if hours and window:
        expected = window_grid_freq(hours, window[0], window[1], freq)

    per_date: dict[str, set[str]] = defaultdict(set)
    dup = Counter()
    for r in rows:
        date = str(r.get("trading_date") or (r.get("datetime") or "")[:10].replace("-", ""))
        lab = label_of(r)
        per_date[date].add(lab)
        dup[(date, lab)] += 1

    out = {
        "symbol": symbol,
        "rows": len(rows),
        "trading_days": len(per_date),
        "duplicates": sum(v - 1 for v in dup.values() if v > 1),
        "first_datetime": rows[0].get("datetime") if rows else None,
        "last_datetime": rows[-1].get("datetime") if rows else None,
    }
    if expected:
        exp = set(expected)
        out["expected_per_day"] = len(exp)
        missing_days = 0
        missing_bars = 0
        extra_bars = 0
        for date, got in per_date.items():
            miss = exp - got
            if miss:
                missing_days += 1
                missing_bars += len(miss)
            extra_bars += len(got - exp)
        out.update({"grid_check": "ok" if not missing_bars and not extra_bars else "gap",
                    "days_missing_any_bar": missing_days,
                    "missing_bars_total": missing_bars,
                    "bars_outside_session": extra_bars,
                    "rows_per_day": sorted({len(v) for v in per_date.values()})})
    else:
        out["grid_check"] = "unknown"
    vols = [r.get("volume") for r in rows if r.get("volume") is not None]
    if vols:
        out["zero_volume_share"] = round(sum(1 for v in vols if not v) / len(vols), 4)
    return out


def main() -> None:
    meta = load_meta()
    index_rows = []
    checks = {}
    for path in sorted(glob.glob(os.path.join(DB, "*.json"))):
        try:
            doc = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        src = doc.get("source") or {}
        method = src.get("method") or doc.get("name") or "?"
        params = src.get("params") or {}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        rows = doc.get("rows") or []
        rec = {
            "id": doc.get("id") or os.path.basename(path).replace(".json", ""),
            "method": method,
            "rowCount": doc.get("rowCount", len(rows)),
            "bytes": os.path.getsize(path),
            "fetchedAt": doc.get("fetchedAt", ""),
            "params": json.dumps(params, ensure_ascii=False, sort_keys=True)[:400],
        }
        index_rows.append(rec)
        if method == "get_future_min" and rows:
            checks[rec["id"]] = check_minute(rows, params, meta)

    os.makedirs(os.path.join(ROOT, "raw"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "notes"), exist_ok=True)
    with open(os.path.join(ROOT, "raw", "probe_index.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "method", "rowCount", "bytes", "fetchedAt", "params"])
        w.writeheader()
        for r in sorted(index_rows, key=lambda x: x["fetchedAt"]):
            w.writerow(r)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_datasets": len(index_rows),
        "by_method": Counter(r["method"] for r in index_rows).most_common(),
        "minute_checks": checks,
    }
    with open(os.path.join(ROOT, "notes", "data_checks.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)

    ok = sum(1 for v in checks.values() if v.get("grid_check") == "ok")
    gap = sum(1 for v in checks.values() if v.get("grid_check") == "gap")
    unknown = sum(1 for v in checks.values() if v.get("grid_check") == "unknown")
    print(json.dumps({"datasets": len(index_rows), "minute_blocks": len(checks),
                      "grid_ok": ok, "grid_gap": gap, "grid_unknown": unknown},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
