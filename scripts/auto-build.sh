#!/usr/bin/env bash
# auto-build.sh — Nightly autonomous blueprint builder.
# Runs at 3am CT (9:00 UTC) via cron, one hour after auto-blueprint.sh.
#
# Cron entry (install after merge):
#   0 9 * * * /home/ubuntu/relay/scripts/auto-build.sh >> /home/ubuntu/relay/logs/auto-build.log 2>&1
# 1. Checks for active relay sessions — skips if user is active (resource courtesy)
# 2. Finds highest-priority open blueprint bead
# 3. Creates a git worktree for isolated development
# 4. Invokes claude to build the blueprint
# 5. Pushes branch and creates a GitHub PR
# 6. Sends Telegram notification with PR link
# 7. Cleans up worktree

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
LOGS_DIR="${RELAY_DIR}/logs"
LOG_FILE="${LOGS_DIR}/auto-build.log"
ADMIN_CHAT_ID="8352167398"
BUILD_BUDGET="10.00"
DATE_STAMP=$(date -u '+%Y%m%d')
WORKTREE_DIR="/tmp/relay-build-${DATE_STAMP}"

mkdir -p "${LOGS_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "${LOG_FILE}"
}

cleanup() {
    if [[ -d "${WORKTREE_DIR}" ]]; then
        log "CLEANUP: removing worktree ${WORKTREE_DIR}"
        git -C "${RELAY_DIR}" worktree remove "${WORKTREE_DIR}" --force 2>/dev/null || true
    fi
}
trap cleanup EXIT

send_telegram() {
    local message="$1"
    if [[ -z "${RELAY_BOT_TOKEN:-}" ]]; then
        log "WARN: RELAY_BOT_TOKEN not set, skipping Telegram notification"
        return
    fi
    curl -s -X POST "https://api.telegram.org/bot${RELAY_BOT_TOKEN}/sendMessage" \
        -d chat_id="${ADMIN_CHAT_ID}" \
        --data-urlencode "text=${message}" \
        -d parse_mode="HTML" > /dev/null 2>&1
}

# Source .env for bot token and API keys
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

# --- Step 2: Find highest-priority open blueprint ---
blueprint_info=$(cd "${RELAY_DIR}" && bd list --status=open -l blueprint --json 2>/dev/null || echo "[]")

