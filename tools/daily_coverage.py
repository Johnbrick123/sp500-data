"""Strict daily coverage: every index member on every trading day must have a
price row that day (or the day falls within 7 days after its last trade - a
deal that closed before S&P removed the name). Usage: daily_coverage.py DIR Y0 Y1"""
import sys
import pandas as pd
D, y0, y1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
p = pd.read_parquet(f"{D}/prices.parquet", columns=["ticker", "date", "close", "volume", "source"])
p["date"] = pd.to_datetime(p["date"])
iv = pd.read_parquet(f"{D}/membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
days_by_tk = p.groupby("ticker").date.apply(set).to_dict()
last = p.groupby("ticker").date.max()
cal = sorted(p[p.ticker == "SPY"].date)
for y in range(y0, y1 + 1):
    days = [d for d in cal if d.year == y]
    member_days = miss = carried = 0
    names = set(); misses = []
    for d in days:
        mem = iv[(iv.start <= d) & (iv["end"].isna() | (iv["end"] > d))].ticker.unique()
        for t in mem:
            names.add(t); member_days += 1
            if d in days_by_tk.get(t, ()):
                continue
            if t in last.index and last[t] < d <= last[t] + pd.Timedelta(days=7):
                carried += 1; continue
            miss += 1; misses.append((t, str(d.date())))
    per = pd.Series([m[0] for m in misses]).value_counts().head(10).to_dict() if misses else {}
    print(f"{y}: {len(days)} trading days, {len(names)} members, {member_days:,} member-days, "
          f"missing {miss} ({(1 - miss / max(member_days, 1)) * 100:.3f}% covered), carried after last trade {carried}"
          + (f"  worst: {per}" if per else ""))
