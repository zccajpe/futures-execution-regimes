#!/usr/bin/env python3
"""Stage 0 (implementation revision 1): choice-space audit.

Fix log for this revision (see REVIEW_CORRECTIONS.md):
  R1  risk cells were looked up with the wrong key -> prior was used everywhere.
      Now each unit builds cell = {arm: cells.get(f"{prod}|{win}|{size}|{D}|{arm}")}
      exactly like exante_policy_20261022/scripts/evaluate_rule.py, and the
      feasible set is re-derived with XC.choose(rho=0, a=0.5) and asserted equal.
  R2  the "E_with_A_defined" bucket was really "A defined, E not applied".
      Now E ∩ A and E-with-A-missing are separate, and VAL/TEST totals are
      cross-checked against the original evaluation pairing range.
  R3  distinct-path counts mixed all-legal and final-feasible scopes.
      Now both scopes are reported with explicit names; the gate uses
      final-feasible historical fill matrices in TRAIN ∩ old E only.
  R4  no hard-coded "future not used" flag: four fixed perturbation cases are
      compared on ref history, arm statistics, risk, feasible set and gate input.
  R5  all assertions fail the run (non-zero exit); totals 74,484 units /
      148,968 unit-sides / 77-39-74 dates / E / E∩A are checked.

Nothing is fitted: no beta, no forecast evaluation, no selection evaluation.
"""
from __future__ import annotations

import csv
import importlib.util
import json
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
ARMS = list(XC.ARMS)
ARM_NAMES = [XC.arm_name(m, h) for m, h in ARMS]
CAP = 0.10
A_BP = 0.5
RISK_K = 1.0
TOL = 5e-5 + 1e-9
SPLITS = {"train": (20260105, 20260430), "val": (20260506, 20260630), "test": (20260701, 20261020)}
EXPECT = {"units": 74484, "unit_sides": 148968,
          "sides": {"train": 60336, "val": 30456, "test": 58176},
          "dates": {"train": 77, "val": 39, "test": 74},
          "E": {"train": 37480, "val": 20182, "test": 38344},
          "E_A": {"val": 17460, "test": 33282}}


def split_of(d):
    d = int(d)
    for s, (a, b) in SPLITS.items():
        if a <= d <= b:
            return s
    return "outside"


def load_frozen():
    rows = {}
    with open(os.path.join(ROOT, "decision_research", "v2", "frozen_A_params.csv")) as fh:
        for r in csv.DictReader(fh):
            rows[(r["product"], r["window"], int(r["size_idx"]), int(r["D"]), r["side"],
                  float(r["a_bp"]), float(r["risk_k"]))] = r
    return rows


def path_variants(d):
    return len({v.tobytes() for v in d.values()})


def side_sign(side):
    return 1 if side == "buy" else -1


def pipeline(volume, close, mask, labels, i0, di, Q, D, prod, win, size, side, cells, frozen, kappa):
    """Full pre-decision pipeline for one unit-side (used by the perturbation checks)."""
    ref = XC.reference(volume, close, mask, labels, i0, di)
    if ref is None:
        return None
    legal = [XC.arm_name(m, h) for m, h in XC.legal_arms(D)]
    pred = XC.arm_stats(ref["V"], ref["cumw"], Q, D, CAP)
    pred = {a: pred[a] for a in legal if a in pred}
    brow = frozen.get((prod, win, size, D, side, A_BP, RISK_K))
    if brow is None:
        return None
    budget = float(brow["budget_bp"])
    cell = {a: cells.get(f"{prod}|{win}|{size}|{D}|{a}") for a in pred}
    risk = {a: XC.predicted_risk(pred, a, budget, RISK_K, cell, kappa=kappa) for a in pred}
    ch = XC.choose(pred, ref["sigma1m"], 0.0, A_BP, side_sign(side), budget, RISK_K,
                   cell, min_comp=0.95, kappa=kappa)
    capv_hist = np.floor(CAP * ref["V"][:, :D]).astype(np.int64)
    hist = {}
    for a in pred:
        method = a.rstrip("0123456789")
        h = int(a[len(method):])
        hist[a] = XC.simulate(XC.schedule(method, h, Q, ref["cumw"], D), capv_hist)
    return {"ref_V": ref["V"], "ref_cumw": ref["cumw"], "sigma1m": ref["sigma1m"],
            "risk": risk, "feasible": ch["feasible"], "arm": ch["arm"], "pred": pred, "hist": hist}


