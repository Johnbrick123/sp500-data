"""
Second-source check of one year against ANY Kaggle price archive, either one
big table (symbol/ticker + date + close) or one CSV per ticker (filename =
ticker). Built for archives frozen while companies that have since died were
still trading, e.g. jacksoncrow/stock-market-dataset (Apr 2020) and
tsaustin/us-historical-stock-prices-with-earnings-data (Jun 2021).
Usage: python crosscheck_dataset.py SLUG YEAR [WATCH,LIST] -> data/xcheck_<name>_YEAR.txt
When the archive has an adjusted close, total returns are compared too (that
tests dividends, which the close-to-close check cannot see); spin-off ex-dates
and distributions over 5% of the price differ by design (see kaggle_crosscheck.py).
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
    acol = next((cols[c] for c in ("adj close", "adj_close", "adjclose", "adjusted_close", "adjusted close", "close_adjusted") if c in cols), None)
    if not dcol or not ccol:
        continue
    scol = next((cols[c] for c in SYM if c in cols and c != "name"), None)
    if scol:                                   # one big table
        for ch in pd.read_csv(f, usecols=[scol, dcol, ccol] + ([acol] if acol else []), chunksize=2_000_000):
            ch[dcol] = pd.to_datetime(ch[dcol], errors="coerce", utc=True).dt.tz_localize(None)
            ch = ch[(ch[dcol] >= lo) & (ch[dcol] <= y1)]
            frames.append(ch.rename(columns={scol: "symbol", dcol: "date", ccol: "close", **({acol: "adj"} if acol else {})}))
    else:                                      # one file per ticker
        per_ticker.append((f, dcol, ccol, acol))
for f, dcol, ccol, acol in per_ticker:
    t = re.sub(r"\.(csv|txt)$", "", os.path.basename(f), flags=re.I)
    t = re.sub(r"\.us$", "", t, flags=re.I)
    g = pd.read_csv(f, usecols=[dcol, ccol] + ([acol] if acol else []))
    g[dcol] = pd.to_datetime(g[dcol], errors="coerce", utc=True).dt.tz_localize(None)
    g = g[(g[dcol] >= lo) & (g[dcol] <= y1)]
    if len(g):
        frames.append(g.rename(columns={dcol: "date", ccol: "close", **({acol: "adj"} if acol else {})}).assign(symbol=t))
k = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "date", "close"])
if "adj" not in k.columns:
    k["adj"] = float("nan")
k["adj"] = pd.to_numeric(k["adj"], errors="coerce")
k["symbol"] = k["symbol"].astype(str).str.upper().str.replace(".", "-", regex=False).str.replace("^", "-", regex=False)
k["close"] = pd.to_numeric(k["close"], errors="coerce"); k = k.dropna(subset=["date", "close"])
say(f"archive rows in/near {YEAR}: {len(k):,}, symbols {k.symbol.nunique():,}, {k.date.min()}..{k.date.max()}")

ours = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "adj_close", "dividends", "stock_splits", "source"])
spin_days = set()
if (ROOT / "spinoffs.csv").exists():
    _sp = pd.read_csv(ROOT / "spinoffs.csv", parse_dates=["ex_date"])
    spin_days = set(zip(_sp.ticker, _sp.ex_date))
tr_days, tr_off = 0, []
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

spans = {t: list(zip(g.start, g["end"])) for t, g in iv.groupby("ticker")}
def member_days(o, lab):
    keep = pd.Series(False, index=o.index)
    for a, b in spans.get(lab, []):
        keep |= (o.date >= a - pd.Timedelta(days=10)) & ((o.date < b) if pd.notna(b) else True)
    return o[keep]

agree, disagree, absent, detail = [], [], [], {}
for lab in sorted(members):
    o = member_days(oy[oy.ticker == lab], lab)          # compare only days the company was in the index
    if o.empty:
        continue
    cs = candidates(lab)
    if not cs:
        absent.append(lab); continue
    best = None
    for s in cs:
        g = k[k.symbol == s][["date", "close", "adj"]].drop_duplicates("date")
        m = o.merge(g, on="date", suffixes=("_o", "_k")).sort_values("date")
        m["r_o"] = m.close_o.pct_change(); m["r_k"] = m.close_k.pct_change()
        m["t_o"] = m.adj_close.pct_change(); m["t_k"] = m.adj.pct_change()
        full = m[m.date >= y0]
        sp = m.stock_splits.replace(0, 1).fillna(1)
        m = m[(sp == 1) & (sp.shift(-1).fillna(1) == 1) & (m.date >= y0)]
        d = (m.r_o - m.r_k).abs().dropna()
        if len(d) < 15:
            continue
        off = (d > 0.005).mean() * 100
        row = (lab, s, str(o.source.iloc[0]), round(off, 2), round(d.max() * 100, 3), len(d), m.date.min().date(), m.date.max().date())
        if best is None or off < best[3]:
            best = row
            bad = m.loc[d[d > 0.005].index]
            detail[lab] = bad[["date", "close_o", "close_k", "r_o", "r_k"]].head(8)
            tt = full.dropna(subset=["t_o", "t_k"])
            best_tr = (len(tt), [(lab, s, str(r.date.date()), round(float(r.t_o) * 100, 3), round(float(r.t_k) * 100, 3),
                         "spin-off ex-date (holder's return)" if (lab, r.date) in spin_days else
                         "price differs too" if abs(r.r_o - r.r_k) > 0.0005 else
                         f"dividend {float(r.dividends):g} on our side" if float(r.dividends) > 0 else "no dividend on our side")
                        for r in tt[(tt.t_o - tt.t_k).abs() > 0.0005].itertuples()])
    if best is None:
        absent.append(lab)
    else:
        (agree if best[3] < 1 else disagree).append(best)
        tr_days += best_tr[0]; tr_off.extend(best_tr[1])

n_days = sum(a[5] for a in agree + disagree); n_off = sum(round(a[3] / 100 * a[5]) for a in agree + disagree)
say(f"\nRESULT {YEAR}: {len(agree)} members agree on >=99% of compared days, {len(disagree)} disagree, {len(absent)} not in this archive")
say(f"daily returns compared: {n_days:,}; differing by >0.5pp: {n_off:,} ({n_off / max(n_days, 1) * 100:.4f}%)")
nonyahoo = [a for a in agree if not a[2].startswith("yahoo")]
say(f"\nnon-Yahoo series confirmed ({len(nonyahoo)}): " + ", ".join(f"{a[0]}({a[6]}..{a[7]})" for a in nonyahoo))
if disagree:
    say("\nDISAGREE (our label, archive symbol, our source, % days off, max pp, days, from, to):")
    for d in sorted(disagree, key=lambda x: -x[3]):
        say(f"  {d}")
        say("    " + detail[d[0]].round(4).to_string(index=False).replace("\n", "\n    "))
few = [a for a in agree if a[3] > 0]
if few:
    say("\nagreeing names with a few differing days:")
    for a in sorted(few, key=lambda x: -x[3])[:10]:
        say(f"  {a}")
        say("    " + detail[a[0]].round(4).to_string(index=False).replace("\n", "\n    "))
if tr_days:
    from collections import Counter
    say(f"\nTOTAL RETURNS (adjusted close, tests dividends): {tr_days:,} member-day returns compared; differing by >0.05pp: {len(tr_off):,}")
    say("  by reason: " + str(dict(Counter("dividend on our side" if x[5].startswith("dividend") else x[5] for x in tr_off))))
    for x in sorted(tr_off, key=lambda x: (x[0], x[2])): say(f"  {x}")
else:
    say("\n(no adjusted close in this archive: total returns not compared)")
if WATCH:
    say("\nWATCHLIST:")
    allr = {a[0]: a for a in agree + disagree}
    for w in WATCH:
        say(f"  {w:12s} " + (str(allr[w]) if w in allr else ("not in archive" if w in absent else "not a member / no prices")))
(ROOT / "data" / f"xcheck_{name}_{YEAR}.txt").write_text("\n".join(out))
