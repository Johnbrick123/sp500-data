"""Probe: print the daily rows around corporate events from every free source we
have, side by side, to hand-verify the closes a spinoffs.csv or price_patches.csv
row needs. Read-only.

Usage: python probe_events.py "SYM:START:END SYM:START:END ..." [TIINGO,SYMS] [WIKI,SYMS]
  Nasdaq historical rows for every SYM (Nasdaq's history is split-adjusted and,
  for some spin-offs, spin-adjusted - fine for a spun-off company's own first
  days, not for a parent's raw close).
  Tiingo (raw closes, divCash, splitFactor) only for the symbols in the second
  argument - one request each, so it stays inside the hourly allowance.
  Kaggle archives unpacked under kag_<name>/ (close = split-adjusted close as
  the archive's source kept it, and the adjusted close when there is one).
-> data/events_probe.txt
"""
import glob, json, os, re, sys, time, urllib.request
import pandas as pd

specs = [s.split(":") for s in sys.argv[1].split() if s.count(":") >= 2]
TI = {s.strip().upper() for s in (sys.argv[2] if len(sys.argv) > 2 else "").split(",") if s.strip()}
KEY = os.environ.get("TIINGO_API_KEY", "").strip()
H = {"User-Agent": "Mozilla/5.0 (data verification)"}
WIKI = {s.strip().upper() for s in (sys.argv[3] if len(sys.argv) > 3 else "").split(",") if s.strip()}
DLKEY = os.environ.get("NASDAQ_DATA_LINK_KEY", "").strip()
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
pd.set_option("display.width", 200)


NQ = {}
def nasdaq(sym, a, b):
    """Same request as verify.py's nightly check (full 10-year window), filtered here."""
    errs = []
    for v in dict.fromkeys([sym, sym.replace("-", "."), sym.replace("-", "/")]):
        if v not in NQ:
            u = (f"https://api.nasdaq.com/api/quote/{v}/historical?assetclass=stocks"
                 f"&fromdate=2016-09-01&todate={pd.Timestamp.today():%Y-%m-%d}&limit=9999")
            try:
                d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30))
                NQ[v] = pd.DataFrame(((d.get("data") or {}).get("tradesTable") or {}).get("rows") or [])
            except Exception as e:
                NQ[v] = None; errs.append(f"{v}: {type(e).__name__} {str(e)[:60]}")
            time.sleep(0.7)
        df = NQ[v]
        if df is not None and len(df):
            dd = pd.to_datetime(df["date"], errors="coerce")
            g = df[(dd >= pd.Timestamp(a)) & (dd <= pd.Timestamp(b))]
            return v, (g.iloc[::-1].to_string(index=False) if len(g) else f"{len(df)} rows, none in the window")
    return None, "no rows " + "; ".join(errs)


def wiki(sym, a, b):
    u = (f"https://data.nasdaq.com/api/v3/datatables/WIKI/PRICES.json?ticker={sym}&date.gte={a}&date.lte={b}"
         f"&qopts.columns=ticker,date,open,high,low,close,volume,ex-dividend,split_ratio&api_key={DLKEY}")
    d = json.load(urllib.request.urlopen(u, timeout=60))
    rows = d["datatable"]["data"]
    if not rows:
        return "no rows"
    return pd.DataFrame(rows, columns=[c["name"] for c in d["datatable"]["columns"]]).to_string(index=False)


def tiingo(sym, a, b):
    u = f"https://api.tiingo.com/tiingo/daily/{sym}/prices?startDate={a}&endDate={b}&format=json&token={KEY}"
    body = urllib.request.urlopen(u, timeout=60).read().decode()
    try:
        js = json.loads(body)
    except json.JSONDecodeError:
        return "not json (throttled?)"
    if isinstance(js, dict):
        return f"Tiingo answered with a message, not rows: {str(js)[:300]}"
    if not js:
        return "no rows"
    df = pd.DataFrame(js); df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[["date", "open", "high", "low", "close", "volume", "divCash", "splitFactor"]].to_string(index=False)


# archives: load only the rows we need
need = {}
for s in specs:
    need.setdefault(s[0].upper().replace(".", "-"), []).append((pd.Timestamp(s[1]), pd.Timestamp(s[2])))
arch = {}
for root in sorted(glob.glob("kag_*")):
    name = root[4:]
    files = [f for f in glob.glob(f"{root}/**/*", recursive=True) if os.path.isfile(f) and f.lower().endswith((".csv", ".txt"))]
    frames = []
    for f in files:
        base = re.sub(r"\.(csv|txt)$", "", os.path.basename(f), flags=re.I)
        base = re.sub(r"\.us$", "", base, flags=re.I).upper().replace(".", "-").replace("_", "-")
        try:
            head = pd.read_csv(f, nrows=3)
        except Exception:
            continue
        cols = {c.lower().strip(): c for c in head.columns}
        dcol = next((cols[c] for c in ("date", "timestamp") if c in cols), None)
        scol = next((cols[c] for c in ("symbol", "ticker") if c in cols), None)
        if not dcol:
            continue
        if scol:
            for ch in pd.read_csv(f, chunksize=2_000_000):
                ch[scol] = ch[scol].astype(str).str.upper().str.replace(".", "-", regex=False).str.replace("_", "-", regex=False)
                ch = ch[ch[scol].isin(need)]
                if len(ch):
                    frames.append(ch.rename(columns={scol: "symbol", dcol: "date"}))
        elif base in need:
            frames.append(pd.read_csv(f).rename(columns={dcol: "date"}).assign(symbol=base, file=os.path.relpath(f, root)))
    if frames:
        a = pd.concat(frames, ignore_index=True)
        a["date"] = pd.to_datetime(a["date"], errors="coerce", utc=True).dt.tz_localize(None)
        arch[name] = a
    say(f"archive {name}: {len(files)} files, {sum(len(x) for x in frames):,} rows for the requested symbols")

for s in specs:
    sym, a, b = s[0].upper(), s[1], s[2]
    say(f"\n================ {sym} {a}..{b}")
    v, txt = nasdaq(sym, a, b)
    say(f"-- NASDAQ ({v}):\n{txt}")
    if sym in TI:
        try:
            say(f"-- TIINGO:\n{tiingo(sym, a, b)}")
        except Exception as e:
            say(f"-- TIINGO error {type(e).__name__} {str(e)[:100]}")
        time.sleep(1)
    wsym = sym.replace("-", "_")
    if wsym in WIKI or sym in WIKI:
        try:
            say(f"-- WIKI ({wsym}):\n{wiki(wsym, a, b)}")
        except Exception as e:
            say(f"-- WIKI error {type(e).__name__} {str(e)[:100]}")
    for name, df in arch.items():
        g = df[(df.symbol == sym.replace(".", "-")) & (df.date >= pd.Timestamp(a)) & (df.date <= pd.Timestamp(b))]
        say(f"-- ARCHIVE {name}:\n" + (g.drop(columns=["symbol"]).sort_values("date").to_string(index=False) if len(g) else "no rows"))
os.makedirs("data", exist_ok=True)
open("data/events_probe.txt", "w").write("\n".join(out))
