"""Deterministic, real-data backtest engines for the discovery harness.

Each engine takes an `Idea` and returns an `EngineResult` measured with strict
walk-forward discipline: tune on the first ~70% of events chronologically,
evaluate ONCE on the final ~30%, never re-tune after seeing the OOS number.
No engine returns or trusts any LLM-supplied performance claim.

Registered engines (routed by `Idea.template`):
  event_window_earnings  -- earnings overreaction/mean-reversion proxy (yfinance)
  macro_release_drift    -- equity reaction to macro rate shocks (FRED + yfinance)

Deferred (credential/data gated, intentionally not implemented here):
  vol_surface_mispricing (IBKR OPTION_IMPLIED_VOLATILITY -- needs TWS/Gateway)
  order_flow_imbalance   (LOBSTER L2 -- free samples too thin for OOS breadth)
  news_sentiment         (Alpaca news feed -- needs API key)
  pairs_stat_arb         (yfinance; unblocked but out of this session's scope)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf

from event_data import load_symbol, realized_vol, baseline_vol
from event_strategy import EventConfig, EventTrade, run_event_strategy
from metrics import summarize
from harness_models import (EngineResult, GridCell, Idea,
                            LINEAGE_PROXY, LINEAGE_REAL)
import fred_data
import fred_releases
from pairs_data import load_panel
from pairs_strategy import (PairConfig, hedge_ratio, rolling_z, run_pairs_strategy,
                            spread_series)
from itertools import combinations


DEFAULT_EARNINGS_SYMBOLS = ["ROKU", "ETSY", "PINS", "SNAP"]
DEFAULT_HOLD_GRID = [1, 2, 3, 5]
DEFAULT_SPREAD_GRID = [30.0, 60.0, 100.0]


def _sharpe_from_dates(trades_sorted, dates_sorted) -> float:
    """Annualise Sharpe off real calendar time between trades, not tick counts
    -- pooled tick indices aren't comparable across symbols/series."""
    pnls = np.array([t.net_pnl for t in trades_sorted])
    if len(pnls) < 2 or pnls.std() == 0:
        return 0.0
    span_days = (pd.Timestamp(dates_sorted[-1]) - pd.Timestamp(dates_sorted[0])).days
    if span_days <= 0:
        return 0.0
    trades_per_year = len(trades_sorted) / span_days * 365.25
    return float((pnls.mean() / pnls.std()) * np.sqrt(trades_per_year))


