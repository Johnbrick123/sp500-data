"""Recover dead index members from the Kaggle CRSP daily dump
(meliodaz/crsp-with-mkt-ret-68-24: PERMNO, date, SHRCD, EXCHCD, PRC, RET, SHROUT,
vwretd; 1986-2024). Used with John's decision of 2026-10-12 (the data is CRSP's;
the Kaggle copy is labelled MIT).

Usage: python fetch_crsp.py "LABEL=PERMNO@START:END ..." ["CHECK_LABEL ..."]
  Each LABEL gets PERMNO's rows START..END (END = the company's last trading day).
  The permno must be identified first (probe_crsp.yml: last day + deal value).
  CHECK_LABELs (or LABEL=PERMNO, to name the permno) are dead members we already hold from Tiingo/WIKI: each is matched
  to its permno by its last trading day and last close (and a close a month
  earlier), and every overlapping
  close is compared - the validation of the source. Nothing is written unless
  >= 99% of the checked closes agree within 0.5%.

What the dump gives and how it is stored (source="crsp", raw like Tiingo's):
  close     = |PRC| (CRSP's closing price; a negative PRC is the bid/ask midpoint
              on a day with no trade - kept, counted in the report)
  dividends = PRC[t-1] x (1 + RET[t]) - PRC[t] x split, rounded to $0.0001,
              when positive (CRSP's RET includes every distribution)
  split     = the nearest standard ratio to PRC[t-1] x (1+RET) / PRC[t] when that
              is >= 1.2 or <= 0.85 (a split or reverse split that day)
  open/high/low/volume are not in the dump: left empty (never forged from the
              close - a flat bar would look like a vendor placeholder).
-> data/raw/LABEL.parquet, data/crsp_fetch_report.txt"""
import glob, os, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
specs = []
for s in sys.argv[1].split():
    if s == "-":
        continue
    lab, rest = s.split("=", 1)
    p, win = rest.split("@", 1)
    a, b = win.split(":")
    specs.append((lab, int(p), pd.Timestamp(a), pd.Timestamp(b)))
checks = sys.argv[2].split() if len(sys.argv) > 2 else []
# A check given as LABEL=PERMNO skips the last-day match (for a label whose series ends before the
# company's last trade, e.g. Sears Holdings: WIKI stops 2018-03, CRSP runs to the Oct-2018 delisting)
fixed_perm = {c.split("=")[0]: int(c.split("=")[1]) for c in checks if "=" in c}
checks = [c.split("=")[0] for c in checks]
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
def done(code=0):
    os.makedirs("data", exist_ok=True); open("data/crsp_fetch_report.txt", "w").write("\n".join(out)); sys.exit(code)

# Honor drop_raw_files.txt (as fetch_prices.py and fetch_wiki.py do): a condemned file - the wrong
# company under the label (old Allergan's and LSI Corp's Tiingo files were Actavis and Life Storage) -
# would otherwise make this recovery refuse the label.
drop_f = ROOT / "drop_raw_files.txt"
if drop_f.exists():
    for line in drop_f.read_text().splitlines():
        parts = line.split("#")[0].split()
        if len(parts) != 2:
            continue
        lab, src = parts
        fp = RAW / f"{lab}.parquet"
        if fp.exists() and str(pd.read_parquet(fp, columns=["source"])["source"].iloc[0]) == src:
            fp.unlink(); say(f"dropped raw file {lab} ({src}) per drop_raw_files.txt")

# ---- the check labels' own series (ours) -> their last day / last close
ours = {}
for lab in checks:
    f = RAW / f"{lab}.parquet"
    if not f.exists():
        say(f"check {lab}: no raw file"); continue
    d = pd.read_parquet(f)
    d["date"] = pd.to_datetime(d["date"]).dt.tz_localize(None) if pd.to_datetime(d["date"]).dt.tz is not None else pd.to_datetime(d["date"])
    d = d.sort_values("date")
    # judge by the last real trade (Tiingo pads dead names with flat bars)
    vol = pd.to_numeric(d.get("volume"), errors="coerce").fillna(0)
    real = d[vol > 0]
    ours[lab] = (real if len(real) else d)[["date", "close", "source"]]

