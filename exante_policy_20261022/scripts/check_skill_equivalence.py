#!/usr/bin/env python3
"""Check that the skill's ex-ante mode and the research evaluation agree.

For a few fixed units (central configuration and one switching unit) the script
builds the same history NPZ the skill consumes, runs the skill, and compares its
research candidate with the research decision file. It also repeats one request
with the post-release bars overwritten to prove they cannot change the answer.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(STAGE)
sys.path.insert(0, HERE)

import make_exante_request as M  # noqa: E402
import importlib.util  # noqa: E402
CORE = os.path.join(ROOT, "delivery", "futures-execution-decision", "scripts", "exante_core.py")
_spec = importlib.util.spec_from_file_location("exante_core_eq", CORE)
XC = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(XC)
PARAMS = json.load(open(os.path.join(ROOT, "delivery", "futures-execution-decision", "data", "exante_params.json")))

UNITS = [
    ("RB", "am", 20260701, 1, 60),
    ("CU", "pm", 20260701, 2, 60),
    ("IF", "pm", 20260701, 1, 15),
    ("SC", "night", 20260701, 1, 60),
    ("CU", "am", 20260701, 2, 60),
]


def research_decision(split_file):
    out = {}
    with open(os.path.join(STAGE, split_file)) as fh:
        for r in csv.DictReader(fh):
            key = (r["product"], r["window"], int(r["date"]), int(r["size"]), int(r["D"]), r["side"])
            out[key] = r
    return out


def main() -> None:
    decisions = research_decision("decisions_test.csv")
    rows = []
    for (prod, win, date, size, D) in UNITS:
        tag = f"{prod}_{win}_{date}"
        got = M.build(product=prod, window=win, date=date, size_idx=size, D=D, out_tag=tag)
        resp = json.load(open(got["response"]))
        cand = (resp.get("research_candidate") or {}).get("arm")
        key = (prod, win, date, size, D, "buy")
        rec = decisions.get(key)
        # independent recomputation of risk / feasibility on the same NPZ
        z = np.load(os.path.join(STAGE, "history", got["history"]), allow_pickle=False)
        labels = [str(x) for x in z["labels"]]
        i0 = labels.index(__import__("engine").release_label(prod, win))
        di = [str(x) for x in z["dates"]].index(str(date))
        ref = XC.reference(z["volume"], z["close"], z["mask"], labels, i0, di)
        st = XC.state(z["volume"], z["close"], z["mask"], labels, i0, di)
        zz = XC.z_pre(st["r_pre_bp"], ref["sigma1m"], st["steps"])
        pred = XC.arm_stats(ref["V"], ref["cumw"], got["Q"], D, 0.10)
        b = XC.bucket_index(zz, st["v_pre"], PARAMS["edges"])
        rho = float(PARAMS["buckets"][b]["rho"])
        cells = {arm: PARAMS["sigma_cells"].get(f"{prod}|{win}|{size}|{D}|{arm}") for arm in pred}
        budget = float((resp.get("reference") or {}).get("budget_bp"))
        ch = XC.choose(pred, ref["sigma1m"], rho, float(0.5), 1, budget, 1.0, cells,
                       kappa=float(PARAMS["kappa_sigma"]))
        risk_ref = {arm: XC.predicted_risk(pred, arm, budget, 1.0, cells, kappa=float(PARAMS["kappa_sigma"]))
                    for arm in pred}
        risk_skill = (resp.get("research_candidate") or {}).get("risk_by_arm") or {}
        risk_ok = all(abs(risk_ref[a] - risk_skill.get(a, 1e9)) < 1e-9 for a in risk_ref)
        feas_ok = sorted(ch["feasible"]) == sorted((resp.get("research_candidate") or {}).get("feasible") or [])
        rows.append({"product": prod, "window": win, "date": date, "size": size, "D": D,
                     "Q": got["Q"], "skill_status": resp.get("status"),
                     "skill_candidate": cand, "research_arm": rec["P_arm"] if rec else None,
                     "match": bool(rec and cand == rec["P_arm"]),
                     "risk_by_arm_match": bool(risk_ok), "feasible_set_match": bool(feas_ok),
                     "research_choice_recomputed": ch["arm"],
                     "skill_top_choice": resp.get("choice"),
                     "skill_budget": (resp.get("reference") or {}).get("budget_bp")})

    # leakage on real data: overwrite everything after the release bar on the release day
    leak = M.build(product="RB", window="am", date=20260701, size_idx=1, D=60, out_tag="RB_leak_base")
    z = dict(np.load(os.path.join(STAGE, "history", leak["history"]), allow_pickle=False))
    z2 = {k: np.array(v, copy=True) for k, v in z.items()}
    z2["close"][-1, 16:] = 1.0e9
    z2["volume"][-1, 16:] = 1.0e9
    alt_name = "exante_leak_perturbed.npz"
    np.savez_compressed(os.path.join(STAGE, "history", alt_name), **z2)
    req = json.load(open(leak["request"]))
    req["history_npz"] = f"../history/{alt_name}"
    rq = os.path.join(STAGE, "requests", "exante_leak_perturbed.json")
    rs = os.path.join(STAGE, "responses", "exante_leak_perturbed.json")
    json.dump(req, open(rq, "w"), ensure_ascii=False, indent=1)
    p = subprocess.run([sys.executable, M.DECIDE, "--request", rq, "--out", rs], capture_output=True, text=True)
    base = json.load(open(leak["response"]))
    pert = json.load(open(rs))
    same = all(json.dumps(base.get(k), sort_keys=True, ensure_ascii=False) ==
               json.dumps(pert.get(k), sort_keys=True, ensure_ascii=False)
               for k in ("status", "choice", "state", "research_candidate", "reference"))

    out = {"units": rows, "all_match": all(r["match"] for r in rows),
           "all_risk_match": all(r["risk_by_arm_match"] for r in rows),
           "all_feasible_match": all(r["feasible_set_match"] for r in rows),
           "leak_check": {"exit": p.returncode, "identical": bool(same),
                          "note": "post-release bars overwritten with 1e9; state/candidate/choice must not change"},
           "n_units": len(rows)}
    json.dump(out, open(os.path.join(STAGE, "skill_equivalence.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
