"""Every member's dividends in a range of years against Nasdaq's dividend history (an independent
record: ex-date, cash amount as declared). For 2025 onward there is no CRSP to check against, so
this is the dividend check for those years.

For each label that was an index member on any day of the range, Nasdaq's rows
(api.nasdaq.com/api/quote/SYM/dividends) are read and compared with ours by ex-date, on the member's
own days. Ours are put back on the as-paid basis first: a Yahoo series stores each dividend divided
by every later split (Tiingo/WIKI/CRSP store them raw). Reported:
  missing   - Nasdaq has a cash dividend ours lacks (within +-3 trading days: a date shift is listed apart)
  extra     - ours has one Nasdaq lacks
  amount    - both have it, amounts differ by more than 0.5% (and a tenth of a cent)
  shifted   - same amount, ex-dates 1-3 days apart
  no data   - Nasdaq returned nothing for the symbol (coverage is reported, never counted as a pass)
Usage: python tools/nasdaq_div_audit.py prices.parquet membership_intervals.parquet FIRST_YEAR LAST_YEAR OUTDIR
Read-only. Added 2026-10-16 (the accuracy pass, second round)."""
import json, os, re, sys, time, urllib.request
import numpy as np, pandas as pd

pf, mf, y0, y1, out = sys.argv[1:6]
y0, y1 = int(y0), int(y1)
os.makedirs(out, exist_ok=True)
A, B = pd.Timestamp(f"{y0}-01-01"), pd.Timestamp(f"{y1}-12-31")
m = pd.read_parquet(mf)
m["start"] = pd.to_datetime(m.start); m["end"] = pd.to_datetime(m["end"]).fillna(pd.Timestamp("2100-01-01"))
m = m[(m.start <= B) & (m.end > A)]
labs = sorted(set(m.ticker))
p = pd.read_parquet(pf, columns=["ticker", "date", "close", "dividends", "stock_splits", "source"])
p = p[p.ticker.isin(labs)].copy(); p["date"] = pd.to_datetime(p.date)
p = p.sort_values(["ticker", "date"])
H = {"User-Agent": "Mozilla/5.0 (data verification)", "Accept": "application/json"}


def nasdaq(sym):
    for s in dict.fromkeys([sym, sym.replace("-", "."), sym.replace("-", "/"), sym.replace("-", "")]):
        u = f"https://api.nasdaq.com/api/quote/{s}/dividends?assetclass=stocks"
        for attempt in range(3):
            try:
                d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
                rows = (((d.get("data") or {}).get("dividends") or {}).get("rows")) or []
                if rows:
                    return s, rows
                break
            except Exception:
                time.sleep(3)
        time.sleep(0.4)
    return sym, []


def amt(x):
    try:
        return float(str(x).replace("$", "").replace(",", ""))
    except Exception:
        return np.nan


# MODE=calendar: Nasdaq's market-wide dividend calendar (one request per ex-date, NYSE names
# included) instead of the per-company history, which serves Nasdaq-listed names only
CAL = None
if os.environ.get("MODE", "").strip() == "calendar":
    recs = []
    days = sorted(d for d in p.date.unique() if A <= d <= B)
    for i, d in enumerate(days):
        u = f"https://api.nasdaq.com/api/calendar/dividends?date={pd.Timestamp(d):%Y-%m-%d}"
        for attempt in range(3):
            try:
                js = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
                rows = ((((js.get("data") or {}).get("calendar") or {}).get("rows")) or [])
                for r in rows:
                    recs.append((str(r.get("symbol", "")).upper(), r.get("dividend_Ex_Date"), r.get("dividend_Rate"), "calendar"))
                break
            except Exception:
                time.sleep(3)
        time.sleep(0.5)
        if (i + 1) % 50 == 0:
            print(f"  calendar {i + 1}/{len(days)} days, {len(recs)} rows", flush=True)
    CAL = pd.DataFrame(recs, columns=["sym", "exOrEffDate", "amount", "type"])
    print(f"calendar rows: {len(CAL)}", flush=True)

    def nasdaq(sym):                                   # noqa: F811 - the calendar replaces the per-company call
        keys = {sym, sym.replace("-", "."), sym.replace("-", "/"), sym.replace("-", " ")}
        r = CAL[CAL.sym.isin(keys)]
        return sym, r.drop(columns="sym").to_dict("records")

