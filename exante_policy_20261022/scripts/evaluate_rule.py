#!/usr/bin/env python3
"""Unified evaluation of the frozen ex-ante rule (R1) and its neutral ablation (R0).

Pre-declared in DESIGN.md sections 5-7:
  * eligible range E is defined from inputs + the ASAP reference only;
  * primary config a=0.5, k=1; VAL and TEST evaluated separately on the same E;
  * criteria C1-C4 are reported, never tuned;
  * A is used exactly as frozen, never re-selected.

Outputs (stage dir):
  decisions_val.csv, decisions_test.csv, evaluation.json
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import sys
from collections import defaultdict

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
CAP = 0.10
FEE = 1.3
CONFIGS = [(0.0, 1.0), (0.0, 2.0), (0.5, 1.0), (0.5, 2.0), (2.0, 1.0), (2.0, 2.0)]
PRIMARY = (0.5, 1.0)
SPLITS = {"val": (20260506, 20260630), "test": (20260701, 20261020)}


def split_of(d):
    d = int(d)
    for s, (a, b) in SPLITS.items():
        if a <= d <= b:
            return s
    return "train"


def load_replay():
    tv = np.load(os.path.join(ROOT, "results", "replay_train_val_t60.npz"), allow_pickle=False)
    te = np.load(os.path.join(ROOT, "results", "replay_test_t60.npz"), allow_pickle=False)
    out = {"keys": np.concatenate([tv["keys"], te["keys"]])}
    for f in ("gap_exec", "gap_resid_T60", "L1", "fee_load", "resid", "Q"):
        out[f] = np.concatenate([tv[f], te[f]])
    return out


def load_frozen():
    rows = {}
    with open(os.path.join(ROOT, "decision_research", "v2", "frozen_A_params.csv")) as fh:
        for r in csv.DictReader(fh):
            rows[(r["product"], r["window"], int(r["size_idx"]), int(r["D"]), r["side"],
                  float(r["a_bp"]), float(r["risk_k"]))] = r
    return rows


def t60_returns():
    """(series, date) -> T60 return in bp, from the same minute arrays used by the replay."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import engine as E
    out = {}
    for pi, p in enumerate(PRODUCTS):
        for wi, w in enumerate(WINDOWS):
            ser = E.load_series(p, w)
            if ser is None:
                continue
            lab = E.release_label(p, w)
            if lab not in ser.labels:
                continue
            i0 = ser.labels.index(lab)
            if i0 + 1 + 60 > len(ser.labels):
                continue
            for di, d in enumerate(ser.dates):
                P0, PT = float(ser.close[di, i0]), float(ser.close[di, i0 + 60])
                if P0 > 0 and PT > 0:
                    out[(pi * 3 + wi, int(d))] = (PT / P0 - 1.0) * 1e4
    return out


