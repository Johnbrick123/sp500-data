"""Heuristic dividend scan over member-days: gaps in a regular schedule, extra
interleaved streams, and amounts out of line with their neighbours."""
import sys
import numpy as np, pandas as pd
D, Y0, Y1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
p = pd.read_parquet(f"{D}/prices.parquet", columns=["ticker","date","close","dividends","stock_splits","source"]); p["date"]=pd.to_datetime(p.date)
iv = pd.read_parquet(f"{D}/membership_intervals.parquet"); iv["start"]=pd.to_datetime(iv.start); iv["end"]=pd.to_datetime(iv.end)
out = []
for t, g in p.groupby("ticker"):
    m = iv[iv.ticker == t]
    if m.empty: continue
    g = g.sort_values("date").reset_index(drop=True)
    sp = g.stock_splits.fillna(0).replace(0, 1.0).astype(float).to_numpy()
    src = g.source.mode().iloc[0]
    later = np.append(np.cumprod(sp[::-1])[::-1][1:], 1.0) if src == "yahoo" else np.ones(len(g))
    g["raw"] = g.dividends.fillna(0) * later
    d = g[g.dividends.fillna(0) > 0][["date", "raw", "close"]].reset_index(drop=True)
    if len(d) < 6: continue
    def member(x): return any((a <= x) and (pd.isna(b) or x < b) for a, b in zip(m.start, m["end"]))
    gaps = d.date.diff().dt.days
    med = gaps.rolling(9, center=True, min_periods=4).median()
    for i in range(1, len(d)):
        x = d.date[i]
        if not (Y0 <= x.year <= Y1): continue
        # 1. a gap about twice the usual spacing (a skipped payment) while member
        if med[i] and 75 <= med[i] <= 100 and gaps[i] > 1.6 * med[i] and gaps[i] < 3.5 * med[i]:
            mid = d.date[i-1] + (x - d.date[i-1]) / 2
            if member(mid): out.append((t, src, str(mid.date()), "gap", f"{int(gaps[i])}d between {d.date[i-1].date()} and {x.date()} (usual {int(med[i])}d)"))
        # 2. interleaved stream: spacing much shorter than usual and amounts unlike neighbours
        if med[i] and med[i] >= 75 and gaps[i] < 0.5 * med[i] and member(x):
            out.append((t, src, str(x.date()), "extra", f"{int(gaps[i])}d after the previous ({d.raw[i-1]:g} -> {d.raw[i]:g})"))
        # 3. amount far from both neighbours (x10, x2, /2 ...)
        if 0 < i < len(d) - 1 and member(x):
            a, b, c = d.raw[i-1], d.raw[i], d.raw[i+1]
            if a > 0 and c > 0 and min(b / a, b / c) > 1.8 and max(a, c) / min(a, c) < 1.3 and b / d.close[i] < 0.05:
                out.append((t, src, str(x.date()), "too big", f"{a:g} / {b:g} / {c:g}"))
            if a > 0 and c > 0 and max(b / a, b / c) < 0.55 and max(a, c) / min(a, c) < 1.3:
                out.append((t, src, str(x.date()), "too small", f"{a:g} / {b:g} / {c:g}"))
df = pd.DataFrame(out, columns=["ticker","src","date","kind","detail"]).drop_duplicates()
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 2000); pd.set_option("display.max_colwidth", 90)
print(df.groupby("kind").size().to_dict())
print(df.sort_values(["date","ticker"]).to_string(index=False))
