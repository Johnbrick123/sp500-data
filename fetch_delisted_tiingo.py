"""
Step 2b: Recover DELISTED companies from Tiingo.

Yahoo purges most companies once they stop trading. Tiingo does not - it keeps
delisted history, which is exactly what a survivorship-bias-free backtest
needs. The free tier allows ~500 unique symbols per month with 30+ years of
history, so the ~590 missing names take two monthly runs.

Setup:
  1. Free account at tiingo.com -> API token
  2. export TIINGO_API_KEY="..."
  3. python fetch_delisted_tiingo.py          (stops at the monthly cap)

Saves into data/raw/ in the same schema as the Yahoo files. Every recovered
series must pass an IDENTITY test (accept()) before it is saved: a symbol
that has been reused by a different company is rejected, not merged.
HOW MUCH THIS RECOVERS IS UNPROVEN until it is run with a key. Tiingo's daily file gives
raw OHLCV + divCash + splitFactor, which is exactly what we want. Its 'close'
is UNADJUSTED - unlike Yahoo's - so we handle both in compute_adjusted.py via
the 'source' column.
"""
import os, re, sys, time, json, urllib.request
from pathlib import Path
import pandas as pd

KEY = os.environ.get("TIINGO_API_KEY")
if not KEY:
    sys.exit("Set TIINGO_API_KEY first (free at tiingo.com).")

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
MONTHLY_CAP = 480          # stay under Tiingo's ~500 unique-symbol free limit


SUFFIX = re.compile(r"-(\d{6})$")


def missing_tickers():
    """(label, api_symbol, membership_start, membership_end) for every index
    member with no price file. Labels like 'AW-200812' carry a delisting
    month; the API symbol is the bare ticker, and the suffix is used to
    VALIDATE that what comes back is the same company (see accept())."""
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    have = {p.stem for p in RAW.glob("*.parquet")}
    out = []
    for t, g in iv.groupby("ticker"):
        if t in have:
            continue
        out.append((t, SUFFIX.sub("", t), g.start.min(), g["end"].max()))
    return out


def accept(label, df, m_start, m_end):
    """Identity test. The series must OVERLAP the membership period: a series
    that only starts after the security left the index is a recycled symbol
    (a different company) and is rejected. If the label has a -YYYYMM
    delisting month, the series must also END within 18 months of it."""
    first, last = df.date.min(), df.date.max()
    if pd.notna(m_end) and first > m_end:
        return False, "starts after the security left the index (recycled symbol)"
    if first > m_start + pd.Timedelta(days=400):
        return False, "starts long after the membership period began"
    mo = SUFFIX.search(label)
    if mo:
        delisted = pd.Timestamp(mo.group(1)[:4] + "-" + mo.group(1)[4:] + "-01")
        if abs((last - delisted).days) > 550:
            return False, f"ends {last.date()}, but label says delisted {delisted:%Y-%m} (different company)"
    return True, "ok"


def fetch(tk):
    url = (f"https://api.tiingo.com/tiingo/daily/{tk}/prices"
           f"?startDate=1995-01-01&format=json&token={KEY}")
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    todo = missing_tickers()
    print(f"{len(todo)} tickers missing from Yahoo; trying Tiingo for up to "
          f"{MONTHLY_CAP} this run")
    ok, none, rejected = 0, [], []
    for i, (tk, sym, m_start, m_end) in enumerate(todo[:MONTHLY_CAP]):
        try:
            rows = fetch(sym)
        except Exception as e:
            none.append(tk); continue
        if not rows or len(rows) < 20:
            none.append(tk); continue
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        good, why = accept(tk, df, m_start, m_end)
        if not good:
            rejected.append(f"{tk}: {why}"); continue
        out = pd.DataFrame({
            "date": df["date"], "open": df["open"], "high": df["high"],
            "low": df["low"], "close": df["close"], "volume": df["volume"],
            "dividends": df.get("divCash", 0.0),
            "stock_splits": df.get("splitFactor", 1.0),
            "ticker": tk, "source": "tiingo",          # keep the LABEL (with suffix) as the security id
        })
        out.to_parquet(RAW / f"{tk}.parquet", index=False)
        ok += 1
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{min(len(todo), MONTHLY_CAP)}  saved={ok}", flush=True)
        time.sleep(0.5)
    print(f"\nrecovered {ok} delisted tickers; {len(none)} not on Tiingo; {len(rejected)} REJECTED as a different company")
    (ROOT / "data" / "not_on_tiingo.txt").write_text("\n".join(none))
    (ROOT / "data" / "tiingo_rejected.txt").write_text("\n".join(rejected))


if __name__ == "__main__":
    main()
