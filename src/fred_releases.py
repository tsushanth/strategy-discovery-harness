"""Authoritative macro-announcement DATES from FRED's releases API.

Unlike `fred_data.py` (keyless series CSVs), the release-*dates* endpoint needs
a FRED API key. The key is free (https://fredaccount.stlouisfed.org/apikeys)
but it is a key, so this module is deliberately KEY-OPTIONAL:

- with a key (env FRED_API_KEY, or passed in), it fetches the REAL historical
  publication dates of a release (CPI, Employment Situation/NFP, GDP, ...);
- without a key it raises `MissingKey` and the engine declines to run. It does
  NOT fall back to a hand-typed date table, because fabricating announcement
  dates is exactly the kind of made-up "data" this project forbids.

Why authoritative dates matter: a scheduled-announcement drift study is only
valid if each event is dated the day the market actually saw the release. FRED
publishes those real publication dates; we use them rather than guessing.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

_API = "https://api.stlouisfed.org/fred/{path}"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "state", "fred_cache")


class MissingKey(RuntimeError):
    pass


def _key(explicit: str | None) -> str:
    k = explicit or os.environ.get("FRED_API_KEY", "").strip()
    if not k:
        raise MissingKey(
            "FRED release dates need a (free) FRED API key. Set FRED_API_KEY to "
            "run the macro_release_calendar engine on real announcement dates. "
            "Refusing to fabricate a date table.")
    return k


def _get(path: str, params: dict, key: str, timeout: float = 20.0) -> dict:
    params = {**params, "api_key": key, "file_type": "json"}
    url = _API.format(path=path) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "discovery-harness/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed FRED host)
        return json.loads(resp.read().decode("utf-8"))


def resolve_release_id(name: str, api_key: str | None = None) -> int:
    """Look up a FRED release id by (case-insensitive substring) name via the
    API, so we don't hardcode ids that could be wrong."""
    key = _key(api_key)
    data = _get("releases", {"limit": 1000}, key)
    name_l = name.strip().lower()
    matches = [(r["id"], r["name"]) for r in data.get("releases", [])
               if name_l in r["name"].lower()]
    if not matches:
        raise ValueError(f"no FRED release matching {name!r}")
    # Prefer an exact-ish match (shortest name containing the query).
    matches.sort(key=lambda m: len(m[1]))
    return int(matches[0][0])


@dataclass
class ReleaseDates:
    release_id: int
    dates: list[str]     # ISO 'YYYY-MM-DD', ascending, real publication dates
    source: str


def release_dates(release_id: int, api_key: str | None = None,
                  start: str = "2005-01-01", allow_stale_days: int = 30) -> ReleaseDates:
    """Real historical publication dates for a FRED release, cached locally."""
    key = _key(api_key)
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = os.path.join(_CACHE_DIR, f"release_{release_id}_dates.json")
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < allow_stale_days * 86400:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        source = "fred:cache"
    else:
        try:
            data = _get("release/dates",
                        {"release_id": release_id, "sort_order": "asc", "limit": 10000,
                         "realtime_start": start,
                         "include_release_dates_with_no_data": "false"},
                        key)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            source = "fred:live"
        except Exception:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                source = "fred:cache(stale)"
            else:
                raise
    dates = sorted({d["date"] for d in data.get("release_dates", []) if d.get("date", "") >= start})
    return ReleaseDates(release_id=release_id, dates=dates, source=source)
