"""
Step 4: Verify the data. Every check reports one of three outcomes:

  PASS        the check ran and the data met the bar
  FAIL        the check ran and the data did not  -> non-zero exit, no publish
  UNVERIFIED  the check could not run (source unreachable, sample missing)

UNVERIFIED never counts as PASS. The first version of this file let an
unreachable cross-check source and a missing sample ticker fall through as
"0 failures". An external audit caught it. Gates are on RETURN ERROR, not
correlation alone: adding 0.1pp to every daily return keeps correlation at
1.000 while compounding to 2.7x the correct wealth over 1,000 days.
"""
import io, sys, urllib.request, warnings
from datetime import date
from pathlib import Path
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).parent
PRICES = ROOT / "data" / "prices.parquet"
INTERVALS = ROOT / "data" / "universe" / "membership_intervals.parquet"
REPORT = ROOT / "data" / "verification_report.txt"

lines, results = [], []            # results: (check, status, detail)


def say(s=""):
    print(s, flush=True); lines.append(str(s))


def record(check, status, detail=""):
    results.append((check, status, detail))
    say(f"    [{status}] {detail}")


# 1. Known corporate actions (issuer filings). A missing sample is a FAIL.
KNOWN_SPLITS = [("AAPL", "2014-06-09", 7.0), ("AAPL", "2020-08-31", 4.0), ("NVDA", "2024-06-10", 10.0),
                ("TSLA", "2020-08-31", 5.0), ("AMZN", "2022-06-06", 20.0), ("GOOGL", "2022-07-18", 20.0),
                ("WMT", "2024-02-26", 3.0), ("GE", "2021-08-02", 0.125)]
KNOWN_DIVS = [("MSFT", "2004-11-15", 3.08), ("COST", "2023-12-27", 15.0), ("NVDA", "2024-06-11", 0.01)]


def check_corporate_actions(df):
    say("\n[1] KNOWN CORPORATE ACTIONS")
    for tk, d, ratio in KNOWN_SPLITS:
        sub = df[(df.ticker == tk) & (df.date == pd.Timestamp(d))]
        if sub.empty:
            record("split " + tk, "FAIL", f"{tk} {d}: no row in dataset"); continue
        got = float(sub.stock_splits.iloc[0])
        record("split " + tk, "PASS" if abs(got - ratio) < 1e-3 else "FAIL", f"{tk} {d} split {got:g} (expected {ratio:g})")
    for tk, d, amt in KNOWN_DIVS:
        sub = df[(df.ticker == tk) & (df.date == pd.Timestamp(d))]
        if sub.empty:
            record("div " + tk, "FAIL", f"{tk} {d}: no row in dataset"); continue
        got = float(sub.dividends.iloc[0])
        record("div " + tk, "PASS" if abs(got - amt) < 0.005 else "FAIL", f"{tk} {d} dividend {got:.4f} (expected {amt})")


# 2. Our adjustment vs Yahoo's own total-return series. Same vendor: tests OUR
#    math, not Yahoo's record. Gate on error size, not correlation.
def check_adjustment(df):
    say("\n[2] ADJUSTMENT RECONCILIATION (ours vs Yahoo auto-adjusted; tests our math only)")
    try:
        import yfinance as yf
    except ImportError:
        record("adjustment", "UNVERIFIED", "yfinance not installed"); return
    for tk in ["AAPL", "KO", "SPY", "NVDA", "JPM", "MSFT", "GE", "WMT"]:
        try:
            y = yf.download(tk, start="2005-01-01", auto_adjust=True, progress=False, threads=False)["Close"]
            y.index = pd.to_datetime(y.index).tz_localize(None)
        except Exception as e:
            record("adjust " + tk, "UNVERIFIED", f"{tk}: Yahoo unreachable ({type(e).__name__})"); continue
        o = df[df.ticker == tk].set_index("date")["adj_close"]
        j = pd.concat([o.rename("a"), y.squeeze().rename("b")], axis=1, join="inner").dropna()
        j = j.iloc[:-1]      # the latest bar is a moving target between two downloads; compare finished days
        if len(j) < 250:
            record("adjust " + tk, "UNVERIFIED", f"{tk}: only {len(j)} overlapping days"); continue
        ra, rb = j.a.pct_change().dropna(), j.b.pct_change().dropna()
        max_err = (ra - rb).abs().max() * 100
        wealth_err = abs((j.a.iloc[-1] / j.a.iloc[0]) / (j.b.iloc[-1] / j.b.iloc[0]) - 1) * 100
        record("adjust " + tk, "PASS" if (max_err < 0.05 and wealth_err < 0.5) else "FAIL",
               f"{tk}: max daily error {max_err:.4f}pp, cumulative wealth error {wealth_err:.3f}%")


