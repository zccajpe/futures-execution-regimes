"""Replay v2 over assembled series: integer lots, loadings separated from scenarios.

Output columns are side-free loadings. Costs are formed later as
  buy : +gap_exec +gap_resid + fee_bp*fee_load + spread_bp*fee_load + a*L_phi + drift
  sell: -gap_exec -gap_resid + fee_bp*fee_load + spread_bp*fee_load + a*L_phi - drift
where drift = sigma_D_bp * mu * (L_exec + L_res) and mu>0 means adverse for the side.

usage: python3 run_replay.py --start 20260105 --end 20260630 --out results/replay_train_val.npz
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
OUTDIR = os.path.join(ROOT, "results")
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]
FIELDS = ("Q", "filled", "resid", "comp_time", "wait_time", "last_fill", "wall_min", "fill_sig", "n_fill_bars",
          "gap_exec", "gap_resid", "fee_load", "L1", "L05", "rho_arm",
          "mo5", "mo15", "mo30", "cov5", "cov15", "cov30",
          "L_exec", "L_res", "P0", "sigma1m", "V_ref")


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> None:
    start = arg("--start", "20260105")
    end = arg("--end", "20260630")
    out_path = arg("--out", os.path.join(OUTDIR, "replay_train_val.npz"))
    products = arg("--products", ",".join(PRODUCTS)).split(",")
    windows = arg("--windows", ",".join(WINDOWS)).split(",")
    quick = int(arg("--quick", "0"))
    proxy = arg("--proxy", "close")
    os.makedirs(OUTDIR, exist_ok=True)

    keys, vals = [], {f: [] for f in FIELDS}
    skipped = {"missing_bars": 0, "short_window": 0, "no_reference": 0, "bad_price": 0,
               "no_release": 0, "vwap_invalid_window": 0}
    t0 = time.time()
    for product in products:
        for window in windows:
            series = E.load_series(product, window, proxy=proxy)
            if series is None:
                continue
            if E.release_label(product, window) not in series.labels:
                skipped["no_release"] += 1
                continue
            i0 = series.labels.index(E.release_label(product, window))
            horizon = max(E.DS)
            if i0 + 1 + horizon > len(series.labels):
                skipped["short_window"] += 1
                continue
            keep = [i for i, d in enumerate(series.dates) if start <= d <= end]
            if quick:
                keep = keep[:quick]
            abs_pos = np.arange(i0 + 1, i0 + 1 + horizon)
            fwd = {}
            for k in (5, 15, 30):
                fwd[k] = np.array([(-1 if E.forward_clock_index(series, p, k) is None else E.forward_clock_index(series, p, k)) for p in abs_pos], dtype=np.int64)
            for di in keep:
                st = E.reference_stats(series, di, i0)
                if st is None:
                    skipped["no_reference"] += 1
                    continue
                sl = slice(i0 + 1, i0 + 1 + horizon)
                if not series.mask[di, sl].all() or not series.mask[di, i0]:
                    skipped["missing_bars"] += 1
                    continue
                if proxy == "vwap" and not (series.vwap_valid[di, sl].all() and series.vwap_valid[di, i0]):
                    skipped["vwap_invalid_window"] += 1
                    continue
                vol = series.volume[di, sl]
                px = series.close[di, sl] if proxy == "close" else series.vwap[di, sl]
                if not (px > 0).all() or not np.isfinite(px).all():
                    skipped["bad_price"] += 1
                    continue
                P0 = float(series.close[di, i0])
                if not np.isfinite(P0) or P0 <= 0:
                    skipped["bad_price"] += 1
                    continue
                rets, covs = {}, {}
                for k in (5, 15, 30):
                    idx = fwd[k]
                    ok = idx >= 0
                    ret = np.zeros(horizon)
                    if ok.any():
                        ret[ok] = (series.close[di, idx[ok]] - px[ok]) / px[ok] * 1e4
                    rets[k], covs[k] = ret, ok
                tiers: dict[int, list[int]] = {}
                for si, rho in enumerate(E.SIZE_TIERS):
                    Q = int(max(1, np.floor(rho * st["v_avg"])))
                    tiers.setdefault(Q, []).append(si)
                for Q, tier_ids in tiers.items():
                    si = tier_ids[0]
                    for ci, capf in enumerate(E.CAPS):
                        capv = np.floor(capf * vol).astype(np.int64)
                        for D in E.DS:
                            for method, h in E.arm_specs(D):
                                tgt = E.int_targets(method, h, Q, st["cumw"], D)
                                fills = E.simulate_fills_int(tgt, capv[:D])
                                filled = int(fills.sum())
                                resid = Q - filled
                                notional = float((fills * px[:D]).sum())
                                N0 = Q * P0
                                gap_exec = (notional - filled * P0) / N0 * 1e4
                                gap_resid = resid * (float(series.close[di, i0 + D]) - P0) / N0 * 1e4
                                fee_load = notional / N0
                                L1 = L05 = 0.0
                                for j in np.nonzero(fills)[0]:
                                    q = float(fills[j])
                                    v = float(vol[j])
                                    if v <= 0:
                                        continue
                                    pr = q / (E.RHO_REF * v)
                                    w = q * px[j] / N0
                                    L1 += w * pr
                                    L05 += w * np.sqrt(pr)
                                tv = float(vol[:D].sum())
                                rho_arm = filled / tv if tv > 0 else 0.0
                                idxs = np.arange(1, D + 1, dtype=np.float64)
                                cum = np.cumsum(fills)
                                hit = np.nonzero(cum >= Q)[0]
                                comp_time = float(hit[0] + 1) if hit.size else np.nan
                                wait_time = float((fills * idxs).sum() / filled) if filled else np.nan
                                nz = np.nonzero(fills)[0]
                                last_fill = float(nz[-1] + 1) if nz.size else np.nan
                                if np.isfinite(comp_time):
                                    wall = float(series.label_min[i0 + int(comp_time)] - series.label_min[i0])
                                    wall_min = wall if wall >= 0 else wall + 1440
                                else:
                                    wall_min = np.nan
                                mos, cvg = [], []
                                for k in (5, 15, 30):
                                    ok = covs[k][:D]
                                    den = float((fills * ok).sum())
                                    if den > 0:
                                        mos.append(float((fills * rets[k][:D] * ok).sum() / den))
                                        cvg.append(den / filled)
                                    else:
                                        mos.append(np.nan)
                                        cvg.append(0.0)
                                fill_sig = float((fills * (idxs ** 2)).sum())
                                n_fill_bars = float((fills > 0).sum())
                                L_exec = float((fills * (idxs / D)).sum() / Q)
                                L_res = resid / Q
                                keys.append((PRODUCTS.index(product), WINDOWS.index(window), int(series.dates[di]),
                                             si, D, METHODS.index(method), h, ci, len(tier_ids)))
                                for f, v in (("Q", Q), ("filled", filled), ("resid", resid),
                                             ("comp_time", comp_time), ("wait_time", wait_time),
                                             ("last_fill", last_fill), ("wall_min", wall_min),
                                             ("fill_sig", fill_sig), ("n_fill_bars", n_fill_bars),
                                             ("gap_exec", gap_exec), ("gap_resid", gap_resid),
                                             ("fee_load", fee_load), ("L1", L1), ("L05", L05),
                                             ("rho_arm", rho_arm), ("mo5", mos[0]), ("mo15", mos[1]),
                                             ("mo30", mos[2]), ("cov5", cvg[0]), ("cov15", cvg[1]),
                                             ("cov30", cvg[2]), ("L_exec", L_exec), ("L_res", L_res),
                                             ("P0", P0), ("sigma1m", st["sigma1m"]), ("V_ref", st["v_avg"])):
                                    vals[f].append(v)
        print(json.dumps({"product": product, "rows": len(keys), "elapsed_s": round(time.time() - t0, 1)}), flush=True)

    out = {"keys": np.array(keys, dtype=np.int64).reshape(-1, 9)}
    for f in FIELDS:
        out[f] = np.array(vals[f], dtype=np.float64)
    np.savez_compressed(out_path, **out)
    print(json.dumps({"saved": out_path, "rows": int(out["keys"].shape[0]),
                      "skipped": skipped, "elapsed_s": round(time.time() - t0, 1)}))


if __name__ == "__main__":
    main()
