import importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest
import numpy as np
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec=importlib.util.spec_from_file_location("deci",os.path.join(HERE,"scripts","decision.py"))
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
EX=os.path.join(HERE,"examples"); NPZ=os.path.join(EX,"RB_am_20d_20260803.npz")
def req(**kw):
    r={"parent":{"symbol":"RB","side":"buy","qty_lots":1186,"release_trading_date":"20260803","release_label":"09:15","Dmax_trading_minutes":60},
       "history_npz":NPZ,"constraints":{"min_completion_probability":0.95,"max_unfilled_lots":0,"risk_sd_budget_bp":40.0},
       "scenario":{"cap_frac":0.1,"fee_spread_bp":1.3,"impact_a_bp":0.5,"phi":1.0,"terminal_price_change_bp":0.0},"mode":"compare","_path":os.path.join(EX,"request.json")}
    for path,val in kw.items():
        cur=r; parts=path.split(".")
        for p in parts[:-1]: cur=cur[p]
        cur[parts[-1]]=val
    return r
def run(r): return mod.apply_baseline(mod.run(r),r)
class T(unittest.TestCase):
    def temporary_directory(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return directory.name

    def test_missing_risk(self):
        r=req(); del r["constraints"]["risk_sd_budget_bp"]; self.assertEqual(run(r)["status"],"INPUT_REQUIRED")
    def test_frac_qty(self):
        self.assertEqual(run(req(**{"parent.qty_lots":3.7}))["status"],"INPUT_REQUIRED")
    def test_bad_side(self):
        self.assertEqual(run(req(**{"parent.side":"short"}))["status"],"INPUT_REQUIRED")
    def test_bool_D(self):
        self.assertEqual(run(req(**{"parent.Dmax_trading_minutes":True}))["status"],"INPUT_REQUIRED")
    def test_nan_hist(self):
        z=dict(np.load(NPZ,allow_pickle=False)); z["close"][0,:]=np.nan
        p=os.path.join(self.temporary_directory(),"t_nan.npz"); np.savez_compressed(p,**z)
        self.assertEqual(run(req(history_npz=p))["status"],"INSUFFICIENT_DATA")
    def test_future_perturbation(self):
        z=dict(np.load(NPZ,allow_pickle=False)); base=run(req())
        n=z["close"].shape[0]; add=3
        z["dates"]=np.concatenate([z["dates"],np.array([20260803,20260804,20260805])])
        for k in ("close","volume","mask"):
            extra=np.repeat(z[k][-1:],add,axis=0) if k!="mask" else np.repeat(z[k][-1:],add,axis=0)
            z[k]=np.concatenate([z[k],extra]); 
        for i in range(n,n+add): z["close"][i,:]=np.nan; z["volume"][i,:]=-9.0; z["mask"][i,:]=False
        p=os.path.join(self.temporary_directory(),"t_fut.npz"); np.savez_compressed(p,**z)
        self.assertEqual(json.dumps(base,sort_keys=True),json.dumps(run(req(history_npz=p)),sort_keys=True))
    def test_new_q_recompute(self):
        a=run(req()); b=run(req(**{"parent.qty_lots":600}))
        self.assertNotEqual(a.get("ref_avg_volume_lots"),None)
        self.assertTrue(a["status"] in ("CONDITIONAL_MODEL_CHOICE","MODEL_INFEASIBLE"))
        self.assertNotEqual(json.dumps(a.get("candidates")),json.dumps(b.get("candidates")))
    def test_overcapacity(self):
        self.assertEqual(run(req(**{"parent.qty_lots":200000,"scenario.impact_a_bp":None}))["status"],"MODEL_INFEASIBLE")
    def test_a_bounds(self):
        n=run(req(**{"scenario.impact_a_bp":None})); self.assertEqual(n["status"],"CONDITIONAL_SET")
        self.assertTrue(all(s["a_to"]=="inf" for s in n["segments"][-1:]))
        g=run(req(**{"scenario.impact_a_bp":[0,2]})); self.assertTrue(all(float(s["a_to"])<=2.0 for s in g["segments"]))
        p=run(req(**{"scenario.impact_a_bp":[0.5,0.5]})); self.assertTrue(all(float(s["a_from"])>=0.5 and float(s["a_to"])<=0.5 for s in p["segments"]))
    def test_budget_changes(self):
        tight=run(req(**{"constraints.risk_sd_budget_bp":1.0})); loose=run(req(**{"constraints.risk_sd_budget_bp":200.0}))
        self.assertNotEqual(len([c for c in tight["candidates"] if c["feasible"]]),len([c for c in loose["candidates"] if c["feasible"]]))
    def test_b_direction(self):
        buy=run(req(**{"scenario.terminal_price_change_bp":-50.0}))
        sell=run(req(**{"parent.side":"sell","scenario.terminal_price_change_bp":-50.0}))
        self.assertTrue(buy["status"] in ("CONDITIONAL_MODEL_CHOICE","MODEL_INFEASIBLE"))
        self.assertTrue(sell["status"] in ("CONDITIONAL_MODEL_CHOICE","MODEL_INFEASIBLE"))
    def test_baseline_missing_ctx(self):
        self.assertEqual(run(req(mode="baseline_first"))["status"],"INSUFFICIENT_BASELINE")
    def test_baseline_compare_keeps(self):
        r=req(mode="compare"); self.assertNotEqual(run(r)["status"],"REFERENCE_BASELINE")
    def test_baseline_future_training(self):
        old=os.path.join(EX,"RB_am_20d.npz")
        r=req(**{"parent.release_trading_date":"20251202"},mode="baseline_first",history_npz=old,baseline_context={"product":"RB","window":"am","size_idx":1,"risk_k":1.0})
        d=run(r); self.assertEqual(d["status"],"INSUFFICIENT_BASELINE"); self.assertIn("training",d["baseline_reason"])
    def test_baseline_out_of_scope(self):
        r=req(**{"parent.qty_lots":1187},mode="baseline_first",baseline_context={"product":"RB","window":"am","size_idx":1,"risk_k":1.0},constraints={"min_completion_probability":0.95,"max_unfilled_lots":0,"risk_sd_budget_bp":40.0})
        self.assertEqual(run(r)["status"],"INSUFFICIENT_BASELINE")
    def test_reference_choice_matches_arm(self):
        r=req(**{"scenario.impact_a_bp":0.0},mode="baseline_first",baseline_context={"product":"RB","window":"am","size_idx":1,"risk_k":2.0},constraints={"min_completion_probability":0.90,"max_unfilled_lots":0,"risk_sd_budget_bp":10.8444})
        d=run(r)
        self.assertEqual(d["status"],"REFERENCE_BASELINE")
        self.assertEqual(d["choice"],d["baseline"]["arm"])
        self.assertEqual(d["comparison_to_baseline"]["conditional_choice"],"asap0")
    def test_cli_relocated(self):
        tmp=self.temporary_directory(); dst=os.path.join(tmp,"pkg"); shutil.copytree(HERE,dst)
        r=req()
        r["history_npz"]="RB_am_20d_20260803.npz"
        reqp=os.path.join(dst,"examples","q_relocated.json")
        json.dump(r,open(reqp,"w"))
        out=subprocess.run([sys.executable,"scripts/decision.py","--request",os.path.join("examples","q_relocated.json"),"--out",os.path.join(tmp,"o.json")],cwd=dst,capture_output=True,text=True)
        self.assertEqual(out.returncode,0,out.stderr[:300])
        d=json.load(open(os.path.join(tmp,"o.json")))
        self.assertIn(d["status"],("CONDITIONAL_MODEL_CHOICE","REFERENCE_BASELINE"))
        self.assertTrue(d.get("choice"))
        self.assertEqual(d.get("n_history_days"),20)
        self.assertEqual(json.dumps(d,sort_keys=True),json.dumps(run(r),sort_keys=True))
if __name__=="__main__": unittest.main(verbosity=2)
