"""Stage 2 v2: corrected predictions (bp, T=60), side-specific A, full coverage, 3a x 2k."""
import csv, json, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import engine as E
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); RES=os.path.join(ROOT,"results"); OUT=os.path.join(ROOT,"decision_research","v2")
P=["RB","I","CU","AU","SC","TA","RU","M","P","IF","IC","T"]; W=["am","pm","night"]; M=["asap","twap","shape"]
TRAIN=(20260105,20260430); VAL=(20260506,20260630); TEST=(20260701,20261020)
CAP=0.10; FEE=1.3; A_LIST=(0.0,0.5,2.0); K_LIST=(1.0,2.0); MINCOMP=0.95; KP=20; HOR=60
ARMS=[("asap",0)]+[(m,h) for m in ("twap","shape") for h in (5,10,20,30,60)]
def load(p):
    with np.load(p,allow_pickle=False) as z: return {k:np.array(z[k],copy=True) for k in z.files}
a=load(os.path.join(RES,"replay_train_val_t60.npz")); b=load(os.path.join(RES,"replay_test_t60.npz"))
R={k:np.concatenate([a[k],b[k]]) for k in a}; k=R["keys"]
def split_of(d):
    if TRAIN[0]<=d<=TRAIN[1]: return "train"
    if VAL[0]<=d<=VAL[1]: return "val"
    if TEST[0]<=d<=TEST[1]: return "test"
    return "warmup"
SPL=np.array([split_of(int(d)) for d in k[:,2]])
assert not ((SPL=="test") & ((k[:,2]<TEST[0])|(k[:,2]>TEST[1]))).any()
IDX={}
for i in range(len(k)):
    if int(k[i,7])==1: IDX[(int(k[i,0]),int(k[i,1]),int(k[i,2]),int(k[i,3]),int(k[i,4]),int(k[i,5]),int(k[i,6]))]=i
def realised(pi,wi,date,size,D,m,h,side,a):
    i=IDX.get((pi,wi,date,size,D,M.index(m),h))
    if i is None: return None
    pr=side*(R["gap_exec"][i]+R["gap_resid_T60"][i]); fe=FEE*R["fee_load"][i]
    im=a*(R["L1"][i] if a is not None else R["L1"][i])
    return dict(price=float(pr),fee=float(fe),imp=float(a*R["L1"][i]),total=float(pr+fe+a*R["L1"][i]),
                comp=bool(R["resid"][i]==0),rq=float(R["resid"][i]/R["Q"][i]),
                wall=float(R["wall_min"][i]) if np.isfinite(R["wall_min"][i]) else None,
                mo15=float(side*R["mo15"][i]) if np.isfinite(R["mo15"][i]) else None)
# ---- A: frozen per (pi,wi,size,D,side,a,k) on TRAIN, budget = TRAIN ASAP sd * k ----
AGG={}
def build_train_agg():
    selT=(k[:,7]==1)&(SPL=="train")
    keys=np.stack([k[:,0],k[:,1],k[:,3],k[:,4],k[:,5],k[:,6]],axis=1)
    idxT=np.nonzero(selT)[0]
    uniq,inv=np.unique(keys[idxT],axis=0,return_inverse=True)
    pr=R["gap_exec"][idxT]+R["gap_resid_T60"][idxT]
    fe=R["fee_load"][idxT]; l1=R["L1"][idxT]; cp=(R["resid"][idxT]==0).astype(float)
    n=np.bincount(inv,minlength=len(uniq))
    sp=np.bincount(inv,weights=pr,minlength=len(uniq)); sp2=np.bincount(inv,weights=pr**2,minlength=len(uniq))
    AGG.clear()
    for g in range(len(uniq)):
        if n[g]<30: continue
        m=sp[g]/n[g]; var=max(sp2[g]/n[g]-m*m,0.0)*n[g]/max(n[g]-1,1)
        AGG[(int(uniq[g,0]),int(uniq[g,1]),int(uniq[g,2]),int(uniq[g,3]),int(uniq[g,4]),int(uniq[g,5]))]=dict(
            n=int(n[g]),price=m,fee=fe[inv==g].mean(),l1=l1[inv==g].mean(),
            comp=cp[inv==g].mean(),sd=float(np.sqrt(var)))
build_train_agg()
def cost_of(g,side,a):
    return side*g["price"]+FEE*g["fee"]+a*g["l1"]
# ---- A: frozen per (pi,wi,size,D,side,a,k) on TRAIN, budget = TRAIN ASAP sd * k ----
def train_stats(pi,wi,size,D,side,a):
    out={}
    for m,h in ARMS:
        if h>D: continue
        ids=[i for i in range(len(k)) if False]
        sel=(k[:,7]==1)&(k[:,0]==pi)&(k[:,1]==wi)&(k[:,3]==size)&(k[:,4]==D)&(k[:,5]==M.index(m))&(k[:,6]==h)&(SPL=="train")
        I=np.nonzero(sel)[0]
        if len(I)<30: continue
        pr=side*(R["gap_exec"][I]+R["gap_resid_T60"][I]); cost=pr+FEE*R["fee_load"][I]+a*R["L1"][I]
        out[(m,h)]=dict(cost=float(cost.mean()),comp=float(np.mean(R["resid"][I]==0)),
                        sd=float(np.std(pr,ddof=1)),n=int(len(I)))
    return out
