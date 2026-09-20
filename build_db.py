"""
Step 5: Load everything into a single DuckDB file.

DuckDB is the right answer here instead of a cloud database. The whole dataset
is a few hundred MB, DuckDB queries it in milliseconds, it is a single file you
can back up or commit, and it costs nothing forever.

Creates:
  prices      raw + self-adjusted daily bars
  members     point-in-time S&P 500 membership
  v_sp500     prices JOINed to membership - the survivorship-bias-free view
"""
from pathlib import Path
import duckdb

ROOT = Path(__file__).parent
DB = ROOT / "data" / "market.duckdb"

DB.unlink(missing_ok=True)   # rebuild fresh so the file does not bloat
con = duckdb.connect(str(DB))
p = (ROOT / "data" / "prices.parquet").as_posix()
m = (ROOT / "data" / "universe" / "members.parquet").as_posix()

iv = (ROOT / "data" / "universe" / "membership_intervals.parquet").as_posix()
rc = (ROOT / "data" / "recycled_tickers.csv").as_posix()
con.execute(f"CREATE OR REPLACE TABLE prices AS SELECT * FROM '{p}'")
con.execute(f"CREATE OR REPLACE TABLE members AS SELECT * FROM '{m}'")
con.execute(f"CREATE OR REPLACE TABLE membership_intervals AS SELECT * FROM '{iv}'")
con.execute(f"CREATE OR REPLACE TABLE recycled AS SELECT ticker FROM read_csv_auto('{rc}')")

con.execute("CREATE INDEX IF NOT EXISTS idx_pt ON prices(ticker, date)")

# Membership snapshots are periodic; forward-fill to daily ranges.
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

for t in ["prices", "members", "membership_intervals", "recycled"]:
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"{t:10s}: {n:>12,} rows")

rng = con.execute("SELECT MIN(date), MAX(date), COUNT(DISTINCT ticker) "
                  "FROM prices").fetchone()
print(f"range     : {rng[0]} -> {rng[1]}")
print(f"tickers   : {rng[2]:,}")
print(f"db size   : {DB.stat().st_size/1e6:.0f} MB")
con.close()

print("""
Query it like this:

  import duckdb
  con = duckdb.connect('data/market.duckdb')

  # 200-day MA for every current holding
  con.sql("SELECT ticker, date, adj_close, "
          "AVG(adj_close) OVER (PARTITION BY ticker ORDER BY date "
          "ROWS 199 PRECEDING) AS ma200 FROM prices").df()

  # Point-in-time: only names actually IN the index that day (price coverage
  # is incomplete - see README coverage table - so this is NOT survivorship-free)
  con.sql("SELECT * FROM v_sp500 WHERE date = '2008-09-15'").df()
""")
