"""Real macro data from FRED -- free and KEYLESS.

FRED exposes every series as a plain CSV at
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>
which needs no API key and no registration. That is the endpoint used here.
(The JSON `api.stlouisfed.org` endpoints *do* require a key; we deliberately
avoid them so the harness has a genuinely no-key macro source, matching the
project's "what's actually free" data plan.)

We prefer daily *market-observed* series (Treasury constant-maturity yields
from the H.15 release: DGS2, DGS10, T10Y2Y, DFF ...). These matter for two
honesty reasons:

- They are essentially not revised (unlike CPI/GDP vintages), so indexing a
  value by its observation date is not a look-ahead cheat.
- Their business-day timestamp is the day the market saw the number, so a
  large day-over-day change is a real, dated "macro shock" event we can line
  up against an equity close without pretending to know an announcement's
  intraday timing.

Anything indexed by a data *reference* period that is released with a lag
(CPI, payrolls) is NOT safe to treat this way without release-date vintages,
and this module intentionally does not pretend otherwise -- see the guard in
`load_series`.
"""
from __future__ import annotations

import csv
import io
import os
import time
import urllib.request
from dataclasses import dataclass

import numpy as np
import pandas as pd

FREDGRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

# Series we consider safe to treat as "dated the day the market saw it":
# daily, market-observed, effectively unrevised. Extend deliberately, not
# casually -- a series indexed by lagged reference period does not belong here.
MARKET_DAILY_SAFE = {
    "DGS1", "DGS2", "DGS5", "DGS10", "DGS30",  # constant-maturity Treasury yields
    "T10Y2Y", "T10Y3M",                         # yield-curve spreads
    "DFF", "DFEDTARU", "DFEDTARL",              # effective / target fed funds
    "DTWEXBGS",                                  # broad dollar index
    "VIXCLS",                                     # CBOE VIX (market-observed)
    "BAMLH0A0HYM2",                              # high-yield OAS
}

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "state", "fred_cache")


@dataclass
class FredSeries:
    series_id: str
    dates: np.ndarray        # np.datetime64[D], ascending, missing values dropped
    values: np.ndarray       # float
    source: str              # "fred:live" or "fred:cache"


def _cache_path(sid: str) -> str:
    return os.path.join(_CACHE_DIR, f"{sid}.csv")


def _fetch_csv(sid: str, timeout: float) -> str:
    url = FREDGRAPH_URL.format(sid=sid)
    req = urllib.request.Request(url, headers={"User-Agent": "discovery-harness/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed FRED host)
        return resp.read().decode("utf-8")


def _parse_csv(text: str, sid: str) -> tuple[np.ndarray, np.ndarray]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValueError(f"FRED returned empty CSV for {sid}")
    header = [h.strip().lower() for h in rows[0]]
    # FRED uses either "DATE" (older) or "observation_date" (current) as col 0.
    date_col = 0
    val_col = 1 if len(header) > 1 else 0
    dates, vals = [], []
    for r in rows[1:]:
        if len(r) <= val_col:
            continue
        d, v = r[date_col].strip(), r[val_col].strip()
        if not d or v in ("", "."):   # "." is FRED's missing-value marker
            continue
        try:
            vals.append(float(v))
        except ValueError:
            continue
        dates.append(np.datetime64(d))
    if not dates:
        raise ValueError(f"FRED CSV for {sid} had no usable observations")
    order = np.argsort(np.array(dates))
    return np.array(dates)[order], np.array(vals, dtype=float)[order]


def load_series(sid: str, *, allow_stale_days: int = 7, timeout: float = 20.0,
                require_market_daily: bool = True) -> FredSeries:
    """Load a FRED series by id, keylessly, with a local CSV cache.

    `require_market_daily=True` refuses ids not in MARKET_DAILY_SAFE, because
    treating a lag-released reference-period series as if it were known on its
    reference date would be a look-ahead bug. Pass False only if you have
    separately handled release timing.
    """
    sid = sid.strip().upper()
    if require_market_daily and sid not in MARKET_DAILY_SAFE:
        raise ValueError(
            f"FRED series {sid!r} is not in the market-daily-safe set. Using a "
            f"lag-released series (CPI/payrolls/GDP) as if known on its reference "
            f"date is look-ahead bias. Add it to MARKET_DAILY_SAFE only if it is "
            f"daily + market-observed + effectively unrevised, or pass "
            f"require_market_daily=False after handling release dates yourself.")

    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _cache_path(sid)
    fresh_cache = os.path.exists(path) and (time.time() - os.path.getmtime(path)) < allow_stale_days * 86400

    if fresh_cache:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        source = "fred:cache"
    else:
        try:
            text = _fetch_csv(sid, timeout)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            source = "fred:live"
        except Exception:
            if os.path.exists(path):  # fall back to any cached copy on network failure
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                source = "fred:cache(stale)"
            else:
                raise

    dates, values = _parse_csv(text, sid)
    return FredSeries(series_id=sid, dates=dates, values=values, source=source)


def shock_days(series: FredSeries) -> pd.Series:
    """Absolute day-over-day change of the series, indexed by observation date.
    This is the raw material for a macro-shock event definition; the threshold
    that turns a change into an 'event' is tuned in-sample by the engine, never
    fixed here."""
    s = pd.Series(series.values, index=pd.DatetimeIndex(series.dates)).sort_index()
    return s.diff().abs().dropna()
