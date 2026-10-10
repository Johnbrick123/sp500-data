"""Probe: Nasdaq's dividend history for a few tickers (ex-date, type, cash amount,
declaration/record/payment dates), to see how far back it goes and whether the
amounts are as declared or split-adjusted.
Usage: python probe_dividends.py "T,AAPL,GE" -> data/dividends_probe.txt"""
import json, os, sys, time, urllib.request
import pandas as pd

H = {"User-Agent": "Mozilla/5.0 (data verification)", "Accept": "application/json"}
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
pd.set_option("display.width", 200)
for tk in [x.strip() for x in sys.argv[1].split(",") if x.strip()]:
    u = f"https://api.nasdaq.com/api/quote/{tk}/dividends?assetclass=stocks"
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
        rows = (((d.get("data") or {}).get("dividends") or {}).get("rows")) or []
        say(f"\n===== {tk}: {len(rows)} rows; header keys {list((d.get('data') or {}).keys())[:8]}")
        if not rows:
            say("  raw: " + json.dumps(d)[:600])
        if rows:
            df = pd.DataFrame(rows)
            say(df.head(6).to_string(index=False))
            say("  ...")
            say(df.tail(6).to_string(index=False))
    except Exception as e:
        say(f"\n===== {tk}: {type(e).__name__} {str(e)[:150]}")
    time.sleep(1)
os.makedirs("data", exist_ok=True)
open("data/dividends_probe.txt", "w").write("\n".join(out))
