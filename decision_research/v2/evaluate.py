"""Pairwise matched evaluation across six configurations, with SD-ratio intervals
from block sums of first and second moments, plus completion calibration."""
import csv, json
from collections import defaultdict
import numpy as np
def load_cells(path="stage2v2_cells.csv"):
    d=defaultdict(dict)
    with open(path) as fh:
        for r in csv.DictReader(fh):
            if r['n_matched']=='0': continue
            k=(r['split'],r['a_bp'],r['risk_k'],r['product'],r['window'],r['date'],r['size_idx'],r['D'],r['side'])
            d[k][r['rule']]=(float(r['mean_total_bp']),float(r['mean_price_bp']),float(r['completion']))
    return d
def boot_block(pa,pb,ca,cb,dates,reps=2000,seed=3):
    u=np.unique(dates); blk={int(x):i//5 for i,x in enumerate(u)}
    bi=np.array([blk[int(x)] for x in dates]); nb=int(bi.max())+1
    n=np.bincount(bi,minlength=nb).astype(float)
    sA=np.bincount(bi,weights=pa,minlength=nb); sA2=np.bincount(bi,weights=pa**2,minlength=nb)
    sB=np.bincount(bi,weights=pb,minlength=nb); sB2=np.bincount(bi,weights=pb**2,minlength=nb)
    sC=np.bincount(bi,weights=(ca-cb),minlength=nb)
    rng=np.random.default_rng(seed); cd=[]; sd=[]; rt=[]
    for _ in range(reps):
        q=rng.integers(0,nb,nb); n_=n[q].sum()
        if n_<2: continue
        ma=sA[q].sum()/n_; va=max(sA2[q].sum()/n_-ma*ma,0.0)*n_/(n_-1)
        mb=sB[q].sum()/n_; vb=max(sB2[q].sum()/n_-mb*mb,0.0)*n_/(n_-1)
        sa,sb=np.sqrt(va),np.sqrt(vb)
        cd.append(sC[q].sum()/n_); sd.append(sa-sb); rt.append(sa/sb if sb>0 else np.nan)
    q=lambda x,p: float(np.nanpercentile(x,p))
    return dict(cost_mean=float((ca-cb).mean()),cost_lo=q(cd,2.5),cost_hi=q(cd,97.5),
                sd_a=float(np.std(pa,ddof=1)),sd_b=float(np.std(pb,ddof=1)),
                sd_ratio=float(np.std(pa,ddof=1)/np.std(pb,ddof=1)),
                sd_diff_lo=q(sd,2.5),sd_diff_hi=q(sd,97.5),
                sd_ratio_lo=q(rt,2.5),sd_ratio_hi=q(rt,97.5),
                comp_a=float("nan"),comp_b=float("nan"),
                n=int(len(pa)),n_dates=int(len(u)),n_blocks=int(nb))
def main():
    d=load_cells(); out={}
    for split in ("val","test"):
        for a in ("0.0","0.5","2.0"):
            for k in ("1.0","2.0"):
                for ra,rb in (("B","A"),("C","A"),("C","B")):
                    sub=[(kk,v) for kk,v in d.items() if kk[0]==split and kk[1]==a and kk[2]==k and ra in v and rb in v]
                    if len(sub)<50: continue
                    ca=np.array([v[ra][0] for _,v in sub]); cb=np.array([v[rb][0] for _,v in sub])
                    pa=np.array([v[ra][1] for _,v in sub]); pb=np.array([v[rb][1] for _,v in sub])
                    cma=np.array([v[ra][2] for _,v in sub]); cmb=np.array([v[rb][2] for _,v in sub])
                    dates=[kk[5] for kk,_ in sub]
                    r=boot_block(pa,pb,ca,cb,dates)
                    r["comp_a"]=float(cma.mean()); r["comp_b"]=float(cmb.mean())
                    r["comp_diff"]=r["comp_a"]-r["comp_b"]
                    out[f"{split}|a{a}|k{k}|{ra}-{rb}"]=r
    json.dump(out,open("evaluate_pairs.json","w"),ensure_ascii=False,indent=1)
    # coverage, uniqueness, calibration
    rows=list(csv.DictReader(open('stage2v2_decisions_main.csv')))
    key=lambda r:(r['split'],r['product'],r['window'],r['date'],r['size_idx'],r['D'],r['side'],r['rule'])
    assert len({key(r) for r in rows})==len(rows)
    td=[int(r['date']) for r in rows if r['split']=='test']
    assert 20260701<=min(td) and max(td)<=20261020
    orders=defaultdict(set); cov=defaultdict(lambda: defaultdict(int)); cal=defaultdict(lambda:[0,0,0.0])
    for r in rows:
        orders[r['split']].add((r['product'],r['window'],r['date'],r['size_idx'],r['D'],r['side']))
        cov[r['split']][(r['rule'],r['status'])]+=1
        if r['rule'] in ('B','C') and r['pred_completion']!='' and r['real_total_bp']!='':
            p=float(r['pred_completion'])
            g='p=1.0' if p>=0.999 else ('p>=0.95' if p>=0.95 else 'p<0.95')
            c=cal[(r['split'],r['rule'],g)]; c[0]+=1; c[1]+=int(float(r['real_completed'])); c[2]+=p
    cali=defaultdict(dict)
    for (sp,rule,g),(n,ok,ps) in sorted(cal.items()):
        cali[sp].setdefault(rule,[]).append([g,round(ps/n,4),round(ok/n,4),round(ok/n-ps/n,4),n])
    chk={"orders":{"val":len(orders['val']),"test":len(orders['test'])},
         "coverage":{s:{f"{r}_{st}":v for (r,st),v in cov[s].items()} for s in cov},
         "calibration":cali}
    json.dump(chk,open("evaluate_checks.json","w"),ensure_ascii=False,indent=1,default=float)
    print(json.dumps({"pairs":len(out),"orders":chk["orders"],
                      "test_a0.5_k1_B-A":{k:round(v,4) for k,v in out["test|a0.5|k1.0|B-A"].items() if isinstance(v,float)},
                      "cal":cali["test"]["B"]},ensure_ascii=False))
main()
