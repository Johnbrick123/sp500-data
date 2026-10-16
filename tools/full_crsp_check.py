"""Every member-day since 1970 against CRSP's daily total return (RET), in one run.

1. Each label's CRSP permno, year by year: the 1970-1995 spells carry theirs (pre1996/membership.csv);
   for 1996 on, the permno whose daily RET correlates best with the label's own daily returns that year
   among the 1,500 largest CRSP permnos at the start of the year (plus every permno we already use).
   A label-year whose best correlation is under 0.9 is reported as unmapped.
2. For every member-day (published membership) the label's total return (adj_close[t]/adj_close[t-1]-1)
   against the mapped permno's RET. Days over TOL percentage points apart are listed.
Usage (CRSP CSV unpacked under kag/; prices and membership from the release):
  python tools/full_crsp_check.py prices.parquet membership_intervals.parquet OUTDIR [TOL=0.5] [LAST_YEAR=2024]
Read-only. Added 2026-10-16 (the accuracy pass)."""
import glob, os, sys
import numpy as np, pandas as pd

pf, mf, out = sys.argv[1:4]
TOL = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
LAST = int(sys.argv[5]) if len(sys.argv) > 5 else 2024
os.makedirs(out, exist_ok=True)
pre = pd.read_csv("pre1996/membership.csv", parse_dates=["start", "end"])
known = set(pre.permno) | set(pd.read_csv("crsp_permnos.csv").permno)
f = glob.glob("kag/**/*.csv", recursive=True)[0]
COLS = {"permno", "date", "prc", "ret", "shrout"}

# pass 1: each permno's market cap on its first day of each year (the year's first trading day, or
# its listing day) -> the year's candidates: the 1,500 largest at the start of the year, plus any
# permno listing during the year at least as large as the 1,500th (spin-offs, IPOs)
fy = []
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in COLS, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    ch["y"] = ch.date.dt.year
    ch["cap"] = pd.to_numeric(ch.prc, errors="coerce").abs() * pd.to_numeric(ch.shrout, errors="coerce")
    ch = ch.dropna(subset=["cap"]).sort_values("date")
    fy.append(ch.groupby(["y", "permno"]).agg(d=("date", "first"), cap=("cap", "first")).reset_index())
fy = pd.concat(fy).sort_values("d").groupby(["y", "permno"]).first().reset_index()
cand = {}
for y, g in fy.groupby("y"):
    d0 = g.d.min()
    start = g[g.d == d0].sort_values("cap", ascending=False)
    top = start.head(1500)
    thr = top.cap.min() if len(top) else 0
    late = g[(g.d > d0) & (g.cap >= thr)]
    cand[y] = set(top.permno) | set(late.permno) | known
print("candidate sets built", flush=True)

# pass 2: RET for each year's candidates
parts = []
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in COLS, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    ch["y"] = ch.date.dt.year
    keep = np.zeros(len(ch), bool)
    for y, idx in ch.groupby("y").groups.items():
        keep[ch.index.get_indexer(idx)] = ch.loc[idx, "permno"].isin(cand.get(y, set())).values
    ch = ch[keep]
    ch["ret"] = pd.to_numeric(ch.ret, errors="coerce").astype("float32")
    parts.append(ch[["permno", "date", "ret", "y"]])
c = pd.concat(parts, ignore_index=True)
print(f"{len(c):,} CRSP rows kept", flush=True)
RY = {y: g.pivot_table(index="date", columns="permno", values="ret") for y, g in c.groupby("y")}
del c

# our daily total returns
p = pd.read_parquet(pf, columns=["ticker", "date", "adj_close", "close", "source"])
p["date"] = pd.to_datetime(p.date)
p = p.sort_values(["ticker", "date"])
p["ours"] = p.groupby("ticker").adj_close.pct_change()
O = p.pivot_table(index="date", columns="ticker", values="ours")
SRC = p.set_index(["ticker", "date"]).source
m = pd.read_parquet(mf)
m["start"] = pd.to_datetime(m.start); m["end"] = pd.to_datetime(m.end).fillna(pd.Timestamp("2100-01-01"))

