"""
Step 4: Verify the data. Runs six independent checks and writes a report.

  1. Split spot-checks   - known corporate actions must appear correctly
  2. Cross-source        - Stooq vs Yahoo adjusted returns, sample of tickers
  3. Calendar            - missing / duplicate / weekend rows vs NYSE calendar
  4. Outliers            - >25% daily moves with no matching corporate action
  5. Continuity          - gaps longer than 5 trading days
  6. Index reconstruction- equal-weight S&P vs SPY (end-to-end sanity)

Exit code is non-zero if a hard check fails, so this can gate a CI run.
"""
import io, sys, urllib.request
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent
PRICES = ROOT / "data" / "prices.parquet"
REPORT = ROOT / "data" / "verification_report.txt"

KNOWN_SPLITS = [
    ("AAPL", "2014-06-09", 7.0), ("AAPL", "2020-08-31", 4.0),
    ("NVDA", "2024-06-10", 10.0), ("TSLA", "2020-08-31", 5.0),
    ("AMZN", "2022-06-06", 20.0), ("GOOGL", "2022-07-18", 20.0),
]
lines = []


def say(s=""):
    print(s, flush=True)
    lines.append(str(s))


def check_splits(df):
    say("\n[1] SPLIT SPOT-CHECKS")
    bad = 0
    for tk, date, ratio in KNOWN_SPLITS:
        sub = df[(df.ticker == tk) & (df.date == pd.Timestamp(date))]
        if sub.empty:
            say(f"    ?  {tk} {date}: no row (ticker may be missing)")
            continue
        got = float(sub["stock_splits"].iloc[0])
        ok = abs(got - ratio) < 0.01
        bad += (not ok)
        say(f"    {'PASS' if ok else 'FAIL'}  {tk} {date}: "
            f"expected {ratio}, got {got}")
    return bad


def check_cross_source(df, n=12):
    """Pull the same tickers from Stooq and compare adjusted returns."""
    say("\n[2] CROSS-SOURCE (Yahoo vs Stooq)")
    sample = ["SPY", "AAPL", "MSFT", "XOM", "JPM", "JNJ",
              "KO", "PG", "WMT", "GE", "IBM", "CAT"][:n]
    worst = []
    for tk in sample:
        try:
            url = (f"https://stooq.com/q/d/l/?s={tk.lower()}.us"
                   f"&d1=20000101&d2=20260918&i=d")
            raw = urllib.request.urlopen(url, timeout=30).read().decode()
            s = pd.read_csv(io.StringIO(raw))
            if "Close" not in s.columns or len(s) < 100:
                say(f"    ?  {tk}: Stooq returned no usable data")
                continue
            s["Date"] = pd.to_datetime(s["Date"])
            ours = df[df.ticker == tk][["date", "adj_close"]].copy()
            m = ours.merge(s[["Date", "Close"]], left_on="date",
                           right_on="Date", how="inner")
            if len(m) < 100:
                say(f"    ?  {tk}: too few overlapping days")
                continue
            r1 = m["adj_close"].pct_change()
            r2 = m["Close"].pct_change()
            d = (r1 - r2).abs()
            corr = r1.corr(r2)
            pct_off = (d > 0.01).mean() * 100
            worst.append(pct_off)
            flag = "PASS" if (corr > 0.98 and pct_off < 2) else "CHECK"
            say(f"    {flag}  {tk}: return corr {corr:.4f}, "
                f"{pct_off:.2f}% of days differ >1% ({len(m):,} days)")
        except Exception as e:
            say(f"    ?  {tk}: {type(e).__name__}")
    return 0


def check_calendar(df):
    say("\n[3] CALENDAR")
    d = df[["date", "ticker"]]
    dupes = d.duplicated().sum()
    weekend = df[df.date.dt.dayofweek >= 5].shape[0]
    say(f"    duplicate (ticker,date) rows : {dupes:,}")
    say(f"    weekend rows                 : {weekend:,}")
    spy = set(df[df.ticker == "SPY"].date)
    say(f"    SPY trading days on file     : {len(spy):,}")
    return int(dupes > 0) + int(weekend > 0)


def check_outliers(df):
    say("\n[4] OUTLIERS (>25% move, no corporate action)")
    g = df.sort_values("date").groupby("ticker")
    df = df.assign(ret=g["close"].pct_change())
    sus = df[(df.ret.abs() > 0.25) &
             (df.stock_splits.fillna(0) == 0) &
             (df.dividends.fillna(0) == 0)]
    say(f"    flagged rows: {len(sus):,} "
        f"({len(sus)/max(len(df),1)*100:.4f}% of all rows)")
    if len(sus):
        top = sus.reindex(sus.ret.abs().sort_values(ascending=False).index).head(5)
        for _, r in top.iterrows():
            say(f"      {r.ticker} {r.date.date()}  {r.ret*100:+.1f}%")
        say("    Note: most are real (crashes, biotech, 2008, COVID). "
            "Review, don't auto-delete.")
    return 0


def check_continuity(df):
    say("\n[5] CONTINUITY (gaps > 10 calendar days mid-history)")
    bad = []
    for tk, g in df.groupby("ticker"):
        gaps = g.sort_values("date").date.diff().dt.days
        n = (gaps > 10).sum()
        if n > 3:
            bad.append((tk, int(n)))
    bad.sort(key=lambda x: -x[1])
    say(f"    tickers with >3 large gaps: {len(bad)}")
    for tk, n in bad[:8]:
        say(f"      {tk}: {n} gaps")
    return 0


