# qbt — the backtester

A QuantConnect-style engine for the `sp500-data` dataset. You describe a
strategy as a short Python class; the engine walks every trading day from
1970 to today, fills your orders, charges costs, marks the book daily on
total returns, and writes a one-page report.

```
python backtest.py strategies/momentum_15.py                    # run it
python backtest.py strategies/momentum_15.py --start 1990-01-01 # different window
python backtest.py strategies/momentum_15.py --set n=20 --set cost_bps=20
python backtest.py strategies/momentum_15.py --sweep cost_bps=5,10,20,35
```

Output: `results/<name>/report.html` plus `equity.csv`, `trades.csv`,
`round_trips.csv`, `holdings.csv`, `yearly.csv`, `summary.json`, `log.txt`.

First run downloads the release data (~235 MB) and builds the panel cache
(~10 s). After that a 17-year monthly strategy runs in about 3 seconds; a
daily strategy in 10–20 seconds; the full 1970–2026 record in under 10.

## Anatomy of a strategy

```python
from qbt import Strategy

class Momentum15(Strategy):
    name = "Momentum 15"
    start = "2010-01-01"      # None = 1970
    end = None                # None = latest day
    schedule = "monthly"      # when on_rebalance runs
    cost_bps = 10             # per side
    n = 15                    # any extra attribute becomes a parameter (--set n=20)

    def on_rebalance(self, ctx):
        score = (0.4 * ctx.momentum(252, 21) + 0.3 * ctx.momentum(126, 21)
                 + 0.2 * ctx.momentum(63, 21) + 0.1 * ctx.pct_of_high(252))
        ctx.equal_weight(ctx.top(score, self.n))
```

Three hooks, all optional except `on_rebalance`:

| hook | runs | use it for |
|---|---|---|
| `initialize(self)` | once, before the first day | set up state (`self.highs = {}`) |
| `on_rebalance(self, ctx)` | on every schedule day | ranking, buying, selling |
| `on_day(self, ctx)` | every trading day | stops, circuit breakers, daily checks |

## Settings (class attributes)

| setting | default | meaning |
|---|---|---|
| `start`, `end` | `"2010-01-01"`, `None` | test window; signals still use all history before `start` |
| `cash` | 1,000,000 | starting capital |
| `schedule` | `"monthly"` | `"daily"`, `"weekly"`, `"monthly"`, `"quarterly"`, `"annual"`, a list of dates, or a function `f(date) -> bool` |
| `schedule_when` | `"end"` | `"end"` = last trading day of the period, `"start"` = first |
| `entry_lag` | 1 | fill N trading days after the signal day (0 = same close) |
| `fill` | `"close"` | `"close"` or `"open"` (open prices exist from 1996) |
| `cost_bps` | 10 | commission + spread per side |
| `slippage_bps` | 0 | extra adverse move on every fill |
| `cash_rate` | 0.0 | annual interest earned on idle cash |
| `benchmark` | `"SPY"` | any ticker, or `None` (SPY prices begin Jan 1995) |
| `universe` | `"sp500"` | `"sp500"` (point-in-time members), `"etfs"`, `"all"`, or a list of tickers |
| `min_history` | 0 | days of prices a name needs before it is eligible |
| `min_dollar_volume` | 0 | 63-day median dollar volume floor |
| `sell_if_not_eligible` | False | auto-sell names that drop out of the universe |
| `buy_order_ttl` | 5 | days an unfilled buy is retried before it is dropped |

## What `ctx` gives you on a day

Everything is **point-in-time**: values are as of `ctx.date`, computed from
trailing data only. There is no way to read tomorrow.

### Universe
- `ctx.date`, `ctx.i` — today
- `ctx.universe` — eligible tickers today (list)
- `ctx.sp500` — S&P 500 members today, regardless of the strategy universe
- `ctx.etfs` — the ETF set
- `ctx.in_index(t)`, `ctx.tradable(t)`

### Prices
- `ctx.price("AAPL")` → float; `ctx.price()` → Series over the universe
- `ctx.price(field="close")` for raw `close`, `open`, `high`, `low`, `volume`, `dividends`
- `ctx.history("adj_close", days=252)` → DataFrame of the last 252 rows (dates × tickers); `tickers=[...]` to narrow

