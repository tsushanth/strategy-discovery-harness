"""Real daily closes for a basket of symbols, aligned on common trading days.

Same yfinance pattern as the rest of the harness. Returns log prices, which is
what the pair spread / hedge-ratio math wants.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class PanelData:
    symbols: list[str]
    dates: np.ndarray                 # common trading days (datetime64), ascending
    log_close: dict[str, np.ndarray]  # symbol -> aligned log close


def load_panel(symbols: list[str], period: str = "8y") -> PanelData:
    frames = {}
    for sym in symbols:
        hist = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True)
        hist = hist[~hist.index.duplicated(keep="first")]
        if hist.empty:
            continue
        idx = hist.index.tz_localize(None) if hist.index.tz is not None else hist.index
        frames[sym] = pd.Series(hist["Close"].to_numpy(), index=pd.DatetimeIndex(idx))
    if len(frames) < 2:
        raise ValueError(f"need >=2 symbols with data, got {list(frames)}")
    df = pd.DataFrame(frames).dropna(how="any").sort_index()
    if len(df) < 100:
        raise ValueError(f"only {len(df)} common trading days across {list(frames)}")
    log_close = {c: np.log(df[c].to_numpy()) for c in df.columns}
    return PanelData(symbols=list(df.columns), dates=df.index.to_numpy(), log_close=log_close)
