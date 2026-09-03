"""One deterministic discovery-harness cycle.

Loads ideas (hypotheses only), routes each to a real-data backtest engine,
applies structural gates, ranks by MEASURED out-of-sample metrics, and writes:
    state/results.json          -- every engine result (full detail)
    state/leaderboard.md        -- ranked, human-readable, gate reasons shown
    state/paper_candidates.json -- only ideas that cleared every structural gate

Nothing here consults an LLM. The LLM's only job (a separate script) is to
propose ideas; this file measures them and lets the numbers decide.

Usage:
    python src/run_harness_cycle.py [--force] [--ideas PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

_SRC = os.path.dirname(os.path.abspath(__file__))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from harness_models import Idea            # noqa: E402
from backtest_engines import run_idea, DEFERRED_TEMPLATES  # noqa: E402
from harness_gates import GateConfig, rank  # noqa: E402

_ROOT = os.path.dirname(_SRC)
_STATE = os.path.join(_ROOT, "state")

# Seed ideas used when state/ideas.json is absent, so a cycle always has
# something real to run. These are hypotheses only -- no performance claims.
SEED_IDEAS = [
    {
        "id": "seed-earnings-smallcap",
        "title": "Fade the earnings overreaction on liquid non-mega-cap names",
        "family": "event_driven",
        "template": "event_window_earnings",
        "instruments": ["ROKU", "ETSY", "PINS", "SNAP"],
        "rationale": "Realized vol spikes 2-3x through earnings; the reaction-day move "
                     "partially mean-reverts, a proxy for getting paid to quote wide.",
        "data_source": "yfinance daily bars + get_earnings_dates",
        "parameters": {},
    },
    {
        "id": "seed-macro-2y-spy",
        "title": "SPY reaction to 2Y Treasury-yield shocks",
        "family": "macro_event",
        "template": "macro_release_drift",
        "instruments": ["SPY"],
        "rationale": "Large daily moves in the policy-sensitive 2Y yield are dated macro "
                     "shocks; test whether SPY drifts predictably in the days after.",
        "data_source": "FRED DGS2 (keyless) + yfinance SPY",
        "parameters": {"fred_series": "DGS2", "cost_bps": 5.0},
    },
    {
        "id": "seed-macro-10y-xlu",
        "title": "Rate-sensitive utilities (XLU) reaction to 10Y-yield shocks",
        "family": "macro_event",
        "template": "macro_release_drift",
        "instruments": ["XLU"],
        "rationale": "Utilities are bond proxies; large 10Y-yield shocks should move XLU. "
                     "Test for a tradable multi-day drift after the shock.",
        "data_source": "FRED DGS10 (keyless) + yfinance XLU",
        "parameters": {"fred_series": "DGS10", "cost_bps": 5.0},
    },
    {
        "id": "seed-cpi-spy-drift",
        "title": "SPY drift around real CPI announcement dates",
        "family": "macro_event",
        "template": "macro_release_calendar",
        "instruments": ["SPY"],
        "rationale": "Scheduled macro releases carry an announcement premium; test whether "
                     "SPY drifts predictably into/out of the CPI print on real release dates.",
        "data_source": "FRED release dates (CPI, needs free FRED_API_KEY) + yfinance SPY",
        "parameters": {"release_name": "Consumer Price Index", "cost_bps": 5.0},
    },
    {
        "id": "seed-pairs-energy",
        "title": "Energy-ETF pairs mean-reversion (XLE/XOP/USO)",
        "family": "stat_arb",
        "template": "pairs_stat_arb",
        "instruments": ["XLE", "XOP", "USO"],
        "rationale": "Closely-related energy ETFs share a cointegrated spread; fade "
                     "stretched deviations. Best pair/config chosen in-sample only, frozen OOS.",
        "data_source": "yfinance daily closes",
        "parameters": {"exit_z": 0.5, "cost_bps": 10.0},
    },
]


def load_ideas(path: str | None) -> list[Idea]:
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        raw = raw.get("ideas", raw) if isinstance(raw, dict) else raw
        return [Idea.from_dict(d) for d in raw]
    return [Idea.from_dict(d) for d in SEED_IDEAS]


def _fmt(v, nd=2):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else str(v)


def write_leaderboard(ranked, path: str):
    lines = ["# Discovery harness leaderboard",
             f"_generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
             "",
             "Ranked by **measured out-of-sample** Sharpe then net PnL. Eligibility is "
             "decided by structural gates only (min OOS trades, min Sharpe, positive PnL, "
             "implausible-Sharpe exclusion, in-sample robustness). No LLM opinion affects rank.",
             "",
             "| # | idea | template | lineage | OOS n | OOS Sharpe | OOS net PnL | eligible | notes |",
             "|---|------|----------|---------|-------|-----------|-------------|----------|-------|"]
    for i, (r, g) in enumerate(ranked, 1):
        if not r.ok:
            lines.append(f"| {i} | {r.idea_id} | {r.template} | {r.lineage} | - | - | - | "
                         f"no | did not run: {r.error} |")
            continue
        m = r.oos_metrics
        reason = "; ".join(g.reasons) if not g.eligible else "clears gates"
        lines.append(
            f"| {i} | {r.idea_id} | {r.template} | {r.lineage} | {r.n_oos_trades} | "
            f"{_fmt(m.get('sharpe', 0.0))} | {_fmt(m.get('net_pnl', 0.0), 4)} | "
            f"{'YES' if g.eligible else 'no'} | {reason} |")
    lines.append("")
    lines.append("## Deferred templates (credential/data gated, not run)")
    for t, why in DEFERRED_TEMPLATES.items():
        lines.append(f"- `{t}` — {why}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_cycle(ideas_path: str | None = None, force: bool = False) -> dict:
    os.makedirs(_STATE, exist_ok=True)
    ideas = load_ideas(ideas_path)
    print(f"[harness] running {len(ideas)} idea(s) through real-data backtests\n")

    results = []
    for idea in ideas:
        print(f"  - {idea.id} [{idea.template}] ...", flush=True)
        r = run_idea(idea)
        if r.ok:
            m = r.oos_metrics
            print(f"      OOS n={r.n_oos_trades} sharpe={_fmt(m.get('sharpe', 0.0))} "
                  f"net_pnl={_fmt(m.get('net_pnl', 0.0), 4)} lineage={r.lineage}")
        else:
            print(f"      did not run: {r.error}")
        results.append(r)

    ranked = rank(results, GateConfig())

    with open(os.path.join(_STATE, "results.json"), "w", encoding="utf-8") as f:
        json.dump([r.to_json() for r in results], f, indent=2, default=str)
    write_leaderboard(ranked, os.path.join(_STATE, "leaderboard.md"))

    candidates = [{"idea_id": r.idea_id, "template": r.template, "lineage": r.lineage,
                   "chosen_params": r.chosen_params, "oos_metrics": r.oos_metrics,
                   "n_oos_trades": r.n_oos_trades}
                  for r, g in ranked if g.eligible]
    with open(os.path.join(_STATE, "paper_candidates.json"), "w", encoding="utf-8") as f:
        json.dump(candidates, f, indent=2, default=str)

    print(f"\n[harness] {len(candidates)} idea(s) cleared all structural gates "
          f"-> state/paper_candidates.json")
    if not candidates:
        print("[harness] nothing promoted -- correct default when the evidence is weak.")
    return {"n_ideas": len(ideas), "n_candidates": len(candidates)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ideas", default=os.path.join(_STATE, "ideas.json"),
                    help="path to ideas JSON (falls back to built-in seeds if missing)")
    ap.add_argument("--force", action="store_true", help="run even if outputs look fresh")
    args = ap.parse_args()
    run_cycle(args.ideas, force=args.force)


if __name__ == "__main__":
    main()
