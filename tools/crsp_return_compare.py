"""Compare our built total returns with CRSP's RET on every member-day of a window.

Our daily total return is adj_close[t] / adj_close[t-1] - 1 (adj_close carries dividends and
splits); CRSP's RET carries every distribution. A splice at the wrong ratio, a split booked at
the wrong factor or a missed distribution shows as a day where the two differ.
Usage (CRSP CSV under kag/): python tools/crsp_return_compare.py PRICES.parquet MEMBERSHIP.csv OUT.csv [TOL_PCT=0.5]
  MEMBERSHIP.csv: label,permno,start,end (pre1996/membership.csv). Added 2026-10-15."""
import glob, sys
import numpy as np, pandas as pd

pf, mf, out = sys.argv[1:4]
tol = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
m = pd.read_csv(mf, parse_dates=["start", "end"])
A, B = m.start.min(), m.end.max()
p = pd.read_parquet(pf, columns=["ticker", "date", "adj_close", "close", "source"])
p["date"] = pd.to_datetime(p.date)
p = p[p.ticker.isin(set(m.label)) & (p.date >= A - pd.Timedelta(days=10)) & (p.date < B)].sort_values(["ticker", "date"])
p["ours"] = p.groupby("ticker").adj_close.pct_change()
f = glob.glob("kag/**/*.csv", recursive=True)[0]
parts = []
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in {"permno", "date", "ret", "prc"}, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch = ch[ch.permno.isin(set(m.permno))]
    if len(ch):
        parts.append(ch)
c = pd.concat(parts)
c["date"] = pd.to_datetime(c["date"].astype(str), errors="coerce")
c = c[(c.date >= A) & (c.date < B)]
c["ret"] = pd.to_numeric(c.ret, errors="coerce")
rows = []
for r in m.itertuples():
    o = p[(p.ticker == r.label) & (p.date >= r.start) & (p.date < r.end)][["date", "ours", "close", "source"]]
    k = c[(c.permno == r.permno) & (c.date >= r.start) & (c.date < r.end)][["date", "ret", "prc"]]
    j = o.merge(k, on="date", how="outer")
    j["label"], j["permno"] = r.label, r.permno
    rows.append(j)
x = pd.concat(rows)
x["diff_pct"] = (x.ours - x.ret) * 100
both = x.dropna(subset=["ours", "ret"])
bad = both[both.diff_pct.abs() > tol]
x.to_csv(out, index=False)
print(f"{len(both):,} member-days compared ({x.label.nunique()} labels); within {tol}%: {100 * (1 - len(bad) / max(len(both), 1)):.3f}%; "
      f"ours without CRSP: {x.ret.isna().sum()}, CRSP without ours: {x.ours.isna().sum()}")
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 400)
print("\nlabels with the most differing days:\n" + bad.groupby("label").size().sort_values(ascending=False).head(40).to_string())
print("\nlargest differences:\n" + bad.reindex(bad.diff_pct.abs().sort_values(ascending=False).index).head(80)[["label", "permno", "date", "ours", "ret", "diff_pct", "close", "prc", "source"]].to_string())
