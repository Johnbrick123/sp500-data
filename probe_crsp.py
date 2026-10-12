"""Probe: find the CRSP permnos of index members that have no price series,
in the Kaggle CRSP daily dump (meliodaz/crsp-with-mkt-ret-68-24: permno, date,
shrcd, exchcd, prc, ret, shrout, vwretd - no tickers or names). Read-only;
prints only identification facts (dates, last price, market cap), never series.

Usage: python probe_crsp.py "LABEL:YYYY-MM-DD[:ANCHOR] ..."   (CSV unpacked in kag/)
  YYYY-MM-DD = the company's last trading day as far as we know (S&P removal or
  deal close); ANCHOR = the deal value per share, to rank candidates.
Candidates: permnos whose last row falls within 20 days of the date, common
shares (shrcd 10/11), market cap over $1B on their last day.
-> data/crsp_probe.txt"""
import glob, sys
import pandas as pd

specs = []
for s in sys.argv[1].split():
    parts = s.split(":")
    specs.append((parts[0], pd.Timestamp(parts[1]), float(parts[2]) if len(parts) > 2 and parts[2] else None))
out = []
def say(s=""): print(s, flush=True); out.append(str(s))

f = glob.glob("kag/**/*.csv", recursive=True)[0]
say(f"file {f}")
head = pd.read_csv(f, nrows=5)
say(f"columns {list(head.columns)}\n{head.to_string(index=False)}")

lo = min(d for _, d, _ in specs) - pd.Timedelta(days=60)
hi = max(d for _, d, _ in specs) + pd.Timedelta(days=60)
last = {}
first = {}
n = 0
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=["permno", "date", "shrcd", "exchcd", "prc", "shrout"]):
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    n += len(ch)
    g = ch.groupby("permno").date.agg(["min", "max"])
    for p, r in g.iterrows():
        first[p] = min(first.get(p, r["min"]), r["min"])
    w = ch[(ch.date >= lo) & (ch.date <= hi)].dropna(subset=["prc"])
    if len(w):
        t = w.sort_values("date").groupby("permno").tail(1)
        for r in t.itertuples():
            if r.permno not in last or r.date > last[r.permno].date:
                last[r.permno] = r
say(f"{n:,} rows read; {len(first):,} permnos; {len(last):,} with a row in {lo.date()}..{hi.date()}")

for lab, d, anchor in specs:
    say(f"\n== {lab}: last day ~{d.date()}" + (f", deal value {anchor}" if anchor else ""))
    c = []
    for p, r in last.items():
        if abs((r.date - d).days) <= 20 and str(r.shrcd) in ("10", "11", "10.0", "11.0"):
            mcap = abs(r.prc) * r.shrout / 1000.0      # shrout in thousands -> $M
            if mcap > 1000:
                c.append((p, r.date.date(), abs(r.prc), round(mcap), r.exchcd, first[p].date(),
                          abs(abs(r.prc) / anchor - 1) if anchor else 0))
    c.sort(key=lambda x: x[-1])
    for x in c[:12]:
        say(f"  permno {x[0]:>6}  last {x[1]}  prc {x[2]:9.3f}  mcap ${x[3]:,}M  exch {x[4]}  first {x[5]}" + (f"  off {x[6]*100:.1f}%" if anchor else ""))
    if not c:
        say("  no candidate")
import os; os.makedirs("data", exist_ok=True)
open("data/crsp_probe.txt", "w").write("\n".join(out))