A_FROZEN={}; ASAP_SD={}; FROZEN_ROWS=[]
for (pi,wi,size,D,mi,hh),g in AGG.items():
    if mi!=0 or hh!=0: continue
    for side in (1.0,-1.0): ASAP_SD[(pi,wi,size,D,side)]=g["sd"]
for pi in range(12):
  for wi in range(3):
    for size in range(3):
      for D in (5,15,30,60):
        for side in (1.0,-1.0):
          sd=ASAP_SD.get((pi,wi,size,D,side))
          if sd is None: continue
          for a_ in A_LIST:
            for kk in K_LIST:
              bud=kk*sd; best=None; feas_n=0
              for hh in (0,5,10,20,30,60):
                if hh>D: continue
                for mi in range(3):
                    if mi==0 and hh!=0: continue
                    if mi>0 and hh==0: continue
                    g=AGG.get((pi,wi,size,D,mi,hh))
                    if not g or g["comp"]<MINCOMP or g["sd"]>bud: continue
                    feas_n+=1; c=cost_of(g,side,a_)
                    if best is None or c<best[0]: best=(c,(M[mi],hh))
              A_FROZEN[(pi,wi,size,D,side,a_,kk)]=best[1] if best else None
              FROZEN_ROWS.append([P[pi],W[wi],size,D,"buy" if side>0 else "sell",a_,kk,
                                  best[1][0] if best else "",best[1][1] if best else "",
                                  round(best[0],5) if best else "",round(bud,4),feas_n,
                                  "ok" if best else "no_feasible"])
csv.writer(open(os.path.join(OUT,"frozen_A_params.csv"),"w",newline="")).writerows(
 [["product","window","size_idx","D","side","a_bp","risk_k","method","h","frozen_cost_bp","budget_bp","n_feasible_arms","status"]]+FROZEN_ROWS)
# ---- prediction (B/C) ----
def ref_paths(series,di,i0,K=KP):
    sl=slice(i0+1,i0+1+HOR); valid=series.mask[:,sl].all(axis=1)&series.mask[:,i0]
    hist=[j for j in range(di) if valid[j]][-K:]
    if len(hist)<5: return None
    V=series.volume[hist][:,sl]; C=series.close[hist][:,sl]; P0=series.close[hist,i0]
    if (P0<=0).any(): return None
    Rh=C/P0[:,None]; return V,Rh
def forecast(V,Rh,Q,cumw,D,scale=1.0):
    Vsc=np.maximum(V*scale,0.0); capv=np.floor(CAP*Vsc).astype(np.int64); out={}
    for m,h in ARMS:
        if h>D: continue
        tgt=E.int_targets(m,h,int(Q),cumw,D)
        cc=np.cumsum(capv[:,:D],axis=1)
        slack=np.minimum(0,np.minimum.accumulate(tgt[None,:]-cc,axis=1))
        cum=np.maximum.accumulate(np.clip(cc+slack,0,Q),axis=1)
        f=np.diff(np.concatenate([np.zeros((len(V),1)),cum],axis=1),axis=1)
        filled=f.sum(1); resid=Q-filled
        gap_bp=1e4*((f*(Rh[:,:D]-1)).sum(1)/Q + resid/Q*(Rh[:,59]-1))
        fee=FEE*filled/Q
        part=f/(0.01*np.maximum(Vsc[:,:D],1e-12))
        L=(f/Q*part).sum(1)
        out[(m,h)]=(float(fee.mean()),float(L.mean()),float((filled>=Q).mean()),
                    float(np.std(gap_bp,ddof=1)) if len(gap_bp)>1 else 0.0)
    return out
def act_vol(series,di,i0):
    seg=series.seg; start=int(np.argmax(seg==seg[i0]))
    hist=[j for j in range(max(0,di-20),di) if series.mask[j,start:i0+1].all()]
    if not hist: return None,None
    cur=float(series.volume[di,start:i0+1].sum()); base=float(np.mean([series.volume[j,start:i0+1].sum() for j in hist]))
    act=cur/base if base>0 else 1.0
    def rv(j):
        p=series.close[j,start:i0+1]
        if (p<=0).any(): return np.nan
        r=np.diff(np.log(np.maximum(p,1e-12)))
        return float(np.std(r))
    cur_rv=rv(di); base_rv=float(np.nanmean([rv(j) for j in hist]))
    return act, (cur_rv/base_rv if base_rv and np.isfinite(base_rv) and base_rv>0 else 1.0)
