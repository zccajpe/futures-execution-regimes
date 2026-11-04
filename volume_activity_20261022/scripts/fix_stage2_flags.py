#!/usr/bin/env python3
"""Post-process the saved Stage 2 CSV/JSON: correct the duration flags and add
the fixed full-E completion/residual and rejected-group statistics.

No selection, no simulation, no refit: it only re-reads the saved arms and
realised outcomes and rewrites the affected columns/fields.
"""
import csv, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)


def method(a):
    return a.rstrip("0123456789") if a else ""


def hh(a):
    return int(a[len(method(a)):]) if a else None


def fix_csv(name):
    path = os.path.join(STAGE, name)
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    cols = list(rows[0].keys())
    n = 0
    for r in rows:
        a, v = r.get("act_arm", ""), r.get("vol20_arm", "")
        r["changed_method"] = int(method(a) != method(v)) if a and v else 0
        r["changed_h"] = int(hh(a) != hh(v)) if a and v else 0
        n += 1
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return n


def main():
    n1 = fix_csv("execution_decisions.csv")
    n2 = fix_csv("outcomes.csv")
    with open(os.path.join(STAGE, "outcomes.csv"), newline="") as fh:
        rows = list(csv.DictReader(fh))
    E = [r for r in rows if r["in_E"] == "1"]
    full = {}
    for sp in ("val", "test"):
        ev = [r for r in E if r["split"] == sp]
        miss = [r for r in ev if not r["act_arm"]]
        sel = [r for r in ev if r["act_arm"] and r["vol20_arm"] and r["act_arm"] != r["vol20_arm"]]
        act_comp = [0.0 if not r["act_arm"] else float(r["ACT_comp"]) for r in ev]
        act_rq = [1.0 if not r["act_arm"] else float(r["ACT_rq"]) for r in ev]
        asap_comp = [float(r["ASAP_comp"]) for r in ev]
        asap_rq = [float(r["ASAP_rq"]) for r in ev]
        full[sp] = {
            "E_units": len(ev), "missing": len(miss), "coverage": (len(ev) - len(miss)) / len(ev),
            "completion_ACT_fullE": sum(act_comp) / len(ev),
            "completion_ASAP_fullE": sum(asap_comp) / len(ev),
            "completion_diff_pp_fullE": 100 * (sum(act_comp) - sum(asap_comp)) / len(ev),
            "residual_ACT_fullE": sum(act_rq) / len(ev),
            "residual_ASAP_fullE": sum(asap_rq) / len(ev),
            "rejected_group_n": len(miss),
            "rejected_group_ASAP_completion": (sum(float(r["ASAP_comp"]) for r in miss) / len(miss)) if miss else None,
            "rejected_group_ASAP_residual": (sum(float(r["ASAP_rq"]) for r in miss) / len(miss)) if miss else None,
            "selection_changes": len(sel),
            "method_changes": sum(1 for r in sel if int(r["changed_method"]) == 1),
            "duration_changes": sum(1 for r in sel if int(r["changed_h"]) == 1),
            "actual_path_changes": sum(int(r["actual_q_path_changed"]) for r in sel),
        }
    path = os.path.join(STAGE, "execution_evaluation.json")
    ev = json.load(open(path))
    ev["full_E"] = full
    for sp in ("val", "test"):
        inc = ev["evaluation"][sp]["ACT_vs_VOL20"]
        inc["selection_changed"] = full[sp]["selection_changes"]
        inc["method_changed"] = full[sp]["method_changes"]
        inc["h_changed"] = full[sp]["duration_changes"]
        inc["actual_q_path_changed"] = full[sp]["actual_path_changes"]
    ev["notes"].append("duration change = parsed h differs; selection change may change the method without changing h "
                       "(e.g. asap0 vs twap5 changes both, but some switches keep the same h)")
    ev["notes"].append("C3 on the full fixed E counts refusals as unexecuted; the conditional (planned-only) C3 is a diagnostic, not the main criterion")
    json.dump(ev, open(path, "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"rows_fixed": [n1, n2], "full_E": full}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
