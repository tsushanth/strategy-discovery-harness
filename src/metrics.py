import numpy as np


def summarize(trades, ticks_per_year: float = 252 * 390) -> dict:
    if not trades:
        return {"n_trades": 0}

    pnls = np.array([t.net_pnl for t in trades])
    fees = np.array([t.fees for t in trades])
    holds = np.array([t.exit_tick - t.entry_tick for t in trades])

    equity = np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity)
    drawdown = equity - running_max
    max_drawdown = drawdown.min() if len(drawdown) else 0.0

    # Sharpe on a per-trade basis, annualized by trade frequency -- a
    # rough measure since trades aren't evenly spaced in time, but
    # standard practice for an event-driven strategy like this one.
    if pnls.std() > 0 and len(pnls) > 1:
        trades_per_year = len(trades) / max(1, (trades[-1].exit_tick - trades[0].entry_tick)) * ticks_per_year
        sharpe = (pnls.mean() / pnls.std()) * np.sqrt(trades_per_year)
    else:
        sharpe = 0.0

    return {
        "n_trades": len(trades),
        "net_pnl": float(pnls.sum()),
        "total_fees": float(fees.sum()),
        "win_rate": float((pnls > 0).mean()),
        "avg_hold_ticks": float(holds.mean()),
        "sharpe": float(sharpe),
        "max_drawdown": float(max_drawdown),
    }
