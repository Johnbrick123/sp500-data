"""
Step 3: Compute our own adjusted prices from raw OHLCV + corporate actions.

This replaces Yahoo's unstable 'Adj Close'. Because we derive it from stored
raw prices and a stored actions table, the same inputs always produce the same
outputs - your backtests become reproducible.

Method (standard total-return back-adjustment):
  For each day t, the adjustment factor applied to all prior history is
      f_t = (1 - div_t / close_{t-1}) / split_ratio_t
  Cumulative product of future factors, applied backwards, gives adj_close.

Outputs:
  data/prices.parquet   one tidy table: date, ticker, o/h/l/c, volume,
                        adj_close, div, split
"""
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"


def adjust_one(df):
    df = df.sort_values("date").reset_index(drop=True)
    close = df["close"].astype(float)
    div = df.get("dividends", pd.Series(0.0, index=df.index)).fillna(0.0)
    split = df.get("stock_splits", pd.Series(0.0, index=df.index)).fillna(0.0)
    split = split.replace(0.0, 1.0)          # retained for audit only

    prev_close = close.shift(1)
    # IMPORTANT: Yahoo's 'Close' is ALREADY split-adjusted retroactively.
    # Dividing by the split ratio again double-counts it (verified against a
    # known AAPL 7:1 - it produced a 7x jump in the adjusted series).
    # So we apply ONLY the dividend adjustment here. The splits column is
    # retained as an audit record and is used by verify.py.
    # A dividend that equals or exceeds the prior close cannot be a cash
    # distribution; it is a bad data row. Applying it would make the factor
    # <= 0 and flip the entire earlier history negative. Skip it and say so,
    # so verify.py flags one row rather than a thousand.
    bogus = (prev_close > 0) & (div >= prev_close)
    if bogus.any():
        t = df["ticker"].iloc[0] if "ticker" in df.columns else "?"
        for _, r in df[bogus].iterrows():
            print(f"  WARN {t} {pd.Timestamp(r['date']).date()}: dividend {r['dividends']} >= prior close - ignored")
    div_factor = np.where((prev_close > 0) & ~bogus, 1.0 - div / prev_close, 1.0)
    factor = pd.Series(div_factor, index=df.index)
    # Tiingo (delisted recovery) gives a truly UNADJUSTED close, so for those
    # rows the split ratio must be applied as well.
    src = df["source"].iloc[0] if "source" in df.columns else "yahoo"
    if src == "tiingo":
        factor = factor / split

    # Back-adjust: each day's history is scaled by all FUTURE factors.
    cum = factor.iloc[::-1].shift(1).fillna(1.0).cumprod().iloc[::-1]
    df["adj_close"] = close * cum
    df["adj_factor"] = cum
    return df


def main():
    files = sorted(RAW.glob("*.parquet"))
    out = []
    for i, f in enumerate(files):
        try:
            out.append(adjust_one(pd.read_parquet(f)))
        except Exception as e:
            print(f"  skip {f.stem}: {e}")
        if (i + 1) % 250 == 0:
            print(f"  adjusted {i + 1}/{len(files)}", flush=True)

    all_df = pd.concat(out, ignore_index=True)
    all_df["date"] = pd.to_datetime(all_df["date"]).dt.tz_localize(None)
    if "source" not in all_df.columns:
        all_df["source"] = "yahoo"
    all_df["source"] = all_df["source"].fillna("yahoo")
    keep = ["date", "ticker", "open", "high", "low", "close", "volume",
            "adj_close", "dividends", "stock_splits", "source"]
    all_df = all_df[[c for c in keep if c in all_df.columns]]
    all_df = all_df.sort_values(["ticker", "date"])
    all_df.to_parquet(ROOT / "data" / "prices.parquet", index=False,
                      compression="zstd", row_group_size=100_000)   # small groups = fast remote ticker queries

    mb = (ROOT / "data" / "prices.parquet").stat().st_size / 1e6
    print(f"\nrows      : {len(all_df):,}")
    print(f"tickers   : {all_df['ticker'].nunique():,}")
    print(f"range     : {all_df['date'].min().date()} -> "
          f"{all_df['date'].max().date()}")
    print(f"file size : {mb:.0f} MB")


if __name__ == "__main__":
    main()
