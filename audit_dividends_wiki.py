"""
Dividend audit against Quandl/Nasdaq WIKI's ex-dividend record, for years WIKI
covers (it stopped updating in April 2018). WIKI is community-maintained and
independent of Yahoo, so for 2017 and earlier it does what audit_dividends.py
does with Tiingo for 2018 on - without using any Tiingo requests.

Our stored dividend is on the series' own basis: for a Yahoo series it is the
amount divided by every later split factor (including Yahoo's spin-off
factors), so it is multiplied back by the product of later factors before the
comparison. Tiingo and WIKI series are raw already.

WIKI symbols: the label without its -YYYYMM suffix, '-' -> '_' (BRK-B -> BRK_B),
plus every older symbol renames.csv maps to the label (LUMN was CTL in 2017).
The symbol used is the one whose WIKI closes move with ours (>= 95% of days
within 0.5pp) - a reused symbol never stands in for the member.

Usage: python audit_dividends_wiki.py YEARS [TICKERS]
  YEARS  2017 or 2013-2017; TICKERS (comma list) limits the check to those labels
-> data/dividend_audit_wiki_<YEARS>.txt and _overrides.csv (rows on our stored
   basis, to review before anything goes into corporate_action_overrides.csv).
Needs NASDAQ_DATA_LINK_KEY.
"""
import json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from collections import Counter
from pathlib import Path
import numpy as np
import pandas as pd

RANGE = sys.argv[1]
Y0, Y1 = int(RANGE.split("-")[0]), int(RANGE.split("-")[-1])
ONLY = {t.strip() for t in (sys.argv[2] if len(sys.argv) > 2 else "").split(",") if t.strip()}
KEY = os.environ.get("NASDAQ_DATA_LINK_KEY", "").strip()
ROOT = Path(__file__).parent
SUFFIX = re.compile(r"-\d{6}$")
API = "https://data.nasdaq.com/api/v3/datatables/WIKI/PRICES.json"
out = []
def say(s=""): print(s, flush=True); out.append(str(s))

