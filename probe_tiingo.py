"""
Does Tiingo expose a directory of permaTickers including DEAD companies?
Tries the fundamentals meta endpoint, counts inactive entries, and looks for
our 8 targets by ticker. Then prices-by-permaTicker for any found. Read-only.
"""
import json, os, urllib.request, urllib.error
KEY = os.environ["TIINGO_API_KEY"]
H = {"Content-Type": "application/json"}
def get(u): return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=120))
out = []
try:
    meta = get(f"https://api.tiingo.com/tiingo/fundamentals/meta?token={KEY}")
    out.append(f"fundamentals/meta: {len(meta)} entries; inactive: {sum(1 for m in meta if not m.get('isActive'))}")
    if meta: out.append("fields: " + ", ".join(sorted(meta[0].keys())))
    want = {"S": 2020, "STI": 2019, "APC": 2019, "NFX": 2019, "INFO": 2022, "DOW": 2017, "CEG": 2013, "NYX": 2013}
    for m in meta:
        t = str(m.get("ticker", "")).upper()
        if t in want:
            out.append(f"  {t:5s} {str(m.get('name'))[:30]:30s} perma={m.get('permaTicker')} active={m.get('isActive')} "
                       f"first={str(m.get('statementLastUpdated') or m.get('dailyLastUpdated'))[:10]}")
            if not m.get("isActive") and m.get("permaTicker"):
                try:
                    p = get(f"https://api.tiingo.com/tiingo/daily/{m['permaTicker']}/prices?startDate=1995-01-01&token={KEY}")
                    out.append(f"      -> PRICES {len(p)} rows {p[0]['date'][:10]}..{p[-1]['date'][:10]} close {p[-1]['close']}" if p else "      -> no rows")
                except urllib.error.HTTPError as e:
                    out.append(f"      -> HTTP {e.code} {e.read().decode(errors='ignore')[:80]}")
except urllib.error.HTTPError as e:
    out.append(f"fundamentals/meta HTTP {e.code}: {e.read().decode(errors='ignore')[:200]}")
except Exception as e:
    out.append(f"error {type(e).__name__}: {e}")
os.makedirs("data", exist_ok=True)
open("data/tiingo_perma_probe.txt", "w").write("\n".join(out)); print("\n".join(out))
