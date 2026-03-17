#!/usr/bin/env bash
# agent-cron.sh — Generic wrapper for agent cron jobs.
# Usage: agent-cron.sh <agent_name> <cron_name>
#
# Reads relay.yaml to find the cron config, runs the script in the agent's
# project_dir, optionally sends output to Telegram, and logs everything.
# Exit codes from the inner script:
#   0 = success (output sent to Telegram if notify=true)
#   2 = skip (logged, no Telegram)
#   1 = error (logged, no Telegram)

set -uo pipefail

RELAY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_NAME="${1:?Usage: agent-cron.sh <agent_name> <cron_name>}"
CRON_NAME="${2:?Usage: agent-cron.sh <agent_name> <cron_name>}"

LOGS_DIR="${RELAY_DIR}/logs"
LOG_FILE="${LOGS_DIR}/${AGENT_NAME}-${CRON_NAME}.log"
TELEGRAM_MAX=4096

mkdir -p "${LOGS_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "${LOG_FILE}"
}

# --- Source .env ---
if [[ -f "${RELAY_DIR}/.env" ]]; then
    set -a
    source "${RELAY_DIR}/.env"
    set +a
fi

# --- Read cron config from relay.yaml via Python ---
CRON_CONFIG=$("${RELAY_DIR}/.venv/bin/python" - "${RELAY_DIR}/relay.yaml" "${AGENT_NAME}" "${CRON_NAME}" <<'PYEOF'
import sys, os, json
import yaml
from pathlib import Path

config_path, agent_name, cron_name = sys.argv[1], sys.argv[2], sys.argv[3]

raw = Path(config_path).read_text()
expanded = os.path.expandvars(raw)
data = yaml.safe_load(expanded)

agent = data.get("agents", {}).get(agent_name)
if not agent:
    print(json.dumps({"error": f"Agent '{agent_name}' not found in config"}))
    sys.exit(1)

crons = agent.get("crons", [])
cron = next((c for c in crons if c["name"] == cron_name), None)
if not cron:
    print(json.dumps({"error": f"Cron '{cron_name}' not found for agent '{agent_name}'"}))
    sys.exit(1)

result = {
    "script": cron["script"],
    "notify": cron.get("notify", False),
    "skip_if_active": cron.get("skip_if_active", False),
    "model": cron.get("model", agent.get("model", "sonnet")),
    "project_dir": agent["project_dir"],
    "bot_token": agent["bot_token"],
    "chat_id": cron.get("notify_chat_id", agent["allowed_users"][0]),
}
print(json.dumps(result))
PYEOF
)

# Check for config errors
CONFIG_ERROR=$(echo "${CRON_CONFIG}" | "${RELAY_DIR}/.venv/bin/python" -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',''))")
if [[ -n "${CONFIG_ERROR}" ]]; then
    log "ERROR: ${CONFIG_ERROR}"
    exit 1
fi

# Parse config fields
parse_field() {
    echo "${CRON_CONFIG}" | "${RELAY_DIR}/.venv/bin/python" -c "import sys,json; print(json.load(sys.stdin)['$1'])"
}

SCRIPT=$(parse_field script)
NOTIFY=$(parse_field notify)
SKIP_IF_ACTIVE=$(parse_field skip_if_active)
MODEL=$(parse_field model)
PROJECT_DIR=$(parse_field project_dir)
BOT_TOKEN=$(parse_field bot_token)
CHAT_ID=$(parse_field chat_id)

# --- Check for active sessions if skip_if_active ---
if [[ "${SKIP_IF_ACTIVE}" == "True" ]]; then
    DB_PATH="${RELAY_DIR}/relay.db"
    if [[ -f "${DB_PATH}" ]]; then
        active_count=$("${RELAY_DIR}/.venv/bin/python" -c "
import sqlite3
from datetime import datetime, timezone, timedelta
conn = sqlite3.connect('${DB_PATH}')
cutoff = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
count = conn.execute(
    \"SELECT COUNT(*) FROM sessions WHERE agent_name=? AND status='active' AND last_active_at > ?\",
    ('${AGENT_NAME}', cutoff)
).fetchone()[0]
conn.close()
print(count)
" 2>/dev/null || echo "0")

        if [[ "${active_count}" -gt 0 ]]; then
            log "SKIP: ${AGENT_NAME} has active session, user is mid-conversation"
            exit 0
        fi
    fi
fi

# --- Resolve and validate script path ---
SCRIPT_PATH="${PROJECT_DIR}/${SCRIPT}"
if [[ ! -f "${SCRIPT_PATH}" ]]; then
    log "ERROR: script not found: ${SCRIPT_PATH}"
    exit 1
fi
if [[ ! -x "${SCRIPT_PATH}" ]]; then
    log "ERROR: script not executable: ${SCRIPT_PATH}"
    exit 1
fi

# --- Export useful env vars for the inner script ---
export RELAY_DIR
export AGENT_NAME
export CRON_MODEL="${MODEL}"
export PROJECT_DIR

log "START: ${AGENT_NAME}/${CRON_NAME} (${SCRIPT_PATH})"

# --- Run the script ---
OUTPUT=$(cd "${PROJECT_DIR}" && "${SCRIPT_PATH}" 2>&1)
EXIT_CODE=$?

if [[ ${EXIT_CODE} -eq 2 ]]; then
    log "SKIP: script exited 2 — ${OUTPUT:0:200}"
    exit 0
elif [[ ${EXIT_CODE} -ne 0 ]]; then
    log "ERROR: script exited ${EXIT_CODE} — ${OUTPUT:0:500}"
    exit 1
fi

OUTPUT_SIZE=${#OUTPUT}
log "OK: script completed (${OUTPUT_SIZE} bytes output)"

# --- Send to Telegram if notify=true ---
if [[ "${NOTIFY}" == "True" ]] && [[ -n "${OUTPUT}" ]]; then
    send_message() {
        local text="$1"
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" \
            --data-urlencode "text=${text}" > /dev/null 2>&1
    }

    if [[ ${#OUTPUT} -le ${TELEGRAM_MAX} ]]; then
        send_message "${OUTPUT}"
    else
        # Split into chunks at line boundaries
        while [[ -n "${OUTPUT}" ]]; do
            chunk="${OUTPUT:0:${TELEGRAM_MAX}}"
            if [[ ${#OUTPUT} -gt ${TELEGRAM_MAX} ]]; then
                # Try to break at last newline
                last_nl_pos=$(echo "${chunk}" | grep -c '')
                chunk=$(echo "${OUTPUT}" | head -n "${last_nl_pos}")
            fi
            send_message "${chunk}"
            OUTPUT="${OUTPUT:${#chunk}}"
            sleep 1
        done
    fi
    log "SENT: output delivered to Telegram"
fi

log "DONE: ${AGENT_NAME}/${CRON_NAME}"
