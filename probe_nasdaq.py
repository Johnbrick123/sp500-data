"""Probe: why does Nasdaq's CRH history disagree with ours? Prints side-by-side rows. Read-only."""
import json, sys, urllib.request
import pandas as pd
from datetime import date
tk = sys.argv[1] if len(sys.argv) > 1 else "CRH"
H = {"User-Agent": "Mozilla/5.0 (data verification)"}
u = f"https://api.nasdaq.com/api/quote/{tk}/historical?assetclass=stocks&fromdate=2016-09-01&todate={date.today():%Y-%m-%d}&limit=9999"
d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
rows = d.get("data", {}).get("tradesTable", {}).get("rows") or []
n = pd.DataFrame(rows); n["date"] = pd.to_datetime(n["date"]); 
for c in ("close", "open", "high", "low"):
    n[c] = pd.to_numeric(n[c].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")
n = n.sort_values("date")
o = pd.read_parquet("data/prices.parquet")
o = o[o.ticker == tk].copy(); o["date"] = pd.to_datetime(o["date"])
m = o.merge(n[["date", "close", "volume"]], on="date", suffixes=("_o", "_n")).sort_values("date")
m["r_o"] = m.close_o.pct_change(); m["r_n"] = m.close_n.pct_change(); m["diff"] = (m.r_o - m.r_n).abs()
print(f"{tk}: nasdaq {len(n)} rows {n.date.min().date()}..{n.date.max().date()}; merged {len(m)}")
print("share of days with |diff|>0.5pp by year:"); print((m.groupby(m.date.dt.year)["diff"].apply(lambda s: (s > 0.005).mean() * 100)).round(1).to_string())
print("\nlevel ratio ours/nasdaq by year (median):"); print((m.close_o / m.close_n).groupby(m.date.dt.year).median().round(4).to_string())
print("\nsample rows:"); print(m[["date", "close_o", "close_n", "r_o", "r_n", "diff"]].iloc[::400].to_string())
print(m[m["diff"] > 0.05][["date", "close_o", "close_n", "r_o", "r_n"]].head(12).to_string())
