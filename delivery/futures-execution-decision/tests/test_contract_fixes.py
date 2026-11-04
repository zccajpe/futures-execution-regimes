"""Tests for the 2026-10-30 contract fixes: choice demotion, research-only exits,
and the completion semantics (path share, not zero residual mean)."""
import importlib.util, json, os, unittest
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("deci", os.path.join(HERE, "scripts", "decision.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
EX = os.path.join(HERE, "examples"); NPZ = os.path.abspath(os.path.join(EX, "RB_am_20d_20260803.npz"))


def req(**kw):
    r = {"parent": {"symbol": "RB", "side": "buy", "qty_lots": 1186, "release_trading_date": "20260803",
                    "release_label": "09:15", "Dmax_trading_minutes": 60},
         "history_npz": NPZ,
         "constraints": {"min_completion_probability": 0.95, "max_unfilled_lots": 0, "risk_sd_budget_bp": 10.8444},
         "scenario": {"cap_frac": 0.1, "fee_spread_bp": 1.3, "impact_a_bp": 0.0, "phi": 1.0,
                      "terminal_price_change_bp": 0.0},
         "mode": "baseline_first",
         "baseline_context": {"product": "RB", "window": "am", "size_idx": 1, "risk_k": 2.0},
         "_path": os.path.abspath(os.path.join(EX, "request.json"))}
    for path, val in kw.items():
        cur = r; parts = path.split(".")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = val
    return r


def run(r):
    return mod.apply_baseline(mod.run(r), r)


class TestChoiceSemantics(unittest.TestCase):
    def test_review_required_has_no_final_choice(self):
        r = req(**{"constraints.risk_sd_budget_bp": 10.0})
        d = run(r)
        self.assertEqual(d["status"], "REVIEW_REQUIRED")
        self.assertIsNone(d["choice"])
        self.assertIsNotNone(d["conditional_choice"])
        self.assertIn(d["conditional_choice"], [c["arm"] for c in d["candidates"] if c["feasible"]])
        self.assertIn("next_action", d)

    def test_insufficient_baseline_has_no_final_choice(self):
        r = req(mode="baseline_first")
        r.pop("baseline_context")
        d = run(r)
        self.assertEqual(d["status"], "INSUFFICIENT_BASELINE")
        self.assertIsNone(d["choice"])
        self.assertIsNotNone(d["conditional_choice"])
        self.assertEqual(d["conditional_choice"], "asap0")

    def test_reference_baseline_keeps_choice(self):
        d = run(req())
        self.assertEqual(d["status"], "REFERENCE_BASELINE")
        self.assertEqual(d["choice"], d["baseline"]["arm"])
        self.assertEqual(d["comparison_to_baseline"]["conditional_choice"], "asap0")

    def test_compare_mode_keeps_conditional_choice(self):
        r = req(mode="compare"); r.pop("baseline_context")
        d = mod.apply_baseline(mod.run(r), r)
        self.assertIn(d["status"], ("CONDITIONAL_MODEL_CHOICE", "CONDITIONAL_SET"))
        self.assertIsNotNone(d.get("choice"))


class TestResearchOnlyExits(unittest.TestCase):
    def _assert_research_only(self, d):
        self.assertEqual(d["execution_action"], "NONE")
        self.assertEqual(d["production_eligibility"], "INSUFFICIENT")
        self.assertIn("not a production release", d["scope"])

    def test_missing_deadline(self):
        r = req(); r["parent"].pop("Dmax_trading_minutes")
        d = run(r); self.assertEqual(d["status"], "INPUT_REQUIRED"); self.assertIsNone(d.get("choice"))
        self._assert_research_only(d)

    def test_missing_risk_budget(self):
        r = req(); r["constraints"].pop("risk_sd_budget_bp")
        d = run(r); self.assertEqual(d["status"], "INPUT_REQUIRED"); self._assert_research_only(d)

    def test_bad_history(self):
        d = run(req(history_npz="/nonexistent.npz"))
        self.assertEqual(d["status"], "INSUFFICIENT_DATA"); self._assert_research_only(d)

    def test_model_infeasible(self):
        d = run(req(**{"parent.qty_lots": 200000, "scenario.impact_a_bp": None}))
        self.assertEqual(d["status"], "MODEL_INFEASIBLE")
        self.assertIsNone(d.get("choice")); self.assertIn("next_action", d)
        self._assert_research_only(d)


class TestCompletionSemantics(unittest.TestCase):
    def test_completion_is_path_share_not_zero_residual(self):
        r = req(**{"parent.qty_lots": 8000,
                   "constraints.min_completion_probability": 0.75,
                   "constraints.risk_sd_budget_bp": 1000.0,
                   "scenario.impact_a_bp": 0.5, "mode": "compare"})
        d = mod.run(r)
        feasible = [c for c in d["candidates"] if c["feasible"]]
        self.assertTrue(feasible)
        partial = [c for c in feasible if 0 < c["completion"] < 1 and c["resid_mean"] > 0]
        self.assertTrue(partial, "expected a feasible arm whose residual mean is above zero")
        for c in partial:
            self.assertGreaterEqual(c["completion"], 0.75)

    def test_zero_residual_allowance_is_per_path(self):
        r = req(**{"parent.qty_lots": 8000, "constraints.min_completion_probability": 0.5,
                   "constraints.max_unfilled_lots": 0, "constraints.risk_sd_budget_bp": 1000.0,
                   "scenario.impact_a_bp": 0.5, "mode": "compare"})
        d = mod.run(r)
        arms = {c["arm"]: c for c in d["candidates"]}
        self.assertEqual(arms["asap0"]["completion"], 0.8)  # 16 of 20 paths end flat
        self.assertGreater(arms["asap0"]["resid_mean"], 0)


class TestEffectiveConstraints(unittest.TestCase):
    def test_effective_constraints_and_risk_model_present(self):
        d = run(req())
        ec = d["effective_constraints"]
        self.assertEqual(ec["Dmax_trading_minutes"], 60)
        self.assertEqual(ec["min_completion_probability"], 0.95)
        self.assertEqual(ec["max_unfilled_lots"], 0)
        self.assertAlmostEqual(ec["risk_sd_budget_bp"], 10.8444)
        self.assertIn("completion_rule", ec)
        self.assertEqual(d["risk_model"]["name"], "empirical_sd_20_paths")
        self.assertEqual(d["risk_model"]["n_paths"], 20)

    def test_effective_constraints_show_defaults(self):
        r = req(); r["constraints"].pop("min_completion_probability"); r["constraints"].pop("max_unfilled_lots")
        d = run(r)
        self.assertEqual(d["effective_constraints"]["min_completion_probability"], 0.95)
        self.assertEqual(d["effective_constraints"]["max_unfilled_lots"], 0)
        self.assertEqual(d["constraints"], r["constraints"])  # raw echo untouched

    def test_demotion_moves_method_and_h(self):
        r = req(mode="baseline_first"); r.pop("baseline_context")
        d = run(r)
        self.assertIsNone(d["choice"]); self.assertNotIn("method", d); self.assertNotIn("h", d)
        self.assertEqual(d["conditional_choice"], d["conditional_method"] + str(d["conditional_h"]))

    def test_next_action_does_not_ask_to_relax_the_order(self):
        d = run(req(**{"parent.qty_lots": 200000, "scenario.impact_a_bp": None}))
        self.assertNotIn("relax", d["next_action"].lower())
        self.assertIn("keep the parent", d["next_action"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
