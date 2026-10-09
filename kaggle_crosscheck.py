"""
Second-source check for one year: compare our daily returns with an old
snapshot of the daily-updated Kaggle dataset andrewmvd/sp-500-stocks (Yahoo-
derived, frozen at the version's date). Covers every name that was an S&P 500
member on the snapshot date - including companies delisted since, which
Nasdaq's API no longer serves. Usage: python kaggle_crosscheck.py YEAR VERSION
-> data/kaggle_crosscheck_YEAR.txt
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

ours = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "stock_splits", "source"])
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
for s, g in k[(k.date >= y0 - pd.Timedelta(days=10)) & (k.date <= y1)].groupby("symbol"):
    lab = our_label(s)
    if lab is None:
        missing.append(s); continue
    o = oy[oy.ticker == lab][["date", "close", "stock_splits"]]
    m = o.merge(g[["date", "close"]], on="date", suffixes=("_o", "_k")).sort_values("date")
    m["r_o"] = m.close_o.pct_change(); m["r_k"] = m.close_k.pct_change()
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
if offdays:
    say("\nEVERY DIFFERING DAY (snapshot symbol, our label, date, our close, snapshot close, our return %, snapshot return %):")
    for o in sorted(offdays, key=lambda x: (x[2], x[1])): say(f"  {o}")
(ROOT / "data" / f"kaggle_crosscheck_{YEAR}.txt").write_text("\n".join(out))
