"""
The backtest engine.

A strategy is a small Python class:

    from qbt import Strategy

    class Momentum15(Strategy):
        name = "Momentum 15"
        start, end = "2010-01-01", None
        schedule = "monthly"          # when on_rebalance is called
        cost_bps = 10                 # per side

        def on_rebalance(self, ctx):
            score = (0.4 * ctx.momentum(252, 21) + 0.3 * ctx.momentum(126, 21)
                     + 0.2 * ctx.momentum(63, 21) + 0.1 * ctx.pct_of_high(252))
            ctx.equal_weight(ctx.top(score, 15))

    result = Momentum15().run()
    result.report()

The engine walks every trading day. Signals computed on day d are filled on
day d + entry_lag at that day's close (or open). The book is marked daily with
dividend- and split-adjusted total returns, costs are charged on every fill,
and names that stop trading are closed out at their last real trade (or the
exit-gap price for companies that collapsed before they could be sold).

Nothing in `ctx` can see past today: every indicator and every history slice
is cut at the current row.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

from . import indicators as ind
from .data import Market, load_market

_MARKET: Market | None = None


def market() -> Market:
    global _MARKET
    if _MARKET is None:
        _MARKET = load_market()
    return _MARKET


# ============================================================================
# Portfolio
# ============================================================================
@dataclass
class Position:
    shares: float
    entry_i: int
    entry_price: float
    cost_basis: float          # dollars paid including commission


class Portfolio:
    def __init__(self, cash: float):
        self.cash = float(cash)
        self.positions: dict[str, Position] = {}
        self._m = market()
        self._tidx = self._m.tidx
        self._adj = self._m.panel("adj_close")
        self.value = float(cash)
        self.i = 0

    # -- marking ------------------------------------------------------------
    def mark(self, i: int) -> float:
        self.i = i
        v = self.cash
        for t, p in self.positions.items():
            px = self._adj[i, self._tidx[t]]
            if not np.isnan(px):
                p.last_price = float(px)
            v += p.shares * getattr(p, "last_price", p.entry_price)
        self.value = float(v)
        return self.value

    def price(self, t: str) -> float:
        p = self.positions.get(t)
        return float(getattr(p, "last_price", p.entry_price)) if p else float("nan")

    # -- views ------------------------------------------------------------------
    @property
    def holdings(self) -> list[str]:
        return list(self.positions)

    @property
    def weights(self) -> pd.Series:
        if not self.positions:
            return pd.Series(dtype=float)
        return pd.Series({t: p.shares * self.price(t) / self.value for t, p in self.positions.items()})

    def weight(self, t: str) -> float:
        p = self.positions.get(t)
        return p.shares * self.price(t) / self.value if p else 0.0

    def has(self, t: str) -> bool:
        return t in self.positions

    @property
    def invested(self) -> float:
        return 1 - self.cash / self.value if self.value else 0.0

    def pnl_pct(self, t: str) -> float:
        """Unrealised return on a position (price only, since entry)."""
        p = self.positions.get(t)
        return self.price(t) / p.entry_price - 1 if p else float("nan")

    def days_held(self, t: str) -> int:
        p = self.positions.get(t)
        return self.i - p.entry_i if p else 0

    def entry_date(self, t: str):
        p = self.positions.get(t)
        return self._m.dates[p.entry_i] if p else None


# ============================================================================
# Context handed to the strategy
# ============================================================================
class Context:
    """What the strategy sees on one day. Every accessor is point-in-time."""

    def __init__(self, strat: "Strategy", i: int):
        self.s = strat
        self.m = strat._m
        self.i = i
        self.date = self.m.dates[i]
        self.portfolio = strat.portfolio
        self.orders: dict[str, float] | None = None   # ticker -> target weight
        self._universe: list[str] | None = None

    # -- universe ---------------------------------------------------------------
    @property
    def universe(self) -> list[str]:
        """Tickers eligible today: in the strategy's universe, priced, tradable,
        with at least `min_history` days of data."""
        if self._universe is None:
            self._universe = self.s._eligible(self.i)
        return self._universe

    @property
    def etfs(self) -> list[str]:
        return [t for t in self.m.etfs if self.m.tradable[self.i, self.m.tidx[t]]]

    @property
    def sp500(self) -> list[str]:
        """Point-in-time S&P 500 members today, whatever the strategy universe."""
        row = self.m.member[self.i] & self.m.tradable[self.i]
        return [t for t, ok in zip(self.m.tickers, row) if ok]

    def in_index(self, t: str) -> bool:
        return bool(self.m.member[self.i, self.m.tidx[t]])

    def tradable(self, t: str) -> bool:
        return bool(self.m.tradable[self.i, self.m.tidx[t]])

    # -- prices / history --------------------------------------------------------
    def _cols(self, tickers):
        tk = list(tickers) if tickers is not None else self.universe
        return tk, [self.m.tidx[t] for t in tk]

    def price(self, ticker: str | None = None, field: str = "adj_close"):
        """Today's price for one ticker (float) or the universe (Series)."""
        pan = self.m.panel(field)
        if ticker is not None:
            return float(pan[self.i, self.m.tidx[ticker]])
        tk, cols = self._cols(None)
        return pd.Series(np.asarray(pan[self.i, cols], dtype=float), index=tk)

    def history(self, field: str = "adj_close", days: int = 252, tickers: Iterable[str] | None = None) -> pd.DataFrame:
        """Last `days` rows (through today) of a price field, dates × tickers."""
        tk, cols = self._cols(tickers)
        a = max(0, self.i - days + 1)
        pan = self.m.panel(field)
        return pd.DataFrame(np.asarray(pan[a:self.i + 1][:, cols], dtype=float),
                            index=self.m.dates[a:self.i + 1], columns=tk)

    def indicator(self, name: str, *args, days: int = 1, tickers=None, **kw):
        """Any function in qbt.indicators, as of today (Series) or the last `days` rows (DataFrame)."""
        pan = getattr(ind, name)(self.m, *args, **kw)
        tk, cols = self._cols(tickers)
        if days == 1:
            return pd.Series(np.asarray(pan[self.i, cols], dtype=float), index=tk)
        a = max(0, self.i - days + 1)
        return pd.DataFrame(np.asarray(pan[a:self.i + 1][:, cols], dtype=float),
                            index=self.m.dates[a:self.i + 1], columns=tk)

    # convenience wrappers (all point-in-time, Series over the universe)
    def returns(self, days=21, **k):          return self.indicator("returns", days, **k)
    def momentum(self, lookback=252, skip=21, **k): return self.indicator("momentum", lookback, skip, **k)
    def sma(self, window=200, **k):           return self.indicator("sma", window, **k)
    def ema(self, span=50, **k):              return self.indicator("ema", span, **k)
    def price_to_sma(self, window=200, **k):  return self.indicator("price_to_sma", window, **k)
    def pct_of_high(self, window=252, **k):   return self.indicator("pct_of_high", window, **k)
    def rolling_high(self, window=252, **k):  return self.indicator("rolling_high", window, **k)
    def rolling_low(self, window=252, **k):   return self.indicator("rolling_low", window, **k)
    def drawdown(self, **k):                  return self.indicator("drawdown", **k)
    def volatility(self, window=63, **k):     return self.indicator("volatility", window, **k)
    def atr(self, window=14, **k):            return self.indicator("atr", window, **k)
    def dollar_volume(self, window=63, **k):  return self.indicator("dollar_volume", window, **k)
    def rsi(self, window=14, **k):            return self.indicator("rsi", window, **k)
    def macd(self, fast=12, slow=26, signal=9, **k): return self.indicator("macd", fast, slow, signal, **k)
    def monthly_macd(self, fast=8, slow=17, signal=9, **k): return self.indicator("monthly_macd", fast, slow, signal, **k)
    def zscore(self, window=20, **k):         return self.indicator("zscore", window, **k)
    def bollinger_pct(self, window=20, nstd=2.0, **k): return self.indicator("bollinger_pct", window, nstd, **k)
    def days_of_history(self, **k):           return self.indicator("days_of_history", **k)

    def above_sma(self, window=200, buffer=0.0, tickers=None) -> pd.Series:
        """Boolean Series: price > SMA × (1 + buffer)."""
        return self.price_to_sma(window, tickers=tickers) > buffer

    # -- ranking helpers ---------------------------------------------------------
    @staticmethod
    def top(score: pd.Series, n: int, ascending: bool = False) -> list[str]:
        """Tickers with the n best scores (NaNs dropped)."""
        return score.dropna().sort_values(ascending=ascending).head(n).index.tolist()

    @staticmethod
    def rank(score: pd.Series, ascending: bool = False) -> pd.Series:
        return score.rank(ascending=ascending)

    @staticmethod
    def where(cond: pd.Series) -> list[str]:
        return cond[cond.fillna(False).astype(bool)].index.tolist()

    # -- orders ---------------------------------------------------------------------
    def _o(self):
        if self.orders is None:
            self.orders = dict(self.portfolio.weights.round(6).to_dict())
        return self.orders

    def set_weights(self, weights: dict | pd.Series) -> None:
        """Full target book: anything not listed is sold."""
        self.orders = {t: float(w) for t, w in dict(weights).items() if w > 0}

    def target(self, ticker: str, weight: float) -> None:
        """Set one name's target weight, leaving the rest as they are."""
        self._o()[ticker] = max(0.0, float(weight))

    buy = target

    def sell(self, ticker: str) -> None:
        self._o()[ticker] = 0.0

    def liquidate(self) -> None:
        self.orders = {}

    def equal_weight(self, tickers: Iterable[str], total: float = 1.0) -> None:
        tk = list(dict.fromkeys(tickers))
        self.set_weights({t: total / len(tk) for t in tk} if tk else {})

    def inverse_vol_weight(self, tickers: Iterable[str], window: int = 63, total: float = 1.0) -> None:
        tk = list(dict.fromkeys(tickers))
        if not tk:
            return self.set_weights({})
        v = self.volatility(window, tickers=tk).replace(0, np.nan)
        w = (1 / v).fillna((1 / v).median())
        w = w / w.sum() * total
        self.set_weights(w.to_dict())

    def hold(self, tickers: Iterable[str], total: float = 1.0, rebalance: bool = True) -> None:
        """Hold exactly these names. rebalance=True trims to equal weight;
        rebalance=False keeps existing weights (lets winners run) and gives
        new names an average slot."""
        tk = list(dict.fromkeys(tickers))
        if rebalance or not tk:
            return self.equal_weight(tk, total)
        cur = self.portfolio.weights
        keep = {t: float(cur.get(t, 0.0)) for t in tk}
        new = [t for t in tk if keep[t] == 0]
        if new:
            free = max(total - sum(keep.values()), 0.0)
            slot = free / len(new) if free > 0 else total / len(tk)
            for t in new:
                keep[t] = slot
        s = sum(keep.values())
        if s > total:
            keep = {t: w * total / s for t, w in keep.items()}
        self.set_weights(keep)

    # -- diagnostics ---------------------------------------------------------------
    def log(self, *msg) -> None:
        self.s._log.append((self.date, " ".join(str(x) for x in msg)))

    def record(self, **values) -> None:
        """Record custom daily series (e.g. ctx.record(cash_pct=0.2))."""
        self.s._records.setdefault(self.date, {}).update(values)


