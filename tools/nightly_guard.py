"""Nightly guard: checks tonight's build before it is published.

Runs after compute_adjusted.py and verify.py. Five checks; each finding is FAIL (do not publish),
WARN (publish, but tell John) or NOTE (logged only).

1. Recent days against Nasdaq: every current member's price return on the last GUARD_WINDOW
   trading days (default 5) against Nasdaq's own history. A day off by more than 0.5pp with no
   booked event (split, dividend, spin-off row, override or patch) is a FAIL: a corporate event the
   vendor did not record, or a bad close (Skydance's 2026-10-14 warrant distribution read -23.8%;
   Nasdaq's adjusted history showed the event). On a booked event day it is a NOTE. Names Nasdaq does
   not serve are listed; an outage never blocks publishing (WARN if over 15% unavailable).
2. Big moves: any current member moving more than 15% in the window (or more than 8% and six times
   its usual daily range) is listed with Nasdaq's figure. Unbooked and unconfirmed by Nasdaq: WARN.
3. History against last night's published data (data/prev/prices.parquet): every member-day
   return older than 7 days that changed by more than 0.05pp, every dividend that changed or
   disappeared, every member-day row that disappeared. Yahoo rewrites old history silently (it has
   deleted dividends we held). WARN; a mass change on a scheduled run (over 1,000 member-day returns
   across 25+ names) is a FAIL - intended global fixes are published by a manual run.
4. New dividends: any dividend in the window above 5% of the prior close (a special or a vendor
   error) is a WARN; Nasdaq's dividend calendar for each window day is checked against ours for the
   names Nasdaq lists (missing or a different amount: WARN).
5. Index changes in the last 10 days: an added member without a row on each day since its add is
   a FAIL; a removed member whose last member day was not a trade is a WARN (see exit_gaps.csv).
   Every current member must have a row on the latest day (WARN).

Writes data/nightly_guard.txt (everything) and data/nightly_alert.txt (FAIL/WARN lines only, or
"ALL CLEAR"). Exit 1 on any FAIL.
Env: GUARD_WINDOW, GUARD_ASOF (YYYY-MM-DD), GUARD_STRICT (1 on scheduled runs),
     GUARD_IGNORE_EVENTS ("LABEL:YYYY-MM-DD,..." - treat as unbooked; for tests), GUARD_SKIP_NASDAQ.
Added 2026-10-16."""
import json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ["GUARD_DATA"]) if os.environ.get("GUARD_DATA") else ROOT / "data"
WIN = int(os.environ.get("GUARD_WINDOW", "5"))
STRICT = os.environ.get("GUARD_STRICT", "").strip() in ("1", "true", "yes")
IGNORE = {tuple(x.split(":")) for x in os.environ.get("GUARD_IGNORE_EVENTS", "").split(",") if ":" in x}
H = {"User-Agent": "Mozilla/5.0 (data verification)", "Accept": "application/json"}
out, alerts = [], []
fails = 0


def say(s=""):
    print(s, flush=True); out.append(str(s))


def flag(level, s):
    global fails
    line = f"{level}: {s}"
    say("  " + line)
    if level in ("FAIL", "WARN"):
        alerts.append(line)
    if level == "FAIL":
        fails += 1


# ---- data
p = pd.read_parquet(DATA / "prices.parquet", columns=["ticker", "date", "close", "adj_close", "volume", "dividends", "stock_splits", "source"])
p["date"] = pd.to_datetime(p.date)
p = p.sort_values(["ticker", "date"])
p["r"] = p.groupby("ticker").adj_close.pct_change()
p["prev_close"] = p.groupby("ticker").close.shift()
iv = pd.read_parquet(DATA / "universe" / "membership_intervals.parquet")
iv["start"] = pd.to_datetime(iv.start); iv["end"] = pd.to_datetime(iv["end"])
per_day = p.groupby("date").ticker.nunique()
broad = per_day[per_day >= 0.5 * per_day.max()]
asof = pd.Timestamp(os.environ.get("GUARD_ASOF")) if os.environ.get("GUARD_ASOF") else broad.index.max()
cal = sorted(d for d in broad.index if d <= asof)
window = cal[-WIN:]
w0 = window[0]
current = sorted(iv[(iv.start <= asof) & (iv["end"].isna() | (iv["end"] > asof))].ticker)
say(f"NIGHTLY GUARD - as of {asof.date()}; window {w0.date()}..{asof.date()} ({len(window)} trading days); "
    f"{len(current)} current members; strict={STRICT}")

# booked events: the days a difference from Nasdaq is expected
booked = set()
def _add(df, tcol, dcol, nextday=False):
    for t, d in zip(df[tcol], pd.to_datetime(df[dcol], errors="coerce")):
        if pd.notna(d):
            booked.add((t, d))
            if nextday:
                booked.add((t, d + pd.Timedelta(days=1)))
