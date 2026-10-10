"""Derive the dividends an archived price file implies, from its close and
adjusted close: an ex-date is where adj/close steps, and the amount is
D = previous close x (1 - (adj/close before) / (adj/close after)).
For series whose own source has no dividend record (Tiingo's Noble Energy).
Usage: python archive_dividends.py TICKER,TICKER [FROM] [TO]   (archive unpacked in kag/)
-> data/archive_dividends_<slug-name>.csv and .txt"""
import glob, os, re, sys
import pandas as pd
tks = [t.strip().upper() for t in sys.argv[1].split(",") if t.strip()]
lo = pd.Timestamp(sys.argv[2] if len(sys.argv) > 2 else "1990-01-01"); hi = pd.Timestamp(sys.argv[3] if len(sys.argv) > 3 else "2030-12-31")
name = os.environ.get("ARCHIVE_NAME", "archive")
files = [f for f in glob.glob("kag/**/*", recursive=True) if os.path.isfile(f) and f.lower().endswith((".csv", ".txt"))]
rows, out = [], []
def say(s=""): print(s, flush=True); out.append(str(s))
def load(t):
    for f in files:                                   # one file per ticker
        b = re.sub(r"\.(csv|txt)$", "", os.path.basename(f), flags=re.I)
        if b.upper() == t:
            return pd.read_csv(f)
    for f in files:                                   # or one big table
        head = pd.read_csv(f, nrows=2); cols = {c.lower(): c for c in head.columns}
        sc = next((cols[c] for c in ("symbol", "ticker") if c in cols), None)
        if sc:
            parts = [ch[ch[sc].astype(str).str.upper() == t] for ch in pd.read_csv(f, chunksize=2_000_000)]
            g = pd.concat(parts)
            if len(g): return g
    return None
for t in tks:
    g = load(t)
    if g is None or g.empty:
        say(f"{t}: not in archive"); continue
    cols = {c.lower().strip(): c for c in g.columns}
    dc = cols.get("date"); cc = cols.get("close")
    ac = next((cols[c] for c in ("adj close", "adj_close", "adjclose", "adjusted_close", "close_adjusted") if c in cols), None)
    if not (dc and cc and ac):
        say(f"{t}: archive has no adjusted close"); continue
    g = g[[dc, cc, ac]].rename(columns={dc: "date", cc: "close", ac: "adj"})
    g["date"] = pd.to_datetime(g["date"], errors="coerce", utc=True).dt.tz_localize(None)
    g = g.dropna().sort_values("date").drop_duplicates("date").reset_index(drop=True)
    r = g.adj / g.close
    step = r.shift(1) / r                              # < 1 on an ex-date (factor 1 - D/prev close)
    for i in g.index[1:]:
        f = step[i]
        if pd.notna(f) and f < 0.9995 and lo <= g.date[i] <= hi:
            d = g.close[i - 1] * (1 - f)
            rows.append((t, str(g.date[i].date()), round(d, 4), round(float(g.close[i - 1]), 4), round(1 - f, 6)))
    say(f"{t}: {sum(1 for x in rows if x[0] == t)} ex-dates in {g.date.min().date()}..{g.date.max().date()}")
df = pd.DataFrame(rows, columns=["ticker", "date", "dividend", "prev_close", "yield"])
os.makedirs("data", exist_ok=True)
df.to_csv(f"data/archive_dividends_{name}.csv", index=False)
say(df.to_string(index=False))
open(f"data/archive_dividends_{name}.txt", "w").write("\n".join(out))
