"""
Inventory a Kaggle dataset of delisted-stock prices against our missing index
members. Expects the dataset already downloaded and unzipped into ./kag/.
Writes data/kaggle_probe.txt. Read-only on the dataset.
"""
import glob, os, re, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent
SUFFIX = re.compile(r"-(\d{6})$")
out = []
def say(s=""): print(s, flush=True); out.append(str(s))

files = sorted(p for p in glob.glob("kag/**/*", recursive=True) if os.path.isfile(p))
say(f"{len(files)} files, {sum(os.path.getsize(f) for f in files)/1e6:.1f} MB total")
by_ext = {}
for f in files:
    by_ext.setdefault(os.path.splitext(f)[1].lower(), []).append(f)
for ext, fs in sorted(by_ext.items(), key=lambda x: -len(x[1])):
    say(f"  {ext or '(none)'}: {len(fs)} files, e.g. {', '.join(os.path.basename(x) for x in fs[:5])}")

# membership -> missing labels
iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
have = {p.stem for p in (ROOT / "data" / "raw").glob("*.parquet")}
span = iv.groupby("ticker").agg(m_start=("start", "min"), m_end=("end", "max"))
missing = span[~span.index.isin(have)]
say(f"\nour missing index members: {len(missing)}")

def to_dt(x):
    x = pd.Series(x)
    if pd.api.types.is_numeric_dtype(x) and x.dropna().between(19000101, 21000101).all():
        return pd.to_datetime(x.astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    return pd.to_datetime(x, errors="coerce")


def load_any(f, nrows=None):
    ext = os.path.splitext(f)[1].lower()
    if ext == ".parquet": return pd.read_parquet(f)
    if ext in (".csv", ".txt"): return pd.read_csv(f, nrows=nrows, low_memory=False)
    if ext == ".json": return pd.read_json(f)
    return None

# Two common layouts: one big table with a ticker column, or one file per ticker.
tables = []
for f in files:
    try:
        df = load_any(f, nrows=5)
    except Exception as e:
        say(f"  cannot read {f}: {type(e).__name__}"); continue
    if df is None: continue
    cols = [c.lower() for c in df.columns]
    if len(tables) < 8: say(f"  {os.path.basename(f)[:50]:50s} cols: {cols[:12]}")
    tables.append((f, cols))

tick_col = {"ticker", "symbol", "tic", "code"}
per_file = [f for f, cols in tables if not (set(cols) & tick_col)]
big = [f for f, cols in tables if set(cols) & tick_col]
coverage = {}
if big:
    say(f"\n{len(big)} file(s) carry a ticker column - reading fully")
    for f in big:
        df = load_any(f)
        tc = next(c for c in df.columns if c.lower() in tick_col)
        dc = next((c for c in df.columns if c.lower() in ("date", "datetime", "timestamp", "time")), None)
        df[tc] = df[tc].astype(str).str.upper().str.replace(".", "-", regex=False)
        if dc: df[dc] = to_dt(df[dc])
        g = df.groupby(tc)[dc].agg(["min", "max", "count"]) if dc else df.groupby(tc).size().to_frame("count")
        if len(big) <= 10: say(f"  {os.path.basename(f)}: {len(df):,} rows, {len(g):,} tickers" + (f", {g['min'].min().date()}..{g['max'].max().date()}" if dc and g['min'].notna().any() else ""))
        for t, r in g.iterrows():
            coverage[t] = (r.get("min"), r.get("max"), int(r["count"]))
if per_file:
    say(f"\n{len(per_file)} per-ticker file(s) - inventorying by file name")
    for f in per_file:
        t = re.sub(r"\.(us|csv|txt|parquet|json)$", "", os.path.basename(f), flags=re.I)
        t = re.sub(r"\.(us|csv|txt)$", "", t, flags=re.I).upper().replace(".", "-")
        try:
            df = load_any(f)
            dc = next((c for c in df.columns if c.lower() in ("date", "datetime", "timestamp", "time")), df.columns[0])
            d = to_dt(df[dc])
            coverage[t] = (d.min(), d.max(), len(df))
        except Exception:
            coverage[t] = (None, None, 0)
say(f"\ndataset tickers: {len(coverage):,}")
if coverage:
    allmin = min(v[0] for v in coverage.values() if v[0] is not None and pd.notna(v[0]))
    allmax = max(v[1] for v in coverage.values() if v[1] is not None and pd.notna(v[1]))
    say(f"overall date range: {allmin.date()}..{allmax.date()}")

# match our missing labels by bare ticker + date overlap
hits = []
for lab, r in missing.iterrows():
    bare = SUFFIX.sub("", lab)
    stop = r.m_end if pd.notna(r.m_end) else pd.Timestamp.today()
    for key in (bare, bare + "Q", bare.replace("-", ".")):
        if key in coverage and coverage[key][0] is not None:
            s, e, n = coverage[key]
            ov = (min(e, stop) - max(s, r.m_start)).days
            if ov > 120:
                hits.append((lab, key, s.date(), e.date(), n, ov))
            break
say(f"\nmissing members with an overlapping series in the dataset: {len(hits)} of {len(missing)}")
for h in hits:
    say(f"  {h[0]:14s} as {h[1]:8s} {h[2]}..{h[3]} {h[4]:6,} rows  overlap {h[5]} days")
# the famous ones, explicitly
say("\nspot checks:")
for t in ["LEH", "LEHMQ", "BSC", "CFC", "BMC", "MOLX", "LIFE", "IGT", "BGEN", "EOP", "CEPH", "WAMUQ", "ENRNQ", "DOW", "AAPL"]:
    v = coverage.get(t)
    say(f"  {t:7s} " + (f"{v[0].date() if v[0] is not None else '?'}..{v[1].date() if v[1] is not None else '?'} {v[2]:,} rows" if v else "not in dataset"))
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data" / "kaggle_probe.txt").write_text("\n".join(out))
