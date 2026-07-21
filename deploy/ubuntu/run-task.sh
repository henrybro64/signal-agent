#!/usr/bin/env bash
# Run a signal-agent task via Docker Compose.
# Safe to invoke repeatedly (idempotent wrapper; task logic owns dedup).
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

exec docker compose run --rm signal-agent \
  python main.py --task "${TASK_NAME}" --config /app/config.yaml
