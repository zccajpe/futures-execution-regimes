#!/usr/bin/env python3
"""Diagnose the budget_bp storage-rounding issue and the absolute tolerance.

The frozen table stores budget_bp with 4 decimals; the TRAIN cell SD is full
precision. This script quantifies, for the buy / a=0.5 / k=1 cells, how many
cells would be excluded by a strict `risk > budget` test purely because of that
rounding, and checks the two counterexamples reported by the coordinator.
"""
from __future__ import annotations

import csv
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(STAGE)
TOL = 5e-5
FTOL = 1e-9


def main() -> None:
    params = json.load(open(os.path.join(STAGE, "params_exante.json")))
    cells = params["sigma_cells"]
    frozen = {}
    with open(os.path.join(ROOT, "decision_research", "v2", "frozen_A_params.csv")) as fh:
        for r in csv.DictReader(fh):
            frozen[(r["product"], r["window"], int(r["size_idx"]), int(r["D"]), r["side"],
                    float(r["a_bp"]), float(r["risk_k"]))] = float(r["budget_bp"])
    rows = []
    for (prod, win, size, D, side, a, k), budget in frozen.items():
        if side != "buy" or a != 0.5 or k != 1.0:
            continue
        cell = cells.get(f"{prod}|{win}|{size}|{D}|asap0")
        if not cell:
            continue
        n, raw = cell
        rows.append({"product": prod, "window": win, "size": size, "D": D,
                     "budget_bp": budget, "raw_sd_asap": raw,
                     "raw_minus_budget": raw - budget})
    over = [r for r in rows if r["raw_minus_budget"] > 0]
    within = [r for r in over if r["raw_minus_budget"] <= TOL + FTOL]
    beyond = [r for r in over if r["raw_minus_budget"] > TOL + FTOL]
    examples = {f"{r['product']}/{r['window']}/size{r['size']}/D{r['D']}": r for r in rows
                if (r["product"], r["window"], r["size"], r["D"]) in
                {("RB", "am", 0, 15), ("RB", "am", 1, 15)}}
    out = {
        "cells_checked": len(rows),
        "asap_raw_sd_above_stored_budget": len(over),
        "within_tolerance": len(within),
        "beyond_tolerance": len(beyond),
        "tolerance_bp": TOL,
        "note": ("the strict test `risk > budget` would exclude the ASAP arm in "
                 f"{len(over)} of {len(rows)} buy/a=0.5/k=1 cells purely from 4-decimal storage"),
        "examples": examples,
        "beyond_tolerance_examples": beyond[:5],
    }
    json.dump(out, open(os.path.join(STAGE, "budget_tolerance_diagnostic.json"), "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
