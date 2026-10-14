"""Per member-day: CRSP-implied distribution vs ours (raw basis), for days where either is > 0.002. Usage: divtable.py REPORT DIR YEAR OUT.csv"""
import re, sys
import pandas as pd
rep, D, Y, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
t = "".join(open(f).read() for f in rep.split(","))
perm = {}
for m in re.finditer(r'^check (\S+) \([a-z_\-]+\): permno (\d+)', t, re.M): perm[m.group(1)] = int(m.group(2))
blocks = {int(m.group(1)): m.group(2) for m in re.finditer(rf'CRSP rows for permno (\d+) {Y}:\n((?:  \d{{4}}-[^\n]*\n)*)', t)}
p = pd.read_parquet(f'{D}/prices.parquet', columns=['ticker','date','close','dividends','stock_splits','source']); p['date'] = pd.to_datetime(p.date); p = p[p.date.dt.year == Y]
iv = pd.read_parquet(f'{D}/membership_intervals.parquet')
res = []
for lab, pn in perm.items():
    if pn not in blocks or not blocks[pn].strip(): continue      # an empty block: no rows that year
    c = pd.DataFrame([l.split() for l in blocks[pn].splitlines()]).iloc[:, [0, 2, 4]]; c.columns = ['cd', 'prc', 'ret']
    c['prc'] = pd.to_numeric(c.prc, errors='coerce').abs(); c['ret'] = pd.to_numeric(c.ret, errors='coerce')
    c['impl'] = c.prc.shift() * (1 + c.ret) - c.prc
    o = p[p.ticker == lab].copy(); o['cd'] = o.date.dt.strftime('%Y-%m-%d')
    x = c.merge(o[['cd','date','close','dividends','stock_splits','source']], on='cd', how='left').sort_values('cd')
    x['basis'] = (x.close / x.prc).rolling(9, center=True, min_periods=3).median()
    x['our_raw'] = x.dividends.fillna(0) / x.basis
    v = iv[iv.ticker == lab]; v_s = pd.to_datetime(v.start); v_e = pd.to_datetime(v['end'])
    for r in x.itertuples():
        if pd.isna(r.close): continue
        sp = r.stock_splits if pd.notna(r.stock_splits) else 1
        imp = r.impl if pd.notna(r.impl) and abs(r.impl) < 0.3 * r.prc else float('nan')
        if (r.our_raw > 0.002) or (pd.notna(imp) and imp > 0.002):
            d = pd.Timestamp(r.cd); mem = bool(((v_s <= d) & (v_e.isna() | (v_e > d))).any())
            res.append(dict(label=lab, permno=pn, date=r.cd, src=r.source, member=mem, prc=r.prc, ret=r.ret, impl=imp, ours=r.dividends, basis=r.basis, our_raw=r.our_raw, split=sp))
pd.DataFrame(res).to_csv(out, index=False)
print(len(res))
