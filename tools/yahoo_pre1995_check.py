"""A second source for 1970-1994: Yahoo's long histories against our CRSP-built returns.

For every label that was an index member before 1995 and is still listed under the same symbol
(a bare label, no -YYYYMM suffix), Yahoo's daily history from 1970 is downloaded (auto-adjusted:
splits and dividends) and its daily total returns are compared with ours (adj_close) on that
label's member-days before 1995-01-03. Yahoo's old data come from a different vendor lineage than
CRSP, so agreement there is independent evidence; a day where the two differ is listed.
Usage: python tools/yahoo_pre1995_check.py prices.parquet membership_intervals.parquet OUTDIR [TOL=1.0]
Read-only. Added 2026-10-16 (the accuracy pass)."""
import os, sys, time
import pandas as pd, numpy as np
import yfinance as yf

pf, mf, out = sys.argv[1:4]
TOL = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
os.makedirs(out, exist_ok=True)
m = pd.read_parquet(mf)
m["start"] = pd.to_datetime(m.start); m["end"] = pd.to_datetime(m.end).fillna(pd.Timestamp("2100-01-01"))
E = pd.Timestamp("1995-01-03")
pre = m[(m.start < E) & ~m.ticker.str.contains(r"-\d{6}$") & ~m.ticker.str.contains("_")]
labs = sorted(set(pre.ticker))
print(len(labs), "bare labels with pre-1995 membership", flush=True)
p = pd.read_parquet(pf, columns=["ticker", "date", "adj_close"])
p = p[p.ticker.isin(labs) & (p.date < E)].copy(); p["date"] = pd.to_datetime(p.date)
p = p.sort_values(["ticker", "date"]); p["ours"] = p.groupby("ticker").adj_close.pct_change()
# WANT=file.csv (label,date): also write Yahoo's daily total return on those label-days (to fill a
# day the CRSP file has no price for from a second source rather than carrying the close flat)
WANT = pd.read_csv(os.environ["WANT"], parse_dates=["date"]) if os.environ.get("WANT") else pd.DataFrame(columns=["label", "date"])
wanted = []
rows, summ = [], []
for i in range(0, len(labs), 25):
    batch = labs[i:i + 25]
    for attempt in range(3):
        try:
            df = yf.download(batch, start="1969-12-01", end="1995-01-04", auto_adjust=True, actions=False,
                             progress=False, group_by="ticker", threads=True, timeout=30); break
        except Exception as e:
            time.sleep(10); df = None
    if df is None:
        continue
    for t in batch:
        try:
            y = df[t]["Close"].dropna() if isinstance(df.columns, pd.MultiIndex) else df["Close"].dropna()
        except KeyError:
            summ.append((t, 0, 0, np.nan, "no Yahoo data")); continue
        y.index = pd.to_datetime(y.index).tz_localize(None) if getattr(y.index, "tz", None) else pd.to_datetime(y.index)
        yr = y.pct_change()
        for d in WANT[WANT.label == t].date:
            nx = y.index[y.index > d]
            n1 = nx[0] if len(nx) else None
            wanted.append((t, d.date(), yr.get(d, np.nan), y.get(d, np.nan), y.get(y.index[y.index < d].max(), np.nan) if (y.index < d).any() else np.nan,
                           n1.date() if n1 is not None else None, yr.get(n1, np.nan) if n1 is not None else np.nan))
        sp = pre[pre.ticker == t]
        o = p[p.ticker == t].set_index("date").ours
        mem = o.index[[bool(((sp.start <= d) & (sp.end > d)).any()) for d in o.index]]
        j = pd.DataFrame({"ours": o.reindex(mem), "yahoo": yr.reindex(mem)}).dropna()
        if j.empty:
            summ.append((t, 0, 0, np.nan, f"Yahoo from {y.index.min().date() if len(y) else '-'}")); continue
        j["diff_pp"] = (j.ours - j.yahoo) * 100
        bad = j[j.diff_pp.abs() > TOL]
        summ.append((t, len(j), len(bad), float(np.corrcoef(j.ours, j.yahoo)[0, 1]), f"Yahoo from {y.index.min().date()}"))
        for d, r in bad.iterrows():
            rows.append((t, d.date(), r.ours, r.yahoo, r.diff_pp))
    time.sleep(1)
pd.DataFrame(wanted, columns=["label", "date", "yahoo_ret", "yahoo_close", "yahoo_prev_close", "next_date", "next_ret"]).to_csv(f"{out}/wanted.csv", index=False)
S = pd.DataFrame(summ, columns=["label", "days", "over_tol", "corr", "note"])
F = pd.DataFrame(rows, columns=["label", "date", "ours", "yahoo", "diff_pp"])
S.to_csv(f"{out}/summary.csv", index=False); F.to_csv(f"{out}/flagged.csv", index=False)
pd.set_option("display.width", 200); pd.set_option("display.max_rows", 500)
tot = S.days.sum()
print(f"\n{len(S[S.days > 0])} labels compared, {tot:,} member-days; within {TOL}pp: {100 * (1 - S.over_tol.sum() / max(tot, 1)):.3f}%")
print("lowest correlation / most flagged:\n" + S.sort_values(["over_tol"], ascending=False).head(40).to_string())
print("no comparison:\n" + S[S.days == 0].to_string())
print("largest differences:\n" + F.reindex(F.diff_pp.abs().sort_values(ascending=False).index).head(60).to_string())
