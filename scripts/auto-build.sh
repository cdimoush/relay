#!/usr/bin/env bash
# auto-build.sh — Nightly autonomous blueprint builder (v2).
# Runs at 3am CT (9:00 UTC) via cron, one hour after auto-blueprint.sh.
#
# Cron entry:
#   0 9 * * * /home/ubuntu/relay/scripts/auto-build.sh >> /home/ubuntu/relay/logs/auto-build.log 2>&1
#
# Chain: idle check → blueprint selection → Sonnet filter → worktree build → PR → notify
#
# v2 changes from v1:
# - Sonnet filter determines which repo a blueprint targets (not hardcoded to relay)
# - Single-repo only — multi-repo blueprints are skipped
# - Worktree created in the target repo, not always relay

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
LOGS_DIR="${RELAY_DIR}/logs"
LOG_FILE="${LOGS_DIR}/auto-build.log"
ADMIN_CHAT_ID="8352167398"
BUILD_BUDGET="10.00"
FILTER_BUDGET="0.05"
DATE_STAMP=$(date -u '+%Y%m%d')

# Known repos — map name to directory
declare -A REPO_DIRS=(
    [relay]="/home/ubuntu/relay"
    [memories]="/home/ubuntu/memories"
    [clone]="/home/ubuntu/clone"
    [cyborg]="/home/ubuntu/cyborg"
    [isaac_research]="/home/ubuntu/isaac_research"
    [aura]="/home/ubuntu/aura"
    [gtc_wingman]="/home/ubuntu/gtc_wingman"
)

mkdir -p "${LOGS_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "${LOG_FILE}"
}

# Track which repo's worktree we created (for cleanup)
ACTIVE_WORKTREE=""
ACTIVE_REPO_DIR=""

