"""Real news headlines (Alpaca's news feed -- same API key already used
for trading/prices, no separate credential needed despite the harness's
earlier assumption) scored with VADER, a standard, deterministic,
lexicon-based sentiment tool (not finance-tuned -- a real limitation,
noted in the engine's output, not hidden).
"""
import os
from datetime import datetime, timedelta

import pandas as pd
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


def load_alpaca_credentials():
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY/ALPACA_SECRET_KEY not set")
    return key, secret


def fetch_daily_sentiment(symbol: str, start: datetime, end: datetime,
                          chunk_days: int = 90) -> pd.Series:
    """Real headlines for `symbol` from Alpaca's news feed, scored with
    VADER's compound score per headline, averaged per calendar day.
    Returns a Series indexed by date (only days with >=1 real article --
    no interpolation, no fabricated quiet days).

    Walks the requested range in `chunk_days`-sized windows rather than
    trusting `next_page_token` across the whole range at once -- found
    by testing this for real: a wide range (e.g. 2018-now) silently
    returns only the most recent ~50 articles with `next_page_token`
    already None, while the exact same symbol with an explicit narrow
    end date correctly returns real older history. Chunking sidesteps
    whatever causes that rather than trusting the token end-to-end.
    """
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest

    key, secret = load_alpaca_credentials()
    client = NewsClient(key, secret)

    rows = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end)
        page_token = None
        while True:
            req = NewsRequest(symbols=symbol, start=chunk_start, end=chunk_end,
                              limit=50, page_token=page_token)
            resp = client.get_news(req)
            articles = resp.data.get("news", [])
            for a in articles:
                score = _analyzer.polarity_scores(a.headline)["compound"]
                rows.append((pd.Timestamp(a.created_at).tz_localize(None).normalize(), score))
            page_token = resp.next_page_token
            if not page_token or not articles:
                break
        chunk_start = chunk_end

    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["date", "score"])
    return df.groupby("date")["score"].mean().sort_index()