# --------------------------------------------------------------------------- #
# Engine 1: earnings event window (wraps the existing Strategy B base modules)
# --------------------------------------------------------------------------- #
def run_event_window_earnings(idea: Idea) -> EngineResult:
    symbols = idea.instruments or DEFAULT_EARNINGS_SYMBOLS
    res = EngineResult(idea_id=idea.id, template=idea.template, ok=False,
                       lineage=LINEAGE_PROXY)  # mean-reversion proxies a wide-quote fill
    try:
        symbol_data, premise = {}, {}
        for sym in symbols:
            sd = load_symbol(sym)
            if len(sd.event_ticks) == 0:
                continue
            symbol_data[sym] = sd
            ev = realized_vol(sd.ret, sd.event_ticks, window=1)
            base = baseline_vol(sd.ret, sd.event_ticks, window=1)
            premise[sym] = {"n_events": len(sd.event_ticks),
                            "event_vol": ev, "baseline_vol": base,
                            "ratio": (ev / base) if base else None}
        res.premise_check = premise

        events = []
        for sym, sd in symbol_data.items():
            for tick, ts in zip(sd.event_ticks, sd.event_dates):
                events.append({"symbol": sym, "tick": tick,
                               "date": pd.Timestamp(ts).tz_localize(None)})
        events.sort(key=lambda e: e["date"])
        n = len(events)
        if n < 10:
            res.error = f"only {n} pooled earnings events -- not enough to split"
            return res
        split = int(n * 0.7)
        in_sample, out_sample = events[:split], events[split:]

        def run_pooled(cfg: EventConfig, ev_slice):
            pooled = []
            for sym, sd in symbol_data.items():
                ticks = [e["tick"] for e in ev_slice if e["symbol"] == sym]
                for tr in run_event_strategy(sd.close, sd.ret, ticks, cfg):
                    tr.symbol = sym
                    pooled.append(tr)
            return pooled

        grid, best_cfg, best_pnl = [], None, -np.inf
        for hold in DEFAULT_HOLD_GRID:
            for spread in DEFAULT_SPREAD_GRID:
                cfg = EventConfig(hold_days=hold, spread_bps=spread)
                trades = run_pooled(cfg, in_sample)
                if not trades:
                    continue
                m = summarize(trades)
                grid.append(GridCell(params={"hold_days": hold, "spread_bps": spread},
                                     score=float(m["net_pnl"]), n_trades=int(m["n_trades"])))
                if m["net_pnl"] > best_pnl:
                    best_pnl, best_cfg = m["net_pnl"], cfg
        if best_cfg is None:
            res.error = "no in-sample earnings trades produced by any config"
            return res

        res.in_sample_grid = grid
        res.chosen_params = {"hold_days": best_cfg.hold_days, "spread_bps": best_cfg.spread_bps}
        res.in_sample_metrics = summarize(run_pooled(best_cfg, in_sample))

        oos_trades = run_pooled(best_cfg, out_sample)
        date_by_tick = {(e["symbol"], e["tick"]): e["date"] for e in out_sample}
        oos_sorted = sorted(oos_trades, key=lambda t: date_by_tick[(t.symbol, t.entry_tick)])
        oos_dates = [date_by_tick[(t.symbol, t.entry_tick)] for t in oos_sorted]
        m = summarize(oos_sorted)
        if oos_sorted:
            m["sharpe"] = _sharpe_from_dates(oos_sorted, oos_dates)
        res.oos_metrics = m
        res.n_oos_trades = int(m.get("n_trades", 0))
        res.notes.append(f"pooled {n} real earnings events across {len(symbol_data)} symbols "
                         f"({split} in-sample / {n - split} OOS)")
        res.notes.append("lineage=proxy: mean-reversion stands in for a wide-quote fill "
                         "(no order-book data)")
        res.ok = True
    except Exception as e:  # a data/network failure is a non-result, not a pass
        res.error = f"{type(e).__name__}: {e}"
    return res


# --------------------------------------------------------------------------- #
# Engine 2: macro release drift (NEW) -- equity reaction to FRED rate shocks
# --------------------------------------------------------------------------- #
def _load_equity(symbol: str, period: str = "10y"):
    hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    hist = hist[~hist.index.duplicated(keep="first")]
    if hist.empty:
        raise ValueError(f"no price history for {symbol}")
    idx = hist.index.tz_localize(None) if hist.index.tz is not None else hist.index
    s = pd.Series(hist["Close"].to_numpy(), index=pd.DatetimeIndex(idx)).sort_index()
    return s.dropna()   # yfinance occasionally emits a NaN close row; a NaN price
                        # would silently poison PnL/Sharpe, so drop it here.


def _macro_trades(shock_dates, close: pd.Series, dates_list, hold_days: int,
                  direction: int, cost_bps: float) -> list[EventTrade]:
    """Enter equity at the close of the trading day on/after each shock date,
    hold `hold_days` trading days, exit at close. `direction` is frozen.

    NON-OVERLAPPING by construction: a shock that lands while a position from an
    earlier shock is still open is skipped. Without this, entering on (say) the
    top 20% of shock days while holding several days double-counts the same
    market move across many 'trades' -- the positions aren't independent, and a
    per-trade Sharpe computed on them would be badly inflated. Non-overlap keeps
    each trade a distinct bet so the reported Sharpe is honest.
    """
    trades = []
    n = len(dates_list)
    pos = {d: i for i, d in enumerate(dates_list)}
    last_exit = -1
    for sd in sorted(shock_dates):
        # find first equity trading day >= shock date
        i = pos.get(sd)
        if i is None:
            after = [j for j, d in enumerate(dates_list) if d >= sd]
            if not after:
                continue
            i = after[0]
        if i <= last_exit:
            continue  # a position is still open -- don't double-count the move
        j = i + hold_days
        if j >= n:
            continue  # not enough data to hold full horizon -- skip, don't truncate
        entry, exit_ = float(close.iloc[i]), float(close.iloc[j])
        fees = (entry + exit_) * cost_bps / 10_000
        trades.append(EventTrade("", i, j, direction, entry, exit_, fees))
        last_exit = j
    return trades


