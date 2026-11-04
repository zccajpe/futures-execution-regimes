"""Summarize selector decisions and run targeted verification.

Outputs results/selector_summary_v4.json and results/verification_v4.json
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

ROOT = E.ROOT
RES = os.path.join(ROOT, "results")
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]


def load_rows():
    with open(os.path.join(RES, "selector_decisions_val.csv"), encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def summarize(rows):
    out = {}
    groups = defaultdict(list)
    for r in rows:
        groups[(r["a_bp"], r["phi"], r["preference"], r["budget_k"])].append(r)
    for key, rs in sorted(groups.items()):
        ok = [r for r in rs if r["status"] == "ok"]
        inf = [r for r in rs if r["status"] != "ok"]
        reasons = Counter(r["status"] for r in inf)
        entry = {"queries": len(rs), "recommended": len(ok),
                 "coverage": round(len(ok) / len(rs), 4) if rs else None,
                 "infeasible_reasons": dict(reasons)}
        if ok:
            for side in ("buy", "sell"):
                sub = [r for r in ok if r["side"] == side]
                if not sub:
                    continue
                entry[side] = {
                    "n": len(sub),
                    "days": len({r["date"] for r in sub}),
                    "sel_cost": round(float(np.mean([num(r["real_cost_bp"]) for r in sub])), 4),
                    "asap_cost": round(float(np.mean([num(r["asap_cost_bp"]) for r in sub])), 4),
                    "twap_cost": round(float(np.nanmean([num(r["fixed_twap_cost_bp"]) for r in sub])), 4),
                    "shape_cost": round(float(np.nanmean([num(r["fixed_shape_cost_bp"]) for r in sub])), 4),
                    "oracle_cost": round(float(np.nanmean([num(r["oracle_cost_bp"]) for r in sub])), 4),
                    "completion": round(float(np.mean([num(r["real_completion"]) for r in sub])), 4),
                    "asap_completion": round(float(np.mean([num(r["asap_completion"]) for r in sub])), 4),
                    "resid": round(float(np.mean([num(r["real_resid_lots"]) for r in sub])), 2),
                    "share_changed_arm": round(float(np.mean([1.0 if r["recommended_method"] != "asap" else 0.0 for r in sub])), 4),
                    "share_changed_path": round(float(np.nanmean([num(r["path_changed_vs_asap"]) for r in sub])), 4),
                }
        out[f"a{key[0]}_phi{key[1]}_{key[2]}_k{key[3]}"] = entry
    return out


def verify(rows):
    out = {}
    # 1) Q consistency between scenario and replay for every recommended row
    ok = [r for r in rows if r["status"] == "ok"]
    mism = sum(1 for r in ok if r["Q_pred"] != r["Q_replay"])
    out["q_consistency"] = {"checked": len(ok), "mismatches": mism}
    # 2) a0 vs a2 difference equals 2 * L (same case, same arm)
    by_key = defaultdict(dict)
    for r in ok:
        key = (r["product"], r["window"], r["date"], r["size_idx"], r["D"], r["side"], r["preference"])
        by_key[key][r["a_bp"]] = r
    deltas = []
    for key, d in by_key.items():
        if "0.0" in d and "2.0" in d and d["0.0"]["status"] == "ok" and d["2.0"]["status"] == "ok":
            if (d["0.0"]["recommended_method"], d["0.0"]["recommended_h"]) != (
                    d["2.0"]["recommended_method"], d["2.0"]["recommended_h"]):
                continue
            c0, c2 = num(d["0.0"]["real_cost_bp"]), num(d["2.0"]["real_cost_bp"])
            L = num(d["2.0"]["real_impact_bp"]) / 2.0
            deltas.append(abs((c2 - c0) - 2.0 * L))
    out["impact_linearity"] = {"cases": len(deltas),
                              "max_abs_error": round(float(np.max(deltas)), 6) if deltas else None}
    # 3) path change distribution
    pc = [num(r["path_changed_vs_asap"]) for r in ok if r["path_changed_vs_asap"] != ""]
    out["path_change"] = {"n": len(pc), "share_changed": round(float(np.mean(pc)), 4) if pc else None}
    # 4) per-day variation of recommended h (must not be one fixed arm for the whole period)
    hs = defaultdict(set)
    for r in ok:
        hs[(r["product"], r["window"], r["size_idx"], r["D"], r["side"], r["preference"])].add(r["recommended_h"])
    multi = sum(1 for v in hs.values() if len(v) > 1)
    out["per_day_variation"] = {"cells": len(hs), "cells_with_multiple_h": multi}
    return out


def main() -> None:
    rows = load_rows()
    summary = summarize(rows)
    ver = verify(rows)
    with open(os.path.join(RES, "selector_summary_v4.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(RES, "verification_v4.json"), "w", encoding="utf-8") as fh:
        json.dump(ver, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"configs": len(summary), "verification": ver}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
