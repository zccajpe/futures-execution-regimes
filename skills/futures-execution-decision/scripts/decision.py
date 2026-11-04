#!/usr/bin/env python3
"""Conditional execution-schedule advisor. Advice only: execution_action is always NONE."""
import argparse, json, math, os
import numpy as np
ARMS=[("asap",0)]+[(m,h) for m in ("twap","shape") for h in (5,10,20,30,60)]
RESEARCH_SCOPE="research-only advisory output; not a production release and not an order permission"
def bad(status,detail): return {"status":status,"detail":detail,"execution_action":"NONE",
    "production_eligibility":"INSUFFICIENT","scope":RESEARCH_SCOPE,
    "next_action":"the request was not modified and no default was substituted; fix or supply the missing/invalid input and re-run"}
def finite(x): return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def validate(req):
    p=req.get("parent"); c=req.get("constraints"); s=req.get("scenario",{})
    if not isinstance(p,dict) or not isinstance(c,dict): return "parent and constraints are required"
    if p.get("side") not in ("buy","sell"): return "parent.side must be buy or sell"
    q=p.get("qty_lots")
    if not isinstance(q,int) or isinstance(q,bool) or q<=0: return "parent.qty_lots must be a positive integer (no truncation)"
    d=p.get("Dmax_trading_minutes")
    if isinstance(d,bool) or not isinstance(d,int) or not 1<=d<=60: return "parent.Dmax_trading_minutes must be an integer in [1,60]; outside is not supported"
    if not isinstance(p.get("release_trading_date"),(str,int)): return "parent.release_trading_date required"
    if not isinstance(p.get("release_label"),str): return "parent.release_label required"
    if not isinstance(p.get("symbol"),str) or not p["symbol"].strip(): return "parent.symbol must be a non-empty string"
    if not isinstance(req.get("history_npz"),str) or not req["history_npz"].strip(): return "history_npz must be a non-empty path string"
    if "risk_sd_budget_bp" not in c: return "constraints.risk_sd_budget_bp is required"
    rb=c["risk_sd_budget_bp"]
    if not finite(rb) or rb<0: return "constraints.risk_sd_budget_bp must be a finite non-negative number"
    mc=c.get("min_completion_probability",0.95)
    if not finite(mc) or not 0<=mc<=1: return "min_completion_probability must be in [0,1]"
    mu=c.get("max_unfilled_lots",0)
    if not isinstance(mu,int) or isinstance(mu,bool) or not 0<=mu<=q: return "max_unfilled_lots must be an integer in [0, qty_lots]"
    cap=s.get("cap_frac",0.10); phi=s.get("phi",1.0); fee=s.get("fee_spread_bp",1.3); b=s.get("terminal_price_change_bp",0.0)
    if not finite(cap) or not 0<cap<=1: return "cap_frac must be in (0,1]"
    if phi not in (1,0.5,1.0,0.5): return "phi must be 1 or 0.5"
    if not finite(fee) or fee<0: return "fee_spread_bp must be finite and non-negative"
    if not finite(b): return "terminal_price_change_bp must be finite"
    a=s.get("impact_a_bp",None)
    if a is not None:
        if isinstance(a,(list,tuple)):
            if len(a)!=2 or not all(finite(x) for x in a) or a[0]<0 or a[1]<a[0]: return "impact_a_bp interval must be [lo,hi] with 0<=lo<=hi"
        elif not finite(a) or a<0: return "impact_a_bp must be null, a non-negative finite number, or [lo,hi]"
    return None
def load_hist(path,release):
    if not os.path.exists(path): return None,"history_npz not found"
    try:
        with np.load(path,allow_pickle=False) as z:
            need=["dates","labels","close","volume","mask"]
            miss=[k for k in need if k not in z.files]
            if miss: return None,f"history missing fields {miss}"
            d={k:np.array(z[k],copy=True) for k in need}
    except Exception as e: return None,f"cannot read history_npz: {type(e).__name__}"
    if d["close"].ndim!=2 or d["volume"].ndim!=2 or d["close"].shape!=d["volume"].shape: return None,"close/volume shape mismatch"
    if d["mask"].shape!=d["close"].shape: return None,"mask shape mismatch"
    if len(d["dates"])!=d["close"].shape[0]: return None,"dates length does not match rows"
    if len(d["labels"])!=d["close"].shape[1]: return None,"labels length does not match columns"
    labels=[str(x) for x in d["labels"]]
    if len(set(labels))!=len(labels): return None,"duplicate labels"
    dates=[str(x) for x in d["dates"]]
    if len(set(dates))!=len(dates): return None,"duplicate trading dates"
    if dates!=sorted(dates): return None,"dates not sorted ascending"
    hist=[i for i,dd in enumerate(dates) if int(dd)<int(release)]
    if not hist: return None,"no trading days before release"
    sl=np.array(hist)
    if not np.isfinite(d["close"][sl]).all() or not np.isfinite(d["volume"][sl]).all(): return None,"historical rows contain NaN or inf"
    if (d["volume"][sl]<0).any(): return None,"historical rows contain negative volume"
    return d,None
def build_targets(method,h,Q,cumw,D):
    if method=="asap": return np.full(D,Q,dtype=np.int64)
    if method=="twap": frac=np.minimum(np.arange(1,D+1)/h,1.0)
    else:
        if cumw[h-1]<=0: return None
        frac=np.minimum(cumw[:D]/cumw[h-1],1.0)
    tg=np.minimum(np.floor(Q*frac+1e-12).astype(np.int64),Q); tg[h-1:]=Q; return tg
def simulate(tg,capv):
    cc=np.cumsum(capv,axis=1); slack=np.minimum(0,np.minimum.accumulate(tg[None,:]-cc,axis=1))
    cum=np.maximum.accumulate(np.clip(cc+slack,0,tg[-1]),axis=1)
    return np.diff(np.concatenate([np.zeros((capv.shape[0],1)),cum],axis=1),axis=1)
def sanitize(x):
    if isinstance(x,float):
        if math.isnan(x): return "NaN"
        if math.isinf(x): return "inf" if x>0 else "-inf"
        return x
    if isinstance(x,dict): return {k:sanitize(v) for k,v in x.items()}
    if isinstance(x,list): return [sanitize(v) for v in x]
    return x
