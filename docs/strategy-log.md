# Strategy log

One entry per strategy tested with `qbt`. Pre-tax, 10 bps a side unless the
file says otherwise, benchmark SPY over the same window, fills one day after
the signal. Sharpe = annualised arithmetic mean / volatility.

## 2026-10-17 — engine built; bundled examples run on the 16 Oct dataset

| Strategy (file in `strategies/`) | Window | CAGR | Sharpe | Max DD | Worst yr | Turnover | SPY CAGR | SPY Sharpe |
|---|---|---|---|---|---|---|---|---|
| Momentum 15 (`momentum_15.py`) | 2010–26 | 17.83% | 0.73 | −36.4% | −13.0% | 453% | 14.11% | 0.86 |
| Momentum 15, full record (`momentum_15.py --start 1997-01-01`) | 1997–26 | 13.27% | 0.57 | −68.8% | −54.0% | 461% | 10.06% | 0.59 |
| Golden cross hold 15, winners run (`golden_cross_hold.py`) | 2010–26 | 16.89% | 0.72 | −37.0% | −15.7% | 95% | 14.11% | 0.86 |
| Momentum 15 + 20% trailing stop + SPY 200d half-book (`momentum_with_stops.py`) | 2010–26 | 12.91% | 0.66 | −28.5% | −10.7% | 535% | 14.11% | 0.86 |
| Low vol 50 quarterly, inverse-vol (`low_vol_quarterly.py`) | 1975–26 | 12.09% | 0.98 | −35.0% | −19.1% | 84% | 11.21% | 0.66 |
| SPY 200-day switch, 2% on cash, 5 bps (`spy_200d_switch.py`) | 1996–26 | 9.87% | 0.79 | −26.6% | −21.0% | 78% | 10.41% | 0.61 |
| ETF rotation top 3 of 17, 5 bps (`etf_rotation.py`) | 2008–26 | 6.37% | 0.46 | −24.7% | −13.4% | 442% | 11.32% | 0.64 |
| RSI(2) mean reversion, daily (`rsi_mean_reversion.py`) | 2005–26 | 1.64% | 0.18 | −41.7% | −31.9% | 4368% | 10.91% | 0.64 |
| Top 20 by 6-mo return, above 200d, quarterly, 15% trailing stop, 15 bps (`example_request.py`) | 1990–26 | 7.68% | 0.53 | −54.9% | −31.6% | 355% | 11.21% | 0.66 |

Cost sweep, Momentum 15 2010–26: 5 bps 18.39% · 10 bps 17.83% · 20 bps 16.72% · 35 bps 15.06% (SPY 14.11%).

Notes
- Momentum 15 on the full, survivorship-free record now shows a −68.8% max
  drawdown and a −54% worst year (2008). The 7 Oct engine on the old data
  showed −63.5% / −55.3%; the missing failed companies made it look shallower.
- The RSI(2) and `example_request` strategies are API demonstrations
  (daily logic, stops, filters), not recommendations.
- The 7 Oct engine's Sharpe used a different convention; its 0.78 for
  Momentum 15 is not comparable with the 0.73 here.