def main() -> None:
    t0 = time.time()
    params = json.load(open(os.path.join(ROOT, "exante_policy_20261022", "params_exante.json")))
    cells = params["sigma_cells"]
    kappa = float(params["kappa_sigma"])
    pred = np.load(os.path.join(ROOT, "exante_policy_20261022", "pred_cache.npz"), allow_pickle=False)
    pk = pred["keys"]
    pQ = pred["Q"].astype(np.int64)
    pT, pL, pComp, pFee = pred["T"], pred["L"], pred["comp"], pred["fee"]
    frozen = load_frozen()

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

    rows = []
    cats = defaultdict(Counter)
    dates_by = defaultdict(set)
    excl_completion = Counter()
    excl_risk = Counter()
    excl_completion_split = defaultdict(Counter)
    excl_risk_split = defaultdict(Counter)
    dominance_violations = []
    choose_mismatch = []
    skipped = Counter()
    ref_cache = {}
    n_units = 0

    for i in range(len(pk)):
        series_id, date, size, D = int(pk[i, 0]), int(pk[i, 1]), int(pk[i, 2]), int(pk[i, 3])
        pi, wi = series_id // 3, series_id % 3
        if (pi, wi) not in series:
            skipped["series_missing"] += 1
            continue
        meta = series[(pi, wi)]
        ser, i0 = meta["ser"], meta["i0"]
        di = meta["idx"].get(date)
        if di is None:
            skipped["date_missing"] += 1
            continue
        prod, win = PRODUCTS[pi], WINDOWS[wi]
        Q = int(pQ[i])
        legal = [XC.arm_name(m, h) for m, h in XC.legal_arms(D)]
        arm_idx = {a: ARM_NAMES.index(a) for a in legal}
        pred_arms = {a: {"T": float(pT[i, j]), "L": float(pL[i, j]),
                         "comp": float(pComp[i, j]), "fee": float(pFee[i, j])}
                     for a, j in arm_idx.items()}
        comp = {a: pred_arms[a]["comp"] for a in legal}

        key = (series_id, date)
        if key not in ref_cache:
            ref_cache[key] = XC.reference(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
        ref = ref_cache[key]
        if ref is None:
            skipped["reference_missing"] += 1
            continue
        capv_hist = np.floor(CAP * ref["V"][:, :D]).astype(np.int64)
        capv_day = np.floor(CAP * ser.volume[di, i0 + 1:i0 + 1 + D]).astype(np.int64)
        targets, fills_hist, fills_day = {}, {}, {}
        for a in legal:
            method = a.rstrip("0123456789")
            h = int(a[len(method):])
            tg = XC.schedule(method, h, Q, ref["cumw"], D)
            targets[a] = tg
            fills_hist[a] = XC.simulate(tg, capv_hist)
            fills_day[a] = XC.simulate(tg, capv_day[None, :])[0]
        cum_hist = {a: np.cumsum(fills_hist[a], axis=1) for a in legal}
        cum_day = {a: np.cumsum(fills_day[a]) for a in legal}
        dom_ok = True
        for a in legal:
            if a == "asap0":
                continue
            if (cum_day["asap0"] < cum_day[a] - 1e-12).any() or (cum_hist["asap0"] < cum_hist[a] - 1e-12).any():
                dom_ok = False
                dominance_violations.append({"series": series_id, "date": date, "size": size, "D": D, "arm": a})
        n_units += 1
        sp = split_of(date)

        all_t = path_variants(targets)
        all_h = path_variants(fills_hist)
        all_a = path_variants(fills_day)

        for side in ("buy", "sell"):
            brow = frozen.get((prod, win, size, D, side, A_BP, RISK_K))
            if brow is None:
                skipped["frozen_row_missing"] += 1
                continue
            budget = float(brow["budget_bp"])
            cell = {a: cells.get(f"{prod}|{win}|{size}|{D}|{a}") for a in pred_arms}
            risk = {a: XC.predicted_risk(pred_arms, a, budget, RISK_K, cell, kappa=kappa) for a in legal}
            comp_ok = [a for a in legal if comp[a] >= 0.95]
            risk_ok = [a for a in comp_ok if risk[a] <= budget + TOL]
            final = risk_ok
            ch = XC.choose(pred_arms, ref["sigma1m"], 0.0, A_BP, side_sign(side), budget, RISK_K,
                           cell, min_comp=0.95, kappa=kappa)
            if sorted(ch["feasible"]) != sorted(final):
                choose_mismatch.append({"series": series_id, "date": date, "size": size, "D": D, "side": side,
                                        "manual": sorted(final), "choose": sorted(ch["feasible"])})
            for a in legal:
                if comp[a] < 0.95:
                    excl_completion[a] += 1
                    excl_completion_split[sp][a] += 1
                elif risk[a] > budget + TOL:
                    excl_risk[a] += 1
                    excl_risk_split[sp][a] += 1
            if not final:
                cat = "NONE"
            elif final == ["asap0"]:
                cat = "ONLY_ASAP"
            elif "asap0" in final:
                cat = "ASAP_PLUS"
            else:
                cat = "ONLY_NON_ASAP"
            in_E = comp["asap0"] >= 0.95
            A_status = brow["status"]
            fin_t = path_variants({a: targets[a] for a in final}) if len(final) >= 2 else (1 if final else 0)
            fin_h = path_variants({a: fills_hist[a] for a in final}) if len(final) >= 2 else (1 if final else 0)
            fin_a = path_variants({a: fills_day[a] for a in final}) if len(final) >= 2 else (1 if final else 0)
            rows.append({"split": sp, "product": prod, "window": win, "date": date, "size_idx": size,
                         "D": D, "side": side, "Q": Q, "in_E": int(in_E), "A_status": A_status,
                         "n_legal": len(legal), "n_comp_ok": len(comp_ok), "n_risk_ok": len(risk_ok),
                         "n_final": len(final), "category": cat, "feasible_arms": "|".join(final),
                         "chosen_arm": ch["arm"] or "",
                         "n_excl_completion": len(legal) - len(comp_ok),
                         "n_excl_risk": len(comp_ok) - len(risk_ok),
                         "all_legal_distinct_targets": all_t, "all_legal_distinct_hist_fills": all_h,
                         "all_legal_distinct_actual_fills": all_a,
                         "final_distinct_targets": fin_t, "final_distinct_hist_fills": fin_h,
                         "final_distinct_actual_fills": fin_a,
                         "asap_dominates": int(dom_ok)})
            cats[sp][cat] += 1
            dates_by[sp].add(date)
            if in_E:
                cats[sp + "_E"][cat] += 1
                dates_by[sp + "_E"].add(date)
                if A_status == "ok":
                    cats[sp + "_E_A"][cat] += 1
                    dates_by[sp + "_E_A"].add(date)
                else:
                    cats[sp + "_E_noA"][cat] += 1
            if A_status == "ok":
                cats[sp + "_A_all"][cat] += 1
        if n_units % 10000 == 0:
            print(f"units={n_units} rows={len(rows)} t={time.time()-t0:.0f}s", flush=True)

    def summarise(scope):
        c = cats[scope]
        tot = sum(c.values())
        return {"total": tot,
                **{k: int(c.get(k, 0)) for k in ("ONLY_ASAP", "ASAP_PLUS", "ONLY_NON_ASAP", "NONE")},
                "shares": {k: (c.get(k, 0) / tot if tot else None)
                           for k in ("ONLY_ASAP", "ASAP_PLUS", "ONLY_NON_ASAP", "NONE")},
                "n_dates": len(dates_by[scope])}

    summary = {}
    for sp in ("train", "val", "test"):
        summary[sp] = {
            "all_inputs": summarise(sp),
            "old_E": summarise(sp + "_E"),
            "E_with_A_defined": summarise(sp + "_E_A"),
            "E_with_A_missing": summarise(sp + "_E_noA"),
            "A_defined_all_units": summarise(sp + "_A_all"),
        }

    # single pass over rows: no O(N^2) scan
    gate_units = {}
    multi = set()
    E_units_train = set()
    for r in rows:
        if r["split"] != "train" or r["in_E"] != 1:
            continue
        key = (r["product"], r["window"], r["date"], r["size_idx"], r["D"])
        E_units_train.add(key)
        if r["n_final"] >= 2:
            multi.add(key)
        v = r["final_distinct_hist_fills"]
        if v > gate_units.get(key, 0):
            gate_units[key] = v
    gate_distinct = sorted(k for k, v in gate_units.items() if v >= 2)
    gate = {
        "rule": "TRAIN and old E only: exists a unit with >=2 final-feasible arms whose 20-path historical fill matrices differ",
        "units_in_E": len(E_units_train),
        "units_with_2plus_final": len(multi),
        "units_with_2plus_distinct_paths": len(gate_distinct),
        "share_of_E": (len(gate_distinct) / len(E_units_train)) if E_units_train else None,
        "dates_with_choice": len({k[2] for k in gate_distinct}),
        "status": "READY_FOR_FORECAST_REVIEW" if gate_distinct else "STOP_NO_CHOICE_SPACE",
        "examples": [{"product": k[0], "window": k[1], "date": k[2], "size": k[3], "D": k[4]}
                     for k in sorted(gate_distinct)[:10]],
    }

    decision_cmp = {}
    for sp in ("val", "test"):
        path = os.path.join(ROOT, "exante_policy_20261022", f"decisions_{sp}.csv")
        ref_map = {}
        with open(path) as fh:
            for r in csv.DictReader(fh):
                ref_map[(r["product"], r["window"], int(r["date"]), int(r["size"]), int(r["D"]), r["side"])] = r["R0_arm"]
        mine = {(r["product"], r["window"], r["date"], r["size_idx"], r["D"], r["side"]): r["chosen_arm"]
                for r in rows if r["split"] == sp}
        keys_ref, keys_mine = set(ref_map), set(mine)
        bad = [k for k in (keys_ref & keys_mine) if (ref_map[k] or "") != (mine[k] or "")]
        decision_cmp[sp] = {"compared": len(keys_ref & keys_mine), "mismatch": len(bad),
                            "keys_only_in_reference": len(keys_ref - keys_mine),
                            "keys_only_in_stage0": len(keys_mine - keys_ref),
                            "key_sets_equal": keys_ref == keys_mine,
                            "examples": [list(k) for k in bad[:5]]}

    probe = []
    for (prod, win) in (("RB", "am"), ("CU", "am"), ("IF", "am"), ("SC", "night")):
        pi, wi = PRODUCTS.index(prod), WINDOWS.index(win)
        meta = series.get((pi, wi))
        if meta is None:
            probe.append({"product": prod, "window": win, "status": "series_not_available"})
            continue
        ser, i0 = meta["ser"], meta["i0"]
        # pk columns are (series, date, size, D): the date is column 1
        avail = sorted({int(pk[i, 1]) for i in range(len(pk)) if int(pk[i, 0]) == pi * 3 + wi})
        if not avail:
            probe.append({"product": prod, "window": win, "status": "no_date"})
            continue
        date = 20260701 if 20260701 in avail else avail[-1]
        di = meta["idx"][date]
        size, D = 1, 60
        j = np.nonzero((pk[:, 0] == pi * 3 + wi) & (pk[:, 1] == date) & (pk[:, 2] == size) & (pk[:, 3] == D))[0]
        if not len(j):
            probe.append({"product": prod, "window": win, "date": date, "status": "no_unit"})
            continue
        Q = int(pQ[j[0]])
        base = pipeline(ser.volume, ser.close, ser.mask, ser.labels, i0, di, Q, D, prod, win, size, "buy",
                        cells, frozen, kappa)
        vol2 = ser.volume.copy()
        cl2 = ser.close.copy()
        vol2[di, i0 + 1:] = np.where(np.arange(vol2.shape[1] - i0 - 1) % 2 == 0, 1e9, 0.0)
        cl2[di, i0 + 1:] = np.where(np.arange(cl2.shape[1] - i0 - 1) % 3 == 0, np.nan, 1e6)
        pert = pipeline(vol2, cl2, ser.mask, ser.labels, i0, di, Q, D, prod, win, size, "buy",
                        cells, frozen, kappa)
        same = {
            "ref_history": bool(np.array_equal(base["ref_V"], pert["ref_V"]) and np.array_equal(base["ref_cumw"], pert["ref_cumw"])),
            "arm_stats": bool(base["pred"] == pert["pred"]),
            "risk": bool(base["risk"] == pert["risk"]),
            "feasible_set": bool(sorted(base["feasible"]) == sorted(pert["feasible"])),
            "chosen_arm": base["arm"] == pert["arm"],
            "gate_input": bool(all(np.array_equal(base["hist"][a], pert["hist"][a]) for a in base["hist"])),
        }
        vol3 = ser.volume.copy()
        vol3[di - 1, i0 + 1:i0 + 61] = 1e9
        neg = pipeline(vol3, ser.close, ser.mask, ser.labels, i0, di, Q, D, prod, win, size, "buy",
                       cells, frozen, kappa)
        probe.append({"product": prod, "window": win, "date": date, "size": size, "D": D, "Q": Q,
                      "status": "ok", "invariant": same, "all_invariant": all(same.values()),
                      "negative_control_changes_ref": bool(neg is None or not np.array_equal(base["ref_V"], neg["ref_V"]))})

    asserts = []

    def check(name, ok, **kw):
        rec = {"check": name, "ok": bool(ok)}
        rec.update(kw)
        asserts.append(rec)

    check("units_total", n_units == EXPECT["units"], got=n_units)
    check("unit_sides_total", len(rows) == EXPECT["unit_sides"], got=len(rows))
    for sp in ("train", "val", "test"):
        check(f"sides:{sp}", sum(1 for r in rows if r["split"] == sp) == EXPECT["sides"][sp],
              got=sum(1 for r in rows if r["split"] == sp))
        check(f"dates:{sp}", len({r["date"] for r in rows if r["split"] == sp}) == EXPECT["dates"][sp],
              got=len({r["date"] for r in rows if r["split"] == sp}))
        check(f"E:{sp}", summary[sp]["old_E"]["total"] == EXPECT["E"][sp], got=summary[sp]["old_E"]["total"])
    for sp in ("val", "test"):
        check(f"E_A:{sp}", summary[sp]["E_with_A_defined"]["total"] == EXPECT["E_A"][sp],
              got=summary[sp]["E_with_A_defined"]["total"])
    for sp in ("train", "val", "test"):
        for sc in ("all_inputs", "old_E", "E_with_A_defined", "E_with_A_missing", "A_defined_all_units"):
            s = summary[sp][sc]
            check(f"categories_sum:{sp}:{sc}",
                  s["ONLY_ASAP"] + s["ASAP_PLUS"] + s["ONLY_NON_ASAP"] + s["NONE"] == s["total"], total=s["total"])
    check("E_subset_of_all", all(summary[sp]["old_E"]["total"] <= summary[sp]["all_inputs"]["total"] for sp in summary))
    check("E_A_plus_E_noA_equals_E",
          all(summary[sp]["E_with_A_defined"]["total"] + summary[sp]["E_with_A_missing"]["total"]
              == summary[sp]["old_E"]["total"] for sp in summary))
    check("asap_weak_dominance", len(dominance_violations) == 0, n_violations=len(dominance_violations))
    check("choose_matches_manual_feasible", len(choose_mismatch) == 0, n_mismatch=len(choose_mismatch))
    check("matches_original_selection:val",
          decision_cmp["val"]["mismatch"] == 0 and decision_cmp["val"]["key_sets_equal"], **decision_cmp["val"])
    check("matches_original_selection:test",
          decision_cmp["test"]["mismatch"] == 0 and decision_cmp["test"]["key_sets_equal"], **decision_cmp["test"])
    uk = {(r["product"], r["window"], r["date"], r["size_idx"], r["D"], r["side"]) for r in rows}
    check("unique_unit_side_keys", len(uk) == len(rows), unique=len(uk), rows=len(rows))
    check("no_skipped_units", sum(skipped.values()) == 0, skipped=dict(skipped))
    probe_ok = len(probe) == 4 and all(p.get("status") == "ok" and p.get("all_invariant")
                                       and p.get("negative_control_changes_ref") for p in probe)
    check("future_perturbation_invariance", probe_ok, cases=probe)

    out = {
        "stage": "volume_activity_20261022_stage0_rev1",
        "scenario": {"a_bp": A_BP, "risk_k": RISK_K, "cap_frac": CAP, "phi": 1.0},
        "E_definition": "ASAP predicted completion >= 0.95 on the 20 historical paths (19/20 or 20/20)",
        "units": {"unit_rows": n_units, "unit_sides": len(rows),
                  "splits": {sp: sum(1 for r in rows if r["split"] == sp) for sp in ("train", "val", "test")}},
        "summary": summary,
        "exclusions": {"scope": "global over all 148,968 unit-sides unless a split key is used",
                       "by_completion": {k: int(v) for k, v in excl_completion.items()},
                       "by_risk": {k: int(v) for k, v in excl_risk.items()},
                       "by_completion_split": {sp: {k: int(v) for k, v in excl_completion_split[sp].items()}
                                               for sp in ("train", "val", "test")},
                       "by_risk_split": {sp: {k: int(v) for k, v in excl_risk_split[sp].items()}
                                         for sp in ("train", "val", "test")}},
        "path_variety": {
            sp: {
                "all_legal_distinct_targets_2plus": sum(1 for r in rows if r["split"] == sp and r["all_legal_distinct_targets"] >= 2),
                "all_legal_distinct_hist_fills_2plus": sum(1 for r in rows if r["split"] == sp and r["all_legal_distinct_hist_fills"] >= 2),
                "all_legal_distinct_actual_fills_2plus": sum(1 for r in rows if r["split"] == sp and r["all_legal_distinct_actual_fills"] >= 2),
                "final_distinct_targets_2plus": sum(1 for r in rows if r["split"] == sp and r["final_distinct_targets"] >= 2),
                "final_distinct_hist_fills_2plus": sum(1 for r in rows if r["split"] == sp and r["final_distinct_hist_fills"] >= 2),
                "final_distinct_actual_fills_2plus": sum(1 for r in rows if r["split"] == sp and r["final_distinct_actual_fills"] >= 2),
            } for sp in ("train", "val", "test")},
        "gate": gate,
        "crosscheck_original_selection": decision_cmp,
        "future_perturbation": probe,
        "assertions": asserts,
        "runtime_s": round(time.time() - t0, 1),
        "pending_corrections": [
            "exante_policy_20261022/EVALUATION.md section 9 writes that 95.8% of units 'can only choose ASAP' at k=1; that is a selection share, not a feasibility share. REPORT.md did not make that claim.",
        ],
    }
    json.dump(out, open(os.path.join(STAGE, "choice_space.json"), "w"), ensure_ascii=False, indent=1)
    json.dump({"assertions": asserts}, open(os.path.join(STAGE, "choice_space_assertions.json"), "w"),
              ensure_ascii=False, indent=1)
    cols = list(rows[0].keys())
    with open(os.path.join(STAGE, "choice_space_units.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    failed = [a["check"] for a in asserts if not a["ok"]]
    print(json.dumps({"gate": gate["status"], "units": out["units"], "assertions_failed": failed,
                      "summary_train_E": summary["train"]["old_E"], "runtime_s": out["runtime_s"]},
                     ensure_ascii=False, indent=1))
    if failed:
        print("ASSERTIONS FAILED:", failed, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