def run_macro_release_drift(idea: Idea) -> EngineResult:
    p = idea.parameters or {}
    series_id = str(p.get("fred_series", "DGS2"))
    equity = (idea.instruments[0] if idea.instruments else str(p.get("equity", "SPY")))
    hold_grid = [int(h) for h in p.get("hold_days_grid", DEFAULT_HOLD_GRID)]
    quantile_grid = [float(q) for q in p.get("threshold_quantiles", [0.80, 0.90, 0.95])]
    cost_bps = float(p.get("cost_bps", 5.0))  # fixed, NOT tuned, so tuning can't game it down

    res = EngineResult(idea_id=idea.id, template=idea.template, ok=False, lineage=LINEAGE_REAL)
    try:
        fs = fred_data.load_series(series_id)             # keyless, real
        deltas = fred_data.shock_days(fs)                 # |Δ| by observation date
        close = _load_equity(equity)                      # real yfinance
        dates_list = list(close.index)

        # overlap window
        lo = max(deltas.index.min(), close.index.min())
        hi = min(deltas.index.max(), close.index.max())
        deltas = deltas[(deltas.index >= lo) & (deltas.index <= hi)]
        if len(deltas) < 50:
            res.error = f"only {len(deltas)} overlapping macro observations for {series_id}/{equity}"
            return res

        # chronological 70/30 split on the macro-observation timeline
        d_sorted = deltas.sort_index()
        split_ts = d_sorted.index[int(len(d_sorted) * 0.7)]
        is_delta = d_sorted[d_sorted.index < split_ts]
        oos_delta = d_sorted[d_sorted.index >= split_ts]

        # premise: do large macro-shock days coincide with bigger equity moves?
        eq_ret = close.pct_change().abs()
        shock90 = d_sorted[d_sorted >= d_sorted.quantile(0.90)].index
        eq_on_shock = eq_ret.reindex(shock90, method="nearest").dropna()
        eq_base = eq_ret[~eq_ret.index.isin(shock90)].dropna()
        res.premise_check = {
            "series": series_id, "equity": equity,
            "equity_absret_on_shock_days": float(eq_on_shock.mean()) if len(eq_on_shock) else None,
            "equity_absret_baseline": float(eq_base.mean()) if len(eq_base) else None,
            "ratio": (float(eq_on_shock.mean() / eq_base.mean())
                      if len(eq_on_shock) and len(eq_base) and eq_base.mean() else None),
        }

        # in-sample grid over (hold_days, threshold_quantile); direction chosen per cell
        grid, best = [], None
        chosen_dir_by_cell: dict[tuple, int] = {}
        chosen_thr_by_cell: dict[tuple, float] = {}
        for hold in hold_grid:
            for q in quantile_grid:
                thr = float(is_delta.quantile(q))
                is_events = list(is_delta[is_delta >= thr].index)
                if len(is_events) < 5:
                    continue
                best_dir, best_dir_pnl, best_dir_n = 0, -np.inf, 0
                for direction in (1, -1):
                    tr = _macro_trades(is_events, close, dates_list, hold, direction, cost_bps)
                    if not tr:
                        continue
                    m = summarize(tr)
                    if m["net_pnl"] > best_dir_pnl:
                        best_dir_pnl, best_dir, best_dir_n = m["net_pnl"], direction, m["n_trades"]
                if best_dir == 0:
                    continue
                key = (hold, q)
                chosen_dir_by_cell[key] = best_dir
                chosen_thr_by_cell[key] = thr
                grid.append(GridCell(params={"hold_days": hold, "threshold_q": q},
                                     score=float(best_dir_pnl), n_trades=int(best_dir_n)))
                if best is None or best_dir_pnl > best.score:
                    best = grid[-1]
        if best is None:
            res.error = "no in-sample macro-shock trades produced by any config"
            return res

        res.in_sample_grid = grid
        key = (best.params["hold_days"], best.params["threshold_q"])
        direction = chosen_dir_by_cell[key]
        thr = chosen_thr_by_cell[key]        # frozen absolute threshold from IN-SAMPLE quantile
        hold = best.params["hold_days"]
        res.chosen_params = {"hold_days": hold, "threshold_q": best.params["threshold_q"]}
        res.in_sample_metrics = {"net_pnl": best.score, "n_trades": best.n_trades,
                                 "direction": direction, "abs_threshold": thr}

        # OOS: apply frozen threshold/hold/direction to the held-out window, once
        oos_events = list(oos_delta[oos_delta >= thr].index)
        oos_trades = _macro_trades(oos_events, close, dates_list, hold, direction, cost_bps)
        oos_sorted = sorted(oos_trades, key=lambda t: t.entry_tick)
        oos_dates = [dates_list[t.entry_tick] for t in oos_sorted]
        m = summarize(oos_sorted)
        if oos_sorted:
            m["sharpe"] = _sharpe_from_dates(oos_sorted, oos_dates)
        res.oos_metrics = m
        res.n_oos_trades = int(m.get("n_trades", 0))
        res.notes.append(f"FRED {series_id} ({fs.source}) shocks vs {equity}; "
                         f"frozen |Δ|>={thr:.4g}, hold={hold}d, dir={'+1' if direction>0 else '-1'}, "
                         f"cost={cost_bps}bps rt")
        res.notes.append(f"{len(is_delta)} in-sample / {len(oos_delta)} OOS macro observations")
        res.ok = True
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


