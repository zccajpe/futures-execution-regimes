"""Tests for the ex-ante scheduling mode (mode=exante).

These tests check behaviour that matters, not wording:
  * the new mode really runs the rule and never claims a release permission;
  * the research candidate is reported separately from the top-level choice;
  * bars after the release instant cannot change anything (leakage);
  * the training-cutoff, missing-release-day and size-bucket guards reject;
  * the tie order is ASAP -> shorter h -> TWAP before shape.
"""
import csv, importlib.util, json, os, subprocess, sys, tempfile, unittest
import shutil
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECIDE = os.path.join(HERE, "scripts", "decision.py")
DEC = importlib.util.spec_from_file_location("dec_mod", DECIDE)
MOD = importlib.util.module_from_spec(DEC)
DEC.loader.exec_module(MOD)


def budget_for(product="RB", window="am", size=1, D=60, side="buy", a=0.5, k=1.0):
    with open(os.path.join(HERE, "data", "frozen_A_params.csv")) as fh:
        for r in csv.DictReader(fh):
            if (r["product"] == product and r["window"] == window and int(r["size_idx"]) == size
                    and int(r["D"]) == D and r["side"] == side and float(r["a_bp"]) == a
                    and float(r["risk_k"]) == k):
                return float(r["budget_bp"])
    raise AssertionError("no frozen row")
spec = importlib.util.spec_from_file_location("xc", os.path.join(HERE, "scripts", "exante_core.py"))
XC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(XC)


def labels_0915():
    mins = list(range(9 * 60 + 1, 11 * 60 + 31))
    return [f"{m // 60:02d}:{m % 60:02d}" for m in mins]


def make_npz(path, *, days=20, include_release=True, future_value=np.nan, seed=7, profile="flat",
             prefix_nan=False, prefix_negvol=False):
    rng = np.random.default_rng(seed)
    labels = labels_0915()
    n = days + (1 if include_release else 0)
    base = 3000.0
    close = np.zeros((n, len(labels)))
    vol = np.zeros((n, len(labels)))
    mask = np.ones((n, len(labels)), dtype=bool)
    for i in range(n):
        px = base + np.cumsum(rng.normal(0, 0.6, len(labels)))
        close[i] = px
        if profile == "tiny":
            v = np.ones(len(labels), dtype=float)
        elif profile == "front20":
            v = rng.integers(4000, 9000, len(labels)).astype(float)
            v[25:] = 0.0
        else:
            v = rng.integers(4000, 9000, len(labels)).astype(float)
            v[rng.integers(0, len(labels), 3)] = 0.0   # real zero-volume minutes
        vol[i] = v
    i0 = labels.index("09:15")
    if prefix_nan:
        close[-1, i0 - 2] = np.nan
    if prefix_negvol:
        vol[-1, i0 - 3] = -5.0
    if include_release:
        mask[-1, i0 + 1:] = False
        close[-1, i0 + 1:] = future_value
        vol[-1, i0 + 1:] = 0.0 if np.isnan(future_value) else 1e9
    dates = [20260601 + k for k in range(n - 1)]
    if include_release:
        dates = dates + [20260701]
    np.savez_compressed(path, dates=np.array(dates), labels=np.array(labels), close=close,
                        volume=vol, mask=mask, contracts=np.array(["RB2610.SHF"] * n))


def v_ref_and_q(npz, tier=0.01):
    z = np.load(npz, allow_pickle=False)
    labels = [str(x) for x in z["labels"]]
    i0 = labels.index("09:15")
    ref = XC.reference(z["volume"], z["close"], z["mask"], labels, i0, z["close"].shape[0] - 1)
    return ref, max(1, int(np.floor(tier * ref["V_ref"])))


def request(npz, q, **kw):
    r = {"mode": "exante",
         "parent": {"symbol": "RB", "side": "buy", "qty_lots": q, "release_trading_date": 20260701,
                    "release_label": "09:15", "Dmax_trading_minutes": 60},
         "history_npz": npz,
         "baseline_context": {"product": "RB", "window": "am", "size_idx": 1, "risk_k": 1.0},
         "constraints": {"min_completion_probability": 0.95, "max_unfilled_lots": 0, "risk_sd_budget_bp": 5.4222},
         "scenario": {"cap_frac": 0.1, "fee_spread_bp": 1.3, "impact_a_bp": 0.5, "phi": 1.0,
                      "terminal_price_change_bp": 0.0}}
    for path, val in kw.items():
        cur = r
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = val
    return r


