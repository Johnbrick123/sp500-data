"""
Step 6: Push the dataset to MotherDuck - DuckDB hosted in the cloud.

Why MotherDuck: it IS DuckDB. Every query in this repo runs unchanged. Free
tier, web SQL editor, and you can connect from Python, Excel (via ODBC), or
any laptop with a token. Nothing to migrate later.

One-time setup (2 minutes):
  1. Create a free account at motherduck.com
  2. Settings -> Access Tokens -> create one
  3. export motherduck_token="..."     (never commit this)
  4. python load_motherduck.py

Re-running replaces the tables in place. Safe to schedule.
"""
import os, sys
from pathlib import Path
import duckdb

if not os.environ.get("motherduck_token"):
    sys.exit("Set the motherduck_token environment variable first.")

ROOT = Path(__file__).parent
prices = (ROOT / "data" / "prices.parquet").as_posix()
members = (ROOT / "data" / "universe" / "members.parquet").as_posix()
intervals = (ROOT / "data" / "universe" / "membership_intervals.parquet").as_posix()
recycled = (ROOT / "data" / "recycled_tickers.csv").as_posix()

con = duckdb.connect("md:")                      # token read from env
con.execute("CREATE DATABASE IF NOT EXISTS market")
con.execute("USE market")
con.execute(f"CREATE OR REPLACE TABLE prices  AS SELECT * FROM '{prices}'")
con.execute(f"CREATE OR REPLACE TABLE members AS SELECT * FROM '{members}'")
con.execute(f"CREATE OR REPLACE TABLE membership_intervals AS SELECT * FROM '{intervals}'")
con.execute(f"CREATE OR REPLACE TABLE recycled AS SELECT ticker FROM read_csv_auto('{recycled}')")
con.execute("""
-- Point-in-time index membership: [start, end) intervals, end NULL = still a member.
-- Recycled symbols (a different company now owns the ticker) are excluded outright.
CREATE OR REPLACE VIEW v_sp500 AS
SELECT p.*
FROM prices p
JOIN membership_intervals m
  ON p.ticker = m.ticker AND p.date >= m.start AND (m."end" IS NULL OR p.date < m."end")
WHERE p.ticker NOT IN (SELECT ticker FROM recycled)
""")
n = con.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM prices").fetchone()
print(f"MotherDuck market.prices: {n[0]:,} rows, {n[1]:,} tickers")
print("Query from anywhere:  duckdb.connect('md:market').sql('SELECT ...')")