# --------------------------------------------------------------------------- #
# Engine 3: macro release calendar (NEW) -- drift around REAL announcement dates
# --------------------------------------------------------------------------- #
def _announcement_trades(reaction_idx: list[int], close: pd.Series, enter_offset: int,
                         hold_days: int, direction: int, cost_bps: float):
    """Enter at close of (reaction_day + enter_offset), hold `hold_days` trading
    days. enter_offset<0 tests pre-announcement drift (hold across the event);
    enter_offset==0 tests post-announcement drift. Non-overlapping."""
    trades = []
    n = len(close)
    last_exit = -1
    for r in sorted(reaction_idx):
        i = r + enter_offset
        j = i + hold_days
        if i <= last_exit or i < 0 or j >= n:
            continue
        entry, exit_ = float(close.iloc[i]), float(close.iloc[j])
        fees = (entry + exit_) * cost_bps / 10_000
        trades.append(EventTrade("", i, j, direction, entry, exit_, fees))
        last_exit = j
    return trades


def run_macro_release_calendar(idea: Idea) -> EngineResult:
    p = idea.parameters or {}
    equity = (idea.instruments[0] if idea.instruments else str(p.get("equity", "SPY")))
    cost_bps = float(p.get("cost_bps", 5.0))
    enter_grid = [int(x) for x in p.get("enter_offsets", [-1, 0])]
    hold_grid = [int(x) for x in p.get("hold_days_grid", [1, 2, 3])]

    res = EngineResult(idea_id=idea.id, template=idea.template, ok=False, lineage=LINEAGE_REAL)
    try:
        # Resolve real announcement dates from FRED (needs free FRED_API_KEY).
        rid = p.get("release_id")
        if rid is None:
            rid = fred_releases.resolve_release_id(str(p.get("release_name", "Consumer Price Index")))
        rd = fred_releases.release_dates(int(rid))
        ann = pd.to_datetime(rd.dates)

        close = _load_equity(equity)
        dates_list = list(close.index)
        pos_after = {}  # announcement date -> first trading day index >= it
        for a in ann:
            later = close.index[close.index >= a]
            if len(later):
                pos_after[a] = dates_list.index(later[0])
        reaction = sorted(set(pos_after.values()))
        # keep only announcements overlapping the price history
        reaction = [r for r in reaction if 2 <= r < len(dates_list) - max(hold_grid) - 1]
        if len(reaction) < 20:
            res.error = (f"only {len(reaction)} announcement reaction days overlap "
                         f"{equity} history (release {rid})")
            return res

        n = len(reaction)
        split = int(n * 0.7)
        is_events, oos_events = reaction[:split], reaction[split:]

        grid, best, dir_by_cell = [], None, {}
        for enter_off in enter_grid:
            for hold in hold_grid:
                best_dir, best_pnl, best_n = 0, -np.inf, 0
                for direction in (1, -1):
                    tr = _announcement_trades(is_events, close, enter_off, hold, direction, cost_bps)
                    if not tr:
                        continue
                    m = summarize(tr)
                    if m["net_pnl"] > best_pnl:
                        best_pnl, best_dir, best_n = m["net_pnl"], direction, m["n_trades"]
                if best_dir == 0:
                    continue
                key = (enter_off, hold)
                dir_by_cell[key] = best_dir
                grid.append(GridCell(params={"enter_offset": enter_off, "hold_days": hold},
                                     score=float(best_pnl), n_trades=int(best_n)))
                if best is None or best_pnl > best.score:
                    best = grid[-1]
        if best is None:
            res.error = "no in-sample announcement trades produced by any config"
            return res

        res.in_sample_grid = grid
        key = (best.params["enter_offset"], best.params["hold_days"])
        direction = dir_by_cell[key]
        res.chosen_params = dict(best.params)
        res.in_sample_metrics = {"net_pnl": best.score, "n_trades": best.n_trades,
                                 "direction": direction}
        oos_trades = _announcement_trades(oos_events, close, best.params["enter_offset"],
                                          best.params["hold_days"], direction, cost_bps)
        oos_sorted = sorted(oos_trades, key=lambda t: t.entry_tick)
        oos_dates = [dates_list[t.entry_tick] for t in oos_sorted]
        m = summarize(oos_sorted)
        if oos_sorted:
            m["sharpe"] = _sharpe_from_dates(oos_sorted, oos_dates)
        res.oos_metrics = m
        res.n_oos_trades = int(m.get("n_trades", 0))
        res.premise_check = {"release_id": int(rid), "n_announcements": n,
                             "equity": equity, "dates_source": rd.source}
        res.notes.append(f"FRED release {rid} ({rd.source}) {n} real announcement dates vs "
                         f"{equity}; frozen enter_offset={best.params['enter_offset']}, "
                         f"hold={best.params['hold_days']}d, dir={'+1' if direction>0 else '-1'}")
        res.ok = True
    except fred_releases.MissingKey as e:
        res.error = f"needs FRED_API_KEY: {e}"
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


