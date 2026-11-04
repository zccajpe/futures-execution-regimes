#!/usr/bin/env python3
"""Stage 2: execution value of the activity-adjusted volume forecast.

Pre-registered in volume_activity_20261022/DESIGN.md sections 7-8 and 7.1:
  ACT   = XC.arm_stats(V * s, cumw, Q, D, 0.10, 1.3) + XC.choose(rho=0, a=0.5, ...)
  VOL20 = pred_cache row (unadjusted) + the same choose
  s     = max(0, 1 + beta*(act_pre-1)), beta = 0.5232982690757275 (Stage 1, single fit)
  realised costs come from the existing cap-10 replay cache; the observed day's
  volume/price are never scaled and the full replay is never re-run.

If ACT has no feasible arm on a unit, that unit is NOT back-filled with ASAP/A:
it counts as unexecuted (completion 0, residual = full Q), its cost stays missing,
and any missing plan inside the old E makes the overall verdict INCOMPLETE_COVERAGE.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(STAGE)
CORE = os.path.join(ROOT, "delivery", "futures-execution-decision", "scripts", "exante_core.py")
spec = importlib.util.spec_from_file_location("exante_core", CORE)
XC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(XC)

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import engine as E  # noqa: E402

PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]
ARMS = [XC.arm_name(m, h) for m, h in XC.ARMS]
CAP = 0.10
FEE = 1.3
A_BP = 0.5
RISK_K = 1.0
TOL = 5e-5 + 1e-9
SEED = 20261022
N_BOOT = 2000
SPLITS = {"train": (20260105, 20260430), "val": (20260506, 20260630), "test": (20260701, 20261020)}
EXPECT = {"unit_sides": 148968, "sides": {"train": 60336, "val": 30456, "test": 58176},
          "E": {"train": 37480, "val": 20182, "test": 38344},
          "E_A": {"train": 34002, "val": 17460, "test": 33282}}


def method_of(arm):
    return arm.rstrip("0123456789") if arm else ""


def h_of(arm):
    return int(arm[len(method_of(arm)):]) if arm else None


def split_of(d):
    d = int(d)
    for s, (a, b) in SPLITS.items():
        if a <= d <= b:
            return s
    return "outside"


def load_replay():
    tv = np.load(os.path.join(ROOT, "results", "replay_train_val_t60.npz"), allow_pickle=False)
    te = np.load(os.path.join(ROOT, "results", "replay_test_t60.npz"), allow_pickle=False)
    out = {"keys": np.concatenate([tv["keys"], te["keys"]])}
    for f in ("Q", "resid", "gap_exec", "gap_resid_T60", "fee_load", "L1"):
        out[f] = np.concatenate([tv[f], te[f]])
    return out


def load_frozen():
    rows = {}
    with open(os.path.join(ROOT, "decision_research", "v2", "frozen_A_params.csv")) as fh:
        for r in csv.DictReader(fh):
            rows[(r["product"], r["window"], int(r["size_idx"]), int(r["D"]), r["side"],
                  float(r["a_bp"]), float(r["risk_k"]))] = r
    return rows


def block_bootstrap(diff_by_date, calendar, seed=SEED, n_boot=N_BOOT):
    dates = sorted(calendar)
    block_of = {d: i // 5 for i, d in enumerate(dates)}
    sums, ns = {}, {}
    for d, (s, n) in diff_by_date.items():
        b = block_of[d]
        sums[b] = sums.get(b, 0.0) + s
        ns[b] = ns.get(b, 0) + n
    keys = sorted(sums)
    S = np.array([sums[k] for k in keys], dtype=float)
    N = np.array([ns[k] for k in keys], dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    boot = S[draws].sum(axis=1) / N[draws].sum(axis=1)
    return {"n_blocks": int(len(keys)), "mean": float(S.sum() / N.sum()),
            "ci": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
            "block_ns": [int(x) for x in N],
            "estimand": "sum(block paired costs)/sum(block counts) over 5-day blocks"}


def realised(rep, idx, pi, wi, date, size, D, arm, side):
    if arm is None:
        return None
    m = 0 if arm == "asap0" else METHODS.index(arm.rstrip("0123456789"))
    h = 0 if arm == "asap0" else int(arm[len(arm.rstrip("0123456789")):])
    i = idx.get((pi, wi, date, size, D, m, h))
    if i is None:
        return None
    price = side * (rep["gap_exec"][i] + rep["gap_resid_T60"][i])
    fee = FEE * rep["fee_load"][i]
    imp = A_BP * rep["L1"][i]
    return {"price": float(price), "fee": float(fee), "impact": float(imp),
            "total": float(price + fee + imp), "comp": float(rep["resid"][i] == 0),
            "rq": float(rep["resid"][i] / rep["Q"][i])}


def main() -> None:
    t0 = time.time()
    beta = json.load(open(os.path.join(STAGE, "params_stage1.json")))["beta"]
    params = json.load(open(os.path.join(ROOT, "exante_policy_20261022", "params_exante.json")))
    cells = params["sigma_cells"]
    kappa = float(params["kappa_sigma"])
    feats = np.load(os.path.join(ROOT, "exante_policy_20261022", "features_exante.npz"), allow_pickle=False)
    fk = feats["keys"]
    act = feats["act_pre"]
    fmap = {(int(a), int(b)): i for i, (a, b) in enumerate(fk)}
    pred = np.load(os.path.join(ROOT, "exante_policy_20261022", "pred_cache.npz"), allow_pickle=False)
    pk = pred["keys"]
    pQ = pred["Q"].astype(np.int64)
    pT, pL, pComp, pFee = pred["T"], pred["L"], pred["comp"], pred["fee"]
    frozen = load_frozen()
    rep = load_replay()
    idx = {}
    for i in np.nonzero(rep["keys"][:, 7] == 1)[0]:
        k = rep["keys"][i]
        idx[(int(k[0]), int(k[1]), int(k[2]), int(k[3]), int(k[4]), int(k[5]), int(k[6]))] = i

    series = {}
    for pi, p in enumerate(PRODUCTS):
        for wi, w in enumerate(WINDOWS):
            s = E.load_series(p, w)
            if s is None:
                continue
            lab = E.release_label(p, w)
            if lab not in s.labels:
                continue
            series[(pi, wi)] = {"ser": s, "i0": s.labels.index(lab),
                                "idx": {int(d): j for j, d in enumerate(s.dates)}}

    decisions, outcomes = [], []
    cal = defaultdict(set)
    ref_cache = {}
    for i in range(len(pk)):
        series_id, date, size, D = int(pk[i, 0]), int(pk[i, 1]), int(pk[i, 2]), int(pk[i, 3])
        pi, wi = series_id // 3, series_id % 3
        meta = series.get((pi, wi))
        if meta is None or date not in meta["idx"]:
            raise SystemExit(f"unit not resolvable: series={series_id} date={date}")
        ser, i0 = meta["ser"], meta["i0"]
        di = meta["idx"][date]
        prod, win = PRODUCTS[pi], WINDOWS[wi]
        Q = int(pQ[i])
        sp = split_of(date)
        cal[sp].add(date)
        fi = fmap.get((series_id, date))
        if fi is None:
            raise SystemExit(f"missing act_pre for series={series_id} date={date}")
        s = max(0.0, 1.0 + beta * (float(act[fi]) - 1.0))
        key = (series_id, date)
        if key not in ref_cache:
            ref_cache[key] = XC.reference(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
        ref = ref_cache[key]
        if ref is None:
            raise SystemExit(f"reference missing for series={series_id} date={date}")
        legal = [XC.arm_name(m, h) for m, h in XC.legal_arms(D)]
        arm_idx = {a: ARMS.index(a) for a in legal}
        vol20 = {a: {"T": float(pT[i, j]), "L": float(pL[i, j]),
                     "comp": float(pComp[i, j]), "fee": float(pFee[i, j])} for a, j in arm_idx.items()}
        actp = XC.arm_stats(ref["V"] * s, ref["cumw"], Q, D, CAP, FEE)
        actp = {a: actp[a] for a in legal if a in actp}
        capv_day = np.floor(CAP * ser.volume[di, i0 + 1:i0 + 1 + D]).astype(np.int64)

        for side_name in ("buy", "sell"):
            side = 1 if side_name == "buy" else -1
            brow = frozen.get((prod, win, size, D, side_name, A_BP, RISK_K))
            if brow is None:
                raise SystemExit(f"no frozen budget for {prod}/{win}/{size}/{D}/{side_name}")
            budget = float(brow["budget_bp"])
            cell_a = {a: cells.get(f"{prod}|{win}|{size}|{D}|{a}") for a in actp}
            cell_v = {a: cells.get(f"{prod}|{win}|{size}|{D}|{a}") for a in vol20}
            ch_act = XC.choose(actp, ref["sigma1m"], 0.0, A_BP, side, budget, RISK_K, cell_a,
                               min_comp=0.95, kappa=kappa)
            ch_vol = XC.choose(vol20, ref["sigma1m"], 0.0, A_BP, side, budget, RISK_K, cell_v,
                               min_comp=0.95, kappa=kappa)
            a_arm = XC.arm_name(brow["method"], int(brow["h"])) if brow["status"] == "ok" else None
            in_E = vol20["asap0"]["comp"] >= 0.95
            arms = {"ACT": ch_act["arm"], "VOL20": ch_vol["arm"], "ASAP": "asap0", "A": a_arm}
            dec = {"split": sp, "product": prod, "window": win, "date": date, "size_idx": size, "D": D,
                   "side": side_name, "Q": Q, "in_E": int(in_E), "A_status": brow["status"],
                   "act_pre": float(act[fi]), "s": s, "beta": beta,
                   "act_status": ch_act["status"], "act_arm": ch_act["arm"] or "",
                   "act_feasible": "|".join(ch_act["feasible"]),
                   "vol20_arm": ch_vol["arm"] or "", "vol20_status": ch_vol["status"],
                   "asap_arm": "asap0", "a_arm": a_arm or "",
                   "changed_method": int(method_of(ch_act["arm"]) != method_of(ch_vol["arm"])),
                   "changed_h": int(h_of(ch_act["arm"]) != h_of(ch_vol["arm"]))}
            decisions.append(dec)
            row = dict(dec)
            for tag, arm in arms.items():
                r = realised(rep, idx, pi, wi, date, size, D, arm, side)
                for f in ("price", "fee", "impact", "total", "comp", "rq"):
                    row[f"{tag}_{f}"] = None if r is None else r[f]
            # actual fill-vector difference between ACT and VOL20 (diagnostic)
            def fills_of(arm):
                if arm is None:
                    return None
                m = METHODS.index(arm.rstrip("0123456789"))
                h = int(arm[len(arm.rstrip("0123456789")):])
                tg = XC.schedule(arm.rstrip("0123456789"), h, Q, ref["cumw"], D)
                return XC.simulate(tg, capv_day[None, :])[0]
            fa, fv = fills_of(ch_act["arm"]), fills_of(ch_vol["arm"])
            row["actual_q_path_changed"] = int(fa is not None and fv is not None and not np.array_equal(fa, fv))
            # coverage rule: ACT without a plan is unexecuted, not zero cost
            if ch_act["status"] != "OK":
                row["ACT_total"] = None
                row["ACT_comp"] = 0.0
                row["ACT_rq"] = 1.0
            outcomes.append(row)

    # ---------------- evaluation ----------------
    def is_missing(r):
        return r["ACT_total"] is None

    def compare(rows, bench, calendar):
        pairs = [r for r in rows if r[f"ACT_total"] is not None and r[f"{bench}_total"] is not None]
        by_date = defaultdict(lambda: [0.0, 0])
        for r in pairs:
            by_date[r["date"]][0] += r["ACT_total"] - r[f"{bench}_total"]
            by_date[r["date"]][1] += 1
        boot = block_bootstrap({d: tuple(v) for d, v in by_date.items()}, calendar)
        d_price = np.array([r["ACT_price"] - r[f"{bench}_price"] for r in pairs])
        d_fee = np.array([r["ACT_fee"] - r[f"{bench}_fee"] for r in pairs])
        d_imp = np.array([r["ACT_impact"] - r[f"{bench}_impact"] for r in pairs])
        side_arr = np.array([r["side"] for r in pairs])
        comp_a = np.array([r["ACT_comp"] for r in pairs])
        comp_b = np.array([r[f"{bench}_comp"] for r in pairs])
        px = np.array([r["ACT_price"] for r in pairs])
        py = np.array([r[f"{bench}_price"] for r in pairs])
        sd_ratio = float(px.std(ddof=1) / py.std(ddof=1)) if len(px) > 1 and py.std(ddof=1) > 0 else None
        return {"n_pairs": len(pairs), "n_dates": len(by_date),
                "mean_diff_bp": float(np.mean([r["ACT_total"] - r[f"{bench}_total"] for r in pairs])) if pairs else None,
                "bootstrap": boot,
                "attribution": {"price": float(d_price.mean()), "fee": float(d_fee.mean()),
                                "impact": float(d_imp.mean())},
                "attribution_by_side": {
                    sd: {"n": int((side_arr == sd).sum()),
                         "mean_diff_bp": float(np.mean([r["ACT_total"] - r[f"{bench}_total"] for r in pairs if r["side"] == sd])) if (side_arr == sd).any() else None,
                         "price": float(np.mean([r["ACT_price"] - r[f"{bench}_price"] for r in pairs if r["side"] == sd])) if (side_arr == sd).any() else None,
                         "fee": float(np.mean([r["ACT_fee"] - r[f"{bench}_fee"] for r in pairs if r["side"] == sd])) if (side_arr == sd).any() else None,
                         "impact": float(np.mean([r["ACT_impact"] - r[f"{bench}_impact"] for r in pairs if r["side"] == sd])) if (side_arr == sd).any() else None}
                    for sd in ("buy", "sell")},
                "completion_rule": float(comp_a.mean()) if pairs else None,
                "completion_bench": float(comp_b.mean()) if pairs else None,
                "completion_diff_pp": float(100 * (comp_a.mean() - comp_b.mean())) if pairs else None,
                "sd_ratio": sd_ratio,
                "resid_rule_mean": float(np.mean([r["ACT_rq"] for r in pairs])) if pairs else None,
                "resid_bench_mean": float(np.mean([r[f"{bench}_rq"] for r in pairs])) if pairs else None}

    evaluation = {}
    for split in ("val", "test"):
        rows = [r for r in outcomes if r["split"] == split and r["in_E"] == 1]
        E_rows = rows
        A_rows = [r for r in rows if r["A_status"] == "ok"]
        missing = [r for r in E_rows if is_missing(r)]
        act_vs_asap = compare(E_rows, "ASAP", cal[split])
        act_vs_a = compare(A_rows, "A", cal[split])
        # ACT vs VOL20 on units where both have plans
        both = [r for r in E_rows if r["ACT_total"] is not None and r["VOL20_total"] is not None]
        inc = compare(both, "VOL20", cal[split])
        inc.update({
            "selection_changed": sum(1 for r in both if r["act_arm"] != r["vol20_arm"]),
            "method_changed": sum(1 for r in both if r["changed_method"] == 1),
            "h_changed": sum(1 for r in both if r["changed_h"] == 1),
            "actual_q_path_changed": sum(r["actual_q_path_changed"] for r in both),
        })
        evaluation[split] = {
            "E_units": len(E_rows), "E_with_A": len(A_rows),
            "act_planned": sum(1 for r in E_rows if r["ACT_total"] is not None),
            "act_missing": len(missing),
            "act_coverage": (len(E_rows) - len(missing)) / len(E_rows) if E_rows else None,
            "ACT_vs_ASAP": act_vs_asap, "ACT_vs_A": act_vs_a, "ACT_vs_VOL20": inc,
            "vol20_non_asap": sum(1 for r in E_rows if r["vol20_arm"] != "asap0"),
            "act_non_asap": sum(1 for r in E_rows if r["act_arm"] and r["act_arm"] != "asap0"),
        }

    def criteria(rec):
        if rec is None or rec["n_pairs"] == 0:
            return {"pass": False, "reason": "no paired units"}
        ok_cost = rec["mean_diff_bp"] is not None and rec["mean_diff_bp"] <= -0.5
        ok_ci = rec["bootstrap"]["ci"][1] < 0
        ok_comp = rec["completion_diff_pp"] is not None and rec["completion_diff_pp"] >= -1.0
        ok_sd = rec["sd_ratio"] is not None and rec["sd_ratio"] <= 1.10
        return {"C1_cost": bool(ok_cost), "C2_ci": bool(ok_ci), "C3_completion": bool(ok_comp),
                "C4_sd": bool(ok_sd), "pass": bool(ok_cost and ok_ci and ok_comp and ok_sd)}

    # fixed full-E view: refusals count as unexecuted, not as zero cost
    full_E = {}
    for split in ("val", "test"):
        ev = [r for r in outcomes if r["split"] == split and r["in_E"] == 1]
        miss = [r for r in ev if r["ACT_total"] is None]
        act_comp = [0.0 if r["ACT_total"] is None else r["ACT_comp"] for r in ev]
        act_rq = [1.0 if r["ACT_total"] is None else r["ACT_rq"] for r in ev]
        full_E[split] = {
            "E_units": len(ev), "missing": len(miss),
            "coverage": (len(ev) - len(miss)) / len(ev),
            "completion_ACT_fullE": float(np.mean(act_comp)),
            "completion_ASAP_fullE": float(np.mean([r["ASAP_comp"] for r in ev])),
            "completion_diff_pp_fullE": float(100 * (np.mean(act_comp) - np.mean([r["ASAP_comp"] for r in ev]))),
            "residual_ACT_fullE": float(np.mean(act_rq)),
            "residual_ASAP_fullE": float(np.mean([r["ASAP_rq"] for r in ev])),
            "rejected_group_n": len(miss),
            "rejected_group_ASAP_completion": float(np.mean([r["ASAP_comp"] for r in miss])) if miss else None,
            "rejected_group_ASAP_residual": float(np.mean([r["ASAP_rq"] for r in miss])) if miss else None,
            "selection_changes": sum(1 for r in ev if r["ACT_arm"] and r["VOL20_arm"] and r["ACT_arm"] != r["VOL20_arm"]),
            "method_changes": sum(1 for r in ev if r["ACT_arm"] and r["VOL20_arm"] and r["ACT_arm"] != r["VOL20_arm"]
                                  and r["changed_method"] == 1),
            "duration_changes": sum(1 for r in ev if r["ACT_arm"] and r["VOL20_arm"] and r["ACT_arm"] != r["VOL20_arm"]
                                    and r["changed_h"] == 1),
            "actual_path_changes": sum(r["actual_q_path_changed"] for r in ev
                                       if r["ACT_arm"] and r["VOL20_arm"] and r["ACT_arm"] != r["VOL20_arm"]),
        }
    verdict = {}
    for split in ("val", "test"):
        e = evaluation[split]
        incomplete = e["act_missing"] > 0
        v_asap, v_a = criteria(e["ACT_vs_ASAP"]), criteria(e["ACT_vs_A"])
        verdict[split] = {"incomplete_coverage": bool(incomplete),
                          "ACT_vs_ASAP": v_asap, "ACT_vs_A": v_a,
                          "pass": bool(not incomplete and v_asap["pass"] and v_a["pass"])}
    overall = ("INCOMPLETE_COVERAGE" if any(v["incomplete_coverage"] for v in verdict.values())
               else ("PASS" if all(v["pass"] for v in verdict.values()) else "FAIL"))
    verdict["overall"] = overall

    # ---------------- fixed cases ----------------
    cases = []
    for prod, win in (("RB", "am"), ("CU", "am"), ("IF", "am"), ("SC", "night")):
        cand = sorted([r for r in outcomes if r["split"] == "test" and r["product"] == prod
                       and r["window"] == win and r["size_idx"] == 1 and r["D"] == 60 and r["side"] == "buy"],
                      key=lambda r: r["date"])
        if cand:
            cases.append({"kind": "named", **{k: cand[0][k] for k in
                         ("product", "window", "date", "size_idx", "D", "side", "Q", "act_pre", "s",
                          "act_arm", "vol20_arm", "a_arm", "act_status",
                          "ACT_total", "VOL20_total", "ASAP_total", "A_total")}})
    switch = sorted([r for r in outcomes if r["in_E"] == 1 and r["ACT_total"] is not None
                     and r["VOL20_total"] is not None and r["act_arm"] != r["vol20_arm"]],
                    key=lambda r: (r["date"], r["product"], r["window"], r["size_idx"], r["D"], r["side"]))
    if switch:
        r = switch[0]
        cases.append({"kind": "first_switch", **{k: r[k] for k in
                     ("product", "window", "date", "size_idx", "D", "side", "Q", "act_pre", "s",
                      "act_arm", "vol20_arm", "a_arm", "ACT_total", "VOL20_total", "ASAP_total", "A_total")}})
    refused = sorted([r for r in outcomes if r["in_E"] == 1 and r["act_status"] != "OK" and r["vol20_arm"]],
                     key=lambda r: (r["date"], r["product"], r["window"], r["size_idx"], r["D"], r["side"]))
    if refused:
        r = refused[0]
        cases.append({"kind": "first_refusal", **{k: r[k] for k in
                     ("product", "window", "date", "size_idx", "D", "side", "Q", "act_pre", "s",
                      "act_arm", "vol20_arm", "a_arm", "act_status")}})

    # ---------------- assertions ----------------
    asserts = []

    def check(name, ok, **kw):
        rec = {"check": name, "ok": bool(ok)}
        rec.update(kw)
        asserts.append(rec)

    keys = {(r["product"], r["window"], r["date"], r["size_idx"], r["D"], r["side"]) for r in decisions}
    check("keys_unique", len(keys) == len(decisions), rows=len(decisions))
    check("unit_sides_total", len(decisions) == EXPECT["unit_sides"], got=len(decisions))
    for sp in ("train", "val", "test"):
        check(f"sides:{sp}", sum(1 for r in decisions if r["split"] == sp) == EXPECT["sides"][sp])
        e_count = sum(1 for r in outcomes if r["split"] == sp and r["in_E"] == 1)
        check(f"E:{sp}", e_count == EXPECT["E"][sp], got=e_count)
        a_count = sum(1 for r in outcomes if r["split"] == sp and r["in_E"] == 1 and r["A_status"] == "ok")
        check(f"E_A:{sp}", a_count == EXPECT["E_A"][sp], got=a_count)
    # VOL20 must reproduce the frozen R0 selection on VAL/TEST
    for sp in ("val", "test"):
        path = os.path.join(ROOT, "exante_policy_20261022", f"decisions_{sp}.csv")
        ref_map = {}
        with open(path) as fh:
            for r in csv.DictReader(fh):
                ref_map[(r["product"], r["window"], int(r["date"]), int(r["size"]), int(r["D"]), r["side"])] = r["R0_arm"]
        mine = {(r["product"], r["window"], r["date"], r["size_idx"], r["D"], r["side"]): r["vol20_arm"]
                for r in decisions if r["split"] == sp}
        bad = [k for k in ref_map if (ref_map[k] or "") != (mine.get(k) or "")]
        check(f"vol20_matches_R0:{sp}", len(bad) == 0 and set(ref_map) == set(mine),
              compared=len(ref_map), mismatch=len(bad))
    # realised costs reconcile with the existing per-order outcomes files
    recon_bad = 0
    for sp in ("val", "test"):
        path = os.path.join(ROOT, "exante_policy_20261022", f"outcomes_{sp}.csv")
        cache = {}
        with open(path) as fh:
            for r in csv.DictReader(fh):
                cache[(r["product"], r["window"], int(r["date"]), int(r["size"]), int(r["D"]), r["side"])] = r
        for r in outcomes:
            if r["split"] != sp:
                continue
            k = (r["product"], r["window"], r["date"], r["size_idx"], r["D"], r["side"])
            c = cache.get(k)
            if c is None:
                continue
            for src, dst in (("VOL20", "R0"), ("ASAP", "ASAP")):
                if r[f"{src}_total"] is None:
                    continue
                if abs(float(c[f"{dst}_total"]) - r[f"{src}_total"]) > 1e-9:
                    recon_bad += 1
    check("realised_cost_reconciles", recon_bad == 0, mismatches=recon_bad)
    check("refusals_not_zero_cost",
          all((r["ACT_total"] is None and r["ACT_comp"] == 0.0 and r["ACT_rq"] == 1.0)
              for r in outcomes if r["act_status"] != "OK"))
    rng = np.random.default_rng(SEED)
    sample = [outcomes[i] for i in rng.choice(len(outcomes), size=min(300, len(outcomes)), replace=False)]
    fee_bad = impact_bad = 0
    for r in sample:
        arm = r["act_arm"]
        if not arm or r["ACT_total"] is None:
            continue
        m = METHODS.index(arm.rstrip("0123456789"))
        h = int(arm[len(arm.rstrip("0123456789")):])
        i = idx.get((PRODUCTS.index(r["product"]), WINDOWS.index(r["window"]), r["date"], r["size_idx"],
                     r["D"], m, h))
        if i is None:
            continue
        if abs(FEE * rep["fee_load"][i] - r["ACT_fee"]) > 1e-9:
            fee_bad += 1
        if abs(A_BP * rep["L1"][i] - r["ACT_impact"]) > 1e-9:
            impact_bad += 1
    check("fee_and_impact_not_overridden", fee_bad == 0 and impact_bad == 0,
          fee_mismatch=fee_bad, impact_mismatch=impact_bad, sampled=len(sample))
    # future perturbation must not change ACT state/prediction/feasibility
    probes = []
    for prod, win in (("RB", "am"), ("CU", "am"), ("IF", "am"), ("SC", "night")):
        sid = PRODUCTS.index(prod) * 3 + WINDOWS.index(win)
        meta = series[(sid // 3, sid % 3)]
        ser, i0 = meta["ser"], meta["i0"]
        cand = sorted([r for r in outcomes if r["product"] == prod and r["window"] == win
                       and r["split"] == "test"], key=lambda r: r["date"])
        if not cand:
            probes.append({"product": prod, "window": win, "status": "no_event"})
            continue
        row = cand[0]
        di = meta["idx"][row["date"]]
        fi = fmap[(sid, row["date"])]
        s_val = max(0.0, 1.0 + beta * (float(act[fi]) - 1.0))
        base_ref = XC.reference(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
        base_state = XC.state(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
        vol2, cl2 = ser.volume.copy(), ser.close.copy()
        vol2[di, i0 + 1:] = 1e9
        cl2[di, i0 + 1:] = np.nan
        new_ref = XC.reference(vol2, cl2, ser.mask, ser.labels, i0, di)
        new_state = XC.state(vol2, cl2, ser.mask, ser.labels, i0, di)
        same = (base_state == new_state and np.array_equal(base_ref["V"], new_ref["V"]))
        probes.append({"product": prod, "window": win, "date": row["date"],
                       "feature_invariant": bool(same), "s": s_val})
    check("future_perturbation_act_inputs", len(probes) == 4 and all(p.get("feature_invariant") for p in probes),
          cases=probes)

    out = {
        "stage": "volume_activity_20261022_stage2",
        "beta": beta, "scenario": {"a_bp": A_BP, "k": RISK_K, "cap": CAP, "fee": FEE, "phi": 1.0, "rho": 0.0},
        "rule": "ACT = arm_stats(V*s) + choose ; VOL20 = pred_cache + choose ; realised from the existing cap-10 replay cache",
        "coverage": {sp: {k: evaluation[sp][k] for k in ("E_units", "E_with_A", "act_planned", "act_missing",
                                                         "act_coverage", "vol20_non_asap", "act_non_asap")}
                     for sp in ("val", "test")},
        "evaluation": evaluation,
        "full_E": full_E,
        "verdict": verdict,
        "cases": cases,
        "assertions": asserts,
        "runtime_s": round(time.time() - t0, 1),
        "notes": ["C3 on the full fixed E counts refusals as unexecuted; the planned-only C3 is a diagnostic, not the main criterion",
                  "duration change = parsed h differs; selection may change the method without changing h",
                  "VAL and TEST were already inspected; this is a retrospective time-ordered check",
                  "a is not calibrated; positive-a results are conditional scenarios",
                  "refusals are unexecuted (completion 0, residual Q) and are never counted as zero cost"],
    }
    json.dump(out, open(os.path.join(STAGE, "execution_evaluation.json"), "w"), ensure_ascii=False, indent=1)

    dcols = list(decisions[0].keys())
    with open(os.path.join(STAGE, "execution_decisions.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=dcols)
        w.writeheader()
        w.writerows(decisions)
    ocols = list(outcomes[0].keys())
    with open(os.path.join(STAGE, "outcomes.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ocols)
        w.writeheader()
        w.writerows(outcomes)

    failed = [a["check"] for a in asserts if not a["ok"]]
    print(json.dumps({"verdict": verdict, "coverage": out["coverage"], "assertions_failed": failed,
                      "runtime_s": out["runtime_s"]}, ensure_ascii=False, indent=1))
    if failed:
        print("ASSERTIONS FAILED:", failed, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