def run_cli(req, tmpdir):
    rq = os.path.join(tmpdir, "req.json")
    rs = os.path.join(tmpdir, "out.json")
    with open(rq, "w") as fh:
        json.dump(req, fh)
    p = subprocess.run([sys.executable, DECIDE, "--request", rq, "--out", rs], capture_output=True, text=True)
    out = {}
    if os.path.exists(rs):
        with open(rs) as fh:
            out = json.load(fh)
    return p.returncode, out, p.stdout, p.stderr


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.npz = os.path.join(self.tmp, "hist.npz")
        make_npz(self.npz)
        _, self.q = v_ref_and_q(self.npz)

    def test_exante_runs_and_never_permits(self):
        rc, out, so, se = run_cli(request(self.npz, self.q), self.tmp)
        self.assertEqual(rc, 0, se)
        self.assertEqual(out["execution_action"], "NONE")
        self.assertEqual(out["production_eligibility"], "INSUFFICIENT")
        self.assertIn(out["status"], ("REFERENCE_BASELINE", "INSUFFICIENT_EVIDENCE", "INSUFFICIENT_BASELINE"))
        rc2 = out["research_candidate"]
        self.assertIn(rc2["arm"], [a for a in ("asap0",) + tuple(f"{m}{h}" for m in ("twap", "shape") for h in (5, 10, 20, 30, 60))])
        self.assertIsNotNone(rc2["predicted"]["fee_bp"])
        self.assertIsNotNone(rc2["predicted_risk_bp"])
        self.assertEqual(out["capability"]["rule_runs"], True)
        self.assertEqual(out["capability"]["rule_enabled"], False)   # evidence is FAIL in this build
        self.assertIsNotNone(out["capability"]["why_not_usable"])

    def test_future_bars_cannot_change_anything(self):
        rc1, a, _, _ = run_cli(request(self.npz, self.q), self.tmp)
        alt = os.path.join(self.tmp, "hist_future.npz")
        make_npz(alt, future_value=9.9e9)
        rc2, b, _, _ = run_cli(request(alt, self.q), self.tmp)
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        for key in ("status", "choice", "state", "research_candidate", "reference"):
            self.assertEqual(json.dumps(a.get(key), sort_keys=True, ensure_ascii=False),
                             json.dumps(b.get(key), sort_keys=True, ensure_ascii=False), key)

    def test_training_cutoff_rejected(self):
        rc, out, _, _ = run_cli(request(self.npz, self.q, **{"parent.release_trading_date": 20260430}), self.tmp)
        self.assertEqual(out["status"], "INSUFFICIENT_EVIDENCE")

    def test_missing_release_day_rejected(self):
        no_rel = os.path.join(self.tmp, "no_rel.npz")
        make_npz(no_rel, include_release=False)
        rc, out, _, _ = run_cli(request(no_rel, self.q), self.tmp)
        self.assertEqual(out["status"], "INSUFFICIENT_DATA")

    def test_q_mismatch_rejected(self):
        rc, out, _, _ = run_cli(request(self.npz, self.q + 1), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        self.assertIn("qty_lots", out["detail"])

    def test_impact_must_be_fixed(self):
        rc, out, _, _ = run_cli(request(self.npz, self.q, **{"scenario.impact_a_bp": [0.0, 0.5]}), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")

    def test_deadline_changes_reachable_state(self):
        rc, out60, _, _ = run_cli(request(self.npz, self.q), self.tmp)
        rc2, out15, _, _ = run_cli(request(self.npz, self.q, **{"parent.Dmax_trading_minutes": 15,
                                                                 "constraints.risk_sd_budget_bp": budget_for(D=15)}), self.tmp)
        self.assertEqual(rc, 0)
        self.assertEqual(rc2, 0)
        cand = out15["research_candidate"]
        legal = {a for a in ("asap0", "twap5", "twap10", "shape5", "shape10")}
        self.assertTrue(cand["arm"] in legal or cand["status"] == "NO_FEASIBLE")


    def test_smaller_size_bucket_also_runs(self):
        _, q0 = v_ref_and_q(self.npz, tier=0.0025)
        rc, out, _, _ = run_cli(request(self.npz, q0, **{"baseline_context.size_idx": 0,
                                                         "constraints.risk_sd_budget_bp": budget_for(size=0)}), self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn(out["status"], ("REFERENCE_BASELINE", "INSUFFICIENT_EVIDENCE", "INSUFFICIENT_BASELINE"))
        cand = out["research_candidate"]
        legal = {"asap0", "twap5", "shape5"}
        self.assertTrue(cand["arm"] in legal or cand["status"] == "NO_FEASIBLE")

    def test_size_idx_mismatch_rejected(self):
        rc, out, _, _ = run_cli(request(self.npz, self.q, **{"baseline_context.size_idx": 0}), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")


class ScopeGuards(unittest.TestCase):
    """Out-of-scope requests must fail structurally, never be silently relaxed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.npz = os.path.join(self.tmp, "hist.npz")
        make_npz(self.npz)
        _, self.q = v_ref_and_q(self.npz)
        self.base_budget = budget_for()

    def _run(self, **kw):
        rc, out, so, se = run_cli(request(self.npz, self.q, **kw), self.tmp)
        self.assertEqual(rc, 0, se)
        return out

    def test_risk_budget_is_not_replaced(self):
        out = self._run(**{"constraints.risk_sd_budget_bp": 1.0})
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        self.assertIsNone(out["choice"])
        self.assertIn("budget", out["detail"])

    def test_completion_gate_is_not_relaxed(self):
        out = self._run(**{"constraints.min_completion_probability": 1.0})
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        self.assertIsNone(out["choice"])

    def test_nonzero_unfilled_is_out_of_scope(self):
        out = self._run(**{"constraints.max_unfilled_lots": 1})
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        self.assertIn("max_unfilled_lots", out["detail"])

    def test_direction_forecast_is_not_silently_ignored(self):
        out = self._run(**{"scenario.terminal_price_change_bp": 20.0})
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        self.assertIn("terminal_price_change_bp", out["detail"])

    def test_bad_date_format(self):
        out = self._run(**{"parent.release_trading_date": "2026-07-01"})
        self.assertEqual(out["status"], "INPUT_REQUIRED")

    def test_symbol_product_mismatch(self):
        out = self._run(**{"parent.symbol": "CU2610.SHF"})
        self.assertEqual(out["status"], "INPUT_REQUIRED")

    def test_window_and_risk_k_ranges(self):
        self.assertEqual(self._run(**{"baseline_context.window": "xx"})["status"], "INPUT_REQUIRED")
        self.assertEqual(self._run(**{"baseline_context.risk_k": 1.5})["status"], "INPUT_REQUIRED")

    def test_size_idx_is_taken_from_the_caller(self):
        _, q0 = v_ref_and_q(self.npz, tier=0.0025)
        req = request(self.npz, q0, **{"baseline_context.size_idx": 0})
        req["constraints"]["risk_sd_budget_bp"] = budget_for(size=0)
        rq = os.path.join(self.tmp, "s0.json")
        with open(rq, "w") as fh:
            json.dump(req, fh)
        rs = os.path.join(self.tmp, "s0_out.json")
        subprocess.run([sys.executable, DECIDE, "--request", rq, "--out", rs], capture_output=True, text=True)
        with open(rs) as fh:
            out = json.load(fh)
        self.assertEqual(out.get("size_idx"), 0)


class PrefixAndIdentity(unittest.TestCase):
    """Completed-prefix validity, product identity, session label and size typing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def _npz(self, **kw):
        p = os.path.join(self.tmp, f"h{len(os.listdir(self.tmp))}.npz")
        make_npz(p, **kw)
        return p

    def test_nan_inside_prefix_is_rejected(self):
        npz = self._npz(prefix_nan=True)
        _, q = v_ref_and_q(npz)
        rc, out, _, se = run_cli(request(npz, q), self.tmp)
        self.assertEqual(rc, 0, se)
        self.assertIn(out["status"], ("INSUFFICIENT_DATA", "INPUT_REQUIRED"))
        self.assertIsNone(out.get("choice"))
        self.assertNotIn("state", out)

    def test_negative_volume_inside_prefix_is_rejected(self):
        npz = self._npz(prefix_negvol=True)
        _, q = v_ref_and_q(npz)
        rc, out, _, se = run_cli(request(npz, q), self.tmp)
        self.assertEqual(out["status"], "INSUFFICIENT_DATA")
        self.assertIsNone(out.get("choice"))

    def test_similar_product_prefix_is_not_a_match(self):
        npz = self._npz()
        _, q = v_ref_and_q(npz)
        rc, out, _, _ = run_cli(request(npz, q, **{"baseline_context.product": "I",
                                                   "parent.symbol": "IF2609.CFE"}), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        rc, out, _, _ = run_cli(request(npz, q, **{"baseline_context.product": "P",
                                                   "parent.symbol": "PP2601.DCE"}), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")

    def test_release_label_must_match_window(self):
        npz = self._npz()
        _, q = v_ref_and_q(npz)
        rc, out, _, _ = run_cli(request(npz, q, **{"baseline_context.window": "pm"}), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        self.assertIn("release_label", out["detail"])

    def test_cfe_has_no_night_session(self):
        npz = self._npz()
        _, q = v_ref_and_q(npz)
        rc, out, _, _ = run_cli(request(npz, q, **{"baseline_context.product": "IF",
                                                   "parent.symbol": "IF2609.CFE",
                                                   "baseline_context.window": "night"}), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")

    def test_fractional_size_idx_is_rejected(self):
        npz = self._npz()
        _, q = v_ref_and_q(npz)
        rc, out, _, _ = run_cli(request(npz, q, **{"baseline_context.size_idx": 0.5}), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        self.assertIn("size_idx", out["detail"])

    def test_impossible_calendar_date_is_rejected(self):
        npz = self._npz()
        _, q = v_ref_and_q(npz)
        rc, out, _, _ = run_cli(request(npz, q, **{"parent.release_trading_date": 20260231}), self.tmp)
        self.assertEqual(out["status"], "INPUT_REQUIRED")
        self.assertIn("calendar date", out["detail"])


class BaselineHandling(unittest.TestCase):
    """A is only a reference when it is feasible today."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.real = MOD._frozen_row
        self.addCleanup(setattr, MOD, "_frozen_row", self.real)

    def _req(self, npz, q, size=1):
        r = request(npz, q)
        r["constraints"]["risk_sd_budget_bp"] = budget_for(size=size)
        r["baseline_context"]["size_idx"] = size
        r["_path"] = os.path.join(self.tmp, "req.json")
        return r

    def test_infeasible_A_with_candidate_needs_review(self):
        npz = os.path.join(self.tmp, "front.npz")
        make_npz(npz, profile="front20")
        _, q = v_ref_and_q(npz)
        MOD._frozen_row = lambda bc, p, a: {"status": "ok", "method": "twap", "h": "60",
                                            "budget_bp": str(budget_for())}
        out = MOD.run_exante(self._req(npz, q))
        self.assertEqual(out["status"], "REVIEW_REQUIRED")
        self.assertIsNone(out["choice"])
        self.assertFalse(out["reference"]["feasible_today"])
        self.assertIsNotNone(out["research_candidate"]["arm"])

    def test_no_feasible_arm_is_model_infeasible(self):
        npz = os.path.join(self.tmp, "tiny.npz")
        make_npz(npz, profile="tiny")
        _, q0 = v_ref_and_q(npz, tier=0.0025)
        out = MOD.run_exante(self._req(npz, q0, size=0))
        self.assertEqual(out["status"], "MODEL_INFEASIBLE")
        self.assertIsNone(out["choice"])

    def test_missing_A_and_no_feasible_arm_is_model_infeasible(self):
        npz = os.path.join(self.tmp, "tiny2.npz")
        make_npz(npz, profile="tiny")
        _, q0 = v_ref_and_q(npz, tier=0.0025)
        MOD._frozen_row = lambda bc, p, a: {"status": "no_feasible", "method": "", "h": "",
                                            "budget_bp": str(budget_for(size=0))}
        out = MOD.run_exante(self._req(npz, q0, size=0))
        self.assertEqual(out["status"], "MODEL_INFEASIBLE")
        self.assertIsNone(out["choice"])

    def test_missing_A_is_insufficient_baseline(self):
        npz = os.path.join(self.tmp, "hist.npz")
        make_npz(npz)
        _, q = v_ref_and_q(npz)
        MOD._frozen_row = lambda bc, p, a: {"status": "no_feasible", "method": "", "h": "",
                                            "budget_bp": str(budget_for())}
        out = MOD.run_exante(self._req(npz, q))
        self.assertEqual(out["status"], "INSUFFICIENT_BASELINE")
        self.assertIsNone(out["choice"])


class TieOrder(unittest.TestCase):
    def _pred(self, rows):
        return {arm: {"T": t, "L": l, "comp": c, "fee": f} for arm, (t, l, c, f) in rows.items()}

    def test_all_tied_prefers_asap(self):
        pred = self._pred({a: (10.0, 5.0, 1.0, 1.3) for a in ("asap0", "twap5", "shape5", "twap10")})
        ch = XC.choose(pred, 1.0, 0.0, 0.0, 1, 100.0, 1.0, {})
        self.assertEqual(ch["arm"], "asap0")

    def test_same_h_prefers_twap(self):
        pred = self._pred({"asap0": (10.0, 5.0, 0.0, 1.3), "twap5": (10.0, 5.0, 1.0, 1.3),
                           "shape5": (10.0, 5.0, 1.0, 1.3)})
        ch = XC.choose(pred, 1.0, 0.0, 0.0, 1, 100.0, 1.0, {})
        self.assertEqual(ch["arm"], "twap5")

    def test_shorter_h_first_on_tie(self):
        pred = self._pred({"asap0": (10.0, 5.0, 0.0, 1.3), "twap10": (10.0, 5.0, 1.0, 1.3),
                           "shape5": (10.0, 5.0, 1.0, 1.3)})
        ch = XC.choose(pred, 1.0, 0.0, 0.0, 1, 100.0, 1.0, {})
        self.assertEqual(ch["arm"], "shape5")

    def test_risk_budget_excludes(self):
        pred = self._pred({"asap0": (5.0, 5.0, 1.0, 1.3), "twap10": (30.0, 4.0, 1.0, 1.3)})
        ch = XC.choose(pred, 1.0, 0.0, 0.0, 1, 10.0, 1.0, {})
        self.assertEqual(ch["arm"], "asap0")
        self.assertIn("risk above budget", ch["excluded"].get("twap10", ""))


class RiskTolerance(unittest.TestCase):
    """budget_bp is stored with 4 decimals; the comparison must use 5e-5 bp, not a percentage."""

    def _pred(self):
        return {"asap0": {"T": 5.0, "L": 5.0, "comp": 1.0, "fee": 1.3},
                "twap10": {"T": 5.0, "L": 4.0, "comp": 1.0, "fee": 1.3}}

    def test_asap_rounding_is_not_a_breach(self):
        ch = XC.choose(self._pred(), 1.0, 0.0, 0.5, 1, 10.0, 1.0, {"asap0": [77, 10.0 + 3e-5]})
        self.assertIn("asap0", ch["feasible"])
        self.assertNotIn("risk above budget", ch["excluded"].get("asap0", ""))

    def test_tiny_overshoot_within_storage_tolerance(self):
        ch = XC.choose(self._pred(), 1.0, 0.0, 0.5, 1, 10.0, 1.0,
                       {"asap0": [77, 10.0], "twap10": [77, 10.0 + 1e-5]})
        self.assertIn("twap10", ch["feasible"])

    def test_real_overshoot_is_excluded(self):
        ch = XC.choose(self._pred(), 1.0, 0.0, 0.5, 1, 10.0, 1.0,
                       {"asap0": [77, 10.0], "twap10": [77, 10.0 + 1e-3]})
        self.assertNotIn("twap10", ch["feasible"])
        self.assertEqual(ch["excluded"].get("twap10"), "risk above budget")

    def test_prior_scaling_still_binds(self):
        pred = {"asap0": {"T": 5.0, "L": 5.0, "comp": 1.0, "fee": 1.3},
                "twap10": {"T": 20.0, "L": 4.0, "comp": 1.0, "fee": 1.3}}
        ch = XC.choose(pred, 1.0, 0.0, 0.5, 1, 10.0, 1.0, {})
        self.assertNotIn("twap10", ch["feasible"])


if __name__ == "__main__":
    unittest.main()
