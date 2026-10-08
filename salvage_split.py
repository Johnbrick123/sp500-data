"""
Step 2f: Salvage dead companies whose ticker was REUSED and whose source series
runs straight on into the next owner.

WIKI and Tiingo both keep one series per ticker. When a company dies and its
symbol is later given to someone else, the series looks like
    [dead company ........ delisting] <gap> [new owner ........ today]
and the identity check "series must end near the label month" rejects it,
throwing away the dead company's history (Altera under ALTR, the first ADT
under ADT, DirecTV under DTV ...). This pass re-fetches exactly those rejects,
cuts the series at the label month and keeps the first part only when the cut
is unmistakable:
  - a calendar gap of >= 45 days right after the cut (ticker lay dormant), or
  - the tail after the cut is dead (no volume / flat price = stale filler).
A series that runs continuously into the next owner (ticker handed over on the
deal day, e.g. Allergan -> Actavis) is still rejected: there is no safe cut.
The kept part then passes the normal checks (>= 250 days of membership overlap,
not a copy of today's owner). Needs NASDAQ_DATA_LINK_KEY and TIINGO_API_KEY.
"""
import json, re, sys, time
from pathlib import Path
import pandas as pd
import fetch_perma as fp
import fetch_perma_names as fpn
import fetch_wiki as fw

ROOT, RAW, SUFFIX = fp.ROOT, fp.RAW, fp.SUFFIX
say = fp.say
REPORT = ROOT / "data" / "salvage_report.txt"
PROG = ROOT / "data" / "salvage_progress.json"
RELABELS = fp.RELABELS
ENDS_LATER = re.compile(r"ends (\d{4}-\d{2}-\d{2}),? (?:but )?label says (\d{4}-\d{2})")


def label_month(lab):
    mo = SUFFIX.search(lab)
    return pd.Timestamp(mo.group(1)[:4] + "-" + mo.group(1)[4:] + "-01") if mo else None


def split_at_label(df, lab):
    """Return (kept_frame, reason) or (None, reason)."""
    dl = label_month(lab)
    if dl is None:
        return None, "bare label, nothing to split on"
    cut = dl + pd.offsets.MonthEnd(0) + pd.Timedelta(days=15)
    pre, post = df[df.date <= cut], df[df.date > cut]
    # stale filler (zero-volume days) at the end of the kept part belongs to the tail
    v = pd.to_numeric(pre.volume, errors="coerce").fillna(0).to_numpy()
    k = len(v)
    while k > 1 and v[k - 1] == 0:
        k -= 1
    post = pd.concat([pre.iloc[k:], post]); pre = pre.iloc[:k]
    if len(pre) < 250:
        return None, f"only {len(pre)} days before the label month"
    if pre.date.max() < dl - pd.Timedelta(days=45):
        return None, f"series already ends {pre.date.max().date()}, before the label month"
    if post.empty:
        return pre, "ends at the label month"
    gap = (post.date.min() - pre.date.max()).days
    vol = pd.to_numeric(post.volume, errors="coerce").fillna(0)
    dead = (vol == 0).mean() > 0.9 or post.close.nunique() <= 3
    if gap >= 45:
        return pre, f"cut at {pre.date.max().date()}: {gap}-day gap before the ticker's next owner"
    if dead:
        return pre, f"cut at {pre.date.max().date()}: tail after it is dead filler ({len(post)} rows)"
    return None, f"runs continuously into the next owner ({gap}-day gap) - no safe cut"


def wiki_rejects():
    rep = ROOT / "data" / "wiki_report.txt"
    out = {}
    if rep.exists():
        for l in rep.read_text().splitlines():
            m = re.search(r"rejected (\S+): " + ENDS_LATER.pattern, l)
            if m and pd.Timestamp(m.group(2)) > pd.Timestamp(m.group(3)) + pd.Timedelta(days=60):
                out[m.group(1)] = m.group(2)
    return out


def tiingo_rejects():
    out = {}
    for f in ("perma_progress.json", "perma_names_progress.json"):
        p = ROOT / "data" / f
        if p.exists():
            for lab, st in json.loads(p.read_text())["done"].items():
                m = ENDS_LATER.search(st)
                if m and pd.Timestamp(m.group(1)) > pd.Timestamp(m.group(2)) + pd.Timedelta(days=60):
                    out[lab] = m.group(1)
    return out


def save(df, lab, src, m_start, relabels):
    bare = SUFFIX.sub("", lab)
    pd.DataFrame({"date": df["date"], "open": df["open"], "high": df["high"], "low": df["low"],
                  "close": df["close"], "volume": df["volume"],
                  "dividends": df["dividends"].fillna(0.0), "stock_splits": df["stock_splits"].fillna(1.0),
                  "ticker": lab, "source": src}).to_parquet(RAW / f"{lab}.parquet", index=False)


