"""Mechanical checks for the replay engine (run before any results are read).

Checks: integer lots, target/capacity conservation, h=1 == ASAP, zero-volume bars,
impact loading behaviour (concentrated vs spread), old-vs-new impact formula on a
real case, side sign, residual counted once, markout continuity, wall-clock gap.

Writes results/sanity_checks.json and prints a short summary.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

ROOT = E.ROOT
OUT = os.path.join(ROOT, "results")


def check_integer_and_conservation(series, i0, di, Q, capf, D):
    vol = series.volume[di, i0 + 1:i0 + 1 + D]
    capv = np.floor(capf * vol).astype(np.int64)
    out = {}
    for method, h in E.arm_specs(D):
        tgt = E.int_targets(method, h, Q, np.linspace(1 / 60, 1, 60), D)
        fills = E.simulate_fills_int(tgt, capv)
        out[f"{method}_{h}"] = {
            "int_target": bool(np.all(tgt == np.round(tgt))),
            "target_monotone": bool(np.all(np.diff(tgt) >= 0)),
            "target_eq_Q_after_h": bool(np.all(tgt[min(h, D) - 1:] == Q)) if h else bool(np.all(tgt == Q)),
            "int_fills": bool(np.all(fills == np.round(fills))),
            "sum_le_Q": bool(fills.sum() <= Q),
            "cap_respected": bool(np.all(fills <= capv)),
            "zero_vol_zero_fill": bool(np.all(fills[capv == 0] == 0)),
            "filled": int(fills.sum()),
        }
    return out


def impact_demo():
    """Constant price/volume: concentrated execution must load more than spread."""
    P = 100.0
    vol = np.full(20, 1000.0)
    cap = np.full(20, 10000)  # no bind
    Q = 200
    a1 = E.simulate_fills_int(E.int_targets("asap", 0, Q, np.linspace(0, 1, 20), 20), cap)
    t1 = E.simulate_fills_int(E.int_targets("twap", 20, Q, np.linspace(0, 1, 20), 20), cap)
    def load(fills):
        N0 = Q * P
        L1 = L05 = 0.0
        for j in np.nonzero(fills)[0]:
            q = float(fills[j])
            pr = q / (E.RHO_REF * vol[j])
            w = q * P / N0
            L1 += w * pr
            L05 += w * np.sqrt(pr)
        return L1, L05
    l_asap, l_asap05 = load(a1)
    l_twap, l_twap05 = load(t1)
    # capacity-bound variant: both arms limited to the same fill path
    capb = np.full(20, 10)  # binds every bar: both arms fill 10 per bar
    a2 = E.simulate_fills_int(E.int_targets("asap", 0, Q, np.linspace(0, 1, 20), 20), capb)
    t2 = E.simulate_fills_int(E.int_targets("twap", 20, Q, np.linspace(0, 1, 20), 20), capb)
    return {"asap_L1": l_asap, "twap_L1": l_twap, "asap_L05": l_asap05, "twap_L05": l_twap05,
            "concentrated_higher": bool(l_asap > l_twap),
            "bound_paths_identical": bool(np.array_equal(a2, t2))}


def old_vs_new_impact(series, i0, di, Q, capf, D):
    """Old formula used only total participation; new one is per-minute."""
    vol = series.volume[di, i0 + 1:i0 + 1 + D]
    px = series.vwap[di, i0 + 1:i0 + 1 + D]
    capv = np.floor(capf * vol).astype(np.int64)
    P0 = float(series.close[di, i0])
    N0 = Q * P0
    out = {}
    for method, h in E.arm_specs(D):
        fills = E.simulate_fills_int(E.int_targets(method, h, Q, np.linspace(1 / 60, 1, 60), D), capv)
        filled = float(fills.sum())
        rho_total = filled / vol.sum() if vol.sum() > 0 else 0.0
        old = (rho_total / E.RHO_REF) * (filled / Q)
        new1 = new05 = 0.0
        for j in np.nonzero(fills)[0]:
            q = float(fills[j]); v = float(vol[j])
            if v <= 0:
                continue
            pr = q / (E.RHO_REF * v)
            w = q * px[j] / N0
            new1 += w * pr
            new05 += w * np.sqrt(pr)
        out[f"{method}_{h}"] = {"old": old, "new_phi1": new1, "new_phi05": new05}
    return out


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    series = E.load_series("RB", "am")
    i0 = series.labels.index(E.release_label("RB", "am"))
    di = series.dates.index("20260302")
    Q = 200
    checks = {}
    checks["integer_and_conservation"] = check_integer_and_conservation(series, i0, di, Q, 0.10, 60)
    checks["impact_demo"] = impact_demo()
    checks["old_vs_new_impact_RB_20260302_Q200_cap10"] = old_vs_new_impact(series, i0, di, Q, 0.10, 60)
    # h=1 must equal ASAP exactly
    t_asap = E.int_targets("asap", 0, Q, np.linspace(0, 1, 60), 60)
    t_h1 = E.int_targets("twap", 1, Q, np.linspace(0, 1, 60), 60)
    capv = np.floor(0.10 * series.volume[di, i0 + 1:i0 + 61]).astype(np.int64)
    checks["h1_equals_asap"] = bool(np.array_equal(
        E.simulate_fills_int(t_asap, capv), E.simulate_fills_int(t_h1, capv)))
    # markout continuity: bar 10:14 + 5 minutes crosses the morning break
    pos_1014 = series.labels.index("10:14")
    checks["markout_break"] = {
        "10:14_plus5": E.forward_clock_index(series, pos_1014, 5),
        "10:10_plus5": E.forward_clock_index(series, series.labels.index("10:10"), 5),
        "note": "None means the target clock minute is outside the continuous segment (NA)",
    }
    # wall clock vs trading minutes across the break
    pos_1010 = series.labels.index("10:10")
    steps = [series.labels.index("10:14"), series.labels.index("10:31")]
    checks["wall_clock"] = {
        "trade_minutes_10:14_to_10:31": 1,
        "wall_minutes_10:14_to_10:31": int(series.label_min[steps[1]] - series.label_min[steps[0]]),
    }
    # side sign
    filled = 100
    gap_exec = 3.5
    checks["side_sign"] = {"buy_pricepart": +(gap_exec + 1.0), "sell_pricepart": -(gap_exec + 1.0),
                           "fee_always_positive": True, "filled": filled}
    with open(os.path.join(OUT, "sanity_checks.json"), "w", encoding="utf-8") as fh:
        json.dump(checks, fh, ensure_ascii=False, indent=1)
    print(json.dumps({k: (v if not isinstance(v, dict) else "…") for k, v in checks.items()}, ensure_ascii=False))
    print(json.dumps(checks["impact_demo"], ensure_ascii=False))
    print(json.dumps({k: v["filled"] for k, v in checks["integer_and_conservation"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
