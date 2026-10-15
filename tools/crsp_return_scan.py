"""Wrong-company screen: for every matched label, the share of its member days whose daily close-to-close
move agrees with CRSP's RET within 1%, and the median level ratio (ours / CRSP). A label far below ~97% is
another company's file (1998: SNT-199910 matched to the wrong permno showed 14%) - run it right after the
all-member check, before anything else. Usage: crsp_return_scan.py REPORT YEAR PRICES.parquet MEMBERSHIP.parquet
Added 2026-10-15 (the 1998 pass)."""
import re, sys, pandas as pd, numpy as np
rep, Y = sys.argv[1], sys.argv[2]
t=open(rep).read()
perm={m.group(1):int(m.group(2)) for m in re.finditer(r'^check (\S+) \([a-z_\-]+\): permno (\d+)',t,re.M)}
blocks={}
for m in re.finditer(rf'CRSP rows for permno (\d+) {Y}:\n((?:  \d{{4}}-[^\n]*\n)*)',t): blocks.setdefault(int(m.group(1)),m.group(2))
p=pd.read_parquet(sys.argv[3],columns=['ticker','date','close','source']); p['date']=pd.to_datetime(p.date)
iv=pd.read_parquet(sys.argv[4])
out=[]
for lab,pn in perm.items():
    b=blocks.get(pn,'').strip()
    if not b: continue
    k=pd.DataFrame([l.split() for l in b.splitlines()]).iloc[:,[0,2,4]]; k.columns=['date','prc','ret']
    k['date']=pd.to_datetime(k.date); k['prc']=pd.to_numeric(k.prc,errors='coerce').abs(); k['ret']=pd.to_numeric(k.ret,errors='coerce')
    v=iv[iv.ticker==lab]
    mem=k.date.apply(lambda d: bool(((v.start<=d)&(v.end.isna()|(v.end>d))).any()))
    o=p[p.ticker==lab].set_index('date').close
    j=k[mem].copy(); j['o']=j.date.map(o); j['op']=j.date.map(o.shift().reindex(o.index)) if len(o) else np.nan
    j['oret']=j.o/j.op-1
    jj=j.dropna(subset=['oret','ret'])
    if len(jj)==0: out.append((lab,pn,len(j),0,np.nan,np.nan)); continue
    agree=((jj.oret-jj.ret).abs()<0.01).mean()
    lvl=(j.o/j.prc).median()
    out.append((lab,pn,len(j),len(jj),agree,lvl))
r=pd.DataFrame(out,columns=['label','permno','memdays','cmp','ret_agree','level_ratio']).sort_values('ret_agree')
print(r.head(40).to_string(index=False))
print(len(r))
