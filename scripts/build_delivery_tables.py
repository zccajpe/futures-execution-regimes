"""Rebuild delivery tables from existing arrays (no data fetch, no new simulation).

Outputs
  results/paired_metrics_complete.csv  same-subset metrics for selector vs each baseline
  results/case_methods.csv             three constructed parent orders x methods
  results/ab_flip.csv                  (a, b) flip thresholds incl. fee-loading term
  results/proxy_case_flip.csv          per-case price-part sign flips at 10% capacity

All inputs come from results/*.npz, results/*.csv produced earlier in this study.
"""
from __future__ import annotations

import csv
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

ROOT = E.ROOT
RES = os.path.join(ROOT, "results")
P = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
W = ["am", "pm", "night"]
M = ["asap", "twap", "shape"]
CAP = 1
FEE = 1.3


def load(p):
    with np.load(p, allow_pickle=False) as z:
        return {k: np.array(z[k], copy=True) for k in z.files}


def index_cap1(k):
    idx = {}
    for i in range(len(k)):
        if int(k[i, 7]) == CAP:
            idx[(int(k[i, 0]), int(k[i, 1]), int(k[i, 2]), int(k[i, 3]), int(k[i, 4]), int(k[i, 5]), int(k[i, 6]))] = i
    return idx


def metrics(R, I, sgn, D):
    """Equal-weight over parent orders; NA where nothing filled."""
    c = sgn * (R["gap_exec"][I] + R["gap_resid_T60"][I]) + FEE * R["fee_load"][I] + 0.5 * R["L1"][I]
    comp = R["resid"][I] == 0
    wall = np.where(comp, R["wall_min"][I], np.nan)
    rq = R["resid"][I] / R["Q"][I]
    wait = D * R["L_exec"][I] + 60 * R["L_res"][I]
    mo = []
    for key in ("mo5", "mo15", "mo30"):
        v = sgn * R[key][I]
        mo.append(float(np.nanmean(v)) if np.isfinite(v).any() else "")
    cov = [float(np.mean(R[k_][I])) for k_ in ("cov5", "cov15", "cov30")]
    return [len(I), float(c.mean()), float(comp.mean()), int(comp.sum()),
            float(np.nanmean(wall)) if np.isfinite(wall).any() else "",
            round(float(rq.mean()), 8), round(float(np.percentile(rq, 95)), 8), float(wait.mean())] + mo + cov


