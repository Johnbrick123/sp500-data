"""Merger-arbitrage identity test for dead members whose series came from CRSP.

A takeover target trades, in its last weeks, either pinned near the cash price (tiny daily
moves) or in lock-step with its acquirer at the exchange ratio. Both are signatures of the
company, not of whatever else ended trading the same week, so a recovered line that shows
neither, or tracks the wrong company, deserves a second look (the 1999 'Nalco', 2026-10-15).
For each label (default: every label whose rows are CRSP-sourced), over its last N trading days:
its daily return volatility, and the three series in the dataset whose daily returns correlate
best with it (with the price ratio's coefficient of variation over the window).
Usage: deal_tracking.py DATA_DIR OUT.csv [N=20] [LABEL ...]   (DATA_DIR holds prices.parquet)
Added 2026-10-15."""
import sys
import numpy as np, pandas as pd

D, out = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 20
only = sys.argv[4:]
p = pd.read_parquet(f"{D}/prices.parquet", columns=["ticker", "date", "close", "source"])
p["date"] = pd.to_datetime(p.date)
src = p.groupby("ticker").source.agg(lambda s: s.astype(str).str.startswith("crsp").mean())
labels = only or sorted(src[src > 0.5].index)
W = p.pivot_table(index="date", columns="ticker", values="close").sort_index()
R = W.pct_change(fill_method=None)
rows = []
for lab in labels:
    s = W[lab].dropna()
    if len(s) < N + 2:
        continue
    end = s.index[-1]
    win = s.index[-(N + 1):]
    r = R.loc[win[1:], lab]
    vol = float(r.std())
    X = R.loc[win[1:]].drop(columns=[lab])
    X = X.loc[:, X.notna().sum() >= N - 2]
    c = X.corrwith(r).dropna().sort_values(ascending=False).head(3)
    tops = []
    for t, v in c.items():
        ratio = (W.loc[win, lab] / W.loc[win, t]).dropna()
        tops.append(f"{t}:{v:.2f}:cv{ratio.std() / ratio.mean():.3f}:x{ratio.iloc[-1]:.4f}")
    rows.append(dict(label=lab, last=end.date(), last_close=round(float(s.iloc[-1]), 4), vol_pct=round(vol * 100, 2),
                     top=" ".join(tops)))
o = pd.DataFrame(rows)
o.to_csv(out, index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 140); pd.set_option("display.max_rows", 1000)
print(o.to_string())
