"""Every split-like day on the CRSP lines we use, with CRSP's exact value factor.

fetch_crsp.py books a day as a split when k = PRC[t-1] x (1+RET[t]) / PRC[t] is within 3% of a
standard ratio. A true split matches its ratio to a few hundredths of a percent; a spin-off or a
large special dividend can land inside 3% and be booked as the round ratio (General Mills'
Darden spin-off on 1995-05-30, k = 1.21, booked 1.25 - a 3% error in that day's total return).
This lists, for each permno in the given label,permno files, every day with k >= 1.1 or <= 0.9:
its exact k, the nearest standard ratio and the gap, so a booking can be corrected with a
corporate_action_overrides.csv row (split_factor = k).
Usage (CRSP CSV under kag/): python tools/crsp_split_audit.py OUT.csv PAIRS.csv [PAIRS.csv ...]
Added 2026-10-15 (the pre-1996 extension)."""
import glob, sys
import numpy as np, pandas as pd

out = sys.argv[1]
pairs = pd.concat([pd.read_csv(f) for f in sys.argv[2:]]).drop_duplicates()
want = set(pairs.permno)
f = glob.glob("kag/**/*.csv", recursive=True)[0]
parts = []
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in {"permno", "date", "prc", "ret"}, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch = ch[ch.permno.isin(want)]
    if len(ch):
        parts.append(ch)
c = pd.concat(parts)
c["date"] = pd.to_datetime(c["date"].astype(str), errors="coerce")
for k in ("prc", "ret"):
    c[k] = pd.to_numeric(c[k], errors="coerce")
c = c.dropna(subset=["prc"]).sort_values(["permno", "date"])
c["prev"] = c.prc.abs().groupby(c.permno).shift()
c["k"] = c.prev * (1 + c.ret) / c.prc.abs()
x = c[(c.k >= 1.1) | (c.k <= 0.9)].copy()
NICE = np.array([2, 3, 4, 5, 1.5, 1.25, 4 / 3, 10, 0.5, 1 / 3, 0.25, 0.2, 0.1, 2 / 3, 0.8, 2.5, 1.2, 5 / 3, 6, 8])
x["nice"] = x.k.apply(lambda v: float(NICE[np.argmin(np.abs(NICE / v - 1))]))
x["gap_pct"] = (x.nice / x.k - 1) * 100
labs = pairs.groupby("permno").label.apply(lambda s: " ".join(sorted(s)))
x["labels"] = x.permno.map(labs)
x = x[["labels", "permno", "date", "prev", "prc", "ret", "k", "nice", "gap_pct"]]
x.to_csv(out, index=False)
print(f"{len(x)} split-like days on {x.permno.nunique()} permnos; booked by fetch_crsp.py as the round ratio but off by 0.5-3%:")
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 500)
print(x[(x.gap_pct.abs() >= 0.5) & (x.gap_pct.abs() < 3)].to_string())