def run(req):
    err=validate(req)
    if err: return bad("INPUT_REQUIRED",err)
    p=req["parent"]; c=req["constraints"]; s=req.get("scenario",{})
    side=1 if p["side"]=="buy" else -1
    Q=p["qty_lots"]; D=p["Dmax_trading_minutes"]
    cap=float(s.get("cap_frac",0.10)); fee=float(s.get("fee_spread_bp",1.3))
    phi=float(s.get("phi",1.0)); b=float(s.get("terminal_price_change_bp",0.0)); a_in=s.get("impact_a_bp",None)
    rd=str(p["release_trading_date"])
    if not (rd.isdigit() and len(rd)==8): return bad("INPUT_REQUIRED","release_trading_date must be YYYYMMDD")
    rel=int(rd)
    hp=req["history_npz"]
    if not os.path.isabs(hp): hp=os.path.join(os.path.dirname(req["_path"]),hp)
    h,err=load_hist(hp,rel)
    if err: return bad("INSUFFICIENT_DATA",err)
    labels=[str(x) for x in h["labels"]]
    if p["release_label"] not in labels: return bad("INSUFFICIENT_DATA","release_label not present in the history grid")
    i0=labels.index(p["release_label"])
    if i0+61>len(labels): return bad("INSUFFICIENT_DATA","fewer than 60 trading minutes after the release label")
    rel=int(p["release_trading_date"]) if str(p["release_trading_date"]).isdigit() else None
    if rel is None: return bad("INPUT_REQUIRED","release_trading_date must be YYYYMMDD")
    dates=[str(x) for x in h["dates"]]
    prior=[i for i,d in enumerate(dates) if int(d)<rel]
    if len(prior)<20: return bad("INSUFFICIENT_DATA",f"only {len(prior)} trading days before release; 20 required")
    win=slice(i0+1,i0+61); good=[]
    for i in reversed(prior):
        if h["mask"][i,win].all() and h["mask"][i,i0] and (h["close"][i,win]>0).all() and h["close"][i,i0]>0:
            good.append(i)
            if len(good)==20: break
    if len(good)<20: return bad("INSUFFICIENT_DATA",f"only {len(good)} complete 60-minute paths before release; 20 required")
    days=sorted(good)
    V=h["volume"][days][:,win].astype(float); C=h["close"][days][:,win].astype(float); P0=h["close"][days][:,i0].astype(float)
    Rh=C/P0[:,None]
    tot=V.sum(axis=1); ok=tot>0
    ref_avg=float(tot[ok].mean()) if ok.any() else float("nan")
    if not ok.any(): return bad("INSUFFICIENT_DATA","history has no trading volume in the window")
    cumw=np.cumsum((V[ok]/tot[ok,None]).mean(axis=0)); cumw=cumw/cumw[-1]
    capv=np.floor(cap*V).astype(np.int64)
    t=np.arange(1,D+1,dtype=float); cands=[]
    for m,hh in ARMS:
        if hh>D: continue
        tg=build_targets(m,hh,Q,cumw,D)
        if tg is None:
            cands.append(dict(arm=f"{m}{hh}",method=m,h=hh,feasible=False,excluded="zero volume shape denominator")); continue
        f=simulate(tg,capv[:,:D]); filled=f.sum(axis=1); R=Q-filled
        gap=1e4*((f*(Rh[:,:D]-1)).sum(axis=1)/Q + R/Q*(Rh[:,59]-1))
        exposure=(f*t).sum(axis=1)/(Q*60)+R/Q
        drift_bp=side*b*exposure
        risk=float(np.std(side*gap+drift_bp,ddof=1)) if len(gap)>1 else 0.0
        part=f/(0.01*np.maximum(V[:,:D],1e-12))
        L=float(((f/Q*part) if phi==1 else (f/Q*np.sqrt(part))).sum(axis=1).mean())
        comp=float((R<=c.get("max_unfilled_lots",0)).mean())
        wait=float(((f*t).sum(axis=1)/Q + (R/Q)*60.0).mean())
        cands.append(dict(arm=f"{m}{hh}",method=m,h=hh,feasible=True,fee=fee*filled.mean()/Q,
                          impact_load=L,drift_bp=float(drift_bp.mean()),risk_bp=risk,completion=comp,
                          resid_mean=float(R.mean()),resid_p95=float(np.percentile(R,95)),
                          wait_min=float(wait),excluded=""))
    feas=[x for x in cands if x["feasible"] and x["completion"]>=c.get("min_completion_probability",0.95) and x["risk_bp"]<=c["risk_sd_budget_bp"]]
    for x in cands:
        if not x["feasible"]: continue
        if x["completion"]<c.get("min_completion_probability",0.95):
            x["excluded"]="completion below threshold"; x["feasible"]=False
        elif x["risk_bp"]>c["risk_sd_budget_bp"]:
            x["excluded"]="risk above budget"; x["feasible"]=False
    base=dict(execution_action="NONE",production_eligibility="INSUFFICIENT",mode=req.get("mode","baseline_first"),
        parent=p,scenario=dict(cap_frac=cap,fee_spread_bp=fee,phi=phi,
        terminal_price_change_bp=b,impact_a_bp=a_in),n_history_days=len(days),ref_avg_volume_lots=round(ref_avg,2),
        scope=RESEARCH_SCOPE,
        completion_note=("completion = share of the 20 prior path-days whose residual stays within max_unfilled_lots; "
                         "with max_unfilled_lots=0 and a 0.95 gate this means at least 19 of 20 paths end flat. It is "
                         "not a calibrated probability, and h is the planned release duration, not a completion "
                         "guarantee."),
        constraints=c,
        effective_constraints=dict(Dmax_trading_minutes=D,
            min_completion_probability=float(c.get("min_completion_probability",0.95)),
            max_unfilled_lots=int(c.get("max_unfilled_lots",0)),
            risk_sd_budget_bp=float(c["risk_sd_budget_bp"]),
            cap_frac=cap,fee_spread_bp=fee,impact_a_bp=a_in,phi=phi,terminal_price_change_bp=b,
            source="caller-supplied values; defaults filled only for optional fields",
            completion_rule="mean(residual <= max_unfilled_lots) >= min_completion_probability"),
        risk_model={"name":"empirical_sd_20_paths",
            "detail":"standard deviation of the per-path price term over the 20 rolling history paths available before the release",
            "n_paths":len(days)},
        candidates=[{k:(round(v,5) if isinstance(v,float) else v) for k,v in x.items()} for x in cands])
    if not feas:
        base.update(status="MODEL_INFEASIBLE",detail="no arm meets the completion and risk constraints",
            next_action="keep the parent order unchanged; first check the history window and the stated constraints. "
                        "This is a model statement, not a market statement. Changing D, size or any constraint is the "
                        "caller's business decision; the model does not relax them on its own")
        return base
    def cost(x,a): return x["fee"]+x["drift_bp"]+a*x["impact_load"]
    if a_in is not None and not isinstance(a_in,(list,tuple)):
        a=float(a_in); ranked=sorted(feas,key=lambda x:cost(x,a))
        best=ranked[0]; nxt=ranked[1] if len(ranked)>1 else None
        base.update(status="CONDITIONAL_MODEL_CHOICE",choice=best["arm"],method=best["method"],h=best["h"],
            predicted_cost_bp=round(cost(best,a),4),
            reason=(f"在满足完成≥{c.get('min_completion_probability',0.95):.2f}且价格风险≤{c['risk_sd_budget_bp']:.1f}bp 的候选里，"
                    f"{best['arm']} 的预计总成本最低（费用 {best['fee']:.2f} + 方向 {best['drift_bp']:.2f} + 冲击 {a*best['impact_load']:.2f} = {cost(best,a):.2f}bp）"
                    + (f"；次优 {nxt['arm']} 高 {cost(nxt,a)-cost(best,a):.2f}bp" if nxt else "")
                    + f"；预计完成率 {best['completion']:.3f}、残余均值 {best['resid_mean']:.1f} 手、等待负担 {best['wait_min']:.1f} 分钟。"))
        return base
    lo,hi=(0.0,math.inf) if a_in is None else (float(a_in[0]),float(a_in[1]))
    def feasible_at(a):
        return [x for x in feas if True]
    cand_arms=feas
    xs={lo}
    for i in range(len(cand_arms)):
        for j in range(i+1,len(cand_arms)):
            dL=cand_arms[i]["impact_load"]-cand_arms[j]["impact_load"]
            if abs(dL)>1e-12:
                x=(cand_arms[j]["fee"]+cand_arms[j]["drift_bp"]-cand_arms[i]["fee"]-cand_arms[i]["drift_bp"])/dL
                if lo<x<hi: xs.add(float(x))
    xs=sorted(xs); segs=[]
    for k in range(len(xs)):
        a0=xs[k]; a1=xs[k+1] if k+1<len(xs) else hi
        mid=(a0+a1)/2 if math.isfinite(a1) else a0+1.0
        best=min(cand_arms,key=lambda x:cost(x,mid))
        if segs and segs[-1]["arm"]==best["arm"]:
            segs[-1]["a_to"]=a1
        else:
            segs.append({"a_from":a0,"a_to":a1,"arm":best["arm"],"cost_at_from_bp":round(cost(best,a0),4)})
    for sg in segs:
        assert sg["a_from"]>=lo-1e-12 and (math.isinf(sg["a_to"]) or sg["a_to"]<=hi+1e-12), "segment outside requested range"
        if math.isinf(sg["a_to"]): sg["a_to"]="inf"
    base.update(status="CONDITIONAL_SET",detail="impact_a_bp not fixed; analytic lower envelope over the stated range",
                a_range=[lo,("inf" if math.isinf(hi) else hi)],segments=segs,
                needed_input="a non-negative impact_a_bp (number) to turn this into a single recommendation",
                next_action="supply impact_a_bp as a number, or keep the envelope as a conditional set")
    return base