# --------------------------------------------------------------------------- #
# Engine 4: pairs stat-arb (reconstructed) -- best pair chosen in-sample, frozen
# --------------------------------------------------------------------------- #
def run_pairs_stat_arb(idea: Idea) -> EngineResult:
    p = idea.parameters or {}
    symbols = idea.instruments or ["XLE", "XOP", "USO"]
    window_grid = [int(x) for x in p.get("window_grid", [20, 40, 60])]
    entry_grid = [float(x) for x in p.get("entry_z_grid", [1.5, 2.0, 2.5])]
    exit_z = float(p.get("exit_z", 0.5))
    cost_bps = float(p.get("cost_bps", 10.0))
    max_pairs = int(p.get("max_pairs", 15))

    res = EngineResult(idea_id=idea.id, template=idea.template, ok=False, lineage=LINEAGE_REAL)
    try:
        panel = load_panel(symbols)
        dates = pd.DatetimeIndex(panel.dates)
        N = len(dates)
        split = int(N * 0.7)
        candidate_pairs = list(combinations(panel.symbols, 2))[:max_pairs]

        # In-sample: choose (pair, window, entry_z) maximising in-sample net PnL.
        # beta is estimated on in-sample logs only, then frozen.
        grid, best = [], None
        best_meta = None
        for a, b in candidate_pairs:
            la, lb = panel.log_close[a], panel.log_close[b]
            beta = hedge_ratio(la[:split], lb[:split])
            spread = spread_series(la, lb, beta)
            for window in window_grid:
                z = rolling_z(spread, window)
                for entry_z in entry_grid:
                    cfg = PairConfig(window=window, entry_z=entry_z, exit_z=exit_z,
                                     cost_bps=cost_bps)
                    tr = run_pairs_strategy(spread, z, cfg, window, split, label=f"{a}/{b}")
                    if len(tr) < 3:
                        continue
                    m = summarize(tr)
                    # grid params include the pair so different pairs are distinct cells;
                    # robustness neighbours are judged on numeric window/entry_z only.
                    cell = GridCell(params={"window": window, "entry_z": entry_z},
                                    score=float(m["net_pnl"]), n_trades=int(m["n_trades"]))
                    if best is None or m["net_pnl"] > best.score:
                        best = cell
                        best_meta = (a, b, beta, window, entry_z, spread, z)
        if best is None or best_meta is None:
            res.error = "no in-sample pairs trades produced by any pair/config"
            return res

        a, b, beta, window, entry_z, spread, z = best_meta
        # Build the robustness grid across window/entry_z for the CHOSEN pair only.
        for w in window_grid:
            zz = rolling_z(spread, w)
            for ez in entry_grid:
                cfg = PairConfig(window=w, entry_z=ez, exit_z=exit_z, cost_bps=cost_bps)
                tr = run_pairs_strategy(spread, zz, cfg, w, split, label=f"{a}/{b}")
                if len(tr) < 3:
                    continue
                m = summarize(tr)
                grid.append(GridCell(params={"window": w, "entry_z": ez},
                                     score=float(m["net_pnl"]), n_trades=int(m["n_trades"])))
        res.in_sample_grid = grid
        res.chosen_params = {"pair": f"{a}/{b}", "beta": round(beta, 4),
                             "window": window, "entry_z": entry_z}

        cfg = PairConfig(window=window, entry_z=entry_z, exit_z=exit_z, cost_bps=cost_bps)
        is_trades = run_pairs_strategy(spread, z, cfg, window, split, label=f"{a}/{b}")
        res.in_sample_metrics = summarize(is_trades)

        oos_trades = run_pairs_strategy(spread, z, cfg, split, N, label=f"{a}/{b}")
        oos_sorted = sorted(oos_trades, key=lambda t: t.entry_tick)
        oos_dates = [dates[t.entry_tick] for t in oos_sorted]
        m = summarize(oos_sorted)
        if oos_sorted:
            m["sharpe"] = _sharpe_from_dates(oos_sorted, oos_dates)
        res.oos_metrics = m
        res.n_oos_trades = int(m.get("n_trades", 0))
        res.premise_check = {"chosen_pair": f"{a}/{b}", "hedge_beta": round(beta, 4),
                             "n_candidate_pairs": len(candidate_pairs),
                             "common_days": N}
        res.notes.append(f"best in-sample pair {a}/{b} (beta={beta:.3f}) frozen and "
                         f"evaluated OOS once; window={window}, entry_z={entry_z}")
        res.notes.append(f"{len(candidate_pairs)} candidate pair(s) searched in-sample only")
        res.ok = True
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
    return res


ENGINES = {
    "event_window_earnings": run_event_window_earnings,
    "macro_release_drift": run_macro_release_drift,
    "macro_release_calendar": run_macro_release_calendar,
    "pairs_stat_arb": run_pairs_stat_arb,
}

# Templates recognised but intentionally not runnable yet (credential/data gated).
DEFERRED_TEMPLATES = {
    "vol_surface_mispricing": "IBKR OPTION_IMPLIED_VOLATILITY -- needs TWS/Gateway connected",
    "order_flow_imbalance": "LOBSTER L2 -- free samples too thin for a real OOS split",
    "news_sentiment": "Alpaca news feed -- needs API key",
}


def run_idea(idea: Idea) -> EngineResult:
    engine = ENGINES.get(idea.template)
    if engine is None:
        reason = DEFERRED_TEMPLATES.get(idea.template, "no engine registered for this template")
        return EngineResult(idea_id=idea.id, template=idea.template, ok=False,
                            lineage=LINEAGE_REAL, error=f"deferred/unknown template: {reason}")
    return engine(idea)
