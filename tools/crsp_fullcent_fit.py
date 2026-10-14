"""Like tools/crsp_fullcent.py, but each label/split-segment basis is the value of close/CRSP that puts the most
days within a cent of CRSP (when that is >= 30% of the segment's days), else the median.
Fractional-era years have no decimal days, and a vendor's tick differences drag a median off.
Usage: [EXTRA_BREAKS=LABEL:YYYY-MM-DD,...] crsp_fullcent_fit.py REPORTS DIR YEAR OUT.csv   (added 2026-10-14, the 2000 pass)"""
import re, sys
import numpy as np, pandas as pd
reps, D, Y, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
t = "".join(open(f).read() for f in reps.split(","))
perm = {}
for m in re.finditer(r'^check (\S+) \([a-z_\-]+\): permno (\d+)', t, re.M): perm[m.group(1)] = int(m.group(2))
blocks = {}
for m in re.finditer(rf'CRSP rows for permno (\d+) {Y}:\n((?:  \d{{4}}-[^\n]*\n)*)', t): blocks.setdefault(int(m.group(1)), m.group(2))
p = pd.read_parquet(f'{D}/prices.parquet', columns=['ticker','date','open','high','low','close','volume','stock_splits','source']); p['date'] = pd.to_datetime(p.date); p = p[p.date.dt.year == Y]
iv = pd.read_parquet(f'{D}/membership_intervals.parquet')
res = []; bases = []
for lab, pn in perm.items():
    if pn not in blocks or not blocks[pn].strip(): continue      # an empty block: the permno has no rows that year
    k = pd.DataFrame([l.split() for l in blocks[pn].splitlines()]).iloc[:, [0, 2, 4]]; k.columns = ['date', 'prc', 'ret']
    k['date'] = pd.to_datetime(k.date); k['prc'] = pd.to_numeric(k.prc, errors='coerce').abs()
    o = p[p.ticker == lab].sort_values('date')
    m = o.merge(k, on='date').dropna(subset=['prc','close'])
    if m.empty: continue
    sp = m.stock_splits.fillna(0).replace(0, 1.0)
    brk = [pd.Timestamp(x.split(':')[1]) for x in __import__('os').environ.get('EXTRA_BREAKS', '').split(',') if x.split(':')[0] == lab]
    m['seg'] = (sp != 1).cumsum() + sum((m.date >= b).astype(int) for b in brk); m['r'] = m.close / m.prc
    b = {}
    for s, h in m.groupby('seg'):
        cand = np.unique(np.round(h.r.values, 6))
        cl = h.close.values[None, :]; pr = h.prc.values[None, :]; cb = cand[:, None]
        ok = (np.abs(cl - pr * cb) < 0.0099 * np.minimum(cb, 1)).sum(axis=1)
        b[s] = float(cand[ok.argmax()]) if ok.max() >= 0.3 * len(h) else h.r.median()
        if h.source.mode().iloc[0] in ('tiingo', 'wiki', 'crsp', 'crsp-patch') and abs(b[s] - 1) < 0.01:
            b[s] = 1.0      # raw closes: the basis is 1 by construction (a fitted 0.9998 would leave patched closes a hair off CRSP's)
        bases.append((lab, s, len(h), b[s], ok.max() / len(h)))
    m['basis'] = m.seg.map(b)
    v = iv[iv.ticker == lab]; vs = pd.to_datetime(v.start); ve = pd.to_datetime(v['end'])
    m['member'] = m.date.apply(lambda d: bool(((vs <= d) & (ve.isna() | (ve > d))).any()))
    m = m[m.member & ((m.close - m.prc * m.basis).abs() >= 0.0099 * m.basis.clip(upper=1))]
    for r in m.itertuples():
        res.append(dict(label=lab, permno=pn, date=r.date.strftime('%Y-%m-%d'), src=r.source, close=r.close, open=r.open, high=r.high, low=r.low, volume=r.volume,
                        crsp=r.prc, basis=r.basis, dev_pct=(r.close/(r.prc*r.basis)-1)*100, cents=(r.close/r.basis-r.prc)*100, neg=False))
pd.DataFrame(res).to_csv(out, index=False)
pd.DataFrame(bases, columns=['label','seg','n','basis','share']).to_csv(out.replace('.csv', '_bases.csv'), index=False)
print(len(res))
