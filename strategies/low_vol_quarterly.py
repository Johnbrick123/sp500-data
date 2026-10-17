"""
Low-volatility: every quarter own the 50 S&P 500 names with the lowest 1-year
volatility, weighted by inverse volatility. Requires 2 years of price history
and $20M/day of trading so thin names and fresh listings are skipped.
Runs from 1975 to show a long-history test (membership starts 1970).
"""
from qbt import Strategy


class LowVolQuarterly(Strategy):
    name = "Low vol 50 quarterly"
    start = "1975-01-01"
    schedule = "quarterly"
    cost_bps = 10
    min_history = 504
    n = 50

    def on_rebalance(self, ctx):
        vol = ctx.volatility(252)
        ctx.inverse_vol_weight(ctx.top(vol, self.n, ascending=True), window=252)