# ============================================================================
# Strategy
# ============================================================================
class Strategy:
    # ---- settings a strategy may override -------------------------------
    name: str = "strategy"
    start: str | None = "2010-01-01"
    end: str | None = None
    cash: float = 1_000_000
    schedule: str | list | Callable = "monthly"   # daily|weekly|monthly|quarterly|annual | list of dates | fn(date)->bool
    schedule_when: str = "end"                      # "end" or "start" of the period
    entry_lag: int = 1                              # fill N trading days after the signal day
    fill: str = "close"                             # "close" | "open"
    cost_bps: float = 10.0                          # commission + spread, per side
    slippage_bps: float = 0.0                       # extra adverse move on every fill
    cash_rate: float = 0.0                          # annual interest on idle cash
    benchmark: str | None = "SPY"
    universe: str | list = "sp500"                  # "sp500" | "etfs" | "all" | [tickers]
    min_history: int = 0                            # days of prices required before eligible
    min_dollar_volume: float = 0.0                  # 63-day median $ volume floor
    sell_if_not_eligible: bool = False              # auto-sell names that drop out of the universe
    buy_order_ttl: int = 5                          # days an unfilled buy is retried before it is dropped

    def __init__(self, **overrides):
        for k, v in overrides.items():
            if not hasattr(self, k):
                raise AttributeError(f"unknown setting {k!r}")
            setattr(self, k, v)
        self._m = market()
        self._log: list = []
        self._records: dict = {}
        self.params = dict(overrides)

    # ---- hooks ------------------------------------------------------------------
    def initialize(self) -> None: ...
    def on_rebalance(self, ctx: Context) -> None:
        raise NotImplementedError
    def on_day(self, ctx: Context) -> None: ...

    # ---- internals ----------------------------------------------------------------
    def _eligible(self, i: int) -> list[str]:
        m = self._m
        row = m.tradable[i].copy()
        if self.universe == "sp500":
            row &= m.member[i]
        elif self.universe == "etfs":
            e = np.zeros(len(m.tickers), bool)
            e[[m.tidx[t] for t in m.etfs]] = True
            row &= e
        elif isinstance(self.universe, (list, tuple, set)):
            e = np.zeros(len(m.tickers), bool)
            e[[m.tidx[t] for t in self.universe if t in m.tidx]] = True
            row &= e
        if self.min_history:
            row &= ind.days_of_history(m)[i] >= self.min_history
        if self.min_dollar_volume:
            dv = ind.dollar_volume(m)[i]
            row &= np.nan_to_num(dv, nan=0.0) >= self.min_dollar_volume
        return [t for t, ok in zip(m.tickers, row) if ok]

    def _rebalance_days(self, idx: np.ndarray) -> np.ndarray:
        d = self._m.dates[idx]
        sch = self.schedule
        if callable(sch):
            return np.array([i for i, dt in zip(idx, d) if sch(dt)])
        if isinstance(sch, (list, tuple)):
            want = pd.DatetimeIndex(pd.to_datetime(list(sch)))
            return np.array([self._m.day_index(x) for x in want if self._m.dates[0] <= x <= self._m.dates[-1]])
        if sch == "daily":
            return idx
        key = {"weekly": d.isocalendar().week.to_numpy() + 100 * d.isocalendar().year.to_numpy(),
               "monthly": d.year * 12 + d.month,
               "quarterly": d.year * 4 + (d.month - 1) // 3,
               "annual": d.year}[sch]
        s = pd.Series(idx, index=key)
        g = s.groupby(level=0)
        return (g.last() if self.schedule_when == "end" else g.first()).to_numpy()

    def _fill_price(self, i: int, t: str, side: int) -> float:
        m = self._m
        j = m.tidx[t]
        px = float(m.panel("adj_close")[i, j])
        if self.fill == "open":
            o, c = float(m.panel("open")[i, j]), float(m.panel("close")[i, j])
            if not (np.isnan(o) or np.isnan(c)) and c > 0:
                px *= o / c
        return px * (1 + side * self.slippage_bps / 1e4)

    def _execute(self, i: int, targets: dict[str, float]) -> dict[str, float]:
        """Trade toward target weights at day i's prices. Returns targets still pending."""
        m, pf = self._m, self.portfolio
        pf.mark(i)
        total = pf.value
        pending = {}
        cost_rate = self.cost_bps / 1e4
        # sells first so cash is available for buys
        order = sorted(targets.items(), key=lambda kv: kv[1])
        for t, w in order:
            j = m.tidx.get(t)
            if j is None:
                continue
            if not m.tradable[i, j]:
                pending[t] = w
                continue
            cur_w = pf.weight(t)
            delta = w * total - cur_w * total
            if abs(delta) < max(1.0, 0.0005 * total):   # ignore dust (< 5 bps)
                continue
            side = 1 if delta > 0 else -1
            px = self._fill_price(i, t, side)
            if np.isnan(px) or px <= 0:
                pending[t] = w
                continue
            if side > 0 and delta > pf.cash:
                delta = max(pf.cash - 1.0, 0.0)
                if delta <= 0:
                    continue
            shares = delta / px
            fee = abs(delta) * cost_rate
            pf.cash -= delta + fee
            pos = pf.positions.get(t)
            if pos is None:
                pos = Position(shares=0.0, entry_i=i, entry_price=px, cost_basis=0.0)
                pf.positions[t] = pos
            pos.shares += shares
            pos.cost_basis += delta + fee
            pos.last_price = px
            self._trades.append(dict(date=m.dates[i], ticker=t, side="BUY" if side > 0 else "SELL",
                                     shares=shares, price=px, value=abs(delta), cost=fee,
                                     weight_before=cur_w, weight_after=w))
            self._turnover_dollars += abs(delta)
            if pos.shares <= 1e-9 or w == 0:
                pf.positions.pop(t, None)
        return pending

    def _close_dead(self, i: int) -> None:
        """Names that no longer trade: close at the last trade, or the exit-gap price."""
        m, pf = self._m, self.portfolio
        for t in list(pf.positions):
            j = m.tidx[t]
            if i > self._last_valid[j]:
                pos = pf.positions.pop(t)
                px = float(m.panel("adj_close")[self._last_valid[j], j])
                gap = self._gap_change.get(t)
                if gap is not None:
                    px *= 1 + gap
                proceeds = pos.shares * px
                fee = proceeds * self.cost_bps / 1e4
                pf.cash += proceeds - fee
                self._trades.append(dict(date=m.dates[i], ticker=t, side="DELIST", shares=-pos.shares, price=px,
                                         value=proceeds, cost=fee, weight_before=pos.shares * px / pf.value, weight_after=0.0))
                self._log.append((m.dates[i], f"{t} stopped trading; closed at {px:.2f}" + (f" (exit gap {gap:+.1%})" if gap is not None else "")))

    # ---- run ---------------------------------------------------------------------
    def run(self, verbose: bool = True) -> "Result":
        m = self._m
        self.initialize()
        self.portfolio = Portfolio(self.cash)
        self._trades: list[dict] = []
        self._turnover_dollars = 0.0
        self._holdings_log: list[dict] = []
        adj = m.panel("adj_close")
        valid = ~np.isnan(np.asarray(adj))
        self._last_valid = np.where(valid.any(axis=0), len(m.dates) - 1 - np.argmax(valid[::-1], axis=0), -1)
        self._gap_change = dict(zip(m.exit_gaps["ticker"], m.exit_gaps["change"]))

        i0 = int(m.dates.searchsorted(pd.Timestamp(self.start), side="left")) if self.start else 0   # first day >= start
        i1 = m.day_index(self.end) if self.end else len(m.dates) - 1
        idx = np.arange(i0, i1 + 1)
        rebal = set(self._rebalance_days(idx).tolist())
        pending: dict[int, dict[str, float]] = {}        # fill day -> targets
        carry: dict[str, float] = {}                     # unfilled targets retried daily
        carry_age: dict[str, int] = {}
        equity = np.empty(len(idx))
        cash_s = np.empty(len(idx))
        daily_rate = self.cash_rate / 252

        for k, i in enumerate(idx):
            # 1. interest, delistings
            if daily_rate:
                self.portfolio.cash *= 1 + daily_rate
            self._close_dead(i)
            # 2. fills due today (+ retries)
            todo = dict(carry)
            todo.update(pending.pop(i, {}))
            if todo:
                left = self._execute(i, todo)
                carry = {}
                for t, w in left.items():
                    age = carry_age.get(t, 0) + 1
                    if w == 0 or age <= self.buy_order_ttl:
                        carry[t] = w
                        carry_age[t] = age
                for t in list(carry_age):
                    if t not in carry:
                        carry_age.pop(t)
            # 3. mark and let the strategy look at the day
            self.portfolio.mark(i)
            ctx = Context(self, i)
            if i in rebal:
                self.on_rebalance(ctx)
                self._holdings_log.append(dict(date=m.dates[i], holdings=list(self.portfolio.positions),
                                               weights=self.portfolio.weights.round(4).to_dict(),
                                               invested=round(self.portfolio.invested, 4)))
            self.on_day(ctx)
            if ctx.orders is not None:
                tgt = dict(ctx.orders)
                if self.sell_if_not_eligible:
                    elig = set(ctx.universe)
                    tgt.update({t: 0.0 for t in self.portfolio.positions if t not in elig and t not in tgt})
                # names we hold but are not in the full target book are sold
                for t in self.portfolio.positions:
                    tgt.setdefault(t, 0.0)
                if self.entry_lag <= 0:                       # fill at today's close
                    left = self._execute(i, tgt)
                    carry.update(left)
                    equity_now = self.portfolio.mark(i)
                else:
                    pending.setdefault(i + self.entry_lag, {}).update(tgt)
            equity[k] = self.portfolio.value
            cash_s[k] = self.portfolio.cash
            if verbose and k % 1000 == 0:
                print(f"  {m.dates[i].date()}  value {self.portfolio.value:,.0f}  positions {len(self.portfolio.positions)}")

        dates = m.dates[idx]
        eq = pd.Series(equity, index=dates, name="strategy")
        bench = None
        if self.benchmark and self.benchmark in m.tidx:
            b = np.asarray(adj[idx, m.tidx[self.benchmark]], dtype=float)
            bench = pd.Series(b / b[~np.isnan(b)][0] * self.cash, index=dates, name=self.benchmark).ffill()
        return Result(self, eq, bench, pd.Series(cash_s, index=dates), pd.DataFrame(self._trades), self._holdings_log, self._log, self._records)


# ============================================================================
# Result
# ============================================================================
class Result:
    def __init__(self, strat, equity, benchmark, cash, trades, holdings, log, records):
        self.strategy = strat
        self.equity = equity
        self.benchmark = benchmark
        self.cash = cash
        self.trades = trades
        self.holdings = holdings
        self.log = log
        self.records = pd.DataFrame.from_dict(records, orient="index").sort_index() if records else pd.DataFrame()
        from .report import compute_stats
        self.stats = compute_stats(self)

    def summary(self) -> str:
        from .report import summary_text
        return summary_text(self)

    def report(self, out_dir=None, open_browser=False):
        from .report import write_report
        return write_report(self, out_dir)

    def __repr__(self):
        return self.summary()
