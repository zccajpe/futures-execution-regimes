"""Stage-1 fixes: per-product attribution, same-h pairs with CIs, (a) envelope."""
import csv, json, os, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results"); OUT = os.path.join(ROOT, "decision_research")
P = ["RB","I","CU","AU","SC","TA","RU","M","P","IF","IC","T"]; W=["am","pm","night"]; M=["asap","twap","shape"]
TRAIN=(20260105,20260430); VAL=(20260506,20260630); TEST=(20260701,20261020)
FEE, CAP, MINCOMP = 1.3, 1, 0.95
def load(p):
    with np.load(p, allow_pickle=False) as z: return {k: np.array(z[k], copy=True) for k in z.files}
a=load(os.path.join(RES,"replay_train_val_t60.npz")); b=load(os.path.join(RES,"replay_test_t60.npz"))
R={k: np.concatenate([a[k],b[k]]) for k in a}
k=R["keys"]; N=len(k)
def split_of(d):
    if TRAIN[0]<=d<=TRAIN[1]: return "train"
    if VAL[0]<=d<=VAL[1]: return "val"
    return "test"
SPL=np.array([split_of(int(d)) for d in k[:,2]])
keep=k[:,7]==CAP
ARMS=[("asap",0)]+[(m,h) for m in ("twap","shape") for h in (5,10,20,30,60)]
def cell_arrays(pi,wi,size,D,side,split,cands):
    out={}
    for m,h in cands:
        sel=keep&(k[:,0]==pi)&(k[:,1]==wi)&(k[:,3]==size)&(k[:,4]==D)&(k[:,5]==M.index(m))&(k[:,6]==h)&(SPL==split)
        I=np.nonzero(sel)[0]
        if len(I)<30: continue
        price=side*(R["gap_exec"][I]+R["gap_resid_T60"][I]); fee=FEE*R["fee_load"][I]
        out[(m,h)]={"I":I,"A":float((price+fee).mean()),"L1":float(R["L1"][I].mean()),"L05":float(R["L05"][I].mean()),
                    "comp":float(np.mean(R["resid"][I]==0)),"sd":float(np.std(price,ddof=1)),"n":int(len(I)),
                    "nd":int(len(np.unique(k[I,2])))}
    return out
# 1) per-product attribution
rows=[]
for pi in range(12):
    for wi in range(3):
        for split in ("train","val","test"):
            for side in (1.0,-1.0):
                for size in range(3):
                    for D in (5,15,30,60):
                        got=cell_arrays(pi,wi,size,D,side,split,ARMS)
                        for (m,h),v in got.items():
                            I=v["I"]; price=side*(R["gap_exec"][I]+R["gap_resid_T60"][I]); fee=FEE*R["fee_load"][I]
                            rows.append([P[pi],W[wi],split,"buy" if side>0 else "sell",size,D,m,h,v["n"],v["nd"],
                                         round(float(price.mean()),5),round(float(fee.mean()),5),
                                         round(float((0.5*R["L1"][I]).mean()),5),
                                         round(float((price+fee+0.5*R["L1"][I]).mean()),5),
                                         round(v["comp"],4),round(float((R["resid"][I]/R["Q"][I]).mean()),5),
                                         round(float(np.percentile(R["resid"][I]/R["Q"][I],95)),5),
                                         round(v["sd"],5)])
csv.writer(open(os.path.join(OUT,"attribution_by_product.csv"),"w",newline="")).writerows(
 [["product","window","split","side","size_idx","D","method","h","n","n_dates","price_cost_bp","fee_spread_bp","impact_bp_a0.5","total_bp","completion","resid_mean","resid_p95","price_sd_bp"]]+rows)
