"""Probe: does Yahoo have dividends our nightly pull is missing? For each ticker,
compare the dividends yf.download(actions=True) returns (what fetch_prices.py
stores) with yf.Ticker().dividends and Ticker().history(actions=True).
Usage: python probe_yahoo_divs.py "APTV,EG,ETN,MDT" [FROM] [TO] -> data/yahoo_divs_probe.txt"""
import os, sys, time
import pandas as pd
import yfinance as yf
tks = [t.strip() for t in sys.argv[1].split(",") if t.strip()]
lo = pd.Timestamp(sys.argv[2] if len(sys.argv) > 2 else "2016-01-01"); hi = pd.Timestamp(sys.argv[3] if len(sys.argv) > 3 else "2021-12-31")
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
def clip(s):
    s = s[s != 0].copy(); s.index = pd.to_datetime(s.index).tz_localize(None) if getattr(s.index, "tz", None) is not None else pd.to_datetime(s.index)
    return s[(s.index >= lo) & (s.index <= hi)].round(6)
say(f"yfinance {yf.__version__}")
for t in tks:
    try:
        d1 = yf.download(t, start="1995-01-01", auto_adjust=False, actions=True, progress=False, threads=False)
        a = clip(d1["Dividends"].squeeze()) if "Dividends" in d1 else pd.Series(dtype=float)
    except Exception as e:
        a = pd.Series(dtype=float); say(f"{t}: download error {type(e).__name__} {e}")
    try:
        b = clip(yf.Ticker(t).dividends)
    except Exception as e:
        b = pd.Series(dtype=float); say(f"{t}: Ticker.dividends error {type(e).__name__} {e}")
    try:
        h = yf.Ticker(t).history(period="max", auto_adjust=False, actions=True)
        c = clip(h["Dividends"]) if "Dividends" in h else pd.Series(dtype=float)
    except Exception as e:
        c = pd.Series(dtype=float); say(f"{t}: history error {type(e).__name__} {e}")
    allx = sorted(set(a.index) | set(b.index) | set(c.index))
    say(f"\n== {t}: download {len(a)}, Ticker.dividends {len(b)}, history {len(c)} dividends in {lo.date()}..{hi.date()}")
    for x in allx:
        va, vb, vc = a.get(x), b.get(x), c.get(x)
        flag = "" if (va is not None and vb is not None and vc is not None and abs(va - vb) < 1e-6 and abs(va - vc) < 1e-6) else "   <-- differs"
        say(f"  {x.date()}  download={va}  Ticker.dividends={vb}  history={vc}{flag}")
    time.sleep(1)
os.makedirs("data", exist_ok=True); open("data/yahoo_divs_probe.txt", "w").write("\n".join(out))
