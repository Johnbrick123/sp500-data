"""Index-trade volume check for a year's membership changes: index funds trade a
name at the close BEFORE its effective date, so each add and removal should show
its biggest volume (many times the prior two months' median) on the trading day
before our interval starts/ends. A spike on a different day flags a wrong date
(2014: IGT and Cimarex spiked on 6/20, so the swap was effective 6/23, not 6/20).
Share-class lines added by a stock dividend (GOOG, DISCK 2014) and spin-offs added at
a zero price show no spike: the index receives them, it does not buy them.
Usage: volume_spikes.py DIR YEAR   (DIR holds prices.parquet and membership_intervals.parquet)"""
import sys
import pandas as pd
D, Y = sys.argv[1], int(sys.argv[2])
p = pd.read_parquet(f"{D}/prices.parquet", columns=["ticker", "date", "volume"])
p["date"] = pd.to_datetime(p["date"])
iv = pd.read_parquet(f"{D}/membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
ev = [(r.start, "add", r.ticker) for r in iv.itertuples() if r.start.year == Y] + \
     [(r.end, "rem", r.ticker) for r in iv.itertuples() if pd.notna(r.end) and r.end.year == Y]
cal = sorted(p[p.ticker == "SPY"].date)
for d, act, t in sorted(ev):
    g = p[p.ticker == t].set_index("date").sort_index()
    prev = max(x for x in cal if x < d)
    if g.empty:
        print(f"{d.date()} {act} {t:14s} no prices"); continue
    med = g.loc[d - pd.Timedelta(days=60):d - pd.Timedelta(days=14)].volume.median()
    w = g.loc[d - pd.Timedelta(days=12):d + pd.Timedelta(days=6)].volume
    if w.empty or not med:
        print(f"{d.date()} {act} {t:14s} no volume around the date"); continue
    top = w.idxmax()
    flag = "" if top == prev else f"   <-- peak is not the day before ({prev.date()})"
    print(f"{d.date()} {act} {t:14s} peak {top.date()} {w.max() / med:5.1f}x{flag}")
