"""
Step 2g: Recover missing index members from a Kaggle price archive.

Kaggle's "Daily Historical Stock Prices (1970-2018)" (ehallmar) is a 2018
snapshot of ~5,700 US stocks with one series per ticker. Like WIKI and Tiingo
it stitches a dead company and the next owner of its symbol into one series
(Sears then Sprint under S; Sunoco then Sunoco LP under SUN), so each candidate
is cut at the label month with the same gap / dead-tail test as
salvage_split.py and then passes the usual identity checks.

Before anything is accepted the archive itself is validated: 40 names we
already hold from Yahoo are compared day by day over 2000-2018 and the run
aborts unless >= 99% of daily returns agree within 0.5pp.

Prices: the archive's `close` is checked for split adjustment (AAPL 7:1 on
2014-06-09). Dividends are recovered from the adj_close/close ratio, splits
(if the close turns out to be raw) from the same ratio, and the stored close
is split-adjusted like Yahoo's so compute_adjusted.py treats it uniformly.

Expects the dataset unzipped in ./kag/. Writes data/kaggle_report.txt.
"""
import glob, json, os, re, sys
from pathlib import Path
import numpy as np
import pandas as pd
import fetch_perma as fp
import salvage_split as ss

ROOT, RAW, SUFFIX = fp.ROOT, fp.RAW, fp.SUFFIX
say = fp.say
REPORT = ROOT / "data" / "kaggle_report.txt"
PROG = ROOT / "data" / "kaggle_progress.json"