want = {p for _, p, _, _ in specs}
f = glob.glob("kag/**/*.csv", recursive=True)[0]
WANT = {"permno", "date", "shrcd", "prc", "ret", "shrout"}
def chunks():
    for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in WANT, low_memory=False):
        ch.columns = [c.lower() for c in ch.columns]
        ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
        yield ch

# pass 1: each check label's permno = the one whose last trade is our last day at our last close
chk_perm = {}
if ours:
    # ... and a second close, a month earlier, so that another security whose last trade happens to
    # coincide cannot be mistaken for it (our CCE line ended 2017-11-08 at 35.71, and so did an
    # unrelated permno, which once failed the whole validation)
    ends = {}
    for lab, o in ours.items():
        ld, lc = o.date.max(), float(o.close.iloc[-1])
        ref = o[o.date <= ld - pd.Timedelta(days=30)]
        ends[lab] = (ld, lc, (ref.date.max(), float(ref.close.iloc[-1])) if len(ref) else None)
    near = []
    for ch in chunks():
        m = pd.Series(False, index=ch.index)
        for ld, _, _ in ends.values():
            m |= (ch.date >= ld - pd.Timedelta(days=45)) & (ch.date <= ld + pd.Timedelta(days=10))
        near.append(ch[m].dropna(subset=["prc"]))
    near = pd.concat(near, ignore_index=True)
    near["close"] = near.prc.abs()
    for lab, (ld, lc, ref) in ends.items():
        if lab in fixed_perm:
            chk_perm[lab] = fixed_perm[lab]; continue
        w = near[(near.date - ld).abs() <= pd.Timedelta(days=10)]
        last = w.sort_values("date").groupby("permno").tail(1)
        cand = last[((last.date - ld).abs() <= pd.Timedelta(days=3)) & ((last.close / lc - 1).abs() < 0.005)]
        if ref is not None and len(cand):
            rd, rc = ref
            r2 = near[(near.date == rd) & near.permno.isin(set(cand.permno)) & ((near.close / rc - 1).abs() < 0.005)]
            cand = cand[cand.permno.isin(set(r2.permno))]
        if len(cand) == 1:
            chk_perm[lab] = int(cand.permno.iloc[0])
        else:
            say(f"check {lab}: {len(cand)} permnos end {ld.date()} at {lc} (and match a month earlier) - skipped")

# pass 2: the full rows of every permno we need
keep = []
for ch in chunks():
    keep.append(ch[ch.permno.isin(want | set(chk_perm.values()))])
crsp = pd.concat(keep, ignore_index=True)

# ---- 1. validation against dead members we already hold - scored from 2003 (decimal prices; the era
# being signed off). Before that our own Tiingo/WIKI copies disagree with CRSP on fractional-price days
# (Jan 1996 especially); those are for the earlier years' passes to arbitrate.
VALID_FROM = pd.Timestamp("2003-01-01")
if ours:
    tot = agree = 0
    for lab, p in chk_perm.items():
        o = ours[lab]
        c = crsp[crsp.permno == p].dropna(subset=["prc"]).assign(close=lambda x: x.prc.abs())
        m_all = o.merge(c[["date", "close"]], on="date", suffixes=("_o", "_c"))
        early = m_all[m_all.date < VALID_FROM]
        if len(early):
            e_ok = int(((early.close_o / early.close_c - 1).abs() < 0.005).sum())
            say(f"  (before {VALID_FROM.date()}, not scored: {len(early)} days, {e_ok / len(early) * 100:.2f}% within 0.5% - fractional-price era; a later year's pass)")
        m = m_all[m_all.date >= VALID_FROM]
        ok = int(((m.close_o / m.close_c - 1).abs() < 0.005).sum())
        tot += len(m); agree += ok
        gap = c[(c.date >= o.date.min()) & (c.date <= o.date.max()) & ~c.date.isin(set(o.date))]
        if len(gap):
            say(f"  days CRSP has that our {lab} lacks: {[(str(r.date.date()), r.close, r.ret) for r in gap.head(12).itertuples()]}")
        bad = m[(m.close_o / m.close_c - 1).abs() >= 0.005]
        cy = os.environ.get("CHECK_YEAR", "").strip()
        if cy:      # every disagreement inside the year being signed off, for hand review
            yrs = {int(y) for y in cy.replace(",", " ").split()}
            yb = bad[bad.date.dt.year.isin(yrs)]
            if len(yb):
                say(f"  {cy} disagreements for {lab}: {[(str(r.date.date()), r.close_o, r.close_c) for r in yb.itertuples()]}")
        say(f"check {lab} ({o.source.iloc[0]}): permno {p}, {len(m)} days compared from 2003, {ok / max(len(m), 1) * 100:.2f}% within 0.5%"
            + (f"; e.g. {[(str(r.date.date()), r.close_o, r.close_c) for r in bad.head(4).itertuples()]}" if len(bad) else ""))
    pct = agree / max(tot, 1) * 100
    say(f"VALIDATION: {tot:,} closes compared, {pct:.3f}% agree within 0.5%")
    if tot < 2000 or pct < 99.5:
        say("ABORT: CRSP does not validate against our held series - nothing written"); done(1)