# 2) same-h TWAP vs SHAPE pairs with block CI at a=0.5/phi=1
def block_ci(delta, dates, reps=2000, seed=3):
    u=np.unique(dates); blk={int(d):i//5 for i,d in enumerate(u)}
    bi=np.array([blk[int(d)] for d in dates]); nb=int(bi.max())+1
    s=np.bincount(bi,weights=delta,minlength=nb); c=np.bincount(bi,minlength=nb)
    rng=np.random.default_rng(seed); out=[]
    for _ in range(reps):
        p=rng.integers(0,nb,nb); out.append(s[p].sum()/c[p].sum())
    return float(delta.mean()), float(np.percentile(out,2.5)), float(np.percentile(out,97.5)), len(u), nb
prows=[]
for wi in range(3):
  for size in (1,):
    for D in (60,):
      for split in ("train","val","test"):
        for h in (5,10,20,30,60):
            if h>D: continue
            A=np.nonzero(keep&(k[:,1]==wi)&(k[:,3]==size)&(k[:,4]==D)&(k[:,5]==1)&(k[:,6]==h)&(SPL==split))[0]
            B=np.nonzero(keep&(k[:,1]==wi)&(k[:,3]==size)&(k[:,4]==D)&(k[:,5]==2)&(k[:,6]==h)&(SPL==split))[0]
            if len(A)<50 or len(B)<50: continue
            key=lambda I:(k[I,0].astype(np.int64)*100+k[I,1])*100000000+k[I,2]
            common,ia,ib=np.intersect1d(key(A),key(B),return_indices=True)
            if len(common)<50: continue
            i1,i2=A[ia],B[ib]
            c1=1.3*R["fee_load"][i1]+0.5*R["L1"][i1]+R["gap_exec"][i1]+R["gap_resid_T60"][i1]
            c2=1.3*R["fee_load"][i2]+0.5*R["L1"][i2]+R["gap_exec"][i2]+R["gap_resid_T60"][i2]
            mean,lo,hi,nd,nb=block_ci(c2-c1,k[i1,2])
            prows.append([split,W[wi],size,D,h,len(common),nd,nb,round(mean,5),round(lo,5),round(hi,5)])
csv.writer(open(os.path.join(OUT,"same_h_pairs.csv"),"w",newline="")).writerows(
 [["split","window","size_idx","D","h","n_pairs","n_dates","n_blocks","shape_minus_twap_bp_a0.5","ci_low","ci_high"]]+prows)
# 3) envelope per cell (buy, size 1, D60): feasible set under completion and risk budget from train
env=[]; regions=[]
for pi in range(12):
  for wi in range(3):
    tr=cell_arrays(pi,wi,1,60,1.0,"train",ARMS)
    if ("asap",0) not in tr: continue
    sd_tr=tr[("asap",0)]["sd"]
    for split in ("train","val","test"):
        got=cell_arrays(pi,wi,1,60,1.0,split,ARMS)
        if len(got)<2: continue
        for kk in (1,2):
            feas={mh:v for mh,v in got.items() if v["comp"]>=MINCOMP and v["sd"]<=kk*sd_tr}
            if not feas:
                env.append([P[pi],W[wi],split,kk,0,"","no_feasible","",""]); continue
            names=list(feas)
            pts=sorted([(feas[nm]["A"],feas[nm]["L1"],nm) for nm in names])
            # envelope at declared a points
            for a_ in (0.0,0.05,0.5,2.0):
                best=min(names,key=lambda nm: feas[nm]["A"]+a_*feas[nm]["L1"])
                env.append([P[pi],W[wi],split,kk,a_,best,round(feas[best]["A"]+a_*feas[best]["L1"],5),
                            round(feas[best]["sd"],4),feas[best]["nd"]])
            # a-intervals where each candidate is argmin
            xs={0.0}
            for i in range(len(pts)):
                for j in range(i+1,len(pts)):
                    dL=pts[i][1]-pts[j][1]
                    if abs(dL)>1e-12:
                        x=(pts[j][0]-pts[i][0])/dL
                        if x>0: xs.add(x)
            xs=sorted(xs)
            bounds=list(xs)+[float("inf")]
            for idx_ in range(len(xs)):
                mid=(bounds[idx_]+bounds[idx_+1])/2 if np.isfinite(bounds[idx_+1]) else bounds[idx_]+1.0
                best=min(names,key=lambda nm: feas[nm]["A"]+mid*feas[nm]["L1"])
                regions.append([P[pi],W[wi],split,kk,round(bounds[idx_],5),
                                (round(bounds[idx_+1],5) if np.isfinite(bounds[idx_+1]) else "inf"),best])
csv.writer(open(os.path.join(OUT,"envelope.csv"),"w",newline="")).writerows(
 [["product","window","split","risk_k","a_bp","best","cost_bp","price_sd","n_dates"]]+env)
csv.writer(open(os.path.join(OUT,"envelope_regions.csv"),"w",newline="")).writerows(
 [["product","window","split","risk_k","a_from","a_to","best"]]+regions)
print({"attribution_rows":len(rows),"same_h_rows":len(prows),"envelope_rows":len(env),"regions":len(regions)})
