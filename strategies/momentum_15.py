"""
Momentum 15 — own the 15 strongest momentum names in the S&P 500, rotate monthly.

Signal = 0.4 × 12-1 month return + 0.3 × 6-1 + 0.2 × 3-1 + 0.1 × % of 52-week high.
Equal weight, trade one day after the month-end signal, 10 bps a side.
"""
from qbt import Strategy


class Momentum15(Strategy):
    name = "Momentum 15"
    start = "2010-01-01"
    schedule = "monthly"
    cost_bps = 10
    entry_lag = 1
    n = 15                         # custom parameter: --set n=20 works too

    def on_rebalance(self, ctx):
        score = (0.4 * ctx.momentum(252, 21)
                 + 0.3 * ctx.momentum(126, 21)
                 + 0.2 * ctx.momentum(63, 21)
                 + 0.1 * ctx.pct_of_high(252))
        ctx.equal_weight(ctx.top(score, self.n))
