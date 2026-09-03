#!/usr/bin/env bash
# Lock-protected wrapper for one full discovery-harness cycle, suitable for
# launchd/cron. Optionally generates fresh LLM hypotheses (stage 1), then runs
# the deterministic backtest+gate+rank cycle (stages 2-3).
#
# Env:
#   SKIP_GENERATION=1   run backtests only, no LLM call (cheap daily refresh)
#   IDEAS_PER_CYCLE     passed through to generate_ideas.sh (default 4)
#   MAX_BUDGET_USD      passed through to generate_ideas.sh (default 2)
#
# Recommended cadence (see HARNESS.md): full generation+backtest 3x/week after
# market close; optional SKIP_GENERATION=1 daily refresh in between.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/state"
LOCKS="$STATE/locks"
LOGS="$STATE/logs"
mkdir -p "$LOCKS" "$LOGS"
LOCK="$LOCKS/cycle.lock"
LOG="$LOGS/cycle-$(date +%Y%m%d-%H%M%S).log"

# Single-instance lock via mkdir (atomic, portable). Stale locks older than 2h
# are reclaimed so a crashed run doesn't wedge the schedule forever.
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    echo "[run_cycle] reclaiming stale lock" | tee -a "$LOG"
    rmdir "$LOCK" 2>/dev/null || true
    mkdir "$LOCK" 2>/dev/null || { echo "[run_cycle] could not acquire lock, exiting" | tee -a "$LOG"; exit 0; }
  else
    echo "[run_cycle] another cycle holds the lock, exiting" | tee -a "$LOG"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

{
  echo "[run_cycle] $(date -u +%FT%TZ) starting"
  if [ "${SKIP_GENERATION:-0}" = "1" ]; then
    echo "[run_cycle] SKIP_GENERATION=1 -- backtest refresh only"
  else
    bash "$ROOT/scripts/generate_ideas.sh" || echo "[run_cycle] generation step failed (non-fatal)"
  fi
  "$PY" "$ROOT/src/run_harness_cycle.py" --force
  echo "[run_cycle] $(date -u +%FT%TZ) done"
} 2>&1 | tee -a "$LOG"
