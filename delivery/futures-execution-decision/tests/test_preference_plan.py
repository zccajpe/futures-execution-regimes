import importlib.util, json, os, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(HERE, "tests", "fixtures")


def _load():
    spec = importlib.util.spec_from_file_location("pp", os.path.join(HERE, "scripts", "preference_plan.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


m = _load()


def load(name):
    """Every fixture is package-local and uses a relative history path, so the package
    keeps working after it is copied elsewhere."""
    p = os.path.join(FIX, name)
    with open(p, encoding="utf-8") as fh:
        req = json.load(fh)
    req["_path"] = p
    return req


def dist(main, share=0.9):
    """A legal full distribution over the whole enumeration."""
    rest = [k for k in m.PREFERENCES if k != main]
    return {main: share, **{k: (1.0 - share) / len(rest) for k in rest}}


def fake(label, probs=None, ok=True, answer=None):
    """Local fake transport. Its answers are NEVER reported as real Jev results."""
    calls = []

    def send(payload, timeout=60.0):
        calls.append(payload)
        if not ok:
            raise TimeoutError("simulated timeout")
        a = answer if answer is not None else {
            "type": "choice", "choice": label, "confidence": 0.9,
            "probabilities": probs if probs is not None else dist(label)}
        return {"model": "fake-local", "usage": {"input_tokens": 0, "output_tokens": 0},
                "answers": {"preference": a}}
    send.calls = calls
    return send


class PlanTests(unittest.TestCase):
    def test_structured_preference_skips_api(self):
        send = fake("wait_first")
        r = m.decide(load("compare_wait_first.json"), send)
        self.assertFalse(r["preference_routing"]["jev_called"])
        self.assertEqual(r["preference_routing"]["api_attempts"], 0)
        self.assertEqual(send.calls, [])
        self.assertEqual(r["plan"]["source"], "deterministic_rank")

    def test_cost_first_reuses_core_choice(self):
        r = m.decide(load("compare_cost_first.json"))
        plain = m.decide(load("compare_plain.json"))
        self.assertEqual(r["plan"]["arm"], r["choice"])
        self.assertEqual(r["plan"]["arm"], plain["plan"]["arm"])

    def test_wait_first_is_a_different_legal_arm(self):
        r = m.decide(load("compare_wait_first.json"))
        legal = {x["arm"]: x for x in m.legal_candidates(r)}
        self.assertIn(r["plan"]["arm"], legal)
        waits = sorted(x["wait_min"] for x in legal.values())
        self.assertAlmostEqual(r["plan"]["wait_min"], waits[0])
        self.assertLessEqual(r["plan"]["h"], r["effective_constraints"]["Dmax_trading_minutes"])

    def test_tie_order_is_asap_then_shorter_h_then_twap(self):
        cands = [{"arm": "twap30", "method": "twap", "h": 30, "wait_min": 5.0},
                 {"arm": "shape5", "method": "shape", "h": 5, "wait_min": 5.0},
                 {"arm": "asap0", "method": "asap", "h": 0, "wait_min": 5.0}]
        self.assertEqual(m.choose_by_wait(cands)["arm"], "asap0")
        # at the same wait: the shorter h wins, before the method-family order
        twap30 = [c for c in cands if c["method"] == "twap"]
        shape5 = [c for c in cands if c["method"] == "shape"]
        self.assertEqual(m.choose_by_wait(twap30 + shape5)["arm"], "shape5")
        # at the same h: TWAP before volume-shaped
        shape30 = dict(shape5[0], arm="shape30", h=30)
        self.assertEqual(m.choose_by_wait(twap30 + [shape30])["arm"], "twap30")

    def test_missing_deadline_zero_api_no_plan(self):
        send = fake("cost_first")
        r = m.decide(load("missing_deadline.json"), send)
        self.assertEqual(r["status"], "INPUT_REQUIRED")
        self.assertEqual(send.calls, [])
        self.assertIsNone(r["plan"]["method"])
        self.assertIsNone(r["plan"]["h"])

    def test_baseline_missing_zero_api_no_plan_no_asap(self):
        send = fake("wait_first")
        r = m.decide(load("baseline_missing.json"), send)
        self.assertEqual(r["status"], "INSUFFICIENT_BASELINE")
        self.assertEqual(send.calls, [])
        self.assertIsNone(r["plan"]["method"])
        self.assertNotEqual(r["plan"]["arm"], "asap0")
        self.assertIn("不可用", r["plan"]["reason"])
        self.assertNotIn("训练期没有可用", r["plan"]["reason"])

    def test_reference_baseline_kept_even_with_other_preference(self):
        r = m.decide(load("baseline_first_ok.json"))
        self.assertEqual(r["status"], "REFERENCE_BASELINE")
        self.assertEqual(r["choice"], "twap30")
        self.assertEqual(r["plan"]["arm"], r["choice"])
        self.assertEqual(r["plan"]["source"], "reference_baseline")

    def test_reference_baseline_not_planned_when_A_not_legal(self):
        req = load("baseline_first_ok.json")
        r = m.decide(req)
        eff = dict(r["effective_constraints"])
        eff["risk_sd_budget_bp"] = 0.0  # tighter than any candidate can meet
        plan = m.build_plan(dict(r, effective_constraints=eff), req, "unspecified",
                            "default", req["scenario"]["impact_a_bp"])
        self.assertIsNone(plan["method"])
        self.assertEqual(plan["source"], "not_available")

    def test_conditional_set_keeps_the_set(self):
        r = m.decide(load("conditional_set.json"))
        self.assertEqual(r["status"], "CONDITIONAL_SET")
        self.assertIsNone(r["plan"]["method"])
        self.assertIsNone(r["plan"]["h"])

    def test_conditional_set_stops_before_api(self):
        """A conditional set has no point choice, so a note must not trigger a request."""
        req = load("conditional_set.json")
        req["business_note"] = "成本优先，便宜点"
        send = fake("cost_first")
        r = m.decide(req, send)
        self.assertEqual(send.calls, [], "no API call when only a conditional set exists")
        self.assertEqual(r["preference_routing"]["api_attempts"], 0)
        self.assertEqual(r["preference_routing"]["origin"], "conditional_set")
        self.assertEqual(r["status"], "CONDITIONAL_SET")
        self.assertIsNone(r["plan"]["method"])
        self.assertIsNone(r["plan"]["h"])

    def test_bad_structured_preference_rejected_not_defaulted(self):
        send = fake("cost_first")
        r = m.decide(load("compare_bad_preference.json"), send)
        self.assertFalse(r["preference_routing"]["jev_called"])
        self.assertEqual(send.calls, [])
        self.assertEqual(r["preference_routing"]["origin"], "caller_invalid")
        self.assertIsNone(r["plan"]["method"])
        self.assertIsNone(r["plan"]["h"])

    def test_explicit_unspecified_produces_no_new_plan(self):
        req = load("compare_plain.json")
        req["preference"] = {"preference": "unspecified"}
        r = m.decide(req)
        self.assertEqual(r["preference_routing"]["origin"], "caller_structured")
        self.assertIsNone(r["plan"]["method"])
        self.assertEqual(r["choice"], "shape60")

    def test_free_text_without_transport_produces_no_plan(self):
        r = m.decide(load("compare_free_text.json"))
        self.assertEqual(r["status"], "CONDITIONAL_MODEL_CHOICE")
        self.assertFalse(r["preference_routing"]["jev_called"])
        self.assertIsNone(r["plan"]["method"], "a note must not be silently mapped to cost_first")
        self.assertIsNone(r["plan"]["h"])

    def test_illegal_label_no_plan_and_core_choice_kept(self):
        send = fake("twap99", probs={"twap99": 0.9})
        req = load("compare_free_text.json")
        clean = m.decide(req)
        r = m.decide(req, send)
        self.assertEqual(len(send.calls), 1)
        self.assertFalse(r["preference_routing"]["jev"]["ok"])
        self.assertEqual(r["choice"], clean["choice"])
        self.assertIsNone(r["plan"]["method"])

    def test_network_failure_no_plan_and_core_choice_kept(self):
        send = fake("cost_first", ok=False)
        req = load("compare_free_text.json")
        clean = m.decide(req)
        r = m.decide(req, send)
        self.assertEqual(len(send.calls), 1)
        self.assertEqual(r["choice"], clean["choice"])
        self.assertIsNone(r["plan"]["method"])

    def test_unspecified_label_no_plan(self):
        send = fake("unspecified")
        r = m.decide(load("compare_free_text.json"), send)
        self.assertEqual(r["preference_routing"]["origin"], "jev_unspecified")
        self.assertIsNone(r["plan"]["method"])

    def test_bad_answer_shapes_rejected(self):
        bad = [
            {"type": "choice", "choice": "cost_first"},                                   # no probabilities
            {"type": "score", "choice": "cost_first", "probabilities": dist("cost_first")},
            {"type": "choice", "choice": "cost_first", "probabilities": [0.9]},            # not an object
            {"type": "choice", "choice": "cost_first", "probabilities": {"cost_first": 0.9}},  # missing keys
            {"type": "choice", "choice": "cost_first",
             "probabilities": {"cost_first": 1.7, "wait_first": -0.35, "unspecified": -0.35}},
            {"type": "choice", "choice": "cost_first",
             "probabilities": {"cost_first": 0.1, "wait_first": 0.8, "unspecified": 0.1}},
            {"type": "choice", "choice": "cost_first",
             "probabilities": {"cost_first": 0.5, "wait_first": 0.2, "unspecified": 0.1}},  # sums 0.8
            {"type": "choice", "choice": "cost_first",
             "probabilities": {"cost_first": 0.5, "wait_first": 0.5, "else": 0.0}},      # stray key
            {"type": "choice", "choice": "cost_first",
             "probabilities": dist("cost_first"), "confidence": 3},
        ]
        for a in bad:
            label, why = m.check_label(a)
            self.assertIsNone(label, a)
            self.assertTrue(why)
        for label in ("cost_first", None):
            r = m.decide(load("compare_free_text.json"), fake(label, answer={"type": "choice"}))
            self.assertIsNone(r["plan"]["method"])

    def test_full_distribution_accepted(self):
        label, why = m.check_label({"type": "choice", "choice": "wait_first",
                                    "confidence": 0.6, "probabilities": dist("wait_first", 0.6)})
        self.assertEqual(label, "wait_first")
        self.assertIsNone(why)

    def test_answers_not_a_dict_does_not_raise(self):
        def send(payload, timeout=60.0):
            return {"model": "fake-local", "answers": ["not", "a", "dict"]}
        r = m.decide(load("compare_free_text.json"), send)
        self.assertEqual(r["preference_routing"]["origin"], "jev_rejected")
        self.assertIsNone(r["plan"]["method"])

    def test_good_label_yields_plan(self):
        send = fake("wait_first")
        r = m.decide(load("compare_free_text.json"), send)
        self.assertEqual(len(send.calls), 1)
        self.assertEqual(r["preference_routing"]["origin"], "jev")
        self.assertEqual(r["plan"]["preference_origin"], "jev")
        legal = {x["arm"]: x for x in m.legal_candidates(r)}
        self.assertIn(r["plan"]["arm"], legal)
        self.assertEqual(r["plan"]["arm"], m.choose_by_wait(list(legal.values()))["arm"])

    def test_payload_carries_no_numbers_or_candidates(self):
        send = fake("cost_first")
        m.decide(load("compare_free_text.json"), send)
        body = dict(send.calls[0])
        body.pop("model", None)
        blob = json.dumps(body, ensure_ascii=False)
        for token in ("1186", "40.0", "candidates", "feasible", "wait_min",
                      "risk_bp", "impact_load", "qty", "Dmax"):
            self.assertNotIn(token, blob)
        self.assertNotRegex(blob, r"\d+\.\d+")
        self.assertEqual(set(send.calls[0]["questions"]["preference"]["criteria"]),
                         set(m.PREFERENCES))

    def test_core_fields_not_overwritten(self):
        r = m.decide(load("compare_wait_first.json"))
        self.assertEqual(r["mode"], "compare")
        self.assertEqual(r["choice"], "shape60")
        self.assertEqual(r["method"], "shape")
        self.assertEqual(r["h"], 60)
        self.assertEqual(r["execution_action"], "NONE")
        self.assertEqual(r["production_eligibility"], "INSUFFICIENT")

    def test_constraints_not_relaxed(self):
        r = m.decide(load("compare_wait_first.json"))
        eff = r["effective_constraints"]
        req = load("compare_wait_first.json")
        self.assertEqual(eff["risk_sd_budget_bp"], req["constraints"]["risk_sd_budget_bp"])
        self.assertEqual(eff["Dmax_trading_minutes"], req["parent"]["Dmax_trading_minutes"])
        for x in m.legal_candidates(r):
            self.assertGreaterEqual(x["completion"], eff["min_completion_probability"])
            self.assertLessEqual(x["risk_bp"], eff["risk_sd_budget_bp"])

    def test_missing_effective_constraints_fail_closed(self):
        r = m.decide(load("compare_plain.json"))
        broken = dict(r, effective_constraints={})
        plan = m.build_plan(broken, load("compare_plain.json"), "unspecified", "default", 0.5)
        self.assertEqual(m.legal_candidates(broken), [])
        self.assertIsNone(plan["method"])

    def test_unsupported_mode_no_api(self):
        req = load("compare_free_text.json")
        req["mode"] = "volume_forecast"
        send = fake("wait_first")
        r = m.decide(req, send)
        self.assertEqual(send.calls, [])
        self.assertEqual(r["preference_routing"]["origin"], "not_supported_mode")
        self.assertIsNone(r["plan"]["method"])

    def test_routing_origin_tracks_final_source(self):
        r = m.decide(load("compare_free_text.json"), fake("wait_first"))
        self.assertEqual(r["preference_routing"]["origin"], r["plan"]["preference_origin"])
        r2 = m.decide(load("compare_free_text.json"), fake("twap99", probs={"twap99": 1.0}))
        self.assertEqual(r2["preference_routing"]["origin"], r2["plan"]["preference_origin"])

    def test_relocated_package_runs_from_relative_path(self):
        """Copy the package to a temporary directory and run the CLI there, so the
        delivered tree is proven self-contained."""
        import shutil
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "pkg")
            shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("__pycache__"))
            out = os.path.join(tmp, "out.json")
            proc = subprocess.run(
                [sys.executable, os.path.join("scripts", "preference_plan.py"),
                 "--request", os.path.join("tests", "fixtures", "compare_cost_first.json"),
                 "--out", out],
                cwd=dst, capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, encoding="utf-8") as fh:
                res = json.load(fh)
            self.assertEqual(res["status"], "CONDITIONAL_MODEL_CHOICE")
            self.assertEqual(res["plan"]["source"], "deterministic_rank")
            self.assertFalse(res["preference_routing"]["jev_called"])
            self.assertEqual(res["execution_action"], "NONE")