def demote_choice(res):
    """Move a conditional pick out of the top-level reference field.

    A status that is not a recommendation must not carry a plausible final `choice`;
    the conditional arm stays visible as `conditional_choice`.
    """
    if res.get("choice") is not None:
        res.setdefault("conditional_choice",res["choice"])
        res["choice"]=None
    if "method" in res and "conditional_method" not in res:
        res["conditional_method"]=res.pop("method")
    if "h" in res and "conditional_h" not in res:
        res["conditional_h"]=res.pop("h")
    return res

def _apply_baseline(res,req):
    if res.get("status") in ("INPUT_REQUIRED","INSUFFICIENT_DATA"):
        return res
    if res.get("status")=="MODEL_INFEASIBLE":
        res["baseline_note"]="model infeasible under the current constraints; baseline cannot change today's feasibility"
        res["next_action"]=("keep the parent unchanged; no baseline can change today's model feasibility. Changing the order "
                            "or constraints is the caller's decision, not a model default")
        return res
    bc=req.get("baseline_context"); mode=req.get("mode","baseline_first")
    if mode=="compare":
        res["baseline_note"]="compare mode: model-only comparison, no baseline claim"
        return res
    if not isinstance(bc,dict):
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="baseline_context not supplied while mode=baseline_first")
        return res
    import csv as _csv
    p=req["parent"]; sc=req.get("scenario",{}); c=req["constraints"]
    need=["product","window","size_idx","risk_k"]
    miss=[x for x in need if x not in bc]
    if miss:
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason=f"missing baseline_context fields {miss}"); return res
    if str(bc.get("product"))!=str(p.get("symbol")):
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="baseline_context.product does not match parent.symbol"); return res
    rel=int(str(p["release_trading_date"]))
    if rel<=20260430:
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="release date is inside or before the training window; frozen A cannot be used as an ex-ante baseline"); return res
    a=sc.get("impact_a_bp",None)
    if a is None or isinstance(a,(list,tuple)):
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="impact_a_bp must be a fixed number (0, 0.5 or 2) to query a point baseline"); return res
    if float(sc.get("phi",1.0))!=1.0 or float(sc.get("cap_frac",0.1))!=0.1 or float(sc.get("fee_spread_bp",1.3))!=1.3 or float(sc.get("terminal_price_change_bp",0.0))!=0.0:
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="scenario differs from the frozen scope (phi=1, cap=0.1, fee=1.3, b=0)"); return res
    path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"data","frozen_A_params.csv")
    row=None
    for r in _csv.DictReader(open(path)):
        if (r["product"]==bc["product"] and r["window"]==bc["window"] and r["size_idx"]==str(bc["size_idx"])
            and float(r["risk_k"])==float(bc["risk_k"]) and r["D"]==str(p["Dmax_trading_minutes"])
            and r["side"]==p["side"] and float(r["a_bp"])==float(a)):
            row=r; break
    import math as _m
    avg=res.get("ref_avg_volume_lots")
    frac=[0.0025,0.01,0.04][int(bc["size_idx"])] if int(bc["size_idx"]) in (0,1,2) else None
    if frac is None or avg is None or not _m.isfinite(float(avg)):
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="cannot derive the reference order size for this size bucket"); return res
    q_expect=max(1,int(_m.floor(float(avg)*frac)))
    if abs(q_expect-int(p["qty_lots"]))>0:
        res["baseline_scope_note"]=f"qty_lots {p['qty_lots']} differs from the research size bucket ({q_expect}); baseline is out of scope, comparison indicative"
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="order size does not match the frozen research size bucket")
        return res
    if row is None:
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="no frozen A entry for this product/window/size/D/side/a/risk_k"); return res
    if row["status"]!="ok":
        demote_choice(res); res.update(status="INSUFFICIENT_BASELINE",baseline_reason="frozen A had no feasible arm on TRAIN for this cell"); return res
    if abs(float(row["budget_bp"])-float(c["risk_sd_budget_bp"]))>1e-9:
        res["baseline_constraint_note"]="risk budget differs from the frozen A budget; comparison is indicative only"
    res["baseline"]={"arm":row["method"]+row["h"],"method":row["method"],"h":int(row["h"]),
                     "trained_through":20260430,"frozen_budget_bp":float(row["budget_bp"]),
                     "frozen_cost_bp":float(row["frozen_cost_bp"])}
    if res["status"]=="" or res["status"] is None: return res
    cands=[x for x in res.get("candidates",[]) if x.get("feasible")]
    Aarm=row["method"]+row["h"]
    Aok=any(x["arm"]==Aarm for x in cands)
    best=res.get("choice")
    if res["status"] in ("CONDITIONAL_MODEL_CHOICE","CONDITIONAL_SET"):
        if Aok:
            Amet=next((x for x in cands if x["arm"]==Aarm),None)
            diff=None
            if Amet is not None and res.get("predicted_cost_bp") is not None and isinstance(req.get("scenario",{}).get("impact_a_bp"),(int,float)):
                a_=float(req["scenario"]["impact_a_bp"])
                cA=Amet["fee"]+Amet["drift_bp"]+a_*Amet["impact_load"]
                diff=round(float(res["predicted_cost_bp"])-cA,4)
            res["conditional_choice"]=best
            res["comparison_to_baseline"]={"conditional_choice":best,"cost_gap_vs_A_bp":diff,
                "difference_note":"B/C have not passed the adoption gates; the reference is frozen A, no automatic upgrade"}
            cA=round(float(Amet["fee"]+Amet["drift_bp"]+a_*Amet["impact_load"]),4) if (Amet is not None and isinstance(req.get("scenario",{}).get("impact_a_bp"),(int,float))) else None
            res["choice"]=Aarm; res["method"]=row["method"]; res["h"]=int(row["h"])
            if cA is not None:
                res["predicted_cost_bp"]=cA
            res["reason"]=(f"参考冻结基准 A：{Aarm}（训练截止 20260430 时在该单元选定）。"
                           f"预计成本 {cA if cA is not None else 'NA'}bp、完成率 {round(Amet['completion'],3) if Amet else 'NA'}、"
                           f"残余均值 {round(Amet['resid_mean'],1) if Amet else 'NA'} 手、等待负担 {round(Amet['wait_min'],1) if Amet else 'NA'} 分钟；"
                           f"当前条件最优 {best} 的差值见 comparison_to_baseline。B/C 尚未通过采用门槛，不自动升级。")
            res["status"]="REFERENCE_BASELINE"
        else:
            demote_choice(res)
            res["status"]="REVIEW_REQUIRED"
            res["baseline_reason"]="frozen A arm does not meet the current completion or risk constraints; a conditional alternative exists"
            res["next_action"]=("keep the parent unchanged; review the conditional alternative, and decide as the caller "
                                "whether any business constraint should change. The model does not relax constraints on its own")
    elif res["status"]=="MODEL_INFEASIBLE":
        res["baseline_reason"]="no candidate meets the current constraints; frozen A is not usable today either"
    return res