res, cov = [], []
cal = sorted(p.date.unique())
ci = {d: i for i, d in enumerate(cal)}
for t in labs:
    g = p[p.ticker == t].reset_index(drop=True)
    sp = g.stock_splits.fillna(0).replace(0, 1.0).astype(float).to_numpy()
    later = np.append(np.cumprod(sp[::-1])[::-1][1:], 1.0)          # product of splits after each row
    src = g.source.astype(str).mode().iloc[0] if len(g) else "yahoo"
    raw = g.dividends.fillna(0).to_numpy() * (later if src == "yahoo" else 1.0)
    spans = m[m.ticker == t]
    mem = np.zeros(len(g), bool)
    for r in spans.itertuples():
        mem |= ((g.date >= max(r.start, A)) & (g.date < r.end) & (g.date <= B)).to_numpy()
    ours = pd.DataFrame({"date": g.date[mem & (raw > 0)], "ours": raw[mem & (raw > 0)]})
    sym = re.sub(r"-\d{6}$", "", t)
    used, rows = nasdaq(sym)
    if CAL is None:
        time.sleep(0.6)
    if not rows:
        cov.append((t, sym, "no data", len(ours), 0)); continue
    n = pd.DataFrame(rows)
    n["date"] = pd.to_datetime(n.get("exOrEffDate"), format="%m/%d/%Y", errors="coerce")
    if n["date"].isna().all():
        n["date"] = pd.to_datetime(n.get("exOrEffDate"), errors="coerce")
    n["nasdaq"] = n.get("amount").map(amt)
    n["type"] = n.get("type", "").astype(str)
    memdays = set(g.date[mem])
    n = n[n.date.isin(memdays) & (n.nasdaq > 0)][["date", "nasdaq", "type"]]
    cov.append((t, used, "ok", len(ours), len(n)))
    used_o, used_n = set(), set()
    for r in n.itertuples():                                         # exact date
        o = ours[ours.date == r.date]
        if len(o):
            used_o.add(o.index[0]); used_n.add(r.Index)
            a, b = float(o.ours.iloc[0]), r.nasdaq
            if abs(a - b) > max(0.001, 0.005 * b):
                res.append((t, r.date.date(), a, b, r.type, "amount", round(a / b, 4)))
    for r in n[~n.index.isin(used_n)].itertuples():                  # a date shift of 1-3 trading days
        k = ci.get(r.date)
        near = ours[~ours.index.isin(used_o) & ours.date.map(lambda d: k is not None and abs(ci.get(d, -99) - k) <= 3)]
        if len(near):
            i = near.index[0]; used_o.add(i)
            res.append((t, r.date.date(), float(near.ours.iloc[0]), r.nasdaq, r.type,
                        f"shifted (ours {near.date.iloc[0].date()})", round(float(near.ours.iloc[0]) / r.nasdaq, 4)))
        else:
            res.append((t, r.date.date(), 0.0, r.nasdaq, r.type, "missing", 0.0))
    for r in ours[~ours.index.isin(used_o)].itertuples():
        res.append((t, r.date.date(), r.ours, 0.0, "", "extra", np.nan))
R = pd.DataFrame(res, columns=["label", "ex_date", "ours", "nasdaq", "type", "issue", "ratio"])
C = pd.DataFrame(cov, columns=["label", "symbol", "status", "ours_n", "nasdaq_n"])
R.to_csv(f"{out}/issues.csv", index=False); C.to_csv(f"{out}/coverage.csv", index=False)
ok = C[C.status == "ok"]
pd.set_option("display.width", 220); pd.set_option("display.max_rows", 400)
print(f"{len(labs)} member labels {y0}-{y1}; Nasdaq has dividend rows for {len(ok)}; no data for {len(C) - len(ok)}")
print(f"dividends compared: ours {int(ok.ours_n.sum())}, Nasdaq {int(ok.nasdaq_n.sum())}; issues: {len(R)}")
print(R.issue.str.split(" ").str[0].value_counts().to_string())
print("\nno data:\n" + ", ".join(C[C.status != "ok"].label))
print("\nissues:\n" + R.sort_values(["issue", "label", "ex_date"]).to_string())