cleanup() {
    if [[ -n "${ACTIVE_WORKTREE}" && -d "${ACTIVE_WORKTREE}" ]]; then
        log "CLEANUP: removing worktree ${ACTIVE_WORKTREE}"
        git -C "${ACTIVE_REPO_DIR}" worktree remove "${ACTIVE_WORKTREE}" --force 2>/dev/null || true
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

# --- Step 3: Sonnet filter — determine target repo ---
log "FILTER: analyzing blueprint for target repo"

# Collect blueprint + children descriptions for the filter
blueprint_detail=$(cd "${RELAY_DIR}" && bd show "${blueprint_id}" --json 2>/dev/null || echo "[]")

repo_names=$(printf '%s\n' "${!REPO_DIRS[@]}" | sort | paste -sd, -)

filter_output=$(claude -p "You are a classification tool. Given the following blueprint and its sub-tasks (in JSON), determine which SINGLE repository this blueprint targets.

Known repositories: ${repo_names}

Rules:
- If all tasks target exactly ONE repo, output that repo name
- If tasks span MULTIPLE repos, output null (this blueprint needs manual work)
- Base your decision on file paths, module names, and descriptions in the tasks

Output ONLY valid JSON, nothing else:
{\"repo\": \"<repo_name>\" or null, \"reason\": \"one sentence explanation\"}

Blueprint JSON:
${blueprint_detail}" \
    --model sonnet \
    --max-turns 1 \
    --max-budget-usd "${FILTER_BUDGET}" \
    --dangerously-skip-permissions \
    --output-format text 2>/dev/null) || true

# Parse the filter response
target_repo=$("${RELAY_DIR}/.venv/bin/python" -c "
import json, sys
try:
    # Extract JSON from response (may have markdown fences)
    text = '''${filter_output}'''
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith('\`\`\`'):
        lines = text.split('\n')
        text = '\n'.join(lines[1:-1])
    data = json.loads(text)
    repo = data.get('repo')
    reason = data.get('reason', 'no reason given')
    if repo is None:
        print(f'NULL|{reason}')
    else:
        print(f'{repo}|{reason}')
except Exception as e:
    print(f'ERROR|Failed to parse filter response: {e}')
" 2>/dev/null || echo "ERROR|filter script failed")

filter_verdict=$(echo "${target_repo}" | cut -d'|' -f1)
filter_reason=$(echo "${target_repo}" | cut -d'|' -f2-)

if [[ "${filter_verdict}" == "NULL" ]]; then
    log "SKIP: multi-repo blueprint — ${filter_reason}"
    send_telegram "⚪ Auto-Build skipped: <b>${blueprint_id}</b>

${blueprint_title}

Reason: multi-repo blueprint (needs manual work)
${filter_reason}"
    exit 0
fi

if [[ "${filter_verdict}" == "ERROR" ]]; then
    log "ERROR: filter failed — ${filter_reason}"
    send_telegram "🔴 Auto-Build filter error for ${blueprint_id}

${filter_reason}"
    exit 1
fi

# Validate the repo name
if [[ -z "${REPO_DIRS[${filter_verdict}]+x}" ]]; then
    log "ERROR: unknown repo '${filter_verdict}' from filter"
    send_telegram "🔴 Auto-Build: filter returned unknown repo '${filter_verdict}' for ${blueprint_id}"
    exit 1
fi

target_repo_dir="${REPO_DIRS[${filter_verdict}]}"

if [[ ! -d "${target_repo_dir}/.git" ]]; then
    log "ERROR: ${target_repo_dir} is not a git repo"
    send_telegram "🔴 Auto-Build: ${target_repo_dir} is not a git repo"
    exit 1
fi

log "FILTER: target repo = ${filter_verdict} (${target_repo_dir}) — ${filter_reason}"

# --- Step 4: Create worktree in the target repo ---
branch_name="auto/${blueprint_id}"
worktree_dir="/tmp/${filter_verdict}-build-${DATE_STAMP}"

# Set for cleanup trap
ACTIVE_WORKTREE="${worktree_dir}"
ACTIVE_REPO_DIR="${target_repo_dir}"

# Clean up stale worktree if it exists from a previous failed run
if [[ -d "${worktree_dir}" ]]; then
    log "CLEANUP: removing stale worktree from previous run"
    git -C "${target_repo_dir}" worktree remove "${worktree_dir}" --force 2>/dev/null || true
fi

# Determine the main branch name for this repo
main_branch=$(git -C "${target_repo_dir}" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "")
if [[ -z "${main_branch}" ]]; then
    # Fallback: check for master or main
    if git -C "${target_repo_dir}" rev-parse --verify master >/dev/null 2>&1; then
        main_branch="master"
    elif git -C "${target_repo_dir}" rev-parse --verify main >/dev/null 2>&1; then
        main_branch="main"
    else
        log "ERROR: cannot determine main branch for ${target_repo_dir}"
        send_telegram "🔴 Auto-Build: cannot determine main branch for ${filter_verdict}"
        exit 1
    fi
fi

# Check if branch already exists (previous partial run)
if git -C "${target_repo_dir}" rev-parse --verify "${branch_name}" >/dev/null 2>&1; then
    log "RESUME: branch ${branch_name} exists, checking out existing branch"
    git -C "${target_repo_dir}" worktree add "${worktree_dir}" "${branch_name}" 2>&1 || {
        log "ERROR: failed to create worktree from existing branch ${branch_name}"
        send_telegram "🔴 Auto-Build failed: could not create worktree for ${blueprint_id}"
        exit 1
    }
else
    git -C "${target_repo_dir}" worktree add "${worktree_dir}" -b "${branch_name}" "${main_branch}" 2>&1 || {
        log "ERROR: failed to create worktree"
        send_telegram "🔴 Auto-Build failed: could not create worktree for ${blueprint_id}"
        exit 1
    }
fi

log "WORKTREE: created at ${worktree_dir} on branch ${branch_name} (repo: ${filter_verdict})"

# --- Step 5: Invoke claude to build the blueprint ---
log "BUILD: starting claude build of ${blueprint_id} in ${filter_verdict} (budget: \$${BUILD_BUDGET})"

build_output=$(cd "${worktree_dir}" && claude -p "You are building blueprint ${blueprint_id} ('${blueprint_title}') in the ${filter_verdict} repository.

Steps:
1. Run: bd show ${blueprint_id}
2. Run: bd ready (to see which sub-tasks are unblocked)
3. For each ready sub-task, in dependency order:
   a. Read the task description
   b. Implement the changes (write code, edit files)
   c. Run tests if applicable
   d. git add the changed files and commit with a descriptive message
   e. Close the task: bd close <task-id>
4. After all tasks are done, output a summary in this exact format:
   BUILD_SUMMARY: <one-line description of what was built>
   TASKS_CLOSED: <number of tasks completed>

Important:
- You are working in a git worktree at ${worktree_dir}
- This is the ${filter_verdict} repository
- Commit after each sub-task
- Do NOT modify config files with secrets (relay.yaml, .env, etc.)
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

# --- Step 6: Check if there are actual changes to push ---
changes=$(git -C "${worktree_dir}" log "${main_branch}..HEAD" --oneline 2>/dev/null | wc -l)

if [[ "${changes}" -eq 0 ]]; then
    log "SKIP: no commits produced, nothing to push"
    send_telegram "⚪ Auto-Build: ${blueprint_id}
No changes produced. Blueprint may need manual attention.

${blueprint_title} (repo: ${filter_verdict})"
    exit 0
fi

# --- Step 7: Push branch and create PR ---
log "PUSH: pushing ${branch_name} to origin (${filter_verdict})"

git -C "${worktree_dir}" push -u origin "${branch_name}" 2>&1 || {
    log "ERROR: failed to push branch"
    send_telegram "🔴 Auto-Build failed: could not push branch for ${blueprint_id} (${filter_verdict})"
    exit 1
}

pr_url=$(cd "${worktree_dir}" && gh pr create \
    --title "auto-build: ${blueprint_title}" \
    --body "$(cat <<EOF
## Auto-Build: ${blueprint_id}

**Repository:** ${filter_verdict}

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
    send_telegram "🟡 Auto-Build: pushed branch but PR creation failed

Repo: ${filter_verdict}
Branch: ${branch_name}
Blueprint: ${blueprint_id} — ${blueprint_title}
${build_summary}"
    exit 1
}

log "PR: created ${pr_url}"

# --- Step 8: Notify via Telegram ---
message="🔨 <b>Auto-Build Complete</b>

<b>${blueprint_id}</b> — ${blueprint_title}
Repo: ${filter_verdict}

${build_summary}
Tasks closed: ${tasks_closed:-?}

<a href=\"${pr_url}\">Review PR</a>"

send_telegram "${message}"
log "DONE: notified user — ${pr_url}"
