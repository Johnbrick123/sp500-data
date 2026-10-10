"""
Dividend audit against Tiingo's raw dividend record (divCash: the amount as
paid, on the ex-date). The price audits compare close-to-close moves and cannot
see a missing, extra or wrong-sized dividend; this one can.

Our stored dividend is on the series' own basis: for a Yahoo series that is the
amount divided by every later split factor (including the factor Yahoo uses
for a spin-off), so it is multiplied back by the product of later factors
before comparing. Tiingo-sourced series are raw already (and are skipped by
default: comparing Tiingo with itself proves nothing).

Usage: python audit_dividends.py YEARS [PRIORITY,TICKERS] [--all]
  YEARS      2018 or 2018-2026 (member-days in that range are compared)
  PRIORITY   names checked first; with --all, every other Yahoo-sourced
             member of the range follows
  env DIV_OFFSET / DIV_LIMIT pick a slice of that list (one request per name),
  DIV_PART names the output: data/dividend_audit_<YEARS>[_partN].txt and
  _overrides.csv (rows on our stored basis, ready for
  corporate_action_overrides.csv once each one is confirmed).
A finding is evidence, not a verdict: Tiingo also has gaps (it has no
DowDuPont dividends in 2018), so EXTRA rows are checked against a third
source before anything of ours is removed.
"""
import json, os, sys, time, urllib.request
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

RANGE = sys.argv[1]
Y0, Y1 = (int(RANGE.split("-")[0]), int(RANGE.split("-")[-1]))
PRIORITY = [t.strip() for t in (sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "").split(",") if t.strip()]
ALL = "--all" in sys.argv
KEY = os.environ.get("TIINGO_API_KEY", "").strip()
OFFSET = int(os.environ.get("DIV_OFFSET", "0") or 0)
LIMIT = int(os.environ.get("DIV_LIMIT", "40") or 40)
PART = os.environ.get("DIV_PART", "").strip()
ROOT = Path(__file__).parent
out = []
def say(s=""): print(s, flush=True); out.append(str(s))

p = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "dividends", "stock_splits", "source"])
p["date"] = pd.to_datetime(p["date"])
iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
y0, y1 = pd.Timestamp(f"{Y0}-01-01"), pd.Timestamp(f"{Y1}-12-31")
mem = iv[(iv.start <= y1) & (iv["end"].isna() | (iv["end"] > y0))]
alias = {}
af = ROOT / "tiingo_aliases.csv"
if af.exists():
    alias = dict(pd.read_csv(af)[["label", "tiingo_symbol"]].values)
spins = set()
sf = ROOT / "spinoffs.csv"
if sf.exists():
    s = pd.read_csv(sf, parse_dates=["ex_date"]); spins = set(zip(s.ticker, s.ex_date))
src_of = p[p.ticker.isin(set(mem.ticker))].groupby("ticker").source.agg(lambda x: x.mode().iloc[0])
yahoo_members = sorted(t for t, v in src_of.items() if v == "yahoo")
todo = list(dict.fromkeys(PRIORITY + (yahoo_members if ALL else [])))
todo = todo[OFFSET:OFFSET + LIMIT]
say(f"dividend audit {RANGE}{' part ' + PART if PART else ''}: {len(todo)} names (offset {OFFSET}) against Tiingo")


def tiingo(sym):
    u = (f"https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate={Y0 - 1}-12-15"
         f"&endDate={Y1}-12-31&format=json&token={KEY}")
    body = urllib.request.urlopen(u, timeout=60).read().decode()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError("throttled" if any(w in body.lower() for w in ("limit", "exceed", "quota", "hour")) else "not json")


def windows(t):
    g = mem[mem.ticker == t]
    return [(max(a, y0), min(b, y1 + pd.Timedelta(days=1)) if pd.notna(b) else y1 + pd.Timedelta(days=1)) for a, b in zip(g.start, g["end"])]


