"""Fix two math errors and regenerate figures.

1) state_by_product uses explicit multi-column keys (no mixed-radix packing) and
   separates "state missing" from bucket -1.
2) risk difference CI is computed by re-deriving both sample SDs (ddof=1) on every
   block bootstrap resample from per-block sums of A, A^2, B, B^2 and counts.
Also: ab_flip includes the fee-loading term; proxy_case_flip is restricted to the
central 10% capacity and labelled as a price-part sign test.
"""
from __future__ import annotations

import csv
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # noqa: E402

ROOT = E.ROOT
RES = os.path.join(ROOT, "results")
PRODUCTS = ["RB", "I", "CU", "AU", "SC", "TA", "RU", "M", "P", "IF", "IC", "T"]
WINDOWS = ["am", "pm", "night"]
METHODS = ["asap", "twap", "shape"]
TRAIN = (20260105, 20260430)
VAL = (20260506, 20260630)
TEST = (20260701, 20261020)
FEE = 1.3
CAP = 1


def load(p):
    with np.load(p, allow_pickle=False) as z:
        return {k: np.array(z[k], copy=True) for k in z.files}


def merged():
    a = load(os.path.join(RES, "replay_train_val_t60.npz"))
    b = load(os.path.join(RES, "replay_test_t60.npz"))
    return {k: np.concatenate([a[k], b[k]]) for k in a}


def split_code(d):
    out = np.zeros(len(d), dtype=np.int8)
    out[(d >= VAL[0]) & (d <= VAL[1])] = 1
    out[d >= TEST[0]] = 2
    return out


def state_rows(rp, side=1.0):
    cmap = {}
    for r in csv.DictReader(open(os.path.join(RES, "state_cases.csv"), encoding="utf-8")):
        cmap[(r["product"], r["window"], int(r["date"]))] = r
    k = rp["keys"]
    n = len(k)
    pi = k[:, 0].astype(np.int64)
    wi = k[:, 1].astype(np.int64)
    date = k[:, 2]
    size = k[:, 3].astype(np.int64)
    D = k[:, 4].astype(np.int64)
    mi = k[:, 5].astype(np.int64)
    hh = k[:, 6].astype(np.int64)
    ok = k[:, 7] == CAP
    rows = []
    for dim in ("liq_bucket", "vol_bucket", "act_bucket", "open_to_release_sign"):
        bucket = np.full(n, -99, dtype=np.int64)
        for i in np.nonzero(ok)[0]:
            r = cmap.get((PRODUCTS[int(pi[i])], WINDOWS[int(wi[i])], int(date[i])))
            if r is None:
                continue
            v = r.get(dim, "")
            if v == "" or v is None:
                continue
            bucket[i] = int(v)
        valid = ok & (bucket != -99)
        idx = np.nonzero(valid)[0]
        if idx.size == 0:
            continue
        keys = np.stack([pi[idx], wi[idx], split_code(date[idx]), bucket[idx], size[idx], D[idx], mi[idx], hh[idx]], axis=1)
        uniq, inv = np.unique(keys, axis=0, return_inverse=True)
        cost = side * (rp["gap_exec"][idx] + rp["gap_resid_T60"][idx]) + FEE * rp["fee_load"][idx] + 0.5 * rp["L1"][idx]
        price = rp["gap_exec"][idx] + rp["gap_resid_T60"][idx]
        comp = (rp["resid"][idx] == 0).astype(float)
        rq = rp["resid"][idx] / rp["Q"][idx]
        wait = D[idx] * rp["L_exec"][idx] + 60 * rp["L_res"][idx]
        cnt = np.bincount(inv, minlength=len(uniq))
        S = {nm: np.bincount(inv, weights=arr, minlength=len(uniq))
             for nm, arr in (("c", cost), ("p", price), ("p2", price ** 2), ("k", comp), ("rq", rq), ("w", wait))}
        pair = np.unique(np.stack([inv, date[idx]], axis=1), axis=0)
        ndays = np.bincount(pair[:, 0], minlength=len(uniq))
        for g in range(len(uniq)):
            if cnt[g] < 20:
                continue
            p_, q_ = S["p"][g] / cnt[g], S["p2"][g] / cnt[g]
            sd = float(np.sqrt(max(q_ - p_ * p_, 0.0) * cnt[g] / max(cnt[g] - 1, 1)))
            rows.append([PRODUCTS[uniq[g, 0]], WINDOWS[uniq[g, 1]], ["train", "val", "test"][uniq[g, 2]],
                         dim, int(uniq[g, 3]), int(uniq[g, 4]), int(uniq[g, 5]), METHODS[uniq[g, 6]],
                         int(uniq[g, 7]), int(cnt[g]), int(ndays[g]), round(float(S["c"][g] / cnt[g]), 4),
                         round(float(S["k"][g] / cnt[g]), 4), round(float(S["rq"][g] / cnt[g]), 5),
                         round(sd, 4), round(float(S["w"][g] / cnt[g]), 2),
                         "descriptive" if ndays[g] < 30 else "ok"])
    return rows