def paired_metrics():
    R = load(os.path.join(RES, "replay_test_t60.npz"))
    k = R["keys"]
    idx = index_cap1(k)
    fixed = {}
    for r in csv.DictReader(open(os.path.join(RES, "fixed_h_train.csv"), encoding="utf-8")):
        if r["flag"] == "chosen" and r["preference"] == "completion_first" and r["a_bp"] == "0.5" \
                and r["phi"] == "1.0" and r["budget_k"] == "1.0":
            fixed[(r["product"], r["window"], int(r["size_idx"]), int(r["D"]), r["side"], r["family"])] = int(r["h"])
    dec = list(csv.DictReader(open(os.path.join(RES, "selector_decisions_test.csv"), encoding="utf-8")))
    rows = []
    for side in ("buy", "sell"):
        sgn = 1.0 if side == "buy" else -1.0
        for size in (1,):
            for D in (60,):
                sub = [r for r in dec if r["side"] == side and int(r["size_idx"]) == size and int(r["D"]) == D
                       and r["preference"] == "completion_first" and r["a_bp"] == "0.5"
                       and r["phi"] == "1.0" and r["budget_k"] == "1.0"]
                uni = len(sub)
                rec = sum(1 for r in sub if r["status"] == "ok")
                for base in ("asap", "fixed_twap", "fixed_shape"):
                    prs = []
                    for r in sub:
                        if r["status"] != "ok":
                            continue
                        pi, wi, date = P.index(r["product"]), W.index(r["window"]), int(r["date"])
                        i_s = idx.get((pi, wi, date, size, D, M.index(r["recommended_method"]), int(r["recommended_h"])))
                        if base == "asap":
                            i_b = idx.get((pi, wi, date, size, D, 0, 0))
                        else:
                            fam = "twap" if base == "fixed_twap" else "shape"
                            hh = fixed.get((r["product"], r["window"], size, D, side, fam))
                            i_b = idx.get((pi, wi, date, size, D, 1 if fam == "twap" else 2, hh)) if hh else None
                        if i_s is None or i_b is None:
                            continue
                        prs.append((i_s, i_b))
                    if not prs:
                        rows.append(["test", side, size, D, base, 0, uni, round(rec / uni, 4)] + [""] * 28)
                        continue
                    A = np.array([p[0] for p in prs])
                    B = np.array([p[1] for p in prs])
                    rows.append(["test", side, size, D, base, len(A), uni, round(rec / uni, 4)]
                                + metrics(R, A, sgn, D) + metrics(R, B, sgn, D))
    head = ["split", "side", "size_idx", "D", "baseline", "n_paired", "n_universe", "recommendation_coverage"]
    cols = ["n", "cost_bp", "completion", "n_completed", "completion_time_min", "R_over_Q_mean",
            "R_over_Q_p95", "wait_burden_min", "mo5_bp", "mo15_bp", "mo30_bp", "mo5_cov", "mo15_cov", "mo30_cov"]
    with open(os.path.join(RES, "paired_metrics_complete.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(head + [f"sel_{c}" for c in cols] + [f"base_{c}" for c in cols])
        w.writerows(rows)
    return len(rows)


def case_methods():
    R = load(os.path.join(RES, "replay_test_t60.npz"))
    k = R["keys"]
    idx = index_cap1(k)
    cases = [("SC", "night", 20260916, "buy"), ("I", "night", 20260818, "buy"), ("IF", "pm", 20260730, "buy")]
    rows = []
    for prod, win, date, side in cases:
        pi, wi = P.index(prod), W.index(win)
        sgn = 1.0 if side == "buy" else -1.0
        for m, hh in (("asap", 0), ("twap", 30), ("twap", 60), ("shape", 30), ("shape", 60)):
            i = idx.get((pi, wi, date, 1, 60, M.index(m), hh))
            if i is None:
                continue
            rows.append([prod, win, date, side, int(R["Q"][i]), m, hh,
                         round(float(sgn * (R["gap_exec"][i] + R["gap_resid_T60"][i]) + FEE * R["fee_load"][i] + 0.5 * R["L1"][i]), 2),
                         round(float(R["wall_min"][i]), 1) if np.isfinite(R["wall_min"][i]) else "",
                         int(R["resid"][i]),
                         round(float(sgn * R["mo5"][i]), 2) if np.isfinite(R["mo5"][i]) else "",
                         round(float(sgn * R["mo15"][i]), 2) if np.isfinite(R["mo15"][i]) else "",
                         round(float(sgn * R["mo30"][i]), 2) if np.isfinite(R["mo30"][i]) else ""])
    with open(os.path.join(RES, "case_methods.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "window", "date", "side", "Q_lots", "method", "h", "cost_t60_bp",
                    "completion_time_min", "resid_lots", "markout5_bp", "markout15_bp", "markout30_bp"])
        w.writerows(rows)
    return len(rows)


def ab_flip():
    a = load(os.path.join(RES, "replay_train_val_t60.npz"))
    b = load(os.path.join(RES, "replay_test_t60.npz"))
    R = {k: np.concatenate([a[k], b[k]]) for k in a}
    k = R["keys"]
    rows = []
    for size in range(3):
        for D in (30, 60):
            key = (k[:, 0].astype(np.int64) * 100 + k[:, 1]) * 100000000 + k[:, 2]
            for m1, h1 in [(m, hh) for m in ("twap", "shape") for hh in (5, 10, 20, 30, 60) if hh <= D]:
                A = (k[:, 7] == CAP) & (k[:, 3] == size) & (k[:, 4] == D) & (k[:, 5] == 0) & (k[:, 6] == 0)
                B = (k[:, 7] == CAP) & (k[:, 3] == size) & (k[:, 4] == D) & (k[:, 5] == M.index(m1)) & (k[:, 6] == h1)
                ia, ib = np.nonzero(A)[0], np.nonzero(B)[0]
                common, ja, jb = np.intersect1d(key[ia], key[ib], return_indices=True)
                if len(common) < 50:
                    continue
                i1, i2 = ia[ja], ib[jb]
                dp = (R["gap_exec"][i2] + R["gap_resid_T60"][i2]) - (R["gap_exec"][i1] + R["gap_resid_T60"][i1])
                dfee = R["fee_load"][i2] - R["fee_load"][i1]
                dL1 = R["L1"][i2] - R["L1"][i1]
                dl = ((D / 60.0) * R["L_exec"][i2] + R["L_res"][i2]) - ((D / 60.0) * R["L_exec"][i1] + R["L_res"][i1])
                base = float(np.mean(dp + FEE * dfee))
                sa, sb = float(np.mean(dL1)), float(np.mean(dl))
                rows.append([size, D, "buy", m1, h1, len(common), round(base, 4), round(sa, 4), round(sb, 4),
                             round(-base / sa, 4) if abs(sa) > 1e-12 else "",
                             round(-base / sb, 2) if abs(sb) > 1e-12 else ""])
    with open(os.path.join(RES, "ab_flip.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["size_idx", "D", "side", "method", "h", "n_pairs", "base_delta_bp_incl_fee",
                    "d_load_impact", "d_load_drift", "a_flip_bp", "b_flip_bp"])
        w.writerows(rows)
    return len(rows)


def proxy_case_flip():
    a = load(os.path.join(RES, "replay_train_val_t60.npz"))
    b = load(os.path.join(RES, "replay_train_val_vwaq_t60.npz"))
    key = lambda kk, i: (int(kk[i, 0]), int(kk[i, 1]), int(kk[i, 2]), int(kk[i, 3]), int(kk[i, 4]),
                         int(kk[i, 5]), int(kk[i, 6]), int(kk[i, 7]))
    ia = {key(a["keys"], i): i for i in range(len(a["keys"])) if int(a["keys"][i, 7]) == CAP}
    ib = {key(b["keys"], i): i for i in range(len(b["keys"])) if int(b["keys"][i, 7]) == CAP}
    rows = []
    for m1, h1, m2, h2 in ((0, 0, 1, 60), (0, 0, 2, 60), (1, 60, 2, 60)):
        fl = tot = 0
        for kk, i in ia.items():
            if kk[3] != 2 or kk[4] != 60 or kk[5] != m1 or kk[6] != h1:
                continue
            kk2 = (kk[0], kk[1], kk[2], kk[3], kk[4], m2, h2, kk[7])
            j, j2, jj2 = ib.get(kk), ia.get(kk2), ib.get(kk2)
            if None in (j, j2, jj2):
                continue
            dc = (a["gap_exec"][i] + a["gap_resid_T60"][i]) - (a["gap_exec"][j2] + a["gap_resid_T60"][j2])
            dv = (b["gap_exec"][j] + b["gap_resid_T60"][j]) - (b["gap_exec"][jj2] + b["gap_resid_T60"][jj2])
            tot += 1
            fl += int(np.sign(dc) != np.sign(dv))
        rows.append([f"{M[m1]}{h1}_minus_{M[m2]}{h2}", tot, fl, round(fl / tot, 4) if tot else ""])
    with open(os.path.join(RES, "proxy_case_flip.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["comparison", "n_cases", "n_sign_flips", "share_flips"])
        w.writerows(rows)
    return rows


def all_candidates_center():
    """Every declared candidate on the same 2,424 test parent orders (buy, 1%, D=60)."""
    R = load(os.path.join(RES, "replay_test_t60.npz"))
    k = R["keys"]
    idx = index_cap1(k)
    dec = [r for r in csv.DictReader(open(os.path.join(RES, "selector_decisions_test.csv"), encoding="utf-8"))
           if r["side"] == "buy" and int(r["size_idx"]) == 1 and int(r["D"]) == 60
           and r["preference"] == "completion_first" and r["a_bp"] == "0.5"
           and r["phi"] == "1.0" and r["budget_k"] == "1.0" and r["status"] == "ok"]
    dyn = []
    for r in dec:
        pi, wi, date = P.index(r["product"]), W.index(r["window"]), int(r["date"])
        i = idx.get((pi, wi, date, 1, 60, M.index(r["recommended_method"]), int(r["recommended_h"])))
        i0 = idx.get((pi, wi, date, 1, 60, 0, 0))
        if i is None or i0 is None:
            continue
        dyn.append((i, i0, date))
    arms = [("asap", 0)] + [(m, hh) for m in ("twap", "shape") for hh in (5, 10, 20, 30, 60)]
    rows, base_sel = [], {}
    for m, hh in arms:
        got = []
        for i_dyn, i0, date in dyn:
            pi, wi, date = int(k[i_dyn, 0]), int(k[i_dyn, 1]), date
            i = idx.get((pi, wi, date, 1, 60, M.index(m), hh))
            if i is None:
                continue
            got.append((i, i_dyn, date))
        if len(got) < 100:
            continue
        I = np.array([g[0] for g in got]); J = np.array([g[1] for g in got])
        dates = np.array([g[2] for g in got])
        met = metrics(R, I, 1.0, 60)
        sd_cand = float(np.std(R["gap_exec"][I] + R["gap_resid_T60"][I], ddof=1))
        rows.append([m, hh, len(I), round(met[1], 6), round(sd_cand, 6), round(met[2], 4),
                     round(met[4], 3) if met[4] != "" else "", round(met[6], 6),
                     round(met[5], 8), round(met[7], 3)])
        cA = 1.0 * (R["gap_exec"][J] + R["gap_resid_T60"][J]) + FEE * R["fee_load"][J] + 0.5 * R["L1"][J]
        cB = 1.0 * (R["gap_exec"][I] + R["gap_resid_T60"][I]) + FEE * R["fee_load"][I] + 0.5 * R["L1"][I]
        pA = R["gap_exec"][J] + R["gap_resid_T60"][J]
        pB = R["gap_exec"][I] + R["gap_resid_T60"][I]
        u = np.unique(dates); blk = {int(d): i // 5 for i, d in enumerate(u)}
        bi = np.array([blk[int(d)] for d in dates]); nb = bi.max() + 1
        n_b = np.bincount(bi, minlength=nb).astype(float)
        sC = np.bincount(bi, weights=(cA - cB), minlength=nb)
        sA = np.bincount(bi, weights=pA, minlength=nb); sA2 = np.bincount(bi, weights=pA ** 2, minlength=nb)
        sB = np.bincount(bi, weights=pB, minlength=nb); sB2 = np.bincount(bi, weights=pB ** 2, minlength=nb)
        rng = np.random.default_rng(3); mc, mr = [], []
        for _ in range(2000):
            q = rng.integers(0, nb, nb); n_ = n_b[q].sum()
            if n_ < 2:
                continue
            ma = sA[q].sum() / n_; va = max(sA2[q].sum() / n_ - ma * ma, 0.0) * n_ / (n_ - 1)
            mb = sB[q].sum() / n_; vb = max(sB2[q].sum() / n_ - mb * mb, 0.0) * n_ / (n_ - 1)
            mc.append(sC[q].sum() / n_); mr.append(np.sqrt(va) - np.sqrt(vb))
        base_sel[(m, hh)] = (float(np.mean(cA - cB)), float(np.percentile(mc, 2.5)),
                             float(np.percentile(mc, 97.5)),
                             float(np.std(pA, ddof=1)) - float(np.std(pB, ddof=1)),
                             float(np.percentile(mr, 2.5)), float(np.percentile(mr, 97.5)))
    J = np.array([g[0] for g in dyn])
    met_d = metrics(R, J, 1.0, 60)
    sd_dyn = float(np.std(R["gap_exec"][J] + R["gap_resid_T60"][J], ddof=1))
    rows.append(["dynamic", "", len(J), round(met_d[1], 6), round(sd_dyn, 6), round(met_d[2], 4),
                 round(met_d[4], 3) if met_d[4] != "" else "", round(met_d[6], 6),
                 round(met_d[5], 8), round(met_d[7], 3)])
    with open(os.path.join(RES, "all_candidates_center.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "h", "n", "cost_bp", "risk_pricepart_sd", "completion",
                    "completion_time_min", "rq_p95", "R_over_Q_mean", "wait_burden_min"])
        w.writerows(rows)
    with open(os.path.join(RES, "all_candidates_vs_dynamic.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["candidate", "n", "d_cost_dyn_minus_cand_bp", "cost_ci_low", "cost_ci_high",
                    "d_sd_dyn_minus_cand_bp", "sd_ci_low", "sd_ci_high"])
        for (m, hh), v in base_sel.items():
            w.writerow([f"{m}{hh}", len(dyn), round(v[0], 6), round(v[1], 6), round(v[2], 6),
                        round(v[3], 6), round(v[4], 6), round(v[5], 6)])
    return len(rows)


if __name__ == "__main__":
    if "--center" in sys.argv:
        print({"all_candidates": all_candidates_center()})
    else:
        print({"paired_rows": paired_metrics(), "case_rows": case_methods(),
               "ab_flip_rows": ab_flip(), "proxy": proxy_case_flip(),
               "all_candidates": all_candidates_center()})
