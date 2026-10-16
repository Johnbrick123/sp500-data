"""For a few label-years, the best-matching CRSP permno among ALL permnos (not just the largest),
with the daily-return correlation on the label's member-days, and how many days differ by >0.5pp.
Usage (CRSP CSV under kag/): python tools/label_permno_probe.py prices.parquet membership.parquet LABEL:YEAR ...
Read-only diagnostic. Added 2026-10-16 (the accuracy pass)."""
import glob, sys
import numpy as np, pandas as pd

pf, mf = sys.argv[1:3]
want = [(a.split(":")[0], int(a.split(":")[1])) for a in sys.argv[3:]]
years = {y for _, y in want}
f = glob.glob("kag/**/*.csv", recursive=True)[0]
parts = []
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in {"permno", "date", "ret"}, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    ch = ch[ch.date.dt.year.isin(years)]
    ch["ret"] = pd.to_numeric(ch.ret, errors="coerce").astype("float32")
    parts.append(ch)
c = pd.concat(parts)
p = pd.read_parquet(pf, columns=["ticker", "date", "adj_close", "source"]); p["date"] = pd.to_datetime(p.date)
p = p[p.ticker.isin({l for l, _ in want})].sort_values(["ticker", "date"]); p["ours"] = p.groupby("ticker").adj_close.pct_change()
m = pd.read_parquet(mf); m["start"] = pd.to_datetime(m.start); m["end"] = pd.to_datetime(m.end).fillna(pd.Timestamp("2100-01-01"))
pd.set_option("display.width", 200)
for y in sorted(years):
    R = c[c.date.dt.year == y].pivot_table(index="date", columns="permno", values="ret")
    for l, yy in want:
        if yy != y:
            continue
        sp = m[m.ticker == l]
        o = p[p.ticker == l].set_index("date").ours.reindex(R.index)
        inm = np.zeros(len(R.index), bool)
        for r in sp.itertuples():
            inm |= (R.index >= r.start) & (R.index < r.end)
        o = o[inm].dropna()
        Rs = R.loc[o.index]
        Rs = Rs.loc[:, Rs.notna().sum() >= 0.8 * len(o)]
        cc = Rs.corrwith(o).sort_values(ascending=False)
        top = cc.head(3)
        out = []
        for q, v in top.items():
            d = (o - Rs[q]).abs() * 100
            out.append(f"{q}: corr {v:.4f}, days >0.5pp {int((d > 0.5).sum())} of {int(d.notna().sum())}, worst {d.max():.1f}pp on {d.idxmax().date() if d.notna().any() else '-'}")
        src = p[(p.ticker == l) & (p.date.dt.year == y)].source.astype(str).value_counts().to_dict()
        print(f"{l} {y} ({len(o)} member-days, sources {src}):\n   " + "\n   ".join(out), flush=True)
