"""
Step 2d: Recover missing index members via Tiingo permaTicker.

Tiingo's fundamentals directory lists ~20,000 companies, 12,500 of them dead,
each with a permanent ID. Asking for prices by ticker returns today's owner of
the symbol; asking by permaTicker returns the original company. This matches
EVERY index member we have no prices for against that directory.

Each recovered series is filed under a DATED label (e.g. S-202004 for the
Sprint that delisted April 2020) when the bare ticker now belongs to someone
else, and data/relabels.csv tells bridge_membership.py to map the old
membership rows to the same label. Nothing is ever filed under a bare ticker
that a living company uses.

Identity checks before anything is saved:
  1. the directory entry must be INACTIVE, or the label's own ticker
  2. for a dated label, the series must END within 18 months of that month
  3. it must overlap the index membership by >= 250 trading days
  4. if we hold today's owner of the ticker, the two must not be the same
     company: < 30% of overlapping days with identical returns
Free tier: 50 requests/hour, so PER_RUN per batch with an hour between batches.
Progress persists in data/perma_progress.json. Needs TIINGO_API_KEY.
"""
import json, os, re, sys, time, urllib.request
from pathlib import Path
import pandas as pd

KEY = os.environ.get("TIINGO_API_KEY") or sys.exit("TIINGO_API_KEY not set")
ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
PROG = ROOT / "data" / "perma_progress.json"
RELABELS = ROOT / "data" / "relabels.csv"
PER_RUN = int(os.environ.get("PERMA_PER_RUN", "45"))
BATCHES = int(os.environ.get("PERMA_BATCHES", "4"))
SUFFIX = re.compile(r"-(\d{6})$")
lines = []
def say(s=""): print(s, flush=True); lines.append(s)
def get(url): return json.load(urllib.request.urlopen(url, timeout=90))


def candidates(iv, have, prog):
    """Missing members -> directory entries with the same ticker."""
    meta = get(f"https://api.tiingo.com/tiingo/fundamentals/meta?token={KEY}")
    by_tk = {}
    for m in meta:
        by_tk.setdefault(str(m.get("ticker", "")).upper().replace(".", "-"), []).append(m)
    say(f"directory: {len(meta):,} companies, {sum(1 for m in meta if not m.get('isActive')):,} inactive")
    out = []
    for lab, g in iv.groupby("ticker"):
        if lab in have or lab in prog["done"]:
            continue
        bare = SUFFIX.sub("", lab)
        for m in by_tk.get(bare, []):
            if not m.get("permaTicker"):
                continue
            # an ACTIVE entry is only a candidate when the label itself is bare
            # (i.e. the membership may be the living company and Yahoo failed)
            if m.get("isActive") and SUFFIX.search(lab):
                continue
            out.append((lab, bare, m["permaTicker"], str(m.get("name"))[:40], bool(m.get("isActive")),
                        g.start.min(), g["end"].max()))
    # dated (known-dead) labels first: highest confidence, highest value
    out.sort(key=lambda x: (0 if SUFFIX.search(x[0]) else 1, x[0]))
    return out


def accept(lab, df, m_start, m_end, bare):
    first, last = df.date.min(), df.date.max()
    mo = SUFFIX.search(lab)
    if mo:
        dl = pd.Timestamp(mo.group(1)[:4] + "-" + mo.group(1)[4:] + "-01")
        if abs((last - dl).days) > 550:
            return False, f"ends {last.date()} but label says {dl:%Y-%m}"
    stop = m_end if pd.notna(m_end) else pd.Timestamp.today()
    ov = int(((df.date >= max(first, m_start)) & (df.date <= min(last, stop))).sum())
    if ov < 250:
        return False, f"only {ov} days overlap membership"
    cur = RAW / f"{bare}.parquet"
    if cur.exists() and bare != lab:
        c = pd.read_parquet(cur); c["date"] = pd.to_datetime(c["date"]).dt.tz_localize(None)
        j = df.set_index("date").close.rename("a").to_frame().join(c.set_index("date").close.rename("b"), how="inner").dropna()
        if len(j) >= 60:
            same = ((j.a.pct_change() - j.b.pct_change()).abs() < 0.002).mean()
            if same > 0.30:
                return False, f"{same*100:.0f}% identical to today's {bare}"
    return True, "ok"


def main():
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    have = {p.stem for p in RAW.glob("*.parquet")}
    prog = json.loads(PROG.read_text()) if PROG.exists() else {"done": {}, "recovered": []}
    todo = candidates(iv, have, prog)
    say(f"{len(todo)} directory matches to try for missing members")
    relabels = {}
    if RELABELS.exists():
        for r in pd.read_csv(RELABELS).itertuples():
            relabels[r.bare] = (r.label, str(r.start), str(r.cutoff))
    n = 0
    for b in range(BATCHES):
        batch = [t for t in todo if t[0] not in prog["done"]][:PER_RUN]
        if not batch:
            say("nothing left to try"); break
        for lab, bare, pid, name, active, m_start, m_end in batch:
            try:
                rows = get(f"https://api.tiingo.com/tiingo/daily/{pid}/prices?startDate=1995-01-01&format=json&token={KEY}")
            except Exception as e:
                prog["done"][lab] = f"request failed {type(e).__name__}"; continue
            if not rows or len(rows) < 250:
                prog["done"][lab] = "no data"; continue
            df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            ok, why = accept(lab, df, m_start, m_end, bare)
            if not ok:
                prog["done"][lab] = "rejected: " + why; say(f"  reject {lab} ({name}): {why}"); continue
            out_label = lab
            if not SUFFIX.search(lab) and (RAW / f"{bare}.parquet").exists():
                # the bare ticker is a living company we hold: file the dead one under
                # a dated label and tell the membership bridge to follow
                out_label = f"{bare}-{df.date.max():%Y%m}"
                relabels[bare] = (out_label, max(df.date.min(), m_start).strftime("%Y-%m-%d"),
                                  (df.date.max() + pd.Timedelta(days=30)).strftime("%Y-%m-%d"))
            pd.DataFrame({"date": df["date"], "open": df["open"], "high": df["high"], "low": df["low"],
                          "close": df["close"], "volume": df["volume"],
                          "dividends": df.get("divCash", 0.0), "stock_splits": df.get("splitFactor", 1.0),
                          "ticker": out_label, "source": "tiingo"}).to_parquet(RAW / f"{out_label}.parquet", index=False)
            prog["done"][lab] = "recovered"; prog["recovered"].append(out_label); n += 1
            say(f"  RECOVERED {out_label} ({name}): {len(df):,} rows {df.date.min().date()}..{df.date.max().date()} close {df.close.iloc[-1]:.2f}")
            time.sleep(0.5)
        PROG.write_text(json.dumps(prog, indent=1))
        if relabels:
            pd.DataFrame([(k, v[0], v[1], v[2]) for k, v in relabels.items()], columns=["bare", "label", "start", "cutoff"]).to_csv(RELABELS, index=False)
        if b < BATCHES - 1 and any(t[0] not in prog["done"] for t in todo):
            say(f"batch {b+1}/{BATCHES} done; sleeping 61 min for the hourly cap", flush=True); time.sleep(61 * 60)
    say(f"\nthis run: {n} recovered. cumulative recovered: {len(prog['recovered'])}")
    (ROOT / "data" / "perma_report.txt").write_text("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say(f"CRASHED: {type(e).__name__}: {e}")
        (ROOT / "data" / "perma_report.txt").write_text("\n".join(lines)); raise
