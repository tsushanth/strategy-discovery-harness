"""Shared data models for the strategy-discovery harness.

The harness pipeline is: LLM proposes `Idea`s (hypotheses only, no quality
scores) -> a deterministic backtest engine turns each into an `EngineResult`
measured on real data with strict walk-forward discipline -> structural
`gate`s (rules, never an LLM opinion) decide eligibility and rank.

Nothing in here scores an idea by "quality". The only numbers that matter
are the ones a backtest actually measured out-of-sample.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# Data lineage tags -- surfaced prominently everywhere a result is reported,
# because "real data only where available; when forced to fall back to
# synthetic/proxy data, say so explicitly and prominently" is a project norm.
LINEAGE_REAL = "real"        # every input series was fetched from a real source
LINEAGE_PROXY = "proxy"      # real data, but the traded signal is a stand-in for
                             # the literal strategy (e.g. mean-reversion proxying
                             # for a wide-quote fill we have no order-book data for)
LINEAGE_SYNTHETIC = "synthetic"  # any fabricated/simulated input -- should be rare
                                 # and never silently promoted


@dataclass
class Idea:
    """A single trading hypothesis. Produced by the LLM stage; it carries NO
    expected-return / Sharpe / win-rate claim by design."""
    id: str
    title: str
    family: str
    template: str            # routes to a deterministic engine in backtest_engines
    instruments: list[str]
    rationale: str
    data_source: str
    parameters: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: dict) -> "Idea":
        return Idea(
            id=str(d["id"]),
            title=str(d.get("title", d["id"])),
            family=str(d.get("family", "")),
            template=str(d["template"]),
            instruments=list(d.get("instruments", [])),
            rationale=str(d.get("rationale", "")),
            data_source=str(d.get("data_source", "")),
            parameters=dict(d.get("parameters", {})),
        )


@dataclass
class GridCell:
    """One in-sample parameter configuration and the in-sample score it earned.
    Kept so the robustness gate can check the winning cell isn't a lone spike."""
    params: dict[str, Any]
    score: float             # the in-sample selection metric (net PnL by default)
    n_trades: int


@dataclass
class EngineResult:
    """What a deterministic backtest engine returns for one idea.

    `oos_metrics` is the ONLY thing ranking/gates are allowed to look at for
    the pass/fail decision (plus the in-sample grid, used purely to detect an
    overfit lone-spike selection -- never to inflate the score)."""
    idea_id: str
    template: str
    ok: bool                              # did the backtest run to completion?
    lineage: str                          # LINEAGE_REAL / PROXY / SYNTHETIC
    chosen_params: dict[str, Any] = field(default_factory=dict)
    oos_metrics: dict[str, Any] = field(default_factory=dict)   # from metrics.summarize
    n_oos_trades: int = 0
    in_sample_grid: list[GridCell] = field(default_factory=list)
    in_sample_metrics: dict[str, Any] = field(default_factory=dict)
    premise_check: dict[str, Any] = field(default_factory=dict)  # e.g. vol-spike ratios
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class GateResult:
    idea_id: str
    eligible: bool
    reasons: list[str] = field(default_factory=list)   # why rejected / accepted
    flags: list[str] = field(default_factory=list)      # non-fatal warnings (e.g. odd Sharpe)
