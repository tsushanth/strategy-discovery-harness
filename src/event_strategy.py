"""Strategy B -- "opportunistic liquidity around volatility events".

Framing actually simulated here: real market makers often widen or pull
quotes around earnings because the risk isn't worth it at their rebate
structure. A patient, wide-quoting participant instead gets filled at a
price that reflects genuine uncertainty. Simulating the two-sided
quote-and-fill mechanics honestly (fill probability, adverse selection on
the quote) would require order-book data this project doesn't have, so
we use the alternative framing the task spec explicitly allows: the real,
testable phenomenon that a large one-day earnings move partially
overreacts and mean-reverts in the following days. A position entered
AFTER the move (i.e. only once the "wide, right-priced" quote from the
event day would have been filled) and held for a short horizon proxies
for the same edge -- getting paid to take on a risk that faster/tighter
participants stepped away from -- without pretending to have order-book
fill data we don't have.

The transaction cost is set wide on purpose (see EventConfig.spread_bps):
that wide cost IS the strategy's premise (this is what a market maker
would actually charge to take the other side of an earnings print), not
an afterthought. Undercosting this would make the backtest meaningless.
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class EventTrade:
    symbol: str
    entry_tick: int
    exit_tick: int
    direction: int      # +1 = long (faded a down-move), -1 = short (faded an up-move)
    entry_price: float
    exit_price: float
    fees: float

    @property
    def gross_pnl(self) -> float:
        return self.direction * (self.exit_price - self.entry_price)

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees


@dataclass
class EventConfig:
    hold_days: int = 3       # trading days held after the reaction-day close
    spread_bps: float = 60.0  # round-trip cost in bps of notional -- wide on purpose,
                               # reflecting the width a MM would actually quote through
                               # an earnings print, not a tight continuous-quoting spread
    min_move: float = 0.0     # only fade reaction-day moves at least this size (abs log return)


def run_event_strategy(close: np.ndarray, ret: np.ndarray, event_ticks: list,
                        cfg: EventConfig) -> list[EventTrade]:
    """For each earnings reaction-day tick: fade the reaction-day move,
    enter at that day's close, exit `hold_days` trading days later.
    """
    trades = []
    for t in event_ticks:
        if t <= 0 or t >= len(close):
            continue
        exit_tick = t + cfg.hold_days
        if exit_tick >= len(close):
            continue  # not enough data left to hold the full horizon -- skip, don't truncate

        move = ret[t]  # the reaction-day's own return, i.e. the move being faded
        if abs(move) < cfg.min_move:
            continue

        direction = -1 if move > 0 else 1  # fade: up move -> short, down move -> long
        entry_price = close[t]
        exit_price = close[exit_tick]
        notional = entry_price + exit_price
        fees = notional * cfg.spread_bps / 10_000

        trades.append(EventTrade("", t, exit_tick, direction, entry_price, exit_price, fees))
    return trades
