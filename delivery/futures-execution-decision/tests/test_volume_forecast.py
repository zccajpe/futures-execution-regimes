"""Tests for mode=volume_forecast and the exante_activity refusal.

Behaviour that matters:
  * the 60-minute total forecast reproduces the Stage 1 fixed RB case;
  * the completion hint uses the caller's D (cap[:, :D] and an ASAP target of D),
    and is explicitly separated from the 60-minute forecast;
  * product/window/symbol/release-label scope is validated (IF vs I, PP vs P, no CFE night);
  * dates inside the training window are refused as insufficient evidence;
  * the pre-release prefix and the prior same-prefix mean are validated with NO 1.0 fallback
    (including the "prior prefix zero, post-release normal" hole);
  * bars after the release instant cannot change the forecast;
  * every return path is normalised: mode set, choice null, execution_action NONE,
    production_eligibility INSUFFICIENT;
  * mode=exante_activity is refused with INSUFFICIENT_EVIDENCE.
"""
import importlib.util, json, os, subprocess, sys, tempfile, unittest

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECIDE = os.path.join(HERE, "scripts", "decision.py")
CORE = os.path.join(HERE, "scripts", "exante_core.py")
EX = os.path.join(HERE, "examples")
NPZ = os.path.join(EX, "RB_am_20d_20260701_prefix.npz")
spec = importlib.util.spec_from_file_location("xc", CORE)
XC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(XC)

EXPECT_ACT = 1.2948947049366122
EXPECT_S = 1.1543178886529266
EXPECT_B60 = 106858.6


def request(**kw):
    r = {"mode": "volume_forecast",
         "parent": {"symbol": "RB2610.SHF", "side": "buy", "qty_lots": 1068,
                    "release_trading_date": 20260701, "release_label": "09:15",
                    "Dmax_trading_minutes": 60},
         "history_npz": NPZ,
         "baseline_context": {"product": "RB", "window": "am", "size_idx": 1, "risk_k": 1.0},
         "constraints": {"min_completion_probability": 0.95, "max_unfilled_lots": 0,
                         "risk_sd_budget_bp": 5.4222},
         "scenario": {"cap_frac": 0.1, "fee_spread_bp": 1.3, "impact_a_bp": 0.5, "phi": 1.0,
                      "terminal_price_change_bp": 0.0}}
    for path, val in kw.items():
        cur = r
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur[p]
        cur[parts[-1]] = val
    return r


