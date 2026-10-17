---
name: backtest
description: Turn a plain-English trading idea into a strategy file for the qbt backtester in the sp500-data repo, run it against the point-in-time S&P 500 / ETF dataset (1970–today), and report the results honestly against SPY. Use whenever John describes a strategy to test ("backtest X", "what if I bought ... and sold ...", "test this rule", "/backtest"), asks to change or re-run one, or asks to sweep a parameter. Not for data-quality work on the dataset itself.
---

# /backtest — custom strategy backtests

John describes what he wants in plain English. You write the Python, run it,
and tell him what happened. He is not a coder: never ask him to edit code or
run commands.

## 1. Set up (once per session)

```bash
# repo not present?  attach it with the add_repo tool, then:
git clone --depth 1 https://github.com/Johnbrick123/sp500-data /home/claude/sp500-data
pip install -q pandas pyarrow duckdb numpy --break-system-packages
cd /home/claude/sp500-data && python backtest.py strategies/momentum_15.py --quiet --no-report
```
The first run downloads the release data (~235 MB) and builds the panel cache.
Then read `docs/BACKTEST_API.md` — it is the whole API.

## 2. Translate the idea into a strategy file

Write `strategies/<short_slug>.py` with one `Strategy` subclass. The docstring
restates his request in plain English; `name` is a short human title.

Map his words to the API:
- "rotate / rebalance monthly" → `schedule = "monthly"`, rank, `ctx.equal_weight(ctx.top(score, n))`
- "hold until ..." / "sell when ..." → keep `ctx.portfolio.holdings` that still pass the sell test, refill freed slots, `ctx.hold(book, rebalance=False)` if he wants winners to run
- "stop loss / trailing stop / exit after N days" → `on_day` with `ctx.sell(t)`
- "only when the market is above its 200-day" → `ctx.price_to_sma(200, tickers=["SPY"])["SPY"] > 0`
- "weight by inverse vol" → `ctx.inverse_vol_weight(...)`
- "ETFs" → `universe = "etfs"` or an explicit list; sector ETF menu lives in `strategies/etf_rotation.py`
- "since 19xx" → `start`; signals still use all prior history
- costs unstated → 10 bps (5 for ETFs); start unstated → 2010-01-01; weighting unstated → equal

Decide defaults yourself and state them in the write-up. Ask at most one
question, and only if the answer changes the result materially (e.g. he says
"sell when it breaks down" with no definition). Otherwise pick the sensible
reading and say what you picked.

Before running, check the strategy against the data's limits
(`docs/BACKTEST_API.md` → Mechanics): no open/high/low/volume before 1996,
SPY from 1995, membership from 1970.

## 3. Run

```bash
cd /home/claude/sp500-data
python backtest.py strategies/<slug>.py --quiet
```
Read `results/<name>/summary.txt`, `yearly.csv`, and `log.txt` (delistings,
stop-outs). If the run errors, fix the strategy file and rerun — do not hand
the error to John.

Cheap robustness checks worth running when the result looks good:
- `--sweep cost_bps=5,10,20,35` (does the edge survive real-world costs?)
- `--start 1997-01-01` and `--start 2010-01-01` (does it depend on the window?)
- the main parameter ±: `--sweep n=10,15,20,30`

## 4. Report back (chat, mobile-sized)

Lead with the verdict in one sentence, then a compact table: CAGR, Sharpe,
max drawdown, worst year, turnover — strategy vs SPY. Then 3–5 plain-English
findings, including the honest risks: which years carry the return, what
costs do to it, turnover/tax implications, data-coverage caveats for
pre-1996 runs. He values honest risk disclosure next to the bull case.

Publish `results/<name>/report.html` as an Artifact (it is self-contained) so
he can open it on his phone. Do not paste the URL; the card carries it.

## 5. Keep the record

```bash
git add strategies/<slug>.py docs/strategy-log.md
git commit -m "backtest: <name>" && git push
```
Append a dated entry to `docs/strategy-log.md`: the plain-English rule, the
headline numbers vs SPY, and the verdict. Results folders are git-ignored;
the strategy file and the log are the record. If `claude/backtester.md` in
the Claude Project exists, add the same entry there.

## Changing the engine

If an idea needs something the API lacks (a new indicator, a new order type),
add it to `qbt/indicators.py` or `qbt/engine.py`, keep it general, document
it in `docs/BACKTEST_API.md`, and commit it. The engine is his, not a vendor's.
