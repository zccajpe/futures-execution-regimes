"""Verify one cached minute dataset against its real session grid.

Usage: python3 verify_dataset.py <dataset_id|path> [--hours "HH:MM-HH:MM,..."] \
       [--window "HH:MM,HH:MM"]

Prints a compact JSON summary. Reads rows from disk only.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from session_grid import window_grid  # noqa: E402

DB = os.path.expanduser("~/.dsh-quantstudio/quantskills/database")
META = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "raw", "contract_metadata.json")


def load_dataset(arg: str) -> tuple[str, dict]:
    path = arg if arg.endswith(".json") else os.path.join(DB, f"{arg}.json")
    with open(path, encoding="utf-8") as fh:
        return path, json.load(fh)


def main() -> None:
    arg = sys.argv[1]
    hours = None
    window = None
    for i, a in enumerate(sys.argv):
        if a == "--hours":
            hours = sys.argv[i + 1]
        if a == "--window":
            window = sys.argv[i + 1]
    path, doc = load_dataset(arg)
    rows = doc["rows"]
    params = (doc.get("source") or {}).get("params") or {}
    symbol = params.get("symbol") or (rows[0].get("symbol") if rows else None)
    if hours is None and os.path.exists(META):
        meta = json.load(open(META, encoding="utf-8"))
        hours = (meta.get(symbol) or {}).get("trading_hours")
    if window is None:
        tz = params.get("time_zone")
        if isinstance(tz, (list, tuple)) and len(tz) == 2:
            window = f"{tz[0]},{tz[1]}"
    expected = None
    if hours and window:
        a, b = window.split(",")
        expected = window_grid(hours, a, b)

    per_date: dict[str, list[dict]] = defaultdict(list)
    dup = Counter()
    for r in rows:
        key = (str(r.get("trading_date")), r.get("datetime"))
        dup[key] += 1
        per_date[str(r.get("trading_date"))].append(r)
    duplicates = sum(v - 1 for v in dup.values() if v > 1)
    out = {
        "id": doc.get("id"),
        "symbol": symbol,
        "frequency": params.get("frequency"),
        "rows": len(rows),
        "rowCount": doc.get("rowCount"),
        "trading_days": len(per_date),
        "first_datetime": rows[0]["datetime"] if rows else None,
        "last_datetime": rows[-1]["datetime"] if rows else None,
        "duplicates": duplicates,
        "expected_per_day": len(expected) if expected else None,
    }
    if expected:
        exp = set(expected)
        rows_per_day = sorted({len(v) for v in per_date.values()})
        missing_days = 0
        missing_bars = 0
        extra_bars = 0
        for day, rs in per_date.items():
            got = {str(r.get("minute") or r["datetime"][11:16]).zfill(6) for r in rs}
            got = {f"{g[0:2]}:{g[2:4]}" for g in got}
            miss = exp - got
            extra = got - exp
            if miss:
                missing_days += 1
                missing_bars += len(miss)
            extra_bars += len(extra)
        out.update({
            "rows_per_day": rows_per_day,
            "days_missing_any_bar": missing_days,
            "missing_bars_total": missing_bars,
            "bars_outside_session": extra_bars,
        })
    vols = [r.get("volume") for r in rows if r.get("volume") is not None]
    if vols:
        out["zero_volume_share"] = round(sum(1 for v in vols if not v) / len(vols), 4)
    if "amount" in (doc.get("columns") or []) and "close" in (doc.get("columns") or []):
        ratios = [r["amount"] / (r["volume"] * r["close"]) for r in rows if r.get("volume") and r.get("close")]
        if ratios:
            ratios.sort()
            out["amount_over_vol_price_median"] = round(ratios[len(ratios) // 2], 4)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
