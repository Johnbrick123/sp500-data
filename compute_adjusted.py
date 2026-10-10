"""
Step 3: Compute our own adjusted prices from raw OHLCV + corporate actions.

This replaces Yahoo's unstable 'Adj Close'. Because we derive it from stored
raw prices and a stored actions table, the same inputs always produce the same
outputs - your backtests become reproducible.

Method (standard total-return back-adjustment):
  For each day t, the adjustment factor applied to all prior history is
      f_t = (1 - div_t / close_{t-1}) / split_ratio_t
  Cumulative product of future factors, applied backwards, gives adj_close.
  Large distributions (>5% of the price) use f_t = close_t / (close_t + div_t).
  Spin-off ex-dates listed in spinoffs.csv get the holder's actual return:
  (parent close x parent_shares + spun-off shares x their first regular-way
  close) / prior close.

Outputs:
  data/prices.parquet   one tidy table: date, ticker, o/h/l/c, volume,
                        adj_close, div, split
"""
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
ANOMALIES = []          # (ticker, date, prev_close, dividend, factor_or_close, action)
OVERRIDES = ROOT / "corporate_action_overrides.csv"   # hand-verified fixes to provider records


def apply_overrides(df):
    """Provider records are sometimes wrong in known ways. This table is the
    place to fix them explicitly, with a note, instead of code heuristics.
    Columns: ticker, date, split_factor, dividend, note. Blank = leave as is."""
    if not OVERRIDES.exists():
        return df
    ov = pd.read_csv(OVERRIDES, dtype={"ticker": str})
    ov = ov[ov.ticker == df.ticker.iloc[0]]
    if ov.empty:
        return df
    d = pd.to_datetime(df["date"]).dt.tz_localize(None)
    for _, r in ov.iterrows():
        m = d == pd.Timestamp(r["date"])
        if not m.any():
            continue
        if pd.notna(r.get("split_factor")):
            df.loc[m, "stock_splits"] = float(r["split_factor"])
        if pd.notna(r.get("dividend")):
            df.loc[m, "dividends"] = float(r["dividend"])
    return df


