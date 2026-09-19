"""
backtest_example.py - a complete, honest backtest against the public dataset.

Strategy (deliberately simple, so the plumbing is the point):
  Monthly rebalance. On each month-end, the tradeable universe is the set of
  names that were IN the S&P 500 on that date (point-in-time, not today's
  list). Each name gets a 1/N slot. If its price is above its own 10-month
  moving average the slot is invested in it; otherwise the slot sits in cash.
  So the portfolio is 100% invested in a broad uptrend and mostly cash in a
  broad downtrend - it does NOT pile everything into the last few stragglers
  still above their MA (an earlier version did, and lost 52% in 2008).

Benchmarks:
  SPY buy-and-hold            - the thing you are trying to beat
  Equal-weight universe B&H   - same names, no timing: isolates the MA effect

Runs straight off the GitHub Release - no download, no key:
    python backtest_example.py                       (default repo)
    python backtest_example.py OWNER/REPO            (someone else's copy)
    python backtest_example.py --local               (data/ on this machine)

Read the caveats printed at the end. They are not boilerplate.
"""
import sys
from pathlib import Path
import duckdb
import numpy as np
import pandas as pd

REPO = "Johnbrick123/sp500-data"
LOCAL = "--local" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if args:
    REPO = args[0]

if LOCAL:
    ROOT = Path(__file__).parent / "data"
    PRICES = (ROOT / "prices.parquet").as_posix()
    MEMBERS = (ROOT / "universe" / "members.parquet").as_posix()
else:
    BASE = f"https://github.com/{REPO}/releases/download/data"
    PRICES, MEMBERS = f"{BASE}/prices.parquet", f"{BASE}/members.parquet"

START, END = "1996-01-01", None
MA_MONTHS = 10


def load():
    con = duckdb.connect()
    if not LOCAL:
        con.execute("INSTALL httpfs; LOAD httpfs;")
    px = con.sql(f"""
        SELECT date, ticker, adj_close
        FROM read_parquet('{PRICES}')
        WHERE date >= DATE '{START}' AND ticker NOT LIKE 'X%'   -- drop sector ETFs
    """).df()
    mem = con.sql(f"SELECT date, ticker FROM read_parquet('{MEMBERS}')").df()
    px["date"] = pd.to_datetime(px["date"])
    mem["date"] = pd.to_datetime(mem["date"])
    return px, mem


def month_end_prices(px):
    """Last adjusted close of each month, tickers as columns."""
    wide = px.pivot(index="date", columns="ticker", values="adj_close")
    return wide.resample("ME").last()


def membership_by_month(mem, month_ends, prices_wide):
    """For each month-end, the set of names in the index on that date.
    Snapshots are on change dates; forward-fill between them. Also apply the
    recycled-ticker quarantine: a symbol whose price history starts AFTER it
    left the index is a different company - never trade it as the original."""
    last_seen = mem.groupby("ticker")["date"].max()
    first_px = prices_wide.apply(lambda s: s.first_valid_index())
    recycled = set(
        t for t in last_seen.index
        if t in first_px.index and pd.notna(first_px[t]) and first_px[t] > last_seen[t]
    )
    snaps = {d: set(g["ticker"]) - recycled for d, g in mem.groupby("date")}
    snap_dates = sorted(snaps)
    out, i = {}, 0
    for me in month_ends:
        while i + 1 < len(snap_dates) and snap_dates[i + 1] <= me:
            i += 1
        out[me] = snaps[snap_dates[i]] if snap_dates[i] <= me else set()
    return out, recycled


def run(prices_m, members_m):
    """Returns monthly return series for strategy, EW benchmark, and coverage."""
    rets = prices_m.pct_change()
    ma = prices_m.rolling(MA_MONTHS).mean()
    above = prices_m > ma                      # signal known at month-end t
    strat, ew, cov = [], [], []
    dates = prices_m.index
    for t in range(MA_MONTHS, len(dates) - 1):
        d, d_next = dates[t], dates[t + 1]
        universe = [x for x in members_m.get(d, ()) if x in prices_m.columns]
        held = [x for x in universe if pd.notna(prices_m.at[d, x])]
        cov.append((d_next, len(held), len(members_m.get(d, ()))))
        if not held:
            strat.append((d_next, 0.0)); ew.append((d_next, 0.0)); continue
        r_next = rets.loc[d_next, held].fillna(0.0)
        ew.append((d_next, r_next.mean()))
        longs = [x for x in held if above.at[d, x]]
        # 1/N slot per name: sum of long returns over the FULL universe size,
        # so slots in cash contribute 0 rather than shrinking the divisor.
        strat.append((d_next, r_next[longs].sum() / len(held)))
    s = pd.Series(dict(strat)); e = pd.Series(dict(ew))
    c = pd.DataFrame(cov, columns=["date", "tradeable", "in_index"]).set_index("date")
    return s, e, c


