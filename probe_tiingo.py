"""
Can this Tiingo account read recycled-ticker history by permaTicker?
For each target: search by company name, take the DELISTED match's permaTicker,
then request its daily prices by permaTicker. Read-only.
Writes data/tiingo_perma_probe.txt.
"""
import json, os, urllib.request, urllib.error, urllib.parse
KEY = os.environ["TIINGO_API_KEY"]
H = {"Content-Type": "application/json"}
targets = [("S", "Sprint"), ("STI", "SunTrust"), ("APC", "Anadarko"), ("NFX", "Newfield"),
           ("INFO", "IHS Markit"), ("DOW", "Dow Chemical"), ("CEG", "Constellation Energy"), ("NYX", "NYSE Euronext")]
def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40))
out = []
for tk, name in targets:
    try:
        res = get(f"https://api.tiingo.com/tiingo/utilities/search?query={urllib.parse.quote(name)}&token={KEY}")
        cands = [r for r in res if str(r.get("ticker", "")).upper() == tk]
        if not cands:
            out.append(f"{tk:5s} {name:22s} search: no ticker match ({len(res)} results)"); continue
        for c in cands:
            pt = c.get("permaTicker"); active = c.get("isActive")
            line = f"{tk:5s} {name:22s} permaTicker={pt} active={active} name={str(c.get('name'))[:28]}"
            if pt:
                try:
                    p = get(f"https://api.tiingo.com/tiingo/daily/{pt}/prices?startDate=1995-01-01&token={KEY}")
                    if isinstance(p, list) and p:
                        line += f" -> PRICES {len(p)} rows {p[0]['date'][:10]}..{p[-1]['date'][:10]} close {p[-1]['close']}"
                    else:
                        line += f" -> prices: {str(p)[:80]}"
                except urllib.error.HTTPError as e:
                    line += f" -> prices HTTP {e.code} {e.read().decode(errors='ignore')[:80]}"
            out.append(line)
    except urllib.error.HTTPError as e:
        out.append(f"{tk:5s} search HTTP {e.code} {e.read().decode(errors='ignore')[:80]}")
    except Exception as e:
        out.append(f"{tk:5s} error {type(e).__name__}: {e}")
os.makedirs("data", exist_ok=True)
open("data/tiingo_perma_probe.txt", "w").write("\n".join(out)); print("\n".join(out))