# 1. mapping
rows = []
for y in range(1970, LAST + 1):
    a, b = pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-12-31")
    labs = sorted(set(m[(m.start <= b) & (m.end > a)].ticker))
    if y < 1996:
        pr = pre[(pre.start <= b) & (pre.end > a)]
        for r in pr.drop_duplicates("label").itertuples():
            rows.append((r.label, y, r.permno, np.nan, -1))
        labs = [l for l in labs if l not in set(pr.label)]
        if not labs:
            continue
    if y not in RY:
        continue
    Ry = RY[y]; dd = Ry.index
    Ry = Ry[[q for q in Ry.columns if q in cand.get(y, set())]]
    Ry = Ry.loc[:, Ry.notna().sum() >= 20]
    Oy = O.reindex(index=dd, columns=[l for l in labs if l in O.columns])
    Oy = Oy.loc[:, Oy.notna().sum() >= 20]
    if Oy.empty:
        continue
    A = (Oy - Oy.mean()).fillna(0).values; B = (Ry - Ry.mean()).fillna(0).values
    num = A.T @ B
    den = np.sqrt((A ** 2).sum(0))[:, None] * np.sqrt((B ** 2).sum(0))[None, :]
    C = num / np.where(den == 0, np.nan, den)
    best = np.nanargmax(np.nan_to_num(C, nan=-2), axis=1)
    for i, l in enumerate(Oy.columns):
        rows.append((l, y, int(Ry.columns[best[i]]), float(C[i, best[i]]), int(Oy[l].notna().sum())))
    print(y, len(Oy.columns), "labels mapped", flush=True)
mp = pd.DataFrame(rows, columns=["label", "year", "permno", "corr", "n"])
mp.to_csv(f"{out}/permno_map.csv", index=False)
weak = mp[(mp["corr"] < 0.9) & (mp["n"] > 0)]
print(f"\nmapping: {len(mp)} label-years; under 0.9 correlation: {len(weak)}")
print(weak.sort_values("corr").head(80).to_string())

# 2. member-day comparison
M = {(r.label, r.year): r.permno for r in mp.itertuples() if not (r.n > 0 and r.corr < 0.9)}
res = []
spans = pd.concat([m.rename(columns={"ticker": "label"})[["label", "start", "end"]]])
spans = spans[spans.start < pd.Timestamp(f"{LAST + 1}-01-01")]
pre1996 = pre.rename(columns={})[["label", "start", "end"]]
spans = pd.concat([pre1996, spans[spans.end > pd.Timestamp("1996-01-02")].assign(start=lambda x: x.start.clip(lower=pd.Timestamp("1996-01-02")))])
for r in spans.itertuples():
    for y in range(max(r.start.year, 1970), min(r.end.year, LAST) + 1):
        q = M.get((r.label, y))
        if q is None or y not in RY or q not in RY[y].columns or r.label not in O.columns:
            continue
        a = max(r.start, pd.Timestamp(f"{y}-01-01")); b = min(r.end - pd.Timedelta(days=1), pd.Timestamp(f"{y}-12-31"))
        Ry = RY[y]; dd = Ry.index[(Ry.index >= a) & (Ry.index <= b)]
        if not len(dd):
            continue
        o = O[r.label].reindex(dd); k = Ry.loc[dd, q]
        res.append(pd.DataFrame({"label": r.label, "permno": q, "date": dd, "ours": o.values, "ret": k.values}))
x = pd.concat(res, ignore_index=True)
both = x.dropna(subset=["ours", "ret"]).copy()
both["diff_pp"] = (both.ours - both.ret) * 100
bad = both[both.diff_pp.abs() > TOL].copy()
bad["source"] = [SRC.get((l, d), "") for l, d in zip(bad.label, bad.date)]
bad.to_csv(f"{out}/flagged.csv", index=False)
both["y"] = both.date.dt.year
summ = both.assign(bad=both.diff_pp.abs() > TOL, big=both.diff_pp.abs() > 2).groupby("y").agg(days=("bad", "size"), over_tol=("bad", "sum"), over_2pp=("big", "sum"))
summ.to_csv(f"{out}/by_year.csv")
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 500)
print(f"\n{len(both):,} member-days compared; within {TOL}pp: {100 * (1 - len(bad) / len(both)):.4f}%; over 2pp: {(both.diff_pp.abs() > 2).sum()}; "
      f"member-days with no CRSP return: {x.ret.isna().sum()}, with no return of ours: {x.ours.isna().sum()}")
print(summ.to_string())
print("\nby source of our row:\n" + bad.groupby(bad.source.astype(str)).size().to_string())
print("\nlabels with most flagged days:\n" + bad.groupby("label").size().sort_values(ascending=False).head(60).to_string())
print("\nlargest:\n" + bad.reindex(bad.diff_pp.abs().sort_values(ascending=False).index).head(100).to_string())
