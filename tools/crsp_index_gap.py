"""Find a member missing from the membership file, from the S&P 500's own daily moves.
If the index held a company our file lacks, with weight w, the published index return
differs from the one rebuilt from our members by about w x (its return - the index's):
the residual series e_t (published - rebuilt, in bp; crsp_index_check.py's base residual)
regresses on that company's excess return with slope ~ w. Every CRSP permno with rows on
>= 90% of the days is scored; the slope is compared with the weight its own market cap
would give (TOTAL_MCAP, $M, the index's total on the first day).
Read-only. Env: RESID="YYYY-MM-DD=bp ..." SPX="YYYY-MM-DD=ret ..." TOTAL_MCAP=8000000 TOP=30
(CSV unpacked in kag/) -> data/crsp_index_gap.txt. Added 2026-10-15 (the 1998 pass). Weak: a missing
member worth 0.2% of the index moves the residual by well under 1 bp a day against 3-4 bp of noise."""
import glob, os
import numpy as np, pandas as pd

E = {pd.Timestamp(a): float(b) for a, b in (x.split("=") for x in os.environ["RESID"].split())}
I = {pd.Timestamp(a): float(b) for a, b in (x.split("=") for x in os.environ["SPX"].split())}
TOTAL = float(os.environ.get("TOTAL_MCAP", "8000000"))
TOP = int(os.environ.get("TOP", "30"))
days = sorted(set(E) & set(I))
e = np.array([E[d] for d in days]) / 1e4
spx = np.array([I[d] for d in days])
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
say(f"{len(days)} days {days[0].date()}..{days[-1].date()}; residual sd {e.std() * 1e4:.2f} bp")
f = glob.glob("kag/**/*.csv", recursive=True)[0]
WANT = {"permno", "date", "shrcd", "prc", "ret", "shrout"}
keep = []
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in WANT, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    keep.append(ch[ch.date.isin(days)])
c = pd.concat(keep)
c["ret"] = pd.to_numeric(c.ret, errors="coerce")
c["mcap"] = c.prc.abs() * c.shrout / 1e3            # $M
R = c.pivot_table(index="date", columns="permno", values="ret").reindex(days)
M = c.groupby("permno").mcap.median()
ok = R.notna().mean() >= 0.9
R = R.loc[:, ok]
X = R.sub(spx, axis=0)
rows = []
for p in X.columns:
    x = X[p].values; m = ~np.isnan(x)
    if m.sum() < 20: continue
    xx = x[m]; ee = e[m]
    b = (xx @ ee) / (xx @ xx)
    res = ee - b * xx
    se = np.sqrt((res @ res) / (m.sum() - 1) / (xx @ xx))
    gain = 1 - (res @ res) / (ee @ ee)
    rows.append((p, b, b / se, gain, M.get(p, np.nan) / TOTAL))
r = pd.DataFrame(rows, columns=["permno", "slope", "t", "r2", "cap_w"]).sort_values("t", ascending=False)
first = c.groupby("permno").date.min(); last = c.groupby("permno").date.max()
say(f"{len(r)} permnos scored. Top {TOP} by t (slope = implied index weight; cap_w = its own cap / total):")
for x in r.head(TOP).itertuples():
    say(f"  permno {x.permno:6d}  slope {x.slope * 100:7.3f}%  cap_w {x.cap_w * 100:7.3f}%  t {x.t:6.2f}  r2 {x.r2:6.3f}  median cap ${M.get(x.permno, np.nan):,.0f}M")
os.makedirs("data", exist_ok=True)
open("data/crsp_index_gap.txt", "w").write("\n".join(out))
