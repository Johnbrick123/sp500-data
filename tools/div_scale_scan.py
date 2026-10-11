"""For every Yahoo raw file: is each dividend on the same basis as the closes?
A declared dividend is a round amount (a multiple of $0.0001). Yahoo divides
both closes and dividends by every later split factor - including the factor it
uses for a spin-off. So dividend x (product of later factors) should be round.
If instead the stored dividend itself is round and the factor is an odd one,
Yahoo left that dividend unscaled."""
import sys, glob, os
import numpy as np, pandas as pd
R = sys.argv[1]
SIMPLE = {2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 1.5, 1.25, 4/3, 5/4, 3/2, 0.5, 0.25, 0.2, 0.125, 0.1, 1/3, 2/3, 1.1, 1.05, 1.02, 1.03, 1.04, 1.06, 1.08}
def is_simple(f): return any(abs(f - s) < 1e-6 for s in SIMPLE)
def rnd(x, tol): return abs(x * 10000 - round(x * 10000)) < tol
rows = []
for f in sorted(glob.glob(os.path.join(R, "*.parquet"))):
    df = pd.read_parquet(f)
    if "source" not in df or df.source.astype(str).mode().iloc[0] != "yahoo": continue
    df = df.sort_values("date").reset_index(drop=True)
    sp = df.stock_splits.fillna(0).replace(0, 1.0).astype(float)
    ev = [(i, float(sp[i])) for i in np.flatnonzero((sp != 1).to_numpy()) if not is_simple(float(sp[i]))]
    if not ev: continue
    later = sp[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    dv = df.dividends.fillna(0).astype(float)
    for i, F in ev:
        # dividends in the 3 years before this event
        d0 = df.date.iloc[i]
        w = df[(df.date < d0) & (df.date >= d0 - pd.Timedelta(days=3*365)) & (dv > 0)]
        if w.empty: continue
        sc = later.loc[w.index]; D = dv.loc[w.index]
        cons = sum(rnd(d * s, 0.02 * s) for d, s in zip(D, sc))
        # unscaled for THIS factor: D x (later factors excluding F) is round
        unsc = sum(rnd(d * s / F, 0.02 * s / F) and not rnd(d * s, 0.02 * s) for d, s in zip(D, sc))
        rows.append((os.path.basename(f)[:-8], str(d0.date())[:10], round(F, 4), len(w), cons, unsc))
out = pd.DataFrame(rows, columns=["ticker", "event", "factor", "divs_3y_before", "consistent", "unscaled_for_this_factor"])
pd.set_option("display.width", 200); pd.set_option("display.max_rows", 500)
print(out[out.unscaled_for_this_factor > 0].to_string(index=False))
print(f"\n{len(out)} odd-factor events with dividends before them; {int((out.unscaled_for_this_factor > out.divs_3y_before/2).sum())} look unscaled")
