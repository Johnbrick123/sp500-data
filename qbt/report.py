"""
Statistics and the single-page HTML report.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------
def _series_stats(eq: pd.Series, rf: float = 0.0) -> dict:
    eq = eq.dropna()
    if len(eq) < 2:
        return {}
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(252)
    down = r[r < 0].std() * np.sqrt(252)
    ann_mean = r.mean() * 252               # Sharpe/Sortino use the arithmetic mean, the usual convention
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    trough = dd.idxmin()
    peak = eq.loc[:trough].idxmax()
    rec = eq.loc[trough:]
    recovered = rec[rec >= eq.loc[peak]]
    rec_date = recovered.index[0] if len(recovered) else None
    yearly = eq.resample("YE").last().pct_change()
    first_year = eq.resample("YE").last().iloc[0] / eq.iloc[0] - 1
    yearly.iloc[0] = first_year
    monthly = eq.resample("ME").last().pct_change().dropna()
    return dict(
        total_return=total, cagr=cagr, volatility=vol,
        sharpe=(ann_mean - rf) / vol if vol else np.nan,
        sortino=(ann_mean - rf) / down if down else np.nan,
        max_drawdown=mdd, max_dd_peak=str(peak.date()), max_dd_trough=str(trough.date()),
        max_dd_recovery=str(rec_date.date()) if rec_date is not None else "not yet",
        calmar=cagr / abs(mdd) if mdd else np.nan,
        best_year=yearly.max(), worst_year=yearly.min(), best_year_label=int(yearly.idxmax().year),
        worst_year_label=int(yearly.idxmin().year),
        pct_positive_months=(monthly > 0).mean(), worst_month=monthly.min(), best_month=monthly.max(),
        years=years, start=str(eq.index[0].date()), end=str(eq.index[-1].date()),
        _yearly=yearly, _dd=dd,
    )


def compute_stats(res) -> dict:
    s = _series_stats(res.equity)
    st = dict(strategy=s)
    if res.benchmark is not None:
        b = _series_stats(res.benchmark)
        st["benchmark"] = b
        rs, rb = res.equity.pct_change().dropna(), res.benchmark.pct_change().dropna()
        both = pd.concat([rs, rb], axis=1).dropna()
        if len(both) > 10:
            cov = np.cov(both.iloc[:, 0], both.iloc[:, 1])
            beta = cov[0, 1] / cov[1, 1] if cov[1, 1] else np.nan
            alpha = (both.iloc[:, 0].mean() - beta * both.iloc[:, 1].mean()) * 252
            te = (both.iloc[:, 0] - both.iloc[:, 1]).std() * np.sqrt(252)
            st["relative"] = dict(beta=beta, alpha=alpha, correlation=both.corr().iloc[0, 1],
                                  tracking_error=te,
                                  info_ratio=(s["cagr"] - b["cagr"]) / te if te else np.nan,
                                  excess_cagr=s["cagr"] - b["cagr"])
    # trades
    tr = res.trades
    yrs = s.get("years", 1) or 1
    t = dict(n_trades=int(len(tr)), turnover_per_year=np.nan, avg_positions=np.nan, avg_invested=np.nan,
             total_costs=float(tr["cost"].sum()) if len(tr) else 0.0)
    if len(tr):
        t["turnover_per_year"] = tr["value"].sum() / 2 / res.equity.mean() / yrs
        t["costs_pct_per_year"] = t["total_costs"] / res.equity.mean() / yrs
    if res.holdings:
        t["avg_positions"] = float(np.mean([len(h["holdings"]) for h in res.holdings]))
        t["avg_invested"] = float(np.mean([h["invested"] for h in res.holdings]))
    # round-trip trade stats
    rt = round_trips(res)
    if len(rt):
        t.update(round_trips=int(len(rt)), win_rate=float((rt["return"] > 0).mean()),
                 avg_win=float(rt.loc[rt["return"] > 0, "return"].mean()) if (rt["return"] > 0).any() else np.nan,
                 avg_loss=float(rt.loc[rt["return"] <= 0, "return"].mean()) if (rt["return"] <= 0).any() else np.nan,
                 avg_days_held=float(rt["days"].mean()),
                 best_trade=rt.loc[rt["return"].idxmax(), "ticker"] + f" {rt['return'].max():+.1%}",
                 worst_trade=rt.loc[rt["return"].idxmin(), "ticker"] + f" {rt['return'].min():+.1%}")
    st["trading"] = t
    return st


def round_trips(res) -> pd.DataFrame:
    """Pair entries and exits per ticker into completed round trips (FIFO by position)."""
    tr = res.trades
    if not len(tr):
        return pd.DataFrame()
    out, open_pos = [], {}
    for r in tr.sort_values("date").itertuples(index=False):
        if r.side == "BUY":
            o = open_pos.setdefault(r.ticker, dict(entry=r.date, cost=0.0, shares=0.0))
            o["cost"] += r.value + r.cost
            o["shares"] += r.shares
        else:
            o = open_pos.get(r.ticker)
            if not o or o["shares"] <= 0:
                continue
            sold = min(-r.shares if r.side == "DELIST" else abs(r.shares), o["shares"])
            frac = sold / o["shares"]
            cost_part = o["cost"] * frac
            proceeds = sold * r.price - r.cost * (sold / abs(r.shares) if r.shares else 1)
            out.append(dict(ticker=r.ticker, entry=o["entry"], exit=r.date, days=(r.date - o["entry"]).days,
                            return_=proceeds / cost_part - 1 if cost_part else np.nan, pnl=proceeds - cost_part))
            o["shares"] -= sold
            o["cost"] -= cost_part
            if o["shares"] <= 1e-9:
                open_pos.pop(r.ticker)
    df = pd.DataFrame(out)
    return df.rename(columns={"return_": "return"}) if len(df) else df


# ----------------------------------------------------------------------------
def _pct(x, d=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def _num(x, d=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def summary_text(res) -> str:
    s, st = res.stats["strategy"], res.stats
    b = st.get("benchmark")
    name = res.strategy.name
    rows = [("CAGR", "cagr", _pct), ("Total return", "total_return", _pct), ("Volatility", "volatility", _pct),
            ("Sharpe", "sharpe", _num), ("Sortino", "sortino", _num), ("Max drawdown", "max_drawdown", _pct),
            ("Calmar", "calmar", _num), ("Best year", "best_year", _pct), ("Worst year", "worst_year", _pct),
            ("Positive months", "pct_positive_months", lambda x: _pct(x, 0))]
    w = max(len(name), 10)
    lines = [f"{name}  ({s['start']} → {s['end']}, {s['years']:.1f} yrs)", ""]
    hdr = f"{'':18}{name[:w]:>{w}}" + (f"{res.strategy.benchmark:>10}" if b else "")
    lines.append(hdr)
    for label, key, f in rows:
        lines.append(f"{label:18}{f(s.get(key)):>{w}}" + (f"{f(b.get(key)):>10}" if b else ""))
    t = st["trading"]
    lines += ["", f"Turnover {_pct(t['turnover_per_year'], 0)}/yr · {t['n_trades']} fills · avg {_num(t.get('avg_positions'), 1)} positions · "
              f"{_pct(t.get('avg_invested'), 0)} invested · costs {_pct(t.get('costs_pct_per_year'), 2)}/yr"]
    if "win_rate" in t:
        lines.append(f"Round trips {t['round_trips']} · win rate {_pct(t['win_rate'], 0)} · avg win {_pct(t['avg_win'], 1)} · "
                     f"avg loss {_pct(t['avg_loss'], 1)} · avg hold {t['avg_days_held']:.0f} days")
    if "relative" in st:
        r = st["relative"]
        lines.append(f"vs {res.strategy.benchmark}: excess CAGR {r['excess_cagr'] * 100:+.2f} pts · beta {_num(r['beta'])} · "
                     f"alpha {_pct(r['alpha'])} · corr {_num(r['correlation'])} · tracking error {_pct(r['tracking_error'])}")
    lines.append(f"Max DD {s['max_dd_peak']} → {s['max_dd_trough']}, recovered {s['max_dd_recovery']}")
    return "\n".join(lines)


def yearly_table(res) -> pd.DataFrame:
    y = res.stats["strategy"]["_yearly"].rename(res.strategy.name)
    df = y.to_frame()
    if res.benchmark is not None:
        b = res.stats["benchmark"]["_yearly"].rename(res.strategy.benchmark)
        df = pd.concat([y, b], axis=1)
        df["excess"] = df.iloc[:, 0] - df.iloc[:, 1]
    df.index = df.index.year
    return df


# ----------------------------------------------------------------------------
def _svg_line(series_list, width=900, height=320, log=True, pad=40):
    allv = pd.concat([s for s, _ in series_list]).dropna()
    if log:
        allv = np.log(allv[allv > 0])
    lo, hi = float(allv.min()), float(allv.max())
    hi = hi if hi > lo else lo + 1
    x0, x1 = series_list[0][0].index[0], series_list[0][0].index[-1]
    span = (x1 - x0).total_seconds() or 1
    paths = []
    for s, color in series_list:
        s = s.dropna()
        v = np.log(s[s > 0]) if log else s
        xs = pad + (v.index - x0).total_seconds() / span * (width - 2 * pad)
        ys = height - pad - (v.values - lo) / (hi - lo) * (height - 2 * pad)
        step = max(1, len(xs) // 1500)
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs[::step], ys[::step]))
        paths.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.6" points="{pts}"/>')
    # gridlines: yearly
    grid = []
    for yr in range(x0.year + 1, x1.year + 1):
        d = pd.Timestamp(year=yr, month=1, day=1)
        x = pad + (d - x0).total_seconds() / span * (width - 2 * pad)
        grid.append(f'<line x1="{x:.1f}" y1="{pad}" x2="{x:.1f}" y2="{height - pad}" stroke="#2a2f3a" stroke-width="0.6"/>')
        if (x1.year - x0.year) <= 20 or yr % 5 == 0:
            grid.append(f'<text x="{x:.1f}" y="{height - pad + 14}" font-size="10" fill="#8a919e" text-anchor="middle">{yr}</text>')
    labels = []
    for frac in (0, 0.25, 0.5, 0.75, 1):
        val = lo + frac * (hi - lo)
        y = height - pad - frac * (height - 2 * pad)
        v = np.exp(val) if log else val
        labels.append(f'<text x="{pad - 4}" y="{y + 3:.1f}" font-size="10" fill="#8a919e" text-anchor="end">{v / 1e6:.2f}M</text>')
        labels.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{width - pad}" y2="{y:.1f}" stroke="#2a2f3a" stroke-width="0.6"/>')
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px">'
            + "".join(grid + labels + paths) + "</svg>")


def _svg_dd(dd_list, width=900, height=160, pad=40):
    lo = min(float(s.min()) for s, _ in dd_list)
    x0, x1 = dd_list[0][0].index[0], dd_list[0][0].index[-1]
    span = (x1 - x0).total_seconds() or 1
    out = []
    for s, color in dd_list:
        s = s.dropna()
        xs = pad + (s.index - x0).total_seconds() / span * (width - 2 * pad)
        ys = pad + (-s.values) / (-lo if lo else 1) * (height - 2 * pad)
        step = max(1, len(xs) // 1500)
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs[::step], ys[::step]))
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.3" points="{pts}"/>')
    labs = "".join(f'<text x="{pad - 4}" y="{pad + f * (height - 2 * pad) + 3:.1f}" font-size="10" fill="#8a919e" text-anchor="end">{lo * f * 100:.0f}%</text>'
                   for f in (0, 0.5, 1))
    return f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width}px">{labs}{"".join(out)}</svg>'


def write_report(res, out_dir=None) -> Path:
    strat = res.strategy
    slug = "".join(c if c.isalnum() else "_" for c in strat.name).strip("_").lower() or "strategy"
    out = Path(out_dir) if out_dir else ROOT / "results" / slug
    out.mkdir(parents=True, exist_ok=True)
    st = res.stats
    s, b, t = st["strategy"], st.get("benchmark"), st["trading"]
    bench_name = strat.benchmark if b else None

    # ---- files -------------------------------------------------------
    eq = res.equity.to_frame()
    if res.benchmark is not None:
        eq[bench_name] = res.benchmark
    eq["cash"] = res.cash
    eq.to_csv(out / "equity.csv")
    res.trades.to_csv(out / "trades.csv", index=False)
    rt = round_trips(res)
    if len(rt):
        rt.to_csv(out / "round_trips.csv", index=False)
    pd.DataFrame([dict(date=h["date"], invested=h["invested"], n=len(h["holdings"]),
                       holdings=" ".join(f"{k}:{v:.3f}" for k, v in h["weights"].items())) for h in res.holdings]).to_csv(out / "holdings.csv", index=False)
    yt = yearly_table(res)
    yt.to_csv(out / "yearly.csv")
    clean = {k: {kk: (vv if not isinstance(vv, (np.floating, np.integer)) else float(vv)) for kk, vv in v.items() if not kk.startswith("_")}
             for k, v in st.items()}
    settings = {k: getattr(strat, k) for k in ("name", "start", "end", "cash", "schedule", "schedule_when", "entry_lag", "fill",
                                               "cost_bps", "slippage_bps", "cash_rate", "benchmark", "universe", "min_history",
                                               "min_dollar_volume", "sell_if_not_eligible") if not callable(getattr(strat, k))}
    (out / "summary.json").write_text(json.dumps(dict(settings=settings, params=strat.params, stats=clean), indent=2, default=str))
    (out / "summary.txt").write_text(summary_text(res))
    if res.log:
        (out / "log.txt").write_text("\n".join(f"{d.date()}  {m}" for d, m in res.log))

    # ---- html -------------------------------------------------------------
    def card(label, val, sub=""):
        return f'<div class="card"><div class="lbl">{label}</div><div class="val">{val}</div><div class="sub">{sub}</div></div>'

    cards = [card("CAGR", _pct(s["cagr"]), f"{bench_name} {_pct(b['cagr'])}" if b else ""),
             card("Sharpe", _num(s["sharpe"]), f"{bench_name} {_num(b['sharpe'])}" if b else ""),
             card("Max drawdown", _pct(s["max_drawdown"], 1), f"{bench_name} {_pct(b['max_drawdown'], 1)}" if b else ""),
             card("Volatility", _pct(s["volatility"], 1), f"{bench_name} {_pct(b['volatility'], 1)}" if b else ""),
             card("Worst year", f"{_pct(s['worst_year'], 1)}", f"{s['worst_year_label']}"),
             card("Turnover", _pct(t["turnover_per_year"], 0) + "/yr", f"{t['n_trades']} fills, costs {_pct(t.get('costs_pct_per_year'))}/yr"),
             card("Positions", _num(t.get("avg_positions"), 1), f"{_pct(t.get('avg_invested'), 0)} invested on average")]
    if "win_rate" in t:
        cards.append(card("Win rate", _pct(t["win_rate"], 0), f"{t['round_trips']} round trips · {t['avg_days_held']:.0f} days avg"))
    if "relative" in st:
        r = st["relative"]
        cards.append(card(f"Excess vs {bench_name}", f"{r['excess_cagr'] * 100:+.2f} pts/yr", f"beta {_num(r['beta'])} · alpha {_pct(r['alpha'])}"))

    series = [(res.equity, "#5aa9ff")] + ([(res.benchmark, "#9aa3b2")] if res.benchmark is not None else [])
    dd = [(s["_dd"], "#5aa9ff")] + ([(b["_dd"], "#9aa3b2")] if b else [])

    yrows = "".join(
        f"<tr><td>{y}</td>" + "".join(f'<td class="{"pos" if v > 0 else "neg"}">{_pct(v, 1)}</td>' for v in row) + "</tr>"
        for y, row in zip(yt.index, yt.values))
    yhead = "".join(f"<th>{c}</th>" for c in yt.columns)

    hrows = "".join(f"<tr><td>{h['date'].date()}</td><td>{len(h['holdings'])}</td><td>{_pct(h['invested'], 0)}</td>"
                    f"<td class=mono>{' '.join(h['holdings'])}</td></tr>" for h in res.holdings[-24:][::-1])
    best = worst = ""
    if len(rt):
        bw = rt.sort_values("return")
        worst = "".join(f"<tr><td>{r.ticker}</td><td>{r.entry.date()}</td><td>{r.exit.date()}</td><td class=neg>{_pct(r._5, 1)}</td></tr>" for r in bw.head(8).itertuples())
        best = "".join(f"<tr><td>{r.ticker}</td><td>{r.entry.date()}</td><td>{r.exit.date()}</td><td class=pos>{_pct(r._5, 1)}</td></tr>" for r in bw.tail(8)[::-1].itertuples())
    src = ""
    if getattr(strat, "_source", None):
        src = f"<h2>Strategy code</h2><pre class=code>{strat._source.replace('<', '&lt;')}</pre>"
    settings_html = " · ".join(f"<b>{k}</b> {v}" for k, v in settings.items() if k not in ("name",))
    html = f"""<!doctype html><html><head><meta charset=utf-8><title>{strat.name}</title>
