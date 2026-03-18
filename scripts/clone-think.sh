#!/usr/bin/env bash
# clone-think.sh — Autonomous thought generation for the clone agent.
# Runs 3x/day via cron (7am, 1pm, 8pm CT).
# Fire-and-forget: spawn claude, save thought, curl to Telegram.
# NOT a relay session — bypasses relay entirely.

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
CLONE_DIR="/home/ubuntu/clone"
THOUGHTS_DIR="${CLONE_DIR}/thoughts"
LOGS_DIR="${RELAY_DIR}/logs"
LOG_FILE="${LOGS_DIR}/clone-think.log"
TELEGRAM_MAX=4096

mkdir -p "${THOUGHTS_DIR}" "${LOGS_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "${LOG_FILE}"
}

# Source .env for bot token and chat ID
if [[ -f "${RELAY_DIR}/.env" ]]; then
    set -a
    source "${RELAY_DIR}/.env"
    set +a
fi

if [[ -z "${CLONE_BOT_TOKEN:-}" ]]; then
    log "ERROR: CLONE_BOT_TOKEN not set"
    exit 1
fi

if [[ -z "${CLONE_CHAT_ID:-}" ]]; then
    log "ERROR: CLONE_CHAT_ID not set"
    exit 1
fi

# --- Check for active clone sessions ---
active_count=$("${RELAY_DIR}/.venv/bin/python" -c "
import sqlite3
from datetime import datetime, timezone, timedelta
conn = sqlite3.connect('${RELAY_DIR}/relay.db')
cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
count = conn.execute(
    \"SELECT COUNT(*) FROM sessions WHERE agent_name='clone' AND status='active' AND last_active_at > ?\",
    (cutoff,)
).fetchone()[0]
conn.close()
print(count)
" 2>/dev/null || echo "0")

if [[ "${active_count}" -gt 0 ]]; then
    log "SKIP: clone has active session, user is mid-conversation"
    exit 0
fi

# --- Find last 3 thought files (excluding archive/) ---
recent_thoughts=$(find "${THOUGHTS_DIR}" -maxdepth 1 -name '*.md' -type f | sort -r | head -3 | tr '\n' ',' | sed 's/,$//')

THOUGHT_PROMPT="Read soul.md (your identity — who you are, what you care about, what you're working on)."

if [[ -n "${recent_thoughts}" ]]; then
    THOUGHT_PROMPT="${THOUGHT_PROMPT} Read your last few journal entries from thoughts/ (skip archive/). These are your recent thoughts — what you've been mulling over."
fi

THOUGHT_PROMPT="${THOUGHT_PROMPT}

You are having a moment of reflection. What thread do you want to pull on? Think out loud — first person, honest, meandering is fine. If a thread calls for deeper knowledge, read the relevant note from /home/ubuntu/cyborg/brain/notes/. End with one concrete thing you want to do or explore next.

Keep it under 3000 characters. No headers or markdown formatting — just think."

log "START: generating thought"

# --- Generate thought ---
OUTPUT=$(claude -p "${THOUGHT_PROMPT}" \
    --model sonnet \
    --max-turns 5 \
    --allowedTools "Read,Glob,Grep" \
    --output-format text \
    --cwd "${CLONE_DIR}" 2>/dev/null)

if [[ -z "${OUTPUT}" ]]; then
    log "ERROR: claude produced no output"
    exit 1
fi

# --- Save thought ---
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M)
THOUGHT_FILE="${THOUGHTS_DIR}/${TIMESTAMP}.md"
echo "${OUTPUT}" > "${THOUGHT_FILE}"
log "SAVED: ${THOUGHT_FILE} ($(echo "${OUTPUT}" | wc -c) bytes)"

# --- Send to Telegram ---
send_message() {
    local text="$1"
    curl -s -X POST "https://api.telegram.org/bot${CLONE_BOT_TOKEN}/sendMessage" \
        -d chat_id="${CLONE_CHAT_ID}" \
        --data-urlencode "text=${text}" > /dev/null 2>&1
}

# Chunk if needed
if [[ ${#OUTPUT} -le ${TELEGRAM_MAX} ]]; then
    send_message "${OUTPUT}"
else
    # Split into chunks at line boundaries
    while [[ -n "${OUTPUT}" ]]; do
        chunk="${OUTPUT:0:${TELEGRAM_MAX}}"
        # Try to break at last newline within limit
        last_nl=$(echo "${chunk}" | grep -n '' | tail -1 | cut -d: -f1)
        if [[ ${#OUTPUT} -gt ${TELEGRAM_MAX} ]]; then
            chunk=$(echo "${OUTPUT}" | head -n "${last_nl}")
        fi
        send_message "${chunk}"
        OUTPUT="${OUTPUT:${#chunk}}"
        sleep 1
    done
fi

log "SENT: thought delivered to Telegram"
