"""Settle each ours-vs-CRSP disagreement with the published S&P 500's own daily return.

For every date in FLAGGED (full_crsp_check.py's flagged.csv: label, permno, date, ours, ret) the
index's daily return is rebuilt from every member's CRSP RET, weighted by CRSP market cap at the
previous close (members and permnos from the release's membership and PERMNO_MAP). Then, for each
flagged member that day, the member's CRSP return is swapped for ours: whichever version leaves the
smaller gap to the published index wins, if the gap shrinks by at least MIN_BP basis points;
otherwise the index cannot tell (a small member, or a small difference).
Usage (CRSP CSV under kag/): python tools/index_adjudicate.py FLAGGED.csv PERMNO_MAP.csv MEMBERSHIP.parquet SPX.csv OUT.csv [MIN_BP=2]
Read-only. Added 2026-10-16 (the accuracy pass)."""
import glob, sys
import numpy as np, pandas as pd

ff, mpf, mf, sf, out = sys.argv[1:6]
MIN = float(sys.argv[6]) if len(sys.argv) > 6 else 2.0
fl = pd.read_csv(ff, parse_dates=["date"])
mp = pd.read_csv(mpf)
M = {(r.label, r.year): int(r.permno) for r in mp.itertuples()}
m = pd.read_parquet(mf); m["start"] = pd.to_datetime(m.start); m["end"] = pd.to_datetime(m.end).fillna(pd.Timestamp("2100-01-01"))
pre = pd.read_csv("pre1996/membership.csv", parse_dates=["start", "end"])
spx = pd.read_csv(sf, parse_dates=["Date"]).set_index("Date").Close.sort_index().pct_change()
dates = sorted(set(fl.date))
need = set()
allcal = set()
f = glob.glob("kag/**/*.csv", recursive=True)[0]
# trading calendar from the index, previous day for each flagged date
cal = list(spx.index)
prev = {d: cal[cal.index(d) - 1] for d in dates if d in spx.index and cal.index(d) > 0}
want_days = set(prev) | set(prev.values())
rows = []
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in {"permno", "date", "prc", "ret", "shrout"}, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    ch = ch[ch.date.isin(want_days)]
    if len(ch):
        rows.append(ch)
c = pd.concat(rows)
for k in ("prc", "ret", "shrout"):
    c[k] = pd.to_numeric(c[k], errors="coerce")
c["cap"] = c.prc.abs() * c.shrout
C = c.set_index(["date", "permno"])
res = []
for d in dates:
    if d not in prev or pd.isna(spx.get(d)):
        continue
    d0 = prev[d]
    if d < pd.Timestamp("1996-01-02"):
        mem = pre[(pre.start <= d) & (pre.end > d)][["label", "permno"]].values.tolist()
    else:
        labs = m[(m.start <= d) & (m.end > d)].ticker
        mem = [(l, M.get((l, d.year))) for l in labs]
    ws, rs, lab_perm = [], [], {}
    for l, q in mem:
        if q is None or (d0, q) not in C.index or (d, q) not in C.index:
            continue
        cap = C.loc[(d0, q), "cap"]; r = C.loc[(d, q), "ret"]
        if isinstance(cap, pd.Series): cap = cap.iloc[0]
        if isinstance(r, pd.Series): r = r.iloc[0]
        if pd.isna(cap) or pd.isna(r):
            continue
        ws.append(cap); rs.append(r); lab_perm[l] = (cap, r)
    W = np.array(ws); tot = W.sum()
    base = float(np.dot(W, rs) / tot)
    e0 = (spx[d] - base) * 1e4
    for r in fl[fl.date == d].itertuples():
        if r.label not in lab_perm:
            res.append(dict(label=r.label, date=d.date(), w_pct=np.nan, resid_crsp_bp=e0, resid_ours_bp=np.nan, verdict="not weighed")); continue
        w = lab_perm[r.label][0] / tot
        e1 = e0 - w * (r.ours - r.ret) * 1e4
        v = "crsp" if abs(e0) + MIN <= abs(e1) else ("ours" if abs(e1) + MIN <= abs(e0) else "undecided")
        res.append(dict(label=r.label, date=d.date(), w_pct=round(w * 100, 3), ours=r.ours, ret=r.ret,
                        resid_crsp_bp=round(e0, 2), resid_ours_bp=round(e1, 2), verdict=v))
o = pd.DataFrame(res); o.to_csv(out, index=False)
pd.set_option("display.width", 200); pd.set_option("display.max_rows", 400)
print(o.verdict.value_counts().to_string())
print(o[o.verdict != "undecided"].to_string())