def run(req, tmp):
    rq = os.path.join(tmp, "req.json")
    rs = os.path.join(tmp, "out.json")
    with open(rq, "w") as fh:
        json.dump(req, fh)
    p = subprocess.run([sys.executable, DECIDE, "--request", rq, "--out", rs],
                       capture_output=True, text=True,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    out = {}
    if os.path.exists(rs):
        with open(rs) as fh:
            out = json.load(fh)
    return p.returncode, out, p.stderr


def write_npz(path, mutate):
    z = dict(np.load(NPZ, allow_pickle=False))
    z = {k: np.array(v, copy=True) for k, v in z.items()}
    mutate(z)
    np.savez_compressed(path, **z)
    return path


def raw_activity_and_scale():
    """Independent derivation from the NPZ: prefix sums and the fitted beta only."""
    z = np.load(NPZ, allow_pickle=False)
    labels = [str(x) for x in z["labels"]]
    i0 = labels.index("09:15")
    di = z["close"].shape[0] - 1
    start = 0
    for i in range(1, i0 + 1):
        a = int(labels[i][:2]) * 60 + int(labels[i][3:])
        b = int(labels[i - 1][:2]) * 60 + int(labels[i - 1][3:])
        if (a - b) % 1440 != 1:
            start = i
    cur = slice(start, i0 + 1)
    prior = list(range(di - 20, di))
    base = float(np.mean([z["volume"][j, cur].sum() for j in prior]))
    act = float(z["volume"][di, cur].sum()) / base
    with open(os.path.join(HERE, "data", "params_volume.json")) as fh:
        beta = json.load(fh)["beta"]
    return z, i0, prior, act, max(0.0, 1.0 + beta * (act - 1.0))


def raw_completion_hint(D, Q, scale):
    """ASAP identity on raw volumes: filled = min(Q, sum_t floor(0.1 * V_t * scale))."""
    z, i0, prior, _, _ = raw_activity_and_scale()
    V = z["volume"][prior][:, i0 + 1:i0 + 1 + D].astype(float)
    cap = np.floor(0.10 * V * scale)
    return float((np.minimum(Q, cap.sum(axis=1)) >= Q).mean())


class VolumeForecast(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def assert_normalised(self, out):
        self.assertEqual(out.get("mode"), "volume_forecast")
        self.assertIsNone(out.get("choice"))
        self.assertEqual(out.get("execution_action"), "NONE")
        self.assertEqual(out.get("production_eligibility"), "INSUFFICIENT")

    def test_forecast_matches_stage1_case(self):
        rc, out, se = run(request(), self.tmp)
        self.assertEqual(rc, 0, se)
        self.assert_normalised(out)
        self.assertEqual(out["status"], "FORECAST_ONLY")
        f = out["forecast"]
        self.assertAlmostEqual(f["act_pre"], EXPECT_ACT, places=6)
        self.assertAlmostEqual(f["scale"], EXPECT_S, places=6)
        self.assertAlmostEqual(f["B60"], EXPECT_B60, places=1)
        self.assertAlmostEqual(f["pred60"], EXPECT_B60 * EXPECT_S, delta=0.05)
        self.assertEqual(f["forecast_horizon_trading_minutes"], 60)
        self.assertGreaterEqual(f["model_completion_hint"], 0.0)
        self.assertLessEqual(f["model_completion_hint"], 1.0)

    def test_hint_uses_the_requested_deadline_and_scaled_capacity(self):
        rc5, out5, _ = run(request(**{"parent.Dmax_trading_minutes": 5}), self.tmp)
        rc60, out60, _ = run(request(**{"parent.Dmax_trading_minutes": 60}), self.tmp)
        self.assertEqual((rc5, rc60), (0, 0))
        _, _, _, act, scale = raw_activity_and_scale()
        self.assertAlmostEqual(scale, 1.1543178886529266, places=12)   # scale != 1, so the test discriminates
        self.assertAlmostEqual(act, EXPECT_ACT, places=9)
        exp5 = raw_completion_hint(5, 1068, scale)
        exp60 = raw_completion_hint(60, 1068, scale)
        self.assertEqual(exp5, 0.60)          # raw-volume identity for D=5 with the scaled capacity
        self.assertEqual(exp60, 1.00)
        unscaled5 = raw_completion_hint(5, 1068, 1.0)
        self.assertEqual(unscaled5, 0.45)     # the unscaled capacity would give 0.45, not 0.60
        self.assertEqual(out5["forecast"]["hint_horizon_trading_minutes"], 5)
        self.assertEqual(out60["forecast"]["hint_horizon_trading_minutes"], 60)
        self.assertAlmostEqual(out5["forecast"]["model_completion_hint"], exp5, places=12)
        self.assertAlmostEqual(out60["forecast"]["model_completion_hint"], exp60, places=12)
        self.assertAlmostEqual(out5["forecast"]["pred60"], out60["forecast"]["pred60"], places=6)

    def test_other_capacity_does_not_silently_use_ten_percent(self):
        rc, out, _ = run(request(**{"scenario.cap_frac": 0.05}), self.tmp)
        self.assertEqual(rc, 0)
        self.assert_normalised(out)
        self.assertEqual(out["status"], "FORECAST_ONLY")
        self.assertIsNone(out["forecast"]["model_completion_hint"])
        self.assertIn("cap_frac=0.10", out["forecast"]["hint_note"])

    def test_future_bars_cannot_change_forecast(self):
        rc1, a, _ = run(request(), self.tmp)

        def mutate(z):
            i0 = [str(x) for x in z["labels"]].index("09:15")
            z["volume"][-1, i0 + 1:] = 1e9
            z["close"][-1, i0 + 1:] = np.nan

        alt = write_npz(os.path.join(self.tmp, "future.npz"), mutate)
        rc2, b, _ = run(request(history_npz=alt), self.tmp)
        self.assertEqual((rc1, rc2), (0, 0))
        for k in ("status", "forecast", "choice", "execution_action", "production_eligibility"):
            self.assertEqual(json.dumps(a.get(k), sort_keys=True, ensure_ascii=False),
                             json.dumps(b.get(k), sort_keys=True, ensure_ascii=False), k)

    def test_prior_prefix_zero_is_refused_even_if_post_release_is_normal(self):
        def mutate(z):
            labels = [str(x) for x in z["labels"]]
            i0 = labels.index("09:15")
            s0 = XC.segment_start(labels, i0)
            z["volume"][:-1, s0:i0 + 1] = 0.0   # only the prefix; post-release window untouched

        alt = write_npz(os.path.join(self.tmp, "prefix_zero.npz"), mutate)
        rc, out, _ = run(request(history_npz=alt), self.tmp)
        self.assert_normalised(out)
        self.assertEqual(out["status"], "INSUFFICIENT_DATA")
        self.assertIn("prior same-prefix mean", out["detail"])
        self.assertNotIn("forecast", out)

    def test_missing_prefix_and_non_finite_prefix_are_refused(self):
        def missing(z):
            i0 = [str(x) for x in z["labels"]].index("09:15")
            z["mask"][-1, i0 - 2] = False

        def nan(z):
            i0 = [str(x) for x in z["labels"]].index("09:15")
            z["close"][-1, i0 - 3] = np.nan

        for tag, mutate in (("missing", missing), ("nan", nan)):
            alt = write_npz(os.path.join(self.tmp, f"{tag}.npz"), mutate)
            rc, out, _ = run(request(history_npz=alt), self.tmp)
            self.assert_normalised(out)
            self.assertEqual(out["status"], "INSUFFICIENT_DATA")

    def test_scope_and_label_validation(self):
        cases = [
            ("product_prefix_PP", {"baseline_context.product": "P", "parent.symbol": "PP2601.DCE"}),
            ("product_prefix_I", {"baseline_context.product": "I", "parent.symbol": "IF2609.CFE"}),
            ("unknown_product", {"baseline_context.product": "XX", "parent.symbol": "XX2601.DCE"}),
            ("cfe_night", {"baseline_context.product": "IF", "baseline_context.window": "night",
                           "parent.symbol": "IF2609.CFE"}),
            ("label_window_mismatch", {"baseline_context.window": "pm"}),
        ]
        for tag, kw in cases:
            rc, out, _ = run(request(**kw), self.tmp)
            self.assert_normalised(out)
            self.assertEqual(out["status"], "INPUT_REQUIRED", tag)

    def test_training_window_date_is_refused(self):
        rc, out, _ = run(request(**{"parent.release_trading_date": 20260430}), self.tmp)
        self.assert_normalised(out)
        self.assertEqual(out["status"], "INSUFFICIENT_EVIDENCE")

    def test_exante_activity_is_refused(self):
        rc, out, se = run(request(mode="exante_activity"), self.tmp)
        self.assertEqual(rc, 0, se)
        self.assertEqual(out["mode"], "exante_activity")
        self.assertIsNone(out["choice"])
        self.assertEqual(out["execution_action"], "NONE")
        self.assertEqual(out["production_eligibility"], "INSUFFICIENT")
        self.assertEqual(out["status"], "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
