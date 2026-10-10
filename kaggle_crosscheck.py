"""
Second-source check for one year: compare our daily returns with an old
snapshot of the daily-updated Kaggle dataset andrewmvd/sp-500-stocks (Yahoo-
derived, frozen at the version's date). Covers every name that was an S&P 500
member on the snapshot date - including companies delisted since, which
Nasdaq's API no longer serves. Usage: python kaggle_crosscheck.py YEAR VERSION
-> data/kaggle_crosscheck_YEAR.txt

Two comparisons: price returns (close to close: tests the bars) and total
returns (adj_close to adj_close: tests the dividends, which the price check
cannot see). Total returns are expected to differ on spin-off ex-dates
(spinoffs.csv: we book the holder's actual return), on distributions over 5%
of the price (we use close / (close + D)), and wherever a bar of ours was
replaced; every other differing day is a dividend to look at.
"""
import io, os, re, sys, zipfile, urllib.request
from pathlib import Path
import pandas as pd

YEAR, VER = int(sys.argv[1]), int(sys.argv[2])
ROOT = Path(__file__).parent
TOK = os.environ["KAGGLE_API_TOKEN"].strip()
out = []
def say(s=""): print(s, flush=True); out.append(str(s))

u = f"https://www.kaggle.com/api/v1/datasets/download/andrewmvd/sp-500-stocks?datasetVersionNumber={VER}"
b = urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": f"Bearer {TOK}"}), timeout=300).read()
z = zipfile.ZipFile(io.BytesIO(b))
k = pd.read_csv(z.open(next(n for n in z.namelist() if "stock" in n.lower() and n.endswith(".csv"))))
k.columns = [c.strip().lower().replace(" ", "_") for c in k.columns]
k["date"] = pd.to_datetime(k["date"]); k["symbol"] = k["symbol"].astype(str).str.upper().str.replace(".", "-", regex=False)
k = k.dropna(subset=["close"])
say(f"Kaggle andrewmvd/sp-500-stocks version {VER}: {k.symbol.nunique()} symbols, {k.date.min().date()}..{k.date.max().date()}")

ours = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "adj_close", "dividends", "stock_splits", "source"])
ours["date"] = pd.to_datetime(ours["date"])
ren = dict(pd.read_csv(ROOT / "data" / "universe" / "renames.csv").values) if (ROOT / "data" / "universe" / "renames.csv").exists() else {}
y0, y1 = pd.Timestamp(f"{YEAR}-01-01"), pd.Timestamp(f"{YEAR}-12-31")
oy = ours[(ours.date >= y0 - pd.Timedelta(days=10)) & (ours.date <= y1)]
labels = set(oy.ticker)

ALIASES = {"FI": "FISV"}   # snapshot-era symbols for names whose ticker later changed back

def our_label(s):
    for c in (ren.get(s, s), s, ALIASES.get(s, s)):
        if c in labels:
            return c
    dated = sorted(l for l in labels if re.match(rf"^{re.escape(s)}-\d{{6}}$", l))
    return dated[0] if dated else None

agree, disagree, missing, offdays = [], [], [], []
tr_days, tr_off = 0, []          # total-return comparison
spin_days = set()
if (ROOT / "spinoffs.csv").exists():
    _sp = pd.read_csv(ROOT / "spinoffs.csv", parse_dates=["ex_date"])
    spin_days = set(zip(_sp.ticker, _sp.ex_date))
has_adj = "adj_close" in k.columns
for s, g in k[(k.date >= y0 - pd.Timedelta(days=10)) & (k.date <= y1)].groupby("symbol"):
    lab = our_label(s)
    if lab is None:
        missing.append(s); continue
    o = oy[oy.ticker == lab][["date", "close", "adj_close", "dividends", "stock_splits"]]
    m = o.merge(g[["date", "close"] + (["adj_close"] if has_adj else [])], on="date", suffixes=("_o", "_k")).sort_values("date")
    m["r_o"] = m.close_o.pct_change(); m["r_k"] = m.close_k.pct_change()
    if has_adj:
        t = m.copy()
        t["t_o"] = t.adj_close_o.pct_change(); t["t_k"] = t.adj_close_k.pct_change()
        t = t[(t.date >= y0)].dropna(subset=["t_o", "t_k"])
        tr_days += len(t)
        for ix in t.index[(t.t_o - t.t_k).abs() > 0.0005]:
            r = t.loc[ix]
            why = ("spin-off ex-date (holder's return)" if (lab, r.date) in spin_days else
                   "price differs too" if abs(r.r_o - r.r_k) > 0.0005 else
                   f"dividend {float(r.dividends):g} on our side" if float(r.dividends or 0) > 0 else "no dividend on our side")
            tr_off.append((s, lab, str(r.date.date()), round(float(r.t_o) * 100, 3), round(float(r.t_k) * 100, 3), why))
    sp = m.stock_splits.replace(0, 1).fillna(1)
    m = m[(sp == 1) & (sp.shift(-1).fillna(1) == 1) & (m.date >= y0)]
    d = (m.r_o - m.r_k).abs().dropna()
    if len(d) < 20:
        missing.append(f"{s}({len(d)}d)"); continue
    off = (d > 0.005).mean() * 100
    for ix in d[d > 0.005].index:
        r = m.loc[ix]
        offdays.append((s, lab, str(r.date.date()), round(float(r.close_o), 4), round(float(r.close_k), 4),
                        round(float(r.r_o) * 100, 2), round(float(r.r_k) * 100, 2)))
    src = str(oy[oy.ticker == lab].source.iloc[0])
    (agree if off < 1 else disagree).append((s, lab, src, round(off, 2), round(d.max() * 100, 3), len(d)))

n_days = sum(a[5] for a in agree + disagree)
n_off = sum(round(a[3] / 100 * a[5]) for a in agree + disagree)
by_src = pd.Series([a[2] for a in agree + disagree]).value_counts().to_dict()
say(f"\nRESULT {YEAR}: {len(agree)} names agree on >=99% of days, {len(disagree)} disagree; sources compared {by_src}")
say(f"daily returns compared: {n_days:,}; differing by >0.5pp: {n_off:,} ({n_off / max(n_days, 1) * 100:.4f}%)")
non_yahoo = [a for a in agree if a[2] != "yahoo"]
say(f"\nnon-Yahoo series confirmed by this second source ({len(non_yahoo)}): " + ", ".join(f"{a[1]}" for a in sorted(non_yahoo)))
if disagree:
    say("\nDISAGREE (kaggle symbol, our label, our source, % days off, max pp, days):")
    for d in sorted(disagree, key=lambda x: -x[3]): say(f"  {d}")
if missing:
    say(f"\nin the snapshot but not matched to a label of ours with {YEAR} prices: {missing}")
if has_adj:
    say(f"\nTOTAL RETURNS (adj close, tests dividends): {tr_days:,} compared; differing by >0.05pp: {len(tr_off):,}")
    from collections import Counter
    say("  by reason: " + str(dict(Counter(x[5].split(" on our")[0] if x[5].startswith("dividend") else x[5] for x in tr_off))))
    for x in sorted(tr_off, key=lambda x: (x[1], x[2])): say(f"  {x}")
if offdays:
    say("\nEVERY DIFFERING DAY (snapshot symbol, our label, date, our close, snapshot close, our return %, snapshot return %):")
    for o in sorted(offdays, key=lambda x: (x[2], x[1])): say(f"  {o}")
(ROOT / "data" / f"kaggle_crosscheck_{YEAR}.txt").write_text("\n".join(out))
