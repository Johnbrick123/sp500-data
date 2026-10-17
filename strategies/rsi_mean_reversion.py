"""
Short-term mean reversion, checked weekly:
buy S&P 500 names that are above their 200-day MA (long-term uptrend) but have
a 2-day RSI under 10 (short-term washout); sell when RSI(2) closes back above 70
or after 10 trading days. Up to 10 positions, 10% each, rest in cash.
"""
from qbt import Strategy


class RsiMeanReversion(Strategy):
    name = "RSI(2) mean reversion"
    start = "2005-01-01"
    schedule = "daily"
    cost_bps = 10
    max_positions = 10
    max_days = 10

    def on_rebalance(self, ctx):
        rsi2 = ctx.rsi(2)
        # exits
        for t in ctx.portfolio.holdings:
            if rsi2.get(t, 100) > 70 or ctx.portfolio.days_held(t) >= self.max_days:
                ctx.sell(t)
        # entries
        open_slots = self.max_positions - len([t for t in ctx.portfolio.holdings if (ctx.orders or {}).get(t, 1) > 0])
        if open_slots > 0:
            uptrend = ctx.above_sma(200)
            cands = [t for t in ctx.top(rsi2, 50, ascending=True)
                     if rsi2[t] < 10 and uptrend.get(t, False) and not ctx.portfolio.has(t)]
            for t in cands[:open_slots]:
                ctx.buy(t, 1 / self.max_positions)
