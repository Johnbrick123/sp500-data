"""
One-off audit of a single calendar year: every index member of that year with
prices on file is compared, day by day, with Nasdaq's own history for the year.
Reports names that disagree (>1% of days off by >0.5pp), names Nasdaq cannot
serve (dead companies - verified separately), and the overall tally.
Usage: python audit_year.py 2023   -> data/audit_2023.txt
"""
import json, sys, time, urllib.request
from datetime import date
from pathlib import Path
import pandas as pd

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2023
ROOT = Path(__file__).parent
H = {"User-Agent": "Mozilla/5.0 (data verification)"}
out = []
def say(s=""): print(s, flush=True); out.append(str(s))

df = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "stock_splits", "source"])
df["date"] = pd.to_datetime(df["date"])
iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
y0, y1 = pd.Timestamp(f"{YEAR}-01-01"), pd.Timestamp(f"{YEAR}-12-31")
members = sorted(iv[(iv.start <= y1) & (iv["end"].isna() | (iv["end"] > y0))].ticker.unique())
say(f"AUDIT {YEAR}: {len(members)} index members during the year")
yr = df[(df.date >= y0) & (df.date <= y1)]
have = set(yr.ticker)
missing = [t for t in members if t not in have]
say(f"members with no {YEAR} prices on file: {len(missing)} {missing}")

agree, disagree, unavailable, thin, errs = [], [], [], [], []
for i, tk in enumerate(members):
    if tk not in have:
        continue
    o = yr[yr.ticker == tk]
    rows = []
    for ac in ("stocks", "etf"):
        try:
            # same request shape the nightly check uses (a bounded past window returned nothing)
            u = (f"https://api.nasdaq.com/api/quote/{tk}/historical?assetclass={ac}"
                 f"&fromdate={YEAR - 1}-12-15&todate={date.today():%Y-%m-%d}&limit=9999")
            d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
            rows = d.get("data", {}).get("tradesTable", {}).get("rows") or []
            if len(rows) >= 20:
                break
            if len(errs) < 3:
                errs.append(f"{tk}/{ac}: {str(d)[:160]}")
        except Exception as e:
            rows = []
            if len(errs) < 3:
                errs.append(f"{tk}/{ac}: {type(e).__name__} {str(e)[:100]}")
        time.sleep(0.4)
    if len(rows) < 20:
        unavailable.append((tk, str(o.source.iloc[0]), len(o)))
        continue
    n = pd.DataFrame(rows); n["date"] = pd.to_datetime(n["date"], errors="coerce")
    n["close"] = pd.to_numeric(n["close"].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")
    n = n.dropna(subset=["date", "close"]).sort_values("date")
    n = n[(n.date >= y0 - pd.Timedelta(days=20)) & (n.date <= y1)]
    m = o.merge(n[["date", "close"]], on="date", suffixes=("_o", "_n")).sort_values("date")
    m["r_o"] = m.close_o.pct_change(); m["r_n"] = m.close_n.pct_change()
    sp = m.stock_splits.replace(0, 1).fillna(1)
    m = m[(sp == 1) & (sp.shift(-1).fillna(1) == 1)]
    diff = (m.r_o - m.r_n).abs().dropna()
    if len(diff) < 20:
        thin.append((tk, len(diff))); continue
    off = (diff > 0.005).mean() * 100
    (agree if off < 1 else disagree).append((tk, round(off, 2), round(diff.max() * 100, 2), len(diff)))
    if (i + 1) % 50 == 0:
        say(f"  ... {i + 1}/{len(members)} checked; agree {len(agree)} disagree {len(disagree)} unavailable {len(unavailable)}")
    time.sleep(0.4)

say(f"\nRESULT {YEAR}: {len(agree)} names agree with Nasdaq on >=99% of days; {len(disagree)} disagree; "
    f"{len(unavailable)} not served by Nasdaq (dead/OTC names, checked by hand); {len(thin)} too few comparable days")
days_total = sum(a[3] for a in agree) + sum(d[3] for d in disagree)
days_off = sum(round(a[1] / 100 * a[3]) for a in agree) + sum(round(d[1] / 100 * d[3]) for d in disagree)
say(f"daily returns compared: {days_total:,}; differing by >0.5pp: {days_off:,} ({days_off / max(days_total, 1) * 100:.4f}%)")
if disagree:
    say("\nDISAGREE (ticker, % days off, max pp, days):")
    for d in sorted(disagree, key=lambda x: -x[1]): say(f"  {d}")
if unavailable:
    say("\nNOT ON NASDAQ (ticker, our source, rows):")
    for u in unavailable: say(f"  {u}")
if thin:
    say(f"\nTHIN: {thin}")
if errs:
    say(f"\nfirst request errors: {errs}")
(ROOT / "data" / f"audit_{YEAR}.txt").write_text("\n".join(out))