<style>
body{{font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;background:#0f1115;color:#e6e8ee;margin:0;padding:24px;max-width:960px;margin:auto}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;margin:28px 0 8px;color:#c8cdd8;text-transform:uppercase;letter-spacing:.06em}}
.meta{{color:#8a919e;font-size:12px;margin-bottom:18px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}}
.card{{background:#171a21;border:1px solid #262a33;border-radius:8px;padding:12px}}
.lbl{{font-size:11px;color:#8a919e;text-transform:uppercase;letter-spacing:.05em}} .val{{font-size:22px;font-weight:600;margin:2px 0}} .sub{{font-size:11px;color:#8a919e}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{padding:5px 8px;border-bottom:1px solid #262a33;text-align:right}} th:first-child,td:first-child{{text-align:left}}
th{{color:#8a919e;font-weight:500}} .pos{{color:#6fd39a}} .neg{{color:#ff7b7b}} .mono{{font-family:ui-monospace,Menlo,monospace;font-size:11px;text-align:left}}
.legend span{{display:inline-block;margin-right:14px;font-size:12px;color:#8a919e}} .legend i{{display:inline-block;width:18px;height:3px;margin-right:6px;vertical-align:middle}}
pre.code{{background:#171a21;border:1px solid #262a33;border-radius:8px;padding:12px;font-size:12px;overflow:auto}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:20px}} @media(max-width:700px){{.two{{grid-template-columns:1fr}}}}
.note{{color:#8a919e;font-size:12px}}
</style></head><body>
<h1>{strat.name}</h1>
<div class=meta>{s['start']} → {s['end']} · {s['years']:.1f} years · starting cash ${strat.cash:,.0f} · {settings_html}</div>
<div class=cards>{''.join(cards)}</div>
<h2>Growth of ${strat.cash / 1e6:.0f}M (log scale)</h2>
<div class=legend><span><i style="background:#5aa9ff"></i>{strat.name}</span>{f'<span><i style="background:#9aa3b2"></i>{bench_name}</span>' if b else ''}</div>
{_svg_line(series)}
<h2>Drawdown</h2>{_svg_dd(dd)}
<div class=note>Max drawdown {_pct(s['max_drawdown'], 1)} from {s['max_dd_peak']} to {s['max_dd_trough']}, recovered {s['max_dd_recovery']}.</div>
<h2>Year by year</h2><table><tr><th>Year</th>{yhead}</tr>{yrows}</table>
<div class=two><div><h2>Best round trips</h2><table><tr><th>Ticker</th><th>In</th><th>Out</th><th>Return</th></tr>{best}</table></div>
<div><h2>Worst round trips</h2><table><tr><th>Ticker</th><th>In</th><th>Out</th><th>Return</th></tr>{worst}</table></div></div>
<h2>Recent rebalances</h2><table><tr><th>Date</th><th>#</th><th>Invested</th><th>Holdings</th></tr>{hrows}</table>
{src}
<h2>Files</h2><div class=note>equity.csv · trades.csv · round_trips.csv · holdings.csv · yearly.csv · summary.json in <span class=mono>{out}</span></div>
<div class=note style="margin-top:20px">All figures pre-tax. Fills at {strat.fill} {strat.entry_lag} trading day(s) after the signal, {strat.cost_bps:g} bps per side. Book marked daily on dividend- and split-adjusted total returns. Point-in-time S&amp;P 500 membership; no-trade days skipped; delistings closed at the last real trade.</div>
</body></html>"""
    (out / "report.html").write_text(html)
    return out / "report.html"
