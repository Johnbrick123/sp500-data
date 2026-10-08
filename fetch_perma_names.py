"""
Step 2e: Recover missing index members via Tiingo permaTicker, matched by NAME.

fetch_perma.py matches missing members to Tiingo's company directory by
ticker. That misses every dead company whose directory entry carries a
different or stale symbol (e.g. the pre-2017 Dow Chemical is not listed under
DOW at all). This pass closes that hole:

  1. Sharadar's ticker table (Nasdaq Data Link, readable on the free key)
     gives the NAME of the company that owned each missing ticker during its
     index membership, by matching ticker + date window.
  2. That name is matched against Tiingo's directory (20,000 companies,
     12,500 dead, each with a permaTicker).
  3. Prices are pulled by permaTicker and pass the same identity checks as
     fetch_perma.py (end date vs label month, >=250 days of membership
     overlap, not a copy of today's owner of the ticker).

Free tier: 50 Tiingo requests/hour, so 45 names per batch with an hour
between batches. Progress persists in data/perma_names_progress.json.
Needs TIINGO_API_KEY and NASDAQ_DATA_LINK_KEY.
"""
import difflib, json, os, re, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path
import pandas as pd
import fetch_perma as fp                     # shares KEY, RAW, accept(), say()

NDL = (os.environ.get("NASDAQ_DATA_LINK_KEY") or sys.exit("NASDAQ_DATA_LINK_KEY not set")).strip()
ROOT = fp.ROOT
RAW = fp.RAW
PROG = ROOT / "data" / "perma_names_progress.json"
RELABELS = fp.RELABELS
REPORT = ROOT / "data" / "perma_names_report.txt"
PER_RUN = int(os.environ.get("PERMA_PER_RUN", "45"))
BATCHES = int(os.environ.get("PERMA_BATCHES", "4"))
SUFFIX = fp.SUFFIX
say, get = fp.say, fp.get

STOP = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "COMPANIES", "LTD", "LIMITED",
        "PLC", "LLC", "LP", "HOLDINGS", "HOLDING", "GROUP", "THE", "AND", "OF", "SA", "NV", "AG",
        "CLASS", "A", "B", "COMMON", "STOCK", "SHARES", "ORDINARY", "NEW", "OLD", "DEL"}


def norm(name):
    s = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper().replace("&", " AND "))
    toks = [t for t in s.split() if t not in STOP]
    return " ".join(toks)


