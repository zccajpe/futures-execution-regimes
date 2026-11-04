"""Ex-ante execution-schedule rule: shared implementation.

This module is the single implementation used by BOTH the research scripts
(exante_policy_20261022/) and the futures-execution-decision skill
(mode=exante). It contains no I/O beyond what the caller passes in and no
side effects.

Definitions (all times are minutes after the release instant):

  target(k)   integer cumulative target of arm k=(method,h); reaches Q at h
  capv_t      floor(cap_frac * V_t) for the *prior* day's volume path
  T           exposure minutes  = sum_t fills_t * t / Q + 60 * resid / Q
  L           impact loading (phi=1) = sum_t (fills_t/Q) * (fills_t/(0.01*V_t))
  comp        predicted completion = mean over prior paths of (filled == Q)
  fee         predicted fee load   = mean over prior paths of filled/Q

Predictions only ever read volume paths from days strictly before the release
day and, for the state, the current day's bars up to and including the release
bar (bar labels are END times, so that bar ends exactly at the release instant).
"""
from __future__ import annotations

import math

import numpy as np

HORIZON = 60
LOOKBACK = 20
# The frozen risk table stores budget_bp rounded to 4 decimals, while the TRAIN
# cell SD is kept at full precision. Half a stored unit (5e-5 bp) is therefore a
# storage artefact, not risk. Any comparison of a predicted SD against budget_bp
# uses this absolute tolerance for every arm alike; it is NOT an economic
# tolerance and must not be scaled with the budget.
BUDGET_STORAGE_TOL_BP = 5e-5
FLOAT_TOL_BP = 1e-9
ARMS = (("asap", 0), ("twap", 5), ("twap", 10), ("twap", 20), ("twap", 30), ("twap", 60),
        ("shape", 5), ("shape", 10), ("shape", 20), ("shape", 30), ("shape", 60))


def arm_name(method: str, h: int) -> str:
    return f"{method}{h}"


def legal_arms(D: int):
    """ASAP plus twap/shape with 5 <= h <= D (same set as engine.arm_specs)."""
    return [a for a in ARMS if a[0] == "asap" or 5 <= a[1] <= D]


def schedule(method: str, h: int, Q: int, cumw, D: int) -> np.ndarray | None:
    """Cumulative integer target, identical to engine.int_targets."""
    if method == "asap":
        return np.full(D, Q, dtype=np.int64)
    if method == "twap":
        frac = np.minimum(np.arange(1, D + 1, dtype=np.float64) / h, 1.0)
    else:
        if h - 1 >= len(cumw) or cumw[h - 1] <= 0:
            return None
        frac = np.minimum(cumw[:D] / cumw[h - 1], 1.0)
    tgt = np.minimum(np.floor(Q * frac + 1e-12).astype(np.int64), Q)
    tgt[h - 1:] = Q
    return tgt


def simulate(target: np.ndarray, capv: np.ndarray) -> np.ndarray:
    """Integer fills per path, identical to engine.simulate_fills_int."""
    cumcap = np.cumsum(capv, axis=1)
    slack = np.minimum(0, np.minimum.accumulate(target[None, :] - cumcap, axis=1))
    cum = np.clip(cumcap + slack, 0, target[-1])
    cum = np.maximum.accumulate(cum, axis=1)
    return np.maximum(np.diff(np.concatenate([np.zeros((capv.shape[0], 1)), cum], axis=1), axis=1), 0)


def arm_stats(V: np.ndarray, cumw: np.ndarray, Q: int, D: int, cap_frac: float,
              fee_bp: float = 1.3) -> dict:
    """Per-arm predicted statistics from prior volume paths V (n_paths x HORIZON)."""
    n = V.shape[0]
    capv = np.floor(cap_frac * V[:, :D]).astype(np.int64)
    t = np.arange(1, D + 1, dtype=np.float64)
    out = {}
    for method, h in legal_arms(D):
        tgt = schedule(method, h, Q, cumw, D)
        if tgt is None:
            continue
        f = simulate(tgt, capv)
        filled = f.sum(axis=1)
        resid = Q - filled
        T = ((f * t).sum(axis=1) + HORIZON * resid) / Q
        Vd = np.maximum(V[:, :D], 1e-12)
        L = ((f / Q) * (f / (0.01 * Vd))).sum(axis=1)
        out[arm_name(method, h)] = {
            "T": float(T.mean()),
            "L": float(L.mean()),
            "comp": float((filled >= Q).mean()),
            "fee": float(fee_bp * (filled / Q).mean()),
            "resid_frac": float((resid / Q).mean()),
            "n_paths": int(n),
        }
    return out


