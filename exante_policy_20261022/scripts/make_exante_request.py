#!/usr/bin/env python3
"""Build one ex-ante request (history NPZ with the release-day prefix) and run the skill.

Used for the research-vs-skill equivalence check. The NPZ contains:
  * 20 complete prior trading days (the reference window),
  * the release day itself, with mask True only up to and including the release bar
    and NaN prices / zero volume after it, so that any future read would be visible.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(STAGE)
CORE = os.path.join(ROOT, "delivery", "futures-execution-decision", "scripts", "exante_core.py")
DECIDE = os.path.join(ROOT, "delivery", "futures-execution-decision", "scripts", "decision.py")
spec = importlib.util.spec_from_file_location("exante_core", CORE)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

sys.path.insert(0, os.path.join(ROOT, "scripts"))
import engine as E  # noqa: E402

SIZES = (0.0025, 0.01, 0.04)


def build(product="RB", window="am", date=20260701, size_idx=1, D=60, a=0.5, k=1.0, side="buy",
          out_tag="RB_am_20260701"):
    ser = E.load_series(product, window)
    lab = E.release_label(product, window)
    i0 = ser.labels.index(lab)
    di = ser.dates.index(str(date))
    prior = [j for j in range(di - 20, di)]
    dates = [ser.dates[j] for j in prior] + [ser.dates[di]]
    close = np.vstack([ser.close[prior], ser.close[di:di + 1]]).copy()
    volume = np.vstack([ser.volume[prior], ser.volume[di:di + 1]]).copy()
    mask = np.vstack([ser.mask[prior], ser.mask[di:di + 1]]).copy()
    # keep only the release-day prefix; blank the future so a leak would change outputs
    mask[-1, i0 + 1:] = False
    close[-1, i0 + 1:] = np.nan
    volume[-1, i0 + 1:] = 0.0
    ref = C.reference(volume[:-1][:, :], close[:-1], mask[:-1], ser.labels, i0, 20) if False else None
    # reference must be computed on the prior rows only, with day index == 20
    ref = C.reference(volume, close, mask, ser.labels, i0, 20)
    st = C.state(volume, close, mask, ser.labels, i0, 20)
    assert ref is not None and st is not None, "cannot build reference/state"
    Q = int(max(1, math.floor(SIZES[size_idx] * ref["V_ref"])))
    name = f"exante_{out_tag}_s{size_idx}_D{D}.npz"
    np.savez_compressed(os.path.join(STAGE, "history", name), dates=np.array(dates), labels=ser.labels,
                        close=close, volume=volume, mask=mask, contracts=np.array([ser.contracts[j] for j in prior] + [ser.contracts[di]]))
    import csv as _csv
    frozen_row = None
    with open(os.path.join(ROOT, "decision_research", "v2", "frozen_A_params.csv")) as fh:
        for r in _csv.DictReader(fh):
            if (r["product"] == product and r["window"] == window and int(r["size_idx"]) == size_idx
                    and int(r["D"]) == D and r["side"] == side and float(r["a_bp"]) == a
                    and float(r["risk_k"]) == k):
                frozen_row = r
                break
    assert frozen_row is not None, "no frozen budget row for this unit"
    budget = float(frozen_row["budget_bp"])
    req = {
        "mode": "exante",
        "parent": {"symbol": product, "side": side, "qty_lots": Q, "release_trading_date": int(date),
                   "release_label": lab, "Dmax_trading_minutes": int(D)},
        "history_npz": f"../history/{name}",
        "baseline_context": {"product": product, "window": window, "size_idx": size_idx, "risk_k": k},
        "constraints": {"min_completion_probability": 0.95, "max_unfilled_lots": 0,
                        "risk_sd_budget_bp": budget},
        "scenario": {"cap_frac": 0.1, "fee_spread_bp": 1.3, "impact_a_bp": a, "phi": 1.0,
                     "terminal_price_change_bp": 0.0},
    }
    rq = os.path.join(STAGE, "requests", f"{out_tag}_s{size_idx}_D{D}.json")
    rs = os.path.join(STAGE, "responses", f"{out_tag}_s{size_idx}_D{D}.json")
    os.makedirs(os.path.dirname(rq), exist_ok=True)
    os.makedirs(os.path.dirname(rs), exist_ok=True)
    json.dump(req, open(rq, "w"), ensure_ascii=False, indent=1)
    p = subprocess.run([sys.executable, DECIDE, "--request", rq, "--out", rs], capture_output=True, text=True)
    out = json.load(open(rs)) if os.path.exists(rs) else {}
    return {"request": rq, "response": rs, "exit": p.returncode, "stdout": p.stdout.strip(),
            "stderr": p.stderr.strip(), "status": out.get("status"), "choice": out.get("choice"),
            "candidate": (out.get("research_candidate") or {}).get("arm"), "Q": Q,
            "history": name}


if __name__ == "__main__":
    print(json.dumps(build(*[eval(x) for x in sys.argv[1:]]) if len(sys.argv) > 1 else build(),
                     ensure_ascii=False, indent=1))
