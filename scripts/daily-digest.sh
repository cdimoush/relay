#!/usr/bin/env bash
# daily-digest.sh — Send a one-line status summary to Telegram.
# Runs 3x/day via cron (8am, 2pm, 10pm EST).

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
ADMIN_CHAT_ID="8352167398"
REPORT_FILE="${RELAY_DIR}/logs/heartbeat-latest.txt"

# Source .env for bot token
if [[ -f "${RELAY_DIR}/.env" ]]; then
    set -a
    source "${RELAY_DIR}/.env"
    set +a
fi

# --- Gather stats ---

# Service status
if systemctl is-active --quiet relay; then
    status="up"
else
    status="DOWN"
fi

# Uptime
uptime_str=$(uptime -p 2>/dev/null | sed 's/^up //' || echo "unknown")

# Disk
disk_pct=$(df --output=pcent / | tail -1 | tr -d ' %')

# Active sessions
active_sessions="?"
if [[ -f "${RELAY_DIR}/relay.db" ]]; then
    active_sessions=$(sqlite3 "${RELAY_DIR}/relay.db" \
        "SELECT COUNT(*) FROM sessions WHERE status='active';" 2>/dev/null || echo "?")
fi

# Messages today
msgs_today="?"
if [[ -f "${RELAY_DIR}/relay.db" ]]; then
    msgs_today=$(sqlite3 "${RELAY_DIR}/relay.db" \
        "SELECT COUNT(*) FROM messages WHERE created_at >= date('now');" 2>/dev/null || echo "?")
fi

# Last heartbeat verdict
verdict="unknown"
if [[ -f "${REPORT_FILE}" ]]; then
    verdict_line=$(grep "^VERDICT:" "${REPORT_FILE}" 2>/dev/null || echo "")
    if [[ -n "${verdict_line}" ]]; then
        verdict=$(echo "${verdict_line}" | cut -d' ' -f2)
    fi
fi

# Time label
hour=$(date +%H)
if [[ "${hour}" -lt 12 ]]; then
    period="morning"
elif [[ "${hour}" -lt 18 ]]; then
    period="afternoon"
else
    period="evening"
fi

# --- Send digest ---

msg="Relay ${period}: ${status} | health: ${verdict} | uptime: ${uptime_str} | disk: ${disk_pct}% | sessions: ${active_sessions} | msgs today: ${msgs_today}"

curl -s -X POST "https://api.telegram.org/bot${RELAY_BOT_TOKEN}/sendMessage" \
    -d chat_id="${ADMIN_CHAT_ID}" \
    -d text="${msg}" > /dev/null 2>&1
