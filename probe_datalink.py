"""
Round 2: is SHARADAR / QUOTEMEDIA access FULL or a free SAMPLE?
  1. Page through SHARADAR/TICKERS for delisted companies; count how many
     this key can see (full Sharadar lists ~10,000+ delisted US tickers).
  2. Try price history for the first few delisted tickers it returns.
  3. Same idea for QUOTEMEDIA via its own ticker table.
Read-only. Writes data/datalink_probe.txt.
"""
import json, os, urllib.request, urllib.error
KEY = os.environ["NASDAQ_DATA_LINK_KEY"]
B = "https://data.nasdaq.com/api/v3/datatables"
out = []
def get(path):
    sep = "&" if "?" in path else "?"
    return json.load(urllib.request.urlopen(f"{B}/{path}{sep}api_key={KEY}", timeout=60))

def page_all(path, cap=60):
    rows, cols, cur = [], None, None
    for _ in range(cap):
        p = path + (f"&qopts.cursor_id={cur}" if cur else "")
        d = get(p); dt = d["datatable"]; cols = [c["name"] for c in dt["columns"]]
        rows += dt["data"]; cur = (d.get("meta") or {}).get("next_cursor_id")
        if not cur: break
    return cols, rows

try:
    cols, rows = page_all("SHARADAR/TICKERS.json?table=SEP&isdelisted=Y&qopts.per_page=10000")
    out.append(f"SHARADAR delisted tickers visible to this key: {len(rows)}")
    i_t = cols.index("ticker"); i_n = cols.index("name")
    i_f = cols.index("firstpricedate") if "firstpricedate" in cols else None
    i_l = cols.index("lastpricedate") if "lastpricedate" in cols else None
    sample = rows[:8]
    for r in sample:
        out.append(f"   {r[i_t]:10s} {str(r[i_n])[:30]:30s} {r[i_f] if i_f is not None else ''} -> {r[i_l] if i_l is not None else ''}")
    for r in rows[:3]:
        t = r[i_t]
        c2, p = page_all(f"SHARADAR/SEP.json?ticker={t}&qopts.per_page=10000", cap=3)
        out.append(f"   SEP price rows for {t}: {len(p)}")
    # known S&P 500 failures, Sharadar-style tickers
    for t in ["LEHMQ", "WAMUQ", "ENRNQ", "WCOEQ", "BSC", "MER", "WB"]:
        c2, p = page_all(f"SHARADAR/SEP.json?ticker={t}&qopts.per_page=10000", cap=3)
        out.append(f"   SEP rows for {t}: {len(p)}")
except urllib.error.HTTPError as e:
    out.append(f"SHARADAR HTTP {e.code}: {e.read().decode(errors='ignore')[:150]}")
except Exception as e:
    out.append(f"SHARADAR error {type(e).__name__}: {e}")

try:
    cols, rows = page_all("SHARADAR/TICKERS.json?table=SEP&qopts.per_page=10000")
    out.append(f"\nSHARADAR ALL tickers visible (live+dead): {len(rows)}")
except Exception as e:
    out.append(f"SHARADAR all-tickers error {type(e).__name__}")

try:
    cols, rows = page_all("QUOTEMEDIA/TICKERS.json?qopts.per_page=10000", cap=20)
    out.append(f"\nQUOTEMEDIA tickers visible: {len(rows)}")
except urllib.error.HTTPError as e:
    out.append(f"\nQUOTEMEDIA/TICKERS HTTP {e.code}: {e.read().decode(errors='ignore')[:150]}")
except Exception as e:
    out.append(f"\nQUOTEMEDIA error {type(e).__name__}: {e}")

os.makedirs("data", exist_ok=True)
open("data/datalink_probe.txt", "w").write("\n".join(out)); print("\n".join(out))