def sharadar_tickers():
    """Full Sharadar ticker table (SEP) with names and price date ranges."""
    base = "https://data.nasdaq.com/api/v3/datatables/SHARADAR/TICKERS.json?table=SEP&qopts.per_page=10000"
    cols = "ticker,name,exchange,isdelisted,firstpricedate,lastpricedate,relatedtickers,permaticker"
    rows, cur = [], None
    for _ in range(40):
        url = base + f"&qopts.columns={cols}&api_key={urllib.parse.quote(NDL)}" + (f"&qopts.cursor_id={urllib.parse.quote(str(cur))}" if cur else "")
        d = get(url); dt = d["datatable"]
        names = [c["name"] for c in dt["columns"]]
        rows += [dict(zip(names, r)) for r in dt["data"]]
        cur = (d.get("meta") or {}).get("next_cursor_id")
        if not cur:
            break
    df = pd.DataFrame(rows)
    for c in ("firstpricedate", "lastpricedate"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["tk"] = df.ticker.astype(str).str.upper().str.replace(".", "-", regex=False)
    say(f"Sharadar ticker table: {len(df):,} listings, {int(df.isdelisted.astype(str).str.upper().eq('Y').sum()):,} delisted")
    return df


def names_for_missing(iv, have, done, shar):
    """label -> (bare, membership start/end, list of Sharadar names that owned the ticker then)."""
    # index every listing under its ticker, the ticker without a bankruptcy Q /
    # trailing digits, and each related ticker
    idx = {}
    for i, r in enumerate(shar.itertuples()):
        keys = {r.tk, re.sub(r"Q$", "", r.tk), re.sub(r"-?\d{1,2}Q?$", "", r.tk)}
        keys |= {t for t in re.split(r"[ ,]+", str(r.relatedtickers or "").upper()) if t and t != "NAN" and t != "NONE"}
        for k in keys:
            idx.setdefault(k, []).append(i)
    out = {}
    for lab, g in iv.groupby("ticker"):
        if lab in have or lab in done:
            continue
        bare = SUFFIX.sub("", lab)
        m_start, m_end = g.start.min(), g["end"].max()
        stop = m_end if pd.notna(m_end) else pd.Timestamp.today()
        names = []
        for i in idx.get(bare, []):
            r = shar.iloc[i]
            if pd.isna(r.firstpricedate) or pd.isna(r.lastpricedate):
                continue
            ov = (min(r.lastpricedate, stop) - max(r.firstpricedate, m_start)).days
            # a listing that began long after the membership did is the ticker's NEXT owner
            if ov >= 60 and r.firstpricedate <= m_start + pd.Timedelta(days=400):
                names.append((str(r["name"]), r.tk, str(r.firstpricedate.date()), str(r.lastpricedate.date())))
        if names:
            out[lab] = (bare, m_start, m_end, names)
    return out


def directory_match(wanted, tried_pids):
    """Match each wanted name to Tiingo directory entries by normalised name."""
    meta = get(f"https://api.tiingo.com/tiingo/fundamentals/meta?token={fp.KEY}")
    say(f"Tiingo directory: {len(meta):,} companies, {sum(1 for m in meta if not m.get('isActive')):,} inactive")
    by_norm = {}
    for m in meta:
        if m.get("permaTicker"):
            by_norm.setdefault(norm(m.get("name")), []).append(m)
    keys = list(by_norm)
    todo = []
    for lab, (bare, m_start, m_end, names) in wanted.items():
        seen = set()
        for nm, stk, f, l in names:
            n = norm(nm)
            if not n:
                continue
            hits = by_norm.get(n, [])
            if not hits:
                close = difflib.get_close_matches(n, keys, n=3, cutoff=0.88)
                hits = [m for k in close for m in by_norm[k]]
            for m in hits:
                pid = m["permaTicker"]
                if pid in seen or pid in tried_pids:
                    continue
                # a living company is only a candidate for a bare (undated) label
                if m.get("isActive") and SUFFIX.search(lab):
                    continue
                seen.add(pid)
                todo.append((lab, bare, pid, str(m.get("name"))[:40], bool(m.get("isActive")), m_start, m_end, nm[:40]))
    todo.sort(key=lambda x: (0 if SUFFIX.search(x[0]) else 1, x[0]))
    return todo


def main():
    iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
    iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
    have = {p.stem for p in RAW.glob("*.parquet")}
    prog = json.loads(PROG.read_text()) if PROG.exists() else {"done": {}, "recovered": [], "pids": []}
    prog.setdefault("pids", [])
    # permaTickers the ticker-based sweep already pulled are not worth a second request
    tried_pids = set(prog["pids"])
    shar = sharadar_tickers()
    wanted = names_for_missing(iv, have, prog["done"], shar)
    say(f"{len(wanted)} missing members have a Sharadar name for their membership window")
    for lab, (bare, s, e, names) in sorted(wanted.items()):
        say(f"  {lab:14s} {names[0][0][:38]:38s} ({names[0][1]} {names[0][2]}..{names[0][3]})" + (f" +{len(names)-1} more" if len(names) > 1 else ""))
    todo = directory_match(wanted, tried_pids)
    say(f"{len(todo)} Tiingo directory entries matched by name")
    relabels = {}
    if RELABELS.exists():
        for r in pd.read_csv(RELABELS).itertuples():
            relabels[r.bare] = (r.label, str(r.start), str(r.cutoff))
    n, fails, paused = 0, 0, False
    def pending(t): return t[0] not in prog["done"] or prog["done"][t[0]].startswith("request failed")
    for b in range(BATCHES):
        batch = [t for t in todo if pending(t)][:PER_RUN]
        if not batch:
            say("nothing left to try"); break
        for lab, bare, pid, name, active, m_start, m_end, shname in batch:
            try:
                rows = get(f"https://api.tiingo.com/tiingo/daily/{pid}/prices?startDate=1990-01-01&format=json&token={fp.KEY}")
                fails = 0
            except urllib.error.HTTPError as e:
                fails += 1
                prog["done"][lab] = f"request failed HTTP {e.code}"; say(f"  failed {lab} ({name}): HTTP {e.code}")
                if fails >= 3 and not paused:
                    say("three HTTP failures in a row - pausing 61 min in case it is the hourly cap"); time.sleep(61 * 60); fails, paused = 0, True
                elif fails >= 3:
                    say("still failing after the pause - stopping (monthly cap?)"); b = BATCHES; break
                continue
            except Exception as e:
                prog["done"][lab] = f"request failed {type(e).__name__}"; say(f"  failed {lab} ({name}): {type(e).__name__}"); continue
            prog["pids"].append(pid)
            if not rows or len(rows) < 250:
                prog["done"][lab] = "no data"; say(f"  no data {lab} ({name} <- {shname}): {len(rows or [])} rows"); continue
            df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
            ok, why = fp.accept(lab, df, m_start, m_end, bare)
            if not ok:
                prog["done"][lab] = "rejected: " + why; say(f"  reject {lab} ({name} <- {shname}): {why}"); continue
            out_label = lab
            if not SUFFIX.search(lab) and (RAW / f"{bare}.parquet").exists():
                out_label = f"{bare}-{df.date.max():%Y%m}"
                relabels[bare] = (out_label, max(df.date.min(), m_start).strftime("%Y-%m-%d"),
                                  (df.date.max() + pd.Timedelta(days=30)).strftime("%Y-%m-%d"))
            pd.DataFrame({"date": df["date"], "open": df["open"], "high": df["high"], "low": df["low"],
                          "close": df["close"], "volume": df["volume"],
                          "dividends": df.get("divCash", 0.0), "stock_splits": df.get("splitFactor", 1.0),
                          "ticker": out_label, "source": "tiingo"}).to_parquet(RAW / f"{out_label}.parquet", index=False)
            prog["done"][lab] = "recovered"; prog["recovered"].append(out_label); n += 1
            say(f"  RECOVERED {out_label} ({name} <- {shname}): {len(df):,} rows {df.date.min().date()}..{df.date.max().date()} close {df.close.iloc[-1]:.2f}")
            time.sleep(0.5)
        PROG.write_text(json.dumps(prog, indent=1))
        if relabels:
            pd.DataFrame([(k, v[0], v[1], v[2]) for k, v in relabels.items()], columns=["bare", "label", "start", "cutoff"]).to_csv(RELABELS, index=False)
        if b >= BATCHES:
            break
        if b < BATCHES - 1 and any(pending(t) for t in todo):
            say(f"batch {b+1}/{BATCHES} done; sleeping 61 min for the hourly cap"); time.sleep(61 * 60)
    say(f"\nthis run: {n} recovered. cumulative recovered by name: {len(prog['recovered'])}")
    REPORT.write_text("\n".join(fp.lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        say(f"CRASHED: {type(e).__name__}: {e}")
        REPORT.write_text("\n".join(fp.lines)); raise
