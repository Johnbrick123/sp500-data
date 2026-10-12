"""Recover a missing member's prices from a Kaggle price archive, for a symbol
and window you name - for archives fetch_kaggle.py does not read (tsaustin's
Alpha Vantage dump, jacksoncrow, Stooq) and for symbols an archive files a dead
company under (Life Technologies is tsaustin's LIFE until 2014; aTyr after).

Usage: python fetch_archive.py NAME "LABEL=SYMBOL@START:END ..."   (archive unpacked in kag/)
  Only rows START..END (the dead company's own trading days) are kept.
  Refused when the label already has a raw file, when the window holds a split
  (the archive's split convention would need checking by hand), or when fewer
  than 20 rows come back. Dividends are read from the archive's dividend column
  when it has one; for a company that paid none, nothing is lost.
-> data/raw/LABEL.parquet (source="archive-NAME", raw closes), data/archive_fetch_report.txt"""
import glob, os, re, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
name = sys.argv[1]
specs = []
for s in sys.argv[2].split():
    lab, rest = s.split("=", 1)
    sym, win = rest.split("@", 1)
    a, b = win.split(":")
    specs.append((lab, sym.upper(), pd.Timestamp(a), pd.Timestamp(b)))
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
need = {s for _, s, _, _ in specs}

frames = []
files = [f for f in glob.glob("kag/**/*", recursive=True) if os.path.isfile(f) and f.lower().endswith((".csv", ".txt"))]
for f in files:
    base = re.sub(r"\.(csv|txt)$", "", os.path.basename(f), flags=re.I)
    base = re.sub(r"\.us$", "", base, flags=re.I).upper().replace(".", "-")
    try:
        head = pd.read_csv(f, nrows=3)
    except Exception:
        continue
    cols = {c.lower().strip(): c for c in head.columns}
    scol = next((cols[c] for c in ("symbol", "ticker") if c in cols), None)
    if scol:
        for ch in pd.read_csv(f, chunksize=2_000_000):
            ch = ch[ch[scol].astype(str).str.upper().isin(need)]
            if len(ch):
                frames.append(ch.rename(columns={scol: "symbol"}))
    elif base in need:
        frames.append(pd.read_csv(f).assign(symbol=base))
if not frames:
    say("no rows for any requested symbol"); open("data/archive_fetch_report.txt", "w").write("\n".join(out)); sys.exit(0)
a = pd.concat(frames, ignore_index=True)
a.columns = [c.lower().strip().replace(" ", "_") for c in a.columns]
a["date"] = pd.to_datetime(a["date"], errors="coerce", utc=True).dt.tz_localize(None)
say(f"archive {name}: {len(a):,} rows for {sorted(a.symbol.astype(str).str.upper().unique())}; columns {list(a.columns)}")

for lab, sym, lo, hi in specs:
    g = a[(a.symbol.astype(str).str.upper() == sym) & (a.date >= lo) & (a.date <= hi)].drop_duplicates("date").sort_values("date")
    say(f"\n{lab} <- {sym} {lo.date()}..{hi.date()}: {len(g)} rows" + (f", {g.date.min().date()}..{g.date.max().date()}" if len(g) else ""))
    if (RAW / f"{lab}.parquet").exists():
        say("  REFUSED: the label already has a raw file"); continue
    if len(g) < 20:
        say("  REFUSED: fewer than 20 rows"); continue
    sc = next((c for c in ("split_coefficient", "split_ratio") if c in g.columns), None)
    if sc and (pd.to_numeric(g[sc], errors="coerce").fillna(1.0) != 1.0).any():
        say(f"  REFUSED: a split inside the window: {g[pd.to_numeric(g[sc], errors='coerce').fillna(1.0) != 1.0][['date', sc]].values.tolist()}"); continue
    div = pd.to_numeric(g["dividend"], errors="coerce").fillna(0.0) if "dividend" in g.columns else pd.Series(0.0, index=g.index)
    df = pd.DataFrame({"date": g.date, "open": g.open.astype(float), "high": g.high.astype(float), "low": g.low.astype(float),
                       "close": g.close.astype(float), "volume": pd.to_numeric(g.volume, errors="coerce"),
                       "dividends": div.values, "stock_splits": 1.0, "ticker": lab, "source": f"archive-{name}"})
    RAW.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RAW / f"{lab}.parquet", index=False)
    say(f"  RECOVERED: first close {df.close.iloc[0]} ({df.date.iloc[0].date()}), last close {df.close.iloc[-1]} ({df.date.iloc[-1].date()}), "
        f"{int((df.dividends > 0).sum())} dividends, median volume {df.volume.median():,.0f}")
open("data/archive_fetch_report.txt", "w").write("\n".join(out))
