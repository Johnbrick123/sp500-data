"""Every matched member's dividends in a year against the distribution CRSP's RET implies,
D = PRC[t-1] x (1 + RET[t]) - PRC[t] (raw basis), with ours divided by the series' basis; lists
every day where the two differ by more than 0.6 cents a share. Split days and moves over 30% are
skipped. Added 2026-10-13 (the 2005 pass: Nike, General Mills and Moody's halves, Duke's triple,
Sara Lee's, XTO's gaps, Sears's payments inside Sprint's line).
Usage: crsp_implied_divs.py REPORT DATA_DIR YEAR OUT.csv [THRESH]   (REPORT as for crsp_cent_diff.py;
THRESH in dollars, default 0.006 - 0.0008 finds sub-cent errors: RET's six decimals pin D to ~0.01 cent)"""
import re, sys
import numpy as np, pandas as pd
rep, D, Y, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
TH = float(sys.argv[5]) if len(sys.argv) > 5 else 0.006
t = open(rep).read()
perm = {m.group(1): int(m.group(2)) for m in re.finditer(r'^check (\S+) \([a-z_\-]+\): permno (\d+)', t, re.M)}
blocks = {int(m.group(1)): m.group(2) for m in re.finditer(rf'CRSP rows for permno (\d+) {Y}:\n((?:  \d{{4}}-[^\n]*\n)*)', t)}
p = pd.read_parquet(f'{D}/prices.parquet'); p['date'] = pd.to_datetime(p.date); p = p[p.date.dt.year == Y]
iv = pd.read_parquet(f'{D}/membership_intervals.parquet'); iv['start']=pd.to_datetime(iv.start); iv['end']=pd.to_datetime(iv.end)
res = []
for lab, pn in perm.items():
    if pn not in blocks or not blocks[pn].strip(): continue      # an empty block: no rows that year
    c = pd.DataFrame([l.split() for l in blocks[pn].splitlines()]).iloc[:, [0, 2, 4]]; c.columns = ['cd', 'prc', 'ret']
    c['prc'] = pd.to_numeric(c.prc, errors='coerce').abs(); c['ret'] = pd.to_numeric(c.ret, errors='coerce')
    c['impl'] = c.prc.shift() * (1 + c.ret) - c.prc
    o = p[p.ticker == lab].copy(); o['cd'] = o.date.dt.strftime('%Y-%m-%d')
    x = c.merge(o[['cd', 'date', 'close', 'dividends', 'stock_splits', 'source']], on='cd', how='left').sort_values('cd')
    x['basis'] = (x.close / x.prc).rolling(9, center=True, min_periods=3).median()
    x['our_raw'] = x.dividends.fillna(0) / x.basis
    v = iv[iv.ticker == lab]
    for r in x.itertuples():
        sp = r.stock_splits if pd.notna(r.stock_splits) else 1
        if sp not in (0, 1): continue
        if pd.isna(r.impl) or pd.isna(r.close): continue
        if abs(r.impl) > 0.3 * r.prc: continue
        d = r.our_raw - max(r.impl, 0)
        if abs(d) > TH and (r.our_raw > 0 or r.impl > max(TH, 0.003)):
            mem = ((v.start <= r.date) & (v['end'].isna() | (v['end'] > r.date))).any()
            res.append(dict(label=lab, permno=pn, date=r.cd, src=r.source, member=mem, prc=r.prc, ret=r.ret, impl=round(r.impl, 4),
                            ours=r.dividends, basis=round(r.basis, 6), our_raw=round(r.our_raw, 4), diff=round(d, 4)))
res = pd.DataFrame(res); res.to_csv(out, index=False)
pd.set_option('display.width', 220); pd.set_option('display.max_rows', 500)
print(res.to_string(index=False))
