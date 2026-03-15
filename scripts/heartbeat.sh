#!/usr/bin/env bash
# heartbeat.sh — Relay health check script
# Runs every 5 minutes via cron. Alerts via Telegram on problems.
# Exits silently when everything is healthy.

set -euo pipefail

RELAY_DIR="/home/ubuntu/relay"
ADMIN_CHAT_ID="8352167398"

# Source .env for bot token
if [[ -f "${RELAY_DIR}/.env" ]]; then
    set -a
    source "${RELAY_DIR}/.env"
    set +a
fi

alert() {
    local msg="$1"
    curl -s -X POST "https://api.telegram.org/bot${RELAY_BOT_TOKEN}/sendMessage" \
        -d chat_id="${ADMIN_CHAT_ID}" \
        -d text="🚨 Heartbeat Alert: ${msg}" \
        -d parse_mode="HTML" > /dev/null 2>&1
}

# 1. Check relay service is active
if ! systemctl is-active --quiet relay; then
    alert "Relay service is NOT active ($(systemctl is-active relay))"
fi

# 2. Check disk usage (alert if >85%)
disk_pct=$(df --output=pcent / | tail -1 | tr -d ' %')
if [[ "${disk_pct}" -gt 85 ]]; then
    alert "Disk usage at ${disk_pct}% (threshold: 85%)"
fi

# 3. Check journal size (alert if >1GB)
journal_bytes=$(journalctl --disk-usage 2>/dev/null | grep -oP '\d+\.\d+[MG]' | head -1 || echo "0M")
# Convert to MB for comparison
if echo "${journal_bytes}" | grep -qP '\d+\.\d+G'; then
    journal_gb=$(echo "${journal_bytes}" | grep -oP '[\d.]+')
    journal_mb=$(awk "BEGIN {printf \"%.0f\", ${journal_gb} * 1024}")
else
    journal_mb=$(echo "${journal_bytes}" | grep -oP '[\d.]+' || echo "0")
    journal_mb=$(printf "%.0f" "${journal_mb}")
fi

if [[ "${journal_mb}" -gt 1024 ]]; then
    alert "Journal size is ${journal_bytes} (threshold: 1GB)"
fi

# 4. Check SQLite DB integrity
if [[ -f "${RELAY_DIR}/relay.db" ]]; then
    integrity=$(sqlite3 "${RELAY_DIR}/relay.db" "PRAGMA integrity_check;" 2>&1)
    if [[ "${integrity}" != "ok" ]]; then
        alert "SQLite integrity check failed: ${integrity}"
    fi
fi
