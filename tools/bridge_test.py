"""Test a change to bridge_membership.py before the nightly build runs it.

Rebuilds the membership tables from scratch in WORKDIR exactly as the build
does (upstream fja05680 snapshot file -> members.parquet, then the bridge), but
reads Wikipedia's change log from the published copy instead of fetching it,
then prints the daily member count for the years asked and every interval
that differs from the published membership.

Usage: python tools/bridge_test.py WORKDIR [Y0 Y1]
  WORKDIR is created if needed (use a scratch directory, not the repo).
  Needs network access to github.com / raw.githubusercontent.com only.
From 2016-04-08 (Under Armour's Class C line added) through 2018 the count
should be 505 on every day (S&P 500 with its dual-class lines: GOOG, DISCK,
NWS, FOX, UA); before that it is 504 (four dual-class extras). On days S&P
itself held an extra line (2017-08-07, Brighthouse added the day before
AutoNation left; 2016-07-05, Fortive added at the Danaher spin while Columbia
Pipeline left after that day's close) the S&P DJI release is the authority.
"""
import importlib.util, json, shutil, subprocess, sys
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
W = Path(sys.argv[1]).resolve()
Y0, Y1 = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (2016, 2018)
REL = "https://github.com/Johnbrick123/sp500-data/releases/download/data"
HIST = "https://raw.githubusercontent.com/fja05680/sp500/master/S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
U = W / "data" / "universe"; U.mkdir(parents=True, exist_ok=True)
(W / "data" / "raw").mkdir(parents=True, exist_ok=True)


def get(url, dest):
    subprocess.run(["curl", "-sSL", "--retry", "3", "-o", str(dest), url], check=True)


get(HIST, W / "hist.csv")
get(f"{REL}/wiki_changes.csv", U / "wiki_changes.csv")
get(f"{REL}/membership_intervals.parquet", W / "published_intervals.parquet")
get(f"{REL}/prices.parquet", W / "prices.parquet") if not (W / "prices.parquet").exists() else None

hist = pd.read_csv(W / "hist.csv"); hist["date"] = pd.to_datetime(hist["date"])
rows = [(d, t.strip()) for d, tks in zip(hist["date"], hist["tickers"]) for t in str(tks).split(",") if t.strip()]
pd.DataFrame(rows, columns=["date", "ticker"]).to_parquet(U / "members.parquet", index=False)
pub = pd.read_parquet(W / "published_intervals.parquet")
cur = sorted(pub[pub["end"].isna()].ticker)                    # today's list = the published open intervals
pd.DataFrame({"Symbol": cur}).to_csv(U / "current_constituents.csv", index=False)
(U / "tickers.txt").write_text("\n".join(sorted({t for _, t in rows} | set(cur))))

shutil.copy(REPO / "bridge_membership.py", W / "bridge_membership.py")
spec = importlib.util.spec_from_file_location("bm", W / "bridge_membership.py")
bm = importlib.util.module_from_spec(spec); spec.loader.exec_module(bm)
bm.wiki_changes = lambda: pd.read_csv(bm.U / "wiki_changes.csv", parse_dates=["date"])   # the published copy, no Wikipedia fetch

# The synthetic current-members snapshot must carry fresh, source-qualified metadata.
meta_path = U / "current_constituents.meta.json"
now_utc = pd.Timestamp.now(tz="UTC")
valid_meta = {
    "source": "wikipedia:List_of_S%26P_500_companies#constituents",
    "snapshot_date": now_utc.tz_convert("America/Chicago").date().isoformat(),
    "fetched_at_utc": now_utc.isoformat(),
    "symbol_count": len(cur),
}
meta_path.write_text(json.dumps(valid_meta), encoding="utf-8")
assert bm.load_snapshot_metadata(meta_path)[1]["symbol_count"] == len(cur)
stale_meta = dict(valid_meta, fetched_at_utc=(now_utc - pd.Timedelta(days=2)).isoformat())
meta_path.write_text(json.dumps(stale_meta), encoding="utf-8")
try:
    bm.load_snapshot_metadata(meta_path)
    raise AssertionError("stale current-constituent metadata was accepted")
