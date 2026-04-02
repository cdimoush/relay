#!/usr/bin/env bash
# auto-build.sh — Nightly autonomous blueprint builder (v2).
# Runs at 3am CT (9:00 UTC) via cron, one hour after auto-blueprint.sh.
#
# Cron entry:
#   0 9 * * * /home/ubuntu/relay/scripts/auto-build.sh >> /home/ubuntu/relay/logs/auto-build.log 2>&1
#
# Chain: idle check → blueprint selection (with target_repo filter) → worktree build → PR → notify
#
# v2 changes from v1:
# - Blueprints are tagged with target_repo metadata at creation time
# - Script filters for single-repo blueprints (target_repo is a single string, not a list)
# - Iterates through blueprints by priority, picks the first eligible one
# - Worktree created in the target repo, not always relay
# - No LLM filter call — pure metadata lookup

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
LOGS_DIR="${RELAY_DIR}/logs"
LOG_FILE="${LOGS_DIR}/auto-build.log"
ADMIN_CHAT_ID="8352167398"
BUILD_BUDGET="10.00"
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

# --- Step 2: Find highest-priority eligible blueprint ---
# Eligible = epic + blueprint label + target_repo metadata is a single known repo
blueprint_info=$(cd "${RELAY_DIR}" && bd list --status=open -l blueprint --json 2>/dev/null || echo "[]")

# Build valid repo names as a comma-separated string for the Python filter
valid_repos=$(printf '%s\n' "${!REPO_DIRS[@]}" | sort | paste -sd, -)

blueprint_line=$("${RELAY_DIR}/.venv/bin/python" -c "
import json, sys

valid_repos = set('${valid_repos}'.split(','))
data = json.loads('''${blueprint_info}''')
if not data:
    print('')
    sys.exit(0)

# Only epics (blueprints with sub-tasks)
epics = [d for d in data if d.get('type') == 'epic']
if not epics:
    print('')
    sys.exit(0)

# Sort by priority
epics.sort(key=lambda x: x.get('priority', 99))

# Find first eligible: has target_repo metadata that is a single known repo
for epic in epics:
    metadata = epic.get('metadata', {})
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}
    target = metadata.get('target_repo', '')
    if isinstance(target, str) and target in valid_repos:
        print(f\"{epic['id']}|{epic.get('title', 'untitled')}|{epic.get('priority', '?')}|{target}\")
        sys.exit(0)

# No eligible blueprint found — report what was skipped
skipped = []
for epic in epics:
    metadata = epic.get('metadata', {})
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (json.JSONDecodeError, TypeError):
            metadata = {}
    target = metadata.get('target_repo', None)
    if target is None:
        skipped.append(f\"{epic['id']}: no target_repo metadata\")
    elif isinstance(target, list):
        skipped.append(f\"{epic['id']}: multi-repo ({','.join(target)})\")
    else:
        skipped.append(f\"{epic['id']}: unknown repo '{target}'\")
print('SKIP|' + '; '.join(skipped))
" 2>/dev/null || echo "")

if [[ -z "${blueprint_line}" ]]; then
    log "SKIP: no open blueprints in backlog"
    exit 0
fi

# Check if the result is a SKIP report
if [[ "${blueprint_line}" == SKIP* ]]; then
    skip_reason="${blueprint_line#SKIP|}"
    log "SKIP: no eligible blueprints — ${skip_reason}"
    send_telegram "⚪ Auto-Build: no eligible blueprints tonight

Skipped:
${skip_reason}

Blueprints need <code>target_repo</code> metadata set to a single repo name."
    exit 0
fi

blueprint_id=$(echo "${blueprint_line}" | cut -d'|' -f1)
blueprint_title=$(echo "${blueprint_line}" | cut -d'|' -f2)
blueprint_priority=$(echo "${blueprint_line}" | cut -d'|' -f3)
target_repo=$(echo "${blueprint_line}" | cut -d'|' -f4)
target_repo_dir="${REPO_DIRS[${target_repo}]}"

log "SELECTED: ${blueprint_id} — ${blueprint_title} (P${blueprint_priority}, repo: ${target_repo})"

if [[ ! -d "${target_repo_dir}/.git" ]]; then
    log "ERROR: ${target_repo_dir} is not a git repo"
    send_telegram "🔴 Auto-Build: ${target_repo_dir} is not a git repo"
    exit 1
fi

# --- Step 3: Create worktree in the target repo ---
branch_name="auto/${blueprint_id}"
worktree_dir="/tmp/${target_repo}-build-${DATE_STAMP}"

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
    if git -C "${target_repo_dir}" rev-parse --verify master >/dev/null 2>&1; then
        main_branch="master"
    elif git -C "${target_repo_dir}" rev-parse --verify main >/dev/null 2>&1; then
        main_branch="main"
    else
        log "ERROR: cannot determine main branch for ${target_repo_dir}"
        send_telegram "🔴 Auto-Build: cannot determine main branch for ${target_repo}"
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

log "WORKTREE: created at ${worktree_dir} on branch ${branch_name} (repo: ${target_repo})"

# --- Step 4: Invoke claude to build the blueprint ---
log "BUILD: starting claude build of ${blueprint_id} in ${target_repo} (budget: \$${BUILD_BUDGET})"

build_output=$(cd "${worktree_dir}" && claude -p "You are building blueprint ${blueprint_id} ('${blueprint_title}') in the ${target_repo} repository.

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
- This is the ${target_repo} repository
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

# --- Step 5: Check if there are actual changes to push ---
changes=$(git -C "${worktree_dir}" log "${main_branch}..HEAD" --oneline 2>/dev/null | wc -l)

if [[ "${changes}" -eq 0 ]]; then
    log "SKIP: no commits produced, nothing to push"
    send_telegram "⚪ Auto-Build: ${blueprint_id}
No changes produced. Blueprint may need manual attention.

${blueprint_title} (repo: ${target_repo})"
    exit 0
fi

# --- Step 6: Push branch and create PR ---
log "PUSH: pushing ${branch_name} to origin (${target_repo})"

git -C "${worktree_dir}" push -u origin "${branch_name}" 2>&1 || {
    log "ERROR: failed to push branch"
    send_telegram "🔴 Auto-Build failed: could not push branch for ${blueprint_id} (${target_repo})"
    exit 1
}

pr_url=$(cd "${worktree_dir}" && gh pr create \
    --title "auto-build: ${blueprint_title}" \
    --body "$(cat <<EOF
## Auto-Build: ${blueprint_id}

**Repository:** ${target_repo}

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

Repo: ${target_repo}
Branch: ${branch_name}
Blueprint: ${blueprint_id} — ${blueprint_title}
${build_summary}"
    exit 1
}

log "PR: created ${pr_url}"

# --- Step 7: Notify via Telegram ---
message="🔨 <b>Auto-Build Complete</b>

<b>${blueprint_id}</b> — ${blueprint_title}
Repo: ${target_repo}

${build_summary}
Tasks closed: ${tasks_closed:-?}

<a href=\"${pr_url}\">Review PR</a>"

send_telegram "${message}"
log "DONE: notified user — ${pr_url}"