# 3. Independent source: Nasdaq's own historical API. This is the only check
#    that tests Yahoo's RECORD rather than our arithmetic - everything else
#    compares us against the vendor we sourced from. Nasdaq closes are
#    unadjusted, so split days are excluded from the comparison.
LEDGER = ROOT / "data" / "cross_source_ledger.csv"
NASDAQ_PER_NIGHT = 25


RECYCLED = set()      # filled by check_recycled; such names are not on Nasdaq under the member's identity


def tiebreak_tiingo(tk, m):
    """Share of days (>=0.5pp tolerance) on which Tiingo's returns match ours, or None."""
    import json, os, urllib.request
    key = os.environ.get("TIINGO_API_KEY", "").strip()
    if not key:
        return None
    try:
        u = f"https://api.tiingo.com/tiingo/daily/{tk}/prices?startDate=2016-09-01&format=json&token={key}"
        rows = json.load(urllib.request.urlopen(u, timeout=60))
        t = pd.DataFrame(rows); t["date"] = pd.to_datetime(t["date"]).dt.tz_localize(None)
        t = t.set_index("date")["adjClose"].pct_change().rename("r_t")
        j = m.set_index("date")[["r_o"]].join(t, how="inner").dropna()
        if len(j) < 250:
            return None
        return float(((j.r_o - j.r_t).abs() < 0.005).mean() * 100)
    except Exception:
        return None


OPEN_MEMBERS = set()   # filled in main(): names whose index membership is current
MEMBERS_EVER = set()


