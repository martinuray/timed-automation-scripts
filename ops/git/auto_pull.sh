#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_NAME="$(basename "${REPO_DIR}")"

LOG_DIR="${REPO_DIR}/logs/git"
LOG_FILE="${LOG_DIR}/auto_pull.log"
LOCK_FILE="/tmp/${REPO_NAME}-auto-pull.lock"

mkdir -p "${LOG_DIR}"

log() {
  printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S%z')" "$*" >> "${LOG_FILE}"
}

# Prevent overlapping runs from cron.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "skip: another run is in progress"
  exit 0
fi

if ! git -C "${REPO_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "error: ${REPO_DIR} is not a git repository"
  exit 1
fi

BRANCH="$(git -C "${REPO_DIR}" rev-parse --abbrev-ref HEAD)"
if [[ "${BRANCH}" == "HEAD" ]]; then
  log "skip: detached HEAD"
  exit 0
fi

if ! git -C "${REPO_DIR}" diff --quiet || ! git -C "${REPO_DIR}" diff --cached --quiet; then
  log "skip: working tree has uncommitted changes"
  exit 0
fi

# Refresh remote state before deciding whether a pull is needed.
if ! git -C "${REPO_DIR}" fetch --prune origin "${BRANCH}" >/dev/null 2>&1; then
  log "error: fetch failed for origin/${BRANCH}"
  exit 1
fi

LOCAL="$(git -C "${REPO_DIR}" rev-parse @)"
REMOTE="$(git -C "${REPO_DIR}" rev-parse "@{u}" 2>/dev/null || true)"
BASE="$(git -C "${REPO_DIR}" merge-base @ "@{u}" 2>/dev/null || true)"

if [[ -z "${REMOTE}" || -z "${BASE}" ]]; then
  log "skip: upstream not configured for ${BRANCH}"
  exit 0
fi

if [[ "${LOCAL}" == "${REMOTE}" ]]; then
  log "up-to-date: ${BRANCH}"
  exit 0
fi

if [[ "${LOCAL}" == "${BASE}" ]]; then
  if git -C "${REPO_DIR}" pull --ff-only origin "${BRANCH}" >/dev/null 2>&1; then
    log "pulled: ${BRANCH} -> $(git -C "${REPO_DIR}" rev-parse --short HEAD)"
    exit 0
  fi
  log "error: fast-forward pull failed"
  exit 1
fi

if [[ "${REMOTE}" == "${BASE}" ]]; then
  log "skip: local branch is ahead of origin"
  exit 0
fi

log "skip: branch diverged from origin"
exit 0

