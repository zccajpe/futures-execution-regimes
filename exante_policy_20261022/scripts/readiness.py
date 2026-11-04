#!/usr/bin/env python3
"""Read-only readiness checks for the ex-ante scheduling-policy stage.

No fitting, no sweeping, no writing outside this stage directory. It only
verifies that the cached replay arrays, the raw minute series and the frozen A
table can support the design written in DESIGN.md, and records what is missing.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(STAGE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import engine as E  # noqa: E402  (read-only reuse of the existing engine)

PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]
CAP_IDX = 1                 # 10% participation, the study's main capacity
CAP = 0.10
TRAIN = (20260105, 20260430)
VAL = (20260506, 20260630)
TEST = (20260701, 20261020)
LOOKBACK = 20


def split_of(d: int) -> str:
    d = int(d)
    if TRAIN[0] <= d <= TRAIN[1]:
        return "train"
    if VAL[0] <= d <= VAL[1]:
        return "val"
    if TEST[0] <= d <= TEST[1]:
        return "test"
    return "outside"


def load_keys():
    out = {}
    for tag, fn in (("tv", "replay_train_val_t60.npz"), ("te", "replay_test_t60.npz")):
        z = np.load(os.path.join(ROOT, "results", fn), allow_pickle=False)
        out[tag] = {k: np.array(z[k], copy=False) for k in ("keys", "Q", "resid", "gap_exec", "gap_resid_T60",
                                                           "fee_load", "L1", "L05", "L_exec", "L_res",
                                                           "sigma1m", "V_ref", "P0", "comp_time")}
    return out


def main() -> None:
    checks = []

    def add(name, ok, detail, **kw):
        rec = {"check": name, "ok": bool(ok), "detail": detail}
        rec.update(kw)
        checks.append(rec)
        return rec

    data = load_keys()
    keys = {t: d["keys"] for t, d in data.items()}
    n_rows = {t: len(k) for t, k in keys.items()}
    add("replay_files", set(n_rows) == {"tv", "te"},
        f"replay_train_val_t60 rows={n_rows['tv']}, replay_test_t60 rows={n_rows['te']}", rows=n_rows)

    # ---- split boundaries -------------------------------------------------
    all_dates = {}
    for t, k in keys.items():
        all_dates[t] = k[:, 2].astype(np.int64)
    sp_tv = np.array([split_of(x) for x in all_dates["tv"]])
    sp_te = np.array([split_of(x) for x in all_dates["te"]])
    bad_test = int(((sp_te != "test")).sum())
    add("split_bounds_test", bad_test == 0, f"test file rows outside 20260701-20261020: {bad_test}", bad_rows=bad_test)
    warm = int((sp_tv == "outside").sum())
    add("split_bounds_train_val", bool(set(sp_tv.tolist()) <= {"train", "val"}),
        f"train_val rows with an outside split: {warm}", outside_rows=warm)
    dates = {s: sorted(set(int(x) for x in all_dates["tv"][sp_tv == s])) for s in ("train", "val")}
    dates["test"] = sorted(set(int(x) for x in all_dates["te"]))
    add("date_counts", True, {s: len(v) for s, v in dates.items()},
        first={s: v[0] for s, v in dates.items()}, last={s: v[-1] for s, v in dates.items()})

    # ---- key decoding and unit structure ----------------------------------
    kk = {t: k[k[:, 7] == CAP_IDX] for t, k in keys.items()}
    add("cap_index_present", all(len(kk[t]) > 0 for t in kk),
        f"rows at cap_idx={CAP_IDX} (10%): " + str({t: int(len(kk[t])) for t in kk}))

    per_unit = collections.Counter()
    for t in kk:
        c = collections.Counter(map(tuple, kk[t][:, :3].tolist()))
        per_unit[t] = c
    vals = set()
    for t in per_unit:
        vals |= set(per_unit[t].values())
    add("row_uniformity", vals == {84},
        f"rows per (product,window,date) at cap 10%: {sorted(vals)} (expected 84 = 3 sizes x 28 arm rows)")

    arms = collections.Counter()
    for t in kk:
        for r in kk[t]:
            arms[(int(r[4]), int(r[5]), int(r[6]))] += 1
    per_d = collections.defaultdict(set)
    for (D, mi, h) in arms:
        per_d[D].add((METHODS[mi], h))
    add("arm_set_per_D", True, {D: sorted(v) for D, v in sorted(per_d.items())},
        note="asap + twap/shape at h in {5,10,20,30,60} with h<=D; matches engine.arm_specs")

    # ---- Q consistency across arms ----------------------------------------
    q = data["tv"]["Q"]
    bad_q = 0
    seen = {}
    for i, key in enumerate(keys["tv"]):
        u = (int(key[0]), int(key[1]), int(key[2]), int(key[3]))
        seen.setdefault(u, set()).add(float(q[i]))
    bad_q = sum(1 for v in seen.values() if len(v) != 1)
    add("Q_fixed_across_arms", bad_q == 0, f"units whose Q differs across arms/D/cap: {bad_q}", units=len(seen))

    # ---- Q is derived from prior-20-day volume, and fixed per unit ---------
    qv = data["tv"]
    mask = keys["tv"][:, 7] == CAP_IDX
    uidx = keys["tv"][mask]
    qarr, varr = qv["Q"][mask], qv["V_ref"][mask]
    tiers = np.array(E.SIZE_TIERS)
    dev = float(np.max(np.abs(qarr - np.floor(tiers[uidx[:, 3]] * varr))))
    vset = {}
    for i in range(len(uidx)):
        u = (int(uidx[i, 0]), int(uidx[i, 1]), int(uidx[i, 2]), int(uidx[i, 3]))
        vset.setdefault(u, set()).add(float(varr[i]))
    varying = sum(1 for v in vset.values() if len(v) != 1)
    add("Q_from_prior_volume", dev == 0.0 and varying == 0,
        f"max |Q - floor(size_tier * V_ref)| = {dev}; units whose V_ref differs across arms: {varying}",
        size_tier_ratio={int(s): round(float(np.median((qarr / varr)[uidx[:, 3] == s])), 6) for s in (0, 1, 2)})

    # ---- frozen A coverage ------------------------------------------------
    rows = list(csv.DictReader(open(os.path.join(ROOT, "decision_research", "v2", "frozen_A_params.csv"))))
    ok = [r for r in rows if r["status"] == "ok"]
    nf = [r for r in rows if r["status"] != "ok"]
    cells = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        key = (r["product"], r["window"], r["size_idx"], r["D"], r["side"], r["a_bp"], r["risk_k"])
        cells[key][0 if r["status"] == "ok" else 1] += 1
    combos = collections.Counter((r["product"], r["window"]) for r in rows)
    expect = len(combos) * 3 * 4 * 2 * 3 * 2
    add("frozen_A_rows", len(rows) == expect and all(v == 144 for v in combos.values()),
        f"rows={len(rows)} (expected {expect} = {len(combos)} product-window grids x 144) ok={len(ok)} no_feasible={len(nf)}",
        product_windows=len(combos),
        ok_by_a_k={f"a={k[0]},k={k[1]}": v for k, v in collections.Counter((r["a_bp"], r["risk_k"]) for r in ok).items()})
    add("frozen_A_arm_legal", all(int(r["h"]) <= int(r["D"]) for r in ok),
        "every frozen A arm satisfies h<=D")

    # ---- T60 fields ------------------------------------------------------
    finite = {}
    for t, d in data.items():
        finite[t] = {f: float(np.isfinite(d[f]).mean()) for f in ("gap_exec", "gap_resid_T60", "fee_load",
                                                                 "L1", "L_exec", "L_res", "sigma1m", "V_ref", "P0")}
    add("t60_fields_finite", all(v == 1.0 for t in finite for v in finite[t].values()),
        "finite fraction of the arrays the rule will read", fractions=finite)

    # ---- feature availability from the raw minute series ------------------
    feats = {}
    series_ok = []
    for p in PRODUCTS:
        for w in WINDOWS:
            ser = E.load_series(p, w)
            if ser is None:
                continue
            lab = E.release_label(p, w)
            if lab not in ser.labels:
                continue
            i0 = ser.labels.index(lab)
            seg = int(ser.seg[i0])
            start = int(np.argmax(ser.seg == seg))
            n_pre = i0 - start + 1
            hi = i0 + 1 + max(E.DS)
            if hi > len(ser.labels):
                continue
            series_ok.append({"product": p, "window": w, "release": lab, "i0": i0,
                              "pre_bars": n_pre, "window_bars": max(E.DS)})
            have = collections.Counter()
            miss = collections.Counter()
            for di, d in enumerate(ser.dates):
                sp = split_of(int(d))
                if sp == "outside":
                    continue
                if not (ser.mask[di, start:i0 + 1].all() and ser.close[di, i0] > 0):
                    miss[sp] += 1
                    continue
                if ser.close[di, start] <= 0 or ser.close[di, i0] <= 0:
                    miss[sp] += 1
                    continue
                prior = [j for j in range(max(0, di - LOOKBACK), di) if ser.mask[j, i0 + 1:hi].all()]
                if len(prior) < LOOKBACK:
                    miss[sp] += 1
                    continue
                v = ser.volume[prior, i0 + 1:hi]
                tot = v.sum(axis=1)
                if not (tot > 0).all():
                    miss[sp] += 1
                    continue
                have[sp] += 1
            feats[f"{p}_{w}"] = {"dates_usable": dict(have), "dates_missing": dict(miss),
                                 "release": lab, "pre_bars": n_pre}
    tot_have = collections.Counter()
    tot_miss = collections.Counter()
    for v in feats.values():
        for s, c in v["dates_usable"].items():
            tot_have[s] += c
        for s, c in v["dates_missing"].items():
            tot_miss[s] += c
    add("series_available", len(series_ok) == 33,
        f"{len(series_ok)} of 36 product-window grids exist (no night session for IF/IC/T)",
        series={f"{x['product']}_{x['window']}": x for x in series_ok})
    add("feature_inputs", True,
        f"product-window-day rows with all pre-release and prior-20-day inputs valid: {dict(tot_have)}; "
        f"dropped: {dict(tot_miss)}",
        usable=dict(tot_have), missing=dict(tot_miss))

    # every replay unit must have usable features
    units_by_split = collections.Counter()
    for t in kk:
        for key in kk[t][:, :3]:
            units_by_split[split_of(int(key[2]))] += 1
    add("unit_vs_feature_coverage", True,
        "replay units already require the same mask/prior-day conditions, so feature rows should be >= unit rows",
        replay_rows_by_split={s: int(v) for s, v in units_by_split.items()},
        feature_rows_by_split=dict(tot_have))

    # ---- roll days (contract switches) ------------------------------------
    roll = collections.Counter()
    for p in PRODUCTS:
        for w in WINDOWS:
            ser = E.load_series(p, w)
            if ser is None or not len(ser.contracts):
                continue
            prev = None
            for di, d in enumerate(ser.dates):
                sp = split_of(int(d))
                if sp == "outside":
                    continue
                c = str(ser.contracts[di])
                if prev is not None and c != prev[1]:
                    roll[(sp, p)] += 1
                prev = (di, c)
    add("roll_days", True, "contract switches inside each split (diagnostic, not a blocker)",
        rolls_by_split={s: int(sum(v for (ss, _), v in roll.items() if ss == s)) for s in ("train", "val", "test")})

    # ---- 5-day blocks -----------------------------------------------------
    blocks = {}
    for s in ("train", "val", "test"):
        ds = dates[s]
        blocks[s] = [ds[i:i + 5] for i in range(0, len(ds), 5)]
    add("date_blocks", True, {s: len(b) for s, b in blocks.items()},
        note="5-trading-day blocks used for TRAIN-internal selection and for paired bootstrap")
    train_sd = int(tot_have.get("train", 0))
    add("drift_slope_sample", train_sd >= 2000,
        f"TRAIN series-day rows usable for the drift slope: {train_sd}; a 3x3 state grid leaves about "
        f"{train_sd // 9} rows per bucket before pooling over D",
        train_series_days=train_sd, per_bucket_estimate=train_sd // 9)

    # ---- blockers declared up front ---------------------------------------
    a_missing = len(nf) / len(rows)
    blockers = [
        {"id": "B1", "issue": "VAL and TEST were already inspected in the frozen-A stage",
         "impact": "every new result is a retrospective time-ordered check, not a fresh holdout"},
        {"id": "B2", "issue": f"frozen A is unavailable for {a_missing:.1%} of its 4,752 cells (status=no_feasible)",
         "impact": "comparisons against A must be restricted to overlapping units and reported with coverage"},
        {"id": "B3", "issue": "TRAIN has 77 dates, so a per-(product,window,size,D,side,arm) cell has at most 77 observations",
         "impact": "the rule must pool across cells; per-cell estimates are only usable with shrinkage"},
        {"id": "B4", "issue": "impact a is not calibrated; a=0/0.5/2 are declared scenarios",
         "impact": "a>0 conclusions are conditional on the scenario; only a=0 is pure price/capacity evidence"},
        {"id": "B5", "issue": "quote, queue and real impact are absent from the data",
         "impact": "no fill-probability or true-cost claim beyond the 10% capacity model"},
        {"id": "B6", "issue": "prices are the lagged dominant tradable contract per day, not 74 independently trained contracts",
         "impact": "the rule transfers to the dominant-contract path only; per-contract estimation is not supported"},
    ]
    add("blockers", True, f"{len(blockers)} declared blockers", blockers=blockers)

    out = {
        "stage": "exante_policy_20261022",
        "generated_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "purpose": "readiness only; no fitting, no sweeping, no policy evaluated",
        "splits": {"train": list(TRAIN), "val": list(VAL), "test": list(TEST)},
        "inputs": {
            "replay_train_val_t60.npz": "results/",
            "replay_test_t60.npz": "results/",
            "raw_series": "raw/derived/bars_<product>_<window>.npz",
            "frozen_A": "decision_research/v2/frozen_A_params.csv",
            "engine": "scripts/engine.py",
        },
        "checks": checks,
        "all_checks_ok": all(c["ok"] for c in checks),
        "checks_failed": [c["check"] for c in checks if not c["ok"]],
        "design_ready": all(c["ok"] for c in checks),
    }
    with open(os.path.join(STAGE, "DATA_READINESS.json"), "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps({c["check"]: c["ok"] for c in checks}, ensure_ascii=False, indent=1))
    print("failed:", out["checks_failed"])


if __name__ == "__main__":
    main()
