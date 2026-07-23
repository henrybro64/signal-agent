#!/usr/bin/env bash
# Render and install systemd units for signal-agent.
# Safe to run repeatedly (overwrites units, then daemon-reload + enable).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UNIT_SRC="${SCRIPT_DIR}/systemd"
UNIT_DST="${UNIT_DST:-/etc/systemd/system}"

DEPLOY_USER="${DEPLOY_USER:-${SUDO_USER:-${USER}}}"
DEPLOY_GROUP="${DEPLOY_GROUP:-${DEPLOY_USER}}"
SIGNAL_AGENT_HOME="${SIGNAL_AGENT_HOME:-${REPO_ROOT}}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: run with sudo so units can be installed under ${UNIT_DST}" >&2
  echo "example: sudo DEPLOY_USER=${DEPLOY_USER} SIGNAL_AGENT_HOME=${SIGNAL_AGENT_HOME} $0" >&2
  exit 1
fi

if [[ -z "${DEPLOY_USER}" || "${DEPLOY_USER}" == "root" ]]; then
  echo "error: set DEPLOY_USER to the non-root account that owns the repo and can run docker" >&2
  exit 1
fi

if ! id "${DEPLOY_USER}" >/dev/null 2>&1; then
  echo "error: user '${DEPLOY_USER}' does not exist" >&2
  exit 1
fi

mkdir -p "${SIGNAL_AGENT_HOME}/data" "${SIGNAL_AGENT_HOME}/logs"

shopt -s nullglob
units=("${UNIT_SRC}"/*.service "${UNIT_SRC}"/*.timer)
if [[ "${#units[@]}" -eq 0 ]]; then
  echo "error: no unit files found in ${UNIT_SRC}" >&2
  exit 1
fi

for src in "${units[@]}"; do
  dest="${UNIT_DST}/$(basename "${src}")"
  sed \
    -e "s|__DEPLOY_USER__|${DEPLOY_USER}|g" \
    -e "s|__DEPLOY_GROUP__|${DEPLOY_GROUP}|g" \
    -e "s|__SIGNAL_AGENT_HOME__|${SIGNAL_AGENT_HOME}|g" \
    "${src}" > "${dest}"
  echo "installed ${dest}"
done

systemctl daemon-reload
systemctl enable --now signal-agent-job-search.timer
systemctl enable --now signal-agent-tw-stock-movers.timer
systemctl enable --now signal-agent-stock-watchlist.timer
systemctl list-timers 'signal-agent-*' --no-pager
