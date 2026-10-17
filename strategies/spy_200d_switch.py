"""
The blunt timing model: 100% SPY when it closes above its 200-day moving
average at month-end, 100% cash (earning 2%) otherwise.
"""
from qbt import Strategy


class Spy200Switch(Strategy):
    name = "SPY 200-day switch"
    start = "1996-01-01"          # SPY prices begin Jan 1995 in this dataset; the 200-day MA needs a year
    universe = ["SPY"]
    schedule = "monthly"
    cost_bps = 5
    cash_rate = 0.02

    def on_rebalance(self, ctx):
        if ctx.price_to_sma(200)["SPY"] > 0:
            ctx.set_weights({"SPY": 1.0})
        else:
            ctx.liquidate()
        ctx.record(in_market=ctx.portfolio.has("SPY") or ctx.orders == {"SPY": 1.0})