rows, overrides, done = [], [], []
for t in todo:
    o = p[p.ticker == t].sort_values("date").reset_index(drop=True)
    if o.empty:
        rows.append((t, "", "no prices of ours", "", "")); done.append(t); continue
    src = o.source.mode().iloc[0]
    sp = o.stock_splits.fillna(0).replace(0, 1.0).astype(float).to_numpy()
    later = np.append(np.cumprod(sp[::-1])[::-1][1:], 1.0)          # product of factors after each row
    scale = pd.Series(later if src == "yahoo" else np.ones(len(o)), index=o.date)
    scale = scale[~scale.index.duplicated()]
    ours = o[o.dividends.fillna(0) > 0].set_index("date").dividends
    try:
        js = tiingo(alias.get(t, t))
    except Exception as e:
        if "throttled" in str(e) or "429" in str(e):
            say(f"  throttled at {t}; stopping"); break
        rows.append((t, "", f"tiingo error {type(e).__name__}", "", "")); done.append(t); continue
    done.append(t)
    if not js:
        rows.append((t, "", "tiingo has no rows", "", "")); continue
    ti = pd.DataFrame(js); ti["date"] = pd.to_datetime(ti["date"]).dt.tz_localize(None)
    ti = ti.set_index("date")
    win = windows(t)
    inwin = lambda d: any(a <= d < b for a, b in win)
    tol = lambda a: max(0.0005, 0.005 * a)
    tdiv = ti[ti.divCash > 0].divCash
    for d, amt in tdiv.items():
        if not inwin(d):
            continue
        if (t, d) in spins:
            continue                                   # Tiingo books some spin-offs as cash; ours are in spinoffs.csv
        if d in ours.index:
            mine = float(ours[d]) * float(scale.get(d, 1.0))
            if abs(mine - amt) <= tol(amt):
                continue
            rows.append((t, str(d.date()), "amount differs", round(float(amt), 6), round(mine, 6)))
            overrides.append((t, str(d.date()), "", round(float(amt) / float(scale.get(d, 1.0)), 6),
                              f"Tiingo divCash {amt:g} vs ours {mine:g} as paid (audit_dividends.py)"))
            continue
        near = [x for x in ours.index if abs((x - d).days) <= 5 and abs(float(ours[x]) * float(scale.get(x, 1.0)) - amt) <= tol(amt)]
        if near:
            rows.append((t, str(d.date()), f"date differs: ours on {near[0].date()}", round(float(amt), 6), round(float(ours[near[0]]) * float(scale.get(near[0], 1.0)), 6)))
        else:
            rows.append((t, str(d.date()), "MISSING on our side", round(float(amt), 6), ""))
            if d in scale.index:
                overrides.append((t, str(d.date()), "", round(float(amt) / float(scale[d]), 6),
                                  f"missing in our source; Tiingo divCash {amt:g} (audit_dividends.py)"))
    for d, v in ours.items():
        if not inwin(d) or d in tdiv.index:
            continue
        mine = float(v) * float(scale.get(d, 1.0))
        if any(abs((x - d).days) <= 5 and abs(float(tdiv[x]) - mine) <= tol(mine) for x in tdiv.index):
            continue                                   # reported above as a date difference
        rows.append((t, str(d.date()), "EXTRA on our side" + ("" if d in ti.index else " (no Tiingo bar that day)"), "", round(mine, 6)))
    time.sleep(0.3)

found = [r for r in rows if r[1]]
say(f"\nRESULT {RANGE}: {len(done)} names checked; {len(found)} dividend differences")
say("  by kind: " + str(dict(Counter(r[2].split(":")[0] for r in rows))))
say("  names checked: " + ",".join(done))
say("\n(ticker, ex-date, finding, Tiingo amount, our amount as paid)")
for r in sorted(rows, key=lambda x: (x[0], x[1])):
    say(f"  {r}")
(ROOT / "data").mkdir(exist_ok=True)
tag = RANGE + (f"_part{PART}" if PART else "")
(ROOT / "data" / f"dividend_audit_{tag}.txt").write_text("\n".join(out))
pd.DataFrame(overrides, columns=["ticker", "date", "split_factor", "dividend", "note"]).to_csv(
    ROOT / "data" / f"dividend_audit_{tag}_overrides.csv", index=False)
