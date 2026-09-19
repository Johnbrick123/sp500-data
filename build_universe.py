"""
Step 1: Build the ticker universe and a point-in-time membership table.

This is the step that protects you from survivorship bias. We pull every
ticker that has EVER been in the S&P 500 (not just today's 503), plus a
long-history ETF list.

Outputs:
  data/universe/members.parquet   point-in-time index membership (date, ticker)
  data/universe/tickers.txt       every ticker we need prices for
"""
import pandas as pd
from pathlib import Path

OUT = Path(__file__).parent / "data" / "universe"
OUT.mkdir(parents=True, exist_ok=True)

HIST_URL = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
            "S%26P%20500%20Historical%20Components%20%26%20Changes.csv")
CURR_URL = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500.csv"

# ~50 ETFs chosen for the longest available history.
# Inception years noted: most ETFs simply did not exist before 2000.
ETFS = [
    "SPY",  # 1993
    "MDY",  # 1995
    "DIA",  # 1998
    "QQQ",  # 1999
    # Sector SPDRs (Dec 1998, except RE/Comms)
    "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY",
    "XLRE", "XLC",
    # iShares style/size (1999-2004)
    "IWM", "IWB", "IWV", "IWD", "IWF", "IWN", "IWO",
    "IVV", "IVE", "IVW", "IJH", "IJR", "IJJ", "IJK", "IJS", "IJT",
    # International (WEBS series from 1996)
    "EFA", "EEM", "EWJ", "EWG", "EWU", "EWA", "EWC", "EWH", "EWS",
    "EWT", "EWY", "EWZ",
    # Fixed income
    "AGG", "LQD", "TLT", "IEF", "SHY", "TIP", "HYG",
    # Commodities / other
    "GLD", "SLV", "VTI", "VOO", "VEU", "VWO", "VNQ", "RSP", "DVY",
]


def main():
    hist = pd.read_csv(HIST_URL)
    hist["date"] = pd.to_datetime(hist["date"])

    # Explode the comma-separated snapshot into tidy (date, ticker) rows.
    rows = []
    for d, tks in zip(hist["date"], hist["tickers"]):
        for t in str(tks).split(","):
            t = t.strip()
            if t:
                rows.append((d, t))
    members = pd.DataFrame(rows, columns=["date", "ticker"])

    curr = pd.read_csv(CURR_URL)
    current_tickers = sorted(set(curr["Symbol"].astype(str).str.strip()))

    ever = sorted(set(members["ticker"]) | set(current_tickers))
    all_tickers = sorted(set(ever) | set(ETFS))

    members.to_parquet(OUT / "members.parquet", index=False)
    curr.to_csv(OUT / "current_constituents.csv", index=False)
    (OUT / "tickers.txt").write_text("\n".join(all_tickers))

    print(f"membership snapshots : {members['date'].min().date()} -> "
          f"{members['date'].max().date()}")
    print(f"ever in S&P 500      : {len(ever)}")
    print(f"in S&P 500 today     : {len(current_tickers)}")
    print(f"ETFs                 : {len(ETFS)}")
    print(f"total tickers to pull: {len(all_tickers)}")
    print(f"\nSurvivorship check: {len(ever) - len(current_tickers)} names "
          f"have left the index. A 'current members only' dataset would "
          f"silently drop all of them.")


if __name__ == "__main__":
    main()