def segment_ids(labels) -> np.ndarray:
    """Continuous trading segment id per grid position (same rule as engine)."""
    mins = [int(x[:2]) * 60 + int(x[3:5]) for x in labels]
    ids = np.zeros(len(labels), dtype=np.int64)
    for i in range(1, len(labels)):
        gap = (mins[i] - mins[i - 1]) % 1440
        ids[i] = ids[i - 1] + (1 if gap != 1 else 0)
    return ids


def segment_start(labels, i0: int) -> int:
    seg = segment_ids(labels)
    return int(np.argmax(seg == seg[i0]))


def reference(volume: np.ndarray, close: np.ndarray, mask: np.ndarray, labels, i0: int,
              day_idx: int, lookback: int = LOOKBACK) -> dict | None:
    """Prior-N-day window volume/shape/sigma. Only days strictly before day_idx."""
    a, b = i0 + 1, i0 + 1 + HORIZON
    if b > volume.shape[1]:
        return None
    prior = [j for j in range(max(0, day_idx - lookback), day_idx)]
    if len(prior) < lookback:
        return None
    # A zero-volume minute is a REAL minute with zero executable capacity, not a
    # missing bar: it is kept. Only structural validity disqualifies a day.
    ok = []
    for j in prior:
        if not mask[j, a:b].all():
            continue
        c = close[j, a:b]
        v = volume[j, a:b]
        if not (np.isfinite(c).all() and (c > 0).all()):
            continue
        if not (np.isfinite(v).all() and (v >= 0).all()):
            continue
        if float(v.sum()) <= 0:
            continue
        ok.append(j)
    if len(ok) < lookback:
        return None
    V = volume[ok, a:b].astype(np.float64)
    P = close[ok, a:b].astype(np.float64)
    daily = V.sum(axis=1)
    if not (daily > 0).all():
        return None
    cumw = np.cumsum((V / daily[:, None]).mean(axis=0))
    cumw = cumw / cumw[-1]
    rets = np.diff(np.log(np.maximum(P, 1e-12)), axis=1).ravel()
    sigma = float(np.std(rets) * 1e4) if rets.size else float("nan")
    if not math.isfinite(sigma):
        return None
    return {"V": V, "cumw": cumw, "V_ref": float(daily.mean()),
            "sigma1m": sigma, "n_days": len(ok)}


