"""
Hold-until-sold momentum with a golden-cross rule.

Buy the strongest momentum names whose 50-day MA is above their 200-day MA.
Keep each one until its own 50-day drops below its 200-day, then refill the
freed slot from the best eligible candidate. Winners are allowed to run
(no trimming back to equal weight).
"""
from qbt import Strategy


class GoldenCrossHold(Strategy):
    name = "Golden cross hold 15"
    start = "2010-01-01"
    schedule = "monthly"
    cost_bps = 10
    n = 15

    def on_rebalance(self, ctx):
        score = 0.4 * ctx.momentum(252, 21) + 0.3 * ctx.momentum(126, 21) + 0.2 * ctx.momentum(63, 21) + 0.1 * ctx.pct_of_high(252)
        golden = ctx.sma(50) > ctx.sma(200)            # Series of True/False over the universe

        keep = [t for t in ctx.portfolio.holdings if golden.get(t, False)]   # sell rule: 50 < 200
        candidates = [t for t in ctx.top(score, 100) if golden[t] and t not in keep]
        book = keep + candidates[: self.n - len(keep)]
        ctx.hold(book, rebalance=False)                # rebalance=False lets winners run
