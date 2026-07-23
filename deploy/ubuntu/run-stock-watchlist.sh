#!/usr/bin/env bash
# Wrapper for scheduled stock_watchlist runs. Safe to invoke repeatedly.
# The task itself skips cleanly outside Taiwan market hours.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/run-task.sh" stock_watchlist