def apply_baseline(res,req):
    res=_apply_baseline(res,req)
    if res.get("status")=="INSUFFICIENT_BASELINE" and not res.get("next_action"):
        res["next_action"]=("keep the parent unchanged; supply a matching baseline_context, or use mode=compare for a "
                            "conditional comparison")
    return res

def _load_exante():
    import importlib.util as _iu
    here=os.path.dirname(os.path.abspath(__file__))
    spec=_iu.spec_from_file_location("exante_core",os.path.join(here,"exante_core.py"))
    core=_iu.module_from_spec(spec); spec.loader.exec_module(core)
    params=json.load(open(os.path.join(os.path.dirname(here),"data","exante_params.json")))
    return core,params

def _frozen_row(bc,p,a):
    import csv as _csv
    path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"data","frozen_A_params.csv")
    for r in _csv.DictReader(open(path)):
        if (r["product"]==bc.get("product") and r["window"]==bc.get("window")
            and r["size_idx"]==str(bc.get("size_idx")) and r["D"]==str(p["Dmax_trading_minutes"])
            and r["side"]==p["side"] and float(r["a_bp"])==float(a) and float(r["risk_k"])==float(bc.get("risk_k"))):
            return r
    return None

def _num(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(float(x))

def run_exante(req):
    """Pre-release scheduling rule. Research candidate only; never a release permission."""
    try:
        return _run_exante(req)
    except Exception as e:
        r=bad("INPUT_REQUIRED",f"the ex-ante rule could not run on this request ({type(e).__name__}); nothing was changed in the caller's request")
        r["mode"]="exante"; r["production_eligibility"]="INSUFFICIENT"; r["choice"]=None
        return r

def _run_exante(req):
    p=req.get("parent") or {}; c=req.get("constraints") or {}; sc=req.get("scenario",{}) or {}
    bc=req.get("baseline_context") or {}
    base={"execution_action":"NONE","mode":"exante","production_eligibility":"INSUFFICIENT",
          "rule_version":"exante-20261022"}
    core,params=_load_exante()
    products=list((params.get("meta") or {}).get("products") or [])
    windows=list((params.get("meta") or {}).get("windows") or [])
    # ---- structural validation -------------------------------------------------
    if p.get("side") not in ("buy","sell"): return bad("INPUT_REQUIRED","parent.side must be buy or sell")
    q=p.get("qty_lots")
    if not isinstance(q,int) or isinstance(q,bool) or q<=0: return bad("INPUT_REQUIRED","parent.qty_lots must be a positive integer")
    d=p.get("Dmax_trading_minutes")
    if not isinstance(d,int) or isinstance(d,bool) or not 1<=d<=60: return bad("INPUT_REQUIRED","parent.Dmax_trading_minutes must be an integer in [1,60]")
    rd=p.get("release_trading_date")
    if isinstance(rd,bool) or not isinstance(rd,(str,int)): return bad("INPUT_REQUIRED","parent.release_trading_date must be YYYYMMDD")
    rds=str(rd)
    if not (rds.isdigit() and len(rds)==8): return bad("INPUT_REQUIRED","parent.release_trading_date must be YYYYMMDD")
    import datetime as _dt
    try: _dt.datetime.strptime(rds,"%Y%m%d")
    except Exception:
        return bad("INPUT_REQUIRED",f"release_trading_date {rds} is not a real calendar date")
    rel=int(rds)
    if not isinstance(p.get("release_label"),str): return bad("INPUT_REQUIRED","parent.release_label required")
    if not isinstance(req.get("history_npz"),str) or not req["history_npz"].strip(): return bad("INPUT_REQUIRED","history_npz required")
    for k in ("product","window","size_idx","risk_k"):
        if k not in bc: return bad("INPUT_REQUIRED",f"baseline_context.{k} required for the ex-ante rule")
    prod=str(bc.get("product")); win=str(bc.get("window"))
    if products and prod not in products: return bad("INPUT_REQUIRED",f"product {prod!r} is outside the supported list {products}")
    if windows and win not in windows: return bad("INPUT_REQUIRED",f"window {win!r} is outside the supported list {windows}")
    sym=str(p.get("symbol",""))
    import re as _re
    m=_re.match(r"^([A-Za-z]+)",sym)
    sym_prod=m.group(1).upper() if m else ""
    if sym_prod!=prod.upper():
        return bad("INPUT_REQUIRED",f"parent.symbol {sym!r} resolves to product {sym_prod!r}, not {prod!r}; prefixes such as IF/I or PP/P are not interchangeable")
    size_req=bc.get("size_idx")
    if isinstance(size_req,bool) or not isinstance(size_req,int) or size_req not in (0,1,2):
        return bad("INPUT_REQUIRED","baseline_context.size_idx must be the integer 0, 1 or 2 (no rounding of fractional values)")
    if not _num(bc.get("risk_k")) or float(bc["risk_k"]) not in (1.0,2.0):
        return bad("INPUT_REQUIRED","baseline_context.risk_k must be 1.0 or 2.0")
    a=sc.get("impact_a_bp",None)
    if not _num(a) or float(a) not in (0.0,0.5,2.0):
        return bad("INPUT_REQUIRED","impact_a_bp must be a fixed number in {0, 0.5, 2} for the ex-ante rule")
    if float(sc.get("phi",1.0))!=1.0 or float(sc.get("cap_frac",0.1))!=0.1 or float(sc.get("fee_spread_bp",1.3))!=1.3:
        return bad("INPUT_REQUIRED","scenario must stay in the frozen scope (phi=1, cap_frac=0.1, fee_spread_bp=1.3)")
    # ---- constraints and direction must match the frozen research scope --------
    scope_viol=[]
    rb=c.get("risk_sd_budget_bp")
    if not _num(rb): scope_viol.append("constraints.risk_sd_budget_bp is required and must be a finite number")
    mcp=c.get("min_completion_probability",0.95)
    if not _num(mcp) or abs(float(mcp)-0.95)>1e-12:
        scope_viol.append("min_completion_probability must be 0.95 in this mode (the rule was fitted with that gate); it is not silently relaxed")
    mu=c.get("max_unfilled_lots",0)
    if not isinstance(mu,int) or isinstance(mu,bool) or mu!=0:
        scope_viol.append("max_unfilled_lots must be 0 in this mode; a non-zero residual allowance is outside the supported scope")
    b=sc.get("terminal_price_change_bp",0.0)
    if not _num(b) or abs(float(b))>1e-12:
        scope_viol.append("terminal_price_change_bp must be 0 in this mode; the rule does not consume an external direction forecast")
    if scope_viol:
        r=bad("INPUT_REQUIRED","; ".join(scope_viol)+" | the caller's business inputs were not modified; use mode=compare for other constraints/scenarios")
        r["mode"]="exante"; r["production_eligibility"]="INSUFFICIENT"; r["choice"]=None
        r["scope_violations"]=scope_viol
        r["next_action"]="use mode=compare for other constraints/scenarios; this mode does not rewrite the caller's business inputs"
        return r
    cfe={"IF","IC","T"}
    expected=None if (win=="night" and prod in cfe) else {
        "night":"21:15",
        "am":("09:45" if prod in cfe else "09:15"),
        "pm":("13:05" if prod in cfe else "13:35")}.get(win)
    if expected is None:
        return bad("INPUT_REQUIRED",f"product {prod!r} has no {win!r} session in the research scope")
    if p["release_label"]!=expected:
        return bad("INPUT_REQUIRED",f"release_label {p['release_label']!r} does not match product {prod!r} / window {win!r} (expected {expected!r}); the rule was fitted on fixed release labels")
    if rel<=20260430:
        return bad("INSUFFICIENT_EVIDENCE","release date is inside or before the training cutoff 20260430; the rule was fitted on that period")
    # ---- history and state -----------------------------------------------------
    hp=req["history_npz"]
    if not os.path.isabs(hp): hp=os.path.join(os.path.dirname(req.get("_path","")),hp)
    h,err=load_hist(hp,rel)
    if err: return bad("INSUFFICIENT_DATA",err)
    labels=[str(x) for x in h["labels"]]
    if p["release_label"] not in labels: return bad("INSUFFICIENT_DATA","release_label not present in the history grid")
    i0=labels.index(p["release_label"])
    dates=[str(x) for x in h["dates"]]
    if rds not in dates:
        return bad("INSUFFICIENT_DATA","the release day itself must be present in the history NPZ (with pre-release minutes valid) so that state can be computed")
    di_rel=dates.index(rds)
    ref=core.reference(h["volume"],h["close"],h["mask"],h["labels"],i0,di_rel)
    st=core.state(h["volume"],h["close"],h["mask"],h["labels"],i0,di_rel)
    if ref is None or st is None:
        return bad("INSUFFICIENT_DATA","cannot build the 20-day reference window or the pre-release state from this NPZ")
    if st["n_pre"] not in (5,15):
        return bad("INSUFFICIENT_DATA",f"pre-release window has {st['n_pre']} bars; this package supports only 5 or 15")
    z=core.z_pre(st["r_pre_bp"],ref["sigma1m"],st["steps"])
    vref=ref["V_ref"]
    tiers=(0.0025,0.01,0.04)
    q_expect=int(max(1,math.floor(tiers[size_req]*vref)))
    if q!=q_expect:
        return bad("INPUT_REQUIRED",f"qty_lots {q} does not match the declared size bucket {size_req} for this history (expected {q_expect}); the rule does not resize the order")
    size=size_req
    row=_frozen_row(bc,p,a)
    if row is None or not row.get("budget_bp"):
        return bad("INSUFFICIENT_BASELINE","no frozen risk budget for this product/window/size/D/side/a/risk_k")
    budget=float(row["budget_bp"])
    tol=5e-5+1e-9
    if abs(float(rb)-budget)>tol:
        r=bad("INPUT_REQUIRED",f"constraints.risk_sd_budget_bp={float(rb):.6g} differs from the frozen unit budget {budget:.6g} by more than the 5e-5bp storage tolerance; this mode neither replaces nor relaxes the caller's budget (use mode=compare)")
        r["mode"]="exante"; r["production_eligibility"]="INSUFFICIENT"; r["choice"]=None
        r["next_action"]="use mode=compare for other budgets; this mode does not replace or relax the caller's budget"
        return r
    # ---- rule ------------------------------------------------------------------
    pred=core.arm_stats(ref["V"],ref["cumw"],q,d,0.10)
    bucket=core.bucket_index(z,st["v_pre"],params["edges"])
    rho=float(params["buckets"][bucket]["rho"])
    cells={arm:params["sigma_cells"].get(f"{prod}|{win}|{size}|{d}|{arm}") for arm in pred}
    side=1 if p["side"]=="buy" else -1
    ch=core.choose(pred,ref["sigma1m"],rho,float(a),side,budget,float(bc["risk_k"]),cells,kappa=float(params["kappa_sigma"]))
    arm=ch.get("arm"); comps=(ch.get("costs") or {}).get(arm,{}) if arm else {}
    Aarm=(row["method"]+row["h"]) if row["status"]=="ok" else None
    feas=ch.get("feasible",[])
    A_feasible=bool(Aarm is not None and Aarm in feas)
    out=dict(base)
    out.update({
      "n_history_days":ref["n_days"],"ref_avg_volume_lots":round(vref,2),"size_idx":size,
      "constraints":c,"scenario":sc,
      "constraints_check":{"risk_budget_matches_frozen":True,"frozen_budget_bp":budget,
                           "min_completion_probability":float(mcp),"max_unfilled_lots":int(mu),
                           "terminal_price_change_bp":float(b),"tolerance_bp":tol},
      "effective_constraints":{"Dmax_trading_minutes":d,"risk_sd_budget_bp":budget,
                           "min_completion_probability":float(mcp),"max_unfilled_lots":int(mu),
                           "terminal_price_change_bp":float(b),"cap_frac":0.10,
                           "completion_rule":"mean(residual <= max_unfilled_lots) >= min_completion_probability",
                           "source":"frozen unit values this mode accepts; caller values that differ are rejected"},
      "risk_model":{"name":"train_cell_shrunk_sd",
                    "detail":"prior = frozen TRAIN ASAP SD x sqrt(predicted wait exposure T / T_asap); shrunk with the TRAIN cell SD at kappa",
                    "kappa":float(params["kappa_sigma"]),"n_days":ref["n_days"]},
      "state":{"bucket":int(bucket),"z_pre":round(z,4),"v_pre":round(st["v_pre"],4),
               "r_pre_bp":round(st["r_pre_bp"],4),"n_pre":int(st["n_pre"]),
               "bucket_edges":params["edges"],"rho_used":rho,
               "bucket_enabled":bool(params["buckets"][bucket].get("enabled"))},
      "research_candidate":{
         "status":ch["status"],"arm":arm,
         "method":(arm.rstrip("0123456789") if arm else None),
         "h":(int(arm[len(arm.rstrip("0123456789")):]) if arm else None),
         "predicted":{"fee_bp":comps.get("fee"),"impact_bp":comps.get("impact"),
                      "price_diff_vs_asap_bp":comps.get("price_diff_vs_asap"),
                      "total_bp":comps.get("total")},
         "predicted_risk_bp":ch.get("predicted_risk"),
         "predicted_completion":(pred[arm]["comp"] if arm else None),
         "predicted_residual_frac":(pred[arm]["resid_frac"] if arm else None),
         "predicted_exposure_minutes":(pred[arm]["T"] if arm else None),
         "predicted_impact_load":(pred[arm]["L"] if arm else None),
         "risk_by_arm":{k:core.predicted_risk(pred,k,budget,float(bc["risk_k"]),cells,kappa=float(params["kappa_sigma"]))
                        for k in pred},
         "feasible":feas,"excluded":ch.get("excluded",{})},
      "reference":{"arm":Aarm,"budget_bp":budget,"status":row["status"],"feasible_today":A_feasible},
      "evidence":params.get("evidence",{}),
      "capability":{
         "rule_runs":True,"rule_enabled":bool(params.get("evidence",{}).get("verdict")=="PASS"),
         "evidence_status":params.get("evidence",{}).get("verdict"),
         "can":["compute the pre-release state and the per-arm predictions from prior 20 days",
                "return the research candidate with its predicted cost decomposition, risk and completion",
                "return the frozen baseline arm for the same cell when it exists and is feasible under the current constraints"],
         "missing":["a state bucket that passes the TRAIN drift gate (all 9 were set to zero)",
                    "a cost improvement of at least 0.5bp against ASAP and against the frozen baseline",
                    "a risk profile within 1.10x of the comparator when the gain is present"],
         "why_not_usable":params.get("evidence",{}).get("reason"),
         "supported_scope":params.get("scope",{})},
    })
    if ch["status"]!="OK" or not feas:
        out["status"]="MODEL_INFEASIBLE"; out["choice"]=None
        out["reason"]=("在该单元的预测完成与风险约束下没有任何可行臂"
                       +("（含冻结基准 A）" if Aarm else "")+"；不给建议。")
    elif Aarm is None:
        out["status"]="INSUFFICIENT_BASELINE"; out["choice"]=None
        out["reason"]=("冻结表在该单元没有可用基准臂；事前规则的研究候选为 "+str(arm)
                       +"，证据未通过预注册标准，不能作为建议。")
    elif A_feasible:
        out["status"]="REFERENCE_BASELINE"; out["choice"]=Aarm
        out["reason"]=("冻结基准 A 为 "+Aarm+"，且在本请求的预测完成与风险约束下可行；事前规则的研究候选为 "
                       +str(arm)+"，证据未通过预注册标准，不自动升级。")
    else:
        out["status"]="REVIEW_REQUIRED"; out["choice"]=None
        out["reason"]=("冻结基准 A（"+Aarm+"）在本请求的预测约束下不可行；规则另有可行候选 "+str(arm)
                       +"，但其证据未通过预注册标准，不能自动采用，需人工复核。")
    return out

def _volume_params():
    path=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"data","params_volume.json")
    with open(path) as fh:
        return json.load(fh)

