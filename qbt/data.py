"""
Data layer for the backtester.

Turns the tidy `prices.parquet` release into wide numpy panels (rows = trading
days, columns = tickers) so a strategy can look at the whole market on any
given day in a single vectorised operation.

Everything is cached under data/panels/ the first time it is built; after that
loading takes about a second.

Point-in-time safety rules baked in here:
  * `member[d, t]` is True only if ticker t was in the S&P 500 on day d.
  * `tradable[d, t]` is False on no-trade days (halts, suspensions, carried
    closes) and on days the ticker has no price at all.
  * `exit_gaps.csv` is used to book the *next* real trade when a company's
    last member day was not a trade (SVB, Signature, HealthSouth ...).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "panels"
RELEASE = "https://github.com/Johnbrick123/sp500-data/releases/download/data"
FILES = ["prices.parquet", "membership_intervals.parquet", "no_trade_days.csv",
         "exit_gaps.csv", "recycled_tickers.csv"]
FIELDS = ["adj_close", "close", "open", "high", "low", "volume", "dividends"]


def ensure_data() -> None:
    """Download the release files if they are not present locally."""
    DATA.mkdir(parents=True, exist_ok=True)
    import urllib.request
    for f in FILES:
        p = DATA / f
        if not p.exists():
            print(f"downloading {f} ...")
            urllib.request.urlretrieve(f"{RELEASE}/{f}", p)


def _build_cache() -> None:
    import duckdb
    ensure_data()
    CACHE.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    print("building panel cache (one-time, ~1-2 minutes) ...")
    px = DATA / "prices.parquet"
    dates = con.sql(f"select distinct date from '{px}' order by date").df()["date"]
    dates = pd.DatetimeIndex(dates)
    tickers = con.sql(f"select distinct ticker from '{px}' order by ticker").df()["ticker"].tolist()
    d_idx = {d: i for i, d in enumerate(dates)}
    t_idx = {t: j for j, t in enumerate(tickers)}
    n_d, n_t = len(dates), len(tickers)

    # Row positions of every record, computed once.
    pos = con.sql(f"select date, ticker from '{px}'").df()
    ri = pos["date"].map(d_idx).to_numpy()
    ci = pos["ticker"].map(t_idx).to_numpy()
    for f in FIELDS:
        vals = con.sql(f"select {f} from '{px}'").df()[f].to_numpy()
        panel = np.full((n_d, n_t), np.nan, dtype=np.float32)
        panel[ri, ci] = vals
        np.save(CACHE / f"{f}.npy", panel)
        print(f"  {f}: {panel.shape}")

    # Membership panel from intervals [start, end)
    iv = pd.read_parquet(DATA / "membership_intervals.parquet")
    member = np.zeros((n_d, n_t), dtype=bool)
    for r in iv.itertuples(index=False):
        if r.ticker not in t_idx:
            continue
        a = dates.searchsorted(r.start, side="left")
        b = dates.searchsorted(r.end, side="left") if pd.notna(r.end) else n_d
        member[a:b, t_idx[r.ticker]] = True
    recycled = pd.read_csv(DATA / "recycled_tickers.csv")
    for t in recycled["ticker"].astype(str):
        if t in t_idx:
            member[:, t_idx[t]] = False
    np.save(CACHE / "member.npy", member)

    # No-trade mask
    ntd = pd.read_csv(DATA / "no_trade_days.csv", parse_dates=["date"])
    notrade = np.zeros((n_d, n_t), dtype=bool)
    ok = ntd["ticker"].isin(t_idx) & ntd["date"].isin(d_idx)
    notrade[ntd.loc[ok, "date"].map(d_idx).to_numpy(), ntd.loc[ok, "ticker"].map(t_idx).to_numpy()] = True
    np.save(CACHE / "notrade.npy", notrade)

    pd.Series(dates).to_frame("date").to_parquet(CACHE / "dates.parquet")
    pd.Series(tickers).to_frame("ticker").to_parquet(CACHE / "tickers.parquet")
    (CACHE / "built_from").write_text(str(os.path.getmtime(px)))
    print("panel cache ready")


@dataclass
class Market:
    """All market data as aligned panels. Index positions, not dates, are the
    currency inside the engine; `dates`/`tickers` map them back."""
    dates: pd.DatetimeIndex
    tickers: list[str]
    member: np.ndarray            # bool  [day, ticker]  point-in-time S&P 500 membership
    tradable: np.ndarray          # bool  [day, ticker]  has a real trade that day
    exit_gaps: pd.DataFrame
    _panels: dict = field(default_factory=dict)
    _ind_cache: dict = field(default_factory=dict)

    # ---- raw fields -------------------------------------------------------
    def panel(self, name: str) -> np.ndarray:
        """Return a raw price field as a float32 panel [day, ticker]."""
        if name not in self._panels:
            p = CACHE / f"{name}.npy"
            if not p.exists():
                raise KeyError(f"unknown field {name!r}; choose from {FIELDS}")
            self._panels[name] = np.load(p, mmap_mode="r")
        return self._panels[name]

    def frame(self, name: str) -> pd.DataFrame:
        return pd.DataFrame(np.asarray(self.panel(name)), index=self.dates, columns=self.tickers)

    @property
    def tidx(self) -> dict:
        if "_tidx" not in self._panels:
            self._panels["_tidx"] = {t: j for j, t in enumerate(self.tickers)}
        return self._panels["_tidx"]

    @property
    def etfs(self) -> list[str]:
        """Tickers that were never index members (the ETF set plus a few extras)."""
        never = ~self.member.any(axis=0)
        return [t for t, n in zip(self.tickers, never) if n and "-" not in t]

    def day_index(self, date) -> int:
        """Index of the last trading day on or before `date`."""
        i = self.dates.searchsorted(pd.Timestamp(date), side="right") - 1
        return int(max(i, 0))


def load_market(rebuild: bool = False) -> Market:
    ensure_data()
    stamp = CACHE / "built_from"
    fresh = stamp.exists() and stamp.read_text() == str(os.path.getmtime(DATA / "prices.parquet"))
    if rebuild or not fresh:
        _build_cache()
    dates = pd.DatetimeIndex(pd.read_parquet(CACHE / "dates.parquet")["date"])
    tickers = pd.read_parquet(CACHE / "tickers.parquet")["ticker"].tolist()
    member = np.load(CACHE / "member.npy")
    notrade = np.load(CACHE / "notrade.npy")
    adj = np.load(CACHE / "adj_close.npy", mmap_mode="r")
    tradable = ~np.isnan(adj) & ~notrade
    gaps = pd.read_csv(DATA / "exit_gaps.csv", parse_dates=["removed", "last_member_day", "last_trade", "next_trade"])
    m = Market(dates=dates, tickers=tickers, member=member, tradable=tradable, exit_gaps=gaps)
    m._panels["adj_close"] = adj
    return m