class ConditionalSetWaitTests(unittest.TestCase):
    """A conditional set still fixes the legal set, completion, risk and wait_min.
    Only the cost point estimate needs a. So an explicit compare + wait_first may get a
    wait plan with an unknown cost; nothing wider than that is allowed."""

    def _cond(self, a, pref=None):
        req = load("conditional_set.json")
        req["scenario"]["impact_a_bp"] = a
        if pref is not None:
            req["preference"] = {"preference": pref}
        return req

    def test_unfixed_a_wait_first_gets_legal_plan_with_null_cost(self):
        for a in (None, [0, 2]):
            r = m.decide(self._cond(a, "wait_first"))
            self.assertEqual(r["status"], "CONDITIONAL_SET")
            self.assertIsNone(r.get("choice"), "the top-level core choice stays null/absent")
            p = r["plan"]
            self.assertEqual(p["source"], "deterministic_rank")
            self.assertEqual(p["objective"], "wait_first")
            self.assertIsNone(p["predicted_cost_bp"], "cost stays unknown, not filled with a guess")
            legal = {x["arm"]: x for x in m.legal_candidates(r)}
            self.assertIn(p["arm"], legal)
            self.assertAlmostEqual(p["wait_min"], min(x["wait_min"] for x in legal.values()))
            self.assertEqual(p["arm"], m.choose_by_wait(list(legal.values()))["arm"])
            self.assertLessEqual(p["h"], r["effective_constraints"]["Dmax_trading_minutes"])
            self.assertEqual(r["execution_action"], "NONE")
            self.assertEqual(r["production_eligibility"], "INSUFFICIENT")

    def test_unfixed_a_wait_first_keeps_original_constraints(self):
        req = self._cond(None, "wait_first")
        r = m.decide(req)
        eff = r["effective_constraints"]
        self.assertEqual(eff["Dmax_trading_minutes"], req["parent"]["Dmax_trading_minutes"])
        self.assertEqual(eff["risk_sd_budget_bp"], req["constraints"]["risk_sd_budget_bp"])
        self.assertEqual(eff["min_completion_probability"],
                         req["constraints"]["min_completion_probability"])
        self.assertIsNone(eff["impact_a_bp"], "a is never filled in by the plan step")
        for x in m.legal_candidates(r):
            self.assertGreaterEqual(x["completion"], eff["min_completion_probability"])
            self.assertLessEqual(x["risk_bp"], eff["risk_sd_budget_bp"])

    def test_unfixed_a_cost_first_and_unspecified_still_get_no_plan(self):
        for a in (None, [0, 2]):
            for pref in ("cost_first", "unspecified"):
                r = m.decide(self._cond(a, pref))
                self.assertEqual(r["status"], "CONDITIONAL_SET")
                self.assertIsNone(r["plan"]["method"], f"{pref} must not get a point plan")
                self.assertIsNone(r["plan"]["h"])
                self.assertEqual(r["plan"]["source"], "not_available")

    def test_unfixed_a_free_text_gets_no_plan_and_no_request(self):
        req = load("conditional_set.json")
        req["business_note"] = "等待负担小一点"
        send = fake("wait_first")
        r = m.decide(req, send)
        self.assertEqual(send.calls, [], "a conditional set still stops before any request")
        self.assertEqual(r["preference_routing"]["api_attempts"], 0)
        self.assertIsNone(r["plan"]["method"])

    def test_unfixed_a_structured_wait_first_calls_no_api(self):
        send = fake("wait_first")
        r = m.decide(self._cond(None, "wait_first"), send)
        self.assertEqual(send.calls, [])
        self.assertFalse(r["preference_routing"]["jev_called"])
        self.assertEqual(r["preference_routing"]["api_attempts"], 0)

    def test_unfixed_a_baseline_first_stays_bounded(self):
        """The mode boundary of the new exception: same legal set, explicit wait_first,
        but the caller declared baseline_first. Still no plan, and the mode is not swapped."""
        req = load("baseline_first_ok.json")
        req["scenario"]["impact_a_bp"] = None
        req["preference"] = {"preference": "wait_first"}
        send = fake("wait_first")
        r = m.decide(req, send)
        self.assertEqual(send.calls, [])
        self.assertNotEqual(r["mode"], "compare", "the declared mode is never swapped")
        self.assertIn(r["status"], ("INSUFFICIENT_BASELINE", "CONDITIONAL_SET", "REFERENCE_BASELINE"))
        self.assertNotEqual(r["status"], "CONDITIONAL_SET")
        self.assertIsNone(r["plan"]["method"], "a wait plan must not be granted outside compare")
        self.assertIsNone(r["plan"]["h"])
        self.assertEqual(r["plan"]["source"], "not_available")

    def test_unfixed_a_wait_first_only_in_compare_mode(self):
        """Same fixture, same unfixed a and same wait_first: compare yields a wait plan
        while baseline_first does not. This is the mode boundary, nothing else."""
        cmp_req = load("conditional_set.json")
        cmp_req["preference"] = {"preference": "wait_first"}
        cmp_res = m.decide(cmp_req)
        self.assertEqual(cmp_res["mode"], "compare")
        self.assertEqual(cmp_res["plan"]["source"], "deterministic_rank")
        self.assertEqual(cmp_res["plan"]["objective"], "wait_first")
        self.assertIsNone(cmp_res["plan"]["predicted_cost_bp"])

    def test_fixed_a_behaviour_is_unchanged(self):
        """a fixed at a point keeps the original path: cost_first reuses the core choice
        with a real cost estimate, and wait_first still ranks by wait."""
        cf = m.decide(load("compare_cost_first.json"))
        self.assertEqual(cf["status"], "CONDITIONAL_MODEL_CHOICE")
        self.assertEqual(cf["plan"]["arm"], cf["choice"])
        self.assertIsNotNone(cf["plan"]["predicted_cost_bp"])
        wf = m.decide(load("compare_wait_first.json"))
        self.assertIsNotNone(wf["plan"]["predicted_cost_bp"],
                             "a fixed at a point still yields a cost estimate")


if __name__ == "__main__":
    unittest.main()
