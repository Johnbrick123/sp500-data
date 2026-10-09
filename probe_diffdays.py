"""Probe: list the days in YEAR where our daily return for TICKER differs from
Nasdaq's by more than 0.5pp, with Tiingo's close as a third source.
Usage: python probe_diffdays.py TICKER YEAR -> data/diffdays_TICKER_YEAR.txt"""
import json, os, sys, urllib.request
from datetime import date
import pandas as pd
tk, year = sys.argv[1], int(sys.argv[2])
H = {"User-Agent": "Mozilla/5.0 (data verification)"}
out = []
def say(s): print(s, flush=True); out.append(str(s))
d = json.load(urllib.request.urlopen(urllib.request.Request(
    f"https://api.nasdaq.com/api/quote/{tk}/historical?assetclass=stocks&fromdate={year-1}-12-15&todate={date.today():%Y-%m-%d}&limit=9999", headers=H), timeout=30))
n = pd.DataFrame(d["data"]["tradesTable"]["rows"]); n["date"] = pd.to_datetime(n["date"])
for c in ("open", "high", "low", "close"):
    n[c] = pd.to_numeric(n[c].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")
n["volume"] = pd.to_numeric(n["volume"].astype(str).str.replace(",", ""), errors="coerce")
key = os.environ.get("TIINGO_API_KEY", "").strip()
t = pd.DataFrame(json.load(urllib.request.urlopen(f"https://api.tiingo.com/tiingo/daily/{tk}/prices?startDate={year-1}-12-15&endDate={year}-12-31&format=json&token={key}", timeout=60)))
t["date"] = pd.to_datetime(t["date"]).dt.tz_localize(None)
o = pd.read_parquet("data/prices.parquet"); o = o[o.ticker == tk].copy(); o["date"] = pd.to_datetime(o["date"])
m = o[["date", "open", "high", "low", "close", "volume"]].merge(n[["date", "open", "high", "low", "close", "volume"]], on="date", suffixes=("", "_nas"))
m = m.merge(t[["date", "close", "volume"]].rename(columns={"close": "close_tii", "volume": "volume_tii"}), on="date", how="left").sort_values("date")
m["r_ours"] = m.close.pct_change(); m["r_nas"] = m.close_nas.pct_change(); m["r_tii"] = m.close_tii.pct_change()
m = m[m.date.dt.year == year]
bad = m[(m.r_ours - m.r_nas).abs() > 0.005]
say(f"{tk} {year}: {len(m)} days compared, {len(bad)} differ >0.5pp")
pd.set_option("display.width", 250)
say(bad[["date", "close", "close_nas", "close_tii", "r_ours", "r_nas", "r_tii", "volume", "volume_nas"]].round(4).to_string(index=False))
idx = []
for d0 in bad.date:
    i = m.index.get_loc(m.index[m.date == d0][0]); idx += list(range(max(0, i - 1), min(len(m), i + 2)))
say("\ncontext rows:\n" + m.iloc[sorted(set(idx))][["date", "open", "high", "low", "close", "open_nas", "high_nas", "low_nas", "close_nas", "close_tii"]].round(3).to_string(index=False))
os.makedirs("data", exist_ok=True); open(f"data/diffdays_{tk}_{year}.txt", "w").write("\n".join(out))
