#!/usr/bin/env python3
"""One main fit: estimate the state-bucket drift table rho_b, its gate, kappa_rho
by a pre-registered forward 3-fold inside TRAIN, and the risk cell table.

Inputs (all already built / cached):
  features_exante.npz, pred_cache.npz, results/replay_*_t60.npz

Output:
  params_exante.json  (frozen; consumed by evaluate_rule.py and the skill)

Estimator (design section 3.2):
  for every (product, window, day) the non-ASAP arms are differenced against the
  same unit's ASAP arm, pooling 3 size tiers x 4 deadlines into ONE rho_t:
      dy = price_arm - price_asap           (buy-side raw price cost, no side sign)
      dX = sigma1m * (Treal_arm - Treal_asap)
      rho_t = sum(dX*dy) / sum(dX*dX)
  Treal = L_exec*D + 60*L_res.
No side flip anywhere in fitting; the side sign is applied once at decision time.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(STAGE)
CORE = os.path.join(ROOT, "delivery", "futures-execution-decision", "scripts", "exante_core.py")
spec = importlib.util.spec_from_file_location("exante_core", CORE)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]
DS = (5, 15, 30, 60)
ARMS = [C.arm_name(m, h) for m, h in C.ARMS]
TRAIN = (20260105, 20260430)
KAPPAS = (10, 20, 50)
KAPPA_SIGMA = 20.0
FOLDS = ((40, 41, 55), (55, 56, 65), (65, 66, 77))


def load_replay():
    tv = np.load(os.path.join(ROOT, "results", "replay_train_val_t60.npz"), allow_pickle=False)
    te = np.load(os.path.join(ROOT, "results", "replay_test_t60.npz"), allow_pickle=False)
    keys = np.concatenate([tv["keys"], te["keys"]])
    out = {"keys": keys}
    for f in ("gap_exec", "gap_resid_T60", "L_exec", "L_res", "fee_load", "resid"):
        out[f] = np.concatenate([tv[f], te[f]])
    return out


def split_of(d):
    d = int(d)
    if TRAIN[0] <= d <= TRAIN[1]:
        return "train"
    if 20260506 <= d <= 20260630:
        return "val"
    if 20260701 <= d <= 20261020:
        return "test"
    return "outside"


def main() -> None:
    rep = load_replay()
    k = rep["keys"]
    cap = k[:, 7] == 1
    idx = {}
    for i in np.nonzero(cap)[0]:
        idx[(int(k[i, 0]), int(k[i, 1]), int(k[i, 2]), int(k[i, 3]), int(k[i, 4]), int(k[i, 5]), int(k[i, 6]))] = i

    feats = np.load(os.path.join(STAGE, "features_exante.npz"), allow_pickle=False)
    fk = feats["keys"]
    fidx = {(int(a), int(b)): i for i, (a, b) in enumerate(fk)}
    sigma1m = feats["sigma1m"]
    z_pre = feats["z_pre"]
    v_pre = feats["v_pre"]

    pred = np.load(os.path.join(STAGE, "pred_cache.npz"), allow_pickle=False)
    pk = pred["keys"]
    pidx = {(int(a), int(b), int(c), int(d)): i for i, (a, b, c, d) in enumerate(pk)}
    pT, pL, pcomp, pfee = pred["T"], pred["L"], pred["comp"], pred["fee"]

    # ---- rho_t per (product, window, day) -----------------------------------
    rows = []
    unident = 0
    series_dates = sorted({(int(a), int(b)) for a, b in fk})
    for (series, date) in series_dates:
        if split_of(date) != "train":
            continue
        pi, wi = series // 3, series % 3
        fi = fidx[(series, date)]
        s1 = float(sigma1m[fi])
        num = den = 0.0
        cnt = 0
        for si in range(3):
            for D in DS:
                ia = idx.get((pi, wi, date, si, D, 0, 0))
                if ia is None:
                    continue
                ya = rep["gap_exec"][ia] + rep["gap_resid_T60"][ia]
                Ta = rep["L_exec"][ia] * D + 60 * rep["L_res"][ia]
                for arm in ARMS:
                    if arm == "asap0":
                        continue
                    method = arm.rstrip("0123456789")
                    h = int(arm[len(method):])
                    m_i = METHODS.index(method)
                    j = idx.get((pi, wi, date, si, D, m_i, h))
                    if j is None or h > D:
                        continue
                    yb = rep["gap_exec"][j] + rep["gap_resid_T60"][j]
                    Tb = rep["L_exec"][j] * D + 60 * rep["L_res"][j]
                    dy = yb - ya
                    dX = s1 * (Tb - Ta)
                    num += dX * dy
                    den += dX * dX
                    cnt += 1
        if den <= 1e-12 or cnt < 2:
            unident += 1
            continue
        rows.append((pi, wi, date, num / den, cnt, float(z_pre[fi]), float(v_pre[fi])))
    rows.sort(key=lambda r: r[2])
    dates = sorted({r[2] for r in rows})
    print(f"train rho_t rows={len(rows)} unidentifiable={unident} dates={len(dates)}", flush=True)
    assert len(dates) == 77, f"expected 77 TRAIN dates, got {len(dates)}"

    def date_blocks(date_list):
        """Fixed 5-trading-day blocks over the WHOLE date list (not per bucket)."""
        ds = sorted(set(date_list))
        return {d: i // 5 for i, d in enumerate(ds)}, (len(ds) + 4) // 5

    def fit(part_rows, kappa, part_dates):
        z = np.array([r[5] for r in part_rows])
        v = np.array([r[6] for r in part_rows])
        ze = [float(np.quantile(z, 1 / 3)), float(np.quantile(z, 2 / 3))]
        ve = [float(np.quantile(v, 1 / 3)), float(np.quantile(v, 2 / 3))]
        edges = {"z_pre": ze, "v_pre": ve}
        bmap, n_blocks = date_blocks(part_dates)
        buckets = []
        for bi in range(9):
            sel = [r for r in part_rows if C.bucket_index(r[5], r[6], edges) == bi]
            n = len(sel)
            if n == 0:
                buckets.append({"i": bi, "n": 0, "rho": 0.0, "enabled": False, "blocks": 0,
                                "blocks_total": n_blocks, "sign_share": None, "se": None, "note": "empty"})
                continue
            rho_raw = sum(r[3] for r in sel) / n
            rho = sum(r[3] for r in sel) / (n + kappa)
            by_block = {}
            for r in sel:
                by_block.setdefault(bmap[r[2]], []).append(r[3])
            bmeans = [float(np.mean(by_block[k])) for k in sorted(by_block)]
            nb = len(bmeans)
            se = float(np.std(bmeans, ddof=1) / math.sqrt(nb)) if nb >= 2 else None
            sign_share = float(np.mean([1.0 if (m > 0) == (rho >= 0) else 0.0 for m in bmeans])) if nb else None
            enabled = (nb >= 6 and se is not None and abs(rho) > 2 * se
                       and sign_share is not None and sign_share >= 0.6)
            buckets.append({"i": bi, "n": n, "rho_raw": rho_raw, "rho": rho if enabled else 0.0,
                            "rho_fitted": rho, "enabled": bool(enabled), "blocks": nb,
                            "blocks_total": n_blocks, "sign_share": sign_share, "se": se,
                            "note": "" if enabled else ("blocks<6" if nb < 6 else
                                                        ("se unavailable" if se is None else
                                                         ("sign" if (sign_share or 0) < 0.6 else "se")))})
        return {"edges": edges, "buckets": buckets, "kappa": kappa, "blocks_total": n_blocks}

    def score(params, held_dates):
        """Sum of squared held-out rho errors and the number of held-out events."""
        sse, n = 0.0, 0
        for r in rows:
            if r[2] not in held_dates:
                continue
            bi = C.bucket_index(r[5], r[6], params["edges"])
            rho = params["buckets"][bi]["rho"]
            sse += (r[3] - rho) ** 2
            n += 1
        return float(sse), int(n)

    cv = []
    for (ntr, v0, v1) in FOLDS:
        tr_dates = dates[:ntr]
        held = dates[ntr:v1]
        tr_rows = [r for r in rows if r[2] in set(tr_dates)]
        for kappa in KAPPAS:
            p = fit(tr_rows, kappa, tr_dates)
            sse, n = score(p, set(held))
            cv.append({"fold": (ntr, v0, v1), "kappa": kappa,
                       "n_train_days": len(tr_dates), "n_held_days": len(held),
                       "held_sse": sse, "held_n": n,
                       "held_mse": (sse / n) if n else None,
                       "fold_blocks_total": p["blocks_total"]})
    # pooled selection: every held-out event weighs the same regardless of the fold size
    agg = {}
    for rec in cv:
        a = agg.setdefault(rec["kappa"], {"sse": 0.0, "n": 0})
        a["sse"] += rec["held_sse"]
        a["n"] += rec["held_n"]
    kappa_scores = {kk: (v["sse"] / v["n"] if v["n"] else None) for kk, v in agg.items()}
    best = min(kappa_scores.items(), key=lambda kv: (kv[1], -kv[0]))  # tie -> larger kappa
    kappa = best[0]

    final = fit(rows, kappa, dates)

    # ---- risk cell table from TRAIN price-cost SD ---------------------------
    cells = {}
    by_cell = {}
    for (pi, wi, date, si, D, mi, h), i in idx.items():
        if split_of(date) != "train":
            continue
        arm = ("asap0" if mi == 0 else C.arm_name(METHODS[mi], h))
        by_cell.setdefault((pi, wi, si, D, arm), []).append(rep["gap_exec"][i] + rep["gap_resid_T60"][i])
    for (pi, wi, si, D, arm), vals in by_cell.items():
        n = len(vals)
        sd = float(np.std(vals, ddof=1)) if n > 1 else 0.0
        cells[f"{PRODUCTS[pi]}|{WINDOWS[wi]}|{si}|{D}|{arm}"] = [n, round(sd, 10)]

    def dictify(params):
        return {"edges": params["edges"],
                "buckets": [{kk: (round(vv, 8) if isinstance(vv, float) else vv) for kk, vv in b.items()}
                            for b in params["buckets"]],
                "kappa": params["kappa"]}

    out = {
        "version": "exante-20261022",
        "train_window": list(TRAIN),
        "fitted_days": len(dates),
        "rho_rows": len(rows),
        "unidentifiable_days": unident,
        "kappa_rho": kappa,
        "kappa_sigma": KAPPA_SIGMA,
        "kappa_scores": kappa_scores,
        "kappa_selection": {"rule": "pooled SSE / pooled n over the three forward folds; tie -> larger kappa",
                            "pooled": {str(kk): {"sse": round(v["sse"], 10), "n": v["n"]} for kk, v in agg.items()}},
        "cv": cv,
        "edges": final["edges"],
        "blocks_total": final["blocks_total"],
        "buckets": dictify(final)["buckets"],
        "enabled_buckets": sum(1 for b in final["buckets"] if b["enabled"]),
        "sigma_cells": cells,
        "fit_audit": {"dates_used_min": dates[0], "dates_used_max": dates[-1],
                       "n_dates_used": len(dates), "n_val_dates_used": 0, "n_test_dates_used": 0,
                       "cutoff": 20260430, "source": "results/replay_*_t60.npz rows with split=train only"},
        "meta": {"cap_frac": 0.10, "fee_bp": 1.3, "phi": 1.0, "arms": ARMS,
                 "products": PRODUCTS, "windows": WINDOWS,
                 "core_path": CORE, "train_dates": dates},
    }
    with open(os.path.join(STAGE, "params_exante.json"), "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps({"kappa_rho": kappa, "kappa_scores": kappa_scores,
                      "enabled_buckets": out["enabled_buckets"],
                      "buckets": [{kk: b[kk] for kk in ("i", "n", "rho", "enabled", "blocks", "se")}
                                  for b in out["buckets"]]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
