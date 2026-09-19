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

con = duckdb.connect("md:")                      # token read from env
con.execute("CREATE DATABASE IF NOT EXISTS market")
con.execute("USE market")
con.execute(f"CREATE OR REPLACE TABLE prices  AS SELECT * FROM '{prices}'")
con.execute(f"CREATE OR REPLACE TABLE members AS SELECT * FROM '{members}'")
con.execute("""
CREATE OR REPLACE VIEW v_sp500 AS
WITH spans AS (
  SELECT ticker, date AS from_date,
         LEAD(date) OVER (PARTITION BY ticker ORDER BY date) AS to_date
  FROM members)
SELECT p.* FROM prices p JOIN spans s
  ON p.ticker = s.ticker AND p.date >= s.from_date
 AND p.date < COALESCE(s.to_date, DATE '2100-01-01')
""")
n = con.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM prices").fetchone()
print(f"MotherDuck market.prices: {n[0]:,} rows, {n[1]:,} tickers")
print("Query from anywhere:  duckdb.connect('md:market').sql('SELECT ...')")
