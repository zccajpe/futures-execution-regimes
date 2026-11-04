#!/usr/bin/env python3
"""Build the ex-ante feature cache and the prior-path prediction cache.

Reads only:
  raw/derived/bars_<product>_<window>.npz   (minute bars)
  results/replay_train_val_t60.npz / replay_test_t60.npz  (for Q and the unit set)

Writes (stage dir):
  features_exante.npz  one row per (series, date): state + prior-20-day reference
  pred_cache.npz       one row per (series, date, size, D): per-arm T/L/comp/fee

No fitting happens here.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(STAGE)
CORE_PATH = os.path.join(ROOT, "delivery", "futures-execution-decision", "scripts", "exante_core.py")

spec = importlib.util.spec_from_file_location("exante_core", CORE_PATH)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
SIZE_TIERS = (0.0025, 0.01, 0.04)
DS = (5, 15, 30, 60)
CAP = 0.10
ARMS = [C.arm_name(m, h) for m, h in C.ARMS]

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import engine as E  # noqa: E402  (release_label only)


def load_replay():
    out = {}
    for fn in ("replay_train_val_t60.npz", "replay_test_t60.npz"):
        z = np.load(os.path.join(ROOT, "results", fn), allow_pickle=False)
        out[fn] = {k: np.array(z[k], copy=False) for k in ("keys", "Q")}
    return out


TRAIN = (20260105, 20260430)
VAL = (20260506, 20260630)
TEST = (20260701, 20261020)


def split_of(d):
    d = int(d)
    if TRAIN[0] <= d <= TRAIN[1]:
        return "train"
    if VAL[0] <= d <= VAL[1]:
        return "val"
    if TEST[0] <= d <= TEST[1]:
        return "test"
    return "outside"


def replay_series_dates(rep):
    """(pi,wi,date) triples that have rows at cap_idx=1, with their split."""
    out = {}
    for tag, d in rep.items():
        k = d["keys"]
        for i in np.nonzero(k[:, 7] == 1)[0]:
            out[(int(k[i, 0]), int(k[i, 1]), int(k[i, 2]))] = split_of(int(k[i, 2]))
    return out


def main() -> None:
    t0 = time.time()
    rep = load_replay()
    units = {}
    for tag, d in rep.items():
        k = d["keys"]
        sel = k[:, 7] == 1
        for i in np.nonzero(sel)[0]:
            key = (int(k[i, 0]), int(k[i, 1]), int(k[i, 2]), int(k[i, 3]))
            units.setdefault(key, float(d["Q"][i]))

    expected = replay_series_dates(rep)
    series_dates = sorted(expected)
    print(f"expected keys={len(expected)} split={ {s: sum(1 for v in expected.values() if v==s) for s in ('train','val','test')} }", flush=True)
    by_series = {}
    for (pi, wi, dt) in series_dates:
        by_series.setdefault((pi, wi), []).append(dt)
    print(f"units={len(units)} series-dates={len(series_dates)}", flush=True)

    feat_rows, pred_rows = [], []
    missing = {"reference": 0, "state": 0}
    for pi, p in enumerate(PRODUCTS):
        for wi, w in enumerate(WINDOWS):
            ser = E.load_series(p, w)
            if ser is None:
                continue
            lab = E.release_label(p, w)
            if lab not in ser.labels:
                continue
            i0 = ser.labels.index(lab)
            idx_of_date = {int(d): j for j, d in enumerate(ser.dates)}
            for dt in by_series.get((pi, wi), []):
                di = idx_of_date.get(int(dt))
                if di is None:
                    missing["date_not_in_series"] = missing.get("date_not_in_series", 0) + 1
                    continue
                ref = C.reference(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
                st = C.state(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
                if ref is None or st is None:
                    missing["reference" if ref is None else "state"] += 1
                    continue
                z = C.z_pre(st["r_pre_bp"], ref["sigma1m"], st["steps"])
                feat_rows.append((pi * 3 + wi, int(dt), i0, st["n_pre"], st["steps"], ref["sigma1m"],
                                  ref["V_ref"], st["r_pre_bp"], z, st["v_pre"], st["act_pre"],
                                  ref["n_days"], st["n_base_days"]))
                for si in range(3):
                    Q = units[(pi, wi, int(dt), si)]
                    for D in DS:
                        pred = C.arm_stats(ref["V"], ref["cumw"], int(Q), D, CAP)
                        vals = []
                        for arm in ARMS:
                            v = pred.get(arm)
                            vals.append((v["T"], v["L"], v["comp"], v["fee"]) if v else (np.nan,) * 4)
                        pred_rows.append((pi * 3 + wi, int(dt), si, D, int(Q), vals))
                if len(feat_rows) % 500 == 0:
                    print(f"  {p}_{w} {dt} rows={len(pred_rows)} t={time.time()-t0:.0f}s", flush=True)

    if not feat_rows:
        raise RuntimeError("build_features produced zero feature rows; refusing to write a model input")
    n_f = len(feat_rows)

    built3 = {(int(r[0]) // 3, int(r[0]) % 3, int(r[1])) for r in feat_rows}  # (pi, wi, date)
    want = set(expected)
    missing_keys = sorted(want - built3)
    extra_keys = sorted(built3 - want)
    key_alignment = {
        "expected": len(want), "built": len(built3),
        "missing": [list(x) for x in missing_keys[:50]], "n_missing": len(missing_keys),
        "extra": [list(x) for x in extra_keys[:50]], "n_extra": len(extra_keys),
        "by_split": {s: sum(1 for x in built3 if expected.get(x) == s) for s in ("train", "val", "test")},
        "expected_by_split": {s: sum(1 for v in expected.values() if v == s) for s in ("train", "val", "test")},
        "exact": not missing_keys and not extra_keys,
    }
    if not key_alignment["exact"]:
        with open(os.path.join(STAGE, "features_build_error.json"), "w") as fh:
            json.dump({"key_alignment": key_alignment, "missing_counts": missing}, fh, ensure_ascii=False, indent=1)
        raise RuntimeError(f"feature key set does not match the replay unit set: missing={len(missing_keys)} extra={len(extra_keys)}")
    feats = np.array(feat_rows, dtype=np.float64)
    keys = feats[:, :2].astype(np.int64)
    np.savez_compressed(
        os.path.join(STAGE, "features_exante.npz"),
        keys=keys,
        i0=feats[:, 2].astype(np.int64), n_pre=feats[:, 3].astype(np.int64), steps=feats[:, 4].astype(np.int64),
        sigma1m=feats[:, 5], V_ref=feats[:, 6], r_pre_bp=feats[:, 7], z_pre=feats[:, 8],
        v_pre=feats[:, 9], act_pre=feats[:, 10],
        n_ref_days=feats[:, 11].astype(np.int64), n_base_days=feats[:, 12].astype(np.int64))

    n_p = len(pred_rows)
    pk = np.array([[r[0], r[1], r[2], r[3]] for r in pred_rows], dtype=np.int64)
    pq = np.array([r[4] for r in pred_rows], dtype=np.int64)
    vals = np.array([r[5] for r in pred_rows], dtype=np.float64)   # n_p x n_arms x 4
    np.savez_compressed(
        os.path.join(STAGE, "pred_cache.npz"),
        keys=pk, Q=pq, arms=np.array(ARMS),
        T=vals[:, :, 0], L=vals[:, :, 1], comp=vals[:, :, 2], fee=vals[:, :, 3])

    # ---- sample consistency against the original engine ---------------------
    samples = []
    for (p, w) in (("RB", "am"), ("SC", "night"), ("TA", "am"), ("T", "pm")):
        ser = E.load_series(p, w)
        if ser is None:
            continue
        lab = E.release_label(p, w)
        i0 = ser.labels.index(lab)
        idx_of_date = {int(d): j for j, d in enumerate(ser.dates)}
        cand = sorted(dt for (pi2, wi2, dt) in expected
                      if PRODUCTS[pi2] == p and WINDOWS[wi2] == w)
        picks = [cand[0], cand[len(cand) // 2], cand[-1]] if len(cand) >= 3 else cand
        for dt in picks:
            di = idx_of_date[int(dt)]
            ref = C.reference(ser.volume, ser.close, ser.mask, ser.labels, i0, di)
            eng = E.reference_stats(ser, di, i0)
            if ref is None or eng is None:
                samples.append({"product": p, "window": w, "date": int(dt), "status": "one_side_none"})
                continue
            pi, wi = PRODUCTS.index(p), WINDOWS.index(w)
            qs = {si: units[(pi, wi, int(dt), si)] for si in range(3)}
            samples.append({
                "product": p, "window": w, "date": int(dt), "split": split_of(dt),
                "d_v_ref": abs(ref["V_ref"] - eng["v_avg"]),
                "d_sigma1m": abs(ref["sigma1m"] - eng["sigma1m"]),
                "d_cumw": float(np.max(np.abs(ref["cumw"] - eng["cumw"]))),
                "zero_minutes_in_paths": int((ref["V"] == 0).sum()),
                "q_floor_match": {si: abs(qs[si] - np.floor(SIZE_TIERS[si] * ref["V_ref"])) for si in range(3)},
                "n_days_ref": ref["n_days"], "n_days_engine": eng["n_days"],
            })
    sample_ok = all(
        x.get("status") != "one_side_none"
        and x["d_cumw"] < 1e-12
        and x["d_sigma1m"] < 1e-9
        and x["d_v_ref"] <= 1e-9 * max(1.0, abs(x["n_days_ref"]))
        and all(v == 0 for v in x["q_floor_match"].values())
        for x in samples)

    # ---- NaN rule: illegal arms (h>D) may be NaN, legal arms must not be -----
    nan_ok = True
    for j, arm in enumerate(ARMS):
        method = arm.rstrip("0123456789")
        h = int(arm[len(method):])
        for i, D in enumerate(DS):
            col = vals[pk[:, 3] == D, j, 0]
            legal = (method == "asap") or (5 <= h <= D)
            if legal:
                if not np.isfinite(col).all():
                    nan_ok = False
            else:
                if not np.isnan(col).all():
                    nan_ok = False

    diag = {
        "feature_rows": int(n_f), "pred_rows": int(n_p), "n_arms": len(ARMS),
        "key_alignment": key_alignment,
        "split_counts": key_alignment["by_split"],
        "sample_consistency": samples, "sample_consistency_ok": bool(sample_ok),
        "nan_rule_ok": bool(nan_ok),
        "missing": missing, "cap_frac": CAP,
        "elapsed_s": round(time.time() - t0, 1),
        "core_path": CORE_PATH,
        "arms": ARMS,
    }
    with open(os.path.join(STAGE, "features_build.json"), "w") as fh:
        json.dump(diag, fh, ensure_ascii=False, indent=1)
    print(json.dumps(diag, ensure_ascii=False))


if __name__ == "__main__":
    main()
