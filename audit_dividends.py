"""
Dividend audit for one year against Tiingo's raw dividend record (divCash, as
paid, on the ex-date). The price audits compare close-to-close moves and cannot
see a missing, extra or wrong-sized dividend; this one can.

Our stored dividend is on the series' own basis: for a Yahoo series that is the
amount divided by every later split factor (including the factor Yahoo uses
for a spin-off), so it is multiplied back by the product of later factors
before comparing. Tiingo-sourced series are raw already.

Usage: python audit_dividends.py YEAR [TICKER,TICKER,...]
  no list: every member of YEAR whose series comes from Yahoo, most-held first.
Free tier: 50 requests an hour - PER_BATCH requests, then an hour's pause.
Output: data/dividend_audit_YEAR.txt (and _overrides.csv: rows ready for
corporate_action_overrides.csv, on our stored basis).
"""
import json, os, sys, time, urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

YEAR = int(sys.argv[1])
ONLY = [t.strip() for t in (sys.argv[2] if len(sys.argv) > 2 else "").split(",") if t.strip()]
KEY = os.environ.get("TIINGO_API_KEY", "").strip()
ROOT = Path(__file__).parent
PER_BATCH = int(os.environ.get("DIV_PER_BATCH", "45"))
BATCHES = int(os.environ.get("DIV_BATCHES", "1"))
out = []
def say(s=""): print(s, flush=True); out.append(str(s))

p = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "dividends", "stock_splits", "source"])
p["date"] = pd.to_datetime(p["date"])
iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
y0, y1 = pd.Timestamp(f"{YEAR}-01-01"), pd.Timestamp(f"{YEAR}-12-31")
mem = iv[(iv.start <= y1) & (iv["end"].isna() | (iv["end"] > y0))]
alias = {}
af = ROOT / "tiingo_aliases.csv"
if af.exists():
    alias = dict(pd.read_csv(af)[["label", "tiingo_symbol"]].values)
spins = set()
sf = ROOT / "spinoffs.csv"
if sf.exists():
    s = pd.read_csv(sf, parse_dates=["ex_date"]); spins = set(zip(s.ticker, s.ex_date))
if ONLY:
    todo = ONLY
else:
    src = p[p.ticker.isin(set(mem.ticker))].groupby("ticker").source.agg(lambda x: x.mode().iloc[0])
    todo = sorted(t for t, v in src.items() if v == "yahoo")
say(f"dividend audit {YEAR}: {len(todo)} names to check against Tiingo")


def tiingo(sym):
    u = (f"https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate={YEAR - 1}-12-15"
         f"&endDate={YEAR}-12-31&format=json&token={KEY}")
    body = urllib.request.urlopen(u, timeout=60).read().decode()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise RuntimeError("throttled" if any(w in body.lower() for w in ("limit", "exceed", "quota", "hour")) else "not json")


def member_window(t):
    g = mem[mem.ticker == t]
    return [(max(a, y0), min(b, y1 + pd.Timedelta(days=1)) if pd.notna(b) else y1 + pd.Timedelta(days=1)) for a, b in zip(g.start, g["end"])]


rows, done, overrides = [], [], []
calls = 0
for t in todo:
    if calls and calls % PER_BATCH == 0:
        if calls // PER_BATCH >= BATCHES:
            say(f"stopping after {calls} requests (batch limit); {len(todo) - len(done)} names left"); break
        say(f"  {calls} requests: pausing an hour for Tiingo's limit"); time.sleep(3660)
    o = p[p.ticker == t].sort_values("date").reset_index(drop=True)
    if o.empty:
        rows.append((t, "", "no prices of ours", "", "")); done.append(t); continue
    src = o.source.mode().iloc[0]
    sp = o.stock_splits.fillna(0).replace(0, 1.0).astype(float).to_numpy()
    later = np.append(np.cumprod(sp[::-1])[::-1][1:], 1.0)          # product of factors after each row
    scale = pd.Series(later if src == "yahoo" else np.ones(len(o)), index=o.date)
    ours = o[o.dividends.fillna(0) > 0].set_index("date").dividends
    try:
        calls += 1
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
    win = member_window(t)
    inwin = lambda d: any(a <= d < b for a, b in win)
    tdiv = ti[ti.divCash > 0].divCash
    # same check both ways, on member-days of YEAR only
    for d, amt in tdiv.items():
        if not inwin(d):
            continue
        if (t, d) in spins:
            rows.append((t, str(d.date()), "spin-off ex-date (Tiingo books the spin as cash; ours is in spinoffs.csv)", round(float(amt), 4), "")); continue
        if d in ours.index:
            mine = float(ours[d]) * float(scale.get(d, 1.0))
            if abs(mine - amt) <= max(0.0005, 0.005 * amt):
                continue
            rows.append((t, str(d.date()), "amount differs", round(float(amt), 4), round(mine, 4)))
            overrides.append((t, str(d.date()), "", round(float(amt) / float(scale.get(d, 1.0)), 6), f"Tiingo divCash {amt:g} (ours was {mine:g} raw); audit_dividends.py"))
        else:
            near = [x for x in ours.index if abs((x - d).days) <= 5 and abs(float(ours[x]) * float(scale.get(x, 1.0)) - amt) <= max(0.0005, 0.005 * amt)]
            if near:
                rows.append((t, str(d.date()), f"date differs: ours on {near[0].date()}", round(float(amt), 4), round(float(ours[near[0]]) * float(scale.get(near[0], 1.0)), 4)))
            else:
                sc = float(scale.get(d, 1.0)) if d in scale.index else np.nan
                rows.append((t, str(d.date()), "MISSING on our side", round(float(amt), 4), ""))
                if d in scale.index:
                    overrides.append((t, str(d.date()), "", round(float(amt) / sc, 6), f"missing in our source; Tiingo divCash {amt:g}; audit_dividends.py"))
    for d, v in ours.items():
        if not inwin(d) or d in tdiv.index:
            continue
        mine = float(v) * float(scale.get(d, 1.0))
        near = [x for x in tdiv.index if abs((x - d).days) <= 5 and abs(float(tdiv[x]) - mine) <= max(0.0005, 0.005 * mine)]
        if near:
            continue                                   # reported above as a date difference
        if d not in ti.index:
            rows.append((t, str(d.date()), "EXTRA on our side (Tiingo has no bar that day)", "", round(mine, 4)))
        else:
            rows.append((t, str(d.date()), "EXTRA on our side", "", round(mine, 4)))
    time.sleep(0.3)

say(f"\nRESULT {YEAR}: {len(done)} names checked with {calls} Tiingo requests; {len([r for r in rows if r[1]])} dividend differences")
from collections import Counter
say("  by kind: " + str(dict(Counter(r[2].split(":")[0] for r in rows))))
say("\n(ticker, ex-date, finding, Tiingo amount, our amount as paid)")
for r in sorted(rows, key=lambda x: (x[0], x[1])):
    say(f"  {r}")
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data" / f"dividend_audit_{YEAR}.txt").write_text("\n".join(out))
pd.DataFrame(overrides, columns=["ticker", "date", "split_factor", "dividend", "note"]).to_csv(
    ROOT / "data" / f"dividend_audit_{YEAR}_overrides.csv", index=False)
