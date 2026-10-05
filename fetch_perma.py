"""
Step 2d: Recover dead companies whose ticker was later REUSED, via Tiingo
permaTicker (a permanent per-security ID). Asking by ticker returns today's
owner of the symbol; asking by permaTicker returns the original company.

Each recovered series is filed under a DATED label (e.g. S-202004 for the
Sprint that delisted April 2020), never the bare ticker, so the nightly Yahoo
refresh can never overwrite it with today's owner. bridge_membership.py maps
the old membership rows to the same dated label (RELABEL below).

Identity checks before anything is saved:
  1. the series must END within 18 months of the delisting month in the label
  2. it must overlap the index membership by >= 250 trading days
  3. if today's owner of the ticker has prices (Yahoo), the two series must
     NOT be the same company: < 30% of overlapping days with identical returns
IDs come from perma_ids.csv (label, permaTicker). Needs TIINGO_API_KEY.
"""
import csv, json, os, re, sys, time, urllib.request
from pathlib import Path
import pandas as pd

KEY = os.environ.get("TIINGO_API_KEY") or sys.exit("TIINGO_API_KEY not set")
ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
SUFFIX = re.compile(r"-(\d{6})$")
lines = []
def say(s=""): print(s, flush=True); lines.append(s)

def main():
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    have = {p.stem for p in RAW.glob("*.parquet")}
    ids = list(csv.DictReader(open(ROOT / "perma_ids.csv")))
    say(f"{len(ids)} permaTicker entries")
    for r in ids:
        lab, pid = r["label"].strip(), r["permaTicker"].strip()
        if lab in have:
            say(f"  {lab}: already have"); continue
        bare = SUFFIX.sub("", lab)
        m = iv[iv.ticker.isin([lab, bare])]
        if m.empty:
            say(f"  {lab}: no membership rows under {lab} or {bare}"); continue
        m_start, m_end = m.start.min(), m["end"].max()
        try:
            rows = json.load(urllib.request.urlopen(
                f"https://api.tiingo.com/tiingo/daily/{pid}/prices?startDate=1995-01-01&format=json&token={KEY}", timeout=60))
        except Exception as e:
            say(f"  {lab}: request failed {type(e).__name__}"); continue
        if not rows or len(rows) < 250:
            say(f"  {lab}: {len(rows) if rows else 0} rows"); continue
        df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        first, last = df.date.min(), df.date.max()
        mo = SUFFIX.search(lab)
        if mo:
            dl = pd.Timestamp(mo.group(1)[:4] + "-" + mo.group(1)[4:] + "-01")
            if abs((last - dl).days) > 550:
                say(f"  {lab}: REJECT ends {last.date()} but label says {dl:%Y-%m}"); continue
        stop = m_end if pd.notna(m_end) else pd.Timestamp.today()
        ov = ((df.date >= max(first, m_start)) & (df.date <= min(last, stop))).sum()
        if ov < 250:
            say(f"  {lab}: REJECT only {ov} days overlap membership"); continue
        cur = RAW / f"{bare}.parquet"           # today's owner of the ticker, if we hold it
        if cur.exists():
            c = pd.read_parquet(cur); c["date"] = pd.to_datetime(c["date"]).dt.tz_localize(None)
            j = df.set_index("date").close.rename("a").to_frame().join(c.set_index("date").close.rename("b"), how="inner").dropna()
            if len(j) >= 60:
                same = ((j.a.pct_change() - j.b.pct_change()).abs() < 0.002).mean()
                if same > 0.30:
                    say(f"  {lab}: REJECT {same*100:.0f}% identical to today's {bare}"); continue
        pd.DataFrame({"date": df["date"], "open": df["open"], "high": df["high"], "low": df["low"],
                      "close": df["close"], "volume": df["volume"],
                      "dividends": df.get("divCash", 0.0), "stock_splits": df.get("splitFactor", 1.0),
                      "ticker": lab, "source": "tiingo"}).to_parquet(RAW / f"{lab}.parquet", index=False)
        say(f"  {lab}: RECOVERED {len(df):,} rows {first.date()}..{last.date()} close {df.close.iloc[-1]:.2f}")
        time.sleep(0.5)
    (ROOT / "data" / "perma_report.txt").write_text("\n".join(lines))

if __name__ == "__main__":
    main()
