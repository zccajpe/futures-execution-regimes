"""Matched scenario forecast: today's parent order inside historical minute paths.

For each query case (series, day d, size tier, cap) this module takes
  * today's Q (from V_ref of the prior 20 days),
  * today's cumulative target schedule (today's volume-shape weights / uniform),
  * the D and arm set,
and evaluates that SAME order on each of the most recent K historical trading days
of the same product/window (all strictly before d):
  * capacity from that day's realised minute volumes,
  * prices normalised by that day's release close (no cross-contract splicing),
  * per-path cost, completion, residual, waiting time and impact loading.

Outputs results/scenarios_train_val.npz with one row per (series, day, size, D, arm).
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

ROOT = E.ROOT
RES = os.path.join(ROOT, "results")
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]
CAP_CENTRAL = 0.10
K_PATHS = 20
FIELDS = ("Q", "pred_cost", "sd_cost", "p95_cost", "pred_comp", "p95_resid", "pred_fill", "pred_fee_load", "pred_resid_frac",
          "pred_wait", "pred_time", "L1", "L05", "n_paths", "sigma_D")


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> None:
    start = arg("--start", "20251103")
    end = arg("--end", "20260630")
    out_path = arg("--out", os.path.join(RES, "scenarios_train_val.npz"))
    keys, vals = [], {f: [] for f in FIELDS}
    t0 = time.time()
    horizon = max(E.DS)
    for pi, product in enumerate(PRODUCTS):
        for wi, window in enumerate(WINDOWS):
            series = E.load_series(product, window)
            if series is None or E.release_label(product, window) not in series.labels:
                continue
            i0 = series.labels.index(E.release_label(product, window))
            if i0 + 1 + horizon > len(series.labels):
                continue
            sl = slice(i0 + 1, i0 + 1 + horizon)
            valid = series.mask[:, sl].all(axis=1) & series.mask[:, i0]
            dates = series.dates
            keep = [i for i, d in enumerate(dates) if start <= d <= end and valid[i]]
            for di in keep:
                st = E.reference_stats(series, di, i0)
                if st is None:
                    continue
                hist = [j for j in range(di) if valid[j]][-K_PATHS:]
                if len(hist) < 5:
                    continue
                Vh = series.volume[hist][:, sl]
                Ch = series.close[hist][:, sl]
                P0h = series.close[hist, i0]
                if (P0h <= 0).any():
                    continue
                Rh = Ch / P0h[:, None]
                sigD = st["sigma1m"] * np.sqrt(np.array(E.DS, dtype=np.float64))
                for si, rho in enumerate(E.SIZE_TIERS):
                    Q = int(max(1, np.floor(rho * st["v_avg"])))
                    capv = np.floor(CAP_CENTRAL * Vh).astype(np.int64)
                    for D in E.DS:
                        idxs = np.arange(1, D + 1, dtype=np.float64)
                        for method, h in E.arm_specs(D):
                            tgt = E.int_targets(method, h, Q, st["cumw"], D)
                            cumcap = np.cumsum(capv[:, :D], axis=1)
                            slack = np.minimum(0, np.minimum.accumulate(tgt[None, :] - cumcap, axis=1))
                            cum = np.clip(cumcap + slack, 0, Q)
                            cum = np.maximum.accumulate(cum, axis=1)
                            fills = np.diff(np.concatenate([np.zeros((len(hist), 1)), cum], axis=1), axis=1)
                            filled = fills.sum(axis=1)
                            resid = Q - filled
                            price = Rh[:, :D]
                            exec_gap = (fills * (price - 1.0)).sum(axis=1) / Q
                            resid_gap = resid / Q * (Rh[:, D - 1] - 1.0)
                            cost = exec_gap + resid_gap
                            comp = (filled >= Q).astype(np.float64)
                            hit = (np.cumsum(fills, axis=1) >= Q)
                            comp_time = np.where(hit.any(axis=1), hit.argmax(axis=1) + 1.0, np.nan)
                            wait = np.where(filled > 0, (fills * idxs).sum(axis=1) / np.maximum(filled, 1e-9), np.nan)
                            v = np.maximum(Vh[:, :D], 1e-12)
                            wt = fills * price / Q
                            part = fills / (E.RHO_REF * v)
                            L1 = (wt * part).sum(axis=1)
                            L05 = (wt * np.sqrt(part)).sum(axis=1)
                            keys.append((pi, wi, int(dates[di]), si, D, METHODS.index(method), h, len(hist)))
                            vals["Q"].append(Q)
                            vals["pred_cost"].append(float(np.nanmean(cost * 1e4)))
                            vals["sd_cost"].append(float(np.nanstd(cost * 1e4, ddof=1)))
                            vals["p95_cost"].append(float(np.nanpercentile(cost * 1e4, 95)))
                            vals["pred_comp"].append(float(comp.mean()))
                            vals["p95_resid"].append(float(np.percentile(resid, 95)))
                            vals["pred_fill"].append(float(np.nanmean(filled / Q)))
                            vals["pred_fee_load"].append(float(np.nanmean((fills * price).sum(axis=1) / Q)))
                            vals["pred_resid_frac"].append(float(np.nanmean(resid / Q)))
                            vals["pred_wait"].append(float(np.nanmean(wait)))
                            vals["pred_time"].append(float(np.nanmean(comp_time)))
                            vals["L1"].append(float(np.nanmean(L1)))
                            vals["L05"].append(float(np.nanmean(L05)))
                            vals["n_paths"].append(len(hist))
                            vals["sigma_D"].append(float(st["sigma1m"] * np.sqrt(D)))
        print(json.dumps({"product": product, "rows": len(keys), "elapsed_s": round(time.time() - t0, 1)}), flush=True)
    out = {"keys": np.array(keys, dtype=np.int64).reshape(-1, 8)}
    for f in FIELDS:
        out[f] = np.array(vals[f], dtype=np.float64)
    np.savez_compressed(out_path, **out)
    print(json.dumps({"saved": out_path, "rows": int(out["keys"].shape[0]),
                      "elapsed_s": round(time.time() - t0, 1)}))


if __name__ == "__main__":
    main()
