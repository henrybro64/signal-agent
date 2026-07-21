#!/usr/bin/env bash
# Run a signal-agent task via Docker Compose.
# Safe to invoke repeatedly (idempotent wrapper; task logic owns dedup).
# Overlapping invocations for the same task exit 0 so cron/systemd do not stack.
set -euo pipefail

TASK_NAME="${1:-}"
if [[ -z "${TASK_NAME}" ]]; then
  echo "usage: $0 <task_name>" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

mkdir -p data logs

if [[ ! -f docker-compose.yml ]]; then
  echo "error: docker-compose.yml not found in ${REPO_ROOT}" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "error: .env is missing; copy .env.example to .env and fill secrets" >&2
  exit 1
fi

LOCK_FILE="${REPO_ROOT}/data/.${TASK_NAME}.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"${LOCK_FILE}"
  if ! flock -n 9; then
    echo "skipping ${TASK_NAME}: another run is already in progress" >&2
    exit 0
  fi
else
  echo "warning: flock not found; overlapping runs are not locked" >&2
fi

exec docker compose run --rm -T signal-agent \
  python main.py --task "${TASK_NAME}" --config /app/config.yaml
