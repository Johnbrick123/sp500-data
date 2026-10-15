"""Identity audit of every CRSP permno the dataset uses (crsp_permnos.csv: label, permno).

Most permnos were found by a member's last trading day and size (probe_crsp.py) - which is how
the 1999 pass took Premark International (71001) for Nalco: both were taken over in November
1999 near $53. The Kaggle dump has no tickers or names, so identity is tested indirectly:
  * each permno's whole-life first and last day, exchange, share code, last price and
    market cap (its line in CRSP, not just the 1995+ window we recovered);
  * its 'rivals': other permnos whose last day falls within 3 days of it, at a price within
    25% and a market cap within a factor of 4 - the candidates a last-day probe could not
    tell apart.
Read-only. Usage: python crsp_identity_audit.py crsp_permnos.csv   (CSV unpacked in kag/)
-> data/crsp_identity.csv. Added 2026-10-15 (after the 1996 pass)."""
import glob, os, sys
import pandas as pd

pairs = pd.read_csv(sys.argv[1])
f = glob.glob("kag/**/*.csv", recursive=True)[0]
WANT = {"permno", "date", "shrcd", "exchcd", "prc", "shrout"}
first, lastrow = {}, {}
n = 0
for ch in pd.read_csv(f, chunksize=5_000_000, usecols=lambda c: c.lower() in WANT, low_memory=False):
    ch.columns = [c.lower() for c in ch.columns]
    ch["date"] = pd.to_datetime(ch["date"].astype(str), errors="coerce")
    ch["prc"] = pd.to_numeric(ch["prc"], errors="coerce")
    n += len(ch)
    g = ch.groupby("permno").date.min()
    for p, d in g.items():
        if p not in first or d < first[p]:
            first[p] = d
    w = ch.dropna(subset=["prc"]).sort_values("date").groupby("permno").tail(1)
    for r in w.itertuples():
        if r.permno not in lastrow or r.date > lastrow[r.permno].date:
            lastrow[r.permno] = r
print(f"{n:,} rows; {len(first):,} permnos", flush=True)
L = pd.DataFrame([(p, r.date, abs(r.prc), r.shrout, r.exchcd, r.shrcd) for p, r in lastrow.items()],
                 columns=["permno", "last", "last_prc", "shrout", "exch", "shrcd"])
L["first"] = L.permno.map(first)
L["mcap_m"] = L.last_prc * L.shrout / 1000.0
L = L.set_index("permno")
out, txt = [], []
for r in pairs.itertuples():
    p = int(r.permno)
    if p not in L.index:
        out.append(dict(label=r.label, permno=p, note="permno not in the dump")); continue
    x = L.loc[p]
    near = L[(abs((L["last"] - x["last"]).dt.days) <= 3) & (L.index != p)]
    near = near[(near.mcap_m >= x.mcap_m / 4) & (near.mcap_m <= x.mcap_m * 4)
                & (abs(near.last_prc / max(x.last_prc, 1e-9) - 1) <= 0.25)]
    near = near.assign(dist=abs(near.last_prc / max(x.last_prc, 1e-9) - 1) + abs((near.mcap_m / max(x.mcap_m, 1e-9)) - 1) / 4).sort_values("dist")
    out.append(dict(label=r.label, permno=p, first=x["first"].date(), last=x["last"].date(), exch=x.exch, shrcd=x.shrcd,
                    last_prc=round(x.last_prc, 4), mcap_m=round(x.mcap_m, 1), rivals=len(near),
                    rival_list=" ".join(f"{q}:{v['last'].date()}:{v.last_prc:g}:{v.mcap_m:.0f}" for q, v in near.head(5).iterrows())))
os.makedirs("data", exist_ok=True)
pd.DataFrame(out).to_csv("data/crsp_identity.csv", index=False)
print(pd.DataFrame(out).to_string(), flush=True)
