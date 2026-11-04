"""Generate 1-minute fetch tasks by (product, tradable contract, month, window).

Contract selection: previous trading day's dominant (never the same day's).
Windows come from each contract's real trading_hours, so CFFEX/treasury products
never request commodity sessions and night tasks exist only for night products.

usage: python3 make_minute_tasks.py 202511 202603 --out /tmp/tasks_min.json
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_map() -> dict[str, dict[str, tuple[str, str]]]:
    """product -> date -> (dominant, tradable)."""
    out: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    path = os.path.join(ROOT, "raw", "reference", "dominant_map.csv")
    with open(path, encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            product, date, dominant, tradable, _roll = line.rstrip("\n").split(",")
            if tradable:
                out[product][date] = (dominant, tradable)
    return out


def windows(hours: str) -> list[tuple[str, list[str]]]:
    has_night = hours.split(",")[0].startswith("21") or hours.split(",")[0].startswith("20")
    last = hours.split(",")[-1].split("-")[1]
    out = [("am", ["09:00", "11:30"]), ("pm", ["13:00", last])]
    if has_night:
        out.append(("night1h", ["21:00", "22:00"]))
    return out


def main() -> None:
    start_month, end_month = sys.argv[1], sys.argv[2]
    out_path = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "/tmp/tasks_min.json"
    meta = json.load(open(os.path.join(ROOT, "raw", "contract_metadata.json"), encoding="utf-8"))
    dmap = load_map()
    tasks = []
    seen = set()
    for product, dates in sorted(dmap.items()):
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for date in sorted(dates):
            month = date[:6]
            if not (start_month <= month <= end_month):
                continue
            _dom, tradable = dates[date]
            groups[(tradable, month)].append(date)
        for (contract, month), days in sorted(groups.items()):
            hours = (meta.get(contract) or {}).get("trading_hours")
            if not hours:
                continue
            code = contract.split(".")[0]
            for label, tz in windows(hours):
                name = f"min_{product}_{code}_{label}_{days[0]}_{days[-1]}"
                if name in seen:
                    continue
                seen.add(name)
                tasks.append({
                    "name": name, "product": product, "contract": contract, "window": label,
                    "symbol": contract, "start": days[0], "end": days[-1], "tz": tz,
                    "expected_days": len(days),
                })
    json.dump(tasks, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    by_window = defaultdict(int)
    for t in tasks:
        by_window[t["window"]] += 1
    print(json.dumps({"tasks": len(tasks), "by_window": dict(by_window),
                      "products": len({t['product'] for t in tasks})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
