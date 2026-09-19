# S&P 500 + ETF Historical Dataset

Free, reproducible daily price history back to 1995. No paid data vendor.

## What you have

| | |
|---|---|
| Rows | 4,603,994 |
| Tickers | 706 |
| Range | 1995-01-03 → 2026-09-18 |
| Size | 108 MB (Parquet) |

`data/prices.parquet` — one tidy table: `date, ticker, open, high, low, close, volume, adj_close, dividends, stock_splits, source`

## Run it

```bash
pip install yfinance pandas pyarrow duckdb

python build_universe.py     # ticker universe + point-in-time membership
python bridge_membership.py  # extend membership 2019 -> today, normalize renames
python fetch_prices.py       # pull raw OHLCV + corporate actions (resumable)
python fetch_delisted_tiingo.py   # optional: recover delisted names (needs free key)
python compute_adjusted.py   # derive our own adjusted prices
python verify.py             # 8 independent data-quality checks
python build_db.py           # load into DuckDB (local)
python load_motherduck.py    # or push to MotherDuck (online, free tier)
```

`fetch_prices.py` is resumable and incremental — re-running only fetches what's
missing. A full cold backfill takes ~20 minutes. A nightly update takes seconds.

## Three things that will bite you (and what was done about them)

**1. Survivorship bias.** 1,233 tickers have been in the S&P 500 since 1996;
503 are in it today. 730 names left. A "current members" dataset silently
deletes all of them and will flatter every backtest you run.

*Handled:* `members.parquet` stores point-in-time membership. The `v_sp500`
view in DuckDB joins prices to membership so you only ever see names that were
actually in the index on that date.

**2. Yahoo's `Adj Close` is unstable.** It gets silently restated every time a
new dividend posts, so last month's snapshot won't reproduce today.

*Handled:* we store raw OHLCV plus a separate corporate-actions record and
derive `adj_close` ourselves in `compute_adjusted.py`. Same inputs always give
the same outputs — your backtests become reproducible.

**3. Ticker recycling.** When a company is acquired or delisted, its symbol gets
reassigned to an unrelated company. Example found in this data: **STI** was
SunTrust Banks, an S&P 500 bank that merged into Truist in 2019. Yahoo purged
SunTrust's history, and STI now returns a ~$6 microcap with data starting 2022.
Join that to the membership table and you're backtesting the wrong company
entirely — silently, with no error.

*Handled:* check 7 in `verify.py` flags any ticker whose price history begins
after it left the index. **17 found**, listed in `data/recycled_tickers.csv`.
Exclude them from point-in-time work.

## Verification (`verify.py`)

1. **Split spot-checks** — AAPL 7:1 and 4:1, NVDA 10:1, TSLA 5:1, AMZN/GOOGL 20:1
2. **Cross-source** — Stooq vs Yahoo adjusted returns
3. **Calendar** — duplicate, weekend, and missing rows
4. **Outliers** — >25% daily moves with no matching corporate action
5. **Continuity** — gaps longer than 10 calendar days
6. **Index reconstruction** — equal-weight large-cap basket vs SPY
7. **Recycled tickers** — symbol reassignment detection
8. **Adjustment reconciliation** — our derived total return vs Yahoo's

Current status: **0 hard failures.**

### Why check 8 exists

The first build had a bug: Yahoo's `Close` column is *already* split-adjusted,
so dividing by the split ratio again double-counted it. SPY and XOM looked
perfect (no splits since 2005) while AAPL was off by 56x.

The damage it did to check 6 is the real lesson — the 20-name basket showed
**18.33% CAGR against SPY's 14.08%.** Over 4% a year of pure phantom alpha,
from a data bug, in names you'd never suspect. After the fix: 14.18% vs 14.08%,
correlation 0.91.

That is the entire argument for running verification before you trust a
backtest. The bug was invisible in the price series and obvious in the returns.

## Known limitations — read before trusting a backtest

- **588 of 1,231 historical index members have no price data.** Yahoo has purged most long-dead
  companies. This is an unavoidable hole in free data and it reintroduces some
  survivorship bias through the back door. Listed in `data/no_data_tickers.txt`.
  `fetch_delisted_tiingo.py` recovers most of them from Tiingo's free tier
  (it retains delisted history; ~500 symbols/month, so two runs). Whatever is
  still missing after that is what CRSP and Norgate charge for.
- **Membership 2019 -> today is replayed from Wikipedia's change log**
  (`bridge_membership.py`). Wikipedia dropped that table from the live page in
  mid-2026, so the script reads it from an older page revision. Renamed symbols
  (FB->META, ANTM->ELV, 37 total in `renames.csv`) are normalized to the symbol
  Yahoo keeps the history under, which recovered those names for free.
- **Most ETFs don't go back 25 years.** SPY (1993), MDY (1995), DIA (1998),
  QQQ and the sector SPDRs (1998–99) do. Most others start 2003–2010. Splicing
  in index history before inception is a modeling choice — make it deliberately.
- **yfinance is an unofficial scraper.** Yahoo can change or throttle the
  endpoints at any time. The fetcher batches 25 tickers per request with a
  1-second pause to stay clear of rate limits.

## Online access (all free)

**MotherDuck** — DuckDB hosted in the cloud. `load_motherduck.py` pushes the
tables; then `duckdb.connect('md:market')` from any machine, or use their web
SQL editor. Every query in this repo runs unchanged.

**GitHub Releases** — `.github/workflows/update.yml` refreshes the data
weekdays at 6pm Chicago and publishes `prices.parquet` as a Release asset.
DuckDB can query that URL directly, no download step:

```sql
SELECT * FROM 'https://github.com/Johnbrick123/sp500-data/releases/download/data/prices.parquet'
WHERE ticker = 'AAPL'
```

Add `MOTHERDUCK_TOKEN` and `TIINGO_API_KEY` as repository secrets and the
workflow pushes to MotherDuck and recovers delisted names on its own.

## Backtest it

`backtest_example.py` is a complete, honest backtest that runs straight off
the public release: a 10-month moving-average trend strategy on the
point-in-time S&P 500, 1996 to now, against SPY and an equal-weight
buy-and-hold of the same names. It prints its own caveats. Read them.

```bash
python backtest_example.py
```

## Sharing with a colleague

Everything is public; there is nothing to log into. Three ways in:

- **Python** — `pip install duckdb` then `python query_remote.py`
- **DuckDB CLI** — `duckdb -c "SELECT * FROM 'https://github.com/Johnbrick123/sp500-data/releases/download/data/prices.parquet' WHERE ticker='AAPL' LIMIT 5"`
- **Excel** — Data → Get Data → From Web, paste the parquet URL. Power Query
  reads parquet natively. Filter to a ticker before loading; the full file is
  4.6M rows.

To let someone edit the pipeline: Settings → Collaborators.

## Querying

```python
import duckdb
con = duckdb.connect('data/market.duckdb')

# 10-month MA and MACD inputs for everything, in one pass
con.sql("""
  SELECT ticker, date, adj_close,
         AVG(adj_close) OVER (PARTITION BY ticker ORDER BY date
                              ROWS 209 PRECEDING) AS ma10m
  FROM prices
""").df()

# Survivorship-bias-free: only names actually in the index that day
con.sql("SELECT * FROM v_sp500 WHERE date = '2008-09-15'").df()
```
