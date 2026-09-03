"""Pairs stat-arb: trade mean-reversion of a hedged log-price spread.

spread_t = logA_t - beta * logB_t, with `beta` a fixed hedge ratio estimated on
IN-SAMPLE data only (OLS) and then frozen. The tradable signal is a CAUSAL
rolling z-score of the spread (trailing window only -- no future data in the
normalisation), so there is no look-ahead in either the in-sample or the
out-of-sample window.

Trades are non-overlapping: enter when the spread is stretched (|z| >= entry_z),
short the rich leg / long the cheap leg, and exit when it reverts (|z| <=
exit_z) or the window ends. This is the classic distance/cointegration
mean-reversion trade, deliberately kept simple and honestly costed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PairConfig:
    window: int = 40          # rolling z-score lookback (trading days)
    entry_z: float = 2.0      # enter when |z| >= this
    exit_z: float = 0.5       # exit when |z| <= this (or window ends)
    cost_bps: float = 10.0    # round-trip cost on the spread (both legs), in bps


@dataclass
class PairTrade:
    symbol: str               # "A/B" pair label, for reporting
    entry_tick: int
    exit_tick: int
    direction: int            # +1 long spread (spread cheap), -1 short spread (spread rich)
    spread_entry: float
    spread_exit: float
    fees: float

    @property
    def gross_pnl(self) -> float:
        return self.direction * (self.spread_exit - self.spread_entry)

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees


def hedge_ratio(log_a: np.ndarray, log_b: np.ndarray) -> float:
    """OLS slope of log_a on log_b (with intercept). Estimated in-sample, frozen."""
    b = np.polyfit(log_b, log_a, 1)
    return float(b[0])


def spread_series(log_a: np.ndarray, log_b: np.ndarray, beta: float) -> np.ndarray:
    return log_a - beta * log_b


def rolling_z(spread: np.ndarray, window: int) -> np.ndarray:
    """Causal rolling z-score: z[t] uses only spread[t-window+1 .. t]."""
    n = len(spread)
    z = np.full(n, np.nan)
    for t in range(window - 1, n):
        w = spread[t - window + 1:t + 1]
        mu, sd = w.mean(), w.std()
        if sd > 0:
            z[t] = (spread[t] - mu) / sd
    return z


def run_pairs_strategy(spread: np.ndarray, z: np.ndarray, cfg: PairConfig,
                       tick_lo: int, tick_hi: int, label: str = "") -> list[PairTrade]:
    """Generate non-overlapping mean-reversion trades whose ENTRY tick falls in
    [tick_lo, tick_hi). A trade may exit after tick_hi (we hold to reversion)."""
    trades = []
    n = len(spread)
    t = max(tick_lo, 0)
    hi = min(tick_hi, n)
    while t < hi:
        if np.isnan(z[t]) or abs(z[t]) < cfg.entry_z:
            t += 1
            continue
        direction = -1 if z[t] > 0 else 1     # z high -> spread rich -> short spread
        entry = t
        s = t + 1
        while s < n and not (abs(z[s]) <= cfg.exit_z):
            s += 1
        exit_ = min(s, n - 1)
        fees = cfg.cost_bps / 10_000          # round-trip cost on the hedged spread
        trades.append(PairTrade(label, entry, exit_, direction,
                                float(spread[entry]), float(spread[exit_]), fees))
        t = exit_ + 1                          # non-overlapping: resume after the exit
    return trades
