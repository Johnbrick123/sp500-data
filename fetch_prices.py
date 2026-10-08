"""
Step 2: Pull RAW OHLCV + dividends + splits from Yahoo, for EVERY live ticker,
EVERY run.

Why a full re-pull every night instead of appending new days:
  Yahoo restates history. Its 'Close' is split-adjusted retroactively, and
  every new dividend/split changes the whole back-series. An append-only
  refresh would leave old rows on the pre-split basis and new rows on the
  post-split basis - a silent corruption. A full re-pull of ~700 live names
  in batches takes about 3 minutes on GitHub's runners, so we just do that.

  (The first version of this script skipped any ticker already on disk. That
  was fine for the one-time backfill and fatal for the nightly refresh: no
  existing name ever received a new day of data. Found by external audit.)

Symbols:
  - dot -> hyphen (BRK.B -> BRK-B), one convention everywhere.
  - labels with a '-YYYYMM' delisting suffix (from the historical membership
    file) are never sent to Yahoo: Yahoo has purged those companies, and the
    bare symbol may now belong to a different company. They go to
    data/delisted_suffix_tickers.txt for fetch_delisted_tiingo.py.
  - known-dead tickers (Yahoo returned nothing) are re-checked on Mondays
    only, since each one costs a slow timeout.
"""
import re, sys, time, warnings
from datetime import date
from pathlib import Path
import os
import pandas as pd
NY_TODAY = pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()
import yfinance as yf

warnings.filterwarnings("ignore")
ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"; RAW.mkdir(parents=True, exist_ok=True)
START, BATCH, PAUSE, MAX_RETRIES = "1995-01-01", 50, 1.0, 3
SUFFIX = re.compile(r"-\d{6}$")
DEAD_F = ROOT / "data" / "no_data_tickers.txt"
SUFFIX_F = ROOT / "data" / "delisted_suffix_tickers.txt"


def norm(t): return t.strip().replace(".", "-")

TIINGO = os.environ.get("TIINGO_API_KEY", "").strip()
TIINGO_MAX = 40                     # free tier: 50 requests an hour


def tiingo_fetch(t):
    """Fallback for a LIVE ticker Yahoo will not serve (EQR and AVB came back with
    1-2 rows for weeks in 2026). Tiingo's by-ticker series is today's owner of the
    symbol, which is what a live ticker needs; close is unadjusted and
    compute_adjusted.py applies the split factor for source='tiingo'."""
    import json, urllib.request
    url = f"https://api.tiingo.com/tiingo/daily/{t}/prices?startDate={START}&format=json&token={TIINGO}"
    rows = json.load(urllib.request.urlopen(url, timeout=60))
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df[df["date"] < NY_TODAY]
    df = df[df["close"] > 0]
    if len(df) < 20:
        return None
    return pd.DataFrame({"date": df["date"], "open": df["open"], "high": df["high"], "low": df["low"],
                         "close": df["close"], "volume": df["volume"],
                         "dividends": df.get("divCash", 0.0), "stock_splits": df.get("splitFactor", 1.0),
                         "ticker": t, "source": "tiingo"})


def guard_truncation(t, sub):
    """Yahoo occasionally serves a live name as a stub (AVB: six weeks of 30 years).
    Overwriting the raw file with that destroys history. If the new series starts
    more than 30 days after the file we already hold - from the same source - keep
    the older rows and append only the new ones."""
    f = RAW / f"{t}.parquet"
    if not f.exists():
        return sub, False
    old = pd.read_parquet(f)
    if "source" in old.columns and str(old["source"].iloc[0]) != "yahoo":
        return sub, False
    old["date"] = pd.to_datetime(old["date"]).dt.tz_localize(None)
    new_first = pd.to_datetime(sub["date"]).min()
    if old["date"].min() < new_first - pd.Timedelta(days=30):
        keep = old[old["date"] < new_first]
        return pd.concat([keep, sub], ignore_index=True), True
    return sub, False


def fetch_batch(tickers):
    for attempt in range(MAX_RETRIES):
        try:
            return yf.download(tickers, start=START, auto_adjust=False, actions=True,
                               progress=False, group_by="ticker", threads=True, timeout=30)
        except Exception as e:
            time.sleep(5 * (attempt + 1)); print(f"    retry ({e})", flush=True)
    return None


