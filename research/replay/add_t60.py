"""Add a common-T (60 trading minutes) residual term to the replay results.

For every arm row we keep the realised execution cost and fees inside its own
deadline D, but revalue the unfilled remainder at the common observation point
T = 60 trading minutes:  gap_resid_T60 = R * (close[i0+60] - P0) / (Q * P0) * 1e4.
No re-simulation is needed; only the residual leg is recomputed from the same
minute arrays, so Q, arrival price and the price path stay unchanged.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

ROOT = E.ROOT
RES = os.path.join(ROOT, "results")
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
HORIZON = 60


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RES, "replay_train_val.npz")
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(RES, "replay_train_val_t60.npz")
    with np.load(src, allow_pickle=False) as z:
        data = {k: np.array(z[k], copy=True) for k in z.files}
    keys = data["keys"]
    n = len(keys)
    out = np.full(n, np.nan)
    cache = {}
    for pi in range(len(PRODUCTS)):
        for wi, wname in enumerate(WINDOWS):
            series = E.load_series(PRODUCTS[pi], wname)
            if series is None:
                continue
            if E.release_label(PRODUCTS[pi], wname) not in series.labels:
                continue
            i0 = series.labels.index(E.release_label(PRODUCTS[pi], wname))
            if i0 + 1 + HORIZON > len(series.labels):
                continue
            for di, date in enumerate(series.dates):
                cache[(pi, wi, int(date))] = (float(series.close[di, i0]), float(series.close[di, i0 + HORIZON]))
    for i in range(n):
        key = (int(keys[i, 0]), int(keys[i, 1]), int(keys[i, 2]))
        pp = cache.get(key)
        if pp is None:
            continue
        P0, PT = pp
        Q = data["Q"][i]
        R = data["resid"][i]
        if P0 > 0 and Q > 0:
            out[i] = R * (PT - P0) / (Q * P0) * 1e4
    data["gap_resid_T60"] = out
    np.savez_compressed(dst, **data)
    print({"rows": n, "with_T60": int(np.isfinite(out).sum()),
           "saved": dst})


if __name__ == "__main__":
    main()
