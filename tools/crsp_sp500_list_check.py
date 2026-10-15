"""Check our membership and CRSP permnos against CRSP's own S&P 500 list.

WRDS publishes, as public teaching material, every S&P 500 addition and removal from
1959 to 2022 with the CRSP permno, the company's names and tickers and its index dates:
  https://wrds-www.wharton.upenn.edu/classroom/sp500-introduction/over-time/
(one table per year; a company in the index since 1957 and still in it at the end of 2022 has
no row, so CRSP's daily count there runs ~455, not 500). The table is CRSP's licensed data:
it is read for checking and never copied into the repo or a release asset.

Two checks, 1996-01-02 .. 2022-12-30:
  1. each permno in crsp_permnos.csv must be in CRSP's list with an index spell that overlaps
     the label's membership (a permno found by its last trading day can be another company:
     THY, DIGI, UAWGQ and the Allegheny Ludlum slot were, 2026-10-15);
  2. every add and drop in our membership must match one of CRSP's within 7 days, by the
     label's permno or by ticker. CRSP's end date is the last day in the index, ours the first
     day out, so a drop shows as -1 (or -3 over a weekend). Unmatched rows on either side are
     printed: ticker renames and CRSP's one-day spin-off rows explain most of them.
Usage: curl -sSL -o page.html <url>
       python -I tools/crsp_sp500_list_check.py page.html crsp_permnos.csv membership_intervals.parquet OUT_DIR
Added 2026-10-15."""
import io, os, sys
import pandas as pd

page, pfile, mfile, out = sys.argv[1:5]
os.makedirs(out, exist_ok=True)
t = pd.concat(pd.read_html(io.StringIO(open(page, encoding="utf-8", errors="replace").read())), ignore_index=True)
c = t.drop_duplicates(["PERMNO", "SP500 Start", "SP500 End"]).copy()
c["s"] = pd.to_datetime(c["SP500 Start"]); c["e"] = pd.to_datetime(c["SP500 End"])
print(f"CRSP list: {len(t)} rows, {c.PERMNO.nunique()} permnos, {c.s.min().date()}..{c.e.max().date()}")
m = pd.read_parquet(mfile)
m["start"] = pd.to_datetime(m.start); m["end"] = pd.to_datetime(m.end).fillna(pd.Timestamp("2100-01-01"))
pm = pd.read_csv(pfile)
pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 90); pd.set_option("display.max_rows", 500)
toks = lambda x: set(str(x).replace(" ", "").split(","))

# 1. permnos
g = m.groupby("ticker").agg(ms=("start", "min"), me=("end", "max"))
rows = []
for r in pm.itertuples():
    base = r.label.split("_crsp")[0]
    x = c[c.PERMNO == r.permno]
    ms, me = (g.loc[base].ms, g.loc[base].me) if base in g.index else (pd.Timestamp("1900-01-01"), pd.Timestamp("2100-01-01"))
    ov = x[(x.s <= me + pd.Timedelta(days=5)) & (x.e >= ms - pd.Timedelta(days=5))]
    st = "not in CRSP's list" if len(x) == 0 else ("ok" if len(ov) else "no overlapping spell")
    if st == "not in CRSP's list" and ms <= pd.Timestamp("1996-01-02") and me >= pd.Timestamp("2022-12-30"):
        st = "ok (no row: a member 1957-2022?)"
    rows.append(dict(label=r.label, permno=r.permno, status=st,
                     crsp=" | ".join(f"{a[:45]} [{b}] {s.date()}..{e.date()}" for a, b, s, e in zip(x.Company, x.Ticker, x.s, x.e))))
P = pd.DataFrame(rows); P.to_csv(f"{out}/permno_check.csv", index=False)
print("\n== permnos:", P.status.value_counts().to_dict())
print(P[~P.status.str.startswith("ok")].to_string())

# 2. events
A, B = pd.Timestamp("1996-01-02"), pd.Timestamp("2022-12-30")
ce = pd.concat([c[(c.s > A) & (c.s <= B)].assign(ev="add", dt=lambda x: x.s),
                c[(c.e >= A) & (c.e < B)].assign(ev="drop", dt=lambda x: x.e)]).reset_index(drop=True)
oe = pd.concat([m[(m.start > A) & (m.start <= B)].assign(ev="add", dt=lambda x: x.start),
                m[(m.end > A) & (m.end <= B)].assign(ev="drop", dt=lambda x: x.end)]).reset_index(drop=True)
pmap = dict(zip(pm.label.str.split("_crsp").str[0], pm.permno))
used, res = set(), []
for r in oe.itertuples():
    cand = ce[(ce.ev == r.ev) & ((ce.dt - r.dt).abs().dt.days <= 7) & (~ce.index.isin(used))]
    h = cand[cand.PERMNO == pmap.get(r.ticker, -1)]
    if not len(h):
        tk = r.ticker.split("-")[0]
        h = cand[cand.Ticker.apply(lambda x: tk in toks(x))]
    hit = h.index[0] if len(h) else None
    if hit is not None:
        used.add(hit)
    res.append(dict(label=r.ticker, ev=r.ev, date=r.dt.date(),
                    crsp=None if hit is None else f"{ce.at[hit, 'PERMNO']} [{ce.at[hit, 'Ticker']}] {ce.at[hit, 'Company'][:45]} {ce.at[hit, 'dt'].date()}",
                    lag_days=None if hit is None else (ce.at[hit, "dt"] - r.dt).days))
E = pd.DataFrame(res); E.to_csv(f"{out}/event_check.csv", index=False)
U = ce[~ce.index.isin(used)]
print(f"\n== events: ours {len(E)}, matched {E.crsp.notna().sum()}; lag days {E.lag_days.value_counts().sort_index().to_dict()}")
print("ours, unmatched:\n" + E[E.crsp.isna()].sort_values("date").to_string())
print("CRSP's, unmatched:\n" + U.sort_values("dt")[["ev", "dt", "PERMNO", "Ticker", "Company"]].to_string())
U[["ev", "dt", "PERMNO", "Ticker"]].to_csv(f"{out}/crsp_unmatched.csv", index=False)
