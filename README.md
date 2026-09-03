# Strategy-discovery harness

A small autonomous harness that **generates trading hypotheses (LLM), backtests
them for real (deterministic), and lets measured out-of-sample numbers — never
an LLM's opinion — decide what gets promoted.**

Part of the same market-making portfolio as `matching-engine`,
`gpu-options-pricer`, `mm-backtester`, `alpaca-paper-trader`, and
`ibkr-paper-trader`. It sits on top of the event-window strategy's
walk-forward machinery (`src/event_data.py`, `src/event_strategy.py`,
`src/metrics.py` — carried over from
[event-window-liquidity-strategy](https://github.com/tsushanth/event-window-liquidity-strategy),
where that strategy still lives standalone) and reuses its discipline
for every new engine added here. Originally built on a branch of that
repo, then split into this dedicated repo since the harness is a
general system, not specific to one strategy.

## Why it's built the way it is

Business ideas get scored by an LLM rubric because there's no ground truth for
"good idea." **Trading strategies have ground truth** — a walk-forward OOS
Sharpe is a real number. So here the LLM only *proposes*; deterministic code
*measures and ranks*. No idea's text can move it up the leaderboard.

## Pipeline

```
scripts/generate_ideas.sh      state/ideas.json        src/run_harness_cycle.py
  claude -p, JSON schema,  ─►   (hypotheses only,  ─►    route by template →
  no quality scores,           no perf claims)          real-data backtest →
  budget-capped                                         structural gates → rank
                                                              │
                        state/leaderboard.md  ◄──────────────┤
                        state/results.json    ◄──────────────┤
                        state/paper_candidates.json ◄─────────┘  (only gate-clearers)
```

## Engines (`src/backtest_engines.py`)

| template | data (all real) | what it tests | lineage |
|----------|-----------------|---------------|---------|
| `event_window_earnings` | yfinance daily bars + `get_earnings_dates` | fade the earnings reaction-day move, hold N days | **proxy** (mean-reversion stands in for a wide-quote fill — no order-book data) |
| `macro_release_drift` | **FRED (keyless) DGSx/DFF/… + yfinance ETF** | equity drift in the days after a large daily macro-rate shock | **real** |
| `macro_release_calendar` | **FRED release-dates API (free key) + yfinance ETF** | equity drift into/out of a real scheduled announcement (CPI, NFP, …) | **real** |
| `pairs_stat_arb` | yfinance daily closes (basket) | mean-reversion of a hedged cointegration spread; best pair chosen in-sample | **real** |

All: grid-search parameters on the first ~70% of events chronologically, then
evaluate the frozen config **once** on the final ~30%. Costs are charged on
every trade and are **not** a tuned parameter (so tuning can't manufacture edge
by shrinking costs).

### The new FRED engine, honestly scoped

- **Keyless.** Uses `fred.stlouisfed.org/graph/fredgraph.csv?id=…` — no API key,
  no registration. (`src/fred_data.py`, cached under `state/fred_cache/`.)
- **Only market-observed daily series** (DGS2, DGS10, T10Y2Y, DFF, VIXCLS, …).
  A guard **refuses** lag-released reference-period series (CPI, payrolls, GDP),
  because treating them as known on their reference date is look-ahead bias.
- **Non-overlapping trades.** A shock landing while a prior position is open is
  skipped. This matters a lot: the naive overlapping version reported an OOS
  Sharpe of **1.40** for 2Y→SPY; forcing independent trades dropped it to
  **0.15** — the 1.40 was an artifact of re-counting the same market move.
- **`macro_release_drift`** tests the *rate-shock reaction* (large daily moves in
  a FRED market series), which needs only keyless data and fabricates no dates.
- **`macro_release_calendar`** tests the literature's canonical
  scheduled-announcement effect (pre-FOMC drift / announcement-day premium — see
  `docs/research/strategy_literature.md`) on **real publication dates** pulled
  from FRED's release-dates API. That endpoint needs a **free** FRED API key
  (`FRED_API_KEY`); without one the engine **declines to run** rather than
  fabricate a date table (`src/fred_releases.py` raises `MissingKey`). The
  engine resolves release ids by name via the API, so ids aren't hardcoded.

### Pairs stat-arb (`pairs_stat_arb`)

Hedged log-price spread `logA − β·logB`, `β` estimated on in-sample data and
**frozen**; a **causal** rolling z-score is the signal; trades are
non-overlapping (enter when `|z| ≥ entry_z`, exit on reversion). Across a basket
it searches all candidate pairs **in-sample only**, freezes the single best
`(pair, β, window, entry_z)`, and evaluates it OOS once — so pair selection can't
peek at the held-out window.

### Deferred engines (credential/data gated — stubs, not implemented)

`vol_surface_mispricing` (IBKR IV, needs TWS/Gateway) · `order_flow_imbalance`
(LOBSTER L2, free samples too thin for an OOS split) · `news_sentiment` (Alpaca
news, needs key). Registered in `DEFERRED_TEMPLATES`, shown on the leaderboard as
not-run so the gap is visible rather than silent.

## Structural gates (`src/harness_gates.py`)

Rules, not prose warnings. An idea is promotable **only if all pass**:

1. **Min OOS trades** (default 30) — a great Sharpe on 13 trades is not evidence.
2. **Positive OOS net PnL.**
3. **Min OOS Sharpe** (default 1.0).
4. **Implausible-Sharpe exclusion** — `|Sharpe| ≥ 5` is auto-flagged as a likely
   artifact and *excluded*, never celebrated.
5. **In-sample robustness** — the winning config's in-sample score must be a
   reasonable fraction of its parameter neighbours' average; a lone lucky spike
   fails. (Not assessed, not failed, when there are too few neighbours.)

Ranking is by measured OOS Sharpe then net PnL, eligible ideas first.

## Current real result (reproduce with `python src/run_harness_cycle.py --force`)

All three seed ideas **correctly fail** the gates; `paper_candidates.json` is
empty — the right default on weak evidence:

| idea | OOS n | OOS Sharpe | OOS net PnL | rejected because |
|------|-------|-----------|-------------|------------------|
| pairs XOP/USO | 14 | 2.69 | +0.42 | **too few OOS trades** (14 < 30) |
| macro 10Y→XLU | 98 | 0.22 | +2.52 | Sharpe < 1.0 |
| macro 2Y→SPY | 71 | 0.19 | +24.6 | Sharpe < 1.0 |
| earnings small-cap | 38 | −0.01 | −0.29 | negative PnL, Sharpe < 1.0 |
| CPI→SPY (calendar) | — | — | — | did not run: needs `FRED_API_KEY` |

The pairs row is instructive: a 2.69 Sharpe / 79% win rate looks great until you
see it's **14 trades** — exactly the tiny-sample mirage the min-trades gate
exists to reject. (yfinance/FRED data refreshes daily, so exact numbers drift a
little run to run.) The base earnings null is unchanged and expected — earnings
give ~4 events/yr/symbol, so OOS counts stay small (see `README.md`).

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q                     # 35 offline tests
python src/run_harness_cycle.py --force        # real yfinance + FRED backtests
# to run the announcement-calendar engine on real dates, add a free FRED key:
FRED_API_KEY=... python src/run_harness_cycle.py --force

# full autonomous cycle (LLM generation + backtest), budget-capped:
IDEAS_PER_CYCLE=4 MAX_BUDGET_USD=2 ./scripts/run_cycle.sh
# cheap backtest-only refresh, no LLM cost:
SKIP_GENERATION=1 ./scripts/run_cycle.sh
```

**Cadence:** full generation+backtest ~3×/week after market close; optional
`SKIP_GENERATION=1` refresh on other days. Engines use daily bars + earnings/
macro dates, so intraday runs would mostly waste quota.

## Honesty invariants (enforced structurally, not by good intentions)

- Only report numbers a backtest actually produced; a too-good number
  (`Sharpe ≥ 5`) is a red flag to investigate, not an achievement.
- Real data only; every result is tagged `real` / `proxy` / `synthetic`, and
  synthetic-lineage results can never be promoted.
- The winning in-sample config is checked for lone-spike overfitting before it's
  eligible.
