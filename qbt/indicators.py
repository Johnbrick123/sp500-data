"""
Vectorised indicator library.

Every function takes the Market and returns a full float32 panel
[day, ticker] computed with *trailing* windows only, so the value on row d
uses data through day d and nothing after it. The engine then hands a strategy
row d (and earlier rows) and never anything later.

Results are cached on the Market for the life of the run, so calling
`ctx.sma(200)` on every rebalance costs nothing after the first call.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _df(m, field="adj_close") -> pd.DataFrame:
    key = ("_df", field)
    if key not in m._ind_cache:
        m._ind_cache[key] = pd.DataFrame(np.asarray(m.panel(field)), index=m.dates, columns=m.tickers)
    return m._ind_cache[key]


def cached(fn):
    def wrap(m, *a, **k):
        key = (fn.__name__, a, tuple(sorted(k.items())))
        if key not in m._ind_cache:
            out = fn(m, *a, **k)
            m._ind_cache[key] = out.to_numpy(dtype=np.float32) if isinstance(out, pd.DataFrame) else out
        return m._ind_cache[key]
    wrap.__name__ = fn.__name__
    wrap.__doc__ = fn.__doc__
    return wrap


# ---- returns ---------------------------------------------------------------
@cached
def returns(m, days=1, field="adj_close"):
    """Trailing `days`-day total return: price[d] / price[d-days] - 1."""
    p = _df(m, field)
    return p / p.shift(days) - 1


@cached
def momentum(m, lookback=252, skip=21, field="adj_close"):
    """Classic '12-1' style momentum: return from d-lookback to d-skip."""
    p = _df(m, field)
    return p.shift(skip) / p.shift(lookback) - 1


@cached
def daily_returns(m, field="adj_close"):
    return _df(m, field).pct_change(fill_method=None)


# ---- moving averages ---------------------------------------------------------
@cached
def sma(m, window=200, field="adj_close"):
    """Simple moving average of the last `window` trading days."""
    return _df(m, field).rolling(window, min_periods=window).mean()


@cached
def ema(m, span=50, field="adj_close"):
    return _df(m, field).ewm(span=span, min_periods=span, adjust=False).mean()


@cached
def price_to_sma(m, window=200, field="adj_close"):
    """price / SMA - 1.  > 0 means above the average."""
    p = _df(m, field)
    return p / p.rolling(window, min_periods=window).mean() - 1


# ---- highs / lows ------------------------------------------------------------
@cached
def rolling_high(m, window=252, field="adj_close"):
    return _df(m, field).rolling(window, min_periods=max(5, window // 4)).max()


@cached
def rolling_low(m, window=252, field="adj_close"):
    return _df(m, field).rolling(window, min_periods=max(5, window // 4)).min()


@cached
def pct_of_high(m, window=252, field="adj_close"):
    """price / trailing high. 1.0 = at the high, 0.8 = 20% below it."""
    p = _df(m, field)
    return p / p.rolling(window, min_periods=max(5, window // 4)).max()


@cached
def drawdown(m, field="adj_close"):
    """Drawdown from the all-time running high (negative numbers)."""
    p = _df(m, field)
    return p / p.cummax() - 1


# ---- volatility / risk --------------------------------------------------------
@cached
def volatility(m, window=63, field="adj_close"):
    """Annualised standard deviation of daily returns over `window` days."""
    return _df(m, field).pct_change(fill_method=None).rolling(window, min_periods=window).std() * np.sqrt(252)


@cached
def atr(m, window=14):
    """Average true range (needs open/high/low; empty before 1996)."""
    h, l, c = _df(m, "high"), _df(m, "low"), _df(m, "close")
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()]).groupby(level=0).max()
    return tr.rolling(window, min_periods=window).mean()


@cached
def dollar_volume(m, window=63):
    """Median daily dollar volume over `window` days (close × volume)."""
    dv = _df(m, "close") * _df(m, "volume")
    return dv.rolling(window, min_periods=max(5, window // 4)).median()


# ---- oscillators -----------------------------------------------------------------
@cached
def rsi(m, window=14, field="adj_close"):
    p = _df(m, field)
    d = p.diff()
    up = d.clip(lower=0).ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = up / dn
    return 100 - 100 / (1 + rs)


@cached
def macd(m, fast=12, slow=26, signal=9, field="adj_close"):
    """MACD histogram (macd line minus signal line)."""
    p = _df(m, field)
    line = p.ewm(span=fast, adjust=False).mean() - p.ewm(span=slow, adjust=False).mean()
    return line - line.ewm(span=signal, adjust=False).mean()


@cached
def zscore(m, window=20, field="adj_close"):
    p = _df(m, field)
    r = p.rolling(window, min_periods=window)
    return (p - r.mean()) / r.std()


@cached
def bollinger_pct(m, window=20, nstd=2.0, field="adj_close"):
    """%B: 0 at the lower band, 1 at the upper band."""
    p = _df(m, field)
    r = p.rolling(window, min_periods=window)
    lo, hi = r.mean() - nstd * r.std(), r.mean() + nstd * r.std()
    return (p - lo) / (hi - lo)


@cached
def days_of_history(m, field="adj_close"):
    """How many priced days each ticker has had so far."""
    return (~_df(m, field).isna()).cumsum().astype(float)


# ---- monthly helpers -------------------------------------------------------------
@cached
def monthly_macd(m, fast=8, slow=17, signal=9, field="adj_close"):
    """MACD histogram on month-end closes, forward-filled to every day."""
    p = _df(m, field)
    me = p.groupby([p.index.year, p.index.month]).tail(1)
    line = me.ewm(span=fast, adjust=False).mean() - me.ewm(span=slow, adjust=False).mean()
    hist = line - line.ewm(span=signal, adjust=False).mean()
    return hist.reindex(p.index).ffill()


ALL = {k: v for k, v in globals().items() if callable(v) and getattr(v, "__name__", "") in {
    "returns", "momentum", "daily_returns", "sma", "ema", "price_to_sma", "rolling_high",
    "rolling_low", "pct_of_high", "drawdown", "volatility", "atr", "dollar_volume", "rsi",
    "macd", "zscore", "bollinger_pct", "days_of_history", "monthly_macd"}}
