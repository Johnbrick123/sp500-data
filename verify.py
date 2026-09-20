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
        if len(j) < 250:
            record("adjust " + tk, "UNVERIFIED", f"{tk}: only {len(j)} overlapping days"); continue
        ra, rb = j.a.pct_change().dropna(), j.b.pct_change().dropna()
        max_err = (ra - rb).abs().max() * 100
        wealth_err = abs((j.a.iloc[-1] / j.a.iloc[0]) / (j.b.iloc[-1] / j.b.iloc[0]) - 1) * 100
        record("adjust " + tk, "PASS" if (max_err < 0.05 and wealth_err < 0.5) else "FAIL",
               f"{tk}: max daily error {max_err:.4f}pp, cumulative wealth error {wealth_err:.3f}%")


# 3. Independent source. Unreachable -> UNVERIFIED, loudly.
def check_cross_source(df):
    say("\n[3] INDEPENDENT SOURCE (Stooq vs ours)")
    for tk in ["SPY", "AAPL", "XOM", "JPM", "KO", "PG"]:
        try:
            url = f"https://stooq.com/q/d/l/?s={tk.lower()}.us&d1=20050101&d2={date.today():%Y%m%d}&i=d"
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read().decode()
            s = pd.read_csv(io.StringIO(raw))
            if "Close" not in s.columns or len(s) < 250:
                record("stooq " + tk, "UNVERIFIED", f"{tk}: Stooq returned no usable data"); continue
        except Exception as e:
            record("stooq " + tk, "UNVERIFIED", f"{tk}: Stooq unreachable ({type(e).__name__})"); continue
        s["Date"] = pd.to_datetime(s["Date"])
        m = df[df.ticker == tk][["date", "adj_close"]].merge(s[["Date", "Close"]], left_on="date", right_on="Date")
        pct_off = ((m.adj_close.pct_change() - m.Close.pct_change()).abs() > 0.01).mean() * 100
        record("stooq " + tk, "PASS" if pct_off < 2 else "FAIL", f"{tk}: {pct_off:.2f}% of days differ >1pp ({len(m):,} days)")


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
    record("recycled", "PASS", f"{len(bad)} quarantined (prices start after the security left the index): "
           f"{', '.join(bad.index[:10])}{'...' if len(bad) > 10 else ''}")


# 8. Coverage by date: the survivorship gap, measured. The most important number here.
def check_coverage(df, iv):
    say("\n[8] PRICE COVERAGE OF INDEX MEMBERS")
    rec = set(pd.read_csv(ROOT / "data" / "recycled_tickers.csv").ticker)
    have = df.groupby("ticker").date.agg(["min", "max"])
    worst = 100
    for d in ["1996-01-02", "2000-01-03", "2005-01-03", "2008-09-15", "2010-01-04", "2015-01-05", "2019-01-11", "2024-09-23", str(df.date.max().date())]:
        ts = pd.Timestamp(d)
        mem = set(iv[(iv.start <= ts) & (iv["end"].isna() | (iv["end"] > ts))].ticker) - rec
        got = sum(1 for t in mem if t in have.index and have.loc[t, "min"] <= ts <= have.loc[t, "max"])
        pct = got / max(len(mem), 1) * 100; worst = min(worst, pct)
        say(f"    {d}  members {len(mem):3d}  with prices {got:3d}  coverage {pct:5.1f}%")
    record("coverage", "PASS" if worst >= 30 else "FAIL", f"worst coverage {worst:.1f}% - pre-2005 results are not survivorship-safe")


def main():
    df = pd.read_parquet(PRICES); df["date"] = pd.to_datetime(df["date"])
    iv = pd.read_parquet(INTERVALS); iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    say("=" * 70); say(f"DATA VERIFICATION REPORT  {date.today()}"); say("=" * 70)
    say(f"rows {len(df):,}   tickers {df.ticker.nunique():,}   {df.date.min().date()} -> {df.date.max().date()}")
    check_corporate_actions(df); check_adjustment(df); check_cross_source(df)
    check_structure(df); check_freshness(df); check_membership(iv); check_recycled(df, iv); check_coverage(df, iv)
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
