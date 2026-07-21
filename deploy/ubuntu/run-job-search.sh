#!/usr/bin/env bash
# Wrapper for scheduled job_search runs. Safe to invoke repeatedly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/run-task.sh" job_search
