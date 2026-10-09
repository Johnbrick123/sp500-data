"""Probe: raw daily rows for several tickers/windows at once, from Tiingo (raw
close, dividends, split factor) and Nasdaq (raw close), side by side. Used to
hand-verify event days such as spin-offs, where the published closes on both
sides of the ex-date are needed.
Usage: python probe_batch.py "RTX:2020-03-30:2020-04-08,CARR:2020-04-01:2020-04-08"
-> data/batch_probe.txt"""
import json, os, sys, time, urllib.request
from datetime import date
import pandas as pd

key = os.environ.get("TIINGO_API_KEY", "").strip()
H = {"User-Agent": "Mozilla/5.0 (data verification)"}
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
pd.set_option("display.width", 220)

for item in [x.strip() for x in sys.argv[1].split(",") if x.strip()]:
    tk, start, end = item.split(":")
    say(f"\n===== {tk} {start}..{end}")
    t = pd.DataFrame()
    try:
        rows = json.load(urllib.request.urlopen(
            f"https://api.tiingo.com/tiingo/daily/{tk}/prices?startDate={start}&endDate={end}&format=json&token={key}", timeout=60))
        t = pd.DataFrame(rows)
        if len(t):
            t["date"] = pd.to_datetime(t["date"]).dt.tz_localize(None)
            t = t[["date", "open", "high", "low", "close", "volume", "divCash", "splitFactor"]]
    except Exception as e:
        say(f"TIINGO error {type(e).__name__} {str(e)[:120]}")
    n = pd.DataFrame()
    try:
        # the nightly's request shape: from mid-December of the year before to today
        y = int(start[:4])
        u = (f"https://api.nasdaq.com/api/quote/{tk}/historical?assetclass=stocks"
             f"&fromdate={y - 1}-12-15&todate={date.today():%Y-%m-%d}&limit=9999")
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
        rows = (d.get("data") or {}).get("tradesTable", {}).get("rows") or []
        if rows:
            n = pd.DataFrame(rows)
            n["date"] = pd.to_datetime(n["date"])
            for c in ("open", "high", "low", "close"):
                n[c] = pd.to_numeric(n[c].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")
            n["volume"] = pd.to_numeric(n["volume"].astype(str).str.replace(",", ""), errors="coerce")
            n = n[(n.date >= start) & (n.date <= end)][["date", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        say(f"NASDAQ error {type(e).__name__} {str(e)[:120]}")
    if t.empty and n.empty:
        say("  no rows from either source"); continue
    if t.empty:
        m = n.add_suffix("_nas").rename(columns={"date_nas": "date"})
    elif n.empty:
        m = t
    else:
        m = t.merge(n.add_suffix("_nas").rename(columns={"date_nas": "date"}), on="date", how="outer")
    say(m.sort_values("date").to_string(index=False))
    time.sleep(1)
os.makedirs("data", exist_ok=True)
open("data/batch_probe.txt", "w").write("\n".join(out))