def adjust_one(df):
    df = apply_overrides(df.sort_values("date").reset_index(drop=True))
    close = df["close"].astype(float)
    div = df.get("dividends", pd.Series(0.0, index=df.index)).fillna(0.0)
    split = df.get("stock_splits", pd.Series(0.0, index=df.index)).fillna(0.0)
    split = split.replace(0.0, 1.0)          # retained for audit only

    prev_close = close.shift(1)
    # IMPORTANT: Yahoo's 'Close' is ALREADY split-adjusted retroactively.
    # Dividing by the split ratio again double-counts it (verified against a
    # known AAPL 7:1 - it produced a 7x jump in the adjusted series).
    # So we apply ONLY the dividend adjustment here. The splits column is
    # retained as an audit record and is used by verify.py.
    # Tiingo (delisted recovery) gives a truly UNADJUSTED close, so for those
    # rows the split ratio applies too. Yahoo rows: close is already
    # split-adjusted, so the effective split is 1 (the column is audit-only).
    if "source" in df.columns:
        srcs = df["source"].astype(str)
        base = srcs[~srcs.str.endswith(("-patch", "-splice"))]   # patch / splice rows never set the series' basis
        src = base.mode().iloc[0] if len(base) else srcs.iloc[0]
    else:
        src = "yahoo"
    eff_split = split if src in ("tiingo", "wiki") else pd.Series(1.0, index=df.index)
    # A dividend is paid per share ON the ex-date, i.e. on the post-split basis
    # when a split lands the same day, while prev_close is pre-split. Put both
    # on the same basis: prev_close_new = prev_close / split.
    # Two conventions, used where each is right:
    #   ordinary dividends -> f = 1 - D/P_prev, the same formula Yahoo uses, so
    #     our series stays exactly reconcilable with an independent copy.
    #   large distributions (>5% of the prior close: spin-offs, special
    #     dividends) -> f = P_t/(P_t + D), the total-return convention. The
    #     Yahoo formula divides by (P_prev - D) and overstates the event-day
    #     return as D approaches P_prev: measured at up to 15.7pp (Danaher /
    #     Fortive 2016), and it is what made KSU's factor go negative.
    d_eff = div * eff_split
    # NOTE: div is per POST-split share, as is close, so the large-distribution
    # factor uses div (not d_eff); the split is applied once, below.
    # A real distribution of D makes the price fall by roughly D. When a vendor
    # records a large distribution but the price did NOT fall (the series is
    # already adjusted for it), applying it again invents a huge one-day gain -
    # Danaher/Fortive 2016 came out at +39% instead of ~+3%. Those records are
    # internally inconsistent: ignore the distribution and log the day.
    is_large = (d_eff > 0.05 * prev_close.fillna(np.inf)) & (prev_close > 0)
    expected_drop = np.divide(d_eff, prev_close, out=np.zeros(len(df)), where=prev_close > 0)
    actual_drop = 1.0 - np.divide(close * eff_split, prev_close, out=np.ones(len(df)), where=prev_close > 0)
    # Only the unambiguous case: a distribution worth more than 20% of the
    # price, where essentially no drop occurred. A moderate dividend on a day
    # the stock happened to rise is NOT this, and must not be stripped.
    inconsistent = (d_eff > 0.20 * prev_close.fillna(np.inf)) & (prev_close > 0) \
                   & (actual_drop < 0.25 * expected_drop)
    for i in np.flatnonzero(inconsistent):
        ANOMALIES.append((df.ticker.iloc[0], df.date.iloc[i].date(), float(prev_close.iloc[i]),
                          float(div.iloc[i]), float(close.iloc[i]),
                          "distribution recorded but price did not fall - ignored (already in the price)"))
    div = div.where(~inconsistent, 0.0); d_eff = d_eff.where(~inconsistent, 0.0)
    small = 1.0 - np.divide(d_eff, prev_close, out=np.zeros(len(df)), where=prev_close > 0)
    denom = close + div
    large = np.divide(close, denom, out=np.ones(len(df)), where=denom > 0)
    use_large = is_large & ~inconsistent
    div_factor = np.where(use_large, large, np.where(prev_close > 0, small, 1.0))
    factor = pd.Series(div_factor, index=df.index) / eff_split

    # Spin-offs (spinoffs.csv): the ex-date return is set to what a holder of
    # the parent actually had at that day's close - the parent's close (times
    # parent_shares when the event also changed the share count: a reverse
    # split, a partial redemption) plus the spun-off shares at THEIR first
    # regular-way close - which is how S&P has
    # booked spin-offs since October 2015 (the spin-off joins the index at a
    # zero price and its value appears at that close). Yahoo instead scales the
    # parent's history by the spin-off's when-issued price the day before, which
    # leaves the spin-off's first-day move out of the parent's return (e.g.
    # Arconic 2020-04-01: Yahoo +7.2%, actual -7.0%). The closes in the table
    # are the published closes; the row is refused (and logged) unless our
    # stored ex-date close matches the table's to within 1%.
    tk = str(df["ticker"].iloc[0]) if "ticker" in df.columns else ""
    ev = SPINS[SPINS.ticker == tk] if len(SPINS) else SPINS
    if len(ev):
        dts = pd.to_datetime(df["date"])
        if dts.dt.tz is not None:
            dts = dts.dt.tz_localize(None)
        later = split[::-1].cumprod()[::-1].shift(-1).fillna(1.0)      # product of split/spin factors after each day
        for exd, g in ev.groupby("ex_date"):
            hit = np.flatnonzero((dts == exd).to_numpy())
            if len(hit) != 1 or hit[0] == 0:
                SPUN.append((tk, str(exd.date()), None, None, None, "REFUSED: ex-date not in the series")); continue
            i = int(hit[0])
            p0, p1 = float(g.parent_prev_close.iloc[0]), float(g.parent_close.iloc[0])
            ps = float(g.parent_shares.iloc[0]) if "parent_shares" in g.columns and pd.notna(g.parent_shares.iloc[0]) else 1.0
            target = (p1 * ps + float((g.ratio.astype(float) * g.spinco_close.astype(float)).sum())) / p0
            c0, c1 = float(close.iloc[i - 1]), float(close.iloc[i])
            scale = 1.0 if src in ("tiingo", "wiki") else float(later.iloc[i])
            if abs(c1 * scale / p1 - 1) > 0.01:
                SPUN.append((tk, str(exd.date()), None, None, None,
                             f"REFUSED: stored ex-date close {c1 * scale:.4f} does not match the table's {p1}")); continue
            note = ""
            if float(div.iloc[i]) != 0:
                if src in ("tiingo", "wiki", "kaggle"):
                    note = f"; replaces the vendor's distribution of {float(div.iloc[i]):.4f}"
                else:
                    SPUN.append((tk, str(exd.date()), None, None, None, "REFUSED: a cash dividend is booked on the ex-date")); continue
            implied = (c1 / c0) / (p1 / p0)      # the factor the stored history already carries for this event
            rec = float(split.iloc[i])
            ok = abs(implied / rec - 1) < 0.01 if rec != 1 else 0.99 <= implied <= 100
            if not ok:
                SPUN.append((tk, str(exd.date()), None, None, None, f"REFUSED: implied factor {implied:.4f} vs recorded {rec:g}")); continue
            old = c1 / (c0 * float(factor.iloc[i])) - 1
            factor.iloc[i] = c1 / (c0 * target)
            SPUN.append((tk, str(exd.date()), round(old * 100, 3), round((target - 1) * 100, 3), round(implied, 4), "applied" + note))

    # An adjustment factor <= 0 is impossible: it means a recorded dividend
    # exceeds the prior close, which happens when a provider books a spin-off
    # on a post-split basis but omits the split (KSU / Stilwell, July 2000).
    # Everything BEFORE such an event would come out negative, so the series
    # is cut at the event and the earlier history is set aside for review.
    bad = factor.index[(factor <= 0) & prev_close.notna()]
    if len(bad):
        cut = bad.max()
        for i in bad:
            ANOMALIES.append((df.ticker.iloc[0], df.date.iloc[i].date(), float(prev_close.iloc[i]),
                              float(div.iloc[i]), float(factor.iloc[i]),
                              f"history before {df.date.iloc[cut].date()} dropped"))
        df = df.iloc[cut + 1:].reset_index(drop=True)
        close, factor = df["close"].astype(float), factor.iloc[cut + 1:].reset_index(drop=True)
    # Back-adjust: each day's history is scaled by all FUTURE factors.
    cum = factor.iloc[::-1].shift(1).fillna(1.0).cumprod().iloc[::-1]
    df["adj_close"] = close * cum
    df["adj_factor"] = cum
    return df


