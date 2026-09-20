"""
Step 2b: Recover DELISTED companies from Tiingo, within the free tier.

Free tier limits (tiingo.com): 50 requests/hour, 1,000/day, 500 unique symbols
per month. When a cap is hit Tiingo returns an error PAGE with a normal status
code, so a naive script reads it as "no data". This one:
  - makes at most PER_RUN requests per invocation (run hourly by tiingo.yml)
  - stops the moment a throttle response appears, without marking names dead
  - keeps a progress file so each run continues where the last stopped
  - tries the RECOVERABLE names first: Tiingo's delisted archive starts around
    2014-2016 (measured: 0 of 35 pre-2014 delistings returned, 6 of 10 later
    ones did), so post-2014 delistings go first and pre-2014 ones last

Every recovered series must pass accept() - an identity test - before it is
saved. A symbol reused by a different company is rejected, not merged.

Setup: TIINGO_API_KEY in the environment (repo secret). Output lands in
data/raw/ under the security's LABEL (suffix included) so it joins membership.
"""
import json, os, re, sys, time, urllib.request
from pathlib import Path
import pandas as pd

KEY = os.environ.get("TIINGO_API_KEY")
if not KEY:
    sys.exit("Set TIINGO_API_KEY first (free at tiingo.com).")

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
PROGRESS = ROOT / "data" / "tiingo_progress.json"
PER_RUN = 40                                        # per batch: under Tiingo's 50/hour
BATCHES = int(os.environ.get("TIINGO_BATCHES", "1"))  # hourly job sets 4: GitHub's cron fires
SLEEP_SEC = 61 * 60                                 # far less than hourly, so make each run count
SUFFIX = re.compile(r"-(\d{6})$")


def load_progress():
    return json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {"tried": {}, "rejected": {}}


def candidates(progress):
    """Members with no price file, not yet tried. Post-2014 delistings first."""
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    have = {p.stem for p in RAW.glob("*.parquet")}
    out = []
    for t, g in iv.groupby("ticker"):
        if t in have:
            continue
        # A name marked "recovered" whose file is gone (an overlapping run
        # overwrote the raw-data upload) must be retried, not skipped forever.
        if t in progress["tried"] and progress["tried"][t] != "recovered":
            continue
        mo = SUFFIX.search(t)
        year = int(mo.group(1)[:4]) if mo else (g["end"].max().year if pd.notna(g["end"].max()) else 2100)
        out.append((0 if year >= 2014 else 1, -year, t, SUFFIX.sub("", t), g.start.min(), g["end"].max()))
    out.sort()
    return [o[2:] for o in out]


def fetch(sym):
    url = f"https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate=1995-01-01&format=json&token={KEY}"
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
    try:
        return json.loads(body), None
    except json.JSONDecodeError:
        low = body.lower()
        if "limit" in low or "exceed" in low or "hour" in low or "quota" in low:
            return None, "throttled"
        return None, "not-json"


def accept(label, df, m_start, m_end):
    first, last = df.date.min(), df.date.max()
    if pd.notna(m_end) and first > m_end:
        return False, "starts after the security left the index (recycled symbol)"
    if first > m_start + pd.Timedelta(days=400):
        return False, "starts long after the membership period began"
    mo = SUFFIX.search(label)
    if mo:
        delisted = pd.Timestamp(mo.group(1)[:4] + "-" + mo.group(1)[4:] + "-01")
        if abs((last - delisted).days) > 550:
            return False, f"ends {last.date()} but label says delisted {delisted:%Y-%m} (different company)"
    return True, "ok"


def run_batch(prog):
    todo = candidates(prog)
    print(f"{len(todo)} untried names remain; attempting up to {PER_RUN} this batch", flush=True)
    if not todo:
        return 0, True
    saved, throttled = 0, False
    for tk, sym, m_start, m_end in todo[:PER_RUN]:
        try:
            rows, err = fetch(sym)
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):
                print(f"  throttled at {tk} (HTTP {e.code}); pausing"); throttled = True; break
            prog["tried"][tk] = f"http {e.code}"; continue
        except Exception as e:
            prog["tried"][tk] = f"error {type(e).__name__}"; continue
        if err == "throttled":
            print(f"  throttled at {tk}; pausing"); throttled = True; break
        if not rows or len(rows) < 20:
            prog["tried"][tk] = "no data"; continue
        df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        ok, why = accept(tk, df, m_start, m_end)
        if not ok:
            prog["tried"][tk] = "rejected"; prog["rejected"][tk] = why; continue
        pd.DataFrame({"date": df["date"], "open": df["open"], "high": df["high"], "low": df["low"],
                      "close": df["close"], "volume": df["volume"],
                      "dividends": df.get("divCash", 0.0), "stock_splits": df.get("splitFactor", 1.0),
                      "ticker": tk, "source": "tiingo"}).to_parquet(RAW / f"{tk}.parquet", index=False)
        prog["tried"][tk] = "recovered"; saved += 1
        time.sleep(0.3)
    PROGRESS.write_text(json.dumps(prog, indent=1))
    return saved, False


def main():
    prog = load_progress()
    total = 0
    for b in range(BATCHES):
        saved, done = run_batch(prog)
        total += saved
        if done:
            print("nothing left to try"); break
        if b < BATCHES - 1:
            print(f"batch {b + 1}/{BATCHES} done ({saved} recovered); sleeping {SLEEP_SEC // 60} min for the hourly cap", flush=True)
            time.sleep(SLEEP_SEC)
    tally = pd.Series(list(prog["tried"].values())).value_counts().to_dict() if prog["tried"] else {}
    print(f"this run: {total} recovered.  cumulative: {tally}")


if __name__ == "__main__":
    main()
