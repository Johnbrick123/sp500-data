"""Our membership changes in a year (from intervals) vs Wikipedia's change log.
Usage: member_changes.py BUILD_DIR YEAR [WIKI_CHANGES_CSV]
BUILD_DIR holds prices.parquet and membership_intervals.parquet (release assets);
the change log defaults to BUILD_DIR/wiki_changes.csv (release asset too).
Wikipedia's log is a lead, not the authority: S&P DJI's own releases decide."""
import sys
import pandas as pd
D, Y = sys.argv[1], int(sys.argv[2])
W = sys.argv[3] if len(sys.argv) > 3 else f"{D}/wiki_changes.csv"
iv = pd.read_parquet(f"{D}/membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
p = pd.read_parquet(f"{D}/prices.parquet", columns=["ticker", "date"], filters=[("ticker", "==", "SPY")])
cal = sorted(pd.to_datetime(p.date))
days = [d for d in cal if d.year == Y]
prev_day = [d for d in cal if d < days[0]][-1]
def members(d):
    return set(iv[(iv.start <= d) & (iv["end"].isna() | (iv["end"] > d))].ticker)
ours = []
last = members(prev_day)
for d in days:
    m = members(d)
    for t in sorted(m - last): ours.append((d, "add", t))
    for t in sorted(last - m): ours.append((d, "rem", t))
    last = m
print(f"members on first/last day of {Y}: {len(members(days[0]))} / {len(members(days[-1]))}")
print(f"OUR CHANGES ({len(ours)}):")
for d, a, t in ours:
    print(f"  {d.date()} {a} {t}")
w = pd.read_csv(W, parse_dates=["date"])
w = w[w.date.dt.year == Y]
print(f"\nWIKIPEDIA ROWS ({len(w)}) vs ours:")
ourset = {(d, a, t) for d, a, t in ours}
def base(t): return t.split("-")[0]
for _, r in w.iterrows():
    for a, col in (("add", "added_ticker"), ("rem", "removed_ticker")):
        t = r[col]
        if pd.isna(t): continue
        hit = [x for x in ours if x[1] == a and base(x[2]) == t]
        same = [x for x in hit if x[0] == r.date]
        status = "OK" if same else ("DATE DIFF " + ",".join(str(x[0].date()) + " " + x[2] for x in hit) if hit else "NOT IN OURS")
        print(f"  {r.date.date()} {a} {t:6s} {status}")
wk = {(r.date, a, r[c]) for _, r in w.iterrows() for a, c in (("add", "added_ticker"), ("rem", "removed_ticker")) if pd.notna(r[c])}
print("\nOURS NOT IN WIKIPEDIA:")
for d, a, t in ours:
    if not any(x[1] == a and x[2] == base(t) and abs((x[0] - d).days) <= 5 for x in wk):
        print(f"  {d.date()} {a} {t}")
