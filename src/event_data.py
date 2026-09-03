"""Real historical price + earnings-date data via yfinance.

Same package/pattern mm-backtester already validated for pairs_data.py --
free daily bars for equities, plus yfinance's Ticker.get_earnings_dates()
for real historical earnings-report timestamps (confirmed working against
AAPL and ROKU before this module was written -- see README).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class SymbolData:
    symbol: str
    dates: np.ndarray       # trading-day dates, aligned with close
    close: np.ndarray       # daily close, adjusted
    ret: np.ndarray         # daily log return, ret[i] = log(close[i]/close[i-1]), ret[0] = 0
    event_ticks: list       # indices into `close`/`dates` that are the earnings "reaction day"
    event_dates: list       # the raw earnings report timestamps behind each event_tick


def _reaction_day_index(dates: pd.DatetimeIndex, earnings_ts: pd.Timestamp) -> int | None:
    """Map a raw earnings report timestamp to the trading-day index where its
    price move actually shows up. Most companies report after the close
    (~16:00 or later), so the reaction is priced in on the NEXT trading day;
    a handful report before the open, in which case the same day is the
    reaction day.
    """
    earnings_ts = pd.Timestamp(earnings_ts)
    if earnings_ts.tzinfo is not None and dates.tz is not None:
        earnings_ts = earnings_ts.tz_convert(dates.tz)
    same_day_reaction = earnings_ts.hour < 9  # reported well before the 9:30 open

    day = earnings_ts.normalize()
    candidates = np.where(dates.normalize() >= day)[0]
    if len(candidates) == 0:
        return None
    idx = candidates[0]
    # If the report landed on a trading day itself and was after-open/after-close,
    # bump to the next trading day (the close we align to hasn't seen the news yet
    # for an after-close report on that same date).
    if not same_day_reaction and dates.normalize()[idx] == day:
        idx += 1
        if idx >= len(dates):
            return None
    return int(idx)


def load_symbol(symbol: str, period: str = "8y") -> SymbolData:
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period, interval="1d", auto_adjust=True)
    hist = hist[~hist.index.duplicated(keep="first")]
    dates = hist.index
    close = hist["Close"].to_numpy()
    ret = np.zeros(len(close))
    ret[1:] = np.diff(np.log(close))

    earnings = ticker.get_earnings_dates(limit=60)
    if earnings is None or earnings.empty:
        event_ticks, event_dates = [], []
    else:
        # Only real, already-reported events (Reported EPS present), not the
        # forward-looking estimated next earnings date row(s) yfinance includes.
        past = earnings[earnings["Reported EPS"].notna()]
        event_ticks, event_dates = [], []
        for ts in past.index:
            idx = _reaction_day_index(dates, ts)
            if idx is not None and 1 <= idx < len(close):
                event_ticks.append(idx)
                event_dates.append(ts)

    return SymbolData(symbol=symbol, dates=dates.to_numpy(), close=close, ret=ret,
                       event_ticks=event_ticks, event_dates=event_dates)


def realized_vol(ret: np.ndarray, ticks: list, window: int = 1) -> float:
    """Stdev of daily returns within `window` days of each tick in `ticks`
    (inclusive on both sides), pooled across all given ticks.
    """
    picked = []
    for t in ticks:
        lo, hi = max(0, t - window), min(len(ret), t + window + 1)
        picked.extend(ret[lo:hi])
    return float(np.std(picked)) if picked else 0.0


def baseline_vol(ret: np.ndarray, event_ticks: list, window: int = 1) -> float:
    """Stdev of daily returns OUTSIDE any event window -- the non-event baseline."""
    excluded = set()
    for t in event_ticks:
        for i in range(max(0, t - window), min(len(ret), t + window + 1)):
            excluded.add(i)
    mask = np.array([i not in excluded for i in range(len(ret))])
    baseline = ret[mask]
    return float(np.std(baseline)) if len(baseline) else 0.0
