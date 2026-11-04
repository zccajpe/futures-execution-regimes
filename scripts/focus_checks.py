"""Focused checks: baseline comparison, per-case deadlines, state buckets, re-derivation.

Outputs results/focus_checks.json plus CSVs:
  deadline_distribution.csv  (per query: shortest D reaching 95% scenario completion)
  state_buckets_val.csv      (per product/window/date state bucket with frozen train bounds)
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
TRAIN = (20260105, 20260430)
VAL = (20260506, 20260630)


def load_npz(path):
    with np.load(path, allow_pickle=False) as z:
        return {k: np.array(z[k], copy=True) for k in z.files}


def baseline_all_queries():
    rows = list(csv.DictReader(open(os.path.join(RES, "selector_decisions_val.csv"), encoding="utf-8")))
    out = {}
    for key in sorted({(r["a_bp"], r["phi"], r["preference"], r["budget_k"]) for r in rows}):
        sel = [r for r in rows if (r["a_bp"], r["phi"], r["preference"], r["budget_k"]) == key]
        for side in ("buy", "sell"):
            sub = [r for r in sel if r["side"] == side]
            if not sub:
                continue
            def m(col):
                vals = [float(r[col]) for r in sub if r[col] not in ("", None)]
                return round(float(np.mean(vals)), 4) if vals else None
            out[f"a{key[0]}_phi{key[1]}_{key[2]}_k{key[3]}_{side}"] = {
                "n": len(sub),
                "asap": m("asap_cost_bp"), "twap": m("fixed_twap_cost_bp"),
                "shape": m("fixed_shape_cost_bp"), "selector": m("real_cost_bp"),
                "coverage": round(sum(1 for r in sub if r["status"] == "ok") / len(sub), 4),
                "asap_completion": m("asap_completion"), "selector_completion": m("real_completion"),
            }
    return out


def deadline_distribution():
    sc = load_npz(os.path.join(RES, "scenarios_train_val.npz"))
    k = sc["keys"]
    best = {}
    for i in range(len(k)):
        key = (int(k[i, 0]), int(k[i, 1]), int(k[i, 2]), int(k[i, 3]))
        D = int(k[i, 4])
        cur = best.get(key)
        if cur is None or sc["pred_comp"][i] > cur[1]:
            best[key] = (D, float(sc["pred_comp"][i]))
    by_day = defaultdict(dict)
    for (pi, wi, date, size), (D, comp) in best.items():
        by_day[(pi, wi, date, size)][D] = comp
    rows = []
    dist = Counter()
    for (pi, wi, date, size), dd in sorted(by_day.items()):
        shortest = next((D for D in (5, 15, 30, 60) if dd.get(D, 0) >= 0.95), None)
        rows.append([PRODUCTS[pi], WINDOWS[wi], date, size,
                     round(dd.get(5, float("nan")), 3), round(dd.get(15, float("nan")), 3),
                     round(dd.get(30, float("nan")), 3), round(dd.get(60, float("nan")), 3),
                     shortest if shortest else "none_up_to_60"])
        dist[shortest if shortest else "none"] += 1
    with open(os.path.join(RES, "deadline_distribution.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "window", "date", "size_idx", "best_comp_D5", "best_comp_D15",
                    "best_comp_D30", "best_comp_D60", "shortest_D_95"])
        w.writerows(rows)
    return {"queries": len(rows), "distribution": {str(k): v for k, v in dist.items()}}


def state_buckets():
    rp = load_npz(os.path.join(RES, "replay_train_val.npz"))
    k = rp["keys"]
    # unique case-level state
    uid = (k[:, 0].astype(np.int64) * 100 + k[:, 1]) * 100000000 + k[:, 2]
    order = np.argsort(uid)
    u, first = np.unique(uid[order], return_index=True)
    idx = order[first]
    st = {"uid": u, "pi": k[idx, 0], "wi": k[idx, 1], "date": k[idx, 2],
          "V": rp["V_ref"][idx], "S": rp["sigma1m"][idx]}
    train = (st["date"] >= TRAIN[0]) & (st["date"] <= TRAIN[1])
    val = (st["date"] >= VAL[0]) & (st["date"] <= VAL[1])
    bounds = {}
    for pi in range(len(PRODUCTS)):
        for wi in range(len(WINDOWS)):
            m = train & (st["pi"] == pi) & (st["wi"] == wi)
            if m.sum() < 10:
                continue
            bounds[(pi, wi)] = (np.quantile(np.log(np.maximum(st["V"][m], 1e-9)), [1 / 3, 2 / 3]),
                                np.quantile(np.log(np.maximum(st["S"][m], 1e-9)), [1 / 3, 2 / 3]))
    rows = []
    same_case = Counter()
    for i in range(len(st["uid"])):
        pi, wi = int(st["pi"][i]), int(st["wi"][i])
        b = bounds.get((pi, wi))
        if b is None:
            continue
        vb = int(np.digitize(np.log(max(st["V"][i], 1e-9)), b[0]))
        sb = int(np.digitize(np.log(max(st["S"][i], 1e-9)), b[1]))
        same_case[(pi, wi, int(st["date"][i]))] += 1
        if val[i]:
            rows.append([PRODUCTS[pi], WINDOWS[wi], int(st["date"][i]),
                         round(float(np.log(max(st["V"][i], 1e-9))), 4), vb,
                         round(float(np.log(max(st["S"][i], 1e-9))), 4), sb])
    with open(os.path.join(RES, "state_buckets_val.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "window", "date", "log_V_ref", "liq_bucket", "log_sigma1m", "vol_bucket"])
        w.writerows(rows)
    # spread of states: cross-product vs within-product
    v_all = np.log(np.maximum(st["V"], 1e-9))
    within = [np.std(v_all[(st["pi"] == pi)]) for pi in range(len(PRODUCTS)) if (st["pi"] == pi).any()]
    return {"case_days": int(len(st["uid"])), "val_rows": len(rows),
            "cells_with_bounds": len(bounds),
            "cross_product_std": round(float(np.std(v_all)), 4),
            "median_within_product_std": round(float(np.median(within)), 4),
            "same_case_duplicate_rows": int(sum(v - 1 for v in same_case.values() if v > 1))}


def rederive(query_keys, n_paths=20):
    """Independently recompute scenario aggregates for a few real queries."""
    sc = load_npz(os.path.join(RES, "scenarios_train_val.npz"))
    k = sc["keys"]
    idx = {}
    for i in range(len(k)):
        idx[(int(k[i, 0]), int(k[i, 1]), int(k[i, 2]), int(k[i, 3]), int(k[i, 4]),
             int(k[i, 5]), int(k[i, 6]))] = i
    out = []
    for key in query_keys:
        pi, wi, date, size, D, mi, hh = key
        series = E.load_series(PRODUCTS[pi], WINDOWS[wi])
        di = series.dates.index(str(date))
        i0 = series.labels.index(E.release_label(PRODUCTS[pi], WINDOWS[wi]))
        valid = series.mask[:, i0 + 1:i0 + 1 + max(E.DS)].all(axis=1) & series.mask[:, i0]
        hist = [j for j in range(di) if valid[j]][-n_paths:]
        st = E.reference_stats(series, di, i0)
        Q = int(max(1, np.floor(E.SIZE_TIERS[size] * st["v_avg"])))
        tgt = E.int_targets(["asap", "twap", "shape"][mi], hh, Q, st["cumw"], D)
        costs, comps, fees, L1 = [], [], [], []
        for j in hist:
            vol = series.volume[j, i0 + 1:i0 + 1 + D]
            capv = np.floor(0.10 * vol).astype(np.int64)
            fills = E.simulate_fills_int(tgt, capv)
            price = series.close[j, i0 + 1:i0 + 1 + D] / series.close[j, i0]
            filled = fills.sum()
            resid = Q - filled
            gap = (fills * (price - 1)).sum() / Q + resid / Q * (price[-1] - 1)
            costs.append(gap * 1e4)
            comps.append(1.0 if filled >= Q else 0.0)
            fees.append(float((fills * price).sum() / Q))
            v = np.maximum(vol, 1e-12)
            L1.append(float((fills * price / Q * (fills / (0.01 * v))).sum()))
        i = idx.get(key)
        out.append({"key": key, "Q": Q,
                    "pred_cost": round(float(np.mean(costs)), 4),
                    "pred_comp": round(float(np.mean(comps)), 4),
                    "pred_fee_load": round(float(np.mean(fees)), 5),
                    "sd_cost": round(float(np.std(costs, ddof=1)), 4),
                    "L1": round(float(np.mean(L1)), 4),
                    "stored": {"Q": int(sc["Q"][i]), "pred_cost": round(float(sc["pred_cost"][i]), 4),
                               "pred_comp": round(float(sc["pred_comp"][i]), 4),
                               "pred_fee_load": round(float(sc["pred_fee_load"][i]), 5),
                               "sd_cost": round(float(sc["sd_cost"][i]), 4),
                               "L1": round(float(sc["L1"][i]), 4)} if i is not None else None})
    return out


def perturbation_test():
    """Shift prices strictly after the release bar and confirm earlier decisions do not move."""
    series = E.load_series("RB", "am")
    i0 = series.labels.index(E.release_label("RB", "am"))
    di = series.dates.index("20260302")
    st_before = E.reference_stats(series, di, i0)
    base = E.int_targets("twap", 30, 200, st_before["cumw"], 60)
    vol = series.volume[di, i0 + 1:i0 + 61]
    capv = np.floor(0.10 * vol).astype(np.int64)
    f_before = E.simulate_fills_int(base, capv)
    saved = series.close[di, i0 + 1:].copy()
    series.close[di, i0 + 1:] *= 1.5
    st_after = E.reference_stats(series, di, i0)
    f_after = E.simulate_fills_int(E.int_targets("twap", 30, 200, st_after["cumw"], 60), capv)
    series.close[di, i0 + 1:] = saved
    return {"fills_unchanged": bool(np.array_equal(f_before, f_after)),
            "cumw_unchanged": bool(np.allclose(st_before["cumw"], st_after["cumw"])),
            "v_ref_unchanged": bool(abs(st_before["v_avg"] - st_after["v_avg"]) < 1e-9)}


def main() -> None:
    out = {"baseline_all_queries": baseline_all_queries(),
           "deadline": deadline_distribution(),
           "state": state_buckets(),
           "perturbation": perturbation_test()}
    keys = []
    sc = load_npz(os.path.join(RES, "scenarios_train_val.npz"))
    k = sc["keys"]
    want = {(0, 1), (2, 1), (4, 1), (6, 1), (8, 1)}   # five products, morning window
    seen = set()
    for i in range(len(k)):
        p, w = int(k[i, 0]), int(k[i, 1])
        if (p, w) not in want or (p, w) in seen:
            continue
        if not (VAL[0] <= int(k[i, 2]) <= VAL[1]):
            continue
        if int(k[i, 4]) == 60 and int(k[i, 6]) in (0, 30):
            seen.add((p, w))
            keys.append((p, w, int(k[i, 2]), int(k[i, 3]), int(k[i, 4]), int(k[i, 5]), int(k[i, 6])))
        if len(seen) >= 5:
            break
    out["rederivation"] = rederive(keys)
    with open(os.path.join(RES, "focus_checks.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"deadline": out["deadline"], "state": out["state"],
                      "perturbation": out["perturbation"],
                      "rederivation_n": len(out["rederivation"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
