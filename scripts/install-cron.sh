#!/usr/bin/env bash
# install-cron.sh — Install heartbeat and session-cleanup cron jobs.
# Preserves any existing crontab entries.

set -euo pipefail

RELAY_DIR="/home/ubuntu/relay"

# Create logs directory
mkdir -p "${RELAY_DIR}/logs"

# Define cron entries (use markers so we can update idempotently)
MARKER_START="# --- relay-heartbeat-start ---"
MARKER_END="# --- relay-heartbeat-end ---"

CRON_BLOCK="${MARKER_START}
*/5 * * * * ${RELAY_DIR}/scripts/heartbeat.sh >> ${RELAY_DIR}/logs/heartbeat.log 2>&1
0 * * * * ${RELAY_DIR}/scripts/session-cleanup.py >> ${RELAY_DIR}/logs/session-cleanup.log 2>&1
${MARKER_END}"

# Get existing crontab (ignore error if empty)
EXISTING=$(crontab -l 2>/dev/null || true)

# Remove old relay entries if present
CLEANED=$(echo "${EXISTING}" | sed "/${MARKER_START}/,/${MARKER_END}/d")

# Append new block
NEW_CRONTAB="${CLEANED}
${CRON_BLOCK}"

# Install
echo "${NEW_CRONTAB}" | crontab -

echo "Cron jobs installed successfully."
echo "Verify with: crontab -l"
