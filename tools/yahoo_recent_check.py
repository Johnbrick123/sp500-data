"""A second source for a few labels in recent years: Yahoo's raw closes and dividends against ours.
For the members no other free source covers in 2025-2026 - companies acquired in those years, whose
rows come from Tiingo (Yahoo is independent of it), and share classes Nasdaq does not serve.
Compared on the label's member-days: daily close-to-close returns (raw closes; a split day skipped)
and every dividend (ex-date and amount as paid).
Usage: python tools/yahoo_recent_check.py prices.parquet membership_intervals.parquet "A,B,C" FIRST LAST OUTFILE
Read-only. Added 2026-10-16 (accuracy pass, second round)."""
import sys, time
import numpy as np, pandas as pd
import yfinance as yf

pf, mf, labs, y0, y1, outf = sys.argv[1:7]
labs = [x.strip() for x in labs.split(",") if x.strip()]
A, B = pd.Timestamp(f"{y0}-01-01"), pd.Timestamp(f"{y1}-12-31")
m = pd.read_parquet(mf); m["start"] = pd.to_datetime(m.start); m["end"] = pd.to_datetime(m["end"]).fillna(pd.Timestamp("2100-01-01"))
p = pd.read_parquet(pf, columns=["ticker", "date", "close", "dividends", "stock_splits", "source"])
p = p[p.ticker.isin(labs)].copy(); p["date"] = pd.to_datetime(p.date)
out = []
def say(s=""): print(s, flush=True); out.append(str(s))
for t in labs:
    g = p[p.ticker == t].sort_values("date").set_index("date")
    sp_ = m[m.ticker == t]
    mem = [d for d in g.index if A <= d <= B and ((sp_.start <= d) & (sp_.end > d)).any()]
    if not mem:
        say(f"{t}: no member-days in {y0}-{y1}"); continue
    sym = t.replace(".", "-")
    try:
        h = yf.Ticker(sym).history(start=str((A - pd.Timedelta(days=10)).date()), end=str((B + pd.Timedelta(days=1)).date()),
                                   auto_adjust=False, actions=True)
    except Exception as e:
        say(f"{t}: Yahoo error {type(e).__name__}"); continue
    time.sleep(1)
    if h is None or h.empty:
        say(f"{t}: Yahoo has no rows ({g.source.iloc[-1]} series, {len(mem)} member-days unchecked)"); continue
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    j = pd.DataFrame({"o": g.close, "y": h["Close"]}).dropna()
    j["ro"], j["ry"] = j.o.pct_change(), j.y.pct_change()
    sp = g.stock_splits.fillna(0).replace(0, 1).reindex(j.index).fillna(1)
    jj = j[j.index.isin(mem) & (sp == 1)].dropna()
    bad = jj[(jj.ro - jj.ry).abs() > 0.005]
    # dividends as paid: ours on a Yahoo basis is divided by later splits; Tiingo's are raw
    spl = g.stock_splits.fillna(0).replace(0, 1).astype(float)
    later = spl[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    od = (g.dividends * (later if str(g.source.mode().iloc[0]) == "yahoo" else 1.0))
    od = od[(od > 0) & od.index.isin(mem)]
    yd = h["Dividends"][(h["Dividends"] > 0) & h.index.isin(mem)] if "Dividends" in h else pd.Series(dtype=float)
    dd = pd.DataFrame({"ours": od, "yahoo": yd}).fillna(0)
    dbad = dd[(dd.ours - dd.yahoo).abs() > np.maximum(0.001, 0.005 * dd.max(axis=1))]
    say(f"{t}: {len(jj)} member-day returns vs Yahoo, {len(bad)} differ >0.5pp; dividends ours {len(od)} / Yahoo {len(yd)}, {len(dbad)} differ"
        f"  (Yahoo rows {h.index.min().date()}..{h.index.max().date()}; our source {g.source.iloc[-1]})")
    for d, r in bad.iterrows():
        say(f"    price {d.date()}: ours {r.o:.4f} ({r.ro * 100:+.2f}%), Yahoo {r.y:.4f} ({r.ry * 100:+.2f}%)")
    for d, r in dbad.iterrows():
        say(f"    dividend {d.date()}: ours {r.ours:.4f}, Yahoo {r.yahoo:.4f}")
open(outf, "w").write("\n".join(out))
