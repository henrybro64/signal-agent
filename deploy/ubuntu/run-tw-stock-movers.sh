#!/usr/bin/env bash
# Wrapper for scheduled tw_stock_movers runs. Safe to invoke repeatedly.
# The task itself skips cleanly outside Taiwan market hours.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/run-task.sh" tw_stock_movers