def _vf_base():
    return {"execution_action":"NONE","mode":"volume_forecast","production_eligibility":"INSUFFICIENT",
            "choice":None}

def _vf_error(status, detail):
    r=_vf_base(); r["status"]=status; r["detail"]=detail
    return r

def run_volume_forecast(req):
    """Forecast-only mode: returns the activity-scaled 60-minute volume forecast."""
    try:
        return _run_volume_forecast(req)
    except Exception as e:
        return _vf_error("INPUT_REQUIRED",f"volume forecast could not run ({type(e).__name__}); inputs were not modified")

def _expected_label(prod,win):
    cfe={"IF","IC","T"}
    if win=="night":
        return None if prod in cfe else "21:15"
    if prod in cfe:
        return {"am":"09:45","pm":"13:05"}.get(win)
    return {"am":"09:15","pm":"13:35"}.get(win)

def _run_volume_forecast(req):
    vp=_volume_params()
    products=list(vp["scope"]["products"]); windows=list(vp["scope"]["windows"])
    p=req.get("parent") or {}; bc=req.get("baseline_context") or {}; sc=req.get("scenario",{}) or {}
    if p.get("side") not in ("buy","sell"): return _vf_error("INPUT_REQUIRED","parent.side must be buy or sell")
    q=p.get("qty_lots")
    if not isinstance(q,int) or isinstance(q,bool) or q<=0: return _vf_error("INPUT_REQUIRED","parent.qty_lots must be a positive integer")
    d=p.get("Dmax_trading_minutes")
    if not isinstance(d,int) or isinstance(d,bool) or not 1<=d<=60: return _vf_error("INPUT_REQUIRED","parent.Dmax_trading_minutes must be an integer in [1,60]")
    rd=p.get("release_trading_date")
    if isinstance(rd,bool) or not isinstance(rd,(str,int)): return _vf_error("INPUT_REQUIRED","parent.release_trading_date must be YYYYMMDD")
    rds=str(rd)
    if not (rds.isdigit() and len(rds)==8): return _vf_error("INPUT_REQUIRED","parent.release_trading_date must be YYYYMMDD")
    import datetime as _dt
    try: _dt.datetime.strptime(rds,"%Y%m%d")
    except Exception: return _vf_error("INPUT_REQUIRED",f"release_trading_date {rds} is not a real calendar date")
    if not isinstance(p.get("release_label"),str): return _vf_error("INPUT_REQUIRED","parent.release_label required")
    if not isinstance(req.get("history_npz"),str) or not req["history_npz"].strip(): return _vf_error("INPUT_REQUIRED","history_npz required")
    for k in ("product","window"):
        if k not in bc: return _vf_error("INPUT_REQUIRED",f"baseline_context.{k} required to validate the product/window and the fixed release label")
    prod=str(bc.get("product")); win=str(bc.get("window"))
    if products and prod not in products: return _vf_error("INPUT_REQUIRED",f"product {prod!r} is outside the supported list {products}")
    if windows and win not in windows: return _vf_error("INPUT_REQUIRED",f"window {win!r} is outside the supported list {windows}")
    import re as _re
    m=_re.match(r"^([A-Za-z]+)",str(p.get("symbol","")))
    sym_prod=m.group(1).upper() if m else ""
    if sym_prod!=prod.upper():
        return _vf_error("INPUT_REQUIRED",f"parent.symbol {p.get('symbol')!r} resolves to product {sym_prod!r}, not {prod!r}; prefixes such as IF/I or PP/P are not interchangeable")
    exp=_expected_label(prod,win)
    if exp is None: return _vf_error("INPUT_REQUIRED",f"product {prod!r} has no {win!r} session in the supported scope")
    if p["release_label"]!=exp:
        return _vf_error("INPUT_REQUIRED",f"release_label {p['release_label']!r} does not match product {prod!r} / window {win!r} (expected {exp!r})")
    rel=int(rds)
    if rel<=20260430:
        return _vf_error("INSUFFICIENT_EVIDENCE","release date is inside or before the training cutoff 20260430; the fitted beta cannot be presented as an ex-ante forecast for this date")
    hp=req["history_npz"]
    if not os.path.isabs(hp): hp=os.path.join(os.path.dirname(req.get("_path","")),hp)
    h,err=load_hist(hp,rel)
    if err: return _vf_error("INSUFFICIENT_DATA",err)
    labels=[str(x) for x in h["labels"]]
    if p["release_label"] not in labels: return _vf_error("INSUFFICIENT_DATA","release_label not present in the history grid")
    i0=labels.index(p["release_label"])
    dates=[str(x) for x in h["dates"]]
    if rds not in dates:
        return _vf_error("INSUFFICIENT_DATA","the release day itself must be present in the history NPZ (with pre-release minutes valid)")
    di=dates.index(rds)
    core,_=_load_exante()
    act,why=core.prefix_activity(h["volume"],h["close"],h["mask"],h["labels"],i0,di)
    if act is None:
        return _vf_error("INSUFFICIENT_DATA",f"pre-release activity cannot be computed: {why}; no fallback value is substituted")
    expected_npre={"am":15,"pm":5,"night":15}[win]
    if act["n_pre"]!=expected_npre:
        return _vf_error("INSUFFICIENT_DATA",f"pre-release prefix has {act['n_pre']} bars but window {win!r} uses {expected_npre}")
    ref=core.reference(h["volume"],h["close"],h["mask"],h["labels"],i0,di)
    if ref is None:
        return _vf_error("INSUFFICIENT_DATA","cannot build the 20-day 60-minute reference window from this NPZ")
    beta=float(vp["beta"]); r=float(act["act_pre"])
    s=max(0.0,1.0+beta*(r-1.0))
    b60=float(ref["V_ref"]); pred60=b60*s
    curve=ref["V"].mean(axis=0)*s
    cap=float(sc.get("cap_frac",0.10))
    hint=None; hint_note=None; comp_hint=None
    if abs(cap-0.10)>1e-12:
        hint_note=f"the completion hint is only defined for cap_frac=0.10; no hint is produced for the requested cap_frac={cap}"
    else:
        capv=np.floor(0.10*ref["V"][:,:d]*s).astype(np.int64)
        tgt=core.schedule("asap",0,q,ref["cumw"],d)
        fills=core.simulate(tgt,capv)
        comp_hint=float((fills.sum(axis=1)>=q).mean())
        hint_note="fraction of the 20 historical paths where an ASAP schedule scaled by s would fill Q within D under the 10% capacity model; a non-binding hint, not a rejection rule"
    out=_vf_base()
    out.update({
      "status":"FORECAST_ONLY",
      "product":prod,"window":win,"symbol":p.get("symbol"),
      "release_date":rds,"release_label":p["release_label"],
      "Q":q,"D":d,
      "forecast":{
        "act_pre":round(r,6),"n_pre":int(act["n_pre"]),"beta":beta,"scale":round(s,6),
        "B60":round(b60,2),"pred60":round(pred60,2),
        "forecast_horizon_trading_minutes":60,
        "curve_head":[round(float(x),2) for x in curve[:5]],
        "cum5":round(float(curve[:5].sum()),2),"cum15":round(float(curve[:15].sum()),2),"cum30":round(float(curve[:30].sum()),2),
        "hint_horizon_trading_minutes":d,
        "model_completion_hint":comp_hint,
        "hint_capacity_frac":0.10,
        "hint_note":hint_note},
      "evidence":{"stage1":{"gate":vp["stage1"]["gate"],"val":vp["stage1"]["val"],"test":vp["stage1"]["test"]},
                  "stage2":vp["stage2"],"source":"references/volume-evidence.md"},
      "constraints_used_for_execution_admission":False,
      "constraints_echo":{"values":req.get("constraints"),
                           "used_for_execution_admission":False,
                           "note":"this mode makes no execution decision; constraints are echoed only"},
      "notes":["forecast only: no method/deadline selection, no rejection, no trading",
               "the 60-minute total forecast and the D-minute completion hint are separate; the hint horizon equals parent.Dmax_trading_minutes",
               "if scale < 1 the predicted volume and capacity are below the 20-day average; treat the completion hint as a warning, not a stop"]})
    return out

