"""Offline tests for the pairs stat-arb math, the announcement-window trade
generator, and the FRED release-dates key guard. No network."""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fred_releases
from pairs_strategy import (PairConfig, hedge_ratio, rolling_z, run_pairs_strategy,
                            spread_series)
from backtest_engines import _announcement_trades


# ------------------------------ pairs math ------------------------------ #
def test_hedge_ratio_recovers_slope():
    lb = np.linspace(1.0, 5.0, 50)
    la = 2.0 * lb + 1.0                      # exact beta = 2
    assert hedge_ratio(la, lb) == pytest.approx(2.0, abs=1e-9)


def test_rolling_z_is_causal_and_nan_padded():
    spread = np.arange(10, dtype=float)
    z = rolling_z(spread, window=3)
    assert np.isnan(z[0]) and np.isnan(z[1])  # first window-1 undefined
    assert not np.isnan(z[2])
    # z[t] must not depend on future values: recompute z[5] from trailing window only
    w = spread[3:6]
    assert z[5] == pytest.approx((spread[5] - w.mean()) / w.std())


def test_spread_series():
    la = np.array([1.0, 2.0, 3.0])
    lb = np.array([1.0, 1.0, 1.0])
    assert list(spread_series(la, lb, beta=2.0)) == [-1.0, 0.0, 1.0]


def test_pairs_trade_enters_on_stretch_exits_on_reversion_nonoverlapping():
    n = 20
    spread = np.zeros(n)
    spread[10] = 5.0                          # rich at entry
    spread[13] = 0.0                          # reverted at exit
    z = np.zeros(n)
    z[0] = z[1] = np.nan
    z[10], z[11], z[12], z[13] = 3.0, 2.5, 1.0, 0.0
    cfg = PairConfig(window=3, entry_z=2.0, exit_z=0.5, cost_bps=10.0)
    trades = run_pairs_strategy(spread, z, cfg, tick_lo=3, tick_hi=n, label="A/B")
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_tick == 10 and t.exit_tick == 13
    assert t.direction == -1                  # z>0 -> spread rich -> short spread
    assert t.gross_pnl == pytest.approx(5.0)  # -1 * (0 - 5)
    assert t.net_pnl == pytest.approx(5.0 - 10.0 / 10_000)


def test_pairs_trade_long_direction_when_spread_cheap():
    n = 12
    spread = np.zeros(n)
    spread[5], spread[8] = -4.0, 0.0
    z = np.zeros(n)
    z[0] = z[1] = np.nan
    z[5], z[6], z[7], z[8] = -3.0, -2.0, -1.0, 0.0
    cfg = PairConfig(window=3, entry_z=2.0, exit_z=0.5, cost_bps=0.0)
    trades = run_pairs_strategy(spread, z, cfg, 3, n, "A/B")
    assert len(trades) == 1 and trades[0].direction == 1   # z<0 -> spread cheap -> long
    assert trades[0].gross_pnl == pytest.approx(4.0)       # +1 * (0 - -4)


# ------------------------- announcement windows ------------------------- #
def test_announcement_trades_pre_event_and_nonoverlap():
    close = pd.Series(np.arange(100, 120, dtype=float),
                      index=pd.date_range("2021-01-04", periods=20, freq="B"))
    reaction = [5, 6, 10]          # 6 overlaps the trade opened at 5
    trades = _announcement_trades(reaction, close, enter_offset=-1, hold_days=1,
                                  direction=1, cost_bps=0.0)
    assert [t.entry_tick for t in trades] == [4, 9]   # r=5 -> enter 4; r=6 skipped; r=10 -> enter 9


def test_announcement_trades_post_event_offset_zero():
    close = pd.Series(np.arange(100, 120, dtype=float),
                      index=pd.date_range("2021-01-04", periods=20, freq="B"))
    trades = _announcement_trades([5], close, enter_offset=0, hold_days=3,
                                  direction=1, cost_bps=0.0)
    assert trades[0].entry_tick == 5 and trades[0].exit_tick == 8


# --------------------------- FRED key guard ---------------------------- #
def test_release_dates_without_key_raises(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(fred_releases.MissingKey):
        fred_releases.release_dates(10)         # CPI, but no key -> refuse, don't fabricate
    with pytest.raises(fred_releases.MissingKey):
        fred_releases.resolve_release_id("Consumer Price Index")
