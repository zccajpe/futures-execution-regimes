#!/usr/bin/env python3
"""Run the skill on EXACTLY the pre-declared case units and attach the calls to evaluation.json.

The case units come from evaluate_rule.py (fixed selection rules). For each of them
this script builds the same history NPZ the skill consumes, calls the skill, checks
that Q / D / window / size / arm agree with the research decision, and writes the
request/response paths back into evaluation.json so that the report cannot quote a
different unit from the one actually executed.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import make_exante_request as M  # noqa: E402


def main() -> None:
    ev = json.load(open(os.path.join(STAGE, "evaluation.json")))
    cases = ev.get("cases", [])
    calls = []
    for c in cases:
        if c.get("kind") == "outside_E":
            calls.append({"kind": c["kind"], "skill_call": None,
                          "note": "outside the pre-declared range: the skill refuses by design (ASAP predicted completion < 0.95)"})
            continue
        tag = f"case_{c['product']}_{c['window']}_{c['date']}"
        got = M.build(product=c["product"], window=c["window"], date=int(c["date"]),
                      size_idx=int(c["size"]), D=int(c["D"]), out_tag=tag)
        resp = json.load(open(got["response"]))
        cand = (resp.get("research_candidate") or {}).get("arm")
        rec = {"kind": c["kind"], "product": c["product"], "window": c["window"], "date": c["date"],
               "size": c["size"], "D": c["D"], "side": c["side"],
               "Q_research": c.get("Q"), "Q_skill": got["Q"],
               "arm_research": c.get("rule_arm"), "arm_skill_candidate": cand,
               "skill_status": resp.get("status"), "skill_choice": resp.get("choice"),
               "reference_arm": (resp.get("reference") or {}).get("arm"),
               "reference_feasible_today": (resp.get("reference") or {}).get("feasible_today"),
               "request": os.path.relpath(got["request"], STAGE),
               "response": os.path.relpath(got["response"], STAGE),
               "match": bool(c.get("Q") == got["Q"] and cand == c.get("rule_arm"))}
        calls.append(rec)
        c["skill_call"] = rec
    ev["case_calls"] = calls
    json.dump(ev, open(os.path.join(STAGE, "evaluation.json"), "w"), ensure_ascii=False, indent=1)
    json.dump({"calls": calls, "all_match": all(c.get("match", True) for c in calls if c.get("skill_call"))},
              open(os.path.join(STAGE, "case_calls.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"all_match": all(c.get("match", True) for c in calls if c.get("skill_call")),
                      "calls": [{k: v for k, v in c.items() if k in ("kind", "product", "window", "date", "size", "D",
                                                                     "Q_research", "Q_skill", "arm_research",
                                                                     "arm_skill_candidate", "skill_status", "match")}
                                for c in calls]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
