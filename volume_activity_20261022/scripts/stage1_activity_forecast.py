#!/usr/bin/env python3
"""Stage 1: does the existing pre-release activity signal improve the 60-minute
volume forecast over the plain 20-day average?

Pre-registered in volume_activity_20261022/DESIGN.md:
  r    = act_pre from features_exante.npz (same definition as exante_core.state(),
         segment start .. release bar, n_pre fixed per window: am 15, pm 5, night 15)
  B60  = mean total volume of the prior 20 days over the same 60 post-release minutes
  X    = r - 1
  Y    = V60 / B60 - 1
  beta = sum(XY) / sum(X^2)      (TRAIN only, no intercept, no search)
  s    = max(0, 1 + beta * X)
  SIG60 = B60 * s ;  VOL20 = B60
  loss = |pred60 - V60| / B60    (equal weight per product-window-day)

Gate: VAL and TEST each need mean loss difference < 0 and a 5-day block bootstrap
upper bound < 0 (seed 20261022, 2000 draws, sum/count).

No beta beyond this one fit; no extra models, bins or per-product coefficients.
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
SPLITS = {"train": (20260105, 20260430), "val": (20260506, 20260630), "test": (20260701, 20261020)}
SEED = 20261022
N_BOOT = 2000
EXPECT = {"events": 6207, "train": 2514, "val": 1269, "test": 2424}


def split_of(d):
    d = int(d)
    for s, (a, b) in SPLITS.items():
        if a <= d <= b:
            return s
    return "outside"


def block_bootstrap(diff_by_date, calendar, n_boot=N_BOOT, seed=SEED):
    """5-day blocks on the whole split calendar; pooled sum/count per draw."""
    dates = sorted(calendar)
    block_of = {d: i // 5 for i, d in enumerate(dates)}
    sums, ns = {}, {}
    for d, v in diff_by_date.items():
        b = block_of[d]
        sums[b] = sums.get(b, 0.0) + v[0]
        ns[b] = ns.get(b, 0) + v[1]
    keys = sorted(sums)
    S = np.array([sums[k] for k in keys], dtype=float)
    N = np.array([ns[k] for k in keys], dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    boot = S[draws].sum(axis=1) / N[draws].sum(axis=1)
    return {"n_blocks": int(len(keys)), "mean": float(S.sum() / N.sum()),
            "ci": [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
            "block_ns": [int(x) for x in N], "block_sums": [float(x) for x in S],
            "estimand": "sum(block loss differences) / sum(block event counts) over 5-day blocks"}


def main() -> None:
    t0 = time.time()
    feats = np.load(os.path.join(ROOT, "exante_policy_20261022", "features_exante.npz"), allow_pickle=False)
    keys = feats["keys"]
    act = feats["act_pre"]
    npre = feats["n_pre"]
    vref = feats["V_ref"]
    params = {"events_total": int(len(keys))}

    series = {}
    for pi, p in enumerate(PRODUCTS):
        for wi, w in enumerate(WINDOWS):
            s = E.load_series(p, w)
            if s is None:
                continue
            lab = E.release_label(p, w)
            if lab not in s.labels:
                continue
            series[pi * 3 + wi] = {"ser": s, "i0": s.labels.index(lab),
                                   "idx": {int(d): j for j, d in enumerate(s.dates)}}

    calendar = defaultdict(set)
    rows = []
    excl = Counter()
    b60_mismatch = 0.0
    for i in range(len(keys)):
        series_id, date = int(keys[i, 0]), int(keys[i, 1])
        pi, wi = series_id // 3, series_id % 3
        prod, win = PRODUCTS[pi], WINDOWS[wi]
        sp = split_of(date)
        meta = series.get(series_id)
        row = {"series_id": series_id, "product": prod, "window": win, "date": date, "split": sp,
               "n_pre": int(npre[i]), "act_pre": float(act[i]), "B60": float(vref[i]),
               "status": "ok"}
        if meta is None or date not in meta["idx"]:
            row["status"] = "series_or_date_missing"
            excl["series_or_date_missing"] += 1
            rows.append(row)
            continue
        ser, i0 = meta["ser"], meta["i0"]
        di = meta["idx"][date]
        calendar[sp].add(date)
        r = float(act[i])
        b60 = float(vref[i])
        if not (math.isfinite(r) and r >= 0):
            row["status"] = "act_pre_invalid"
            excl["act_pre_invalid"] += 1
            rows.append(row)
            continue
        if not (math.isfinite(b60) and b60 > 0):
            row["status"] = "B60_zero_or_missing"
            excl["B60_zero_or_missing"] += 1
            rows.append(row)
            continue
        w0, w1 = i0 + 1, i0 + 1 + 60
        if w1 > ser.volume.shape[1] or not ser.mask[di, w0:w1].all():
            row["status"] = "label_window_incomplete"
            excl["label_window_incomplete"] += 1
            rows.append(row)
            continue
        vol = ser.volume[di, w0:w1]
        if not (np.isfinite(vol).all() and (vol >= 0).all()):
            row["status"] = "label_volume_invalid"
            excl["label_volume_invalid"] += 1
            rows.append(row)
            continue
        ref = XC.reference(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
        if ref is None:
            row["status"] = "reference_missing"
            excl["reference_missing"] += 1
            rows.append(row)
            continue
        b60_mismatch = max(b60_mismatch, abs(ref["V_ref"] - b60) / max(b60, 1.0))
        V60 = float(vol.sum())
        curve = ref["V"].mean(axis=0)
        row.update({"prefix_vol": None, "prefix_base": None,
                    "V60": V60, "X": r - 1.0, "Y": V60 / b60 - 1.0,
                    "curve_cum5": float(curve[:5].sum()), "curve_cum15": float(curve[:15].sum()),
                    "curve_cum30": float(curve[:30].sum()),
                    "actual_cum5": float(vol[:5].sum()), "actual_cum15": float(vol[:15].sum()),
                    "actual_cum30": float(vol[:30].sum())})
        rows.append(row)

    # prefix volume / base for reporting (recompute on valid rows only)
    for row in rows:
        if row["status"] != "ok":
            continue
        meta = series[row["series_id"]]
        ser, i0 = meta["ser"], meta["i0"]
        di = meta["idx"][row["date"]]
        s0 = XC.segment_start(ser.labels, i0)
        prior = [j for j in range(max(0, di - 20), di)
                 if ser.mask[j, s0:i0 + 1].all() and (ser.volume[j, s0:i0 + 1] >= 0).all()]
        row["prefix_vol"] = float(ser.volume[di, s0:i0 + 1].sum())
        row["prefix_base"] = float(np.mean([ser.volume[j, s0:i0 + 1].sum() for j in prior])) if prior else None

    usable = [r for r in rows if r["status"] == "ok"]
    by_split = Counter(r["split"] for r in usable)
    dates_by_split = {sp: sorted(calendar[sp]) for sp in SPLITS}

    # ---------- one main fit on TRAIN ----------
    train = [r for r in usable if r["split"] == "train"]
    num = sum(r["X"] * r["Y"] for r in train)
    den = sum(r["X"] * r["X"] for r in train)
    if not (math.isfinite(num) and math.isfinite(den) and den > 0):
        raise SystemExit("beta is not identifiable: sum(X^2) is zero or non-finite")
    beta = num / den
    for r in usable:
        r["s"] = max(0.0, 1.0 + beta * r["X"])
        r["pred_sig"] = r["B60"] * r["s"]
        r["pred_vol20"] = r["B60"]
        r["loss_sig"] = abs(r["pred_sig"] - r["V60"]) / r["B60"]
        r["loss_vol20"] = abs(r["pred_vol20"] - r["V60"]) / r["B60"]
        r["loss_diff"] = r["loss_sig"] - r["loss_vol20"]

    # ---------- evaluation per split ----------
    def evaluate(sp):
        ev = [r for r in usable if r["split"] == sp]
        diffs = defaultdict(lambda: [0.0, 0])
        for r in ev:
            diffs[r["date"]][0] += r["loss_diff"]
            diffs[r["date"]][1] += 1
        boot = block_bootstrap({d: tuple(v) for d, v in diffs.items()}, dates_by_split[sp])
        mean_sig = float(np.mean([r["loss_sig"] for r in ev]))
        mean_vol = float(np.mean([r["loss_vol20"] for r in ev]))
        rmse = lambda xs: float(np.sqrt(np.mean(np.square(xs))))
        diag = {}
        for k in (5, 15, 30):
            ps = [abs(r["curve_cum%d" % k] * r["s"] - r["actual_cum%d" % k]) / r["B60"] for r in ev]
            pv = [abs(r["curve_cum%d" % k] - r["actual_cum%d" % k]) / r["B60"] for r in ev]
            diag[f"cum{k}"] = {"mae_sig": float(np.mean(ps)), "mae_vol20": float(np.mean(pv)),
                               "diff": float(np.mean(ps) - np.mean(pv))}
        return {"n_events": len(ev), "n_dates": len({r["date"] for r in ev}),
                "mean_loss_sig": mean_sig, "mean_loss_vol20": mean_vol,
                "mean_diff": mean_sig - mean_vol,
                "relative_reduction_pct": (100.0 * (mean_vol - mean_sig) / mean_vol) if mean_vol else None,
                "bootstrap": boot,
                "rmse_sig": rmse([r["pred_sig"] - r["V60"] for r in ev]),
                "rmse_vol20": rmse([r["pred_vol20"] - r["V60"] for r in ev]),
                "rmse_norm_sig": rmse([(r["pred_sig"] - r["V60"]) / r["B60"] for r in ev]),
                "rmse_norm_vol20": rmse([(r["pred_vol20"] - r["V60"]) / r["B60"] for r in ev]),
                "cumulative_diagnostics": diag,
                "gate_pass": bool(mean_sig - mean_vol < 0 and boot["ci"][1] < 0)}

    evaluation = {sp: evaluate(sp) for sp in ("val", "test")}
    gate = {"rule": "VAL and TEST each: mean loss diff < 0 and 5-day block bootstrap upper bound < 0",
            "val_pass": evaluation["val"]["gate_pass"], "test_pass": evaluation["test"]["gate_pass"],
            "status": "READY_FOR_EXECUTION_REVIEW" if (evaluation["val"]["gate_pass"] and evaluation["test"]["gate_pass"])
            else "STOP_FORECAST_NO_INCREMENT"}

    # ---------- four fixed OOS perturbation cases ----------
    probes = []
    for prod, win in (("RB", "am"), ("CU", "am"), ("IF", "am"), ("SC", "night")):
        sid = PRODUCTS.index(prod) * 3 + WINDOWS.index(win)
        cand = [r for r in usable if r["split"] == "test" and r["product"] == prod and r["window"] == win]
        if not cand:
            probes.append({"product": prod, "window": win, "status": "no_test_event"})
            continue
        r = sorted(cand, key=lambda x: x["date"])[0]
        meta = series[sid]
        ser, i0 = meta["ser"], meta["i0"]
        di = meta["idx"][r["date"]]
        base_state = XC.state(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
        base_ref = XC.reference(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
        vol2, cl2 = ser.volume.copy(), ser.close.copy()
        vol2[di, i0 + 1:] = np.where(np.arange(vol2.shape[1] - i0 - 1) % 2 == 0, 1e9, 0.0)
        cl2[di, i0 + 1:] = np.where(np.arange(cl2.shape[1] - i0 - 1) % 3 == 0, np.nan, 1e6)
        new_state = XC.state(vol2, cl2, ser.mask, ser.labels, i0, di)
        new_ref = XC.reference(vol2, cl2, ser.mask, ser.labels, i0, di)
        same_feat = (base_state == new_state and base_ref is not None and new_ref is not None
                     and np.array_equal(base_ref["V"], new_ref["V"]))
        s0 = max(0.0, 1.0 + beta * (base_state["act_pre"] - 1.0))
        s1 = max(0.0, 1.0 + beta * (new_state["act_pre"] - 1.0))
        curve0 = base_ref["V"].mean(axis=0) * s0
        curve1 = new_ref["V"].mean(axis=0) * s1
        label0 = float(ser.volume[di, i0 + 1:i0 + 61].sum())
        label1 = float(vol2[di, i0 + 1:i0 + 61].sum())
        probes.append({"product": prod, "window": win, "date": r["date"], "status": "ok",
                       "feature_invariant": bool(same_feat), "scale_invariant": bool(abs(s0 - s1) < 1e-12),
                       "forecast_curve_invariant": bool(np.array_equal(curve0, curve1)),
                       "label_changed": bool(label0 != label1), "s": s0,
                       "B60": float(base_ref["V_ref"])})
    # rejection paths
    rej = []
    sid = PRODUCTS.index("RB") * 3 + WINDOWS.index("am")
    meta = series[sid]
    ser, i0 = meta["ser"], meta["i0"]
    r0 = sorted([r for r in usable if r["series_id"] == sid], key=lambda x: x["date"])[0]
    di = meta["idx"][r0["date"]]
    m2 = ser.mask.copy()
    m2[di, i0 - 2] = False
    rej.append({"case": "prefix_bar_masked", "rejected": XC.state(ser.volume, ser.close, m2, ser.labels, i0, di) is None})
    v2 = ser.volume.copy()
    v2[di - 20:di, i0 + 1:i0 + 61] = 0.0
    rej.append({"case": "reference_window_all_zero", "rejected": XC.reference(v2, ser.close, ser.mask, ser.labels, i0, di) is None})

    # ---------- write events csv ----------
    cols = ["product", "window", "date", "split", "status", "n_pre", "act_pre", "prefix_vol", "prefix_base",
            "B60", "V60", "X", "Y", "s", "pred_sig", "pred_vol20", "loss_sig", "loss_vol20", "loss_diff",
            "curve_cum5", "curve_cum15", "curve_cum30", "actual_cum5", "actual_cum15", "actual_cum30"]
    with open(os.path.join(STAGE, "forecast_events.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # ---------- assertions ----------
    asserts = []

    def check(name, ok, **kw):
        rec = {"check": name, "ok": bool(ok)}
        rec.update(kw)
        asserts.append(rec)

    check("event_keys_unique", len({(r["series_id"], r["date"]) for r in rows}) == len(rows), rows=len(rows))
    check("events_total", len(rows) == EXPECT["events"], got=len(rows))
    for sp in ("train", "val", "test"):
        check(f"usable_events:{sp}", by_split[sp] == EXPECT[sp], got=by_split[sp])
    check("exclusions_accounted",
          sum(by_split.values()) + sum(excl.values()) == len(rows), usable=sum(by_split.values()), excl=dict(excl))
    check("beta_identifiable", math.isfinite(beta) and den > 0, numerator=num, denominator=den)
    check("B60_matches_cached_reference", b60_mismatch < 1e-6, max_rel_diff=b60_mismatch)
    check("scale_non_negative", all(r["s"] >= 0 and math.isfinite(r["s"]) for r in usable))
    # loss reconciliation: recompute from the CSV rows and compare with the JSON values
    recal_val = float(np.mean([r["loss_diff"] for r in usable if r["split"] == "val"]))
    check("loss_reconciles:val", abs(recal_val - evaluation["val"]["mean_diff"]) < 1e-12, recomputed=recal_val)
    recal_test = float(np.mean([r["loss_diff"] for r in usable if r["split"] == "test"]))
    check("loss_reconciles:test", abs(recal_test - evaluation["test"]["mean_diff"]) < 1e-12, recomputed=recal_test)
    # equal event weight: bootstrap sample counts equal the usable event counts
    for sp in ("val", "test"):
        exp_blocks = (len(dates_by_split[sp]) + 4) // 5
        check(f"bootstrap_blocks:{sp}", evaluation[sp]["bootstrap"]["n_blocks"] == exp_blocks,
              got=evaluation[sp]["bootstrap"]["n_blocks"], expected=exp_blocks)
        check(f"bootstrap_counts:{sp}", sum(evaluation[sp]["bootstrap"]["block_ns"]) == evaluation[sp]["n_events"],
              counted=sum(evaluation[sp]["bootstrap"]["block_ns"]), events=evaluation[sp]["n_events"])
    check("perturbation_cases", len(probes) == 4 and all(p.get("status") == "ok" and p["feature_invariant"]
                                                          and p["scale_invariant"] and p["forecast_curve_invariant"]
                                                          and p["label_changed"] for p in probes), cases=probes)
    check("rejection_paths", all(x["rejected"] for x in rej), cases=rej)
    check("gate_status_consistent",
          (gate["status"] == "READY_FOR_EXECUTION_REVIEW") == (gate["val_pass"] and gate["test_pass"]))

    out = {
        "stage": "volume_activity_20261022_stage1",
        "signal": {"name": "act_pre", "source": "exante_policy_20261022/features_exante.npz",
                   "definition": "pre-release segment volume / prior-20-day same-segment mean; n_pre fixed per window (am 15, pm 5, night 15)"},
        "fit": {"split": "train", "beta": beta, "numerator": num, "denominator": den,
                "n_events": len(train), "n_dates": len({r["date"] for r in train}),
                "date_min": min(r["date"] for r in train), "date_max": max(r["date"] for r in train),
                "formula": "X = act_pre - 1 ; Y = V60/B60 - 1 ; beta = sum(XY)/sum(X^2) ; s = max(0, 1 + beta X)"},
        "events": {"total": len(rows), "usable": sum(by_split.values()), "by_split": dict(by_split),
                   "exclusions": dict(excl), "calendar_days": {sp: len(dates_by_split[sp]) for sp in SPLITS}},
        "evaluation": evaluation,
        "gate": gate,
        "perturbation_cases": probes,
        "rejection_cases": rej,
        "assertions": asserts,
        "runtime_s": round(time.time() - t0, 1),
        "notes": ["VAL and TEST were already inspected in earlier stages; this is a retrospective time-ordered check, not a fresh holdout",
                  "a>0 execution gains are not evaluated here; Stage 2 needs separate approval"],
    }
    json.dump(out, open(os.path.join(STAGE, "forecast_evaluation.json"), "w"), ensure_ascii=False, indent=1)
    json.dump({"beta": beta, "numerator": num, "denominator": den, "n_events": len(train),
               "n_dates": len({r["date"] for r in train}), "s_formula": "max(0, 1 + beta*(act_pre-1))",
               "train_window": list(SPLITS["train"]), "signal": "act_pre"},
              open(os.path.join(STAGE, "params_stage1.json"), "w"), ensure_ascii=False, indent=1)

    failed = [a["check"] for a in asserts if not a["ok"]]
    print(json.dumps({"beta": beta, "numerator": num, "denominator": den,
                      "val": {k: evaluation["val"][k] for k in ("n_events", "mean_loss_sig", "mean_loss_vol20", "mean_diff", "gate_pass")},
                      "test": {k: evaluation["test"][k] for k in ("n_events", "mean_loss_sig", "mean_loss_vol20", "mean_diff", "gate_pass")},
                      "gate": gate["status"], "assertions_failed": failed, "runtime_s": out["runtime_s"]},
                     ensure_ascii=False, indent=1))
    if failed:
        print("ASSERTIONS FAILED:", failed, file=sys.stderr)
        raise SystemExit(1)
    if gate["status"] != "READY_FOR_EXECUTION_REVIEW":
        print("STOP_FORECAST_NO_INCREMENT", file=sys.stderr)


if __name__ == "__main__":
    main()
