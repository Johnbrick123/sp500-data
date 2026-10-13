"""Every member-day close of a year that differs from CRSP by a cent or more on the raw basis
(our close / the series' basis - CRSP's |PRC|; the basis is the 9-day rolling median of our close /
CRSP's, so Yahoo's split-adjusted closes compare too). Added 2026-10-13 (the 2005 pass).
Usage: crsp_cent_diff.py REPORT DATA_DIR YEAR OUT.csv
  REPORT = crsp_fetch_report.txt of a read-only crsp_fetch.yml run with check_year=YEAR and check_rows=1.
A dev_pct over 30% is the rolling basis lagging at a split, not an error."""
import re, sys
import numpy as np, pandas as pd
rep, D, Y, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
t = open(rep).read()
rows = []
for m in re.finditer(r'CRSP rows for permno (\d+) (\d{4}):\n((?:  \d{4}-[^\n]*\n)*)', t):
    for line in m.group(3).splitlines():
        f = line.split()
        try: rows.append((int(m.group(1)), f[0], float(f[2]), f[4], f[6]))
        except (ValueError, IndexError): pass
c = pd.DataFrame(rows, columns=['permno','cd','prc','ret','shrout']).drop_duplicates(['permno','cd'])
c['ret'] = pd.to_numeric(c.ret, errors='coerce')
perm = {m.group(1): int(m.group(2)) for m in re.finditer(r'^check (\S+) \([a-z_\-]+\): permno (\d+)', t, re.M)}
p = pd.read_parquet(f'{D}/prices.parquet')
p['date'] = pd.to_datetime(p.date)
p = p[p.ticker.isin(perm) & (p.date.dt.year == Y)]
p['cd'] = p.date.dt.strftime('%Y-%m-%d')
iv = pd.read_parquet(f'{D}/membership_intervals.parquet'); iv['start']=pd.to_datetime(iv.start); iv['end']=pd.to_datetime(iv.end)
res = []
for lab, pn in perm.items():
    x = c[c.permno == pn].merge(p[p.ticker == lab], on='cd').sort_values('cd').reset_index(drop=True)
    if not len(x): continue
    x['aprc'] = x.prc.abs()
    rel = x.close / x.aprc
    med = rel.rolling(9, center=True, min_periods=3).median()
    v = iv[iv.ticker == lab]
    for i, r in x.iterrows():
        mem = ((v.start <= r.date) & (v['end'].isna() | (v['end'] > r.date))).any()
        b = med[i]
        raw_ours = r.close / b
        dc = raw_ours - r.aprc
        if abs(dc) >= 0.0095 and mem:
            res.append(dict(label=lab, permno=pn, date=r.cd, src=r.source, close=r.close, open=r.open, high=r.high, low=r.low,
                            volume=r.volume, crsp=r.prc, basis=round(b, 6), dev_pct=round((rel[i]/b-1)*100, 4), cents=round(dc*100, 2),
                            neg=r.prc < 0))
res = pd.DataFrame(res)
res.to_csv(out, index=False)
print(len(res), 'closes off by >= 1 cent;', (res.dev_pct.abs() > 30).sum(), 'basis artifacts (>30%)')
print(res.groupby('src').size().to_string())
print(res[res.dev_pct.abs() <= 30].dev_pct.abs().describe().to_string())
