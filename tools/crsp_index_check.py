"""Adjudicate each flagged close (ours vs CRSP) with the S&P 500's own daily return.
Scenario: every member's return = CRSP's RET, plus the log deviation of any close we keep
(x_t - x_{t-1}). Greedy: keep a vendor close only when that lowers the squared index residual
on its two days by more than the noise allows. Usage: YEAR=2000 crsp_index_check.py REPORT DIR SPX.csv FLAGGED.csv OUT.csv [MIN_BP]
  SPX.csv: Date,Close of the S&P 500 index (the 2000 pass used the fHMM R package's spx data, from Yahoo's ^GSPC).
  FLAGGED.csv: crsp_fullcent_fit.py output. Added 2026-10-14 (the 2000 pass)."""
import re, sys
import numpy as np, pandas as pd
rep, D, spx, fl, out = sys.argv[1:6]
MIN_BP = float(sys.argv[6]) if len(sys.argv) > 6 else 3.0
t = open(rep).read()
perm = {m.group(1): int(m.group(2)) for m in re.finditer(r'^check (\S+) \([a-z_\-]+\): permno (\d+)', t, re.M)}
blocks = {}
for m in re.finditer(rf'CRSP rows for permno (\d+) {__import__("os").environ.get("YEAR", "2000")}:\n((?:  \d{{4}}-[^\n]*\n)*)', t): blocks.setdefault(int(m.group(1)), m.group(2))
iv = pd.read_parquet(f'{D}/membership_intervals.parquet'); iv['start']=pd.to_datetime(iv.start); iv['end']=pd.to_datetime(iv['end'])
rows = []
for lab, pn in perm.items():
    if pn not in blocks: continue
    k = pd.DataFrame([l.split() for l in blocks[pn].splitlines()]).iloc[:, [0, 2, 4, 6]]; k.columns = ['date','prc','ret','shrout']
    k['date'] = pd.to_datetime(k.date)
    for c in ['prc','ret','shrout']: k[c] = pd.to_numeric(k[c], errors='coerce')
    k['cap0'] = (k.prc.abs() * k.shrout).shift(); k['prev'] = k.date.shift()
    v = iv[iv.ticker == lab]
    k['member'] = k.date.apply(lambda d: bool(((v.start <= d) & (v['end'].isna() | (v['end'] > d))).any()))
    k['label'] = lab
    rows.append(k)
a = pd.concat(rows); a = a[a.member].dropna(subset=['cap0','ret'])
a['w'] = a.cap0 / a.groupby('date').cap0.transform('sum')
s = pd.read_csv(spx); s['Date'] = pd.to_datetime(s.Date); I = s.set_index('Date').Close.pct_change()
f = pd.read_csv(fl); f['date'] = pd.to_datetime(f.date)
f = f[f.dev_pct.abs() <= 30]
f['x'] = np.log(f.close / (f.crsp * f.basis))
X = {(r.label, r.date): r.x for r in f.itertuples()}
# residual with all CRSP
A = a.groupby('date').apply(lambda g: float(np.dot(g.w, g.ret)))
e = (I.reindex(A.index) - A) * 1e4
base = e.copy()
# each flagged name-day k touches its own day (w_t * x) and the label's next member day (w_t1 * -x)
W = {(r.label, r.date): r.w for r in a.itertuples()}
nxt = {}
for lab, g in a.sort_values('date').groupby('label'):
    ds = list(g.date)
    for d0, d1 in zip(ds[:-1], ds[1:]): nxt[(lab, d0)] = d1
cands = []
for (lab, d), x in X.items():
    w0 = W.get((lab, d), 0.0); d1 = nxt.get((lab, d)); w1 = W.get((lab, d1), 0.0) if d1 is not None else 0.0
    eff = [(d, w0 * x * 1e4)] + ([(d1, -w1 * x * 1e4)] if d1 is not None else [])
    if max(abs(v) for _, v in eff) >= MIN_BP: cands.append(((lab, d), eff))
keep = set()
changed = True
while changed:
    changed = False
    best = None
    for k, eff in cands:
        if k in keep: continue
        # keeping our close adds its deviation to the scenario's returns: residual moves by -effect
        before = sum(e[d] ** 2 for d, _ in eff if d in e.index)
        after = sum((e[d] - v) ** 2 for d, v in eff if d in e.index)
        gain = before - after
        if gain > 0 and (best is None or gain > best[0]): best = (gain, k, eff)
    if best and best[0] > 2 * MIN_BP ** 2:
        gain, k, eff = best; keep.add(k); changed = True
        for d, v in eff:
            if d in e.index: e[d] -= v
res = pd.DataFrame([dict(label=k[0], date=k[1].strftime('%Y-%m-%d'), effect_bp=round(max(abs(v) for _, v in eff), 2)) for k, eff in cands])
res['keep_ours'] = [ (r.label, pd.Timestamp(r.date)) in keep for r in res.itertuples()]
res.to_csv(out, index=False)
B = e  # final
mm = pd.DataFrame({'crsp_all': base, 'final': e}); mm['m'] = mm.index.to_period('M')
# vendor-all residual for comparison
f2 = f.copy(); a2 = a.merge(f2[['label','date','x']], on=['label','date'], how='left').fillna({'x': 0})
a2 = a2.sort_values(['label','date']); a2['xprev'] = a2.groupby('label').x.shift().fillna(0)
V = a2.groupby('date').apply(lambda g: float(np.dot(g.w, g.ret + g.x - g.xprev)))
mm['vendor_all'] = (I.reindex(V.index) - V) * 1e4
print(mm.groupby('m').agg(sd_crsp=('crsp_all','std'), sd_vendor=('vendor_all','std'), sd_final=('final','std')).round(2).to_string())
print(len(cands), 'candidates with an index effect >=', MIN_BP, 'bp;', len(keep), 'kept as ours')
