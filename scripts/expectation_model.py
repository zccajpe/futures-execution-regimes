"""Expectation model v2: three causal pre-release features, fixed-lambda ridge.

Features (all computable at the release bar, all inside the actual contract):
  f1 = log(close_release / close_first_bar_of_session)        session-to-release return
  f2 = volume(session start .. release) / prior-20-day mean of the same segment
  f3 = previous trading day's same-window return (computed inside that day's contract)
Target: forward return release -> release+D (bp), divided by ex-ante sigma_D.
Train 2026-01-05..04-30, evaluate 2026-05-06..06-30 against a zero forecast.
No cross-roll gap, no hyper-parameter search.
"""
from __future__ import annotations

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
TRAIN = ("20260105", "20260430")
VAL = ("20260506", "20260630")
LAMBDA = 1.0


def build(D: int):
    X, y, split = [], [], []
    for product in PRODUCTS:
        for window in WINDOWS:
            series = E.load_series(product, window)
            if series is None:
                continue
            rel = E.release_label(product, window)
            if rel not in series.labels:
                continue
            i0 = series.labels.index(rel)
            if i0 + 1 + D >= len(series.labels):
                continue
            start = int(np.argmax(series.seg == series.seg[i0]))
            for di, date in enumerate(series.dates):
                if di < 21 or not (TRAIN[0] <= date <= VAL[1]):
                    continue
                if not series.mask[di, i0 + 1:i0 + 1 + D].all() or not series.mask[di, i0]:
                    continue
                p0, p1 = float(series.close[di, i0]), float(series.close[di, i0 + D])
                p_open = float(series.close[di, start])
                if min(p0, p1, p_open) <= 0:
                    continue
                hist = [j for j in range(max(0, di - 20), di) if series.mask[j, start:i0 + 1].all()]
                if not hist:
                    continue
                base_vol = float(np.mean([series.volume[j, start:i0 + 1].sum() for j in hist]))
                cur_vol = float(series.volume[di, start:i0 + 1].sum())
                f2 = cur_vol / base_vol if base_vol > 0 else 1.0
                prev = di - 1
                p_prev0, p_prev1 = float(series.close[prev, i0]), float(series.close[prev, i0 + D])
                f3 = np.log(p_prev1 / p_prev0) * 1e4 if p_prev0 > 0 and p_prev1 > 0 else 0.0
                st = E.reference_stats(series, di, i0)
                sig = st["sigma1m"] * np.sqrt(D)
                if sig <= 0:
                    continue
                X.append([np.log(p0 / p_open) * 1e4 / sig, f2, f3 / sig])
                y.append((p1 - p0) / p0 * 1e4 / sig)
                split.append("train" if TRAIN[0] <= date <= TRAIN[1] else "val")
    return np.array(X), np.array(y), np.array(split)


def ridge(X, y, lam):
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-12
    Z = (X - mu) / sd
    beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ y)
    return mu, sd, beta


def main() -> None:
    out = {}
    for D in (5, 15, 30, 60):
        X, y, s = build(D)
        if len(y) < 200:
            out[str(D)] = {"status": "insufficient", "n": int(len(y))}
            continue
        tr, va = s == "train", s == "val"
        mu, sd, beta = ridge(X[tr], y[tr], LAMBDA)
        pred_tr = ((X[tr] - mu) / sd) @ beta
        pred = ((X[va] - mu) / sd) @ beta
        ss_res = float(((y[va] - pred) ** 2).sum())
        ss_tot = float(((y[va] - y[va].mean()) ** 2).sum())
        ss_zero = float((y[va] ** 2).sum())
        out[str(D)] = {
            "n_train": int(tr.sum()), "n_val": int(va.sum()),
            "ic_train": round(float(np.corrcoef(pred_tr, y[tr])[0, 1]), 4),
            "ic_val": round(float(np.corrcoef(pred, y[va])[0, 1]), 4),
            "dir_acc_val": round(float(np.mean(np.sign(pred) == np.sign(y[va]))), 4),
            "r2_val_vs_mean": round(1 - ss_res / ss_tot, 5) if ss_tot > 0 else None,
            "r2_val_vs_zero": round(1 - ss_res / ss_zero, 5) if ss_zero > 0 else None,
            "mae_val": round(float(np.mean(np.abs(y[va] - pred))), 4),
            "mae_zero": round(float(np.mean(np.abs(y[va]))), 4),
            "coef_standardised": [round(float(b), 5) for b in beta],
            "feature_mean": [round(float(m), 5) for m in mu],
            "feature_sd": [round(float(v), 5) for v in sd],
        }
        print(json.dumps({k: out[str(D)][k] for k in ("n_train", "n_val", "ic_val", "dir_acc_val", "r2_val_vs_zero")}))
    with open(os.path.join(RES, "expectation_model_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
