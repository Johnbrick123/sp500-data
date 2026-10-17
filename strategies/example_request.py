"""
Written from the plain-English request in docs/BACKTEST_API.md:

  "Buy the 20 S&P 500 stocks with the best 6-month return that are above
   their 200-day average. Rebalance quarterly. Sell anything that falls 15%
   from its high. Run it from 1990 at 15 bps."

Reading taken: 'its high' = highest close since we bought it (a trailing
stop, checked daily). Stopped-out slots stay in cash until the next quarter.
"""
from qbt import Strategy


class Top20SixMonth(Strategy):
    name = "Top 20 6-month, 200d filter, 15% stop"
    start = "1990-01-01"
    schedule = "quarterly"
    cost_bps = 15
    n = 20
    stop = 0.15

    def initialize(self):
        self.high = {}

    def on_rebalance(self, ctx):
        r6 = ctx.returns(126)
        ok = ctx.above_sma(200)
        picks = [t for t in ctx.top(r6, 200) if ok[t]][: self.n]
        ctx.equal_weight(picks)
        self.high = {t: self.high.get(t, ctx.price(t)) for t in picks}

    def on_day(self, ctx):
        for t in ctx.portfolio.holdings:
            px = ctx.price(t)
            self.high[t] = max(self.high.get(t, px), px)
            if px < self.high[t] * (1 - self.stop):
                ctx.sell(t)
                self.high.pop(t, None)
