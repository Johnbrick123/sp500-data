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

Saves into data/raw/ in the same schema as the Yahoo files, so
compute_adjusted.py and build_db.py need no changes. Tiingo's daily file gives
raw OHLCV + divCash + splitFactor, which is exactly what we want. Its 'close'
is UNADJUSTED - unlike Yahoo's - so we handle both in compute_adjusted.py via
the 'source' column.
"""
import os, sys, time, json, urllib.request
from pathlib import Path
import pandas as pd

KEY = os.environ.get("TIINGO_API_KEY")
if not KEY:
    sys.exit("Set TIINGO_API_KEY first (free at tiingo.com).")

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
MONTHLY_CAP = 480          # stay under Tiingo's ~500 unique-symbol free limit


def missing_tickers():
    m = pd.read_parquet(ROOT / "data" / "universe" / "members.parquet")
    have = {p.stem for p in RAW.glob("*.parquet")}
    ever = sorted(set(m.ticker))
    return [t for t in ever if t.replace(".", "-") not in have]


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
    ok, none = 0, []
    for i, tk in enumerate(todo[:MONTHLY_CAP]):
        try:
            rows = fetch(tk.replace(".", "-"))
        except Exception as e:
            none.append(tk); continue
        if not rows or len(rows) < 20:
            none.append(tk); continue
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        out = pd.DataFrame({
            "date": df["date"], "open": df["open"], "high": df["high"],
            "low": df["low"], "close": df["close"], "volume": df["volume"],
            "dividends": df.get("divCash", 0.0),
            "stock_splits": df.get("splitFactor", 1.0),
            "ticker": tk.replace(".", "-"), "source": "tiingo",
        })
        out.to_parquet(RAW / f"{tk.replace('.', '-')}.parquet", index=False)
        ok += 1
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{min(len(todo), MONTHLY_CAP)}  saved={ok}", flush=True)
        time.sleep(0.5)
    print(f"\nrecovered {ok} delisted tickers; {len(none)} not on Tiingo either")
    (ROOT / "data" / "not_on_tiingo.txt").write_text("\n".join(none))


if __name__ == "__main__":
    main()