SPINS_F = ROOT / "spinoffs.csv"
SPINS = pd.read_csv(SPINS_F, parse_dates=["ex_date"]) if SPINS_F.exists() else pd.DataFrame(columns=["ticker", "ex_date"])
SPUN = []               # (ticker, ex-date, old event-day return %, new %, implied factor, outcome)

TRIMMED = []
PATCHES_F = ROOT / "price_patches.csv"
PATCHES = pd.read_csv(PATCHES_F, parse_dates=["date"]) if PATCHES_F.exists() else pd.DataFrame()
PATCHED = []
REPLACED = []
PLACEHOLDERS = []
MEMBER_LABELS = set()
MEMBER_ENDS = {}            # label -> membership end dates (S&P removal days)
CARRIED = []                # end-of-membership no-trade days kept on purpose
_ivf = ROOT / "data" / "universe" / "membership_intervals.parquet"
if _ivf.exists():
    _iv = pd.read_parquet(_ivf, columns=["ticker", "start", "end"])
    MEMBER_LABELS = set(_iv.ticker)
    for _t, _e in zip(_iv.ticker, pd.to_datetime(_iv["end"])):
        if pd.notna(_e):
            MEMBER_ENDS.setdefault(_t, []).append(_e)


def drop_placeholders(df, stem):
    """Remove mid-series placeholder bars from index stocks: zero volume and
    open = high = low = close = the previous close. Yahoo emits these on days
    it has no record (Crown Castle 2020-07-31: the real bar closed $166.70 on
    3.3M shares). They carry no trade, so dropping one never changes a
    multi-day return; price_patches.csv then supplies the real bar where a
    second source has it. ETFs are left alone (thin ones can genuinely have
    no trades on a day). The last row is never touched (trim_filler's job).
    Kept on purpose: a no-trade bar in the last 7 days before S&P removed the
    name (Signature Bank 2023-03-14 and First Republic 2023-05-02, halted;
    Ansys 2025-07-17, deal closed the day before removal). The index carried
    those stocks at their last price until removal, so the flat bar is the
    right row for that member-day (logged as CARRIED)."""
    if stem not in MEMBER_LABELS or len(df) < 3:
        return df
    df = df.sort_values("date").reset_index(drop=True)
    pc = df["close"].shift()
    vol = pd.to_numeric(df.get("volume", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    bad = (vol == 0) & (df["open"] == df["high"]) & (df["high"] == df["low"]) & (df["low"] == df["close"]) & (df["close"] == pc)
    if "dividends" in df:                       # a row carrying a dividend or a split is an event: keep it
        bad &= df["dividends"].fillna(0).eq(0)
    if "stock_splits" in df:
        bad &= df["stock_splits"].fillna(1).replace(0, 1).eq(1)
    bad.iloc[-1] = False
    ends = MEMBER_ENDS.get(stem)
    if ends and bad.any():
        dd = pd.to_datetime(df["date"])
        if dd.dt.tz is not None:
            dd = dd.dt.tz_localize(None)
        carry = pd.Series(False, index=df.index)
        for e in ends:
            carry |= (dd < e) & (dd >= e - pd.Timedelta(days=7))
        for x in dd[bad & carry]:
            CARRIED.append((stem, str(x.date())))
        bad &= ~carry
    if bad.any():
        d = pd.to_datetime(df.loc[bad, "date"])
        for x in (d.dt.tz_localize(None) if d.dt.tz is not None else d):
            PLACEHOLDERS.append((stem, str(x.date()), str(df["source"].iloc[0]) if "source" in df else "?"))
        df = df[~bad].reset_index(drop=True)
    return df


SPLICES_F = ROOT / "series_splices.csv"
SPLICES = pd.read_csv(SPLICES_F, parse_dates=["before"]) if SPLICES_F.exists() else pd.DataFrame(columns=["label", "prefix", "before"])
SPLICED = []


def apply_splice(df, stem):
    """Join a company's earlier history, held under another raw file, in front
    of its current series (series_splices.csv: HWM <- ARNC before 2016-11-01).
    The prefix is rescaled to the current series' price basis by the median
    close ratio over overlapping days; the splice is refused unless there are
    >= 60 overlapping days and the ratio is constant to within 0.5%."""
    rows = SPLICES[SPLICES.label == stem]
    if rows.empty:
        return df
    r = rows.iloc[0]
    pf = RAW / f"{r.prefix}.parquet"
    if not pf.exists():
        print(f"  splice {stem} <- {r.prefix}: prefix file missing"); return df
    pre = pd.read_parquet(pf)
    for x in (df, pre):
        d = pd.to_datetime(x["date"]); x["date"] = d.dt.tz_localize(None) if d.dt.tz is not None else d
    j = df[["date", "close"]].merge(pre[["date", "close"]], on="date", suffixes=("", "_p")).dropna()
    ratio = (j.close / j.close_p)
    if len(j) < 60 or (ratio.quantile(0.95) - ratio.quantile(0.05)) / ratio.median() > 0.005:
        print(f"  splice {stem} <- {r.prefix}: REFUSED ({len(j)} overlapping days, ratio spread {(ratio.quantile(0.95)-ratio.quantile(0.05))/ratio.median():.4f})")
        return df
    k = float(ratio.median())
    add = pre[(pre.date < r.before) & (~pre.date.isin(set(df.date)))].copy()
    for col in ("open", "high", "low", "close", "dividends"):
        if col in add:
            add[col] = add[col] * k
    add["ticker"] = stem
    add["source"] = str(pre["source"].iloc[0]) + "-splice"
    SPLICED.append((stem, r.prefix, len(add), round(k, 4), len(j)))
    return pd.concat([add[df.columns.intersection(add.columns)], df], ignore_index=True).sort_values("date").reset_index(drop=True)


def apply_patches(df, stem):
    """Hand-verified rows (price_patches.csv). Values must already be on the
    series' price basis.
      action=fill (the default): adds a day the source is missing, e.g.
        Veralto's first two trading days. Never touches an existing row.
      action=replace: overwrites open/high/low/close/volume of an existing day
        that two independent sources show to be wrong (Vontier 2020-10-09:
        Nasdaq, Tiingo and Fortive's Form 8937 against Yahoo). The row keeps its
        dividend and split. Refused, and logged, when the date is not in the
        series or the existing close is more than 5% away from the patch (that
        would mean the series' price basis moved and the patch needs redoing)."""
    if PATCHES.empty or stem not in set(PATCHES.ticker):
        return df
    d = pd.to_datetime(df["date"])
    if d.dt.tz is not None:
        d = d.dt.tz_localize(None)
    df = df.assign(date=d).reset_index(drop=True)
    mine = PATCHES[PATCHES.ticker == stem]
    act = (mine["action"].fillna("fill").astype(str).str.strip().str.lower()
           if "action" in mine.columns else pd.Series("fill", index=mine.index))
    for _, p in mine[act.eq("replace")].iterrows():
        hit = df.index[df["date"] == p["date"]]
        if len(hit) != 1:
            REPLACED.append((stem, str(p["date"].date()), None, float(p["close"]), "REFUSED: date not in series"))
            continue
        i = hit[0]
        old = float(df.at[i, "close"])
        if not abs(old / float(p["close"]) - 1) <= 0.05:
            REPLACED.append((stem, str(p["date"].date()), old, float(p["close"]), "REFUSED: more than 5% from the existing close"))
            continue
        for c in ("open", "high", "low", "close", "volume"):
            if c in df.columns and pd.notna(p.get(c)):
                df.at[i, c] = float(p[c])
        if "source" in df.columns:
            df["source"] = df["source"].astype(object)
            df.at[i, "source"] = str(p["source"])
        REPLACED.append((stem, str(p["date"].date()), round(old, 4), float(p["close"]), "replaced"))
    add = mine[act.eq("fill") & (~mine.date.isin(set(d)))]
    if add.empty:
        return df
    cols = [c for c in ("date", "open", "high", "low", "close", "volume", "dividends", "stock_splits", "source") if c in add.columns]
    rows = add[cols].assign(ticker=stem)
    PATCHED.append((stem, len(rows)))
    return pd.concat([df, rows], ignore_index=True).sort_values("date").reset_index(drop=True)


def trim_filler(df, stem):
    """Drop fake rows at the end of a DEAD series. Tiingo pads delisted companies
    with flat bars after their last trade (open = high = low = close = the final
    close, near-zero volume) - for days, sometimes a year (Pepsi Bottling: 258).
    They change no return but they are not trades, and they put a company on
    the tape after it stopped trading. Only trailing rows of a series whose last
    real (non-flat) bar is more than 30 days old are touched, and never past a
    split. A dividend
    booked on a filler day (Baxalta, St. Jude, XL, Pepsi Bottling, Constellation:
    the next scheduled dividend, dated after the company had stopped trading)
    is dropped with the row and logged - a security that no longer trades
    cannot go ex-dividend."""
    df = df.sort_values("date").reset_index(drop=True)
    d = pd.to_datetime(df["date"])
    if d.dt.tz is not None:
        d = d.dt.tz_localize(None)
    if len(df) < 3:
        return df
    o, h, l, c = (df[x].to_numpy(dtype=float) for x in ("open", "high", "low", "close"))
    dv = df["dividends"].fillna(0).to_numpy(dtype=float) if "dividends" in df else [0.0] * len(df)
    sp = df["stock_splits"].fillna(1).replace(0, 1).to_numpy(dtype=float) if "stock_splits" in df else [1.0] * len(df)
    k = len(df)
    while k > 1 and o[k-1] == h[k-1] == l[k-1] == c[k-1] == c[k-2] and sp[k-1] == 1:
        k -= 1
    # dead = the last REAL bar is more than 30 days old. Judged on the last
    # non-flat row, not the last row: Tiingo keeps padding some acquired
    # companies with a flat bar every day up to today (Ansys after 2025-07-16).
    if d.iloc[k - 1] >= pd.Timestamp.today().normalize() - pd.Timedelta(days=30):
        return df
    if k < len(df):
        TRIMMED.append((stem, str(df.get("source", pd.Series(["?"])).iloc[0]), len(df) - k,
                        str(pd.to_datetime(df["date"].iloc[k-1]).date()), str(pd.to_datetime(df["date"].iloc[-1]).date()),
                        round(float(sum(dv[k:])), 4)))
        df = df.iloc[:k]
    return df


def main():
    # Labels whose stored history belongs to a different company that later
    # reused the ticker are never published (see quarantine_tickers.txt).
    qf = ROOT / "quarantine_tickers.txt"
    quarantined = {l.split("#")[0].strip() for l in qf.read_text().splitlines()} - {""} if qf.exists() else set()
    prefixes = set(SPLICES.prefix) if not SPLICES.empty else set()     # merged into their label, not published on their own
    files = sorted(f for f in RAW.glob("*.parquet") if f.stem not in quarantined and f.stem not in prefixes)
    print(f"quarantined labels skipped: {len(quarantined)}")
    out = []
    for i, f in enumerate(files):
        try:
            out.append(adjust_one(trim_filler(apply_patches(drop_placeholders(apply_splice(pd.read_parquet(f), f.stem), f.stem), f.stem), f.stem)))
        except Exception as e:
            print(f"  skip {f.stem}: {e}")
        if (i + 1) % 250 == 0:
            print(f"  adjusted {i + 1}/{len(files)}", flush=True)

    all_df = pd.concat(out, ignore_index=True)
    all_df["date"] = pd.to_datetime(all_df["date"]).dt.tz_localize(None)
    if "source" not in all_df.columns:
        all_df["source"] = "yahoo"
    all_df["source"] = all_df["source"].fillna("yahoo")
    keep = ["date", "ticker", "open", "high", "low", "close", "volume",
            "adj_close", "dividends", "stock_splits", "source"]
    all_df = all_df[[c for c in keep if c in all_df.columns]]
    all_df = all_df.sort_values(["ticker", "date"])
    all_df.to_parquet(ROOT / "data" / "prices.parquet", index=False,
                      compression="zstd", row_group_size=100_000)   # small groups = fast remote ticker queries

    pd.DataFrame(TRIMMED, columns=["ticker", "source", "rows_dropped", "last_trade", "last_filler", "dividends_dropped"]) \
        .to_csv(ROOT / "data" / "trimmed_filler.csv", index=False)
    pd.DataFrame(PLACEHOLDERS, columns=["ticker", "date", "source"]).to_csv(ROOT / "data" / "dropped_placeholders.csv", index=False)
    print(f"placeholder bars dropped (zero volume, flat at prior close): {len(PLACEHOLDERS):,} (data/dropped_placeholders.csv)")
    print(f"end-of-membership no-trade bars kept (halt / deal closed before removal): {CARRIED}")
    print(f"hand-verified patch rows added: {PATCHED}")
    print(f"hand-verified bars replaced (ticker, date, old close, new close, outcome): {REPLACED}")
    print("spin-off ex-dates set to the holder's actual return (ticker, ex-date, old %, new %, factor already in history, outcome):")
    for x in SPUN:
        print(f"    {x}")
    print(f"series splices (label, prefix, rows, ratio, overlap days): {SPLICED}")
    print(f"trailing filler trimmed: {sum(t[2] for t in TRIMMED):,} rows from {len(TRIMMED)} dead series (data/trimmed_filler.csv)")
    pd.DataFrame(ANOMALIES, columns=["ticker", "date", "prev_close", "dividend", "factor", "action"]) \
        .to_csv(ROOT / "data" / "adjustment_anomalies.csv", index=False)
    if ANOMALIES:
        print(f"\nADJUSTMENT ANOMALIES: {len(ANOMALIES)} (see data/adjustment_anomalies.csv)")
        for a in ANOMALIES: print("   ", *a)
    mb = (ROOT / "data" / "prices.parquet").stat().st_size / 1e6
    print(f"\nrows      : {len(all_df):,}")
    print(f"tickers   : {all_df['ticker'].nunique():,}")
    print(f"range     : {all_df['date'].min().date()} -> "
          f"{all_df['date'].max().date()}")
    print(f"file size : {mb:.0f} MB")


if __name__ == "__main__":
    main()
