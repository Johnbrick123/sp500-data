"""
Round 3: Sharadar / QuoteMedia - can this key read PRICES for dead companies,
or only the ticker list? Finds famous dead S&P 500 members by NAME in the
ticker table, then requests their daily prices. Read-only.
"""
import json, os, urllib.request, urllib.error
KEY = os.environ["NASDAQ_DATA_LINK_KEY"]
B = "https://data.nasdaq.com/api/v3/datatables"
out = []
def page_all(path, cap=40):
    rows, cols, cur = [], None, None
    for _ in range(cap):
        p = path + (f"&qopts.cursor_id={cur}" if cur else "")
        d = json.load(urllib.request.urlopen(f"{B}/{p}&api_key={KEY}", timeout=90))
        dt = d["datatable"]; cols = [c["name"] for c in dt["columns"]]
        rows += dt["data"]; cur = (d.get("meta") or {}).get("next_cursor_id")
        if not cur: break
    return cols, rows
targets = ["LEHMAN", "WASHINGTON MUTUAL", "ENRON", "WORLDCOM", "BEAR STEARNS",
           "MERRILL LYNCH", "WACHOVIA", "COUNTRYWIDE", "CIRCUIT CITY", "GENERAL MOTORS CORP"]
for table, pricetab in [("SHARADAR/TICKERS.json?table=SEP&qopts.per_page=10000", "SHARADAR/SEP"),
                        ("QUOTEMEDIA/TICKERS.json?qopts.per_page=10000", "QUOTEMEDIA/PRICES")]:
    try:
        cols, rows = page_all(table)
        it = cols.index("ticker")
        iname = next(i for i, c in enumerate(cols) if c in ("name", "company_name", "companyname"))
        out.append(f"\n{pricetab}: {len(rows)} tickers listed")
        for tgt in targets:
            hits = [r for r in rows if tgt in str(r[iname]).upper()][:2]
            for r in hits:
                t = r[it]
                try:
                    _, p = page_all(f"{pricetab}.json?ticker={t}&qopts.per_page=10000", cap=3)
                    out.append(f"   {tgt[:22]:22s} -> {t:9s} price rows: {len(p)}")
                except urllib.error.HTTPError as e:
                    out.append(f"   {tgt[:22]:22s} -> {t:9s} HTTP {e.code}")
            if not hits:
                out.append(f"   {tgt[:22]:22s} -> not in list")
    except Exception as e:
        out.append(f"{pricetab}: error {type(e).__name__}: {str(e)[:120]}")
os.makedirs("data", exist_ok=True)
open("data/datalink_probe.txt", "w").write("\n".join(out)); print("\n".join(out))
