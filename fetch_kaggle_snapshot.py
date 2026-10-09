"""
Step 2h: Recover a delisted member from an OLD VERSION of a daily-updated
Kaggle S&P 500 dataset - a time machine. andrewmvd/sp-500-stocks has been
re-published every day since Dec 2021; version 71 (2022-02-28) still carries
IHS Markit (INFO) through its last trading day.

Usage (env): KAGGLE_API_TOKEN, KAGGLE_SLUG=andrewmvd/sp-500-stocks,
KAGGLE_VERSION=71, KAGGLE_PAIRS="INFO-202203:INFO,OTHER-202203:OTHER"
(our label : symbol inside the snapshot). The snapshot is validated against
40 names we hold before anything is accepted; each recovered series goes
through the usual identity checks (fetch_perma.accept).
"""
import io, json, os, sys, zipfile, urllib.request
import numpy as np
import pandas as pd
import fetch_perma as fp
import fetch_kaggle as fk

ROOT, RAW, SUFFIX, say = fp.ROOT, fp.RAW, fp.SUFFIX, fp.say
REPORT = ROOT / "data" / "kaggle_snapshot_report.txt"
TOK = os.environ["KAGGLE_API_TOKEN"].strip()
SLUG = os.environ.get("KAGGLE_SLUG", "andrewmvd/sp-500-stocks")
VER = int(os.environ["KAGGLE_VERSION"])
PAIRS = [p.split(":") for p in os.environ["KAGGLE_PAIRS"].split(",") if ":" in p]


def main():
    u = f"https://www.kaggle.com/api/v1/datasets/download/{SLUG}?datasetVersionNumber={VER}"
    b = urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": f"Bearer {TOK}"}), timeout=300).read()
    z = zipfile.ZipFile(io.BytesIO(b))
    f = next(n for n in z.namelist() if "stock" in n.lower() and n.endswith(".csv"))
    arch = pd.read_csv(z.open(f))
    arch.columns = [c.strip().lower().replace(" ", "_") for c in arch.columns]
    arch = arch.rename(columns={"symbol": "ticker"})
    arch["ticker"] = arch["ticker"].astype(str).str.upper().str.replace(".", "-", regex=False)
    arch["date"] = pd.to_datetime(arch["date"], errors="coerce")
    arch = arch.dropna(subset=["date", "close"]).sort_values(["ticker", "date"])
    say(f"{SLUG} version {VER}: {len(arch):,} rows, {arch.ticker.nunique()} tickers, {arch.date.min().date()}..{arch.date.max().date()}")
    ours = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "source"])
    ours["date"] = pd.to_datetime(ours["date"])
    fk.validate(arch, ours)                    # aborts unless >=99% of daily returns agree with Yahoo-held names
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    span = iv.groupby("ticker").agg(m_start=("start", "min"), m_end=("end", "max"))
    n = 0
    for lab, sym in PAIRS:
        lab, sym = lab.strip().upper(), sym.strip().upper()
        g = arch[arch.ticker == sym]
        if g.empty or lab not in span.index:
            say(f"  {lab}: symbol {sym} not in snapshot or label not in membership"); continue
        df = fk.to_raw_frame(g, lab)
        bare = SUFFIX.sub("", lab)
        ok, why = fp.accept(lab, df, span.loc[lab, "m_start"], span.loc[lab, "m_end"], bare)
        if not ok:
            say(f"  reject {lab} (as {sym}): {why}"); continue
        df.to_parquet(RAW / f"{lab}.parquet", index=False); n += 1
        say(f"  RECOVERED {lab} (as {sym} from version {VER}): {len(df):,} rows {df.date.min().date()}..{df.date.max().date()} "
            f"close {df.close.iloc[-1]:.2f}, {int((df.dividends > 0).sum())} dividends, {int((df.stock_splits != 1).sum())} splits")
    say(f"\nthis run: {n} recovered")
    REPORT.write_text("\n".join(fp.lines))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        REPORT.write_text("\n".join(fp.lines)); raise
    except Exception as e:
        say(f"CRASHED: {type(e).__name__}: {e}"); REPORT.write_text("\n".join(fp.lines)); raise
