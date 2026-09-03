#!/usr/bin/env bash
# Stage 1 of the discovery harness: LLM generates trading HYPOTHESES only.
#
# Hard rule enforced by the prompt + schema: the model proposes strategy
# families/instruments/parameters and a rationale, and is explicitly forbidden
# from claiming any expected Sharpe / return / win-rate. Ground truth comes
# from the deterministic backtest (stage 2), never from the model.
#
# Bounded by --max-budget-usd so a cron/launchd loop can't run away on cost.
# New ideas are de-duplicated against recent titles and merged into
# state/ideas.json, which run_harness_cycle.py then consumes.
#
# Env:
#   IDEAS_PER_CYCLE   how many ideas to request (default 4)
#   MAX_BUDGET_USD    hard cost cap for the single claude call (default 2)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/state"
mkdir -p "$STATE"
IDEAS_FILE="$STATE/ideas.json"

IDEAS_PER_CYCLE="${IDEAS_PER_CYCLE:-4}"
MAX_BUDGET_USD="${MAX_BUDGET_USD:-2}"

# A non-interactive shell (cron/launchd/SSH) often doesn't have `claude`
# on PATH even when it's installed and logged in for the interactive user
# session -- check the common install locations before giving up.
CLAUDE_BIN="claude"
if ! command -v claude >/dev/null 2>&1; then
  for candidate in "$HOME/.local/bin/claude" "/opt/homebrew/bin/claude"; do
    if [ -x "$candidate" ]; then CLAUDE_BIN="$candidate"; break; fi
  done
fi

if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1 && [ ! -x "$CLAUDE_BIN" ]; then
  echo "[generate_ideas] 'claude' CLI not found -- skipping generation." >&2
  echo "[generate_ideas] The harness still runs on state/ideas.json / built-in seeds." >&2
  exit 0
fi

# Only these templates have real backtest engines wired today. The model must
# choose from this list so every idea is actually testable, not aspirational.
SUPPORTED_TEMPLATES='event_window_earnings, macro_release_drift, macro_release_calendar, pairs_stat_arb'

RECENT_TITLES="$(python3 - "$IDEAS_FILE" <<'PY'
import json, sys
p = sys.argv[1]
try:
    with open(p) as f:
        data = json.load(f)
    ideas = data.get("ideas", data) if isinstance(data, dict) else data
    print("; ".join(i.get("title", "") for i in ideas[-20:]))
except Exception:
    print("")
PY
)"

SCHEMA="$(cat <<'JSON'
{
  "type": "object",
  "required": ["ideas"],
  "properties": {
    "ideas": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["id","title","family","template","instruments","rationale","data_source","parameters"],
        "properties": {
          "id":          {"type": "string"},
          "title":       {"type": "string"},
          "family":      {"type": "string"},
          "template":    {"type": "string", "enum": ["event_window_earnings","macro_release_drift","macro_release_calendar","pairs_stat_arb"]},
          "instruments": {"type": "array", "items": {"type": "string"}},
          "rationale":   {"type": "string"},
          "data_source": {"type": "string"},
          "parameters":  {"type": "object"}
        },
        "additionalProperties": false
      }
    }
  },
  "additionalProperties": false
}
JSON
)"

PROMPT="You are proposing testable quantitative trading HYPOTHESES for a solo retail
quant who trades daily bars and free data (yfinance equities/ETFs, keyless FRED
daily market series like DGS2/DGS10). Propose exactly ${IDEAS_PER_CYCLE} ideas.

Choose 'template' ONLY from: ${SUPPORTED_TEMPLATES}.
 - event_window_earnings: instruments = liquid non-mega-cap tickers with real
   earnings history; parameters may be left {} (engine grid-searches hold/spread).
 - macro_release_drift: instruments = one equity/ETF ticker; parameters must set
   'fred_series' to a keyless daily market series (DGS1/DGS2/DGS5/DGS10/DGS30/
   T10Y2Y/DFF/VIXCLS) and may set 'cost_bps'.
 - macro_release_calendar: instruments = one equity/ETF ticker; parameters set
   'release_name' (e.g. 'Consumer Price Index', 'Employment Situation') and may
   set 'cost_bps'. Needs a free FRED_API_KEY at run time.
 - pairs_stat_arb: instruments = 2+ closely-related tickers (same sector/theme);
   parameters may set 'exit_z' and 'cost_bps'.

HARD RULES:
 - Do NOT claim, estimate, or imply any expected Sharpe, return, win rate, or
   profitability. You output hypotheses; a deterministic backtest measures them.
 - No look-ahead: only propose signals computable from data available at trade time.
 - Avoid duplicating these recently-proposed ideas: ${RECENT_TITLES}
Return ONLY JSON matching the provided schema."

echo "[generate_ideas] requesting ${IDEAS_PER_CYCLE} idea(s), budget \$${MAX_BUDGET_USD}..." >&2
RAW="$("$CLAUDE_BIN" -p "$PROMPT" \
  --output-format json \
  --json-schema "$SCHEMA" \
  --max-budget-usd "$MAX_BUDGET_USD" \
  --tools "WebSearch WebFetch" 2>/dev/null || true)"

if [ -z "$RAW" ]; then
  echo "[generate_ideas] claude returned nothing (budget/timeout?) -- leaving ideas unchanged." >&2
  exit 0
fi

# Merge new ideas into state/ideas.json, de-duplicating by title (case-insensitive).
python3 - "$IDEAS_FILE" "$RAW" <<'PY'
import json, sys
ideas_file, raw = sys.argv[1], sys.argv[2]

def extract(obj):
    # claude --output-format json wraps the model result; be tolerant of shape.
    if isinstance(obj, dict) and "ideas" in obj:
        return obj["ideas"]
    for key in ("result", "content", "output"):
        if isinstance(obj, dict) and key in obj:
            try:
                inner = json.loads(obj[key]) if isinstance(obj[key], str) else obj[key]
                if isinstance(inner, dict) and "ideas" in inner:
                    return inner["ideas"]
            except Exception:
                pass
    return []

try:
    new = extract(json.loads(raw))
except Exception as e:
    print(f"[generate_ideas] could not parse claude output: {e}", file=sys.stderr)
    sys.exit(0)

try:
    with open(ideas_file) as f:
        cur = json.load(f)
    existing = cur.get("ideas", cur) if isinstance(cur, dict) else cur
except Exception:
    existing = []

seen = {i.get("title", "").strip().lower() for i in existing}
added = 0
for idea in new:
    t = idea.get("title", "").strip().lower()
    if not t or t in seen:
        continue
    seen.add(t)
    existing.append(idea)
    added += 1

with open(ideas_file, "w") as f:
    json.dump({"ideas": existing}, f, indent=2)
print(f"[generate_ideas] added {added} new idea(s); {len(existing)} total in {ideas_file}", file=sys.stderr)
PY
