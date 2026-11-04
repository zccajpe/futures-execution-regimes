"""Replay engine v2: integer lots, per-minute impact loadings, continuous markout.

Fixes applied after review:
  * integer lots everywhere; cumulative floor targets; Q forced once t>=h
  * impact loading is per-minute: L_phi = sum_t [(q_t P_t)/(Q P0)] * (q_t/(0.01 V_t))^phi
  * missing bars are never treated as zero volume/price: a case is usable only if
    its whole reference/execution slice is present in the mask
  * markout uses real clock continuity inside a continuous trading segment
  * ex-ante sigma comes from prior-20-day minute returns, not from the case day
"""
from __future__ import annotations

import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DERIVED = os.path.join(ROOT, "raw", "derived")

SIZE_TIERS = (0.0025, 0.01, 0.04)
DS = (5, 15, 30, 60)
HS = (1, 5, 10, 20, 30, 60)
CAPS = (0.05, 0.10, 0.20)
REF_BARS = 60
LOOKBACK = 20
CFE = {"IF", "IC", "T"}
RELEASE = {"am_c": "09:15", "am_f": "09:45", "pm_c": "13:35", "pm_f": "13:05", "night": "21:15"}
RHO_REF = 0.01


def release_label(product: str, window: str) -> str:
    if window == "night":
        return RELEASE["night"]
    kind = "f" if product in CFE else "c"
    return RELEASE[f"{window}_{kind}"]


def label_minutes(lab: str) -> int:
    return int(lab[:2]) * 60 + int(lab[3:5])


def segment_ids(labels: list[str]) -> np.ndarray:
    """Continuous-trading-segment id per grid position (a break starts a new segment)."""
    ids = np.zeros(len(labels), dtype=np.int32)
    for i in range(1, len(labels)):
        gap = (label_minutes(labels[i]) - label_minutes(labels[i - 1])) % 1440
        ids[i] = ids[i - 1] + (1 if gap != 1 else 0)
    return ids


def int_targets(method: str, h: int, Q: int, cumw: np.ndarray, D: int) -> np.ndarray:
    """Cumulative integer target; equals Q for every t >= h."""
    if method == "asap":
        return np.full(D, Q, dtype=np.int64)
    if method == "twap":
        frac = np.minimum(np.arange(1, D + 1, dtype=np.float64) / h, 1.0)
    else:
        frac = np.minimum(cumw[:D] / cumw[h - 1], 1.0)
    tgt = np.minimum(np.floor(Q * frac + 1e-12).astype(np.int64), Q)
    if h <= D:
        tgt[h - 1:] = Q
    else:
        tgt[D - 1] = Q
    return tgt


def simulate_fills_int(target: np.ndarray, capv: np.ndarray) -> np.ndarray:
    """Integer fills for a non-decreasing integer target under integer capacity."""
    cumcap = np.cumsum(capv)
    slack = np.minimum(0, np.minimum.accumulate(target - cumcap))
    cum = np.clip(cumcap + slack, 0, target[-1])
    cum = np.maximum.accumulate(cum)
    fills = np.diff(np.concatenate(([0], cum)))
    return np.maximum(fills, 0)


def arm_specs(D: int) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = [("asap", 0)]
    for h in HS:
        if h <= D and h > 1:
            out.append(("twap", h))
            out.append(("shape", h))
    return out


class Series:
    __slots__ = ("product", "window", "dates", "labels", "volume", "vwap", "close", "mask",
                 "contracts", "seg", "label_min", "clock", "vwap_valid", "proxy")

    def __init__(self, product, window, z):
        self.product = product
        self.window = window
        self.dates = [str(d) for d in z["dates"]]
        self.labels = [str(x) for x in z["labels"]]
        self.volume = z["volume"].astype(np.float64)
        self.vwap = z["vwap_raw"].astype(np.float64)
        self.vwap_valid = z["vwap_valid"].astype(bool)
        self.close = z["close"].astype(np.float64)
        self.mask = z["mask"].astype(bool)
        self.contracts = z["contracts"] if "contracts" in z.files else np.array([])
        self.seg = segment_ids(self.labels)
        self.label_min = np.array([label_minutes(l) for l in self.labels], dtype=np.int64)
        self.proxy = "close"
        self.clock: dict[tuple[int, int], int] = {}
        for i, (s, m) in enumerate(zip(self.seg, self.label_min)):
            self.clock[(int(s), int(m))] = i


def load_series(product: str, window: str, proxy: str = "close") -> Series | None:
    path = os.path.join(DERIVED, f"bars_{product}_{window}.npz")
    if not os.path.exists(path):
        return None
    ser = Series(product, window, np.load(path, allow_pickle=False))
    ser.proxy = proxy
    return ser


def forward_clock_index(series: Series, abs_pos: int, k: int) -> int | None:
    """Position k clock minutes after abs_pos, inside the same continuous segment."""
    seg = int(series.seg[abs_pos])
    target = (int(series.label_min[abs_pos]) + k) % 1440
    return series.clock.get((seg, target))


def reference_stats(series: Series, day_idx: int, i0: int, lookback: int = LOOKBACK):
    """Prior-N-day reference volume, volume shape and ex-ante 1-minute sigma."""
    prior = list(range(max(0, day_idx - lookback), day_idx))
    a, b = i0 + 1, i0 + 1 + REF_BARS
    if b > series.volume.shape[1] or not prior:
        return None
    vol = series.volume[prior, a:b]
    msk = series.mask[prior, a:b]
    ok = msk.all(axis=1)
    if not ok.any():
        return None
    vol = vol[ok]
    px = series.close[prior, a:b][ok]
    daily = vol.sum(axis=1)
    keep = daily > 0
    vol, px, daily = vol[keep], px[keep], daily[keep]
    if len(daily) == 0:
        return None
    v_avg = float(daily.mean())
    w = vol / daily[:, None]
    cumw = np.cumsum(w.mean(axis=0))
    cumw = cumw / cumw[-1]
    rets = np.diff(np.log(np.maximum(px, 1e-12)), axis=1).ravel()
    sigma1m = float(np.std(rets) * 1e4) if rets.size else 0.0
    return {"v_avg": v_avg, "cumw": cumw, "sigma1m": sigma1m, "n_days": int(len(daily))}
