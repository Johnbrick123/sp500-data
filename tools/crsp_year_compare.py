"""Compare one year of the built dataset with CRSP, member by member.

Input: the report of a read-only `crsp_fetch.yml` run made with specs='-', every member label of
the year as `checks`, `check_year=YYYY`, `match_year=YYYY` and `check_rows=1` (download
crsp_fetch_report.txt from the release). Each matched label's CRSP rows for the year are in it.

Usage: crsp_year_compare.py REPORT DATA_DIR YEAR OUT_PREFIX [TR_PP] [PX_PCT]
  DATA_DIR holds prices.parquet and membership_intervals.parquet.
  Writes OUT_PREFIX_tr.csv - member-days whose total return (adj_close) differs from CRSP's RET
  by more than TR_PP percentage points (default 0.2) - and OUT_PREFIX_px.csv - closes that differ
  from CRSP's by more than PX_PCT percent (default 0.1) after removing the series' own basis (the
  9-day rolling median of our close / CRSP's, so Yahoo's split-adjusted closes compare too).
Note: on an ex-date with a large price move the 1 - D/P adjustment and RET's (P1 + D)/P0 differ
by a few hundredths of a point - the dataset's convention, not an error (use TR_PP >= 0.03)."""
import re, sys
import numpy as np, pandas as pd

def load_rows(fn):
    rows = []
    t = open(fn).read()
    for m in re.finditer(r'CRSP rows for permno (\d+) [^\n]*:\n((?:  \d{4}-[^\n]*\n)*)', t):
        for line in m.group(2).splitlines():
            f = line.split()
            try:
                rows.append((int(m.group(1)), f[0], abs(float(f[2])), f[4]))
            except (ValueError, IndexError):
                pass
    c = pd.DataFrame(rows, columns=['permno', 'cd', 'prc', 'ret_raw']).drop_duplicates(['permno', 'cd'])
    c['ret'] = pd.to_numeric(c.ret_raw, errors='coerce')
    return c, {m.group(1): int(m.group(2)) for m in re.finditer(r'^check (\S+) \([a-z_\-]+\): permno (\d+)', t, re.M)}

rep, D, Y, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
TR = float(sys.argv[5]) / 100 if len(sys.argv) > 5 else 0.002
PX = float(sys.argv[6]) / 100 if len(sys.argv) > 6 else 0.001
c, perm = load_rows(rep)
p = pd.read_parquet(f'{D}/prices.parquet', columns=['ticker', 'date', 'close', 'adj_close', 'dividends', 'stock_splits', 'source'])
p['date'] = pd.to_datetime(p.date)
p = p[p.ticker.isin(perm) & (p.date >= f'{Y-1}-12-01') & (p.date <= f'{Y}-12-31')].sort_values(['ticker', 'date'])
p['tr'] = p.groupby('ticker').adj_close.pct_change()
p['cd'] = p.date.dt.strftime('%Y-%m-%d')
iv = pd.read_parquet(f'{D}/membership_intervals.parquet')
out_tr, out_px = [], []
for lab, pn in perm.items():
    x = c[c.permno == pn].merge(p[p.ticker == lab], on='cd')
    x = x[x.date.dt.year == Y]
    if not len(x):
        continue
    v = iv[iv.ticker == lab]
    x = x[x.date.apply(lambda d: ((v.start <= d) & (v['end'].isna() | (v['end'] > d))).any())]
    rel = x.close / x.prc
    med = rel.rolling(9, center=True, min_periods=3).median()
    for r, rr, mm in zip(x.itertuples(), rel, med):
        if abs(rr / mm - 1) > PX:
            out_px.append((lab, r.source, pn, r.cd, r.close, r.prc, round((rr / mm - 1) * 100, 3), round(mm, 6)))
        if not (np.isnan(r.tr) or np.isnan(r.ret)) and abs(r.tr - r.ret) > TR:
            out_tr.append((lab, r.source, pn, r.cd, round(r.ret * 100, 3), round(r.tr * 100, 3), round((r.tr - r.ret) * 100, 3), r.dividends, r.stock_splits, r.close, r.prc))
pd.DataFrame(out_tr, columns=['label', 'src', 'permno', 'date', 'crsp_ret%', 'our_ret%', 'diff_pp', 'dividend', 'split', 'close', 'crsp_close']).to_csv(out + '_tr.csv', index=False)
pd.DataFrame(out_px, columns=['label', 'src', 'permno', 'date', 'close', 'crsp_close', 'dev%', 'basis']).to_csv(out + '_px.csv', index=False)
print(f'{len(perm)} labels matched; returns off RET by >{TR*100:g}pp: {len(out_tr)}; closes off CRSP by >{PX*100:g}%: {len(out_px)}')
