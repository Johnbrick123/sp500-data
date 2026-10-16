"""Build the S&P 500 membership before 1996 from CRSP's own S&P 500 list.

Inputs
  PAGE      the WRDS classroom page (curl -sSL -o page.html
            https://wrds-www.wharton.upenn.edu/classroom/sp500-introduction/over-time/)
  MEMBERS   pre1996/members_19960102.csv - label,permno of every member on 1996-01-02
            (permnos from the 1996 crsp_fetch check run; ROK's pre-12/1996 permno is 18948)
  DEPARTED  pre1996/departed_labels.csv - permno,label,name for companies that left before 1996
  PRICES    a prices.parquet whose dates give the trading calendar
  START     first day of the window (e.g. 1991-01-02)
  OUT       membership CSV: label,permno,start,end - end is the first day OUT of the index
            (our convention; CRSP's end date is the last day IN), and 1996-01-02 for a member
            that carries on into the existing membership.

How
  * every CRSP spell that touches [START, 1996-01-01] is mapped to a label by its permno;
  * the list has no row for a company in the index from before 1959 to after 2022 (its daily
    count is a steady 452): those are the 1996-01-02 members whose permno has no row at all,
    taken as members for the whole window. A gap in such a member's spell would show as a
    count of 453 (Ingersoll-Rand's 2009-2010 removal does), so the count check below catches it.
  * checks: every spell mapped, no label in two spells at once, 500 members on every trading day.
The table itself is CRSP's licensed data: only the derived spells are written. Added 2026-10-15."""
import io, sys
import pandas as pd

page, mfile, dfile, pfile, start, out = sys.argv[1:7]
A, T = pd.Timestamp(start), pd.Timestamp("1996-01-02")
t = pd.concat(pd.read_html(io.StringIO(open(page, encoding="utf-8", errors="replace").read())), ignore_index=True)
c = t.drop_duplicates(["PERMNO", "SP500 Start", "SP500 End"]).copy()
c["s"] = pd.to_datetime(c["SP500 Start"]); c["e"] = pd.to_datetime(c["SP500 End"])
mem = pd.read_csv(mfile); dep = pd.read_csv(dfile)
lab = dict(zip(mem.permno, mem.label)); lab[18948] = "ROK"
for r in dep.itertuples():
    lab.setdefault(r.permno, r.label)
w = c[(c.s < T) & (c.e >= A)].copy()
w["label"] = w.PERMNO.map(lab)
bad = w[w.label.isna()]
if len(bad):
    print("UNMAPPED spells:\n" + bad[["PERMNO", "Ticker", "Company", "s", "e"]].to_string()); sys.exit(1)
cal = sorted(pd.read_parquet(pfile, columns=["date"]).date.unique())
cal = [pd.Timestamp(d) for d in cal if A <= pd.Timestamp(d) < T]
nxt = {d: cal[i + 1] if i + 1 < len(cal) else T for i, d in enumerate(cal)}
rows = []
for r in w.itertuples():
    s = max(r.s, A)
    s = next(d for d in cal if d >= s) if s < T else s
    if r.e >= T - pd.Timedelta(days=1):
        e = T
    else:
        last = max(d for d in cal if d <= r.e)
        e = nxt[last]
    rows.append((r.label, r.PERMNO, s, e))
hidden = mem[~mem.permno.isin(set(c.PERMNO))]
for r in hidden.itertuples():
    rows.append((r.label, r.permno, cal[0], T))
o = pd.DataFrame(rows, columns=["label", "permno", "start", "end"]).sort_values(["start", "label"])
print(f"{len(w)} CRSP spells + {len(hidden)} members with no row ({', '.join(hidden.label)})")
# overlaps within a label
for l, g in o.groupby("label"):
    g = g.sort_values("start")
    if (g.start.iloc[1:].values < g.end.iloc[:-1].values).any():
        print("OVERLAP", l, g.values.tolist()); sys.exit(1)
cnt = {d: int(((o.start <= d) & (o.end > d)).sum()) for d in cal}
off = {d.date(): n for d, n in cnt.items() if n != 500}
print(f"{len(cal)} trading days {cal[0].date()}..{cal[-1].date()}: 500 members on {sum(n == 500 for n in cnt.values())}; other counts: {off}")
o["start"] = o.start.dt.date; o["end"] = o.end.dt.date
o.to_csv(out, index=False)
print(f"-> {out}: {len(o)} spells, {o.label.nunique()} labels; added in the window: {(pd.to_datetime(o.start) > cal[0]).sum()}, "
      f"left in the window: {(pd.to_datetime(o.end) < T).sum()}")
sys.exit(1 if off else 0)
