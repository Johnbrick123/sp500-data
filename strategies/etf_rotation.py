"""
Sector/asset ETF rotation: each month own the 3 ETFs (from a fixed menu) with
the best 6-month return, but only if that return is positive; otherwise that
slot goes to IEF (7-10 yr Treasuries).
"""
from qbt import Strategy


class EtfRotation(Strategy):
    name = "ETF rotation top 3"
    start = "2008-01-01"
    universe = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF", "XLE", "XLF", "XLK", "XLV", "XLU", "XLI", "XLP", "XLY", "XLB"]
    schedule = "monthly"
    cost_bps = 5

    def on_rebalance(self, ctx):
        r6 = ctx.returns(126)
        picks = [t for t in ctx.top(r6, 3) if r6[t] > 0 and t != "IEF"]
        weights = {t: 1 / 3 for t in picks}
        if len(picks) < 3:
            weights["IEF"] = (3 - len(picks)) / 3
        ctx.set_weights(weights)