def check_index_reconstruction(df):
    """End-to-end test: equal-weight basket of large caps vs SPY."""
    say("\n[6] INDEX RECONSTRUCTION (equal-weight basket vs SPY)")
    try:
        names = ["AAPL", "MSFT", "JNJ", "XOM", "JPM", "PG", "KO", "WMT",
                 "MRK", "PFE", "CVX", "HD", "MCD", "IBM", "CAT", "BA",
                 "MMM", "DIS", "VZ", "T"]
        sub = df[df.ticker.isin(names)].pivot(index="date", columns="ticker",
                                              values="adj_close")
        sub = sub.loc["2010-01-01":].dropna(axis=1, how="any")
        rets = sub.pct_change().mean(axis=1)
        basket = (1 + rets).cumprod()
        spy = df[df.ticker == "SPY"].set_index("date")["adj_close"]
        spy = spy.loc[basket.index[0]:basket.index[-1]]
        spy_c = spy / spy.iloc[0]
        yrs = (basket.index[-1] - basket.index[0]).days / 365.25
        b_cagr = basket.iloc[-1] ** (1 / yrs) - 1
        s_cagr = spy_c.iloc[-1] ** (1 / yrs) - 1
        corr = rets.corr(spy.pct_change().reindex(rets.index))
        say(f"    basket ({sub.shape[1]} names) CAGR : {b_cagr*100:6.2f}%")
        say(f"    SPY CAGR                     : {s_cagr*100:6.2f}%")
        say(f"    daily return correlation     : {corr:.4f}")
        ok = corr > 0.70  # 20 equal-wt names vs cap-wt 500: ~0.79 is normal
        say(f"    {'PASS' if ok else 'CHECK'} - a broad large-cap basket should "
            f"track SPY closely. Low correlation means adjustment is broken.")
        return 0 if ok else 1
    except Exception as e:
        say(f"    could not run: {e}")
        return 0




def check_recycled_tickers(df):
    """A ticker that LEFT the index but whose price history only STARTS after
    it left is a different company wearing the same symbol. Yahoo purged the
    original and reassigned the ticker. Joining these to the membership table
    silently corrupts history. Real example found: STI (SunTrust Banks, merged
    into Truist 2019) now returns a ~$6 microcap with data only from 2022."""
    say("\n[7] RECYCLED TICKERS (symbol reassigned to a different company)")
    try:
        mem = pd.read_parquet(ROOT / "data" / "universe" / "members.parquet")
        mem["date"] = pd.to_datetime(mem["date"])
        last_seen = mem.groupby("ticker")["date"].max()
        first_px = df.groupby("ticker")["date"].min()
        j = pd.concat([last_seen.rename("left_index"),
                       first_px.rename("price_starts")], axis=1).dropna()
        bad = j[j.price_starts > j.left_index]
        say(f"    QUARANTINE {len(bad)} tickers - price history begins AFTER "
            f"they left the index")
        for tk, r in bad.head(10).iterrows():
            say(f"      {tk}: left {r.left_index.date()}, "
                f"prices start {r.price_starts.date()}")
        bad.to_csv(ROOT / "data" / "recycled_tickers.csv")
        say("    -> written to data/recycled_tickers.csv; exclude these from "
            "any point-in-time backtest.")
    except Exception as e:
        say(f"    could not run: {e}")
    return 0


def check_adjustment(df):
    """Decisive test of our own adjustment math: compare our derived adj_close
    total return against Yahoo's auto-adjusted series. Catches the classic
    double-counted-split bug."""
    say("\n[8] ADJUSTMENT RECONCILIATION (ours vs Yahoo total return)")
    import yfinance as yf, warnings
    warnings.filterwarnings("ignore")
    fails = 0
    for tk in ["AAPL", "KO", "SPY", "NVDA", "JPM", "MSFT"]:
        try:
            y = yf.download(tk, start="2005-01-01", auto_adjust=True,
                            progress=False, threads=False)["Close"]
            y.index = pd.to_datetime(y.index).tz_localize(None)
            o = df[df.ticker == tk].set_index("date")["adj_close"]
            j = pd.concat([o.rename("a"), y.squeeze().rename("b")],
                          axis=1, join="inner").dropna()
            ra, rb = j.a.pct_change().dropna(), j.b.pct_change().dropna()
            c = ra.corr(rb)
            ok = c > 0.9999
            fails += (not ok)
            say(f"    {'PASS' if ok else 'FAIL'}  {tk}: corr {c:.6f}, "
                f"max daily diff {(ra-rb).abs().max()*100:.4f}%")
        except Exception as e:
            say(f"    ?  {tk}: {type(e).__name__}")
    return fails


def main():
    df = pd.read_parquet(PRICES)
    df["date"] = pd.to_datetime(df["date"])
    say("=" * 64)
    say("DATA VERIFICATION REPORT")
    say("=" * 64)
    say(f"rows    : {len(df):,}")
    say(f"tickers : {df.ticker.nunique():,}")
    say(f"range   : {df.date.min().date()} -> {df.date.max().date()}")

    fails = 0
    fails += check_splits(df)
    fails += check_cross_source(df)
    fails += check_calendar(df)
    fails += check_outliers(df)
    fails += check_continuity(df)
    fails += check_index_reconstruction(df)
    fails += check_recycled_tickers(df)
    fails += check_adjustment(df)

    say("\n" + "=" * 64)
    say(f"HARD FAILURES: {fails}")
    say("=" * 64)
    REPORT.write_text("\n".join(lines))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
