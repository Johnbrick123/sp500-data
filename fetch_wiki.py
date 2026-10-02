"""
Step 2c: Recover DEAD index members from the Quandl/Nasdaq WIKI dataset.

WIKI is a public-domain, community-curated EOD file: ~3,200 US stocks,
1962-2018, INCLUDING companies that later died. It stopped updating in April
2018, so it is used only for history that ends before then - exactly the
1995-2015 window where Yahoo and Tiingo have nothing.

Because WIKI is community-curated, nothing is accepted on trust:
  1. CROSS-VALIDATION. For companies in BOTH WIKI and our Yahoo data, compare
     daily returns over 1998-2017. If WIKI disagrees with Yahoo on more than
     1% of days overall, abort and accept nothing.
  2. IDENTITY. Each recovered series must overlap the security's membership
     period, and if its label carries a -YYYYMM delisting month, must end
     within 18 months of it. Recycled symbols are rejected, not merged.
  3. Only names we have NO price file for are filled. WIKI never overwrites
     Yahoo or Tiingo data.

Needs NASDAQ_DATA_LINK_KEY (repo secret). Output: data/raw/<label>.parquet with
source="wiki" (unadjusted close + ex-dividend + split ratio, like Tiingo), and
data/wiki_report.txt.
"""
import io, json, os, re, sys, time, urllib.request, zipfile
from pathlib import Path
import numpy as np
import pandas as pd

KEY = os.environ.get("NASDAQ_DATA_LINK_KEY")
if not KEY:
    sys.exit("NASDAQ_DATA_LINK_KEY not set")

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
REPORT = ROOT / "data" / "wiki_report.txt"
SUFFIX = re.compile(r"-(\d{6})$")
lines = []


def say(s=""):
    print(s, flush=True); lines.append(str(s))


def download_wiki():
    """Bulk export: ask for the file, wait until it is ready, download the zip."""
    url = f"https://data.nasdaq.com/api/v3/datatables/WIKI/PRICES?qopts.export=true&api_key={KEY}"
    for attempt in range(30):
        meta = json.load(urllib.request.urlopen(url, timeout=60))
        f = meta["datatable_bulk_download"]["file"]
        if f.get("status", "").lower() == "fresh" and f.get("link"):
            say(f"bulk file ready ({f.get('data_snapshot_time', '?')})")
            data = urllib.request.urlopen(f["link"], timeout=900).read()
            say(f"downloaded {len(data)/1e6:.0f} MB")
            z = zipfile.ZipFile(io.BytesIO(data))
            return z.open(z.namelist()[0])
        say(f"  export status '{f.get('status')}', waiting..."); time.sleep(20)
    sys.exit("WIKI bulk export never became ready")


def main():
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    have = {p.stem for p in RAW.glob("*.parquet")}

    # label -> bare symbol, for every index member we have no prices for
    want = {}
    for t, g in iv.groupby("ticker"):
        if t not in have:
            want[t] = (SUFFIX.sub("", t).replace("-", "."), g.start.min(), g["end"].max())
    bare_needed = {v[0] for v in want.values()}

    # a validation sample: live names we already hold from Yahoo
    yahoo = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "source"])
    yahoo = yahoo[yahoo.source == "yahoo"]
    val_names = set(yahoo.ticker.unique())

    keep_cols = ["ticker", "date", "open", "high", "low", "close", "volume", "ex-dividend", "split_ratio"]
    parts, val_parts = [], []
    for chunk in pd.read_csv(download_wiki(), usecols=keep_cols, chunksize=1_000_000):
        tk = chunk.ticker.astype(str)
        parts.append(chunk[tk.isin(bare_needed)])
        val_parts.append(chunk[tk.str.replace(".", "-", regex=False).isin(val_names)])
    wiki = pd.concat(parts, ignore_index=True); wiki["date"] = pd.to_datetime(wiki["date"])
    val = pd.concat(val_parts, ignore_index=True); val["date"] = pd.to_datetime(val["date"])
    say(f"WIKI rows for missing members: {len(wiki):,} across {wiki.ticker.nunique()} symbols")

    # ---- 1. cross-validation against Yahoo on names both sources carry ----
    val["ticker"] = val.ticker.str.replace(".", "-", regex=False)
    val = val[(val.date >= "1998-01-01") & (val.date < "2018-01-01")]
    tot = bad = n_names = 0
    for t, g in val.groupby("ticker"):
        m = g.merge(yahoo[yahoo.ticker == t][["date", "close"]], on="date", suffixes=("_w", "_y")).sort_values("date")
        if len(m) < 250:
            continue
        sp = m.split_ratio.fillna(1.0)
        m = m[(sp == 1.0) & (sp.shift(-1).fillna(1.0) == 1.0)]
        d = (m.close_w.pct_change() - m.close_y.pct_change()).abs().dropna()
        tot += len(d); bad += int((d > 0.005).sum()); n_names += 1
    agree = 100 - bad / max(tot, 1) * 100
    say(f"CROSS-VALIDATION vs Yahoo, 1998-2017: {n_names} companies, {tot:,} daily returns, "
        f"{agree:.3f}% agree within 0.5pp")
    if n_names < 50 or agree < 99.0:
        say("ABORT: WIKI does not agree with Yahoo closely enough. Nothing accepted.")
        REPORT.write_text("\n".join(lines)); sys.exit(1)

    # ---- 2. identity check, 3. fill only missing names ----
    by_bare = {t: g.sort_values("date") for t, g in wiki.groupby("ticker")}
    ok, rejected, absent = 0, [], []
    for label, (bare, m_start, m_end) in sorted(want.items()):
        g = by_bare.get(bare)
        if g is None or len(g) < 20:
            absent.append(label); continue
        first, last = g.date.min(), g.date.max()
        if pd.notna(m_end) and first > m_end:
            rejected.append(f"{label}: starts after leaving the index"); continue
        if first > m_start + pd.Timedelta(days=400):
            rejected.append(f"{label}: starts long after joining"); continue
        if pd.isna(m_end) and last < pd.Timestamp("2018-03-01"):
            rejected.append(f"{label}: still a member but WIKI stops {last.date()}"); continue
        mo = SUFFIX.search(label)
        if mo:
            dl = pd.Timestamp(mo.group(1)[:4] + "-" + mo.group(1)[4:] + "-01")
            if abs((last - dl).days) > 550:
                rejected.append(f"{label}: ends {last.date()}, label says {dl:%Y-%m}"); continue
        pd.DataFrame({
            "date": g.date, "open": g.open, "high": g.high, "low": g.low, "close": g.close,
            "volume": g.volume, "dividends": g["ex-dividend"].fillna(0.0),
            "stock_splits": g.split_ratio.fillna(1.0), "ticker": label, "source": "wiki",
        }).to_parquet(RAW / f"{label}.parquet", index=False)
        ok += 1
    say(f"\nRECOVERED from WIKI: {ok}   rejected by identity check: {len(rejected)}   not in WIKI: {len(absent)}")
    for r in rejected[:40]:
        say("  rejected " + r)
    REPORT.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