for f, tcol, dcol, nd in (("spinoffs.csv", "ticker", "ex_date", False), ("corporate_action_overrides.csv", "ticker", "date", False),
                          ("price_patches.csv", "ticker", "date", True)):
    if (ROOT / f).exists():
        _add(pd.read_csv(ROOT / f, usecols=[tcol, dcol]), tcol, dcol, nd)
ev = p[(p.date >= w0) & (p.date <= asof) & ((p.stock_splits.fillna(0).replace(0, 1) != 1) | (p.dividends.fillna(0) > 0))]
booked |= set(zip(ev.ticker, ev.date))
booked -= {(t, pd.Timestamp(d)) for t, d in IGNORE}

W = p[(p.date >= w0) & (p.date <= asof) & p.ticker.isin(current)].copy()
W["price_r"] = W.r - W.dividends.fillna(0) / W.prev_close          # ours, dividend taken out: comparable with Nasdaq's closes

# ---- 1. recent days against Nasdaq
say("\n[1] RECENT DAYS AGAINST NASDAQ")


def nasdaq(tk):
    frm = (w0 - pd.Timedelta(days=12)).strftime("%Y-%m-%d")
    for sym in dict.fromkeys([tk, tk.replace("-", "."), tk.replace("-", "/")]):
        for ac in ("stocks", "etf"):
            u = (f"https://api.nasdaq.com/api/quote/{sym}/historical?assetclass={ac}"
                 f"&fromdate={frm}&todate={(asof + pd.Timedelta(days=1)):%Y-%m-%d}&limit=60")
            for attempt in range(2):
                try:
                    d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=20))
                    rows = ((((d or {}).get("data") or {}).get("tradesTable") or {}).get("rows")) or []
                    if len(rows) >= 3:
                        n = pd.DataFrame(rows)
                        n["date"] = pd.to_datetime(n["date"], errors="coerce")
                        n["close"] = pd.to_numeric(n["close"].astype(str).str.replace(r"[$,]", "", regex=True), errors="coerce")
                        n = n.dropna(subset=["date", "close"]).sort_values("date")
                        n["nr"] = n.close.pct_change()
                        return tk, n.set_index("date").nr
                    break
                except Exception:
                    time.sleep(2)
    return tk, None


NQ = {}
if not os.environ.get("GUARD_SKIP_NASDAQ"):
    with ThreadPoolExecutor(max_workers=4) as ex:
        for tk, s in ex.map(nasdaq, current):
            NQ[tk] = s
    unavailable = [t for t in current if NQ.get(t) is None]
    say(f"  Nasdaq served {len(current) - len(unavailable)} of {len(current)} current members"
        + (f"; not served: {', '.join(unavailable[:40])}{' ...' if len(unavailable) > 40 else ''}" if unavailable else ""))
    if len(unavailable) > 0.15 * len(current):
        flag("WARN", f"Nasdaq did not serve {len(unavailable)} of {len(current)} members tonight (an outage?) - the recent-day check is incomplete")
    n_cmp = n_off = 0
    for r in W.dropna(subset=["price_r"]).itertuples():
        s = NQ.get(r.ticker)
        if s is None or r.date not in s.index or pd.isna(s.get(r.date)):
            continue
        nr = float(s.get(r.date)); n_cmp += 1
        d = (r.price_r - nr) * 100
        if abs(d) > 0.5:
            n_off += 1
            msg = (f"{r.ticker} {r.date.date()}: ours {r.price_r * 100:+.2f}% (price), Nasdaq {nr * 100:+.2f}% - "
                   f"{d:+.2f}pp; our close {r.close:.4f} (source {r.source})")
            if (r.ticker, r.date) in booked:
                flag("NOTE", msg + " - a booked event day (split/dividend/spin/override/patch): expected")
            else:
                flag("FAIL", msg + " - NO booked event: a corporate action the vendor missed (spin-off, warrants, special) or a bad close. "
                     "Check the company's news; book it in spinoffs.csv / corporate_action_overrides.csv or patch the close")
    say(f"  member-day returns compared: {n_cmp:,}; over 0.5pp: {n_off}")
else:
    say("  skipped (GUARD_SKIP_NASDAQ)")

