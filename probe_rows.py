"""Probe: print one ticker's daily rows for a date window from Tiingo and from
Nasdaq side by side, to hand-verify a short patch. Usage: python probe_rows.py TICKER START END"""
import json, os, sys, urllib.request
import pandas as pd
tk, start, end = sys.argv[1], sys.argv[2], sys.argv[3]
key = os.environ.get("TIINGO_API_KEY", "").strip()
out = []
def say(s): print(s, flush=True); out.append(str(s))
try:
    t = json.load(urllib.request.urlopen(f"https://api.tiingo.com/tiingo/daily/{tk}/prices?startDate={start}&endDate={end}&format=json&token={key}", timeout=60))
    t = pd.DataFrame(t); t["date"] = pd.to_datetime(t["date"]).dt.date
    say("TIINGO\n" + t[["date", "open", "high", "low", "close", "volume", "divCash", "splitFactor"]].to_string(index=False))
except Exception as e:
    say(f"TIINGO error {type(e).__name__} {str(e)[:120]}")
try:
    H = {"User-Agent": "Mozilla/5.0 (data verification)"}
    d = json.load(urllib.request.urlopen(urllib.request.Request(f"https://api.nasdaq.com/api/quote/{tk}/historical?assetclass=stocks&fromdate={start}&todate={end}&limit=100", headers=H), timeout=30))
    rows = (d.get("data") or {}).get("tradesTable", {}).get("rows") or []
    say("NASDAQ\n" + pd.DataFrame(rows).to_string(index=False) if rows else f"NASDAQ: no rows ({str(d)[:200]})")
except Exception as e:
    say(f"NASDAQ error {type(e).__name__} {str(e)[:120]}")
os.makedirs("data", exist_ok=True); open("data/rows_probe.txt", "w").write("\n".join(out))
