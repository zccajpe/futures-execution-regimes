"""Selector v4: per-query decisions, trained fixed baselines, exact path check.

Performance: NPZ files are read once into plain dicts and closed; no per-row getitem
inside loops.

Correctness fixes after review:
  * one row per predeclared query/config, including infeasible ones
  * fixed h chosen on TRAIN by aggregating each h over all train dates, per family,
    honouring the same preference (completion >= 0.95 / risk budget) as the selector;
    risk is the pricepart SD (never a total-cost SD that contains impact)
  * infeasible training families return None instead of a forced cheap choice
  * CENTRAL_CAP_IDX = 1, impact a*L_phi added to every realised cost
"""
from __future__ import annotations

import csv
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

ROOT = E.ROOT
RES = os.path.join(ROOT, "results")
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]
FEE = 1.3
CENTRAL_CAP_IDX = 1
TRAIN = (20260105, 20260430)
VAL = (20260506, 20260630)
import sys as _sys
def _arg(name, dflt):
    return _sys.argv[_sys.argv.index(name) + 1] if name in _sys.argv else dflt
VAL = (int(_arg("--dec-start", str(VAL[0]))), int(_arg("--dec-end", str(VAL[1]))))
MIN_COMP = 0.95


def load_npz(path: str) -> dict:
    with np.load(path, allow_pickle=False) as z:
        return {k: np.array(z[k], copy=True) for k in z.files}


def side_cost(rp, i, side, a, phi):
    L = rp["L1"][i] if phi == 1.0 else rp["L05"][i]
    return side * (rp["gap_exec"][i] + rp["gap_resid"][i]) + FEE * rp["fee_load"][i] + a * L


def pred_cost(sc, rows, side, a, phi):
    L = sc["L1"][rows] if phi == 1.0 else sc["L05"][rows]
    return side * sc["pred_cost"][rows] + FEE * sc["pred_fee_load"][rows] + a * L


def choose(sc, rows, side, a, phi, pref, budget_k, budget_sigma):
    comp = sc["pred_comp"][rows]
    risk = sc["sd_cost"][rows]
    budget = budget_k * budget_sigma
    cost = pred_cost(sc, rows, side, a, phi)
    if pref == "completion_first":
        feas = comp >= MIN_COMP
        if not feas.any():
            return None, "NO_FEASIBLE_COMPLETION"
        ok = feas & (risk <= budget)
        if not ok.any():
            return None, "NO_RISK_FEASIBLE"
        pool = np.where(ok)[0]
        return int(pool[np.argmin(cost[pool])]), "ok"
    ok = risk <= budget
    if not ok.any():
        return None, "NO_RISK_FEASIBLE"
    pool = np.where(ok)[0]
    return int(pool[np.argmin(cost[pool])]), "ok"


def load_frozen(path):
    """(product,window,size,D,side,a,phi,pref,k) -> {twap, shape, sigma, reason_*}"""
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        key = (r["product"], r["window"], int(r["size_idx"]), int(r["D"]), r["side"],
               float(r["a_bp"]), float(r["phi"]), r["preference"], float(r["budget_k"]))
        slot = out.setdefault(key, {"sigma": float(r["sigma_D_rms_bp"]), "twap": None, "shape": None,
                                    "reason_twap": r["reason"], "reason_shape": r["reason"]})
        slot["sigma"] = float(r["sigma_D_rms_bp"])
        if r["flag"] == "chosen":
            slot[r["family"]] = int(r["h"])
        elif slot.get(r["family"]) is None:
            slot["reason_" + r["family"]] = r["reason"]
    return out