def stats(r, name):
    r = r.dropna()
    yrs = len(r) / 12
    growth = (1 + r).cumprod()
    cagr = growth.iloc[-1] ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(12)
    dd = (growth / growth.cummax() - 1).min()
    sharpe = (r.mean() * 12) / vol if vol else float("nan")
    worst = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1).min()
    return {"": name, "CAGR": f"{cagr*100:6.2f}%", "Vol": f"{vol*100:6.2f}%",
            "MaxDD": f"{dd*100:7.2f}%", "Sharpe": f"{sharpe:5.2f}",
            "Worst yr": f"{worst*100:7.2f}%", "Final $1": f"${growth.iloc[-1]:8.2f}"}


def main():
    src = "local files" if LOCAL else f"github.com/{REPO} release"
    print(f"loading from {src} ...", flush=True)
    px, mem = load()
    prices_m = month_end_prices(px)
    members_m, recycled = membership_by_month(mem, prices_m.index, prices_m)
    strat, ew, cov = run(prices_m, members_m)
    spy = prices_m["SPY"].pct_change().reindex(strat.index)

    print(f"\n{MA_MONTHS}-month MA trend strategy on the point-in-time S&P 500")
    print(f"{strat.index[0].date()} -> {strat.index[-1].date()}, "
          f"{len(strat)} months, monthly rebalance, equal weight\n")
    table = pd.DataFrame([stats(strat, "10M-MA strategy"),
                          stats(ew, "Equal-wt universe B&H"),
                          stats(spy, "SPY buy & hold")])
    print(table.to_string(index=False))

    # Crisis-period comparison: where trend following earns its keep, if anywhere
    print("\nDrawdown periods (cumulative return, strategy vs SPY):")
    for label, a, b in [("Dot-com 2000-02", "2000-03-31", "2002-09-30"),
                        ("GFC 2007-09", "2007-10-31", "2009-02-28"),
                        ("COVID 2020", "2020-01-31", "2020-03-31"),
                        ("2022", "2021-12-31", "2022-12-31")]:
        s = (1 + strat.loc[a:b]).prod() - 1
        p = (1 + spy.loc[a:b]).prod() - 1
        print(f"  {label:16s} strategy {s*100:7.1f}%   SPY {p*100:7.1f}%")

    c = cov
    print(f"\nCoverage: on average {c.tradeable.mean():.0f} of "
          f"{c.in_index.mean():.0f} index members had price data "
          f"({c.tradeable.mean()/c.in_index.mean()*100:.0f}%). "
          f"Worst month: {c.tradeable.min()} names.")
    print(f"Recycled tickers excluded: {len(recycled)}")

    print("""
CAVEATS - read before believing any of the numbers above
  1. SURVIVORSHIP BIAS IS STILL PRESENT. Names that went bankrupt or were
     acquired before Yahoo purged them have no price data, so they silently
     drop out of the universe. Coverage above shows how much is missing. The
     strategy numbers are flattered by exactly the losers it cannot see.
     Add a Tiingo key (fetch_delisted_tiingo.py) to close most of this gap.
  2. No transaction costs, slippage, or taxes. Monthly turnover in a trend
     strategy is real money.
  3. Cash earns 0%. Using T-bills would add roughly the risk-free rate to the
     strategy's time out of the market.
  4. Signals use month-end closes and trade at the same close - a small
     look-ahead. Trading the next open is more realistic.
  5. One parameter (10 months), no fitting. That is a feature, not a bug -
     but also means this is a demonstration of the data, not a strategy.
""")


if __name__ == "__main__":
    main()
