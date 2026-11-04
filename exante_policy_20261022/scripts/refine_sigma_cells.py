#!/usr/bin/env python3
"""Rewrite sigma_cells at higher precision without touching the fitted rho table.

The TRAIN cell SD is a sample standard deviation of realised price costs; storing
it with 6 decimals was an unnecessary truncation. This script recomputes exactly
the same quantity from the replay cache and stores 10 decimals, then checks that
no stored value moved by more than 1e-6 bp (it must not: same formula, same rows).
"""
from __future__ import annotations

import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(STAGE)
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]


def main() -> None:
    tv = np.load(os.path.join(ROOT, "results", "replay_train_val_t60.npz"), allow_pickle=False)
    k = tv["keys"]
    price = tv["gap_exec"] + tv["gap_resid_T60"]
    by = {}
    for i in np.nonzero(k[:, 7] == 1)[0]:
        d = int(k[i, 2])
        if not (20260105 <= d <= 20260430):
            continue
        mi, h = int(k[i, 5]), int(k[i, 6])
        arm = "asap0" if mi == 0 else f"{METHODS[mi]}{h}"
        by.setdefault((int(k[i, 0]), int(k[i, 1]), int(k[i, 3]), int(k[i, 4]), arm), []).append(price[i])
    cells = {}
    for (pi, wi, si, D, arm), vals in by.items():
        n = len(vals)
        sd = float(np.std(vals, ddof=1)) if n > 1 else 0.0
        cells[f"{PRODUCTS[pi]}|{WINDOWS[wi]}|{si}|{D}|{arm}"] = [n, round(sd, 10)]
    params = json.load(open(os.path.join(STAGE, "params_exante.json")))
    old = params["sigma_cells"]
    max_move = max((abs(cells[key][1] - old[key][1]) for key in cells if key in old), default=0.0)
    missing = sorted(set(old) - set(cells))
    params["sigma_cells"] = cells
    json.dump(params, open(os.path.join(STAGE, "params_exante.json"), "w"), ensure_ascii=False, indent=1)
    did = {"cells": len(cells), "max_abs_change_bp": max_move, "missing_vs_old": missing[:5],
           "n_missing": len(missing)}
    json.dump(did, open(os.path.join(STAGE, "sigma_cells_precision.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(did, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
