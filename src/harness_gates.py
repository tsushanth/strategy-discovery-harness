"""Structural hard gates + ranking for the discovery harness.

These are RULES, not an LLM's opinion of a strategy. They encode the project
norms structurally rather than as prose warnings:

- a minimum out-of-sample trade count before a strategy is even eligible
  (a great Sharpe on 6 trades is not evidence);
- a minimum OOS Sharpe and non-negative OOS net PnL to clear the bar;
- an *implausible* Sharpe (too-good, e.g. > 5) is auto-flagged and excluded
  as a likely artifact, never celebrated;
- a robustness check that the winning in-sample config is not a lone lucky
  spike among its parameter neighbours.

Ranking is by measured OOS metrics only. No idea's LLM-supplied text can move
it up or down the leaderboard.
"""
from __future__ import annotations

from dataclasses import dataclass

from harness_models import EngineResult, GateResult, GridCell


@dataclass
class GateConfig:
    min_oos_trades: int = 30          # below this, OOS Sharpe is too noisy to trust
    min_oos_sharpe: float = 1.0       # bar to clear for promotion eligibility
    require_positive_pnl: bool = True # OOS net PnL must be > 0
    implausible_sharpe: float = 5.0   # at/above this, treat as artifact and exclude
    # Robustness: the winning in-sample cell's score must be at least this
    # fraction of the *mean* score of its parameter neighbours. A lone spike
    # (neighbours near zero/negative while the winner is high) fails this.
    robustness_min_neighbor_ratio: float = 0.5
    min_neighbors_for_check: int = 2  # need at least this many neighbours to judge


def _neighbor_cells(grid: list[GridCell], chosen: dict) -> list[GridCell]:
    """Cells that differ from `chosen` in exactly one numeric parameter by one
    grid step. Purely structural: we look for the adjacent configs, whatever the
    parameter names are."""
    if not grid:
        return []
    # Collect the sorted distinct values seen per parameter key.
    keys = list(chosen.keys())
    values_by_key: dict[str, list] = {}
    for k in keys:
        vals = sorted({c.params.get(k) for c in grid if isinstance(c.params.get(k), (int, float))})
        values_by_key[k] = vals

    def is_one_step_off(cell: GridCell) -> bool:
        diffs = 0
        for k in keys:
            cv, chosenv = cell.params.get(k), chosen.get(k)
            if cv == chosenv:
                continue
            vals = values_by_key.get(k, [])
            if chosenv in vals and cv in vals:
                i, j = vals.index(chosenv), vals.index(cv)
                if abs(i - j) == 1:
                    diffs += 1
                    continue
            return False  # differs by more than one step in this key -> not a neighbour
        return diffs == 1

    return [c for c in grid if c.params != chosen and is_one_step_off(c)]


def robustness_check(result: EngineResult, cfg: GateConfig) -> tuple[bool, str]:
    """True if the winning in-sample config looks robust (not a lone spike).

    If there aren't enough neighbours to judge, we do NOT fail the strategy on
    robustness -- we return True but say so, so the caller can flag it."""
    grid, chosen = result.in_sample_grid, result.chosen_params
    if not grid or not chosen:
        return True, "no in-sample grid supplied; robustness not assessed"
    neighbors = _neighbor_cells(grid, chosen)
    if len(neighbors) < cfg.min_neighbors_for_check:
        return True, f"only {len(neighbors)} parameter neighbour(s); robustness not assessed"

    winner = next((c for c in grid if c.params == chosen), None)
    if winner is None:
        return True, "winning config not found in grid; robustness not assessed"

    neigh_mean = sum(c.score for c in neighbors) / len(neighbors)
    # If the winner is positive but neighbours average near zero or negative,
    # the ratio is small/negative -> a lone spike.
    if winner.score <= 0:
        return True, "winning in-sample score is non-positive; nothing to over-trust"
    if neigh_mean <= 0:
        return False, (f"lone-spike risk: winner in-sample score {winner.score:.4g} but "
                       f"neighbours average {neigh_mean:.4g} (<= 0)")
    ratio = neigh_mean / winner.score
    if ratio < cfg.robustness_min_neighbor_ratio:
        return False, (f"lone-spike risk: neighbours average only {ratio:.0%} of the "
                       f"winning in-sample score")
    return True, f"robust: neighbours average {ratio:.0%} of winning in-sample score"


def evaluate(result: EngineResult, cfg: GateConfig | None = None) -> GateResult:
    """Apply the structural gates to a single engine result."""
    cfg = cfg or GateConfig()
    gr = GateResult(idea_id=result.idea_id, eligible=False)

    if not result.ok or result.error:
        gr.reasons.append(f"backtest did not complete: {result.error or 'unknown error'}")
        return gr

    if result.lineage == "synthetic":
        gr.flags.append("SYNTHETIC DATA -- not promotable on synthetic inputs")

    n = int(result.n_oos_trades)
    sharpe = float(result.oos_metrics.get("sharpe", 0.0))
    net_pnl = float(result.oos_metrics.get("net_pnl", 0.0))

    # 1) implausible Sharpe -> artifact, exclude (a red flag, never an achievement)
    if abs(sharpe) >= cfg.implausible_sharpe:
        gr.flags.append(f"IMPLAUSIBLE Sharpe {sharpe:.2f} (>|{cfg.implausible_sharpe}|) "
                        f"-- treated as a likely backtest artifact, excluded")
        gr.reasons.append("excluded: implausible Sharpe (investigate, do not deploy)")
        return gr

    # 2) minimum OOS trade count
    if n < cfg.min_oos_trades:
        gr.reasons.append(f"too few OOS trades: {n} < {cfg.min_oos_trades}")

    # 3) positive OOS net PnL
    if cfg.require_positive_pnl and net_pnl <= 0:
        gr.reasons.append(f"OOS net PnL not positive: {net_pnl:.4g}")

    # 4) minimum OOS Sharpe
    if sharpe < cfg.min_oos_sharpe:
        gr.reasons.append(f"OOS Sharpe {sharpe:.2f} < {cfg.min_oos_sharpe}")

    # 5) in-sample robustness (lone-spike detection)
    robust, why = robustness_check(result, cfg)
    if not robust:
        gr.reasons.append(why)
    else:
        gr.flags.append(why)

    if result.lineage == "synthetic":
        # already flagged; never eligible on synthetic data
        gr.reasons.append("excluded: synthetic data lineage")

    gr.eligible = len(gr.reasons) == 0
    if gr.eligible:
        gr.reasons.append("clears all structural gates")
    return gr


def rank(results: list[EngineResult], cfg: GateConfig | None = None
         ) -> list[tuple[EngineResult, GateResult]]:
    """Return every (result, gate) pair, ELIGIBLE ones first, each group sorted
    by measured OOS Sharpe then OOS net PnL. Ranking never consults idea text."""
    cfg = cfg or GateConfig()
    paired = [(r, evaluate(r, cfg)) for r in results]

    def sort_key(pair):
        r, g = pair
        return (
            0 if g.eligible else 1,
            -float(r.oos_metrics.get("sharpe", 0.0)),
            -float(r.oos_metrics.get("net_pnl", 0.0)),
        )

    return sorted(paired, key=sort_key)