blueprint_line=$("${RELAY_DIR}/.venv/bin/python" -c "
import json, sys
data = json.loads('''${blueprint_info}''')
if not data:
    print('')
    sys.exit(0)
# Only pick epics (blueprints with sub-tasks), skip non-epic beads
epics = [d for d in data if d.get('type') == 'epic']
if not epics:
    print('')
    sys.exit(0)
epics.sort(key=lambda x: x.get('priority', 99))
best = epics[0]
print(f\"{best['id']}|{best.get('title', 'untitled')}|{best.get('priority', '?')}\")
" 2>/dev/null || echo "")

if [[ -z "${blueprint_line}" ]]; then
    log "SKIP: no open blueprints in backlog"
    exit 0
fi

blueprint_id=$(echo "${blueprint_line}" | cut -d'|' -f1)
blueprint_title=$(echo "${blueprint_line}" | cut -d'|' -f2)
blueprint_priority=$(echo "${blueprint_line}" | cut -d'|' -f3)

log "FOUND: ${blueprint_id} — ${blueprint_title} (P${blueprint_priority})"

# --- Step 3: Create worktree ---
branch_name="auto/${blueprint_id}"

# Clean up stale worktree if it exists from a previous failed run
if [[ -d "${WORKTREE_DIR}" ]]; then
    log "CLEANUP: removing stale worktree from previous run"
    git -C "${RELAY_DIR}" worktree remove "${WORKTREE_DIR}" --force 2>/dev/null || true
fi

# Check if branch already exists (previous partial run)
if git -C "${RELAY_DIR}" rev-parse --verify "${branch_name}" >/dev/null 2>&1; then
    log "RESUME: branch ${branch_name} exists, checking out existing branch"
    git -C "${RELAY_DIR}" worktree add "${WORKTREE_DIR}" "${branch_name}" 2>&1 || {
        log "ERROR: failed to create worktree from existing branch ${branch_name}"
        send_telegram "🔴 Auto-Build failed: could not create worktree for ${blueprint_id}"
        exit 1
    }
else
    git -C "${RELAY_DIR}" worktree add "${WORKTREE_DIR}" -b "${branch_name}" master 2>&1 || {
        log "ERROR: failed to create worktree"
        send_telegram "🔴 Auto-Build failed: could not create worktree for ${blueprint_id}"
        exit 1
    }
fi

log "WORKTREE: created at ${WORKTREE_DIR} on branch ${branch_name}"

# --- Step 4: Invoke claude to build the blueprint ---
log "BUILD: starting claude build of ${blueprint_id} (budget: \$${BUILD_BUDGET})"

build_output=$(cd "${WORKTREE_DIR}" && claude -p "You are the relay admin agent. Build blueprint ${blueprint_id} ('${blueprint_title}').

Steps:
1. Run: bd show ${blueprint_id}
2. Run: bd ready (to see which sub-tasks are unblocked)
3. For each ready sub-task, in dependency order:
   a. Read the task description
   b. Implement the changes (write code, edit files)
   c. Run tests if applicable: .venv/bin/python -m pytest tests/ -v
   d. git add the changed files and commit with a descriptive message
   e. Close the task: bd close <task-id>
4. After all tasks are done, output a summary in this exact format:
   BUILD_SUMMARY: <one-line description of what was built>
   TASKS_CLOSED: <number of tasks completed>

Important:
- You are working in a git worktree at ${WORKTREE_DIR}
- Commit after each sub-task
- Do NOT modify relay.yaml or .env
- If a test fails, fix it before moving on
- If you get stuck on a task, skip it and note why in the summary" \
    --model sonnet \
    --max-turns 30 \
    --max-budget-usd "${BUILD_BUDGET}" \
    --dangerously-skip-permissions \
    --output-format text 2>/dev/null) || true

log "BUILD: claude session completed"

# Extract summary
build_summary=$(echo "${build_output}" | grep "^BUILD_SUMMARY:" | head -1 | sed 's/^BUILD_SUMMARY: //')
tasks_closed=$(echo "${build_output}" | grep "^TASKS_CLOSED:" | head -1 | sed 's/^TASKS_CLOSED: //')

if [[ -z "${build_summary}" ]]; then
    build_summary="Build completed (no summary extracted)"
fi

# --- Step 5: Check if there are actual changes to push ---
changes=$(git -C "${WORKTREE_DIR}" log master..HEAD --oneline 2>/dev/null | wc -l)

if [[ "${changes}" -eq 0 ]]; then
    log "SKIP: no commits produced, nothing to push"
    send_telegram "⚪ Auto-Build: ${blueprint_id}
No changes produced. Blueprint may need manual attention.

${blueprint_title}"
    exit 0
fi

# --- Step 6: Push branch and create PR ---
log "PUSH: pushing ${branch_name} to origin"

git -C "${WORKTREE_DIR}" push -u origin "${branch_name}" 2>&1 || {
    log "ERROR: failed to push branch"
    send_telegram "🔴 Auto-Build failed: could not push branch for ${blueprint_id}"
    exit 1
}

pr_url=$(cd "${WORKTREE_DIR}" && gh pr create \
    --title "auto-build: ${blueprint_title}" \
    --body "$(cat <<EOF
## Auto-Build: ${blueprint_id}

${build_summary}

**Tasks completed:** ${tasks_closed:-unknown}
**Budget:** \$${BUILD_BUDGET} max

---

Blueprint: \`bd show ${blueprint_id}\`

> This PR was created automatically by auto-build.sh.
> Review carefully before merging.
EOF
)" 2>&1) || {
    log "ERROR: failed to create PR"
    # Still notify with branch link
    send_telegram "🟡 Auto-Build: pushed branch but PR creation failed

Branch: ${branch_name}
Blueprint: ${blueprint_id} — ${blueprint_title}
${build_summary}"
    exit 1
}

log "PR: created ${pr_url}"

# --- Step 7: Notify via Telegram ---
message="🔨 <b>Auto-Build Complete</b>

<b>${blueprint_id}</b> — ${blueprint_title}

${build_summary}
Tasks closed: ${tasks_closed:-?}

<a href=\"${pr_url}\">Review PR</a>"

send_telegram "${message}"
log "DONE: notified user — ${pr_url}"