agg={}; decisions=[]
QT={}
for pi in range(12):
  for wi in range(3):
    series=E.load_series(P[pi],W[wi])
    if series is None or E.release_label(P[pi],W[wi]) not in series.labels: continue
    i0=series.labels.index(E.release_label(P[pi],W[wi]))
    if i0+1+HOR>len(series.labels): continue
    for di,date in enumerate(series.dates):
        sp=split_of(int(date))
        if sp not in ("val","test") or di<21: continue
        st=E.reference_stats(series,di,i0); pa=ref_paths(series,di,i0)
        if st is None or pa is None: continue
        V,Rh=pa; act,volr=act_vol(series,di,i0)
        scale=float(np.clip(act,0.7,1.3)) if act else 1.0
        rmult=float(np.clip(volr,0.7,1.3)) if volr else 1.0
        for size in range(3):
            Q=int(max(1,np.floor(E.SIZE_TIERS[size]*st["v_avg"])))
            for D in (5,15,30,60):
                cB=forecast(V,Rh,Q,st["cumw"],D,1.0)
                cC=forecast(V,Rh,Q,st["cumw"],D,scale)
                for a_ in A_LIST:
                    fB={mh:(v[0]+a_*v[1],v[2],v[3]) for mh,v in cB.items()}
                    fC={mh:(v[0]+a_*v[1],v[2],v[3]*rmult) for mh,v in cC.items()}
                    for side in (1.0,-1.0):
                        sd=ASAP_SD.get((pi,wi,size,D,side))
                        if sd is None: continue
                        for kk in K_LIST:
                            bud=kk*sd
                            def pick(f):
                                feas={mh:v for mh,v in f.items() if v[1]>=MINCOMP and v[2]<=bud}
                                return (min(feas,key=lambda mh:feas[mh][0]) if feas else None)
                            armA=A_FROZEN.get((pi,wi,size,D,side,a_,kk))
                            armB=pick(fB); armC=pick(fC)
                            for rule,arm,f in (("A",armA,fB),("B",armB,fB),("C",armC,fC)):
                                status="ok" if arm else "no_feasible"
                                real=realised(pi,wi,int(date),size,D,arm[0],arm[1],side,a_) if arm else None
                                key=(sp,pi,wi,int(date),size,D,side,a_,kk,rule)
                                agg.setdefault(key,[]).append((real["total"] if real else np.nan,
                                                              1 if real and real["comp"] else 0,
                                                              real["price"] if real else np.nan,
                                                              real["rq"] if real else np.nan,
                                                              real["wall"] if real and real["wall"] is not None else np.nan))
                                if (a_,kk)==(0.5,1.0):
                                    decisions.append([sp,P[pi],W[wi],int(date),size,D,"buy" if side>0 else "sell",
                                                      a_,kk,rule,arm[0] if arm else "",arm[1] if arm else "",status,Q,round(bud,4),
                                                      round(f.get(arm,("","",""))[1],4) if arm and arm in f else "",
                                                      round(f.get(arm,("","",""))[2],4) if arm and arm in f else "",
                                                      round(real["total"],4) if real else "",1 if real and real["comp"] else 0,
                                                      round(real["rq"],5) if real else ""])
rows=[]
for key,v in agg.items():
    sp,pi,wi,date,size,D,side,a_,kk,rule=key
    arr=np.array(v,dtype=float)
    rows.append([sp,P[pi],W[wi],date,size,D,"buy" if side>0 else "sell",a_,kk,rule,len(arr),
                 int(np.isfinite(arr[:,0]).sum()),round(float(np.nanmean(arr[:,0])),4) if np.isfinite(arr[:,0]).any() else "",
                 round(float(np.nanmean(arr[:,1])),4),round(float(np.nanmean(arr[:,2])),4) if np.isfinite(arr[:,2]).any() else "",
                 round(float(np.nanmean(arr[:,3])),5) if np.isfinite(arr[:,3]).any() else "",
                 round(float(np.nanmean(arr[:,4])),2) if np.isfinite(arr[:,4]).any() else ""])
csv.writer(open(os.path.join(OUT,"stage2v2_cells.csv"),"w",newline="")).writerows(
 [["split","product","window","date","size_idx","D","side","a_bp","risk_k","rule","n_orders","n_matched","mean_total_bp","completion","mean_price_bp","mean_rq","mean_wall"]]+rows)
csv.writer(open(os.path.join(OUT,"stage2v2_decisions_main.csv"),"w",newline="")).writerows(
 [["split","product","window","date","size_idx","D","side","a_bp","risk_k","rule","method","h","status","Q","budget_bp","pred_completion","pred_risk_bp","real_total_bp","real_completed","real_rq"]]+decisions)
print({"cells":len(rows),"decisions":len(decisions),"frozen_ok":sum(1 for v in A_FROZEN.values() if v),"frozen_none":sum(1 for v in A_FROZEN.values() if v is None)})
