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
ANOMALIES = []          # (ticker, date, prev_close, dividend, factor, action)
OVERRIDES = ROOT / "corporate_action_overrides.csv"   # hand-verified fixes to provider records


def apply_overrides(df):
    """Provider records are sometimes wrong in known ways. This table is the
    place to fix them explicitly, with a note, instead of code heuristics.
    Columns: ticker, date, split_factor, dividend, note. Blank = leave as is."""
    if not OVERRIDES.exists():
        return df
    ov = pd.read_csv(OVERRIDES, dtype={"ticker": str})
    ov = ov[ov.ticker == df.ticker.iloc[0]]
    if ov.empty:
        return df
    d = pd.to_datetime(df["date"]).dt.tz_localize(None)
    for _, r in ov.iterrows():
        m = d == pd.Timestamp(r["date"])
        if not m.any():
            continue
        if pd.notna(r.get("split_factor")):
            df.loc[m, "stock_splits"] = float(r["split_factor"])
        if pd.notna(r.get("dividend")):
            df.loc[m, "dividends"] = float(r["dividend"])
    return df


def adjust_one(df):
    df = apply_overrides(df.sort_values("date").reset_index(drop=True))
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
    # Tiingo (delisted recovery) gives a truly UNADJUSTED close, so for those
    # rows the split ratio applies too. Yahoo rows: close is already
    # split-adjusted, so the effective split is 1 (the column is audit-only).
    src = df["source"].iloc[0] if "source" in df.columns else "yahoo"
    eff_split = split if src == "tiingo" else pd.Series(1.0, index=df.index)
    # A dividend is paid per share ON the ex-date, i.e. on the post-split basis
    # when a split lands the same day, while prev_close is pre-split. Put both
    # on the same basis: prev_close_new = prev_close / split.
    div_factor = np.where(prev_close > 0, 1.0 - div * eff_split / prev_close, 1.0)
    factor = pd.Series(div_factor, index=df.index) / eff_split

    # An adjustment factor <= 0 is impossible: it means a recorded dividend
    # exceeds the prior close, which happens when a provider books a spin-off
    # on a post-split basis but omits the split (KSU / Stilwell, July 2000).
    # Everything BEFORE such an event would come out negative, so the series
    # is cut at the event and the earlier history is set aside for review.
    bad = factor.index[(factor <= 0) & prev_close.notna()]
    if len(bad):
        cut = bad.max()
        for i in bad:
            ANOMALIES.append((df.ticker.iloc[0], df.date.iloc[i].date(), float(prev_close.iloc[i]),
                              float(div.iloc[i]), float(factor.iloc[i]),
                              f"history before {df.date.iloc[cut].date()} dropped"))
        df = df.iloc[cut + 1:].reset_index(drop=True)
        close, factor = df["close"].astype(float), factor.iloc[cut + 1:].reset_index(drop=True)
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

    pd.DataFrame(ANOMALIES, columns=["ticker", "date", "prev_close", "dividend", "factor", "action"]) \
        .to_csv(ROOT / "data" / "adjustment_anomalies.csv", index=False)
    if ANOMALIES:
        print(f"\nADJUSTMENT ANOMALIES: {len(ANOMALIES)} (see data/adjustment_anomalies.csv)")
        for a in ANOMALIES: print("   ", *a)
    mb = (ROOT / "data" / "prices.parquet").stat().st_size / 1e6
    print(f"\nrows      : {len(all_df):,}")
    print(f"tickers   : {all_df['ticker'].nunique():,}")
    print(f"range     : {all_df['date'].min().date()} -> "
          f"{all_df['date'].max().date()}")
    print(f"file size : {mb:.0f} MB")


if __name__ == "__main__":
    main()
