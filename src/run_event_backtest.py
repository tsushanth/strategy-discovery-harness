"""Strategy B walk-forward backtest: opportunistic wide-quote / fade-the-move
around real historical earnings dates, on real yfinance daily price data.

Step 1: confirm real vol actually spikes around earnings for the chosen
symbols (don't assume it).
Step 2: pool real earnings events across symbols, sort chronologically,
tune hold_days/spread_bps on the first ~70%, evaluate ONCE on the final
~30% with those fixed parameters.
Step 3: report the honest result, explicitly flagging the small-sample
problem inherent to earnings (only ~4 events/year/symbol).
"""
import numpy as np
import pandas as pd

from event_data import load_symbol, realized_vol, baseline_vol
from event_strategy import EventConfig, run_event_strategy
from metrics import summarize

SYMBOLS = ["ROKU", "ETSY", "PINS", "SNAP"]


def sharpe_from_dates(trades_sorted, dates_sorted):
    """Sharpe annualized off real calendar time between trades, not tick
    counts -- tick indices aren't comparable across different symbols'
    price series once trades are pooled, so metrics.summarize's own
    tick-based annualization would be meaningless here.
    """
    pnls = np.array([t.net_pnl for t in trades_sorted])
    if len(pnls) < 2 or pnls.std() == 0:
        return 0.0
    span_days = (dates_sorted[-1] - dates_sorted[0]).days
    if span_days <= 0:
        return 0.0
    trades_per_year = len(trades_sorted) / span_days * 365.25
    return float((pnls.mean() / pnls.std()) * np.sqrt(trades_per_year))


def main():
    print("=== Step 1: does realized vol actually spike around earnings? ===\n")
    symbol_data = {}
    for sym in SYMBOLS:
        sd = load_symbol(sym)
        symbol_data[sym] = sd
        ev_vol = realized_vol(sd.ret, sd.event_ticks, window=1)
        base_vol = baseline_vol(sd.ret, sd.event_ticks, window=1)
        ratio = ev_vol / base_vol if base_vol else float("nan")
        print(f"  {sym:5s}  n_events={len(sd.event_ticks):3d}  "
              f"event-window daily vol={ev_vol:.4f}  baseline daily vol={base_vol:.4f}  "
              f"ratio={ratio:.2f}x")
    print()

    # Pool all real earnings events across symbols, chronologically.
    events = []
    for sym, sd in symbol_data.items():
        for tick, ts in zip(sd.event_ticks, sd.event_dates):
            events.append({"symbol": sym, "tick": tick, "date": pd.Timestamp(ts).tz_localize(None)})
    events.sort(key=lambda e: e["date"])
    n = len(events)
    split = int(n * 0.7)
    print(f"=== Step 2: pooled real earnings events across {len(SYMBOLS)} symbols: {n} total ===")
    print(f"in-sample:     {events[0]['date'].date()} .. {events[split-1]['date'].date()}  ({split} events)")
    print(f"out-of-sample: {events[split]['date'].date()} .. {events[-1]['date'].date()}  ({n - split} events)\n")

    def ticks_for(symbol, ev_slice):
        return [e["tick"] for e in ev_slice if e["symbol"] == symbol]

    def run_pooled(cfg: EventConfig, ev_slice):
        pooled = []
        for sym, sd in symbol_data.items():
            t = ticks_for(sym, ev_slice)
            for tr in run_event_strategy(sd.close, sd.ret, t, cfg):
                tr.symbol = sym
                pooled.append(tr)
        return pooled

    in_sample = events[:split]
    out_sample = events[split:]

    print("=== In-sample parameter search (first 70% of events) ===")
    best_cfg, best_pnl = None, -np.inf
    for hold_days in [1, 2, 3, 5]:
        for spread_bps in [30.0, 60.0, 100.0]:
            cfg = EventConfig(hold_days=hold_days, spread_bps=spread_bps)
            trades = run_pooled(cfg, in_sample)
            if not trades:
                continue
            m = summarize(trades)
            pnl = m["net_pnl"]
            print(f"  hold_days={hold_days}  spread_bps={spread_bps:5.1f}  "
                  f"trades={m['n_trades']:3d}  net_pnl={pnl:8.2f}  win_rate={m['win_rate']:.0%}")
            if pnl > best_pnl:
                best_pnl, best_cfg = pnl, cfg

    print(f"\nSelected in-sample: hold_days={best_cfg.hold_days}, spread_bps={best_cfg.spread_bps}\n")

    print("=== Out-of-sample evaluation (final 30% of events, params fixed, evaluated once) ===")
    oos_trades = run_pooled(best_cfg, out_sample)
    # sort by actual event date for a chronologically valid equity curve / sharpe
    date_by_tick = {(e["symbol"], e["tick"]): e["date"] for e in out_sample}
    oos_trades_sorted = sorted(oos_trades, key=lambda t: date_by_tick[(t.symbol, t.entry_tick)])
    oos_dates_sorted = [date_by_tick[(t.symbol, t.entry_tick)] for t in oos_trades_sorted]

    m = summarize(oos_trades_sorted)
    m["sharpe"] = sharpe_from_dates(oos_trades_sorted, oos_dates_sorted)
    for k, v in m.items():
        print(f"  {k:16s}: {v}")

    print("\n=== Honest read ===")
    if m.get("n_trades", 0) == 0:
        print("  No trades in the out-of-sample window -- not a result, a sign this")
        print("  needs rethinking, not a pass or fail.")
    else:
        print(f"  net PnL > 0  : {'yes' if m['net_pnl'] > 0 else 'no'} ({m['net_pnl']:.2f})")
        print(f"  win rate     : {m['win_rate']:.0%}")
        print(f"  sharpe       : {m['sharpe']:.2f}  <- see sample-size caveat in README, do not")
        print( "                   trust this in isolation")
        print(f"  n_trades     : {m['n_trades']}  out of {n} total real pooled earnings events")
        print("  Earnings happen ~4x/year/symbol -- even pooling 4 symbols over ~8 years only")
        print("  yields a couple dozen out-of-sample events. Any Sharpe/win-rate number here")
        print("  carries far more sampling noise than a real trading decision should be based on.")


if __name__ == "__main__":
    main()
