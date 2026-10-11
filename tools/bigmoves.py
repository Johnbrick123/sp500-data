"""Members' one-day total returns (adj_close) beyond a threshold in a year, with
the event fields of that row. Usage: bigmoves.py DIR YEAR [THRESH]"""
import sys
import pandas as pd
D, Y = sys.argv[1], int(sys.argv[2]); TH = float(sys.argv[3]) if len(sys.argv) > 3 else 0.10
p = pd.read_parquet(f"{D}/prices.parquet", columns=["ticker","date","close","adj_close","dividends","stock_splits","source"])
p["date"] = pd.to_datetime(p["date"])
iv = pd.read_parquet(f"{D}/membership_intervals.parquet"); iv["start"]=pd.to_datetime(iv["start"]); iv["end"]=pd.to_datetime(iv["end"])
lo, hi = pd.Timestamp(f"{Y}-01-01"), pd.Timestamp(f"{Y}-12-31")
mem = iv[(iv.start <= hi) & (iv["end"].isna() | (iv["end"] > lo))]
out = []
for t, g in p[p.ticker.isin(set(mem.ticker))].groupby("ticker"):
    g = g.sort_values("date").reset_index(drop=True)
    g["r"] = g.adj_close.pct_change(); g["pr"] = g.close.pct_change()
    w = mem[mem.ticker == t]
    inm = lambda d: any((a <= d) and (pd.isna(b) or d < b) for a, b in zip(w.start, w["end"]))
    for i in g.index[(g.date >= lo) & (g.date <= hi) & (g.r.abs() >= TH)]:
        d = g.date[i]
        if not inm(d): continue
        out.append((t, str(d.date()), round(g.r[i]*100, 2), round(g.pr[i]*100, 2), g.close[i-1] if i else None, g.close[i], g.dividends[i], g.stock_splits[i], g.source[i]))
o = pd.DataFrame(out, columns=["ticker","date","tr%","pr%","prev_close","close","div","split","src"]).sort_values("date")
pd.set_option("display.width", 220); print(o.to_string(index=False))
