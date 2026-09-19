"""
Step 2: Pull RAW OHLCV + dividends + splits from Yahoo.

Design decision that matters: we store RAW prices and a SEPARATE corporate
actions table. We do NOT store Yahoo's 'Adj Close'.

Why: Yahoo silently restates Adj Close every time a new dividend posts, so a
snapshot you took last month will not reproduce today. Storing raw + actions
and computing the adjustment yourself makes your history immutable and
auditable. compute_adjusted.py does the math.

Batched downloads (25 tickers/request) keep us well clear of Yahoo's rate
limiter. Resumable: already-downloaded tickers are skipped on re-run.
"""
import sys, time, warnings
from pathlib import Path
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
RAW.mkdir(parents=True, exist_ok=True)

START = "1995-01-01"
BATCH = 25
PAUSE = 1.0          # seconds between batches - be polite to Yahoo
MAX_RETRIES = 3


def load_tickers():
    p = ROOT / "data" / "universe" / "tickers.txt"
    return [t for t in p.read_text().split("\n") if t.strip()]


def clean(t):
    """Yahoo uses - where the index files use . (BRK.B -> BRK-B)."""
    return t.replace(".", "-").strip()


def fetch_batch(tickers):
    for attempt in range(MAX_RETRIES):
        try:
            return yf.download(
                [clean(t) for t in tickers], start=START,
                auto_adjust=False, actions=True, progress=False,
                group_by="ticker", threads=True, timeout=30,
            )
        except Exception as e:
            wait = 5 * (attempt + 1)
            print(f"    retry in {wait}s ({e})", flush=True)
            time.sleep(wait)
    return None


def main():
    tickers = load_tickers()
    done = {p.stem for p in RAW.glob("*.parquet")}
    dead_f = ROOT / "data" / "no_data_tickers.txt"
    dead = set(dead_f.read_text().split("\n")) if dead_f.exists() else set()
    todo = [t for t in tickers if clean(t) not in done and t not in dead]
    print(f"{len(tickers)} tickers, {len(done)} already on disk, "
          f"{len(todo)} to fetch", flush=True)

    ok, empty = 0, []
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        df = fetch_batch(batch)
        if df is None:
            empty.extend(batch)
            continue

        for t in batch:
            c = clean(t)
            try:
                sub = df[c] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                empty.append(t)
                continue
            sub = sub.dropna(how="all")
            if sub.empty or len(sub) < 20:
                empty.append(t)
                continue
            sub = sub.reset_index()
            sub.columns = [str(x).lower().replace(" ", "_") for x in sub.columns]
            sub = sub.drop(columns=["adj_close"], errors="ignore")
            sub["ticker"] = c
            sub.to_parquet(RAW / f"{c}.parquet", index=False)
            ok += 1

        print(f"  {i + len(batch):>5}/{len(todo)}  saved={ok}  "
              f"empty={len(empty)}", flush=True)
        time.sleep(PAUSE)

    dead_f.write_text("\n".join(sorted(dead | set(empty))))
    print(f"\nDONE. {ok} tickers saved. {len(empty)} returned no data.")
    print("Tickers with no data are mostly long-delisted names Yahoo has "
          "purged - see data/no_data_tickers.txt. This is a real and "
          "unavoidable gap in free data; note it before backtesting.")


if __name__ == "__main__":
    main()
