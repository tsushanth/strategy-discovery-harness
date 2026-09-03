#!/usr/bin/env bash
# VPS cron wrapper for the backtest-only refresh (SKIP_GENERATION=1) --
# no LLM/claude CLI dependency, so this is the part of the harness that
# can run reliably on the always-on VPS. Idea GENERATION still needs the
# local `claude` CLI login and stays on the dev Mac's launchd jobs, which
# are less reliable (skip runs if the Mac is asleep) -- a known, accepted
# gap, not silently ignored.
set -euo pipefail
ROOT="$HOME/strategy-discovery-harness"
source "$ROOT/.env"
"$ROOT/scripts/run_cycle.sh"
