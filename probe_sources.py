"""
Probe: which free web sources serve DAILY history for dead S&P 500 members
that Tiingo, WIKI and Yahoo all lack? Runs on a GitHub runner because the
cloud workspace cannot reach these hosts. Read-only; writes data/sources_probe.txt.
"""
import io, json, re, time, urllib.request, urllib.parse
import pandas as pd
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      "Accept": "text/csv,text/plain,application/json,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}
TEST = ["BMC", "LEH", "LEHMQ", "BSC", "CFC", "MOLX", "LIFE", "IGT", "BGEN", "EOP", "DG", "APCC", "CEPH", "AW", "CMVT", "WAMUQ", "ENRNQ"]
out = []
def say(s): print(s, flush=True); out.append(s)
def get(url, timeout=40):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()

say("== Stooq (https://stooq.com/q/d/l/?s=TICKER.us&i=d)")
for t in TEST:
    try:
        b = get(f"https://stooq.com/q/d/l/?s={t.lower()}.us&i=d")
        txt = b.decode("utf-8", "replace")
        if txt.startswith("Date,"):
            df = pd.read_csv(io.StringIO(txt))
            say(f"  {t:7s} {len(df):6,} rows {df.Date.min()}..{df.Date.max()}  last close {df.Close.iloc[-1]}")
        else:
            import html as _h
            body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", txt, flags=re.S)
            body = _h.unescape(re.sub(r"<[^>]+>", " ", body)); body = re.sub(r"\s+", " ", body).strip()
            say(f"  {t:7s} -> HTML: {body[:300]!r}")
            if t == TEST[1]: break
    except Exception as e:
        say(f"  {t:7s} error {type(e).__name__}: {str(e)[:80]}")
    time.sleep(1.5)

say("\n== Wayback Machine: archived Yahoo table.csv downloads (CDX index)")
for t in TEST[:8]:
    for host in ("ichart.finance.yahoo.com/table.csv?s=%s" % t, "real-chart.finance.yahoo.com/table.csv?s=%s" % t, "ichart.yahoo.com/table.csv?s=%s" % t):
        try:
            q = f"https://web.archive.org/cdx/search/cdx?url={urllib.parse.quote(host + '*', safe='')}&output=json&limit=50&filter=statuscode:200"
            j = json.loads(get(q).decode())
            rows = j[1:] if j else []
            if rows:
                best = max(rows, key=lambda r: int(r[6]) if r[6].isdigit() else 0)
                say(f"  {t:7s} {host.split('/')[0]:32s} {len(rows)} captures; largest {int(best[6]):,} bytes at {best[1]} -> {best[2][:90]}")
                break
        except Exception as e:
            say(f"  {t:7s} {host.split('/')[0]:32s} error {type(e).__name__}: {str(e)[:60]}")
        time.sleep(1)

say("\n== Wayback: fetch the largest archived CSV for one name, to see what it holds")
try:
    q = "https://web.archive.org/cdx/search/cdx?url=" + urllib.parse.quote("ichart.finance.yahoo.com/table.csv?s=BMC*", safe="") + "&output=json&limit=200&filter=statuscode:200"
    j = json.loads(get(q).decode()); rows = j[1:]
    if rows:
        best = max(rows, key=lambda r: int(r[6]) if r[6].isdigit() else 0)
        txt = get(f"https://web.archive.org/web/{best[1]}id_/{best[2]}").decode("utf-8", "replace")
        df = pd.read_csv(io.StringIO(txt))
        say(f"  BMC via Wayback: {len(df):,} rows {df.iloc[-1, 0]}..{df.iloc[0, 0]}; columns {list(df.columns)}")
    else:
        say("  no BMC captures")
except Exception as e:
    say(f"  error {type(e).__name__}: {str(e)[:100]}")

say("\n== Barchart delisted symbols (page reachable?)")
for sym in ("LHHMQ", "BSC", "BMC"):
    try:
        b = get(f"https://www.barchart.com/stocks/quotes/{sym}/price-history")
        say(f"  {sym}: HTTP ok, {len(b):,} bytes, mentions 'price-history' table: {'historical' in b.decode('utf-8','replace').lower()}")
    except Exception as e:
        say(f"  {sym}: {type(e).__name__}: {str(e)[:80]}")

say("\n== Macrotrends / companiesmarketcap (quick reachability)")
for u in ("https://companiesmarketcap.com/lehman-brothers/stock-price-history/",):
    try:
        b = get(u); say(f"  {u}: {len(b):,} bytes")
    except Exception as e:
        say(f"  {u}: {type(e).__name__}")
open("data/sources_probe.txt", "w").write("\n".join(out))