# ---- 2. big moves
say("\n[2] BIG MOVES IN THE WINDOW")
hist = p[p.ticker.isin(current) & (p.date < w0) & (p.date >= w0 - pd.Timedelta(days=120))]
vol = hist.groupby("ticker").r.std()
big = W[(W.r.abs() > 0.15) | ((W.r.abs() > 0.08) & (W.r.abs() > 6 * W.ticker.map(vol).fillna(1)))]
for r in big.itertuples():
    s = NQ.get(r.ticker)
    nr = float(s.get(r.date)) if s is not None and r.date in s.index and pd.notna(s.get(r.date)) else None
    txt = f"{r.ticker} {r.date.date()}: {r.r * 100:+.1f}% total return" + (f"; Nasdaq {nr * 100:+.1f}% price" if nr is not None else "; Nasdaq: no figure")
    if (r.ticker, r.date) in booked:
        flag("NOTE", txt + " - booked event")
    elif nr is not None and abs(r.price_r - nr) <= 0.005:
        flag("NOTE", txt + " - Nasdaq agrees: a real move (news); no corporate action expected")
    elif nr is None:
        flag("WARN", txt + " - no booked event and no second source tonight: confirm it is news, not a corporate action")
    # a Nasdaq disagreement is already a FAIL in [1]
if big.empty:
    say("  none")

# ---- 3. history against last night's published data
say("\n[3] HISTORY AGAINST LAST NIGHT'S PUBLISHED DATA")
prevf = DATA / "prev" / "prices.parquet"
if prevf.exists():
    q = pd.read_parquet(prevf, columns=["ticker", "date", "close", "adj_close", "dividends"])
    q = q[q.ticker.isin(set(iv.ticker))]
    q["date"] = pd.to_datetime(q.date)
    q = q.sort_values(["ticker", "date"]); q["r"] = q.groupby("ticker").adj_close.pct_change()
    # a dividend is compared as a yield (amount / that day's close): a vendor re-basing its history
    # for a new split changes every past amount but no yield
    q["yld"] = q.dividends.fillna(0) / q.close
    pp_ = p[p.ticker.isin(set(iv.ticker))][["ticker", "date", "r", "dividends", "close"]].copy()
    pp_["yld"] = pp_.dividends.fillna(0) / pp_.close
    cut = asof - pd.Timedelta(days=7)
    mem = iv.copy(); mem["end"] = mem["end"].fillna(pd.Timestamp("2100-01-01"))
    j = pp_[pp_.date < cut][["ticker", "date", "r", "dividends", "yld"]].merge(q[q.date < cut][["ticker", "date", "r", "dividends", "yld"]],
                                                                    on=["ticker", "date"], how="outer", suffixes=("", "_prev"), indicator=True)
    # restrict to member-days (the backtest-relevant rows)
    j = j.merge(mem[["ticker", "start", "end"]], on="ticker")
    j = j[(j.date >= j.start) & (j.date < j.end)].drop(columns=["start", "end"]).drop_duplicates(["ticker", "date"])
    gone = j[j._merge == "right_only"]
    new = j[j._merge == "left_only"]
    both = j[j._merge == "both"]
    chg = both[(both.r - both.r_prev).abs() > 0.0005]
    dchg = both[(both.yld - both.yld_prev).abs() > 0.00002]
    say(f"  member-days compared {len(both):,}; returns changed >0.05pp: {len(chg):,} on {chg.ticker.nunique()} names; "
        f"dividends changed: {len(dchg)}; rows gone: {len(gone)}; rows added: {len(new)}")
    dchg = dchg.assign(dividends=dchg.dividends.fillna(0), dividends_prev=dchg.dividends_prev.fillna(0))
    for r in dchg.head(30).itertuples():
        flag("WARN", f"dividend changed: {r.ticker} {r.date.date()} {r.dividends_prev:.4f} -> {r.dividends:.4f}"
             + (" (DELETED by the vendor - restore it with an override if it was real)" if r.dividends == 0 else ""))
    if len(dchg) > 30:
        flag("WARN", f"... and {len(dchg) - 30} more dividend changes (nightly_guard.txt)")
        for r in dchg.iloc[30:].itertuples():
            say(f"    {r.ticker} {r.date.date()} {r.dividends_prev:.4f} -> {r.dividends:.4f}")
    if len(gone):
        flag("WARN", f"{len(gone)} member-day rows present last night are gone: " + ", ".join(f"{t} {d.date()}" for t, d in zip(gone.ticker[:15], gone.date[:15])))
    if len(chg):
        top = chg.assign(d=(chg.r - chg.r_prev) * 100).reindex((chg.r - chg.r_prev).abs().sort_values(ascending=False).index)
        by = chg.groupby("ticker").size().sort_values(ascending=False)
        msg = f"{len(chg)} past member-day returns changed on {len(by)} names (most: {', '.join(f'{t} {n}' for t, n in by.head(8).items())})"
        mass = len(chg) > 1000 and len(by) >= 25
        flag("FAIL" if (mass and STRICT) else "WARN", msg + (" - a mass restatement on a scheduled run: not published" if (mass and STRICT) else ""))
        for r in top.head(25).itertuples():
            say(f"    {r.ticker} {r.date.date()}: {r.r_prev * 100:+.3f}% -> {r.r * 100:+.3f}% ({r.d:+.3f}pp)")
    del q, pp_, j, both, chg, gone, new
