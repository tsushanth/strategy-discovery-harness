import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from event_strategy import EventConfig, EventTrade, run_event_strategy
from event_data import realized_vol, baseline_vol
from metrics import summarize


def test_event_trade_pnl_long_fade():
    # faded a down-move (direction=+1, i.e. long): price recovers -> profit.
    t = EventTrade("X", entry_tick=0, exit_tick=3, direction=1,
                    entry_price=100, exit_price=104, fees=1.0)
    assert abs(t.gross_pnl - 4.0) < 1e-9
    assert abs(t.net_pnl - 3.0) < 1e-9


def test_event_trade_pnl_short_fade():
    # faded an up-move (direction=-1, i.e. short): price gives back gains -> profit.
    t = EventTrade("X", entry_tick=0, exit_tick=3, direction=-1,
                    entry_price=100, exit_price=96, fees=1.0)
    assert abs(t.gross_pnl - 4.0) < 1e-9
    assert abs(t.net_pnl - 3.0) < 1e-9


def test_run_event_strategy_fades_the_reaction_day_move():
    # tick 5 has a big up move (reaction day); strategy should short it.
    close = np.array([100.0, 100, 100, 100, 100, 110, 108, 106, 105], dtype=float)
    ret = np.zeros(len(close))
    ret[1:] = np.diff(np.log(close))
    cfg = EventConfig(hold_days=3, spread_bps=10.0)
    trades = run_event_strategy(close, ret, event_ticks=[5], cfg=cfg)
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == -1  # up move -> fade by shorting
    assert t.entry_tick == 5
    assert t.exit_tick == 8
    assert t.entry_price == 110
    assert t.exit_price == 105
    # spread cost must scale with notional (both legs), never zero for a nonzero spread_bps
    assert t.fees > 0


def test_run_event_strategy_skips_trades_that_would_run_past_data_end():
    close = np.array([100.0, 105, 104, 103], dtype=float)
    ret = np.zeros(len(close))
    ret[1:] = np.diff(np.log(close))
    cfg = EventConfig(hold_days=5, spread_bps=10.0)
    trades = run_event_strategy(close, ret, event_ticks=[1], cfg=cfg)
    assert trades == []  # not enough days left to hold the full horizon


def test_run_event_strategy_respects_min_move_filter():
    close = np.array([100.0, 100.01, 100.02, 100.03, 100.04], dtype=float)
    ret = np.zeros(len(close))
    ret[1:] = np.diff(np.log(close))
    cfg = EventConfig(hold_days=2, spread_bps=10.0, min_move=0.05)
    trades = run_event_strategy(close, ret, event_ticks=[1], cfg=cfg)
    assert trades == []  # move is tiny, below the min_move filter


def test_summarize_works_with_event_trades():
    trades = [
        EventTrade("X", 0, 3, 1, 100, 104, 1.0),
        EventTrade("X", 10, 13, -1, 100, 98, 1.0),
    ]
    m = summarize(trades)
    assert m["n_trades"] == 2
    assert m["net_pnl"] > 0
    assert m["win_rate"] == 1.0


def test_realized_vol_is_higher_when_event_windows_have_bigger_moves():
    rng = np.random.default_rng(0)
    ret = rng.normal(0, 0.01, 200)
    event_ticks = [50, 100, 150]
    for t, move in zip(event_ticks, [0.15, -0.12, 0.18]):
        ret[t] = move  # inject artificial large, varied moves at each "event"
    ev_vol = realized_vol(ret, event_ticks, window=0)
    base_vol = baseline_vol(ret, event_ticks, window=0)
    assert ev_vol > base_vol


if __name__ == "__main__":
    test_event_trade_pnl_long_fade()
    test_event_trade_pnl_short_fade()
    test_run_event_strategy_fades_the_reaction_day_move()
    test_run_event_strategy_skips_trades_that_would_run_past_data_end()
    test_run_event_strategy_respects_min_move_filter()
    test_summarize_works_with_event_trades()
    test_realized_vol_is_higher_when_event_windows_have_bigger_moves()
    print("all tests passed")