# ---- 2. recovery
for lab, p, lo, hi in specs:
    g = crsp[(crsp.permno == p) & (crsp.date >= lo) & (crsp.date <= hi)].dropna(subset=["prc"]).sort_values("date").reset_index(drop=True)
    say(f"\n{lab} <- permno {p} {lo.date()}..{hi.date()}: {len(g)} rows" + (f", {g.date.min().date()}..{g.date.max().date()}" if len(g) else ""))
    if (RAW / f"{lab}.parquet").exists():
        say("  REFUSED: the label already has a raw file"); continue
    if len(g) < 20:
        say("  REFUSED: fewer than 20 rows"); continue
    close = g.prc.abs().astype(float)
    ret = pd.to_numeric(g.ret, errors="coerce")
    x = close.shift() * (1 + ret)                      # what yesterday's share was worth today, incl. distributions
    k = x / close
    NICE = np.array([2, 3, 4, 5, 1.5, 1.25, 4 / 3, 10, 0.5, 1 / 3, 0.25, 0.2, 0.1, 2 / 3, 0.8])
    split = pd.Series(1.0, index=g.index)
    div = pd.Series(0.0, index=g.index)
    odd = []
    for i in g.index[1:]:
        if pd.isna(k[i]):
            continue
        if k[i] >= 1.2 or k[i] <= 0.85:
            j = np.argmin(np.abs(NICE / k[i] - 1))
            if abs(NICE[j] / k[i] - 1) < 0.03:
                split[i] = NICE[j]
            else:
                odd.append((str(g.date[i].date()), round(float(k[i]), 4)))     # a big distribution or a data break: reported, not guessed
                continue
        d = x[i] - close[i] * split[i]
        if d > 0.001:
            v = d / split[i]
            # CRSP's RET has 6 decimals, so the derived amount carries noise of about price x 1e-6
            # (Allergan's $0.05 came out 0.0501 at $240): snap to the cent when that close
            v = round(v, 2) if abs(v - round(v, 2)) <= max(0.0002, close[i] * 1.5e-6) else round(v, 4)
            div[i] = v
    df = pd.DataFrame({"date": g.date, "open": np.nan, "high": np.nan, "low": np.nan, "close": close,
                       "volume": np.nan, "dividends": div, "stock_splits": split, "ticker": lab, "source": "crsp"})
    RAW.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RAW / f"{lab}.parquet", index=False)
    say(f"  RECOVERED: {len(df)} rows, first {df.close.iloc[0]} ({df.date.iloc[0].date()}), last {df.close.iloc[-1]} ({df.date.iloc[-1].date()}); "
        f"{int((g.prc < 0).sum())} bid/ask days; {int((div > 0).sum())} dividends (last 6: {[(str(a.date()), b) for a, b in zip(df.date[div > 0][-6:], div[div > 0][-6:])]}); "
        f"splits {[(str(a.date()), b) for a, b in zip(df.date[split != 1], split[split != 1])]}")
    if odd:
        say(f"  UNEXPLAINED value jumps (big distribution or data break - check by hand): {odd[:10]}")
done(0)
