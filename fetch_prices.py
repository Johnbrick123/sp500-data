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
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"; RAW.mkdir(parents=True, exist_ok=True)
START, BATCH, PAUSE, MAX_RETRIES = "1995-01-01", 50, 1.0, 3
SUFFIX = re.compile(r"-\d{6}$")
DEAD_F = ROOT / "data" / "no_data_tickers.txt"
SUFFIX_F = ROOT / "data" / "delisted_suffix_tickers.txt"


def norm(t): return t.strip().replace(".", "-")


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

    saved, empty = 0, []
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
            if len(sub) < 20:
                empty.append(t); continue
            sub = sub.reset_index()
            sub.columns = [str(c).lower().replace(" ", "_") for c in sub.columns]
            sub = sub.drop(columns=["adj_close"], errors="ignore")
            sub["ticker"] = t; sub["source"] = "yahoo"
            sub.to_parquet(RAW / f"{t}.parquet", index=False)   # overwrite: full restated history
            saved += 1
        print(f"  {i + len(batch):>5}/{len(live)}  saved={saved}  empty={len(empty)}", flush=True)
        time.sleep(PAUSE)

    # a previously-live ticker that returned nothing today keeps yesterday's file
    # (Yahoo hiccups) but is reported; a never-seen ticker joins the dead list
    newly_dead = [t for t in empty if not (RAW / f"{t}.parquet").exists()]
    still_dead = (dead - set(live)) | set(newly_dead) if not recheck_dead else set(newly_dead)
    DEAD_F.write_text("\n".join(sorted(still_dead)))
    print(f"\nDONE. {saved} pulled fresh. {len(newly_dead)} returned nothing and have no file. "
          f"{len(empty) - len(newly_dead)} returned nothing but keep yesterday's file (check freshness).")


if __name__ == "__main__":
    main()
