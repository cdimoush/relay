#!/usr/bin/env bash
# install-cron.sh — Install heartbeat, session-cleanup, daily-digest, and auto-blueprint cron jobs.
# Preserves any existing crontab entries. Idempotent (safe to re-run).

set -euo pipefail

RELAY_DIR="/home/ubuntu/relay"

# Create logs directory
mkdir -p "${RELAY_DIR}/logs"

# Define cron entries (use markers so we can update idempotently)
MARKER_START="# --- relay-heartbeat-start ---"
MARKER_END="# --- relay-heartbeat-end ---"

# Digest runs at 8am, 2pm, 10pm EST = 13:00, 19:00, 03:00 UTC
CRON_BLOCK="${MARKER_START}
# Heartbeat: every 5 min — health check + classify + throttled alert
*/5 * * * * ${RELAY_DIR}/scripts/heartbeat.sh >> ${RELAY_DIR}/logs/heartbeat.log 2>&1
# Session cleanup: hourly — expire stale sessions, purge old messages
0 * * * * ${RELAY_DIR}/scripts/session-cleanup.py >> ${RELAY_DIR}/logs/session-cleanup.log 2>&1
# Daily digest: 3x/day (8am, 2pm, 10pm EST)
0 13 * * * ${RELAY_DIR}/scripts/daily-digest.sh >> ${RELAY_DIR}/logs/digest.log 2>&1
0 19 * * * ${RELAY_DIR}/scripts/daily-digest.sh >> ${RELAY_DIR}/logs/digest.log 2>&1
0 3 * * * ${RELAY_DIR}/scripts/daily-digest.sh >> ${RELAY_DIR}/logs/digest.log 2>&1
# Auto-blueprint: 2am CT (8:00 UTC) — promote highest-priority concept to blueprint
0 8 * * * ${RELAY_DIR}/scripts/auto-blueprint.sh >> ${RELAY_DIR}/logs/auto-blueprint.log 2>&1
# Memories daily question: 9am CT (15:00 UTC) — send psychological interview question
0 15 * * * ${RELAY_DIR}/scripts/memories-question.sh >> ${RELAY_DIR}/logs/memories-question.log 2>&1
# Clone think: 3x/day (7am, 1pm, 8pm CT = 13:00, 19:00, 02:00 UTC)
0 13 * * * ${RELAY_DIR}/scripts/clone-think.sh >> ${RELAY_DIR}/logs/clone-think.log 2>&1
0 19 * * * ${RELAY_DIR}/scripts/clone-think.sh >> ${RELAY_DIR}/logs/clone-think.log 2>&1
0 2 * * * ${RELAY_DIR}/scripts/clone-think.sh >> ${RELAY_DIR}/logs/clone-think.log 2>&1
# Clone distill: daily 6am CT (12:00 UTC) — refresh soul.md if sources changed
0 12 * * * ${RELAY_DIR}/scripts/clone-distill.sh >> ${RELAY_DIR}/logs/clone-distill.log 2>&1
# Clone prune: weekly Sunday 3am CT (09:00 UTC) — archive old thoughts
0 9 * * 0 ${RELAY_DIR}/scripts/clone-prune.sh >> ${RELAY_DIR}/logs/clone-prune.log 2>&1
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

echo "Cron jobs installed successfully:"
echo "  - heartbeat.sh:         every 5 min"
echo "  - session-cleanup.py:   every hour"
echo "  - daily-digest.sh:      8am, 2pm, 10pm EST"
echo "  - auto-blueprint.sh:    2am CT (8:00 UTC)"
echo "  - memories-question.sh: 9am CT (15:00 UTC)"
echo "  - clone-think.sh:       7am, 1pm, 8pm CT"
echo "  - clone-distill.sh:     6am CT (12:00 UTC)"
echo "  - clone-prune.sh:       Sunday 3am CT (09:00 UTC)"
echo ""
echo "Verify with: crontab -l"
