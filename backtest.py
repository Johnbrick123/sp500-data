#!/usr/bin/env python3
"""
Run a strategy file through the backtester.

    python backtest.py strategies/momentum_15.py
    python backtest.py strategies/momentum_15.py --start 1990-01-01 --end 2009-12-31
    python backtest.py strategies/momentum_15.py --set cost_bps=20 --set entry_lag=2
    python backtest.py strategies/momentum_15.py --sweep cost_bps=5,10,20,35
    python backtest.py strategies/momentum_15.py --quiet

Any class attribute on the strategy can be overridden with --set key=value, and
--sweep runs the same strategy once per value and prints a comparison table.
Results land in results/<strategy-name>/ (report.html, equity.csv, trades.csv ...).
"""
import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qbt import run_file
from qbt.report import _pct, _num


def parse_val(v: str):
    try:
        return ast.literal_eval(v)
    except Exception:
        return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("strategy", help="path to a .py file containing a Strategy subclass")
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--sweep", metavar="KEY=V1,V2,...")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args()

    over = {k: parse_val(v) for k, v in (s.split("=", 1) for s in a.set)}
    if a.start:
        over["start"] = a.start
    if a.end:
        over["end"] = a.end

    if a.sweep:
        key, vals = a.sweep.split("=", 1)
        rows = []
        for v in vals.split(","):
            o = dict(over, **{key: parse_val(v)})
            res = run_file(a.strategy, verbose=False, report=not a.no_report, **o)
            s, t = res.stats["strategy"], res.stats["trading"]
            b = res.stats.get("benchmark", {})
            rows.append((v, s["cagr"], s["sharpe"], s["max_drawdown"], s["worst_year"], t["turnover_per_year"], b.get("cagr")))
        print(f"\nSweep {key}:")
        print(f"{key:>12} {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>8} {'WorstYr':>8} {'Turnover':>9} {'Bench':>8}")
        for r in rows:
            print(f"{r[0]:>12} {_pct(r[1]):>8} {_num(r[2]):>7} {_pct(r[3], 1):>8} {_pct(r[4], 1):>8} {_pct(r[5], 0):>9} {_pct(r[6]):>8}")
        return

    res = run_file(a.strategy, verbose=not a.quiet, report=not a.no_report, **over)
    print()
    print(res.summary())
    if not a.no_report:
        print(f"\nreport: results/{''.join(c if c.isalnum() else '_' for c in res.strategy.name).strip('_').lower()}/report.html")


if __name__ == "__main__":
    main()