def main() -> None:
    sc = load_npz(os.path.join(RES, _arg("--in-sc", "scenarios_train_val.npz")))
    rp = load_npz(os.path.join(RES, _arg("--in-rp", "replay_train_val.npz")))
    rp_train = load_npz(os.path.join(RES, _arg("--train-rp", "replay_train_val.npz"))) if _arg("--train-rp", "") else rp
    k_sc, k_rp = sc["keys"], rp["keys"]

    sc_cells = {}
    for i in range(len(k_sc)):
        sc_cells.setdefault((int(k_sc[i, 0]), int(k_sc[i, 1]), int(k_sc[i, 3]), int(k_sc[i, 4])), []).append(i)
    sc_cells = {k: np.array(v) for k, v in sc_cells.items()}

    rp_idx, rp_train_cells = {}, {}
    for i in range(len(k_rp)):
        if int(k_rp[i, 7]) != CENTRAL_CAP_IDX:
            continue
        date = int(k_rp[i, 2])
        rp_idx[(int(k_rp[i, 0]), int(k_rp[i, 1]), date, int(k_rp[i, 3]), int(k_rp[i, 4]),
                int(k_rp[i, 5]), int(k_rp[i, 6]))] = i
    k_tr = rp_train["keys"]
    for i in range(len(k_tr)):
        date = int(k_tr[i, 2])
        if int(k_tr[i, 7]) != CENTRAL_CAP_IDX or not (TRAIN[0] <= date <= TRAIN[1]):
            continue
        cell = (int(k_tr[i, 0]), int(k_tr[i, 1]), int(k_tr[i, 3]), int(k_tr[i, 4]))
        rp_train_cells.setdefault((cell, int(k_tr[i, 5]), int(k_tr[i, 6])), []).append(i)
    for key in rp_train_cells:
        rp_train_cells[key] = np.array(rp_train_cells[key])
    rp_train_cells = {k: np.array(v) for k, v in rp_train_cells.items()}

    configs = [(0.0, 1.0, "completion_first", 1.0),
               (0.5, 1.0, "completion_first", 1.0),
               (2.0, 1.0, "completion_first", 1.0),
               (0.5, 1.0, "cost_first", 1.0),
               (0.5, 0.5, "completion_first", 1.0),
               (0.5, 1.0, "cost_first", 0.25),
               (0.5, 1.0, "cost_first", 0.5)]

    frozen_path = _arg("--frozen-train-input", "")
    frozen = load_frozen(os.path.join(RES, frozen_path) if frozen_path and not os.path.isabs(frozen_path) else frozen_path) if frozen_path else None
    if frozen is not None:
        bad = [k for k, v in frozen.items() if not np.isfinite(v["sigma"]) or v["sigma"] <= 0]
        if bad:
            raise SystemExit(f"frozen training sigma invalid for {len(bad)} groups, e.g. {bad[0]}")
    series_cache, st_cache = {}, {}
    decisions, train_rows = [], []
    for key4, rows in sorted(sc_cells.items()):
        pi, wi, size, D = key4
        dates = k_sc[rows, 2]
        val_dates = np.unique(dates[(dates >= VAL[0]) & (dates <= VAL[1])])
        if len(val_dates) == 0:
            continue
        for side in (1.0, -1.0):
            for (a, phi, pref, bk) in configs:
                if frozen is not None:
                    fkey = (PRODUCTS[pi], WINDOWS[wi], size, D, "buy" if side > 0 else "sell",
                            a, phi, pref, bk)
                    fz = frozen.get(fkey)
                    if fz is None:
                        raise SystemExit(f"missing frozen training parameters for {fkey}")
                    budget_sigma = fz["sigma"]
                    fixed = {"twap": (fz["twap"], fz["reason_twap"]), "shape": (fz["shape"], fz["reason_shape"])}
                    for fam in ("twap", "shape"):
                        train_rows.append([PRODUCTS[pi], WINDOWS[wi], size, D, "buy" if side > 0 else "sell",
                                           a, phi, pref, bk, fam, fixed[fam][0] if fixed[fam][0] is not None else "",
                                           "", "", "", "", round(bk * budget_sigma, 4), "frozen", fixed[fam][1],
                                           round(budget_sigma, 4)])
                sig_train = sc["sigma_D"][rows[(dates >= TRAIN[0]) & (dates <= TRAIN[1])]] if frozen is None else None
                if frozen is None:
                    budget_sigma = float(np.sqrt(np.mean(sig_train ** 2))) if len(sig_train) else float("nan")
                fixed = fixed if frozen is not None else {}
                for fam, mi in (("twap", 1), ("shape", 2)) if frozen is None else ():
                    stats = []
                    for hh in (5, 10, 20, 30, 60):
                        if hh > D:
                            continue
                        ids = rp_train_cells.get((key4, mi, hh))
                        if ids is None or len(ids) == 0:
                            continue
                        cost = np.array([side_cost(rp_train, i, side, a, phi) for i in ids])
                        comp = np.array([1.0 if rp_train["resid"][i] == 0 else 0.0 for i in ids])
                        price = np.array([rp_train["gap_exec"][i] + rp_train["gap_resid"][i] for i in ids])
                        stats.append({"h": hh, "cost": float(cost.mean()), "comp": float(comp.mean()),
                                      "risk": float(price.std(ddof=1)) if len(price) > 1 else 0.0,
                                      "n": int(len(ids))})
                    if not stats:
                        fixed[fam] = (None, "NO_TRAIN_DATA")
                        continue
                    feas = [s for s in stats if s["comp"] >= MIN_COMP]
                    if pref == "completion_first" and not feas:
                        fixed[fam] = (None, "NO_FEASIBLE_COMPLETION")
                    else:
                        pool = feas if pref == "completion_first" else stats
                        in_budget = [s for s in pool if s["risk"] <= bk * budget_sigma]
                        fixed[fam] = ((min(in_budget, key=lambda s: s["cost"])["h"], "ok")
                                      if in_budget else (None, "NO_RISK_FEASIBLE"))
                    for s in stats:
                        train_rows.append([PRODUCTS[pi], WINDOWS[wi], size, D, "buy" if side > 0 else "sell",
                                           a, phi, pref, bk, fam, s["h"], s["n"],
                                           round(s["cost"], 4), round(s["comp"], 4), round(s["risk"], 4),
                                           round(bk * budget_sigma, 4),
                                           "chosen" if s["h"] == fixed[fam][0] else "", fixed[fam][1],
                                           round(budget_sigma, 4)])
                for date in val_dates:
                    d_rows = rows[dates == date]
                    Q = int(sc["Q"][d_rows[0]])
                    chosen, status = choose(sc, d_rows, side, a, phi, pref, bk, budget_sigma)
                    base = [PRODUCTS[pi], WINDOWS[wi], int(date), size, D, "buy" if side > 0 else "sell",
                            a, phi, pref, bk, Q, status]
                    r0 = rp_idx.get((pi, wi, int(date), size, D, 0, 0))
                    asap_c = round(float(side_cost(rp, r0, side, a, phi)), 4) if r0 is not None else ""
                    asap_comp = round(float(1.0 if r0 is not None and rp["resid"][r0] == 0 else 0.0), 4) if r0 is not None else ""
                    asap_r = round(float(rp["resid"][r0]), 2) if r0 is not None else ""
                    if chosen is None:
                        x_tw0 = rp_idx.get((pi, wi, int(date), size, D, 1, fixed["twap"][0])) if fixed["twap"][0] else None
                        x_sh0 = rp_idx.get((pi, wi, int(date), size, D, 2, fixed["shape"][0])) if fixed["shape"][0] else None
                        cells0 = [rp_idx.get((pi, wi, int(date), size, D, mi, hh))
                                  for mi in range(3) for hh in (0, 5, 10, 20, 30, 60)]
                        orc0 = min([side_cost(rp, x, side, a, phi) for x in cells0 if x is not None], default=np.nan)
                        decisions.append(base + ["", "", "", "", "", "", "", "", "", "", "",
                                                 asap_c, asap_comp, asap_r,
                                                 round(float(side_cost(rp, x_tw0, side, a, phi)), 4) if x_tw0 is not None else "",
                                                 round(float(side_cost(rp, x_sh0, side, a, phi)), 4) if x_sh0 is not None else "",
                                                 round(float(orc0), 4) if np.isfinite(orc0) else "", "", ""])
                        continue
                    ci = int(d_rows[chosen])
                    ri = rp_idx.get((pi, wi, int(date), size, D, int(k_sc[ci, 5]), int(k_sc[ci, 6])))
                    x_tw = rp_idx.get((pi, wi, int(date), size, D, 1, fixed["twap"][0])) if fixed["twap"][0] else None
                    x_sh = rp_idx.get((pi, wi, int(date), size, D, 2, fixed["shape"][0])) if fixed["shape"][0] else None
                    cells = [rp_idx.get((pi, wi, int(date), size, D, mi, hh))
                             for mi in range(3) for hh in (0, 5, 10, 20, 30, 60)]
                    orc = min([side_cost(rp, x, side, a, phi) for x in cells if x is not None], default=np.nan)
                    path = ""
                    if key4 not in series_cache:
                        series_cache[key4] = E.load_series(PRODUCTS[pi], WINDOWS[wi])
                    series = series_cache[key4]
                    if series is not None:
                        dstr = str(int(date))
                        if (key4, dstr) not in st_cache:
                            di = series.dates.index(dstr)
                            i0 = series.labels.index(E.release_label(PRODUCTS[pi], WINDOWS[wi]))
                            st_cache[(key4, dstr)] = (di, i0, E.reference_stats(series, di, i0))
                        di, i0, st = st_cache[(key4, dstr)]
                        capv = np.floor(0.10 * series.volume[di, i0 + 1:i0 + 1 + D]).astype(np.int64)
                        f_asap = E.simulate_fills_int(E.int_targets("asap", 0, Q, st["cumw"], D), capv)
                        f_sel = E.simulate_fills_int(
                            E.int_targets(METHODS[int(k_sc[ci, 5])], int(k_sc[ci, 6]), Q, st["cumw"], D), capv)
                        path = int(0 if np.array_equal(f_sel, f_asap) else 1)
                    decisions.append(base + [
                        METHODS[int(k_sc[ci, 5])], int(k_sc[ci, 6]),
                        round(float(pred_cost(sc, np.array([ci]), side, a, phi)[0]), 4),
                        round(float(sc["pred_cost"][ci]), 4),
                        round(float(sc["sd_cost"][ci]), 4),
                        round(float(sc["pred_comp"][ci]), 4),
                        round(float(sc["pred_fee_load"][ci]), 5),
                        round(float(a * (sc["L1"][ci] if phi == 1.0 else sc["L05"][ci])), 4),
                        round(float(side_cost(rp, ri, side, a, phi)), 4) if ri is not None else "",
                        round(float(1.0 if ri is not None and rp["resid"][ri] == 0 else 0.0), 4) if ri is not None else "",
                        round(float(rp["resid"][ri]), 2) if ri is not None else "",
                        asap_c, asap_comp, asap_r,
                        round(float(side_cost(rp, x_tw, side, a, phi)), 4) if x_tw is not None else "",
                        round(float(side_cost(rp, x_sh, side, a, phi)), 4) if x_sh is not None else "",
                        round(float(orc), 4) if np.isfinite(orc) else "", path,
                        int(round(rp["Q"][ri])) if ri is not None else "",
                        round(float(a * (rp["L1"][ri] if phi == 1.0 else rp["L05"][ri])), 4) if ri is not None else ""])

    header = ["product", "window", "date", "size_idx", "D", "side", "a_bp", "phi", "preference", "budget_k",
              "Q_pred", "status", "recommended_method", "recommended_h", "pred_total_cost_bp",
              "pred_pricepart_bp", "pred_risk_sd_bp", "pred_completion", "pred_fee_load", "pred_impact_bp",
              "real_cost_bp", "real_completion", "real_resid_lots",
              "asap_cost_bp", "asap_completion", "asap_resid_lots",
              "fixed_twap_cost_bp", "fixed_shape_cost_bp", "oracle_cost_bp", "path_changed_vs_asap",
              "Q_replay", "real_impact_bp"]
    with open(os.path.join(RES, _arg("--out-decisions", "selector_decisions_val.csv")), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(header); w.writerows(decisions)
    with open(os.path.join(RES, _arg("--out-fixed", "fixed_h_train.csv")), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "window", "size_idx", "D", "side", "a_bp", "phi", "preference", "budget_k",
                    "family", "h", "n_train", "mean_cost_bp", "completion", "risk_pricepart_sd",
                    "budget_bp", "flag", "reason", "sigma_D_rms_bp"])
        w.writerows(train_rows)
    print(json.dumps({"decisions": len(decisions), "train_rows": len(train_rows)}))


if __name__ == "__main__":
    main()