def main():
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    span = iv.groupby("ticker").agg(m_start=("start", "min"), m_end=("end", "max"))
    have = {p.stem for p in RAW.glob("*.parquet")}
    prog = json.loads(PROG.read_text()) if PROG.exists() else {"done": {}, "recovered": []}
    n = 0

    # ---- WIKI ------------------------------------------------------------
    wr = {k: v for k, v in wiki_rejects().items() if k not in have and k not in prog["done"]}
    say(f"WIKI: {len(wr)} rejected-for-running-on series to re-examine")
    for lab in sorted(wr):
        bare = SUFFIX.sub("", lab)
        df = fw.fetch_ticker(bare)
        if df is None or df.empty:
            prog["done"][lab] = "wiki: no data"; say(f"  {lab}: WIKI returned nothing"); continue
        df["date"] = pd.to_datetime(df["date"]); df = df.sort_values("date").reset_index(drop=True)
        df = df.rename(columns={"ex-dividend": "dividends", "split_ratio": "stock_splits"})
        kept, why = split_at_label(df, lab)
        if kept is None:
            prog["done"][lab] = "wiki: " + why; say(f"  reject {lab} (WIKI): {why}"); continue
        ok, why2 = fp.accept(lab, kept, span.loc[lab, "m_start"], span.loc[lab, "m_end"], bare)
        if not ok:
            prog["done"][lab] = "wiki: " + why2; say(f"  reject {lab} (WIKI): {why} BUT {why2}"); continue
        save(kept, lab, "wiki", span.loc[lab, "m_start"], None)
        prog["done"][lab] = "recovered (wiki)"; prog["recovered"].append(lab); n += 1
        say(f"  RECOVERED {lab} from WIKI: {len(kept):,} rows {kept.date.min().date()}..{kept.date.max().date()} close {kept.close.iloc[-1]:.2f} - {why}")
        time.sleep(0.3)

    # ---- Tiingo (by permaTicker) ----------------------------------------
    for k in [k for k, v in prog["done"].items() if v.startswith("tiingo: ") and "request failed" in v and "RECOVERED" not in v]:
        prog["done"].pop(k)
    tr = {k: v for k, v in tiingo_rejects().items() if k not in have and k not in prog["done"]}
    say(f"\nTiingo: {len(tr)} rejected-for-running-on labels to re-examine")
    cands = {}
    if tr:
        for lab, bare, pid, name, active, m_start, m_end in fp.candidates(iv, have - set(tr), {"done": {}}):
            if lab in tr:
                cands.setdefault(lab, []).append((pid, name))
        try:
            shar = fpn.sharadar_tickers()
            wanted = fpn.names_for_missing(iv, have - set(tr), {}, shar)
            for lab, bare, pid, name, active, m_start, m_end, shname in fpn.directory_match({k: v for k, v in wanted.items() if k in tr}, set()):
                if (pid, name) not in cands.get(lab, []):
                    cands.setdefault(lab, []).append((pid, name))
        except Exception as e:
            say(f"  (name matching unavailable: {type(e).__name__})")
    calls = 0
    paused = [False]
    def tiingo_get(url):
        """One Tiingo request; on an HTTP error (usually the hourly cap) wait an hour once and retry."""
        import urllib.error
        for attempt in (1, 2):
            try:
                return fp.get(url)
            except urllib.error.HTTPError as e:
                if attempt == 1 and not paused[0]:
                    say(f"  Tiingo HTTP {e.code} - pausing 61 min for the hourly cap"); paused[0] = True; time.sleep(61 * 60); continue
                raise
    for lab in sorted(tr):
        bare = SUFFIX.sub("", lab)
        verdicts = []
        for pid, name in cands.get(lab, []):
            if calls >= 45:
                say("  hourly Tiingo cap reached - the rest waits for the next run"); break
            try:
                rows = tiingo_get(f"https://api.tiingo.com/tiingo/daily/{pid}/prices?startDate=1990-01-01&format=json&token={fp.KEY}"); calls += 1
            except Exception as e:
                verdicts.append(f"{name}: request failed {type(e).__name__}"); continue
            if not rows or len(rows) < 250:
                verdicts.append(f"{name}: {len(rows or [])} rows"); continue
            df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            df = df.rename(columns={"divCash": "dividends", "splitFactor": "stock_splits"})
            for c in ("dividends", "stock_splits"):
                if c not in df:
                    df[c] = 0.0 if c == "dividends" else 1.0
            kept, why = split_at_label(df, lab)
            if kept is None:
                verdicts.append(f"{name}: {why}"); continue
            ok, why2 = fp.accept(lab, kept, span.loc[lab, "m_start"], span.loc[lab, "m_end"], bare)
            if not ok:
                verdicts.append(f"{name}: {why} BUT {why2}"); continue
            save(kept, lab, "tiingo", span.loc[lab, "m_start"], None)
            prog["done"][lab] = "recovered (tiingo)"; prog["recovered"].append(lab); n += 1
            say(f"  RECOVERED {lab} from Tiingo ({name}): {len(kept):,} rows {kept.date.min().date()}..{kept.date.max().date()} close {kept.close.iloc[-1]:.2f} - {why}")
            break
        else:
            if calls < 45:
                if verdicts and all("request failed" in v for v in verdicts):
                    say(f"  retry later {lab} (Tiingo): " + "; ".join(verdicts)); continue   # not recorded: retried next run
                prog["done"][lab] = "tiingo: " + ("; ".join(verdicts) or "no directory match")
                say(f"  reject {lab} (Tiingo): " + ("; ".join(verdicts) or "no directory match"))
            continue
        if calls >= 45:
            break
    PROG.write_text(json.dumps(prog, indent=1))
    say(f"\nthis run: {n} recovered. cumulative: {len(prog['recovered'])}")
    REPORT.write_text("\n".join(fp.lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say(f"CRASHED: {type(e).__name__}: {e}")
        REPORT.write_text("\n".join(fp.lines)); raise
