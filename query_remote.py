"""
Query the dataset from anywhere - no download, no account, no token.
Works for Claude, for you, for a colleague. Just needs `pip install duckdb`.

Defaults to Johnbrick123/sp500-data; pass OWNER/REPO to point at a fork.
"""
import duckdb, sys

REPO = sys.argv[1] if len(sys.argv) > 1 else "Johnbrick123/sp500-data"
BASE = f"https://github.com/{REPO}/releases/download/data"
PRICES = f"{BASE}/prices.parquet"
MEMBERS = f"{BASE}/members.parquet"

con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")

# 1. One ticker, adjusted total-return series (fetches ~2 MB, not the whole file)
print(con.sql(f"""
    SELECT date, adj_close
    FROM read_parquet('{PRICES}')
    WHERE ticker = 'AAPL' ORDER BY date DESC LIMIT 5
""").df())

# 2. 10-month moving average signal across the whole universe
print(con.sql(f"""
    SELECT ticker, date, adj_close,
           AVG(adj_close) OVER (PARTITION BY ticker ORDER BY date
                                ROWS 209 PRECEDING) AS ma_10m
    FROM read_parquet('{PRICES}')
    QUALIFY date = MAX(date) OVER ()
    ORDER BY ticker LIMIT 10
""").df())

# 3. Survivorship-bias-free: who was actually in the S&P 500 on Lehman day
print(con.sql(f"""
    WITH spans AS (
      SELECT ticker, date AS from_date,
             LEAD(date) OVER (PARTITION BY ticker ORDER BY date) AS to_date
      FROM read_parquet('{MEMBERS}'))
    SELECT COUNT(*) AS names_in_index_2008_09_15
    FROM spans WHERE from_date <= DATE '2008-09-15'
     AND COALESCE(to_date, DATE '2100-01-01') > DATE '2008-09-15'
""").df())