def state(volume: np.ndarray, close: np.ndarray, mask: np.ndarray, labels, i0: int,
          day_idx: int, lookback: int = LOOKBACK) -> dict | None:
    """Release-time state from the current day's completed bars only (<= i0)."""
    s0 = segment_start(labels, i0)
    n_pre = i0 - s0 + 1
    if n_pre < 2:
        return None
    cur = slice(s0, i0 + 1)

    def _valid_prefix_row(j):
        """Every completed minute of the prefix must be usable; zero volume is allowed."""
        if not mask[j, cur].all():
            return False
        c = close[j, cur]
        v = volume[j, cur]
        if not (np.isfinite(c).all() and (c > 0).all()):
            return False
        if not (np.isfinite(v).all() and (v >= 0).all()):
            return False
        return True

    if not _valid_prefix_row(day_idx):
        return None
    c0, c1 = float(close[day_idx, s0]), float(close[day_idx, i0])
    r_pre = 1e4 * math.log(c1 / c0)
    prior = [j for j in range(max(0, day_idx - lookback), day_idx) if _valid_prefix_row(j)]
    if len(prior) < max(5, lookback // 2):
        return None
    base_vol, base_amv = [], []
    for j in prior:
        p = close[j, cur]
        r = np.diff(np.log(np.maximum(p, 1e-12)))
        base_amv.append(float(np.std(r)))
        base_vol.append(float(volume[j, cur].sum()))
    cur_r = np.diff(np.log(np.maximum(close[day_idx, cur], 1e-12)))
    cur_amv = float(np.std(cur_r))
    base_amv = float(np.mean(base_amv))
    base_vol = float(np.mean(base_vol))
    v_pre = (cur_amv / base_amv) if base_amv > 0 else 1.0
    act_pre = (float(volume[day_idx, cur].sum()) / base_vol) if base_vol > 0 else 1.0
    vals = (r_pre, v_pre, act_pre, base_amv, base_vol, cur_amv)
    if not all(isinstance(x, float) and math.isfinite(x) for x in vals):
        return None
    return {"r_pre_bp": float(r_pre), "n_pre": int(n_pre), "steps": int(n_pre - 1),
            "v_pre": float(v_pre), "act_pre": float(act_pre), "n_base_days": len(prior)}


def prefix_activity(volume, close, mask, labels, i0: int, day_idx: int, lookback: int = LOOKBACK):
    """Strict pre-release activity with NO 1.0 fallback.

    Returns (dict, None) or (None, reason). The current day's prefix and the prior
    lookback-day same-prefix slices must all be mask-valid, close finite positive,
    volume finite non-negative, and the prior same-prefix mean must be > 0.
    """
    s0 = segment_start(labels, i0)
    n_pre = i0 - s0 + 1
    if n_pre < 2:
        return None, "pre-release prefix has fewer than 2 bars"
    cur = slice(s0, i0 + 1)

    def valid(j):
        c = close[j, cur]
        v = volume[j, cur]
        return (mask[j, cur].all() and np.isfinite(c).all() and (c > 0).all()
                and np.isfinite(v).all() and (v >= 0).all())

    if not valid(day_idx):
        return None, "the current day's pre-release prefix is invalid (mask/price/volume)"
    prior = [j for j in range(max(0, day_idx - lookback), day_idx) if valid(j)]
    if len(prior) < lookback:
        return None, f"only {len(prior)} valid prior pre-release prefixes; {lookback} required"
    base = float(np.mean([volume[j, cur].sum() for j in prior]))
    if not (math.isfinite(base) and base > 0):
        return None, "the prior same-prefix mean volume is zero; activity cannot be normalised (no fallback to 1)"
    cur_vol = float(volume[day_idx, cur].sum())
    if not (math.isfinite(cur_vol) and cur_vol >= 0):
        return None, "the current pre-release volume is invalid"
    return {"act_pre": cur_vol / base, "prefix_volume": cur_vol, "prefix_base": base,
            "n_pre": int(n_pre), "n_base_days": len(prior)}, None


def z_pre(r_pre_bp: float, sigma1m: float, steps: int) -> float:
    """Standardised pre-release move; steps = n_pre - 1 return intervals."""
    den = sigma1m * math.sqrt(max(steps, 1))
    return float(r_pre_bp / den) if den > 0 else 0.0


def bucket_index(z: float, v: float, edges: dict) -> int:
    def cut(x, e):
        return 0 if x < e[0] else (1 if x < e[1] else 2)
    return cut(z, edges["z_pre"]) * 3 + cut(v, edges["v_pre"])


def predict_cost(pred: dict, sigma1m: float, rho_b: float, a: float, side: int) -> dict:
    """Predicted cost per arm; price part is a DIFFERENTIAL versus ASAP."""
    base = pred["asap0"]
    out = {}
    for arm, p in pred.items():
        d_price = side * rho_b * sigma1m * (p["T"] - base["T"])
        out[arm] = {"fee": p["fee"], "impact": a * p["L"], "price_diff_vs_asap": d_price,
                    "total": p["fee"] + a * p["L"] + d_price}
    return out


def predicted_risk(pred: dict, arm: str, budget_bp: float, k: float, sigma_cell: dict, kappa: float = 20.0) -> float:
    """Shrunk SD prediction; prior = original TRAIN ASAP SD (= budget/k) scaled by sqrt(T/T_asap)."""
    asap_sd = budget_bp / k if k else budget_bp
    prior = asap_sd * math.sqrt(max(pred[arm]["T"], 1e-9) / max(pred["asap0"]["T"], 1e-9))
    cell = sigma_cell.get(arm)
    if not cell or cell[0] < 30:
        return float(prior)
    n, s = float(cell[0]), float(cell[1])
    return float(math.sqrt((n * s * s + kappa * prior * prior) / (n + kappa)))


def tie_key(arm: str):
    """Deterministic tie order fixed by the design: ASAP, then shorter h, then TWAP before shape."""
    method = arm.rstrip("0123456789")
    h = int(arm[len(method):]) if arm[len(method):] else 0
    if method == "asap":
        return (0, 0, 0)
    return (1, h, 0 if method == "twap" else 1)


def choose(pred: dict, sigma1m: float, rho_b: float, a: float, side: int, budget_bp: float,
           k: float, sigma_cell: dict, min_comp: float = 0.95, kappa: float = 20.0) -> dict:
    """Feasible set + argmin predicted cost with a fixed deterministic tie-break."""
    costs = predict_cost(pred, sigma1m, rho_b, a, side)
    feasible, excluded = [], {}
    for arm, p in pred.items():
        if p["comp"] < min_comp:
            excluded[arm] = "completion below threshold"
            continue
        risk = predicted_risk(pred, arm, budget_bp, k, sigma_cell, kappa)
        if risk > budget_bp + BUDGET_STORAGE_TOL_BP + FLOAT_TOL_BP:
            excluded[arm] = "risk above budget"
            continue
        feasible.append((costs[arm]["total"], tie_key(arm), arm, risk))
    if not feasible:
        return {"status": "NO_FEASIBLE", "arm": None, "feasible": [], "excluded": excluded, "costs": costs}
    feasible.sort(key=lambda x: (x[0], x[1]))
    best = feasible[0]
    return {"status": "OK", "arm": best[2], "predicted_total": best[0], "predicted_risk": best[3],
            "feasible": [x[2] for x in feasible], "excluded": excluded, "costs": costs}
