"""
Second-source check of one year against ANY Kaggle price archive, either one
big table (symbol/ticker + date + close) or one CSV per ticker (filename =
ticker). Built for archives frozen while companies that have since died were
still trading, e.g. jacksoncrow/stock-market-dataset (Apr 2020) and
tsaustin/us-historical-stock-prices-with-earnings-data (Jun 2021).
Usage: python crosscheck_dataset.py SLUG YEAR [WATCH,LIST] -> data/xcheck_<name>_YEAR.txt
"""
import glob, io, os, re, sys, zipfile
from pathlib import Path
import pandas as pd

slug, YEAR = sys.argv[1], int(sys.argv[2])
WATCH = [w.strip().upper() for w in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if w.strip()]
ROOT = Path(__file__).parent
name = slug.split("/")[1][:30]
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
y0, y1 = pd.Timestamp(f"{YEAR}-01-01"), pd.Timestamp(f"{YEAR}-12-31")
lo = y0 - pd.Timedelta(days=12)

files = [f for f in glob.glob("kag/**/*", recursive=True) if os.path.isfile(f) and f.lower().endswith((".csv", ".txt"))]
say(f"{slug}: {len(files)} csv/txt files")
SYM = ("symbol", "ticker", "tic", "name")
frames = []
per_ticker = []
for f in files:
    try:
        head = pd.read_csv(f, nrows=3)
    except Exception:
        continue
    cols = {c.lower().strip(): c for c in head.columns}
    dcol = next((cols[c] for c in ("date", "timestamp", "datetime") if c in cols), None)
    ccol = next((cols[c] for c in ("close", "close_price") if c in cols), None)
    if not dcol or not ccol:
        continue
    scol = next((cols[c] for c in SYM if c in cols and c != "name"), None)
    if scol:                                   # one big table
        for ch in pd.read_csv(f, usecols=[scol, dcol, ccol], chunksize=2_000_000):
            ch[dcol] = pd.to_datetime(ch[dcol], errors="coerce", utc=True).dt.tz_localize(None)
            ch = ch[(ch[dcol] >= lo) & (ch[dcol] <= y1)]
            frames.append(ch.rename(columns={scol: "symbol", dcol: "date", ccol: "close"}))
    else:                                      # one file per ticker
        per_ticker.append((f, dcol, ccol))
for f, dcol, ccol in per_ticker:
    t = re.sub(r"\.(csv|txt)$", "", os.path.basename(f), flags=re.I)
    t = re.sub(r"\.us$", "", t, flags=re.I)
    g = pd.read_csv(f, usecols=[dcol, ccol])
    g[dcol] = pd.to_datetime(g[dcol], errors="coerce", utc=True).dt.tz_localize(None)
    g = g[(g[dcol] >= lo) & (g[dcol] <= y1)]
    if len(g):
        frames.append(g.rename(columns={dcol: "date", ccol: "close"}).assign(symbol=t))
k = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "date", "close"])
k["symbol"] = k["symbol"].astype(str).str.upper().str.replace(".", "-", regex=False).str.replace("^", "-", regex=False)
k["close"] = pd.to_numeric(k["close"], errors="coerce"); k = k.dropna(subset=["date", "close"])
say(f"archive rows in/near {YEAR}: {len(k):,}, symbols {k.symbol.nunique():,}, {k.date.min()}..{k.date.max()}")

ours = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "stock_splits", "source"])
ours["date"] = pd.to_datetime(ours["date"])
iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
members = set(iv[(iv.start <= y1) & (iv["end"].isna() | (iv["end"] > y0))].ticker)
ren = dict(pd.read_csv(ROOT / "data" / "universe" / "renames.csv").values)
inv = {}
for o, n in ren.items():
    inv.setdefault(n, []).append(o)
oy = ours[(ours.date >= lo) & (ours.date <= y1) & ours.ticker.isin(members)]
ksyms = set(k.symbol)

def candidates(lab):
    bare = re.sub(r"-\d{6}$", "", lab)
    c = [lab, bare] + inv.get(lab, []) + inv.get(bare, [])
    return [x for x in dict.fromkeys(c) if x in ksyms]

agree, disagree, absent = [], [], []
for lab in sorted(members):
    o = oy[oy.ticker == lab]
    if o.empty:
        continue
    cs = candidates(lab)
    if not cs:
        absent.append(lab); continue
    best = None
    for s in cs:
        g = k[k.symbol == s][["date", "close"]].drop_duplicates("date")
        m = o.merge(g, on="date", suffixes=("_o", "_k")).sort_values("date")
        m["r_o"] = m.close_o.pct_change(); m["r_k"] = m.close_k.pct_change()
        sp = m.stock_splits.replace(0, 1).fillna(1)
        m = m[(sp == 1) & (sp.shift(-1).fillna(1) == 1) & (m.date >= y0)]
        d = (m.r_o - m.r_k).abs().dropna()
        if len(d) < 15:
            continue
        off = (d > 0.005).mean() * 100
        row = (lab, s, str(o.source.iloc[0]), round(off, 2), round(d.max() * 100, 3), len(d), m.date.min().date(), m.date.max().date())
        if best is None or off < best[3]:
            best = row
    if best is None:
        absent.append(lab)
    else:
        (agree if best[3] < 1 else disagree).append(best)

n_days = sum(a[5] for a in agree + disagree); n_off = sum(round(a[3] / 100 * a[5]) for a in agree + disagree)
say(f"\nRESULT {YEAR}: {len(agree)} members agree on >=99% of compared days, {len(disagree)} disagree, {len(absent)} not in this archive")
say(f"daily returns compared: {n_days:,}; differing by >0.5pp: {n_off:,} ({n_off / max(n_days, 1) * 100:.4f}%)")
nonyahoo = [a for a in agree if not a[2].startswith("yahoo")]
say(f"\nnon-Yahoo series confirmed ({len(nonyahoo)}): " + ", ".join(f"{a[0]}({a[6]}..{a[7]})" for a in nonyahoo))
if disagree:
    say("\nDISAGREE (our label, archive symbol, our source, % days off, max pp, days, from, to):")
    for d in sorted(disagree, key=lambda x: -x[3]): say(f"  {d}")
if WATCH:
    say("\nWATCHLIST:")
    allr = {a[0]: a for a in agree + disagree}
    for w in WATCH:
        say(f"  {w:12s} " + (str(allr[w]) if w in allr else ("not in archive" if w in absent else "not a member / no prices")))
(ROOT / "data" / f"xcheck_{name}_{YEAR}.txt").write_text("\n".join(out))
