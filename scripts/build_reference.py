"""Build the contract/date reference from the daily dominant map.

Tradable contract for date d = dominant contract of the PREVIOUS trading day
(published before d opens). The dominant of d itself is never used to trade d.

Writes:
  raw/reference/dominant_map.csv   product,date,dominant,tradable,roll_flag
  raw/reference/contracts.json     unique tradable contracts with first/last date
Prints a compact summary.
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.expanduser("~/.dsh-quantstudio/quantskills/database")
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]


def load_dom(product: str) -> dict[str, str]:
    path = os.path.join(ROOT, "raw", "datasets", "index.json")
    index = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    ds_id = index.get(f"dom_{product}_20251001_20261020")
    if not ds_id:
        raise SystemExit(f"missing dominant dataset id for {product}")
    doc = json.load(open(os.path.join(DB, f"{ds_id}.json"), encoding="utf-8"))
    out = {}
    for row in doc["rows"]:
        out[str(row["date"])] = row["symbol"]
    return out


def main() -> None:
    index_path = os.path.join(ROOT, "raw", "datasets", "index.json")
    index = json.load(open(index_path, encoding="utf-8")) if os.path.exists(index_path) else {}
    rows = []
    contracts: dict[str, dict] = {}
    summary = {}
    for product in PRODUCTS:
        dom = load_dom(product)
        dates = sorted(dom)
        rolls = 0
        prev = None
        prev_tradable = None
        for i, d in enumerate(dates):
            tradable = dom[dates[i - 1]] if i else None
            # roll_flag must be knowable at release: compare the CONTRACT you could
            # trade today with the one you could trade yesterday (both lagged).
            roll = tradable is not None and prev_tradable is not None and tradable != prev_tradable
            ex_post = prev is not None and dom[d] != prev
            if roll:
                rolls += 1
            prev = dom[d]
            prev_tradable = tradable
            rows.append([product, d, dom[d], tradable or "", "1" if roll else "0", "1" if ex_post else "0"])
            if tradable:
                c = contracts.setdefault(tradable, {"product": product, "first": d, "last": d})
                c["last"] = d
        summary[product] = {"dates": len(dates), "first": dates[0], "last": dates[-1],
                            "rolls": rolls, "contracts": sorted({v for v in dom.values()})}
    outdir = os.path.join(ROOT, "raw", "reference")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "dominant_map.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "date", "dominant", "tradable", "roll_flag", "ex_post_roll"])
        w.writerows(rows)
    with open(os.path.join(outdir, "contracts.json"), "w", encoding="utf-8") as fh:
        json.dump(contracts, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"products": {k: {"dates": v["dates"], "rolls": v["rolls"],
                                       "first": v["first"], "last": v["last"],
                                       "n_contracts": len(v["contracts"])} for k, v in summary.items()},
                      "tradable_contracts": len(contracts)}, ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main()
