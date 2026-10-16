"""Every member-day since 1970 against CRSP's daily total return (RET), in one run.

1. Each label's CRSP permno, month by month (a year's best match, overridden for a month whose own
   best match is clearly better): the 1970-1995 spells carry theirs (pre1996/membership.csv);
   for 1996 on, the permno whose daily RET correlates best with the label's own daily returns that year
   among the 3,000 largest CRSP permnos at the start of the year (plus every permno we already use).
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
# its listing day) -> the year's candidates: the 3,000 largest at the start of the year, plus any
# permno listing during the year at least as large as the 3,000th (spin-offs, IPOs)
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
    top = start.head(3000)
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
            for mo in range(1, 13):
                rows.append((r.label, y, mo, r.permno, np.nan, -1, np.nan))
        labs = [l for l in labs if l not in set(pr.label)]
        if not labs:
            continue
    if y not in RY:
        continue
    Ry = RY[y]; dd = Ry.index
    Ry = Ry[[q for q in Ry.columns if q in cand.get(y, set())]]
    Ry = Ry.loc[:, Ry.notna().sum() >= 20]
    Oy = O.reindex(index=dd, columns=[l for l in labs if l in O.columns]).copy()
    # correlate on the label's member-days only (a label's rows outside its spells can be another
    # security: an OTC tail after a delisting, a when-issued market)
    for l in Oy.columns:
        sp = m[m.ticker == l]
        inm = np.zeros(len(dd), bool)
        for r in sp.itertuples():
            inm |= (dd >= r.start) & (dd < r.end)
        Oy.loc[~inm, l] = np.nan
    Oy = Oy.loc[:, Oy.notna().sum() >= 10]
    if Oy.empty:
        continue
    Bv = Ry.values
    mon = dd.month.values

    cols = {q: j for j, q in enumerate(Ry.columns)}

    def best(vals, msk, k=8):
        """The permno that fits the label's returns on these days: among the k best by correlation,
        the one with the largest share of days within 0.5pp (correlation alone cannot tell two share
        classes apart - UAA/UA, NWS/NWSA - nor, in a market-wide selloff, two similar stocks)."""
        a = vals[msk]; b = Bv[msk]
        ok = ~np.isnan(b)
        am = a - a.mean()
        with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
            __import__("warnings").simplefilter("ignore")
            bm = np.where(ok, b - np.nanmean(np.where(ok, b, np.nan), axis=0), 0.0)
        num = am @ bm
        den = np.sqrt((am ** 2).sum()) * np.sqrt((bm ** 2).sum(0))
        with np.errstate(invalid="ignore", divide="ignore"):
            cc = num / np.where(den == 0, np.nan, den)
        cc = np.where(ok.sum(0) >= max(5, int(0.8 * msk.sum())), cc, np.nan)
        if np.all(np.isnan(cc)):
            return None, np.nan, np.nan
        top = [j for j in np.argsort(-np.nan_to_num(cc, nan=-9))[:k] if not np.isnan(cc[j])]
        hit = [float(np.nanmean(np.where(ok[:, j], np.abs(a - np.where(ok[:, j], b[:, j], 0)) < 0.005, np.nan))) for j in top]
        j = top[int(np.argmax([h + 1e-6 * c for h, c in zip(hit, cc[top])]))]
        return int(Ry.columns[j]), float(cc[j]), float(hit[top.index(j)])

    def fit(vals, msk, q):
        b = Bv[msk, cols[q]]; a = vals[msk]; ok = ~np.isnan(b)
        if ok.sum() < max(5, int(0.8 * msk.sum())):
            return np.nan, np.nan
        return float(np.corrcoef(a[ok], b[ok])[0, 1]), float(np.mean(np.abs(a[ok] - b[ok]) < 0.005))

    for l in Oy.columns:
        vals = Oy[l].values
        msk = ~np.isnan(vals)
        qy, cy, hy = best(vals, msk)   # None when no single permno covers 80% of the year (a mid-year switch)
        # month by month: a permno can change inside a year (a merger that keeps the symbol, a
        # spin-off's when-issued days). A month keeps the year's permno unless that permno has no
        # prices that month, or the month's own best matches 80% of its days and 20 points more
        for mo in sorted(set(mon[msk])):
            mm = msk & (mon == mo)
            cq, hq = fit(vals, mm, qy) if qy is not None else (np.nan, np.nan)
            q, cm, hm = qy, cq, hq
            if qy is None or np.isnan(cq) or (hq < 0.95 and mm.sum() >= 8):
                # the month's own best takes over when it matches 80% of the month's days and clearly
                # better than the year's choice - which can itself be wrong when no permno covers 80% of
                # the year (Starwood 2006: the paired-share line to April, the new one after; the year's
                # best was another hotel stock matching about half the days)
                q2, c2, h2 = best(vals, mm)
                if q2 is not None and q2 != qy and c2 >= 0.9 and h2 >= 0.8 and not (hq >= h2 - 0.2):
                    q, cm, hm = q2, c2, h2
            if q is None:
                continue
            if mm.sum() < 8 and q == qy:
                cm = cy   # too few days to judge the month alone: the year's fit stands
            rows.append((l, y, int(mo), q, cm, int(mm.sum()), hm))
    print(y, len(Oy.columns), "labels mapped", flush=True)
mp = pd.DataFrame(rows, columns=["label", "year", "month", "permno", "corr", "n", "match"])
mp.to_csv(f"{out}/permno_map.csv", index=False)
# unmapped: a weak correlation AND fewer than half the days within 0.5pp (one bad day can pull a
# month's correlation under 0.9; that month must still be compared, or the check hides the error)
weak = mp[(mp["corr"] < 0.9) & (mp["n"] > 0) & ~(mp["match"] >= 0.5)]
print(f"\nmapping: {len(mp)} label-months; under 0.9 correlation: {len(weak)}; "
      f"label-months whose permno differs from the label's main permno that year: "
      f"{(mp.groupby(['label', 'year']).permno.transform(lambda s: s.mode().iloc[0]) != mp.permno).sum()}")
print(weak.sort_values("corr").head(80).to_string())

# 2. member-day comparison
M = {(r.label, r.year, r.month): r.permno for r in mp.drop(weak.index).itertuples()}
res = []
spans = pd.concat([m.rename(columns={"ticker": "label"})[["label", "start", "end"]]])
spans = spans[spans.start < pd.Timestamp(f"{LAST + 1}-01-01")]
pre1996 = pre.rename(columns={})[["label", "start", "end"]]
spans = pd.concat([pre1996, spans[spans.end > pd.Timestamp("1996-01-02")].assign(start=lambda x: x.start.clip(lower=pd.Timestamp("1996-01-02")))])
for r in spans.itertuples():
    for y in range(max(r.start.year, 1970), min(r.end.year, LAST) + 1):
        if y not in RY or r.label not in O.columns:
            continue
        a = max(r.start, pd.Timestamp(f"{y}-01-01")); b = min(r.end - pd.Timedelta(days=1), pd.Timestamp(f"{y}-12-31"))
        Ry = RY[y]; dd = Ry.index[(Ry.index >= a) & (Ry.index <= b)]
        for mo in sorted(set(dd.month)):
            q = M.get((r.label, y, mo))
            dm = dd[dd.month == mo]
            o = O[r.label].reindex(dm)
            if q is None or q not in Ry.columns:
                # an unmapped month split between two permnos (WBD 2022-04: Discovery's line to 4/8,
                # the new one from 4/11): each day takes the previous month's permno where it has a
                # return, else the next month's
                pq = M.get((r.label, y, mo - 1)) if mo > 1 else M.get((r.label, y - 1, 12))
                nq = M.get((r.label, y, mo + 1)) if mo < 12 else M.get((r.label, y + 1, 1))
                ks = [Ry.loc[dm, c] for c in (pq, nq) if c is not None and c in Ry.columns]
                if not ks:
                    continue
                k = ks[0].copy()
                for k2 in ks[1:]:
                    k = k.fillna(k2)
                q = pq if pq is not None else nq
            else:
                k = Ry.loc[dm, q]
            res.append(pd.DataFrame({"label": r.label, "permno": q, "date": dm, "ours": o.values, "ret": k.values}))
x = pd.concat(res, ignore_index=True)
both = x.dropna(subset=["ours", "ret"]).copy()
both["diff_pp"] = (both.ours - both.ret) * 100
bad = both[both.diff_pp.abs() > TOL].copy()
bad["source"] = [SRC.get((l, d), "") for l, d in zip(bad.label, bad.date)]
bad.to_csv(f"{out}/flagged.csv", index=False)
both["y"] = both.date.dt.year
summ = both.assign(bad=both.diff_pp.abs() > TOL, big=both.diff_pp.abs() > 2).groupby("y").agg(days=("bad", "size"), over_tol=("bad", "sum"), over_2pp=("big", "sum"))
summ.to_csv(f"{out}/by_year.csv")
# drift: each label-year's summed daily difference. A bad close shows as two offsetting days and
# cancels; a missing, extra or wrong dividend (or distribution) does not - this is the test for the
# ones under the daily tolerance (a 0.3% quarterly dividend). Listed over DRIFT pp (default 0.1).
DRIFT = float(os.environ.get("DRIFT", "0.1"))
dr = both.groupby(["label", "y"]).agg(days=("diff_pp", "size"), drift_pp=("diff_pp", "sum"),
                                      worst_day=("diff_pp", lambda s: s.loc[s.abs().idxmax()]),
                                      worst_date=("date", lambda s: s.loc[both.loc[s.index, "diff_pp"].abs().idxmax()].date()))
dr = dr[dr.drift_pp.abs() > DRIFT].sort_values("drift_pp", key=abs, ascending=False).reset_index()
dr.to_csv(f"{out}/drift.csv", index=False)
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 500)
print(f"\n{len(both):,} member-days compared; within {TOL}pp: {100 * (1 - len(bad) / len(both)):.4f}%; over 2pp: {(both.diff_pp.abs() > 2).sum()}; "
      f"member-days with no CRSP return: {x.ret.isna().sum()}, with no return of ours: {x.ours.isna().sum()}")
print(summ.to_string())
print("\nby source of our row:\n" + bad.groupby(bad.source.astype(str)).size().to_string())
print("\nlabels with most flagged days:\n" + bad.groupby("label").size().sort_values(ascending=False).head(60).to_string())
print(f"\nlabel-years whose summed difference exceeds {DRIFT}pp (a dividend or distribution off): {len(dr)}\n" + dr.head(80).to_string())
print("\nlargest:\n" + bad.reindex(bad.diff_pp.abs().sort_values(ascending=False).index).head(100).to_string())