def main():
    tickers = sorted({norm(t) for t in (ROOT / "data" / "universe" / "tickers.txt").read_text().split("\n") if t.strip()})
    suffixed = [t for t in tickers if SUFFIX.search(t)]
    SUFFIX_F.write_text("\n".join(suffixed))
    dead = set(DEAD_F.read_text().split("\n")) - {""} if DEAD_F.exists() else set()
    recheck_dead = date.today().weekday() == 0 or "--recheck-dead" in sys.argv
    live = [t for t in tickers if t not in suffixed and (t not in dead or recheck_dead)]
    print(f"{len(tickers)} symbols: {len(suffixed)} delisted-suffix (skipped, for Tiingo), "
          f"{len(dead)} known-dead ({'re-checking' if recheck_dead else 'skipped until Monday'}), "
          f"{len(live)} to pull fresh", flush=True)

    saved, empty, dropped, truncated = 0, [], 0, []
    for i in range(0, len(live), BATCH):
        batch = live[i:i + BATCH]
        df = fetch_batch(batch)
        if df is None:
            empty.extend(batch); continue
        for t in batch:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                empty.append(t); continue
            sub = sub.dropna(how="all")
            # a row with no positive close is not a price (Yahoo emits 0.0 on some
            # listing days and during outages); keep it out of the raw store
            if "Close" in sub.columns:
                n0 = len(sub); sub = sub[sub["Close"] > 0]; dropped += n0 - len(sub)
            # Never store the current New York session's bar. Yahoo serves it
            # before it is final - open/high/low from different moments, so the
            # open can sit outside the high-low range and the close moves between
            # downloads. That failed nightly runs #5 and #6. It is picked up,
            # finalized, on the next run.
            n0 = len(sub)
            idx = pd.to_datetime(sub.index)
            idx = idx.tz_localize(None) if idx.tz is not None else idx
            sub = sub[idx.normalize() < NY_TODAY]; dropped += n0 - len(sub)
            if len(sub) < 20:
                empty.append(t); continue
            sub = sub.reset_index()
            sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
            sub = sub.drop(columns=["adj_close"], errors="ignore")
            sub["ticker"] = t; sub["source"] = "yahoo"
            sub["date"] = pd.to_datetime(sub["date"]).dt.tz_localize(None)
            sub, patched = guard_truncation(t, sub)
            if patched:
                truncated.append(t)
            sub.to_parquet(RAW / f"{t}.parquet", index=False)   # overwrite: full restated history
            saved += 1
        print(f"  {i + len(batch):>5}/{len(live)}  saved={saved}  empty={len(empty)}", flush=True)
        time.sleep(PAUSE)

    # Live names Yahoo would not serve: try Tiingo before giving up on them.
    rescued = []
    if TIINGO and empty:
        for t in [t for t in empty if not SUFFIX.search(t)][:TIINGO_MAX]:
            try:
                df = tiingo_fetch(t)
            except Exception as e:
                print(f"    tiingo {t}: {type(e).__name__}", flush=True); continue
            if df is None:
                continue
            f = RAW / f"{t}.parquet"
            if f.exists():      # must agree with what we hold on overlapping days (same company?)
                old = pd.read_parquet(f); old["date"] = pd.to_datetime(old["date"]).dt.tz_localize(None)
                j = old.set_index("date")["close"].rename("o").to_frame().join(df.set_index("date")["close"].rename("n"), how="inner").dropna()
                if len(j) >= 50 and ((j.o.pct_change() - j.n.pct_change()).abs() < 0.005).mean() < 0.95:
                    print(f"    tiingo {t}: disagrees with the file we hold - not replaced", flush=True); continue
            df.to_parquet(f, index=False); rescued.append(t); saved += 1
            time.sleep(0.5)
        empty = [t for t in empty if t not in rescued]
        print(f"  Tiingo fallback: {len(rescued)} live names rescued {rescued}", flush=True)
    if truncated:
        print(f"  truncation guard kept older history for {len(truncated)}: {truncated}", flush=True)
    # a previously-live ticker that returned nothing today keeps yesterday's file
    # (Yahoo hiccups) but is reported; a never-seen ticker joins the dead list
    newly_dead = [t for t in empty if not (RAW / f"{t}.parquet").exists()]
    still_dead = (dead - set(live)) | set(newly_dead) if not recheck_dead else set(newly_dead)
    DEAD_F.write_text("\n".join(sorted(still_dead)))
    print(f"\nDONE. {saved} pulled fresh. {len(newly_dead)} returned nothing and have no file. "
          f"{len(empty) - len(newly_dead)} returned nothing but keep yesterday's file (check freshness).")


if __name__ == "__main__":
    main()
