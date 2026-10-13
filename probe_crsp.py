"""Probe: find the CRSP permnos of index members that have no price series,
in the Kaggle CRSP daily dump (meliodaz/crsp-with-mkt-ret-68-24: permno, date,
shrcd, exchcd, prc, ret, shrout, vwretd - no tickers or names). Read-only;
prints only identification facts (dates, last price, market cap), never series.

Usage: python probe_crsp.py "LABEL:YYYY-MM-DD[:ANCHOR] ..."   (CSV unpacked in kag/)
  A spec may instead be LABEL@YYYY-MM-DD=PRICE[,YYYY-MM-DD=PRICE...][~MCAP] (the
  "known day" mode, added 2026-10-13): candidates are permnos with a row on every
  given day, ranked by how far their prices sit from the given ones (and from the
  market cap in $M, when given) - for a member whose line runs on long after it
  left the index (a bankruptcy, a removal), a spin-off's first day, or a company
  whose last day is unknown. Each candidate's first and last day is printed.
  YYYY-MM-DD = the company's last trading day as far as we know (S&P removal or
  deal close); ANCHOR = the deal value per share, to rank candidates.
Candidates: permnos whose last row falls within 20 days of the date, common
shares (shrcd 10/11/12, and 18 for REITs), market cap over $1B (MIN_MCAP env, in $M) on their last day.
-> data/crsp_probe.txt"""
import glob, os, sys
import pandas as pd

specs = []
dspecs = []        # known-day mode: (label, [(day, price)], mcap or None)
for s in sys.argv[1].split():
    if "@" in s:
        lab, rest = s.split("@", 1)
        mc = None
        if "~" in rest:
            rest, mc = rest.split("~", 1); mc = float(mc)
        pts = [(pd.Timestamp(a), float(b)) for a, b in (x.split("=") for x in rest.split(","))]
        dspecs.append((lab, pts, mc)); continue
    parts = s.split(":")
    specs.append((parts[0], pd.Timestamp(parts[1]), float(parts[2]) if len(parts) > 2 and parts[2] else None))
out = []
def say(s=""): print(s, flush=True); out.append(str(s))

f = glob.glob("kag/**/*.csv", recursive=True)[0]
say(f"file {f}")
head = pd.read_csv(f, nrows=5)
say(f"columns {list(head.columns)}\n{head.to_string(index=False)}")

alld = [d for _, d, _ in specs] or [pd.Timestamp("2000-01-03")]
lo = min(alld) - pd.Timedelta(days=60)
hi = max(alld) + pd.Timedelta(days=60)
kdays = sorted({d for _, pts, _ in dspecs for d, _ in pts})
krows = {}        # (permno, day) -> row
last = {}
first = {}
lastall = {}
n = 0
WANT = {"permno", "date", "shrcd", "exchcd", "prc", "shrout"}
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in WANT):
    ch.columns = [c.lower() for c in ch.columns]      # the dump's headers are CRSP's upper case (PERMNO, PRC, ...)
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    n += len(ch)
    g = ch.groupby("permno").date.agg(["min", "max"])
    for p, r in g.iterrows():
        first[p] = min(first.get(p, r["min"]), r["min"])
        lastall[p] = max(lastall.get(p, r["max"]), r["max"])
    if kdays:
        k = ch[ch.date.isin(kdays)].dropna(subset=["prc"])
        for r in k.itertuples():
            krows[(r.permno, r.date)] = r
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
        codes = os.environ.get("SHRCD", "10,11,12,18").split(",")      # 12 = foreign-incorporated (Cooper Industries plc, Tyco, Accenture...); 18 = REIT; SHRCD=any lifts the filter
        if abs((r.date - d).days) <= 20 and ("any" in codes or str(r.shrcd).split(".")[0] in codes):
            mcap = abs(r.prc) * r.shrout / 1000.0      # shrout in thousands -> $M
            if mcap > float(os.environ.get("MIN_MCAP", "1000")):
                c.append((p, r.date.date(), abs(r.prc), round(mcap), r.exchcd, first[p].date(),
                          abs(abs(r.prc) / anchor - 1) if anchor else 0))
    c.sort(key=lambda x: x[-1])
    for x in c[:12]:
        say(f"  permno {x[0]:>6}  last {x[1]}  prc {x[2]:9.3f}  mcap ${x[3]:,}M  exch {x[4]}  first {x[5]}" + (f"  off {x[6]*100:.1f}%" if anchor else ""))
    if not c:
        say("  no candidate")
codes = os.environ.get("SHRCD", "10,11,12,18").split(",")
import math
for lab, pts, mc in dspecs:
    say(f"\n== {lab} (known-day mode): " + ", ".join(f"{d.date()} = {v}" for d, v in pts) + (f", mcap ~${mc:,.0f}M" if mc else ""))
    cands = {p for (p, d) in krows if d == pts[0][0]}
    c = []
    for p in cands:
        rows = [krows.get((p, d)) for d, _ in pts]
        if any(r is None for r in rows): continue
        if not ("any" in codes or str(rows[0].shrcd).split(".")[0] in codes): continue
        sc = sum(abs(math.log(abs(r.prc) / v)) for r, (_, v) in zip(rows, pts)) / len(pts)
        m0 = abs(rows[0].prc) * rows[0].shrout / 1000.0
        if mc and m0 > 0: sc += 0.5 * abs(math.log(m0 / mc))
        c.append((sc, p, rows, m0))
    c.sort(key=lambda x: x[0])
    for sc, p, rows, m0 in c[:12]:
        say(f"  permno {p:>6}  " + "  ".join(f"{r.date.date()} prc {abs(r.prc):9.3f}" for r in rows)
            + f"  mcap ${m0:,.0f}M  exch {rows[0].exchcd}  shrcd {rows[0].shrcd}  first {first[p].date()}  last {lastall[p].date()}  score {sc:.3f}")
    if not c:
        say("  no candidate")
import os; os.makedirs("data", exist_ok=True)
open("data/crsp_probe.txt", "w").write("\n".join(out))
