#!/usr/bin/env bash
# heartbeat.sh — Relay health check with classify + throttled alerts.
# Runs every 5 minutes via cron.
# 1. Collects system checks into a report file
# 2. Classifies report as "good" or "bad" via claude -p
# 3. Sends Telegram alert only if bad AND throttle window passed (8h)
# 4. Exception: relay service down = always alert immediately

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
LOGS_DIR="${RELAY_DIR}/logs"
REPORT_FILE="${LOGS_DIR}/heartbeat-latest.txt"
THROTTLE_FILE="/tmp/relay-heartbeat-last-alert"
THROTTLE_SECONDS=28800  # 8 hours
ADMIN_CHAT_ID="8352167398"

mkdir -p "${LOGS_DIR}"

# Source .env for bot token
if [[ -f "${RELAY_DIR}/.env" ]]; then
    set -a
    source "${RELAY_DIR}/.env"
    set +a
fi

# --- Collect checks into report ---

{
    echo "=== Relay Heartbeat Report ==="
    echo "Timestamp: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo ""

    # 1. Relay service status
    if systemctl is-active --quiet relay; then
        echo "SERVICE: active"
    else
        echo "SERVICE: $(systemctl is-active relay)"
    fi

    # 2. Disk usage
    disk_pct=$(df --output=pcent / | tail -1 | tr -d ' %')
    echo "DISK: ${disk_pct}%"

    # 3. Journal size
    journal_info=$(journalctl --disk-usage 2>/dev/null | head -1 || echo "unknown")
    echo "JOURNAL: ${journal_info}"

    # 4. SQLite DB integrity + active sessions (via Python, sqlite3 CLI not installed)
    if [[ -f "${RELAY_DIR}/relay.db" ]]; then
        db_info=$("${RELAY_DIR}/.venv/bin/python" -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('${RELAY_DIR}/relay.db')
    integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
    active = conn.execute(\"SELECT COUNT(*) FROM sessions WHERE status='active'\").fetchone()[0]
    conn.close()
    print(f'DB_INTEGRITY: {integrity}')
    print(f'ACTIVE_SESSIONS: {active}')
except Exception as e:
    print(f'DB_INTEGRITY: error - {e}')
    print('ACTIVE_SESSIONS: ?')
" 2>&1)
        echo "${db_info}"
    else
        echo "DB_INTEGRITY: no database found"
        echo "ACTIVE_SESSIONS: 0"
    fi

    # 5. Uptime
    echo "UPTIME: $(uptime -p)"

} > "${REPORT_FILE}" 2>&1

# --- Quick check: is service down? Always alert immediately ---

service_line=$(grep "^SERVICE:" "${REPORT_FILE}" || echo "SERVICE: unknown")
if [[ "${service_line}" != "SERVICE: active" ]]; then
    curl -s -X POST "https://api.telegram.org/bot${RELAY_BOT_TOKEN}/sendMessage" \
        -d chat_id="${ADMIN_CHAT_ID}" \
        -d text="🚨 RELAY DOWN: ${service_line}. Auto-restart should kick in (Restart=always). Check: sudo journalctl -u relay -n 50" \
        -d parse_mode="HTML" > /dev/null 2>&1
    date +%s > "${THROTTLE_FILE}"
    exit 0
fi

# --- Classify report via claude -p ---

verdict=$(claude -p "Read this system health report. Reply with exactly one word: 'good' if everything is healthy, or 'bad' if anything needs attention. Only output that one word, nothing else.

$(cat "${REPORT_FILE}")" --model haiku --max-turns 1 --output-format text 2>/dev/null | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')

# Default to good if claude fails (don't spam alerts on API issues)
if [[ "${verdict}" != "good" && "${verdict}" != "bad" ]]; then
    verdict="good"
fi

echo "VERDICT: ${verdict}" >> "${REPORT_FILE}"

# --- If bad, check throttle before alerting ---

if [[ "${verdict}" == "bad" ]]; then
    should_alert=false

    if [[ ! -f "${THROTTLE_FILE}" ]]; then
        should_alert=true
    else
        last_alert=$(cat "${THROTTLE_FILE}")
        now=$(date +%s)
        elapsed=$(( now - last_alert ))
        if [[ "${elapsed}" -ge "${THROTTLE_SECONDS}" ]]; then
            should_alert=true
        fi
    fi

    if [[ "${should_alert}" == "true" ]]; then
        report_summary=$(cat "${REPORT_FILE}")
        curl -s -X POST "https://api.telegram.org/bot${RELAY_BOT_TOKEN}/sendMessage" \
            -d chat_id="${ADMIN_CHAT_ID}" \
            -d text="⚠️ Heartbeat: something needs attention

${report_summary}" \
            -d parse_mode="" > /dev/null 2>&1
        date +%s > "${THROTTLE_FILE}"
    fi
fi
