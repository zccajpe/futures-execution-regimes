"""Add 5-day block bootstrap intervals for the centre-cell cost differences."""
import csv, os, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results"); OUT = os.path.join(ROOT, "decision_research")
P = ["RB","I","CU","AU","SC","TA","RU","M","P","IF","IC","T"]; W=["am","pm","night"]; M=["asap","twap","shape"]
FEE, CAP = 1.3, 1
def load(p):
    with np.load(p, allow_pickle=False) as z: return {k: np.array(z[k], copy=True) for k in z.files}
R = load(os.path.join(RES, "replay_test_t60.npz")); k = R["keys"]
dec = [r for r in csv.DictReader(open(os.path.join(RES, "selector_decisions_test.csv"), encoding="utf-8"))
       if r["side"]=="buy" and int(r["size_idx"])==1 and int(r["D"])==60 and r["preference"]=="completion_first"
       and r["a_bp"]=="0.5" and r["phi"]=="1.0" and r["budget_k"]=="1.0" and r["status"]=="ok"]
idx = {}
for i in range(len(k)):
    if int(k[i,7])==CAP: idx[(int(k[i,0]),int(k[i,1]),int(k[i,2]),int(k[i,3]),int(k[i,4]),int(k[i,5]),int(k[i,6]))]=i
keys = [(P.index(r["product"]), W.index(r["window"]), int(r["date"])) for r in dec]
dates = np.array([d for _,_,d in keys])
rows = []
for m, hh in [(mm, h) for mm in ("twap","shape") for h in (5,10,20,30,60)]:
    try:
        I = np.array([idx[(p,w,d,1,60,M.index(m),hh)] for p,w,d in keys]); J = np.array([idx[(p,w,d,1,60,0,0)] for p,w,d in keys])
    except KeyError: continue
    dp = (R["gap_exec"][I]+R["gap_resid_T60"][I]) - (R["gap_exec"][J]+R["gap_resid_T60"][J])
    dfee = R["fee_load"][I]-R["fee_load"][J]
    delta = dp + FEE*dfee
    u = np.unique(dates); blk = {int(d): i//5 for i,d in enumerate(u)}
    bi = np.array([blk[int(d)] for d in dates]); nb = bi.max()+1
    s = np.bincount(bi, weights=delta, minlength=nb); c = np.bincount(bi, minlength=nb)
    rng = np.random.default_rng(3); bs = []
    for _ in range(2000):
        p = rng.integers(0, nb, nb); bs.append(s[p].sum()/c[p].sum())
    rows.append([m, hh, len(delta), len(u), nb, round(float(delta.mean()),6),
                 round(float(np.percentile(bs,2.5)),6), round(float(np.percentile(bs,97.5)),6)])
with open(os.path.join(OUT, "boundary_ci.csv"), "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["method","h","n_pairs","n_dates","n_blocks","delta_cost_bp","ci_low","ci_high"]); w.writerows(rows)
print({"rows": len(rows), "sample": rows[0] if rows else None})
