"""Conditional envelope C(a)=intercept+a*slope per (product,window,split,size,D,side,phi,k)."""
import csv, json, os
import numpy as np
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); RES=os.path.join(ROOT,"results"); OUT=os.path.join(ROOT,"decision_research")
P=["RB","I","CU","AU","SC","TA","RU","M","P","IF","IC","T"]; W=["am","pm","night"]; M=["asap","twap","shape"]
TRAIN=(20260105,20260430); VAL=(20260506,20260630); TEST=(20260701,20261020); FEE,MINCOMP=1.3,0.95
def load(p):
    with np.load(p,allow_pickle=False) as z: return {k:np.array(z[k],copy=True) for k in z.files}
a=load(os.path.join(RES,"replay_train_val_t60.npz")); b=load(os.path.join(RES,"replay_test_t60.npz"))
R={k:np.concatenate([a[k],b[k]]) for k in a}; K=R["keys"]
sp=np.select([K[:,2]<TRAIN[0],K[:,2]<=TRAIN[1],K[:,2]<=VAL[1]],[ -1,0,1],default=2)
use=(K[:,7]==1)&(sp>=0); I=np.nonzero(use)[0]; S=sp[I]
keys=np.stack([K[I,0],K[I,1],S,K[I,3],K[I,4],K[I,5],K[I,6]],axis=1)
uq,inv=np.unique(keys,axis=0,return_inverse=True)
price=R["gap_exec"][I]+R["gap_resid_T60"][I]; fee=R["fee_load"][I]; l1=R["L1"][I]; l5=R["L05"][I]; cp=(R["resid"][I]==0).astype(float)
n=np.bincount(inv,minlength=len(uq))
def bs(w): return np.bincount(inv,weights=w,minlength=len(uq))
p_sum,p2_sum,f_sum,l1_sum,l5_sum,c_sum=[bs(v) for v in (price,price**2,fee,l1,l5,cp)]
pair=np.unique(np.stack([inv,K[I,2]],axis=1),axis=0); nd=np.bincount(pair[:,0],minlength=len(uq))
G={}
for g in range(len(uq)):
    if n[g]<5: continue
    m=p_sum[g]/n[g]; var=max(p2_sum[g]/n[g]-m*m,0.0)*n[g]/max(n[g]-1,1)
    G[tuple(int(x) for x in uq[g])]=dict(n=int(n[g]),nd=int(nd[g]),price=m,fee=f_sum[g]/n[g],
        l1=l1_sum[g]/n[g],l05=l5_sum[g]/n[g],comp=c_sum[g]/n[g],sd=float(np.sqrt(var)))
def arm(split,pi,wi,size,D,side,phi,m,h):
    g=G.get((pi,wi,split,size,D,M.index(m),h))
    if not g: return None
    return dict(name=f"{m}{h}",intercept=side*g["price"]+FEE*g["fee"],
                slope=(g["l1"] if phi==1 else g["l05"]),sd=g["sd"],comp=g["comp"],nd=g["nd"],n=g["n"])
rows=[]; nf=[]; assert_ok=True
for pi in range(12):
 for wi in range(3):
  for split in (0,1,2):
   for size in range(3):
    for D in (5,15,30,60):
     for side in (1.0,-1.0):
      base=arm(0,pi,wi,size,D,side,1.0,"asap",0)
      if base is None: continue
      for phi in (1.0,0.5):
       for kk in (1.0,2.0):
        bud=kk*base["sd"]; cands=[]
        for h in (0,5,10,20,30,60):
         for m in ("asap","twap","shape"):
          if (m=="asap" and h!=0) or (m!="asap" and h==0) or h>D: continue
          x=arm(split,pi,wi,size,D,side,phi,m,h)
          if x and x["comp"]>=MINCOMP and x["sd"]<=bud: cands.append(x)
        cell=[P[pi],W[wi],["train","val","test"][split],size,D,"buy" if side>0 else "sell",phi,kk]
        if not cands:
            nf.append(cell+["no_feasible_under_completion_and_risk"]); continue
        xs={0.0}
        for i in range(len(cands)):
            for j in range(i+1,len(cands)):
                dL=cands[i]["slope"]-cands[j]["slope"]
                if abs(dL)>1e-12:
                    x=(cands[j]["intercept"]-cands[i]["intercept"])/dL
                    if x>0: xs.add(float(x))
        xs=sorted(xs); out=[]
        for t in range(len(xs)):
            lo=xs[t]; hi=xs[t+1] if t+1<len(xs) else None
            mid=(lo+hi)/2 if hi is not None else lo+1.0
            best=min(cands,key=lambda c:c["intercept"]+mid*c["slope"])
            if all(best["intercept"]+mid*best["slope"]<=c["intercept"]+mid*c["slope"]+1e-9 for c in cands):
                pass
            else:
                assert_ok=False
            if out and out[-1]["name"]==best["name"] and (hi is None or abs(out[-1]["hi"]-lo)<1e-12):
                out[-1]["hi"]=hi
            else:
                out.append(dict(name=best["name"],lo=lo,hi=hi,A=best["intercept"],L=best["slope"],
                                sd=best["sd"],comp=best["comp"],nd=best["nd"]))
        assert len(out)<=len(cands), (cell,len(out),len(cands))
        for o in out:
            rows.append(cell+[round(o["lo"],6),"inf" if o["hi"] is None else round(o["hi"],6),o["name"],
                              round(o["A"]+o["lo"]*o["L"],5),round(o["L"],5),round(o["sd"],4),round(o["comp"],4),o["nd"],
                              "descriptive" if o["nd"]<30 else "ok"])
with open(os.path.join(OUT,"envelope_all.csv"),"w",newline="") as fh:
    w=csv.writer(fh); w.writerow(["product","window","split","size_idx","D","side","phi","risk_k","a_from","a_to",
        "arm","cost_at_a_from_bp","slope_L","price_sd","completion","n_dates","note"]); w.writerows(rows)
with open(os.path.join(OUT,"envelope_all_nofeasible.csv"),"w",newline="") as fh:
    w=csv.writer(fh); w.writerow(["product","window","split","size_idx","D","side","phi","risk_k","reason"]); w.writerows(nf)
json.dump({"rows":len(rows),"no_feasible":len(nf),"all_intervals_verified":bool(assert_ok)},open(os.path.join(OUT,"envelope_all_checks.json"),"w"))
print({"rows":len(rows),"no_feasible":len(nf),"all_intervals_verified":assert_ok})
