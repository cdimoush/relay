#!/usr/bin/env bash
# clone-distill.sh — Daily soul.md refresh with change detection.
# Runs at 6am CT (12:00 UTC) via cron — before the first thought cron.
# Only recomputes soul.md if source data (memories profile or cyborg brain) has changed.

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
CLONE_DIR="/home/ubuntu/clone"
STATE_FILE="${CLONE_DIR}/.state/last-distill.json"
LOGS_DIR="${RELAY_DIR}/logs"
LOG_FILE="${LOGS_DIR}/clone-distill.log"

PROFILE_PATH="/home/ubuntu/memories/profile/profile.yaml"
THREADS_PATH="/home/ubuntu/cyborg/brain/threads.md"
ATLAS_PATH="/home/ubuntu/cyborg/brain/atlas.md"
NOTES_PATH="/home/ubuntu/cyborg/.cyborg/notes.jsonl"

mkdir -p "${CLONE_DIR}/.state" "${LOGS_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "${LOG_FILE}"
}

# Source .env
if [[ -f "${RELAY_DIR}/.env" ]]; then
    set -a
    source "${RELAY_DIR}/.env"
    set +a
fi

# --- Compute current hashes ---
current_profile_hash=$(sha256sum "${PROFILE_PATH}" 2>/dev/null | cut -d' ' -f1 || echo "none")
current_threads_mtime=$(stat -c %Y "${THREADS_PATH}" 2>/dev/null || echo "0")
current_atlas_mtime=$(stat -c %Y "${ATLAS_PATH}" 2>/dev/null || echo "0")
current_notes_hash=$(sha256sum "${NOTES_PATH}" 2>/dev/null | cut -d' ' -f1 || echo "none")

# --- Load stored hashes ---
if [[ -f "${STATE_FILE}" ]]; then
    stored_profile_hash=$("${RELAY_DIR}/.venv/bin/python" -c "import json; d=json.load(open('${STATE_FILE}')); print(d.get('profile_sha256',''))" 2>/dev/null || echo "")
    stored_threads_mtime=$("${RELAY_DIR}/.venv/bin/python" -c "import json; d=json.load(open('${STATE_FILE}')); print(d.get('threads_mtime',''))" 2>/dev/null || echo "")
    stored_atlas_mtime=$("${RELAY_DIR}/.venv/bin/python" -c "import json; d=json.load(open('${STATE_FILE}')); print(d.get('atlas_mtime',''))" 2>/dev/null || echo "")
    stored_notes_hash=$("${RELAY_DIR}/.venv/bin/python" -c "import json; d=json.load(open('${STATE_FILE}')); print(d.get('notes_jsonl_sha256',''))" 2>/dev/null || echo "")
else
    stored_profile_hash=""
    stored_threads_mtime=""
    stored_atlas_mtime=""
    stored_notes_hash=""
fi

# --- Compare ---
if [[ "${current_profile_hash}" == "${stored_profile_hash}" ]] &&
   [[ "${current_threads_mtime}" == "${stored_threads_mtime}" ]] &&
   [[ "${current_atlas_mtime}" == "${stored_atlas_mtime}" ]] &&
   [[ "${current_notes_hash}" == "${stored_notes_hash}" ]]; then
    log "SKIP: no changes to source data"
    exit 0
fi

log "CHANGED: profile=${current_profile_hash:0:8}→${stored_profile_hash:0:8} threads_mt=${current_threads_mtime}→${stored_threads_mtime} atlas_mt=${current_atlas_mtime}→${stored_atlas_mtime} notes=${current_notes_hash:0:8}→${stored_notes_hash:0:8}"
log "START: regenerating soul.md"

# --- Invoke claude to run /distill ---
OUTPUT=$(claude -p "Run /distill to regenerate soul.md from current source data. Read all source files, write the first-person prose self-portrait, and update the change detection state in .state/last-distill.json." \
    --model opus \
    --max-turns 8 \
    --allowedTools "Read,Write,Bash,Glob,Grep" \
    --output-format text \
    --cwd "${CLONE_DIR}" 2>/dev/null)

if [[ -z "${OUTPUT}" ]]; then
    log "ERROR: claude produced no output"
    exit 1
fi

# Verify soul.md was updated
if [[ ! -f "${CLONE_DIR}/soul.md" ]]; then
    log "ERROR: soul.md not found after distill"
    exit 1
fi

soul_size=$(wc -c < "${CLONE_DIR}/soul.md")
log "DONE: soul.md regenerated (${soul_size} bytes)"

# --- Update state file (fallback if skill didn't) ---
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "${STATE_FILE}" <<EOF
{
  "profile_sha256": "${current_profile_hash}",
  "threads_mtime": "${current_threads_mtime}",
  "atlas_mtime": "${current_atlas_mtime}",
  "notes_jsonl_sha256": "${current_notes_hash}",
  "last_distill": "${NOW}"
}
EOF

# --- Optional: notify via Telegram ---
if [[ -n "${CLONE_BOT_TOKEN:-}" ]] && [[ -n "${CLONE_CHAT_ID:-}" ]]; then
    curl -s -X POST "https://api.telegram.org/bot${CLONE_BOT_TOKEN}/sendMessage" \
        -d chat_id="${CLONE_CHAT_ID}" \
        --data-urlencode "text=Soul refreshed. Sources changed — I've updated my self-portrait." > /dev/null 2>&1
fi

log "NOTIFIED: soul refresh complete"
