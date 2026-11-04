"""States, sensitivities and T60 Pareto for the focused round.

Outputs:
  state_within.csv        per (product, window) buckets from TRAIN quantiles
  sensitivity.csv         fee / spread / impact / capacity / price-proxy effects
  pareto_t60.csv          non-dominated arms on cost_T60, completion, risk_T60, wall time
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

ROOT = E.ROOT
RES = os.path.join(ROOT, "results")
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]
TRAIN = (20260105, 20260430)
VAL = (20260506, 20260630)
FEE = 1.3


def load_npz(path):
    with np.load(path, allow_pickle=False) as z:
        return {k: np.array(z[k], copy=True) for k in z.files}


def cost(rp, i, side, a, phi, fee_total=FEE, T60=True):
    L = rp["L1"][i] if phi == 1.0 else rp["L05"][i]
    resid = rp["gap_resid_T60"][i] if T60 else rp["gap_resid"][i]
    return side * (rp["gap_exec"][i] + resid) + fee_total * rp["fee_load"][i] + a * L


def activity_series(pi, wi):
    """first-segment volume from session open to release, vs prior-20-day same segment."""
    series = E.load_series(PRODUCTS[pi], WINDOWS[wi])
    if series is None:
        return None
    rel = E.release_label(PRODUCTS[pi], WINDOWS[wi])
    if rel not in series.labels:
        return None
    i0 = series.labels.index(rel)
    seg = series.seg
    start = int(np.argmax(seg == seg[i0]))
    out = {}
    for di in range(len(series.dates)):
        hist = [j for j in range(max(0, di - 20), di)
                if series.mask[j, start:i0 + 1].all()]
        if not hist:
            continue
        cur = float(series.volume[di, start:i0 + 1].sum())
        base = float(np.mean([series.volume[j, start:i0 + 1].sum() for j in hist]))
        if base > 0:
            out[int(series.dates[di])] = cur / base
    return out


def state_tables(rp):
    k = rp["keys"]
    uid = (k[:, 0].astype(np.int64) * 100 + k[:, 1]) * 100000000 + k[:, 2]
    order = np.argsort(uid)
    u, first = np.unique(uid[order], return_index=True)
    idx = order[first]
    dates = k[idx, 2]
    pis, wis = k[idx, 0], k[idx, 1]
    V, S = rp["V_ref"][idx], rp["sigma1m"][idx]
    act = {}
    for pi in range(len(PRODUCTS)):
        for wi in range(len(WINDOWS)):
            a = activity_series(pi, wi)
            if a:
                act[(pi, wi)] = a
    bounds = {}
    train = (dates >= TRAIN[0]) & (dates <= TRAIN[1])
    for pi in range(len(PRODUCTS)):
        for wi in range(len(WINDOWS)):
            m = train & (pis == pi) & (wis == wi)
            if m.sum() < 20:
                continue
            b = [np.quantile(np.log(np.maximum(V[m], 1e-9)), [1 / 3, 2 / 3]),
                 np.quantile(np.log(np.maximum(S[m], 1e-9)), [1 / 3, 2 / 3])]
            av = [act[(pi, wi)].get(int(d), np.nan) for d in dates[m]]
            av = np.array([x for x in av if np.isfinite(x)])
            b.append(np.quantile(np.log(np.maximum(av, 1e-9)), [1 / 3, 2 / 3]) if len(av) > 10 else None)
            bounds[(pi, wi)] = b
    rows = []
    cap = k[:, 7] == 1
    for pi in range(len(PRODUCTS)):
        for wi in range(len(WINDOWS)):
            b = bounds.get((pi, wi))
            if b is None:
                continue
            series = E.load_series(PRODUCTS[pi], WINDOWS[wi])
            i0 = series.labels.index(E.release_label(PRODUCTS[pi], WINDOWS[wi])) if series is not None else None
            for split, (lo, hi) in (("train", TRAIN), ("val", VAL)):
                m0 = cap & (k[:, 0] == pi) & (k[:, 1] == wi) & (k[:, 2] >= lo) & (k[:, 2] <= hi)
                m0 &= np.isin(k[:, 3], [0, 2]) & (k[:, 4] == 60)
                for size in (0, 2):
                    for mi, method in enumerate(METHODS):
                        for hh in (0, 60):
                            sel = m0 & (k[:, 3] == size) & (k[:, 5] == mi) & (k[:, 6] == hh)
                            if sel.sum() < 20:
                                continue
                            for dim, val in (("liq", np.log(np.maximum(rp["V_ref"][sel], 1e-9))),
                                             ("vol", np.log(np.maximum(rp["sigma1m"][sel], 1e-9)))):
                                bnd = b[0] if dim == "liq" else b[1]
                                bucket = np.digitize(val, bnd)
                                for bb in (0, 1, 2):
                                    mm = bucket == bb
                                    if mm.sum() < 10:
                                        continue
                                    c = cost(rp, np.nonzero(sel)[0][mm], 1.0, 0.5, 1.0)
                                    rows.append([split, PRODUCTS[pi], WINDOWS[wi], dim, bb, size, method, hh,
                                                 int(mm.sum()), round(float(np.mean(c)), 4),
                                                 round(float(np.std(c, ddof=1)), 4),
                                                 round(float(np.mean(rp["resid"][sel][mm] == 0)), 4)])
    with open(os.path.join(RES, "state_within.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["split", "product", "window", "state_dim", "bucket", "size_idx", "method", "h",
                    "n", "mean_cost_t60_bp", "sd_cost_bp", "completion"])
        w.writerows(rows)
    return len(rows)


def sensitivity(rp):
    rows = []
    cap = rp["keys"][:, 7]
    k = rp["keys"]
    base = (k[:, 3] == 2) & (k[:, 4] == 60)
    for split, (lo, hi) in (("train", TRAIN), ("val", VAL)):
        m0 = base & (k[:, 2] >= lo) & (k[:, 2] <= hi)
        for mi, method in enumerate(METHODS):
            for hh in (0, 30, 60):
                sel = m0 & (k[:, 5] == mi) & (k[:, 6] == hh)
                if sel.sum() == 0:
                    continue
                idx = np.nonzero(sel)[0]
                price = rp["gap_exec"][idx] + rp["gap_resid_T60"][idx]
                fee_load = rp["fee_load"][idx]
                L1, L05 = rp["L1"][idx], rp["L05"][idx]
                for fee in (0.0, 0.3, 0.6):
                    for spread in (0.0, 1.0, 2.0):
                        c = price + (fee + spread) * fee_load
                        rows.append([split, "fee_spread", f"fee{fee}_spread{spread}", method, hh, int(sel.sum()),
                                     round(float(np.mean(c)), 4), "", ""])
                for a in (0.0, 0.5, 2.0):
                    for phi, L in ((1.0, L1), (0.5, L05)):
                        c = price + FEE * fee_load + a * L
                        rows.append([split, "impact", f"a{a}_phi{phi}", method, hh, int(sel.sum()),
                                     round(float(np.mean(c)), 4), "", ""])
                for ci, cf in enumerate((0.05, 0.10, 0.20)):
                    sel_c = base & (k[:, 2] >= lo) & (k[:, 2] <= hi) & (k[:, 5] == mi) & (k[:, 6] == hh) & (cap == ci)
                    idxc = np.nonzero(sel_c)[0]
                    if len(idxc) == 0:
                        continue
                    c = rp["gap_exec"][idxc] + rp["gap_resid_T60"][idxc] + FEE * rp["fee_load"][idxc] + 0.5 * rp["L1"][idxc]
                    rows.append([split, "capacity", f"cap{cf}", method, hh, int(len(idxc)),
                                 round(float(np.mean(c)), 4),
                                 round(float(np.mean(rp["resid"][idxc] == 0)), 4), ""])
    with open(os.path.join(RES, "sensitivity.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["split", "family", "variant", "method", "h", "n", "mean_cost_bp", "completion", ""])
        w.writerows(rows)
    return len(rows)


def proxy_compare():
    a = load_npz(os.path.join(RES, "replay_train_val_t60.npz"))
    b = load_npz(os.path.join(RES, "replay_train_val_vwaq_t60.npz"))
    ka, kb = a["keys"], b["keys"]
    def key(k, i):
        return (int(k[i, 0]), int(k[i, 1]), int(k[i, 2]), int(k[i, 3]), int(k[i, 4]), int(k[i, 5]), int(k[i, 6]), int(k[i, 7]))
    ib = {key(kb, i): i for i in range(len(kb))}
    rows = []
    for i in range(len(ka)):
        kk = key(ka, i)
        j = ib.get(kk)
        if j is None or kk[7] != 1:
            continue
        if kk[3] != 2 or kk[4] != 60:
            continue
        date = kk[2]
        if not (VAL[0] <= date <= VAL[1]):
            continue
        ca = float(a["gap_exec"][i] + a["gap_resid_T60"][i] + FEE * a["fee_load"][i])
        cb = float(b["gap_exec"][j] + b["gap_resid_T60"][j] + FEE * b["fee_load"][j])
        rows.append([PRODUCTS[kk[0]], kk[5], kk[6], round(ca, 4), round(cb, 4), round(cb - ca, 4)])
    with open(os.path.join(RES, "proxy_compare.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "method", "h", "cost_close_bp", "cost_vwap_bp", "delta_bp"])
        w.writerows(rows)
    agg = defaultdict(list)
    for r in rows:
        agg[(r[1], r[2])].append(r[5])
    return {"cases": len(rows), "by_arm": {f"{k[0]}_{k[1]}": round(float(np.mean(v)), 4) for k, v in agg.items()},
            "products": sorted({r[0] for r in rows})}


def pareto(rp):
    k = rp["keys"]
    rows = []
    for split, (lo, hi) in (("train", TRAIN), ("val", VAL)):
        m0 = (k[:, 2] >= lo) & (k[:, 2] <= hi) & (k[:, 7] == 1)
        for pi in range(len(PRODUCTS)):
            for wi in range(len(WINDOWS)):
                for size in range(3):
                    for side in (1.0, -1.0):
                        m = m0 & (k[:, 0] == pi) & (k[:, 1] == wi) & (k[:, 3] == size)
                        if m.sum() == 0:
                            continue
                        pts = []
                        for mi, method in enumerate(METHODS):
                            for hh in (0, 5, 10, 20, 30, 60):
                                sel = m & (k[:, 5] == mi) & (k[:, 6] == hh)
                                if sel.sum() < 20:
                                    continue
                                c = cost(rp, np.nonzero(sel)[0], side, 0.5, 1.0)
                                pts.append({"D": int(k[sel, 4][0]), "method": method, "h": hh,
                                            "cost": float(np.mean(c)),
                                            "comp": float(np.mean(rp["resid"][sel] == 0)),
                                            "risk": float(np.std(c, ddof=1)),
                                            "time": float(np.nanmean(rp["wall_min"][sel]))})
                        for p in pts:
                            dom = any((q["cost"] <= p["cost"] and q["comp"] >= p["comp"] and q["risk"] <= p["risk"]
                                       and q["time"] <= p["time"]) and
                                      (q["cost"] < p["cost"] or q["comp"] > p["comp"] or q["risk"] < p["risk"]
                                       or q["time"] < p["time"]) for q in pts if q is not p)
                            if not dom:
                                rows.append([split, PRODUCTS[pi], WINDOWS[wi], size,
                                             "buy" if side > 0 else "sell", p["D"], p["method"], p["h"],
                                             round(p["cost"], 4), round(p["comp"], 4),
                                             round(p["risk"], 4), round(p["time"], 3)])
    with open(os.path.join(RES, "pareto_t60.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["split", "product", "window", "size_idx", "side", "D", "method", "h",
                    "cost_t60_bp", "completion", "risk_bp", "wall_min"])
        w.writerows(rows)
    return len(rows)


def main() -> None:
    rp = load_npz(os.path.join(RES, "replay_train_val_t60.npz"))
    out = {"state_rows": state_tables(rp), "sensitivity_rows": sensitivity(rp),
           "proxy": proxy_compare(), "pareto_rows": pareto(rp)}
    with open(os.path.join(RES, "analysis_v3b.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