def load_archive():
    f = next((p for p in glob.glob("kag/**/*.csv", recursive=True) if "historical_stock_prices" in p), None)
    if not f:
        sys.exit("historical_stock_prices.csv not found under kag/")
    df = pd.read_csv(f, usecols=["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"])
    df["ticker"] = df["ticker"].astype(str).str.upper().str.replace(".", "-", regex=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values(["ticker", "date"])
    say(f"archive: {len(df):,} rows, {df.ticker.nunique():,} tickers, {df.date.min().date()}..{df.date.max().date()}")
    return df


def validate(arch, ours):
    """Daily returns of 40 Yahoo-held names vs the archive, 2000-2018."""
    held = ours[(ours.source == "yahoo") & (ours.date >= "2000-01-01")]
    common = sorted(set(held.ticker) & set(arch.ticker))
    rng = np.random.default_rng(7)
    sample = list(rng.choice(common, size=min(40, len(common)), replace=False))
    agree, n_names = [], 0
    for t in sample:
        a = arch[arch.ticker == t].set_index("date").close
        o = held[held.ticker == t].set_index("date").close
        j = pd.concat([a.pct_change().rename("a"), o.pct_change().rename("o")], axis=1).dropna()
        j = j[(j.index >= "2000-01-01")]
        if len(j) < 500:
            continue
        n_names += 1
        agree.append(((j.a - j.o).abs() < 0.005).mean() * 100)
    pct = float(np.median(agree)) if agree else 0.0
    say(f"validation: {n_names} held names compared 2000-2018; median {pct:.3f}% of daily returns agree within 0.5pp")
    if n_names < 30:
        sys.exit("validation: too few comparable names - aborting")
    if pct < 99.0:
        sys.exit("validation: archive disagrees with Yahoo - aborting")


def to_raw_frame(g, label):
    """Archive rows for one ticker -> our raw schema (split-adjusted close, dividends, splits)."""
    g = g.sort_values("date").reset_index(drop=True)
    r = (g.adj_close / g.close).replace([np.inf, -np.inf], np.nan)
    j = (r.shift(1) / r)                      # F_{t-1}/F_t : 1-D/C_{t-1} for a dividend, 1/s for a split
    close = g.close.astype(float).copy()
    div = pd.Series(0.0, index=g.index); spl = pd.Series(1.0, index=g.index)
    for i in np.where((j < 0.9995) | (j > 1.0005))[0]:
        if 0.8 < j[i] < 0.9995:
            div[i] = round(float(close[i - 1] * (1 - j[i])), 4)
        elif j[i] <= 0.8 or j[i] >= 1.2:
            s = 1 / j[i]
            spl[i] = round(float(s), 4)
            close.iloc[:i] = close.iloc[:i] / s     # make the stored close split-adjusted, Yahoo-style
            for c in ("open", "high", "low"):
                g.loc[: i - 1, c] = g.loc[: i - 1, c] / s
    return pd.DataFrame({"date": g.date, "open": g.open, "high": g.high, "low": g.low, "close": close,
                         "volume": g.volume, "dividends": div, "stock_splits": spl,
                         "ticker": label, "source": "kaggle"})


def main():
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    span = iv.groupby("ticker").agg(m_start=("start", "min"), m_end=("end", "max"))
    have = {p.stem for p in RAW.glob("*.parquet")}
    ours = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "source"])
    ours["date"] = pd.to_datetime(ours["date"])
    arch = load_archive()
    # is the archive's close split-adjusted? AAPL 7:1 on 2014-06-09
    a = arch[(arch.ticker == "AAPL") & (arch.date.between("2014-06-05", "2014-06-10"))].set_index("date").close
    if len(a) >= 2:
        say(f"AAPL around the 2014 split: {a.iloc[0]:.2f} -> {a.iloc[-1]:.2f} ({'split-adjusted' if a.iloc[0] / a.iloc[-1] < 2 else 'RAW'} close)")
    validate(arch, ours)
    prog = json.loads(PROG.read_text()) if PROG.exists() else {"done": {}, "recovered": []}
    for lab in [x.strip().upper() for x in os.environ.get("KAGGLE_REDO", "").split(",") if x.strip()]:
        prog["done"].pop(lab, None)
        if lab in prog["recovered"]: prog["recovered"].remove(lab)
        if (RAW / f"{lab}.parquet").exists() and pd.read_parquet(RAW / f"{lab}.parquet", columns=["source"])["source"].iloc[0] != "kaggle":
            (RAW / f"{lab}.parquet").unlink()
        have.discard(lab); say(f"redo requested for {lab}")
    n = 0
    missing = span[~span.index.isin(have)]
    for lab, r in missing.iterrows():
        if lab in prog["done"]:
            continue
        bare = SUFFIX.sub("", lab)
        key = next((k for k in (bare, bare + "Q") if k in arch.ticker.values), None)
        if key is None:
            continue
        g = arch[arch.ticker == key]
        stop = r.m_end if pd.notna(r.m_end) else pd.Timestamp.today()
        if (min(g.date.max(), stop) - max(g.date.min(), r.m_start)).days < 120:
            continue
        df = to_raw_frame(g, lab)
        if SUFFIX.search(lab):
            kept, why = ss.split_at_label(df, lab)
            if kept is None:
                prog["done"][lab] = "kaggle: " + why; say(f"  reject {lab} (as {key}): {why}"); continue
        else:
            kept, why = df, "bare label, whole series"
        ok, why2 = fp.accept(lab, kept, r.m_start, r.m_end, bare)
        if not ok:
            prog["done"][lab] = "kaggle: " + why2; say(f"  reject {lab} (as {key}): {why} BUT {why2}"); continue
        kept.to_parquet(RAW / f"{lab}.parquet", index=False)
        prog["done"][lab] = "recovered"; prog["recovered"].append(lab); n += 1
        say(f"  RECOVERED {lab} (as {key}): {len(kept):,} rows {kept.date.min().date()}..{kept.date.max().date()} "
            f"close {kept.close.iloc[-1]:.2f}, {int((kept.dividends > 0).sum())} dividends, {int((kept.stock_splits != 1).sum())} splits - {why}")
    PROG.write_text(json.dumps(prog, indent=1))
    say(f"\nthis run: {n} recovered. cumulative: {len(prog['recovered'])}")
    REPORT.write_text("\n".join(fp.lines))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        REPORT.write_text("\n".join(fp.lines)); raise
    except Exception as e:
        say(f"CRASHED: {type(e).__name__}: {e}")
        REPORT.write_text("\n".join(fp.lines)); raise
