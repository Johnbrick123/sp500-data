"""
Query the dataset from anywhere - no download, no account, no token.
Works for Claude, for you, for a colleague. Just needs `pip install duckdb`.

Defaults to Johnbrick123/sp500-data; pass OWNER/REPO to point at a fork.
"""
import duckdb, sys

REPO = sys.argv[1] if len(sys.argv) > 1 else "Johnbrick123/sp500-data"
BASE = f"https://github.com/{REPO}/releases/download/data"
PRICES, INTERVALS, RECYCLED = f"{BASE}/prices.parquet", f"{BASE}/membership_intervals.parquet", f"{BASE}/recycled_tickers.csv"

con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")

# 1. One ticker, adjusted total-return series (fetches ~2 MB, not the whole file)
print(con.sql(f"""
    SELECT date, adj_close FROM read_parquet('{PRICES}')
    WHERE ticker = 'AAPL' ORDER BY date DESC LIMIT 5
""").df())

# 2. 10-month moving average across the whole universe, as of the latest date
print(con.sql(f"""
    SELECT ticker, date, adj_close,
           AVG(adj_close) OVER (PARTITION BY ticker ORDER BY date ROWS 209 PRECEDING) AS ma_10m
    FROM read_parquet('{PRICES}')
    QUALIFY date = MAX(date) OVER ()
    ORDER BY ticker LIMIT 10
""").df())

# 3. Point-in-time membership: who was in the S&P 500 on Lehman day, and how
#    many of them we actually have prices for (the honest coverage number)
print(con.sql(f"""
    WITH members AS (
      SELECT ticker FROM read_parquet('{INTERVALS}')
      WHERE start <= DATE '2008-09-15' AND ("end" IS NULL OR "end" > DATE '2008-09-15')
        AND ticker NOT IN (SELECT ticker FROM read_csv_auto('{RECYCLED}'))
    )
    SELECT COUNT(*) AS in_index,
           COUNT(p.ticker) AS with_price_data
    FROM members m
    LEFT JOIN (SELECT DISTINCT ticker FROM read_parquet('{PRICES}') WHERE date = DATE '2008-09-15') p USING (ticker)
""").df())
