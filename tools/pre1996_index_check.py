"""Rebuild the S&P 500's daily return from our pre-1996 membership and CRSP, and compare it
with the published index - the independent check of membership and prices before 1996, where
most dead companies have no second price source.

Each member-day's weight is its CRSP market cap at the previous close (|PRC| x SHROUT); its
return is CRSP's RET (dividends included), so the rebuilt index runs about one dividend yield
a year ahead of the price index - a constant drift, not noise. A wrong or missing member, or a
bad price, shows up as a daily residual far above the usual few basis points.
Usage (CRSP CSV unpacked under kag/): python tools/pre1996_index_check.py MEMBERSHIP.csv SPX.csv START END OUTDIR
  MEMBERSHIP.csv: label,permno,start,end (pre1996/membership.csv); SPX.csv: Date,Close.
-> OUTDIR/index_daily.csv (date, spx, rebuilt, resid_bp, n, top movers on the worst days)
   and a monthly table printed. Added 2026-10-15."""
import glob, os, sys
import numpy as np, pandas as pd

mfile, sfile, A, B, out = sys.argv[1:6]
A, B = pd.Timestamp(A), pd.Timestamp(B)
os.makedirs(out, exist_ok=True)
m = pd.read_csv(mfile, parse_dates=["start", "end"])
want = set(m.permno)
f = glob.glob("kag/**/*.csv", recursive=True)[0]
parts = []
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in {"permno", "date", "prc", "ret", "shrout"}, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch = ch[ch.permno.isin(want)]
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    ch = ch[(ch.date >= A - pd.Timedelta(days=10)) & (ch.date <= B)]
    if len(ch):
        parts.append(ch)
c = pd.concat(parts)
for k in ("prc", "ret", "shrout"):
    c[k] = pd.to_numeric(c[k], errors="coerce")
c = c.sort_values(["permno", "date"])
c["cap0"] = (c.prc.abs() * c.shrout).groupby(c.permno).shift()
s = pd.read_csv(sfile, parse_dates=["Date"]).set_index("Date").Close.sort_index()
spx = s.pct_change()
days = [d for d in s.index if A <= d <= B]
rows, worst = [], []
for d in days:
    mem = m[(m.start <= d) & (m.end > d)]
    x = c[(c.date == d) & c.permno.isin(set(mem.permno))].dropna(subset=["cap0", "ret"])
    miss = len(mem) - len(x)
    w = x.cap0 / x.cap0.sum()
    r = float((w * x.ret).sum())
    e = (spx.get(d, np.nan) - r) * 1e4
    lab = dict(zip(mem.permno, mem.label))
    top = (w * x.ret).abs().sort_values(ascending=False).head(3).index
    rows.append(dict(date=d.date(), spx=round(spx.get(d, np.nan) * 1e4, 2), rebuilt=round(r * 1e4, 2), resid_bp=round(e, 2),
                     members=len(mem), priced=len(x), missing=miss,
                     top=" ".join(f"{lab[int(x.loc[i, 'permno'])]}:{x.loc[i, 'ret'] * 100:+.1f}%" for i in top)))
o = pd.DataFrame(rows)
o.to_csv(f"{out}/index_daily.csv", index=False)
o["month"] = pd.to_datetime(o.date).dt.to_period("M")
g = o.groupby("month").agg(days=("resid_bp", "size"), mean_bp=("resid_bp", "mean"), sd_bp=("resid_bp", "std"),
                           max_abs_bp=("resid_bp", lambda v: v.abs().max()), missing=("missing", "sum"))
pd.set_option("display.max_rows", 500); pd.set_option("display.width", 200)
print(g.round(2).to_string())
print(f"\nall days: mean {o.resid_bp.mean():.2f} bp, sd {o.resid_bp.std():.2f} bp, |resid| > 20 bp on {(o.resid_bp.abs() > 20).sum()} days; member-days without a CRSP return: {o.missing.sum()}")
print("\nworst 25 days:\n" + o.reindex(o.resid_bp.abs().sort_values(ascending=False).index).head(25).drop(columns="month").to_string())
