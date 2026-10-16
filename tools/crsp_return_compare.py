"""Compare our built total returns with CRSP's RET on every member-day of a window.

Our daily total return is adj_close[t] / adj_close[t-1] - 1 (adj_close carries dividends and
splits); CRSP's RET carries every distribution. A splice at the wrong ratio, a split booked at
the wrong factor or a missed distribution shows as a day where the two differ.
Usage (CRSP CSV under kag/): python tools/crsp_return_compare.py PRICES.parquet MEMBERSHIP.csv OUT.csv [TOL_PCT=0.5]
  MEMBERSHIP.csv: label,permno,start,end (pre1996/membership.csv). Added 2026-10-15."""
import glob, os, sys
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
x = pd.concat(rows, ignore_index=True)
x["diff_pct"] = (x.ours - x.ret) * 100
both = x.dropna(subset=["ours", "ret"])
bad = both[both.diff_pct.abs() > tol]
x.to_csv(out, index=False)
bad[["label", "permno", "date", "ours", "ret", "diff_pct", "close", "source"]].to_csv(out.replace(".csv", "_flagged.csv"), index=False)
print(f"{len(both):,} member-days compared ({x.label.nunique()} labels); within {tol}%: {100 * (1 - len(bad) / max(len(both), 1)):.3f}%; "
      f"ours without CRSP: {x.ret.isna().sum()}, CRSP without ours: {x.ours.isna().sum()}")
# coverage: member-days (on CRSP's trading calendar) with no row of ours at all
cal = np.array(sorted(c.date.unique()))
gaps = []
for r in m.itertuples():
    days = cal[(cal >= np.datetime64(r.start)) & (cal < np.datetime64(r.end))]
    have = set(p.loc[p.ticker == r.label, "date"].values)
    gaps += [(r.label, pd.Timestamp(d).date()) for d in days if d not in have]
print(f"COVERAGE: {sum(((cal >= np.datetime64(r.start)) & (cal < np.datetime64(r.end))).sum() for r in m.itertuples()):,} member-days on CRSP's calendar, {len(gaps)} without a row of ours"
      + (f"; by label: {pd.Series([g[0] for g in gaps]).value_counts().head(30).to_dict()}; first: {gaps[:10]}" if gaps else ""))
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 400)
print("\nlabels with the most differing days:\n" + bad.groupby("label").size().sort_values(ascending=False).head(40).to_string())
print("\nby the source of our row:\n" + both.assign(bad=both.diff_pct.abs() > tol).groupby(both.source.astype(str)).bad.agg(["size", "sum"]).to_string())
print("\nby year:\n" + both.assign(bad=both.diff_pct.abs() > tol).groupby(both.date.dt.year).bad.agg(["size", "sum"]).to_string())
print("\nlargest differences:\n" + bad.reindex(bad.diff_pct.abs().sort_values(ascending=False).index).head(150)[["label", "permno", "date", "ours", "ret", "diff_pct", "close", "prc", "source"]].to_string())

# PATCH_OUT=path: for vendor rows (tiingo, wiki, yahoo) inside the window, a replace_close patch
# wherever our close sits more than 0.4% from CRSP's close on the row's own basis (a 21-day rolling
# median of close / |PRC|, so a Yahoo series' spin-off factor is followed). The pre-1996 tier's
# prices are CRSP's: a vendor close that disagrees is put on CRSP's (added 2026-10-15).
po = os.environ.get("PATCH_OUT")
if po:
    v = x[x.source.astype(str).isin(["tiingo", "wiki", "yahoo"])].dropna(subset=["close", "prc"]).sort_values(["label", "date"]).copy()
    v["rt"] = v.close / v.prc.abs()
    v["b"] = v.groupby("label").rt.transform(lambda s: s.rolling(21, center=True, min_periods=5).median())
    v["new"] = v.prc.abs() * v.b
    v["dev"] = v.close / v.new - 1
    pv = v[v.dev.abs() > 0.004]
    with open(po, "w") as f:
        f.write("ticker,date,open,high,low,close,volume,dividends,stock_splits,source,action,note,max_move\n")
        for r in pv.itertuples():
            f.write(f'{r.label},{r.date.date()},,,,{r.new:.6f},,0.0,1,crsp-patch,replace_close,"{r.date.date()}: the {r.source} close {r.close:g} is {r.dev * 100:+.2f}% off CRSP\'s {abs(r.prc):g} (permno {r.permno}) on the series\' basis ({r.b:.6f}); the pre-1996 tier\'s prices are CRSP\'s (2026-10-15)",0.1\n')
    print(f"\n{len(pv)} vendor closes put on CRSP's -> {po}; by label: {pv.groupby('label').size().sort_values(ascending=False).head(30).to_dict()}")