def run_exante_activity(req):
    vp=_volume_params()
    return {"status":"INSUFFICIENT_EVIDENCE","execution_action":"NONE","mode":"exante_activity",
            "choice":None,"production_eligibility":"INSUFFICIENT",
            "reason":"the activity-adjusted selection (ACT) did not pass the pre-registered execution gate: coverage 96.76%/96.80% of the fixed E, full-E completion -2.95/-2.84pp, cost difference vs ASAP only -0.0048/-0.0064bp and slightly worse than unadjusted VOL20",
            "stage2":vp["stage2"],
            "next_action":"use mode=baseline_first for the frozen baseline, or mode=volume_forecast for a forecast-only estimate; this mode does not select a schedule"}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--request",required=True); ap.add_argument("--out",required=True)
    ns=ap.parse_args()
    try: req=json.load(open(ns.request))
    except Exception as e:
        json.dump(bad("INPUT_REQUIRED",f"cannot read request: {type(e).__name__}"),open(ns.out,"w"),ensure_ascii=False,indent=1); return
    req["_path"]=os.path.abspath(ns.request)
    if req.get("mode")=="exante":
        res=sanitize(run_exante(req))
    elif req.get("mode")=="volume_forecast":
        res=sanitize(run_volume_forecast(req))
    elif req.get("mode")=="exante_activity":
        res=sanitize(run_exante_activity(req))
    else:
        res=sanitize(apply_baseline(run(req),req))
    res.setdefault("mode",req.get("mode","baseline_first"))
    res.setdefault("production_eligibility","INSUFFICIENT")
    res.setdefault("scope",RESEARCH_SCOPE)
    res.setdefault("execution_action","NONE")
    json.dump(res,open(ns.out,"w"),ensure_ascii=False,indent=1)
    print(json.dumps({"status":res["status"],"execution_action":"NONE","choice":res.get("choice","")},ensure_ascii=False))
if __name__=="__main__": main()