def check_cross_source(df):
    """Every currently-listed name is compared with Nasdaq's own 10-year history,
    25 names a night in a fixed rotation, so the whole universe is independently
    re-verified every ~2 months. Results accumulate in data/cross_source_ledger.csv."""
    say("\n[3] INDEPENDENT SOURCE (Nasdaq historical API vs ours)")
    import json, time, urllib.request
    H = {"User-Agent": "Mozilla/5.0 (data verification)"}
    last = df.groupby("ticker").date.max()
    members_ever = set(MEMBERS_EVER)
    live = sorted(t for t, d in last.items() if d >= df.date.max() - pd.Timedelta(days=10)
                  and "-" not in t and t.isalpha() and t not in RECYCLED
                  and (t in OPEN_MEMBERS or t not in members_ever))   # current members and ETFs; not ex-members' OTC tails
    ledger = pd.read_csv(LEDGER, parse_dates=["checked"]) if LEDGER.exists() else \
        pd.DataFrame(columns=["ticker", "checked", "status", "days", "pct_off", "max_pp"])
    # anchors every night (large, long-lived names) plus a rotating slice of the rest
    anchors = [t for t in ["AAPL", "MSFT", "JPM", "XOM", "SPY"] if t in live]
    rest = [t for t in live if t not in anchors]
    i = (date.today().toordinal() * (NASDAQ_PER_NIGHT - len(anchors))) % max(len(rest), 1)
    sample = anchors + [rest[(i + k) % len(rest)] for k in range(NASDAQ_PER_NIGHT - len(anchors))] if rest else anchors
    # names that failed earlier are re-checked every night until they clear
    sample += [t for t in ledger[ledger.status == "FAIL"].ticker if t in live and t not in sample]
    new = []
    for tk in sample:
        rows = []
        for ac in ("stocks", "etf"):
            try:
                u = (f"https://api.nasdaq.com/api/quote/{tk}/historical?assetclass={ac}"
                     f"&fromdate=2016-09-01&todate={date.today():%Y-%m-%d}&limit=9999")
                d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
                rows = d.get("data", {}).get("tradesTable", {}).get("rows") or []
                if len(rows) >= 250:
                    break
            except Exception as e:
                rows = []; err = type(e).__name__
            time.sleep(0.7)
        need_days = min(250, int(0.8 * int((df.ticker == tk).sum())))
        if len(rows) < max(60, need_days):
            record("nasdaq " + tk, "UNVERIFIED", f"{tk}: Nasdaq returned {len(rows)} rows")
            new.append((tk, date.today(), "UNVERIFIED", len(rows), None, None)); continue
        n = pd.DataFrame(rows)
        n["date"] = pd.to_datetime(n["date"], errors="coerce")
        n["close"] = pd.to_numeric(n["close"].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")
        n = n.dropna(subset=["date", "close"]).sort_values("date")
        o = df[df.ticker == tk][["date", "close", "stock_splits"]]
        m = o.merge(n[["date", "close"]], on="date", suffixes=("_o", "_n")).sort_values("date")
        # Yahoo's close is split-adjusted, Nasdaq's is not: compare daily RETURNS
        # and drop days on or after a split, where the two bases differ.
        m["r_o"] = m.close_o.pct_change(); m["r_n"] = m.close_n.pct_change()
        sp = m.stock_splits.replace(0, 1).fillna(1)      # Yahoo writes 0.0 for "no split"
        m = m[(sp == 1) & (sp.shift(-1).fillna(1) == 1)]
        diff = (m.r_o - m.r_n).abs().dropna()
        if len(diff) < max(60, need_days):
            record("nasdaq " + tk, "UNVERIFIED", f"{tk}: only {len(diff)} comparable days")
            new.append((tk, date.today(), "UNVERIFIED", len(diff), None, None)); continue
        off = (diff > 0.005).mean() * 100
        st = "PASS" if off < 1 else "FAIL"
        note = ""
        if st == "FAIL":
            # Two sources disagree: bring in a third. Tiingo is independent of both
            # Yahoo and Nasdaq; if it agrees with ours on >=99% of days the Nasdaq
            # record is the odd one out (CRH's pre-2023 ADR line on Nasdaq is noisy).
            v = tiebreak_tiingo(tk, m)
            if v is not None:
                if v >= 99.0:
                    st, note = "PASS", f"; Nasdaq disagrees but Tiingo agrees with ours on {v:.2f}% of days"
                else:
                    note = f"; Tiingo agrees with ours on only {v:.2f}% of days"
        record("nasdaq " + tk, st, f"{tk}: {off:.2f}% of {len(diff):,} days differ >0.5pp (max {diff.max()*100:.3f}pp){note}")
        new.append((tk, date.today(), st, len(diff), round(off, 3), round(diff.max() * 100, 3)))
    done = {r[0] for r in new}
    ledger = pd.concat([ledger[~ledger.ticker.isin(done)], pd.DataFrame(new, columns=ledger.columns)], ignore_index=True)
    ledger["checked"] = pd.to_datetime(ledger["checked"])
    ledger = ledger[ledger.ticker.isin(live)]          # drop names that left the listed universe (recycled/delisted)
    ledger.to_csv(LEDGER, index=False)
    recent = ledger[ledger.checked >= pd.Timestamp(date.today()) - pd.Timedelta(days=120)]
    nf = int((recent.status == "FAIL").sum())
    record("nasdaq ledger", "PASS" if nf == 0 else "FAIL",
           f"ledger: {int((recent.status == 'PASS').sum())} of {len(live)} listed names verified against Nasdaq "
           f"in the last 120 days, {nf} failed, {int((recent.status == 'UNVERIFIED').sum())} unverified")


# 4. Structure.
def check_structure(df):
    say("\n[4] STRUCTURE")
    d = df.duplicated(["ticker", "date"]).sum()
    record("duplicate keys", "PASS" if d == 0 else "FAIL", f"{d:,} duplicate (ticker, date) rows")
    wk = (df.date.dt.dayofweek >= 5).sum()
    record("weekend rows", "PASS" if wk == 0 else "FAIL", f"{wk:,} weekend rows")
    np_ = ((df.close <= 0) | (df.adj_close <= 0)).sum()
    record("non-positive prices", "PASS" if np_ == 0 else "FAIL", f"{np_:,} non-positive close/adj_close")
    ohlc = ((df.high < df.close - 1e-6) | (df.low > df.close + 1e-6) | (df.high < df.open - 1e-6) | (df.low > df.open + 1e-6)).sum()
    record("ohlc sanity", "PASS" if ohlc <= 5 else "FAIL", f"{ohlc:,} bars with open/close outside high-low (known: HUBB, UA 2021-05-05)")
    latest = df.date.max(); stale = []
    for tk, g in df[["ticker", "date", "open", "high", "low", "close"]].groupby("ticker", sort=False):
        if len(g) < 3:
            continue
        o, h, l, c = (g[x].to_numpy() for x in ("open", "high", "low", "close"))
        k = len(g)
        while k > 1 and o[k-1] == h[k-1] == l[k-1] == c[k-1] == c[k-2]:
            k -= 1
        # trailing flat bars after a last REAL bar that is over 30 days old =
        # filler on a dead series (also when the filler runs up to today)
        if k < len(g) and g.date.iloc[k - 1] < latest - pd.Timedelta(days=30):
            stale.append(tk)
    record("stale tails", "PASS" if not stale else "FAIL", f"{len(stale)} dead series end in flat filler bars (should be trimmed by compute_adjusted.py) {stale[:10]}")
    ivx = pd.read_parquet(INTERVALS, columns=["ticker", "start", "end"])
    mem = set(ivx.ticker)
    z = df[df.ticker.isin(mem)].sort_values(["ticker", "date"])
    pc = z.groupby("ticker").close.shift()
    ev = z.dividends.fillna(0).ne(0) | z.stock_splits.fillna(1).replace(0, 1).ne(1)    # dividend/split rows are events, kept on purpose
    flag = ((z.volume.fillna(0) == 0) & (z.open == z.high) & (z.high == z.low) & (z.low == z.close) & (z.close == pc)
            & ~ev & z.groupby("ticker").date.shift(-1).notna())
    f = z.loc[flag, ["ticker", "date"]]
    # a no-trade bar in the last 7 days before S&P removed the name (halt, or a
    # deal that closed first) is the index's carry row: kept on purpose
    e = f.merge(ivx.dropna(subset=["end"]).assign(end=lambda x: pd.to_datetime(x["end"])), on="ticker")
    carry = e[(e.date < e["end"]) & (e.date >= e["end"] - pd.Timedelta(days=7))][["ticker", "date"]].drop_duplicates()
    ph = len(f) - len(carry)
    record("placeholder bars", "PASS" if ph == 0 else "FAIL", f"{ph} zero-volume flat bars inside index stocks' series (dropped by compute_adjusted.py; rows carrying a dividend or split, and {len(carry)} end-of-membership carry rows, are kept)")
    spy = df[df.ticker == "SPY"].date
    record("calendar", "PASS" if len(spy) > 7000 else "FAIL", f"SPY has {len(spy):,} trading days on file")


# 5. Freshness: a weekday run must end within the last 4 days.
def check_freshness(df):
    say("\n[5] FRESHNESS")
    last = df.date.max().date(); age = (date.today() - last).days
    record("freshness", "PASS" if age <= (4 if date.today().weekday() < 5 else 6) else "FAIL", f"latest date {last}, {age} days old")
    per = df.groupby("ticker").date.max()
    stale = per[per < pd.Timestamp(last) - pd.Timedelta(days=30)]
    record("stale tickers", "PASS", f"{len(stale)} tickers end >30 days before the latest date (acquired/delisted names expected): "
           f"{', '.join(stale.index[:8])}{'...' if len(stale) > 8 else ''}")


# 6. Membership events: effective dates from S&P DJI announcements.
MEMBERSHIP_TESTS = [
    ("AIV", "2020-12-18", True), ("AIV", "2020-12-21", False),
    ("LNC", "2023-09-15", True), ("LNC", "2023-09-18", False), ("NWL", "2023-09-18", False),
    ("AAL", "2024-09-20", True), ("AAL", "2024-09-23", False), ("ETSY", "2024-09-23", False), ("BIO", "2024-09-23", False),
    ("EA", "2026-08-04", True), ("EA", "2026-08-05", False), ("FERG", "2026-08-05", True), ("FERG", "2026-08-04", False),
    ("AVB", "2026-08-17", True), ("AVB", "2026-08-18", False), ("RDDT", "2026-08-18", True), ("RDDT", "2026-08-17", False),
    ("BRK-B", "2026-09-18", True), ("FISV", "2026-09-18", True), ("META", "2026-09-18", True),
    ("EA", "2010-06-30", True), ("AVB", "2015-12-31", True), ("FERG", "2020-01-01", False), ("RDDT", "2020-01-01", False),
]


def check_membership(iv):
    say("\n[6] MEMBERSHIP EVENTS (effective dates from S&P announcements)")
    def member(t, d):
        d = pd.Timestamp(d); g = iv[iv.ticker == t]
        return bool(((g.start <= d) & (g["end"].isna() | (g["end"] > d))).any())
    bad = [(t, d, e) for t, d, e in MEMBERSHIP_TESTS if member(t, d) != e]
    record("membership events", "PASS" if not bad else "FAIL",
           f"{len(MEMBERSHIP_TESTS) - len(bad)}/{len(MEMBERSHIP_TESTS)} pass" + (f"; wrong: {bad}" if bad else ""))
    for d in ["2000-01-03", "2008-09-15", "2015-12-31", "2026-09-18"]:
        n = int(((iv.start <= pd.Timestamp(d)) & (iv["end"].isna() | (iv["end"] > pd.Timestamp(d)))).sum())
        record("member count " + d, "PASS" if 495 <= n <= 510 else "FAIL", f"{n} members on {d}")


# 7. Recycled tickers -> quarantine list consumed by build_db / query_remote / backtest.
def check_recycled(df, iv):
    say("\n[7] RECYCLED TICKERS")
    last_end = iv.groupby("ticker")["end"].max()          # NaT if still a member
    first_px = df.groupby("ticker").date.min()
    j = pd.concat([last_end.rename("left"), first_px.rename("px_start")], axis=1).dropna()
    bad = j[j.px_start > j.left]
    bad.to_csv(ROOT / "data" / "recycled_tickers.csv")
    RECYCLED.update(bad.index)
    record("recycled", "PASS", f"{len(bad)} quarantined (prices start after the security left the index): "
           f"{', '.join(bad.index[:10])}{'...' if len(bad) > 10 else ''}")


# 8. Coverage by date: the survivorship gap, measured. The most important number here.
def check_coverage(df, iv):
    say("\n[8] PRICE COVERAGE OF INDEX MEMBERS")
    rec = set(pd.read_csv(ROOT / "data" / "recycled_tickers.csv").ticker)
    have = df.groupby("ticker").date.agg(["min", "max"])
    worst = 100
    # the final probe uses the latest date carried by most tickers, not the
    # newest date in the file: a single ticker with one extra bar would
    # otherwise make coverage read ~0% and block the publish.
    per_day = df.groupby("date").ticker.nunique()
    broad = per_day[per_day >= 0.5 * per_day.max()]
    latest = str((broad.index.max() if len(broad) else df.date.max()).date())
    for d in ["1996-01-02", "2000-01-03", "2005-01-03", "2008-09-15", "2010-01-04", "2015-01-05", "2019-01-11", "2024-09-23", latest]:
        ts = pd.Timestamp(d)
        mem = set(iv[(iv.start <= ts) & (iv["end"].isna() | (iv["end"] > ts))].ticker) - rec
        got = sum(1 for t in mem if t in have.index and have.loc[t, "min"] <= ts <= have.loc[t, "max"] + pd.Timedelta(days=7))
        pct = got / max(len(mem), 1) * 100; worst = min(worst, pct)
        say(f"    {d}  members {len(mem):3d}  with prices {got:3d}  coverage {pct:5.1f}%")
    record("coverage", "PASS" if worst >= 30 else "FAIL", f"worst coverage {worst:.1f}% - pre-2005 results are not survivorship-safe")


# 9. End-to-end: rebuild the equal-weight S&P 500 from our own membership and
#    adjusted prices, and compare it to RSP, the real equal-weight S&P 500 ETF.
#    This is the only check that tests the whole pipeline at once - universe,
#    point-in-time membership and total-return adjustment together - against an
#    instrument priced by the market.
def check_index_reconstruction(df, iv):
    say("\n[9] INDEX RECONSTRUCTION (our equal-weight S&P 500 vs the RSP ETF, 2015+)")
    try:
        rec = set(pd.read_csv(ROOT / "data" / "recycled_tickers.csv").ticker)
        px = df.pivot(index="date", columns="ticker", values="adj_close").loc["2015-01-01":]
        if "RSP" not in px.columns:
            record("index reconstruction", "UNVERIFIED", "RSP not in dataset"); return
        me = px.resample("ME").last(); rets = me.pct_change()
        rows = []
        for i in range(1, len(me)):
            prev, cur = me.index[i - 1], me.index[i]
            mem = set(iv[(iv.start <= prev) & (iv["end"].isna() | (iv["end"] > prev))].ticker) - rec
            hold = [t for t in mem if t in me.columns and pd.notna(me.at[prev, t])]
            rows.append((cur, rets.loc[cur, hold].dropna().mean()))
        b = pd.Series(dict(rows)).dropna()
        rsp = me["RSP"].pct_change().reindex(b.index).dropna()
        b = b.reindex(rsp.index)
        corr = b.corr(rsp)
        cagr_b = (1 + b).prod() ** (12 / len(b)) - 1
        cagr_r = (1 + rsp).prod() ** (12 / len(rsp)) - 1
        gap = (cagr_b - cagr_r) * 100
        ok = corr > 0.99 and abs(gap) < 2.0
        record("index reconstruction", "PASS" if ok else "FAIL",
               f"corr {corr:.4f}, our CAGR {cagr_b*100:.2f}% vs RSP {cagr_r*100:.2f}% "
               f"(gap {gap:+.2f}pp over {len(b)} months)")
    except Exception as e:
        record("index reconstruction", "UNVERIFIED", f"could not run: {type(e).__name__}: {e}")


def main():
    df = pd.read_parquet(PRICES); df["date"] = pd.to_datetime(df["date"])
    iv = pd.read_parquet(INTERVALS); iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    say("=" * 70); say(f"DATA VERIFICATION REPORT  {date.today()}"); say("=" * 70)
    say(f"rows {len(df):,}   tickers {df.ticker.nunique():,}   {df.date.min().date()} -> {df.date.max().date()}")
    check_corporate_actions(df); check_adjustment(df)
    OPEN_MEMBERS.update(iv[iv["end"].isna()].ticker); MEMBERS_EVER.update(iv.ticker)
    check_recycled(df, iv)                       # first: its quarantine list keeps recycled symbols out of the Nasdaq sample
    check_cross_source(df)
    check_structure(df); check_freshness(df); check_membership(iv); check_coverage(df, iv); check_index_reconstruction(df, iv)
    n = {s: sum(1 for _, st, _ in results if st == s) for s in ["PASS", "FAIL", "UNVERIFIED"]}
    say("\n" + "=" * 70)
    say(f"PASS {n['PASS']}   FAIL {n['FAIL']}   UNVERIFIED {n['UNVERIFIED']}")
    if n["UNVERIFIED"]:
        say("UNVERIFIED checks did not run. They are not passes.")
    say("=" * 70)
    REPORT.write_text("\n".join(lines))
    sys.exit(1 if n["FAIL"] else 0)


if __name__ == "__main__":
    main()