### Indicators (Series over the universe; `tickers=[...]` to narrow; `days=N` for a DataFrame of history)
| call | meaning |
|---|---|
| `ctx.returns(63)` | trailing 63-day total return |
| `ctx.momentum(252, 21)` | return from 252 days ago to 21 days ago (the "12-1") |
| `ctx.sma(200)`, `ctx.ema(50)` | moving averages |
| `ctx.price_to_sma(200)` | price / SMA − 1 (> 0 means above) |
| `ctx.above_sma(200, buffer=0.02)` | boolean: price > SMA × 1.02 |
| `ctx.pct_of_high(252)` | price / 52-week high (1.0 = at the high) |
| `ctx.rolling_high(252)`, `ctx.rolling_low(252)` | trailing extremes |
| `ctx.drawdown()` | distance from all-time high |
| `ctx.volatility(63)` | annualised vol of daily returns |
| `ctx.atr(14)` | average true range (1996+) |
| `ctx.dollar_volume(63)` | median daily $ volume |
| `ctx.rsi(14)` | RSI |
| `ctx.macd(12, 26, 9)` | MACD histogram |
| `ctx.monthly_macd(8, 17, 9)` | MACD histogram on month-end closes |
| `ctx.zscore(20)`, `ctx.bollinger_pct(20, 2)` | mean-reversion measures |
| `ctx.days_of_history()` | how many priced days a name has |
| `ctx.indicator("name", ...)` | any function in `qbt/indicators.py` |

Add a new indicator by writing a function in `qbt/indicators.py` decorated
with `@cached` that returns a DataFrame computed with rolling/trailing windows.

### Ranking helpers
- `ctx.top(score, 15)` — 15 highest; `ascending=True` for lowest
- `ctx.where(cond)` — tickers where a boolean Series is True
- `ctx.rank(score)`

### Portfolio
- `ctx.portfolio.holdings` (list), `.weights` (Series), `.weight(t)`, `.has(t)`
- `.value`, `.cash`, `.invested` (fraction)
- `.pnl_pct(t)` since entry, `.days_held(t)`, `.entry_date(t)`, `.price(t)`

### Orders (target weights of portfolio value, filled `entry_lag` days later)
- `ctx.set_weights({"AAPL": .5, "MSFT": .5})` — the whole book; anything not named is sold
- `ctx.equal_weight(tickers, total=1.0)`
- `ctx.inverse_vol_weight(tickers, window=63)`
- `ctx.hold(tickers, rebalance=False)` — keep these names; `rebalance=False` lets winners run instead of trimming to equal weight
- `ctx.buy(t, weight)` / `ctx.target(t, weight)` — change one name, leave the rest
- `ctx.sell(t)`, `ctx.liquidate()`
- weights that sum to less than 1 leave the rest in cash

### Diagnostics
- `ctx.log("text")` → `log.txt`
- `ctx.record(cash_pct=0.2)` → `result.records` DataFrame

## Mechanics you should know

- **Fills.** Orders placed on day *d* fill at the close (or open) of day
  *d + entry_lag*. A name that is not tradable that day is retried daily (sells
  indefinitely, buys for `buy_order_ttl` days).
- **Marking.** Daily, on `adj_close` (split- and dividend-adjusted total
  return). Drawdowns are real daily drawdowns, not rebalance-to-rebalance.
- **Costs.** `cost_bps` on every dollar traded, each side. Delisting exits pay it too.
- **Delistings.** When a holding stops trading it is closed at its last real
  trade. For the companies in `exit_gaps.csv` (SVB, Signature, HealthSouth …)
  the next trade price is used instead: you could not have sold at the last
  close. Every such exit is in `log.txt`.
- **No-trade days** (`no_trade_days.csv`) are never fill days and never count
  as a trading opportunity; the strategy still sees the carried price.
- **Index membership** is point-in-time. A name that leaves the index stays
  in the book until the strategy sells it (or `sell_if_not_eligible = True`).
- **Before 1996** only closes exist: `open/high/low/volume`, `atr`,
  `dollar_volume` and `fill = "open"` are empty there.
- **Statistics.** CAGR is geometric. Sharpe and Sortino use the annualised
  arithmetic mean of daily returns over the annualised standard deviation
  (risk-free rate 0). Turnover = one-way traded value / average equity / year.

## Running from Python

```python
from qbt import run_file
res = run_file("strategies/momentum_15.py", n=20, start="2000-01-01")
print(res.summary())
res.stats["strategy"]["cagr"]; res.equity; res.trades; res.holdings
```

## Describing a strategy in plain English

You do not need to write the Python. Say what you want and Claude turns it
into a strategy file using this API, runs it, and reports back. The things to
specify (defaults in brackets):

1. **Universe** — S&P 500 [default], ETFs, or a list
2. **When to act** — monthly [default], weekly, daily, quarterly, annual
3. **What to buy** — the rule or ranking (momentum, low vol, RSI, above the 200-day …)
4. **How many / how to weight** — top 15 equal weight [default], inverse vol, let winners run
5. **When to sell** — rotate out of the top N [default], or a sell rule, stop, time limit
6. **Costs, start date, cash rate** — 10 bps, 2010, 0% [defaults]

Example: *"Buy the 20 S&P 500 stocks with the best 6-month return that are
above their 200-day average. Rebalance quarterly. Sell anything that falls
15% from its high. Run it from 1990 at 15 bps."*
