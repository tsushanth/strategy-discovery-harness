"""Tests for the structural gates, ranking, and lone-spike robustness check.

These are deterministic and offline -- no network, no yfinance/FRED calls.
They test the RULES that decide eligibility, which is where the harness's
"numbers decide, not opinions" guarantee actually lives.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness_models import EngineResult, GridCell, LINEAGE_REAL, LINEAGE_SYNTHETIC
from harness_gates import GateConfig, evaluate, rank, robustness_check


def _result(sharpe=2.0, net_pnl=10.0, n=50, grid=None, chosen=None,
            ok=True, lineage=LINEAGE_REAL, error=None):
    return EngineResult(
        idea_id="x", template="t", ok=ok, lineage=lineage, error=error,
        chosen_params=chosen or {},
        oos_metrics={"sharpe": sharpe, "net_pnl": net_pnl, "n_trades": n},
        n_oos_trades=n, in_sample_grid=grid or [],
    )


def test_passes_when_all_gates_clear():
    g = evaluate(_result(sharpe=1.5, net_pnl=5.0, n=40))
    assert g.eligible, g.reasons


def test_rejects_too_few_oos_trades():
    g = evaluate(_result(sharpe=3.0, net_pnl=5.0, n=13))
    assert not g.eligible
    assert any("too few OOS trades" in r for r in g.reasons)


def test_rejects_negative_pnl():
    g = evaluate(_result(sharpe=1.5, net_pnl=-0.3, n=40))
    assert not g.eligible
    assert any("net PnL not positive" in r for r in g.reasons)


def test_rejects_low_sharpe():
    g = evaluate(_result(sharpe=0.68, net_pnl=5.0, n=40))
    assert not g.eligible
    assert any("Sharpe" in r for r in g.reasons)


def test_implausible_sharpe_is_excluded_not_celebrated():
    g = evaluate(_result(sharpe=28.0, net_pnl=999.0, n=40))
    assert not g.eligible
    assert any("IMPLAUSIBLE" in f for f in g.flags)
    assert any("implausible Sharpe" in r for r in g.reasons)


def test_failed_backtest_is_not_eligible():
    g = evaluate(_result(ok=False, error="network down"))
    assert not g.eligible
    assert any("did not complete" in r for r in g.reasons)


def test_synthetic_data_never_promoted():
    g = evaluate(_result(sharpe=2.0, net_pnl=5.0, n=40, lineage=LINEAGE_SYNTHETIC))
    assert not g.eligible


def test_robustness_flags_lone_spike():
    # winner scores high, both parameter neighbours are negative -> lone spike.
    grid = [
        GridCell({"hold_days": 5, "spread_bps": 30.0}, score=100.0, n_trades=40),
        GridCell({"hold_days": 3, "spread_bps": 30.0}, score=-5.0, n_trades=40),   # neighbour
        GridCell({"hold_days": 5, "spread_bps": 60.0}, score=-8.0, n_trades=40),   # neighbour
    ]
    r = _result(grid=grid, chosen={"hold_days": 5, "spread_bps": 30.0})
    robust, why = robustness_check(r, GateConfig())
    assert not robust, why
    assert not evaluate(r).eligible


def test_robustness_passes_when_neighbours_agree():
    grid = [
        GridCell({"hold_days": 5, "spread_bps": 30.0}, score=100.0, n_trades=40),
        GridCell({"hold_days": 3, "spread_bps": 30.0}, score=80.0, n_trades=40),
        GridCell({"hold_days": 5, "spread_bps": 60.0}, score=70.0, n_trades=40),
    ]
    r = _result(grid=grid, chosen={"hold_days": 5, "spread_bps": 30.0})
    robust, _ = robustness_check(r, GateConfig())
    assert robust
    assert evaluate(r).eligible


def test_robustness_not_assessed_without_enough_neighbours():
    grid = [GridCell({"hold_days": 5, "spread_bps": 30.0}, score=100.0, n_trades=40)]
    r = _result(grid=grid, chosen={"hold_days": 5, "spread_bps": 30.0})
    robust, why = robustness_check(r, GateConfig())
    assert robust
    assert "not assessed" in why


def test_rank_puts_eligible_first_then_by_sharpe():
    eligible_hi = _result(sharpe=2.5, net_pnl=10, n=50)
    eligible_lo = _result(sharpe=1.2, net_pnl=3, n=50)
    ineligible = _result(sharpe=4.0, net_pnl=50, n=5)  # great sharpe, too few trades
    ranked = rank([eligible_lo, ineligible, eligible_hi])
    order = [g.eligible for _, g in ranked]
    assert order[0] and order[1]        # both eligible first
    assert not order[2]                 # ineligible last despite higher sharpe
    assert ranked[0][0].oos_metrics["sharpe"] == 2.5  # higher sharpe ranks first
