# sp500-data

Point-in-time S&P 500 + ETF dataset (1970–today) and the `qbt` backtester.

- Data pipeline, verification: `README.md`, `verify.py`, `tools/`
- **Backtester:** `qbt/` (engine), `backtest.py` (runner), `strategies/` (examples),
  `docs/BACKTEST_API.md` (API). Run: `python backtest.py strategies/<file>.py`
- Plain-English strategy requests → the `backtest` skill (`.claude/skills/backtest/SKILL.md`)
- Record of every strategy tested: `docs/strategy-log.md`

Data files are release assets, not in git. `qbt` downloads them on first use.
