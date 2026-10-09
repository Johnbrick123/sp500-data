"""Probe: find companies in Tiingo's directory by NAME fragment and test whether
their permaTicker serves prices. Usage: python probe_directory.py "IHS MARKIT" "DISCOVERY" ...  -> data/directory_probe.txt"""
import json, os, sys, time, urllib.request
import pandas as pd
KEY = os.environ["TIINGO_API_KEY"].strip()
out = []
def say(s): print(s, flush=True); out.append(s)
meta = json.load(urllib.request.urlopen(f"https://api.tiingo.com/tiingo/fundamentals/meta?token={KEY}", timeout=90))
calls = 0
for frag in sys.argv[1:]:
    hits = [m for m in meta if frag.upper() in str(m.get("name", "")).upper()]
    say(f"== {frag}: {len(hits)} directory entries")
    for m in hits[:8]:
        pid = m.get("permaTicker")
        line = f"  {str(m.get('ticker')):8s} {str(m.get('name'))[:45]:45s} perma={pid} active={m.get('isActive')} exch={m.get('exchange')}"
        if pid and calls < 40:
            try:
                rows = json.load(urllib.request.urlopen(f"https://api.tiingo.com/tiingo/daily/{pid}/prices?startDate=1990-01-01&format=json&token={KEY}", timeout=60)); calls += 1
                if rows:
                    line += f" -> {len(rows)} rows {rows[0]['date'][:10]}..{rows[-1]['date'][:10]} last close {rows[-1]['close']}"
                else:
                    line += " -> 0 rows"
            except Exception as e:
                line += f" -> {type(e).__name__}"
            time.sleep(0.5)
        say(line)
os.makedirs("data", exist_ok=True); open("data/directory_probe.txt", "w").write("\n".join(out))
