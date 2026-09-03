"""Offline tests for the FRED data layer and the macro engine's trade math.

No network: FRED CSV parsing is tested on literal strings, and the macro
trade generator is tested on a hand-built price series with a known answer.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fred_data
from backtest_engines import _macro_trades


def test_parse_csv_current_header_and_missing_marker():
    text = "observation_date,DGS2\n2020-01-02,1.58\n2020-01-03,.\n2020-01-06,1.54\n"
    dates, vals = fred_data._parse_csv(text, "DGS2")
    assert list(vals) == [1.58, 1.54]           # "." row dropped
    assert dates[0] == np.datetime64("2020-01-02")
    assert dates[-1] == np.datetime64("2020-01-06")


def test_parse_csv_legacy_header():
    text = "DATE,DGS10\n2019-05-01,2.51\n2019-05-02,2.55\n"
    dates, vals = fred_data._parse_csv(text, "DGS10")
    assert list(vals) == [2.51, 2.55]


def test_parse_csv_sorts_ascending():
    text = "observation_date,DFF\n2021-03-03,0.07\n2021-03-01,0.09\n"
    dates, vals = fred_data._parse_csv(text, "DFF")
    assert dates[0] == np.datetime64("2021-03-01")
    assert list(vals) == [0.09, 0.07]


def test_load_series_rejects_lag_released_series():
    # CPI is release-lagged; treating it as known on its reference date is
    # look-ahead bias. The guard must refuse it by default.
    with pytest.raises(ValueError, match="look-ahead"):
        fred_data.load_series("CPIAUCSL")


def test_shock_days_are_abs_first_difference():
    fs = fred_data.FredSeries(
        series_id="DGS2",
        dates=np.array([np.datetime64(d) for d in
                        ["2020-01-02", "2020-01-03", "2020-01-06"]]),
        values=np.array([1.50, 1.60, 1.40]),
        source="test",
    )
    sd = fred_data.shock_days(fs)
    assert sd.iloc[0] == pytest.approx(0.10)
    assert sd.iloc[1] == pytest.approx(0.20)


def test_macro_trades_direction_and_horizon():
    # close rises 100 -> 110 over 5 trading days; a long (dir +1) entered on the
    # shock day and held 5 days should show a gross gain of 10 minus fees.
    dates = pd.date_range("2021-01-04", periods=10, freq="B")
    close = pd.Series(np.linspace(100, 118, 10), index=dates)
    shock = [dates[1]]                       # enter at index 1
    trades = _macro_trades(shock, close, list(dates), hold_days=5, direction=1, cost_bps=0.0)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_tick == 1 and t.exit_tick == 6
    assert t.gross_pnl == pytest.approx(close.iloc[6] - close.iloc[1])


def test_macro_trades_skips_when_horizon_runs_off_the_end():
    dates = pd.date_range("2021-01-04", periods=4, freq="B")
    close = pd.Series([100, 101, 102, 103], index=dates)
    shock = [dates[2]]                       # index 2 + hold 5 = 7 > len -> skipped
    trades = _macro_trades(shock, close, list(dates), hold_days=5, direction=1, cost_bps=0.0)
    assert trades == []


def test_macro_trades_are_non_overlapping():
    # Shocks on consecutive days with a 3-day hold: only non-overlapping entries
    # should become trades, so the same move isn't counted repeatedly.
    dates = pd.date_range("2021-01-04", periods=20, freq="B")
    close = pd.Series(np.arange(100, 120), index=dates)
    shocks = [dates[1], dates[2], dates[3], dates[6], dates[7]]  # clustered
    trades = _macro_trades(shocks, close, list(dates), hold_days=3, direction=1, cost_bps=0.0)
    entries = [t.entry_tick for t in trades]
    # index1 -> holds to 4; next allowed entry is >4: index6 -> holds to 9.
    assert entries == [1, 6]


def test_macro_trades_maps_shock_to_next_trading_day():
    # A shock dated on a weekend (not in the trading calendar) enters on the
    # next available trading day.
    dates = pd.date_range("2021-01-04", periods=12, freq="B")  # 12 business days
    close = pd.Series(np.arange(100, 112), index=dates)
    weekend = pd.Timestamp("2021-01-09")     # a Saturday, not in `dates`
    trades = _macro_trades([weekend], close, list(dates), hold_days=1, direction=1, cost_bps=0.0)
    assert len(trades) == 1
    # first trading day >= Sat 2021-01-09 is Mon 2021-01-11 = index 5
    assert trades[0].entry_tick == 5 and trades[0].exit_tick == 6
