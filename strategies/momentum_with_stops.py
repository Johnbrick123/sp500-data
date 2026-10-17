"""
Momentum 15 with daily risk rules (uses on_day, which runs every trading day):

  * trailing stop: sell any holding that closes 20% below its highest close
    since entry
  * index circuit-breaker: if SPY is below its 200-day MA, cut the whole book
    to 50% and park the rest in cash

Monthly rebalance refills the slots as usual.
"""
from qbt import Strategy


class MomentumWithStops(Strategy):
    name = "Momentum 15 + 20% trailing stop"
    start = "2010-01-01"
    schedule = "monthly"
    cost_bps = 10
    n = 15
    stop = 0.20

    def initialize(self):
        self.high_since_entry = {}

    def on_rebalance(self, ctx):
        score = 0.4 * ctx.momentum(252, 21) + 0.3 * ctx.momentum(126, 21) + 0.2 * ctx.momentum(63, 21) + 0.1 * ctx.pct_of_high(252)
        picks = ctx.top(score, self.n)
        risk_on = ctx.price_to_sma(200, tickers=["SPY"])["SPY"] > 0
        ctx.equal_weight(picks, total=1.0 if risk_on else 0.5)

    def on_day(self, ctx):
        for t in ctx.portfolio.holdings:
            px = ctx.price(t)
            hi = self.high_since_entry.get(t)
            if hi is None or ctx.portfolio.days_held(t) == 0:
                hi = px
            hi = max(hi, px)
            self.high_since_entry[t] = hi
            if px < hi * (1 - self.stop):
                ctx.sell(t)
                ctx.log(f"stop out {t} at {px:.2f} ({px / hi - 1:+.1%} from high)")
                self.high_since_entry.pop(t, None)