except RuntimeError:
    pass
meta_path.write_text(json.dumps(valid_meta), encoding="utf-8")
print("PASS: fresh snapshot metadata accepted; stale metadata rejected")

# Point-in-time regression: future events must be projected from the actual
# current snapshot, not from the stale historical replay state.
as_of = pd.Timestamp("2026-11-05")
events = pd.DataFrame([
    {"date": pd.Timestamp("2026-11-04"), "added_ticker": "OKTA", "removed_ticker": "KVUE"},
    {"date": pd.Timestamp("2026-11-06"), "added_ticker": "NEW", "removed_ticker": "OLD"},
])
current = {"TT", "OKTA", "OLD", "BASE"}
historical, hist_end = bm.replay_changes(events, {"TT", "KVUE", "OLD"}, "2026-11-03", as_of)
assert hist_end == {"TT", "OKTA", "OLD"}, f"historical replay cutoff failed: {hist_end}"
future, future_end = bm.replay_changes(events, current, as_of)
assert future_end == {"TT", "OKTA", "BASE", "NEW"}, f"future replay did not start from current snapshot: {future_end}"
first_future = set(future[future.date == pd.Timestamp("2026-11-06")].ticker)
assert first_future == {"TT", "OKTA", "BASE", "NEW"}, f"future first snapshot mismatch: {first_future}"
assert "NEW" in first_future and "KVUE" not in future_end
tt_relabel = bm.RELABEL["IR"][0]
assert tt_relabel[0] == "TT" and tt_relabel[2] == "2020-03-03", f"TT cutoff mismatch: {tt_relabel}"
effective_fixture = pd.DataFrame([
    {"date": pd.Timestamp("2020-03-02"), "added_ticker": "IR", "removed_ticker": "XEC"}
])
effective_fixture = bm.apply_effective_date_overrides(effective_fixture)
assert effective_fixture.iloc[0].date == pd.Timestamp("2020-03-03"), "IR/XEC index effective-date override failed"
labels = pd.DataFrame({"date": [pd.Timestamp("2020-03-02"), pd.Timestamp("2020-03-03")],
                       "ticker": ["IR", "IR"]})
for label, start, cutoff in bm.RELABEL["IR"]:
    mask = (labels.ticker == "IR") & (labels.date >= pd.Timestamp(start)) & (labels.date < pd.Timestamp(cutoff))
    labels.loc[mask, "ticker"] = label
assert list(labels.ticker) == ["TT", "IR"], f"TT/IR relabel boundary failed: {list(labels.ticker)}"
print("PASS: as-of replay cutoff, future projection from current snapshot, 2020-03-03 IR/XEC effective date, and TT relabel boundary")

bm.main()

new = pd.read_parquet(U / "membership_intervals.parquet")
for x in (new, pub):
    x["start"] = pd.to_datetime(x["start"]); x["end"] = pd.to_datetime(x["end"])
spy = pd.read_parquet(W / "prices.parquet", columns=["ticker", "date"], filters=[("ticker", "==", "SPY")])
cal = sorted(pd.to_datetime(spy.date))
for y in range(Y0, Y1 + 1):
    c = pd.Series({d: int(((new.start <= d) & (new["end"].isna() | (new["end"] > d))).sum()) for d in cal if d.year == y})
    odd = c[c != c.mode().iloc[0]]
    print(f"{y}: {dict(c.value_counts().sort_index())}" + (f"  days off the usual count: {', '.join(f'{d.date()}={v}' for d, v in odd.items())}" if 0 < len(odd) <= 40 else (f"  {len(odd)} days off the usual count" if len(odd) else "")))
o = set(map(tuple, pub[["ticker", "start", "end"]].astype(str).values)); n = set(map(tuple, new[["ticker", "start", "end"]].astype(str).values))
print("intervals only in the published membership:", sorted(o - n))
print("intervals only in this test:", sorted(n - o))
