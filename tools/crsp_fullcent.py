"""Every member-day close vs CRSP x (per-label, per-split-segment median basis from decimal-era days). Usage: fullcent.py REPORTS DIR YEAR OUT.csv"""
import re, sys
import pandas as pd
reps, D, Y, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
t = "".join(open(f).read() for f in reps.split(","))
perm = {}
for m in re.finditer(r'^check (\S+) \([a-z_\-]+\): permno (\d+)', t, re.M): perm[m.group(1)] = int(m.group(2))
blocks = {int(m.group(1)): m.group(2) for m in re.finditer(rf'CRSP rows for permno (\d+) {Y}:\n((?:  \d{{4}}-[^\n]*\n)*)', t)}
p = pd.read_parquet(f'{D}/prices.parquet', columns=['ticker','date','open','high','low','close','volume','stock_splits','source']); p['date'] = pd.to_datetime(p.date); p = p[p.date.dt.year == Y]
iv = pd.read_parquet(f'{D}/membership_intervals.parquet')
res = []
for lab, pn in perm.items():
    if pn not in blocks or not blocks[pn].strip(): continue      # an empty block: no rows that year
    k = pd.DataFrame([l.split() for l in blocks[pn].splitlines()]).iloc[:, [0, 2]]; k.columns = ['date', 'prc']
    k['date'] = pd.to_datetime(k.date); k['prc'] = pd.to_numeric(k.prc, errors='coerce').abs()
    o = p[p.ticker == lab].sort_values('date')
    m = o.merge(k, on='date').dropna(subset=['prc','close'])
    if m.empty: continue
    sp = m.stock_splits.fillna(0).replace(0, 1.0)
    m['seg'] = (sp != 1).cumsum(); m['r'] = m.close / m.prc
    dec = set(m[m.date >= pd.Timestamp(f'{Y}-04-10')].date) if Y <= 2001 else set(m.date)
    b = {}
    for s, h in m.groupby('seg'):
        hh = h[h.date.isin(dec)]; b[s] = (hh if len(hh) >= 10 else h).r.median()
    m['basis'] = m.seg.map(b)
    v = iv[iv.ticker == lab]; vs = pd.to_datetime(v.start); ve = pd.to_datetime(v['end'])
    m['member'] = m.date.apply(lambda d: bool(((vs <= d) & (ve.isna() | (ve > d))).any()))
    m = m[m.member & ((m.close - m.prc * m.basis).abs() >= 0.0099 * m.basis.clip(upper=1))]
    for r in m.itertuples():
        res.append(dict(label=lab, permno=pn, date=r.date.strftime('%Y-%m-%d'), src=r.source, close=r.close, open=r.open, high=r.high, low=r.low, volume=r.volume,
                        crsp=r.prc, basis=r.basis, dev_pct=(r.close/(r.prc*r.basis)-1)*100, cents=(r.close/r.basis-r.prc)*100, neg=False))
pd.DataFrame(res).to_csv(out, index=False); print(len(res))