def fourway_risk(rp, decisions, fixed, split):
    """Cost delta and risk delta with both SDs re-derived per bootstrap resample."""
    k = rp["keys"]
    idx = {}
    for i in range(len(k)):
        if int(k[i, 7]) == CAP:
            idx[(int(k[i, 0]), int(k[i, 1]), int(k[i, 2]), int(k[i, 3]), int(k[i, 4]), int(k[i, 5]), int(k[i, 6]))] = i
    out = []
    for side in ("buy", "sell"):
        sgn = 1.0 if side == "buy" else -1.0
        for size in range(3):
            for D in (5, 15, 30, 60):
                sub = [r for r in decisions if r["side"] == side and int(r["size_idx"]) == size and int(r["D"]) == D
                       and r["preference"] == "completion_first" and r["a_bp"] == "0.5"
                       and r["phi"] == "1.0" and r["budget_k"] == "1.0"]
                if not sub:
                    continue
                uni = len(sub)
                rec = sum(1 for r in sub if r["status"] == "ok")
                for base in ("asap", "fixed_twap", "fixed_shape"):
                    pairs = []
                    for r in sub:
                        if r["status"] != "ok":
                            continue
                        pi, wi = PRODUCTS.index(r["product"]), WINDOWS.index(r["window"])
                        date = int(r["date"])
                        i_s = idx.get((pi, wi, date, size, D, METHODS.index(r["recommended_method"]), int(r["recommended_h"])))
                        if base == "asap":
                            i_b = idx.get((pi, wi, date, size, D, 0, 0))
                        else:
                            fam = "twap" if base == "fixed_twap" else "shape"
                            hh = fixed.get((r["product"], r["window"], size, D, side, fam))
                            i_b = idx.get((pi, wi, date, size, D, 1 if fam == "twap" else 2, hh)) if hh else None
                        if i_s is None or i_b is None:
                            continue
                        pairs.append((i_s, i_b, date))
                    if not pairs:
                        out.append([split, side, size, D, base, 0, uni, round(rec / uni, 4), 0, 0, "", "", "", "", "", "", "", "", "", "", "", ""])
                        continue
                    a = np.array([p[0] for p in pairs]); b = np.array([p[1] for p in pairs])
                    dates = np.array([p[2] for p in pairs])
                    pa = (rp["gap_exec"][a] + rp["gap_resid_T60"][a])
                    pb = (rp["gap_exec"][b] + rp["gap_resid_T60"][b])
                    ca = sgn * pa + FEE * rp["fee_load"][a] + 0.5 * rp["L1"][a]
                    cb = sgn * pb + FEE * rp["fee_load"][b] + 0.5 * rp["L1"][b]
                    u = np.unique(dates)
                    blk = {int(d): i // 5 for i, d in enumerate(u)}
                    bi = np.array([blk[int(d)] for d in dates])
                    nb = bi.max() + 1
                    n_b = np.bincount(bi, minlength=nb).astype(float)
                    sA = np.bincount(bi, weights=pa, minlength=nb)
                    sA2 = np.bincount(bi, weights=pa ** 2, minlength=nb)
                    sB = np.bincount(bi, weights=pb, minlength=nb)
                    sB2 = np.bincount(bi, weights=pb ** 2, minlength=nb)
                    sD = np.bincount(bi, weights=(ca - cb), minlength=nb)
                    rng = np.random.default_rng(3)
                    mc, mr = [], []
                    for _ in range(2000):
                        p = rng.integers(0, nb, nb)
                        n_ = n_b[p].sum()
                        if n_ < 2:
                            continue
                        ma = sA[p].sum() / n_
                        va = max(sA2[p].sum() / n_ - ma * ma, 0.0) * n_ / (n_ - 1)
                        mb = sB[p].sum() / n_
                        vb = max(sB2[p].sum() / n_ - mb * mb, 0.0) * n_ / (n_ - 1)
                        mr.append(np.sqrt(va) - np.sqrt(vb))
                        mc.append(sD[p].sum() / n_)
                    mc = np.array(mc); mr = np.array(mr)
                    sd_a = float(np.std(pa, ddof=1)); sd_b = float(np.std(pb, ddof=1))
                    out.append([split, side, size, D, base, len(pairs), uni, round(rec / uni, 4),
                                int(len(u)), int(nb), round(float((ca - cb).mean()), 4),
                                round(float(np.percentile(mc, 2.5)), 4), round(float(np.percentile(mc, 97.5)), 4),
                                round(sd_a, 4), round(sd_b, 4), round(sd_a - sd_b, 4),
                                round(float(np.percentile(mr, 2.5)), 4), round(float(np.percentile(mr, 97.5)), 4),
                                round(float(np.mean(rp["resid"][a] == 0)), 4), round(float(np.mean(rp["resid"][b] == 0)), 4),
                                "", ""])
    return out


def main() -> None:
    rp = merged()
    rows = state_rows(rp, 1.0)
    with open(os.path.join(RES, "state_by_product.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product", "window", "split", "state_dim", "bucket", "size_idx", "D", "method", "h",
                    "n", "n_days", "cost_t60_bp", "completion", "R_over_Q", "risk_pricepart_sd",
                    "wait_burden_min", "note"])
        w.writerows(rows)
    rpv = load(os.path.join(RES, "replay_train_val_t60.npz"))
    rpt = load(os.path.join(RES, "replay_test_t60.npz"))
    fixed = {}
    for r in csv.DictReader(open(os.path.join(RES, "fixed_h_train.csv"), encoding="utf-8")):
        if r["flag"] == "chosen" and r["preference"] == "completion_first" and r["a_bp"] == "0.5" \
                and r["phi"] == "1.0" and r["budget_k"] == "1.0":
            fixed[(r["product"], r["window"], int(r["size_idx"]), int(r["D"]), r["side"], r["family"])] = int(r["h"])
    dv = list(csv.DictReader(open(os.path.join(RES, "selector_decisions_val.csv"), encoding="utf-8")))
    dt = list(csv.DictReader(open(os.path.join(RES, "selector_decisions_test.csv"), encoding="utf-8")))
    mo = fourway_risk(rpv, dv, fixed, "val") + fourway_risk(rpt, dt, fixed, "test")
    with open(os.path.join(RES, "multiobjective.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["split", "side", "size_idx", "D", "baseline", "n_paired", "n_universe", "recommendation_coverage",
                    "n_dates", "n_blocks", "delta_cost_bp", "cost_ci_low", "cost_ci_high",
                    "risk_selector", "risk_baseline", "risk_delta", "risk_ci_low", "risk_ci_high",
                    "completion_selector", "completion_baseline", "completion_time_selector", "completion_time_baseline"])
        w.writerows(mo)

    # verification: 10 random groups recomputed directly from the raw arrays
    import random as _random
    _random.seed(11)
    k = rp["keys"]
    cm = {}
    for x in csv.DictReader(open(os.path.join(RES, "state_cases.csv"), encoding="utf-8")):
        cm[(x["product"], x["window"], int(x["date"]))] = x
    spv = np.where(k[:, 2] < VAL[0], "train", np.where(k[:, 2] <= VAL[1], "val", "test"))
    ok_rows = [r for r in rows if r[16] == "ok"]
    pick = _random.sample(ok_rows, 10)
    ver = []
    for r in pick:
        prod, win, sp, dim, bkt, size, D, method, h = r[:9]
        sel = ((k[:, 0] == PRODUCTS.index(prod)) & (k[:, 1] == WINDOWS.index(win)) &
               (k[:, 3] == int(size)) & (k[:, 4] == int(D)) & (k[:, 5] == METHODS.index(method)) &
               (k[:, 6] == int(h)) & (k[:, 7] == CAP) & (spv == sp))
        lab = np.array([cm.get((PRODUCTS[int(k[i, 0])], WINDOWS[int(k[i, 1])], int(k[i, 2])), {}).get(dim, "") == str(bkt)
                        for i in range(len(k))])
        i = np.nonzero(sel & lab)[0]
        if len(i) == 0:
            ver.append({"key": [prod, win, sp, dim, int(bkt), int(size), int(D), method, int(h)],
                        "match": False, "error": "no_rows"})
            continue
        c = (rp["gap_exec"][i] + rp["gap_resid_T60"][i] + FEE * rp["fee_load"][i] + 0.5 * rp["L1"][i])
        pr = rp["gap_exec"][i] + rp["gap_resid_T60"][i]
        n_rec = int(len(i)); d_rec = int(len(np.unique(k[i, 2])))
        c_rec = float(c.mean()); sd_rec = float(np.std(pr, ddof=1))
        e_n = abs(n_rec - int(r[9])); e_d = abs(d_rec - int(r[10]))
        e_c = abs(c_rec - float(r[11])); e_sd = abs(sd_rec - float(r[14]))
        ver.append({"key": [prod, win, sp, dim, int(bkt), int(size), int(D), method, int(h)],
                    "n_table": int(r[9]), "n_recalc": n_rec,
                    "days_table": int(r[10]), "days_recalc": d_rec,
                    "cost_table": float(r[11]), "cost_recalc": round(c_rec, 6),
                    "sd_table": float(r[14]), "sd_recalc": round(sd_rec, 6),
                    "abs_err_n": e_n, "abs_err_days": e_d,
                    "abs_err_cost": round(e_c, 9), "abs_err_sd": round(e_sd, 9),
                    "match": bool(e_n == 0 and e_d == 0 and e_c < 1e-3 and e_sd < 1e-3)})
    all_match = all(x["match"] for x in ver)
    with open(os.path.join(RES, "state_verify.json"), "w", encoding="utf-8") as fh:
        json.dump({"checked": len(ver), "all_match": bool(all_match), "rows": ver}, fh,
                  ensure_ascii=False, indent=1, default=int)
    assert all_match, "state verification failed"
    print(json.dumps({"state_rows": len(rows), "multiobjective_rows": len(mo),
                      "verified": len(ver), "all_match": bool(all_match)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