def block_bootstrap(vals_by_date, all_dates, n_boot=2000, seed=20261022):
    """5-trading-day block bootstrap that keeps the per-order mean as the estimand.

    Blocks are laid on the WHOLE split calendar (not on the dates that happen to
    have pairs), each block keeps its sum of paired differences and its count, and
    a bootstrap draw reports sum(selected sums) / sum(selected counts). This keeps
    pairing and makes the interval describe the same mu as mean_diff_bp.
    """
    dates = sorted(all_dates)
    block_of = {d: i // 5 for i, d in enumerate(dates)}
    sums, ns = {}, {}
    for d, vs in vals_by_date.items():
        b = block_of[d]
        sums[b] = sums.get(b, 0.0) + float(np.sum(vs))
        ns[b] = ns.get(b, 0) + len(vs)
    keys = sorted(sums)
    S = np.array([sums[k] for k in keys], dtype=float)
    N = np.array([ns[k] for k in keys], dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    boot = S[draws].sum(axis=1) / N[draws].sum(axis=1)
    return {"n_blocks": int(len(keys)), "mean": float(S.sum() / N.sum()),
            "ci": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
            "estimand": "sum(block pair differences) / sum(block order counts) over 5-day blocks of the whole split",
            "block_sums": [float(x) for x in S], "block_ns": [int(x) for x in N]}


def main() -> None:
    params = json.load(open(os.path.join(STAGE, "params_exante.json")))
    edges = params["edges"]
    bucket_rho = [b["rho"] for b in params["buckets"]]
    cells = params["sigma_cells"]
    kappa_sigma = params["kappa_sigma"]

    rep = load_replay()
    k = rep["keys"]
    idx = {}
    for i in np.nonzero(k[:, 7] == 1)[0]:
        idx[(int(k[i, 0]), int(k[i, 1]), int(k[i, 2]), int(k[i, 3]), int(k[i, 4]), int(k[i, 5]), int(k[i, 6]))] = i

    feats = np.load(os.path.join(STAGE, "features_exante.npz"), allow_pickle=False)
    fidx = {(int(a), int(b)): i for i, (a, b) in enumerate(feats["keys"])}
    sig1 = feats["sigma1m"]
    zpre = feats["z_pre"]
    vpre = feats["v_pre"]

    pred = np.load(os.path.join(STAGE, "pred_cache.npz"), allow_pickle=False)
    pk = pred["keys"]
    pidx = {(int(a), int(b), int(c), int(d)): i for i, (a, b, c, d) in enumerate(pk)}
    pT, pL, pcomp, pfee = pred["T"], pred["L"], pred["comp"], pred["fee"]

    frozen = load_frozen()
    r60 = t60_returns()

    unit_rows = []
    for (series, date, si, D), pi_ in pidx.items():
        pi, wi = series // 3, series % 3
        fe = fidx.get((series, date))
        if fe is None:
            continue
        arm_pred = {}
        legal = [a for a in ARMS if a == "asap0" or 5 <= int(a[len(a.rstrip("0123456789")):]) <= D]
        for j, arm in enumerate(ARMS):
            if arm not in legal:
                continue
            arm_pred[arm] = {"T": float(pT[pi_, j]), "L": float(pL[pi_, j]),
                             "comp": float(pcomp[pi_, j]), "fee": float(pfee[pi_, j])}
        unit_rows.append({"series": series, "pi": pi, "wi": wi, "date": date, "size": si, "D": D,
                          "q": pi_, "pred": arm_pred, "sigma1m": float(sig1[fe]),
                          "z": float(zpre[fe]), "v": float(vpre[fe])})
    print(f"units built: {len(unit_rows)}", flush=True)

    decisions = {"val": [], "test": []}
    outcomes = []                                     # per unit-side realised outcomes (primary config)
    store = defaultdict(lambda: defaultdict(list))   # (split,a,k,comparison) -> field -> values

    for u in unit_rows:
        sp = split_of(u["date"])
        if sp not in SPLITS:
            continue
        bucket = C.bucket_index(u["z"], u["v"], edges)
        rho = bucket_rho[bucket]
        prod, win = PRODUCTS[u["pi"]], WINDOWS[u["wi"]]
        for side in (1, -1):
            side_s = "buy" if side > 0 else "sell"
            asap = u["pred"]["asap0"]
            in_E = (asap["comp"] >= 0.95)
            for (a, kk) in CONFIGS:
                frow = frozen.get((prod, win, u["size"], u["D"], side_s, a, kk))
                budget = float(frow["budget_bp"]) if frow and frow.get("budget_bp") else None
                if budget is None:
                    continue
                cell = {arm: cells.get(f"{prod}|{win}|{u['size']}|{u['D']}|{arm}")
                        for arm in u["pred"]}
                ch1 = C.choose(u["pred"], u["sigma1m"], rho, a, side, budget, kk, cell, kappa=kappa_sigma)
                ch0 = C.choose(u["pred"], u["sigma1m"], 0.0, a, side, budget, kk, cell, kappa=kappa_sigma)
                a_arm = None
                if frow and frow["status"] == "ok":
                    a_arm = C.arm_name(frow["method"], int(frow["h"]))

                def realised(arm):
                    if arm is None:
                        return None
                    m = 0 if arm == "asap0" else METHODS.index(arm.rstrip("0123456789"))
                    h = 0 if arm == "asap0" else int(arm[len(arm.rstrip("0123456789")):])
                    i = idx.get((u["pi"], u["wi"], u["date"], u["size"], u["D"], m, h))
                    if i is None:
                        return None
                    price = side * (rep["gap_exec"][i] + rep["gap_resid_T60"][i])
                    fee = FEE * rep["fee_load"][i]
                    imp = a * rep["L1"][i]
                    return {"price": float(price), "fee": float(fee), "impact": float(imp),
                            "total": float(price + fee + imp),
                            "comp": float(rep["resid"][i] == 0),
                            "rq": float(rep["resid"][i] / rep["Q"][i])}

                rec = {"split": sp, "product": prod, "window": win, "date": u["date"], "size": u["size"],
                       "D": u["D"], "side": side_s, "a": a, "k": kk, "bucket": bucket, "in_E": bool(in_E),
                       "rho": rho, "budget": budget,
                       "P": ch1["arm"], "P_status": ch1["status"],
                       "R0": ch0["arm"], "R0_status": ch0["status"],
                       "A": a_arm, "A_status": (frow["status"] if frow else "missing"),
                       "asap": "asap0"}
                if (a, kk) == PRIMARY:
                    decisions[sp].append(rec)

                rr = {"P": realised(ch1["arm"]), "R0": realised(ch0["arm"]),
                      "A": realised(a_arm), "ASAP": realised("asap0")}
                if (a, kk) == PRIMARY:
                    comp_p = (ch1.get("costs") or {}).get(ch1.get("arm"), {}) if ch1.get("arm") else {}
                    row = {"split": sp, "product": prod, "window": win, "date": u["date"],
                           "size": u["size"], "D": u["D"], "side": side_s, "bucket": bucket,
                           "in_E": bool(in_E), "rho": rho, "budget": budget,
                           "P_arm": ch1["arm"], "P_status": ch1["status"],
                           "R0_arm": ch0["arm"], "A_arm": a_arm,
                           "pred_total_bp": ch1.get("predicted_total"), "pred_risk_bp": ch1.get("predicted_risk"),
                           "pred_fee_bp": comp_p.get("fee"), "pred_impact_bp": comp_p.get("impact"),
                           "pred_price_diff_bp": comp_p.get("price_diff_vs_asap"),
                           "asap_pred_completion": asap["comp"]}
                    for tag, val in rr.items():
                        for f in ("price", "fee", "impact", "total", "comp", "rq"):
                            row[f"{tag}_{f}"] = (None if val is None else val[f])
                    outcomes.append(row)

                if not in_E:
                    continue
                for rule in ("P", "R0"):
                    for bench in ("ASAP", "A"):
                        x, y = rr[rule], rr[bench]
                        if x is None or y is None:
                            continue
                        key = (sp, a, kk, f"{rule}-{bench}")
                        store[key]["diff"].append(x["total"] - y["total"])
                        store[key]["date"].append(u["date"])
                        store[key]["side"].append(side_s)
                        store[key]["d_price"].append(x["price"] - y["price"])
                        store[key]["d_fee"].append(x["fee"] - y["fee"])
                        store[key]["d_imp"].append(x["impact"] - y["impact"])
                        store[key]["comp_x"].append(x["comp"])
                        store[key]["comp_y"].append(y["comp"])
                        store[key]["price_x"].append(x["price"])
                        store[key]["price_y"].append(y["price"])
                        store[key]["rq_x"].append(x["rq"])
                        store[key]["rq_y"].append(y["rq"])
                # full-universe descriptive cost for refusals (rule produced no arm in E)
                if ch1["status"] != "OK":
                    key = (sp, a, kk, "P-REFUSED")
                    store[key]["date"].append(u["date"])

    # ---------------- summaries ----------------
    split_dates = {sp: sorted({u["date"] for u in unit_rows if split_of(u["date"]) == sp}) for sp in SPLITS}

    def summarise(recs, all_dates):
        diff = np.array(recs["diff"])
        date = recs["date"]
        side = np.array(recs["side"])
        by_date = defaultdict(list)
        for d, v in zip(date, diff):
            by_date[d].append(v)
        boot = block_bootstrap(by_date, all_dates)
        assert abs(boot["mean"] - float(diff.mean())) < 1e-9, "bootstrap estimand differs from mean_diff_bp"
        comp_x, comp_y = np.array(recs["comp_x"]), np.array(recs["comp_y"])
        px, py = np.array(recs["price_x"]), np.array(recs["price_y"])
        return {
            "n": int(len(diff)),
            "n_dates": int(len(by_date)),
            "mean_diff_bp": float(diff.mean()),
            "boot": boot,
            "attribution": {"price": float(np.mean(recs["d_price"])),
                            "fee": float(np.mean(recs["d_fee"])),
                            "impact": float(np.mean(recs["d_imp"]))},
            "completion_rule": float(comp_x.mean()), "completion_bench": float(comp_y.mean()),
            "completion_diff_pp": float(100 * (comp_x.mean() - comp_y.mean())),
            "price_sd_rule": float(px.std(ddof=1)) if len(px) > 1 else None,
            "price_sd_bench": float(py.std(ddof=1)) if len(py) > 1 else None,
            "sd_ratio": float(px.std(ddof=1) / py.std(ddof=1)) if len(py) > 1 and py.std(ddof=1) > 0 else None,
            "resid_rule_mean": float(np.mean(recs["rq_x"])), "resid_rule_p95": float(np.percentile(recs["rq_x"], 95)),
            "resid_bench_mean": float(np.mean(recs["rq_y"])), "resid_bench_p95": float(np.percentile(recs["rq_y"], 95)),
            "attribution_by_side": {
                sd: {"n": int((side == sd).sum()),
                     "mean_diff_bp": float(diff[side == sd].mean()) if (side == sd).any() else None,
                     "price": float(np.mean(np.array(recs["d_price"])[side == sd])) if (side == sd).any() else None,
                     "fee": float(np.mean(np.array(recs["d_fee"])[side == sd])) if (side == sd).any() else None,
                     "impact": float(np.mean(np.array(recs["d_imp"])[side == sd])) if (side == sd).any() else None,
                     "price_sd_rule": float(np.array(recs["price_x"])[side == sd].std(ddof=1)) if (side == sd).sum() > 1 else None,
                     "price_sd_bench": float(np.array(recs["price_y"])[side == sd].std(ddof=1)) if (side == sd).sum() > 1 else None}
                for sd in ("buy", "sell")},
            "note_price_cancellation": ("with rho=0 both sides use the same arm, so the pooled mean price "
                                        "difference can cancel by construction; read attribution_by_side, not the pooled price figure"),
        }

    results = {}
    for key, recs in store.items():
        sp, a, kk, comp_name = key
        if comp_name == "P-REFUSED":
            results.setdefault(sp, {}).setdefault(f"a={a},k={kk}", {})["P_refused_units_in_E"] = len(recs["date"])
            continue
        results.setdefault(sp, {}).setdefault(f"a={a},k={kk}", {})[comp_name] = summarise(recs, split_dates[sp])

    # coverage
    coverage = {}
    for sp in SPLITS:
        tot_u = len([u for u in unit_rows if split_of(u["date"]) == sp])
        inE_u = len([u for u in unit_rows if split_of(u["date"]) == sp and u["pred"]["asap0"]["comp"] >= 0.95])
        rows = decisions[sp]
        sides = len(rows)
        sides_E = sum(1 for r in rows if r["in_E"])
        sides_E_A = sum(1 for r in rows if r["in_E"] and r["A"] is not None)
        sides_E_P = sum(1 for r in rows if r["in_E"] and r["P_status"] == "OK")
        nonasap = sum(1 for r in rows if r["in_E"] and r["P_status"] == "OK" and r["P"] != "asap0")
        coverage[sp] = {
            "units_total": tot_u, "units_in_E": inE_u,
            "unit_sides_total": sides, "unit_sides_in_E": sides_E,
            "share_sides_in_E": sides_E / sides if sides else None,
            "unit_sides_E_with_A": sides_E_A, "share_E_with_A": sides_E_A / sides_E if sides_E else None,
            "unit_sides_E_with_rule": sides_E_P, "share_E_with_rule": sides_E_P / sides_E if sides_E else None,
            "rule_no_plan_in_E": sides_E - sides_E_P,
            "E_non_asap_share": nonasap / sides_E_P if sides_E_P else None,
        }
        for k_, v_ in list(coverage[sp].items()):
            if k_.startswith("share_") or k_.endswith("_share"):
                assert v_ is None or (0.0 <= v_ <= 1.0), f"coverage share out of range: {sp}.{k_}={v_}"

    # outside E: does ASAP actually complete when the model says completion < 1?
    outside = {}
    for sp in SPLITS:
        outs = []
        for u in unit_rows:
            if split_of(u["date"]) != sp or u["pred"]["asap0"]["comp"] >= 0.95:
                continue
            for side in (1, -1):
                i = idx.get((u["pi"], u["wi"], u["date"], u["size"], u["D"], 0, 0))
                if i is None:
                    continue
                outs.append((float(rep["resid"][i] == 0), float(rep["resid"][i] / rep["Q"][i])))
        outside[sp] = {"unit_sides": len(outs),
                       "asap_realised_completion": float(np.mean([o[0] for o in outs])) if outs else None,
                       "asap_mean_resid_frac": float(np.mean([o[1] for o in outs])) if outs else None}

    # bucket / product / window / D / size attribution for the primary config
    def grouped(recs, keyfn):
        out = {}
        for r in recs:
            out.setdefault(keyfn(r), []).append(r)
        return out

    def pair_stats(rows, rule="P", bench="ASAP"):
        diffs = [x[rule + "_cost"] - x[bench + "_cost"] for x in rows if x.get(rule + "_cost") is not None and x.get(bench + "_cost") is not None]
        return {"n": len(diffs), "mean_diff_bp": float(np.mean(diffs)) if diffs else None,
                "n_dates": len({x["date"] for x in rows})}

    # recompute per-unit costs for the primary config tables
    prim = {"val": [], "test": []}
    for u in unit_rows:
        sp = split_of(u["date"])
        if sp not in SPLITS:
            continue
        bucket = C.bucket_index(u["z"], u["v"], edges)
        rho = bucket_rho[bucket]
        prod, win = PRODUCTS[u["pi"]], WINDOWS[u["wi"]]
        for side in (1, -1):
            side_s = "buy" if side > 0 else "sell"
            if u["pred"]["asap0"]["comp"] < 0.95:
                continue
            a, kk = PRIMARY
            frow = frozen.get((prod, win, u["size"], u["D"], side_s, a, kk))
            if not frow or not frow.get("budget_bp"):
                continue
            budget = float(frow["budget_bp"])
            cell = {arm: cells.get(f"{prod}|{win}|{u['size']}|{u['D']}|{arm}") for arm in u["pred"]}
            ch1 = C.choose(u["pred"], u["sigma1m"], rho, a, side, budget, kk, cell, kappa=kappa_sigma)
            ch0 = C.choose(u["pred"], u["sigma1m"], 0.0, a, side, budget, kk, cell, kappa=kappa_sigma)
            a_arm = C.arm_name(frow["method"], int(frow["h"])) if frow["status"] == "ok" else None

            def realised(arm):
                if arm is None:
                    return None
                m = 0 if arm == "asap0" else METHODS.index(arm.rstrip("0123456789"))
                h = 0 if arm == "asap0" else int(arm[len(arm.rstrip("0123456789")):])
                i = idx.get((u["pi"], u["wi"], u["date"], u["size"], u["D"], m, h))
                if i is None:
                    return None
                return float(side * (rep["gap_exec"][i] + rep["gap_resid_T60"][i]) + FEE * rep["fee_load"][i] + a * rep["L1"][i])

            comps = (ch1.get("costs") or {}).get(ch1.get("arm"), {}) if ch1.get("arm") else {}
            prim[sp].append({"product": prod, "window": win, "date": u["date"], "D": u["D"],
                             "size": u["size"], "side": side_s, "bucket": bucket,
                             "P_cost": realised(ch1["arm"]), "R0_cost": realised(ch0["arm"]),
                             "ASAP_cost": realised("asap0"), "A_cost": realised(a_arm),
                             "P_arm": ch1["arm"], "R0_arm": ch0["arm"], "A_arm": a_arm,
                             "Q": int(u["pred"] and rep["Q"][idx[(u["pi"], u["wi"], u["date"], u["size"], u["D"], 0, 0)]]),
                             "rho": rho, "budget": budget, "asap_pred_comp": u["pred"]["asap0"]["comp"],
                             "pred_total": ch1.get("predicted_total"), "pred_risk": ch1.get("predicted_risk"),
                             "pred_fee": comps.get("fee"), "pred_impact": comps.get("impact"),
                             "pred_price_diff": comps.get("price_diff_vs_asap")})

    attribution = {}
    for sp in SPLITS:
        rows = prim[sp]
        attribution[sp] = {
            "by_bucket": {f"b{k}": pair_stats(v) for k, v in grouped(rows, lambda r: r["bucket"]).items()},
            "by_product": {k: pair_stats(v) for k, v in grouped(rows, lambda r: r["product"]).items()},
            "by_window": {k: pair_stats(v) for k, v in grouped(rows, lambda r: r["window"]).items()},
            "by_D": {k: pair_stats(v) for k, v in grouped(rows, lambda r: r["D"]).items()},
            "by_size": {k: pair_stats(v) for k, v in grouped(rows, lambda r: r["size"]).items()},
            "R1_minus_R0": {
                "n": len([r for r in rows if r["P_cost"] is not None and r["R0_cost"] is not None]),
                "mean_diff_bp": float(np.mean([r["P_cost"] - r["R0_cost"] for r in rows if r["P_cost"] is not None and r["R0_cost"] is not None])) if rows else None,
            },
        }

    # cases: pre-declared selection (first eligible TEST date per named product + first refusal)
    def case_row(r, kind, note):
        return {"kind": kind, "product": r["product"], "window": r["window"], "date": r["date"],
                "size": r["size"], "D": r["D"], "side": r["side"], "bucket": r["bucket"],
                "Q": r.get("Q"), "rho": r.get("rho"), "budget": r.get("budget"),
                "rule_arm": r["P_arm"], "R0_arm": r["R0_arm"], "A_arm": r["A_arm"],
                "pred_total_bp": None if r.get("pred_total") is None else round(r["pred_total"], 4),
                "pred_fee_bp": None if r.get("pred_fee") is None else round(r["pred_fee"], 4),
                "pred_impact_bp": None if r.get("pred_impact") is None else round(r["pred_impact"], 4),
                "pred_price_diff_bp": None if r.get("pred_price_diff") is None else round(r["pred_price_diff"], 4),
                "pred_risk_bp": None if r.get("pred_risk") is None else round(r["pred_risk"], 4),
                "asap_pred_completion": r.get("asap_pred_comp"),
                "cost_rule": None if r["P_cost"] is None else round(r["P_cost"], 4),
                "cost_asap": None if r["ASAP_cost"] is None else round(r["ASAP_cost"], 4),
                "cost_A": None if r["A_cost"] is None else round(r["A_cost"], 4),
                "note": note}

    cases = []
    # pre-declared case rule A: central configuration (size 1%, D=60, buy), first
    # eligible TEST date for each named product. Nothing here depends on profit.
    for prod in ("RB", "CU", "IF", "SC"):
        cand = sorted([r for r in prim["test"] if r["product"] == prod and r["size"] == 1
                       and r["D"] == 60 and r["side"] == "buy"], key=lambda r: r["date"])
        if cand:
            cases.append(case_row(cand[0], "typical",
                                  "first TEST date for this product at size=1%, D=60, buy"))
    # pre-declared case rule B: first TEST unit (date, product order) where the rule
    # actually leaves ASAP, i.e. where its state/impact reasoning bites.
    for r in sorted(prim["test"], key=lambda r: (r["date"], PRODUCTS.index(r["product"]),
                                                 WINDOWS.index(r["window"]), r["size"], r["D"], r["side"])):
        if r["P_arm"] and r["P_arm"] != "asap0":
            cases.append(case_row(r, "switch", "first TEST unit where the rule does not pick ASAP"))
            break
    # pre-declared case rule C: first TEST unit outside E (cannot promise completion).
    ref = sorted([u for u in unit_rows if split_of(u["date"]) == "test" and u["pred"]["asap0"]["comp"] < 0.95],
                 key=lambda u: (u["date"], u["pi"], u["size"], u["D"]))
    if ref:
        u = ref[0]
        i = idx.get((u["pi"], u["wi"], u["date"], u["size"], u["D"], 0, 0))
        cases.append({"kind": "outside_E", "product": PRODUCTS[u["pi"]], "window": WINDOWS[u["wi"]],
                      "date": u["date"], "size": u["size"], "D": u["D"],
                      "asap_pred_completion": round(u["pred"]["asap0"]["comp"], 4),
                      "asap_realised_completed": bool(rep["resid"][i] == 0) if i is not None else None,
                      "asap_realised_resid_frac": round(float(rep["resid"][i] / rep["Q"][i]), 4) if i is not None else None,
                      "note": "first TEST unit outside E: the model cannot promise completion"})

    def verdict_for(rec):
        if not rec:
            return None
        return {"C1_cost_improvement": bool(rec["mean_diff_bp"] <= -0.5),
                "C2_ci_upper_below_zero": bool(rec["boot"]["ci"][1] < 0),
                "C3_completion_not_down_1pp": bool(rec["completion_diff_pp"] >= -1.0),
                "C4_sd_ratio_le_1_10": bool(rec["sd_ratio"] is not None and rec["sd_ratio"] <= 1.10),
                "pass": bool(rec["mean_diff_bp"] <= -0.5 and rec["boot"]["ci"][1] < 0
                             and rec["completion_diff_pp"] >= -1.0
                             and rec["sd_ratio"] is not None and rec["sd_ratio"] <= 1.10)}

    verdict = {}
    for sp in SPLITS:
        key = f"a={PRIMARY[0]},k={PRIMARY[1]}"
        r = results.get(sp, {}).get(key, {})
        verdict[sp] = {b: verdict_for(r.get(f"P-{b}")) for b in ("ASAP", "A")}
        verdict[sp]["both_benchmarks_pass"] = bool(all((verdict[sp][b] or {}).get("pass") for b in ("ASAP", "A")))
    verdict["overall_pass"] = bool(all(verdict[sp]["both_benchmarks_pass"] for sp in SPLITS))

    out = {
        "stage": "exante_policy_20261022",
        "outside_E": outside,
        "verdict_primary": verdict,
        "params": {"kappa_rho": params["kappa_rho"], "enabled_buckets": params["enabled_buckets"],
                   "train_window": params["train_window"], "edges": edges,
                   "bucket_rho": bucket_rho},
        "primary": {"a": PRIMARY[0], "k": PRIMARY[1]},
        "coverage": coverage,
        "coverage_assertions_ok": True,
        "outcomes_files": {sp: f"outcomes_{sp}.csv" for sp in SPLITS},
        "results": results,
        "attribution_primary": attribution,
        "cases": cases,
    }
    with open(os.path.join(STAGE, "evaluation.json"), "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    oc_cols = (["split", "product", "window", "date", "size", "D", "side", "bucket", "in_E", "rho", "budget",
                "P_arm", "P_status", "R0_arm", "A_arm",
                "pred_total_bp", "pred_risk_bp", "pred_fee_bp", "pred_impact_bp", "pred_price_diff_bp",
                "asap_pred_completion"]
               + [f"{t}_{f}" for t in ("P", "R0", "A", "ASAP")
                  for f in ("price", "fee", "impact", "total", "comp", "rq")])
    for sp in SPLITS:
        with open(os.path.join(STAGE, f"outcomes_{sp}.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=oc_cols)
            w.writeheader()
            w.writerows([r for r in outcomes if r["split"] == sp])

    for sp in SPLITS:
        with open(os.path.join(STAGE, f"decisions_{sp}.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["split", "product", "window", "date", "size", "D", "side", "bucket", "in_E",
                        "rho", "P_arm", "P_status", "R0_arm", "A_arm", "A_status", "budget"])
            for r in decisions[sp]:
                w.writerow([r["split"], r["product"], r["window"], r["date"], r["size"], r["D"], r["side"],
                            r["bucket"], int(r["in_E"]), r["rho"], r["P"], r["P_status"], r["R0"], r["A"],
                            r["A_status"], r["budget"]])

    print(json.dumps({"coverage": coverage,
                      "primary": {sp: results[sp]["a=0.5,k=1.0"] for sp in SPLITS}},
                     ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
