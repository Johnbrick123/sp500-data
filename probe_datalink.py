"""
Which Nasdaq Data Link databases can this account actually read?
Asks each candidate for one dead company (Lehman) and one live one (Apple),
and reports HTTP status and rows. Writes data/datalink_probe.txt. Read-only.
"""
import json, os, urllib.request, urllib.error
KEY = os.environ["NASDAQ_DATA_LINK_KEY"]
B = "https://data.nasdaq.com/api/v3"
tests = [
    ("SHARADAR/SEP  daily prices incl. delisted", "datatables/SHARADAR/SEP.json?ticker={t}&qopts.per_page=5"),
    ("SHARADAR/TICKERS  listing metadata",        "datatables/SHARADAR/TICKERS.json?ticker={t}&table=SEP"),
    ("SHARADAR/ACTIONS  corporate actions",        "datatables/SHARADAR/ACTIONS.json?ticker={t}&qopts.per_page=5"),
    ("QUOTEMEDIA/PRICES",                         "datatables/QUOTEMEDIA/PRICES.json?ticker={t}&qopts.per_page=5"),
    ("WIKI/PRICES (baseline)",                    "datatables/WIKI/PRICES.json?ticker={t}&qopts.per_page=5"),
    ("EOD dataset",                               "datasets/EOD/{t}.json?rows=5"),
    ("ZACKS/HDM",                                 "datatables/ZACKS/HDM.json?ticker={t}&qopts.per_page=5"),
]
out = []
for name, path in tests:
    for t in ["LEH", "AAPL"]:
        url = f"{B}/{path.format(t=t)}&api_key={KEY}" if "?" in path else f"{B}/{path.format(t=t)}?api_key={KEY}"
        try:
            d = json.load(urllib.request.urlopen(url, timeout=40))
            n = len((d.get("datatable") or {}).get("data") or (d.get("dataset") or {}).get("data") or [])
            out.append(f"{name:45s} {t:5s} OK   rows={n}")
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="ignore")[:90].replace("\n", " ")
            out.append(f"{name:45s} {t:5s} HTTP {e.code}  {msg}")
        except Exception as e:
            out.append(f"{name:45s} {t:5s} {type(e).__name__}")
os.makedirs("data", exist_ok=True)
open("data/datalink_probe.txt", "w").write("\n".join(out))
print("\n".join(out))
