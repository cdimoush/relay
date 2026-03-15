#!/usr/bin/env bash
# auto-blueprint.sh — Nightly concept-to-blueprint promotion.
# Runs at 2am CT (8:00 UTC) via cron.
# 1. Checks for active relay sessions — skips if user is active
# 2. Finds highest-priority open concept bead
# 3. Invokes claude -p to promote it to a blueprint
# 4. Sends Telegram notification to user

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
LOGS_DIR="${RELAY_DIR}/logs"
LOG_FILE="${LOGS_DIR}/auto-blueprint.log"
ADMIN_CHAT_ID="8352167398"

mkdir -p "${LOGS_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "${LOG_FILE}"
}

# Source .env for bot token
if [[ -f "${RELAY_DIR}/.env" ]]; then
    set -a
    source "${RELAY_DIR}/.env"
    set +a
fi

# --- Step 1: Check for active sessions ---
active_count=$("${RELAY_DIR}/.venv/bin/python" -c "
import sqlite3
from datetime import datetime, timezone, timedelta
conn = sqlite3.connect('${RELAY_DIR}/relay.db')
cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
count = conn.execute(
    \"SELECT COUNT(*) FROM sessions WHERE status='active' AND last_active_at > ?\",
    (cutoff,)
).fetchone()[0]
conn.close()
print(count)
" 2>/dev/null || echo "0")

if [[ "${active_count}" -gt 0 ]]; then
    log "SKIP: ${active_count} active session(s) found, user is active"
    exit 0
fi

# --- Step 2: Find highest-priority open concept ---
concept_info=$(cd "${RELAY_DIR}" && bd list --status=open -l concept --json 2>/dev/null || echo "[]")

# Parse with Python to find highest priority concept
concept_line=$("${RELAY_DIR}/.venv/bin/python" -c "
import json, sys
data = json.loads('''${concept_info}''')
if not data:
    print('')
    sys.exit(0)
# Sort by priority (lower number = higher priority)
data.sort(key=lambda x: x.get('priority', 99))
best = data[0]
print(f\"{best['id']}|{best.get('title', 'untitled')}|{best.get('priority', '?')}\")
" 2>/dev/null || echo "")

if [[ -z "${concept_line}" ]]; then
    log "SKIP: no open concepts in backlog"
    exit 0
fi

concept_id=$(echo "${concept_line}" | cut -d'|' -f1)
concept_title=$(echo "${concept_line}" | cut -d'|' -f2)
concept_priority=$(echo "${concept_line}" | cut -d'|' -f3)

log "FOUND: ${concept_id} — ${concept_title} (P${concept_priority})"

# --- Step 3: Invoke claude to promote concept to blueprint ---
blueprint_output=$(cd "${RELAY_DIR}" && claude -p "You are the relay admin agent. Promote concept bead ${concept_id} ('${concept_title}') to a full blueprint.

Steps:
1. Run: bd show ${concept_id}
2. Read the concept description and design
3. Explore relevant codebase files to understand what needs to change (keep exploration brief — 2-3 files max)
4. Promote it: bd update ${concept_id} --type=epic --set-labels=blueprint
5. Write an implementation plan into the design field: bd update ${concept_id} --design=\"...\"
6. Create 2-6 sub-tasks with bd create --parent=${concept_id} and wire dependencies with bd dep add
7. Output a summary in this exact format:
   BLUEPRINT_SUMMARY: {title} | {number_of_tasks} tasks | {one-line description}

Do NOT write any code. Do NOT create files. Only work with beads." \
    --model sonnet \
    --max-turns 15 \
    --output-format text 2>/dev/null)

# Extract summary line
summary=$(echo "${blueprint_output}" | grep "^BLUEPRINT_SUMMARY:" | head -1 | sed 's/^BLUEPRINT_SUMMARY: //')

if [[ -z "${summary}" ]]; then
    summary="${concept_title} — promoted to blueprint"
fi

log "PROMOTED: ${summary}"

# --- Step 4: Notify user via Telegram with epic-view format ---
epic_message=$("${RELAY_DIR}/.venv/bin/python" -c "
import json, subprocess, sys

raw = subprocess.run(['bd', 'show', '${concept_id}', '--json'],
                     capture_output=True, text=True, cwd='${RELAY_DIR}')
data = json.loads(raw.stdout)[0]

epic_id = data['id']
title = data['title']
labels = ', '.join(data.get('labels', []))
desc = data.get('description', '')

icons = {'open': '○', 'in_progress': '◐', 'closed': '●'}

children = [d for d in data.get('dependents', [])
            if d.get('dependency_type') == 'parent-child']

lines = []
lines.append(f'🔧 Auto-Blueprint')
lines.append(f'')
lines.append(f'<b>{epic_id}</b> — {title}')
lines.append(f'🏷 {labels}')
lines.append(f'')
lines.append(desc)

if children:
    lines.append(f'')
    lines.append(f'── Tasks ──')
    for c in children:
        icon = icons.get(c['status'], '?')
        lines.append(f\"{icon} <b>{c['id']}</b> · {c['title']}\")

lines.append(f'')
lines.append(f\"Reply 'build {epic_id}' to start implementation.\")

print('\n'.join(lines))
" 2>/dev/null)

if [[ -z "${epic_message}" ]]; then
    epic_message="🔧 Auto-Blueprint: ${summary}

Concept ${concept_id} has been promoted. Review with: bd show ${concept_id}"
fi

curl -s -X POST "https://api.telegram.org/bot${RELAY_BOT_TOKEN}/sendMessage" \
    -d chat_id="${ADMIN_CHAT_ID}" \
    --data-urlencode "text=${epic_message}" \
    -d parse_mode="HTML" > /dev/null 2>&1

log "NOTIFIED: user messaged via Telegram"
