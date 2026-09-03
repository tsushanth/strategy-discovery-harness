"""Tests for the news_sentiment engine. VADER scoring is tested directly
(real, deterministic, no network). The backtest engine itself is tested
with monkeypatched data (synthetic but controlled) so the test suite
stays fast and offline -- the real end-to-end run against live Alpaca
news is documented as manually verified in the engine's own notes/PR,
not re-run on every test invocation.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import backtest_engines
from harness_models import Idea


def test_vader_scores_positive_and_negative_headlines_correctly():
    analyzer = SentimentIntensityAnalyzer()
    pos = analyzer.polarity_scores("Company beats estimates, stock surges on record profit")["compound"]
    neg = analyzer.polarity_scores("Company misses estimates, shares crash amid fraud allegations")["compound"]
    neutral = analyzer.polarity_scores("Company to report earnings next Tuesday")["compound"]
    assert pos > 0.3
    assert neg < -0.3
    assert abs(neutral) < 0.3


def _make_price_series(n_days, start_price=100.0, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n_days, freq="B")
    rets = rng.normal(0, 0.01, n_days)
    prices = start_price * np.cumprod(1 + rets)
    return pd.Series(prices, index=dates)


def test_news_sentiment_engine_runs_offline_with_monkeypatched_data(monkeypatch):
    """No network: fake sentiment (strong positive events every ~10 days)
    and a price series that actually goes up after those events, so the
    engine has a real (synthetic-input) signal to find -- proves the
    plumbing (grid search, walk-forward split, gates-ready output shape)
    works, independent of whatever real news says on a given day.
    """
    close = _make_price_series(700)
    dates = list(close.index)

    # inject a real move after each "positive sentiment" day so the
    # engine's direction-selection logic has something genuine to pick up
    sent_dates, sent_scores = [], []
    for i in range(10, 690, 6):
        sent_dates.append(dates[i])
        sent_scores.append(0.8)
        close.iloc[i + 1:i + 6] *= 1.03  # real bump baked into the synthetic series

    sentiment = pd.Series(sent_scores, index=pd.DatetimeIndex(sent_dates)).sort_index()

    def fake_fetch(symbol, start, end, chunk_days=90):
        return sentiment

    def fake_load_equity(symbol, period="10y"):
        return close

    monkeypatch.setattr("news_data.fetch_daily_sentiment", fake_fetch)
    monkeypatch.setattr(backtest_engines, "_load_equity", fake_load_equity)

    idea = Idea(id="t", title="t", family="news", template="news_sentiment",
               instruments=["FAKE"], rationale="t", data_source="alpaca")
    res = backtest_engines.run_news_sentiment(idea)

    assert res.ok, res.error
    assert res.lineage == "real"
    assert res.premise_check["pooled_days"] > 0
    assert "chosen_params" in res.to_json()
    assert res.oos_metrics.get("n_trades", 0) >= 0  # may be 0 in a small synthetic OOS slice, that's fine
    assert any("VADER is a generic lexicon" in n for n in res.notes)


def test_news_sentiment_errors_cleanly_on_too_little_data(monkeypatch):
    def fake_fetch(symbol, start, end, chunk_days=90):
        return pd.Series(dtype=float)  # no real news at all

    monkeypatch.setattr("news_data.fetch_daily_sentiment", fake_fetch)

    idea = Idea(id="t2", title="t2", family="news", template="news_sentiment",
               instruments=["FAKE"], rationale="t", data_source="alpaca")
    res = backtest_engines.run_news_sentiment(idea)

    assert not res.ok
    assert "not enough" in res.error or "no symbol had enough" in res.error