p = pd.read_parquet(ROOT / "data" / "prices.parquet", columns=["ticker", "date", "close", "dividends", "stock_splits", "source"])
p["date"] = pd.to_datetime(p["date"])
iv = pd.read_parquet(ROOT / "data" / "universe" / "membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv["start"]); iv["end"] = pd.to_datetime(iv["end"])
y0, y1 = pd.Timestamp(f"{Y0}-01-01"), pd.Timestamp(f"{Y1}-12-31")
mem = iv[(iv.start <= y1) & (iv["end"].isna() | (iv["end"] > y0))]
labels = sorted(set(mem.ticker) & set(p.ticker))
if ONLY:
    labels = [t for t in labels if t in ONLY]
ren = pd.read_csv(ROOT / "data" / "universe" / "renames.csv") if (ROOT / "data" / "universe" / "renames.csv").exists() else pd.DataFrame(columns=["old", "new"])
olds = {}
for o, n in ren.values:
    olds.setdefault(n, []).append(o)
spins = set()
if (ROOT / "spinoffs.csv").exists():
    s = pd.read_csv(ROOT / "spinoffs.csv", parse_dates=["ex_date"]); spins = set(zip(s.ticker, s.ex_date))
say(f"WIKI dividend audit {RANGE}: {len(labels)} member labels with prices")


def wiki_rows(symbols, a, b):
    """All WIKI rows for these symbols in [a, b], following the cursor."""
    rows, cols, cursor = [], None, None
    for _ in range(50):
        u = (f"{API}?ticker={urllib.parse.quote(','.join(symbols), safe=',')}&date.gte={a}&date.lte={b}"
             f"&qopts.columns=ticker,date,close,ex-dividend,split_ratio&api_key={KEY}")
        if cursor:
            u += f"&qopts.cursor_id={urllib.parse.quote(str(cursor), safe='')}"
        for attempt in range(4):
            try:
                d = json.load(urllib.request.urlopen(u, timeout=90)); break
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(20); continue
                raise
        cols = [c["name"] for c in d["datatable"]["columns"]]
        rows += d["datatable"]["data"]
        cursor = (d.get("meta") or {}).get("next_cursor_id")
        if not cursor:
            break
        time.sleep(0.3)
    df = pd.DataFrame(rows, columns=cols or ["ticker", "date", "close", "ex-dividend", "split_ratio"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def candidates(t):
    bare = SUFFIX.sub("", t)
    c = [bare] + olds.get(t, []) + olds.get(bare, [])
    return list(dict.fromkeys(x.replace("-", "_").replace(".", "_") for x in c))


a = (y0 - pd.Timedelta(days=20)).date(); b = min(y1, pd.Timestamp("2018-04-15")).date()
want = {t: candidates(t) for t in labels}
syms = sorted({s for v in want.values() for s in v})
wiki = []
for i in range(0, len(syms), 25):
    try:
        wiki.append(wiki_rows(syms[i:i + 25], a, b))
    except Exception as e:
        say(f"  WIKI query failed for {syms[i:i + 25][:3]}...: {type(e).__name__} {str(e)[:100]}")
    time.sleep(0.5)
W = pd.concat(wiki, ignore_index=True) if wiki else pd.DataFrame(columns=["ticker", "date", "close", "ex-dividend", "split_ratio"])
say(f"WIKI rows: {len(W):,} for {W.ticker.nunique()} of {len(syms)} symbols, {a}..{b}")
byw = {k: g.sort_values("date").set_index("date") for k, g in W.groupby("ticker")}

rows, overrides, nomatch, used = [], [], [], {}
for t in labels:
    o = p[p.ticker == t].sort_values("date").reset_index(drop=True)
    src = o.source.mode().iloc[0]
    sp = o.stock_splits.fillna(0).replace(0, 1.0).astype(float).to_numpy()
    later = np.append(np.cumprod(sp[::-1])[::-1][1:], 1.0)
    scale = pd.Series(later if src == "yahoo" else np.ones(len(o)), index=o.date)
    scale = scale[~scale.index.duplicated()]
    win = [(max(s0, y0), min(e0, y1 + pd.Timedelta(days=1)) if pd.notna(e0) else y1 + pd.Timedelta(days=1))
           for s0, e0 in zip(mem[mem.ticker == t].start, mem[mem.ticker == t]["end"])]
    win = [(s0, min(e0, pd.Timestamp("2018-03-27"))) for s0, e0 in win if s0 < pd.Timestamp("2018-03-27")]
    if not win:
        continue
    inwin = lambda d: any(s0 <= d < e0 for s0, e0 in win)
    # pick the WIKI symbol that is this security
    best = None
    oc = o.set_index("date").close
    for sym in want[t]:
        g = byw.get(sym)
        if g is None or len(g) < 20:
            continue
        j = pd.concat([oc.pct_change().rename("o"), g.close.pct_change().rename("w")], axis=1, join="inner").dropna()
        j = j[[inwin(d) for d in j.index]]
        if len(j) < 20:
            continue
        agree = float(((j.o - j.w).abs() < 0.005).mean())
        if best is None or agree > best[1]:
            best = (sym, agree, len(j))
    if best is None or best[1] < 0.95:
        nomatch.append(f"{t}({best[0]} {best[1]:.0%})" if best else t); continue
    used[t] = best[0]
    g = byw[best[0]]
    wd = g[g["ex-dividend"].fillna(0) > 0]["ex-dividend"]
    ours = o[o.dividends.fillna(0) > 0].set_index("date").dividends
    tol = lambda x: max(0.0005, 0.005 * x)
    for d, amt in wd.items():
        if not inwin(d) or (t, d) in spins:
            continue
        if d in ours.index:
            mine = float(ours[d]) * float(scale.get(d, 1.0))
            if abs(mine - amt) <= tol(amt):
                continue
            rows.append((t, str(d.date()), "amount differs", round(float(amt), 6), round(mine, 6)))
            overrides.append((t, str(d.date()), "", round(float(amt) / float(scale.get(d, 1.0)), 6), f"WIKI ex-dividend {amt:g} vs ours {mine:g} as paid (audit_dividends_wiki.py)"))
            continue
        near = [x for x in ours.index if abs((x - d).days) <= 5 and abs(float(ours[x]) * float(scale.get(x, 1.0)) - amt) <= tol(amt)]
        if near:
            rows.append((t, str(d.date()), f"date differs: ours on {near[0].date()}", round(float(amt), 6), round(float(ours[near[0]]) * float(scale.get(near[0], 1.0)), 6)))
        else:
            rows.append((t, str(d.date()), "MISSING on our side", round(float(amt), 6), ""))
            if d in scale.index:
                overrides.append((t, str(d.date()), "", round(float(amt) / float(scale[d]), 6), f"missing in our source; WIKI ex-dividend {amt:g} (audit_dividends_wiki.py)"))
    for d, v in ours.items():
        if not inwin(d) or d in wd.index or (t, d) in spins:
            continue
        mine = float(v) * float(scale.get(d, 1.0))
        if any(abs((x - d).days) <= 5 and abs(float(wd[x]) - mine) <= tol(mine) for x in wd.index):
            continue
        rows.append((t, str(d.date()), "EXTRA on our side" + ("" if d in g.index else " (no WIKI bar that day)"), "", round(mine, 6)))

say(f"\nRESULT {RANGE}: {len(used)} labels compared with WIKI; {len(rows)} dividend differences")
say("  by kind: " + str(dict(Counter(r[2].split(":")[0] for r in rows))))
say(f"  no matching WIKI series ({len(nomatch)}): " + ", ".join(nomatch))
say("  symbol used where it is not the label: " + ", ".join(f"{k}<-{v}" for k, v in used.items() if v != SUFFIX.sub('', k).replace('-', '_')))
say("\n(label, ex-date, finding, WIKI amount, our amount as paid)")
for r in sorted(rows):
    say(f"  {r}")
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data" / f"dividend_audit_wiki_{RANGE}.txt").write_text("\n".join(out))
pd.DataFrame(overrides, columns=["ticker", "date", "split_factor", "dividend", "note"]).to_csv(
    ROOT / "data" / f"dividend_audit_wiki_{RANGE}_overrides.csv", index=False)