else:
    say("  no previous dataset on hand - skipped")

# ---- 4. new dividends
say("\n[4] NEW DIVIDENDS")
nd = W[W.dividends.fillna(0) > 0]
say(f"  dividends in the window: {len(nd)}")
for r in nd.itertuples():
    y = r.dividends / r.prev_close if r.prev_close else np.nan
    if pd.notna(y) and y > 0.05:
        flag("WARN", f"{r.ticker} {r.date.date()}: dividend {r.dividends:.4f} = {y * 100:.1f}% of the prior close - a special, a spin-off "
             "booked as cash, or a vendor error; confirm against the company's release")
if not os.environ.get("GUARD_SKIP_NASDAQ"):
    rows = []
    for d in window:
        try:
            js = json.load(urllib.request.urlopen(urllib.request.Request(
                f"https://api.nasdaq.com/api/calendar/dividends?date={d:%Y-%m-%d}", headers=H), timeout=20))
            for r in ((((js.get("data") or {}).get("calendar") or {}).get("rows")) or []):
                rows.append((str(r.get("symbol", "")).upper().replace(".", "-").replace("/", "-"), d,
                             pd.to_numeric(str(r.get("dividend_Rate", "")).replace("$", ""), errors="coerce")))
        except Exception:
            pass
        time.sleep(0.5)
    C = pd.DataFrame(rows, columns=["ticker", "date", "amt"])
    C = C[C.ticker.isin(current)]
    say(f"  Nasdaq's calendar lists {len(C)} member dividends in the window (its own listings only)")
    ours = {(t, d): v for t, d, v in zip(nd.ticker, nd.date, nd.dividends)}
    # ours is on the vendor's basis; with no split in the window that is the as-paid amount
    for r in C.itertuples():
        o = ours.get((r.ticker, r.date))
        if o is None:
            near = [k for k in ours if k[0] == r.ticker and abs((k[1] - r.date).days) <= 3]
            if near:
                flag("WARN", f"{r.ticker}: Nasdaq ex-date {r.date.date()}, ours {near[0][1].date()} - a dividend on a different day")
            else:
                flag("WARN", f"{r.ticker} {r.date.date()}: Nasdaq has a {r.amt} dividend that ours lacks")
        elif pd.notna(r.amt) and abs(o - r.amt) > max(0.001, 0.01 * r.amt):
            flag("WARN", f"{r.ticker} {r.date.date()}: dividend ours {o:.4f}, Nasdaq {r.amt}")

# ---- 5. index changes
say("\n[5] INDEX CHANGES (last 10 days) AND TODAY'S COVERAGE")
recent = asof - pd.Timedelta(days=10)
adds = iv[(iv.start > recent) & (iv.start <= asof)]
rems = iv[iv["end"].notna() & (iv["end"] > recent) & (iv["end"] <= asof + pd.Timedelta(days=1))]
_r = p[p.date >= recent - pd.Timedelta(days=5)]
have = set(zip(_r.ticker, _r.date))
for r in adds.itertuples():
    days = [d for d in cal if d >= r.start]
    miss = [d for d in days if (r.ticker, d) not in have]
    flag("FAIL" if miss else "NOTE", f"added {r.ticker} from {r.start.date()}: " + (f"no rows on {', '.join(str(d.date()) for d in miss)}" if miss else "priced every day"))
for r in rems.itertuples():
    g = p[(p.ticker == r.ticker) & (p.date < r.end)]
    if g.empty:
        flag("WARN", f"removed {r.ticker} (end {r.end.date()}): no rows at all"); continue
    last = g.iloc[-1]
    traded = (last.volume or 0) > 0 and not (len(g) > 1 and last.close == g.close.iloc[-2] and (last.volume or 0) == 0)
    flag("NOTE" if traded else "WARN", f"removed {r.ticker} (end {r.end.date()}): last member day {last.date.date()} close {last.close:.4f} volume {last.volume:,.0f}"
         + ("" if traded else " - not a trade: a halt or a deal that closed early; check exit_gaps.csv and what holders received"))
missing_today = [t for t in current if (t, asof) not in have]
if missing_today:
    flag("WARN", f"{len(missing_today)} current members have no row on {asof.date()}: {', '.join(missing_today[:20])}")
else:
    say(f"  every current member has a row on {asof.date()}")

# ---- summary
say(f"\nRESULT: {fails} FAIL, {sum(1 for a in alerts if a.startswith('WARN'))} WARN")
(DATA / "nightly_guard.txt").write_text("\n".join(out))
head = f"Nightly guard {asof.date()}: " + (f"{fails} FAIL - NOT PUBLISHED" if fails else ("warnings - published" if alerts else "ALL CLEAR"))
(DATA / "nightly_alert.txt").write_text("\n".join([head] + alerts))
sys.exit(1 if fails else 0)
