#!/usr/bin/env bash
# install-cron.sh — Install relay system crons + agent crons from relay.yaml.
# Preserves any existing crontab entries. Idempotent (safe to re-run).
#
# Relay system crons (heartbeat, cleanup, digest, auto-blueprint) are hardcoded.
# Agent crons are read from the `crons` blocks in relay.yaml and routed through
# the generic agent-cron.sh wrapper.

set -euo pipefail

RELAY_DIR="/home/ubuntu/relay"

mkdir -p "${RELAY_DIR}/logs"

MARKER_START="# --- relay-crons-start ---"
MARKER_END="# --- relay-crons-end ---"

# --- Relay system crons (hardcoded) ---
SYSTEM_CRONS="# Heartbeat: every 5 min — health check + classify + throttled alert
*/5 * * * * ${RELAY_DIR}/scripts/heartbeat.sh >> ${RELAY_DIR}/logs/heartbeat.log 2>&1
# Session cleanup: hourly — expire stale sessions, purge old messages
0 * * * * ${RELAY_DIR}/scripts/session-cleanup.py >> ${RELAY_DIR}/logs/session-cleanup.log 2>&1
# Daily digest: 3x/day (8am, 2pm, 10pm EST = 13:00, 19:00, 03:00 UTC)
0 13 * * * ${RELAY_DIR}/scripts/daily-digest.sh >> ${RELAY_DIR}/logs/digest.log 2>&1
0 19 * * * ${RELAY_DIR}/scripts/daily-digest.sh >> ${RELAY_DIR}/logs/digest.log 2>&1
0 3 * * * ${RELAY_DIR}/scripts/daily-digest.sh >> ${RELAY_DIR}/logs/digest.log 2>&1
# Auto-blueprint: 2am CT (8:00 UTC)
0 8 * * * ${RELAY_DIR}/scripts/auto-blueprint.sh >> ${RELAY_DIR}/logs/auto-blueprint.log 2>&1"

# --- Agent crons (from relay.yaml) ---
AGENT_CRONS=$("${RELAY_DIR}/.venv/bin/python" - "${RELAY_DIR}/relay.yaml" "${RELAY_DIR}" <<'PYEOF'
import sys, os
import yaml
from pathlib import Path

config_path, relay_dir = sys.argv[1], sys.argv[2]

raw = Path(config_path).read_text()
expanded = os.path.expandvars(raw)
data = yaml.safe_load(expanded)

lines = []
for agent_name, agent_data in data.get("agents", {}).items():
    crons = agent_data.get("crons", [])
    if not crons:
        continue
    lines.append(f"# --- {agent_name} agent crons ---")
    for cron in crons:
        name = cron["name"]
        schedule = cron["schedule"]
        log_file = f"{relay_dir}/logs/{agent_name}-{name}.log"
        cmd = f"{relay_dir}/scripts/agent-cron.sh {agent_name} {name}"
        lines.append(f"{schedule} {cmd} >> {log_file} 2>&1")

print("\n".join(lines))
PYEOF
)

# --- Build the full cron block ---
CRON_BLOCK="${MARKER_START}
${SYSTEM_CRONS}"

if [[ -n "${AGENT_CRONS}" ]]; then
    CRON_BLOCK="${CRON_BLOCK}
${AGENT_CRONS}"
fi

CRON_BLOCK="${CRON_BLOCK}
${MARKER_END}"

# --- Install idempotently ---
EXISTING=$(crontab -l 2>/dev/null || true)

# Remove old relay entries (both old and new marker styles)
CLEANED=$(echo "${EXISTING}" | sed '/# --- relay-heartbeat-start ---/,/# --- relay-heartbeat-end ---/d' | sed "/${MARKER_START}/,/${MARKER_END}/d")

NEW_CRONTAB="${CLEANED}
${CRON_BLOCK}"

echo "${NEW_CRONTAB}" | crontab -

# --- Report what was installed ---
echo "Cron jobs installed:"
echo ""
echo "  System:"
echo "    heartbeat.sh:         every 5 min"
echo "    session-cleanup.py:   every hour"
echo "    daily-digest.sh:      8am, 2pm, 10pm EST"
echo "    auto-blueprint.sh:    2am CT"
echo ""

if [[ -n "${AGENT_CRONS}" ]]; then
    echo "  Agent crons (from relay.yaml):"
    echo "${AGENT_CRONS}" | grep -v '^#' | while read -r line; do
        # Extract schedule and agent/cron from the line
        cron_cmd=$(echo "${line}" | grep -oP 'agent-cron\.sh \K.*' | cut -d' ' -f1-2)
        schedule=$(echo "${line}" | awk '{print $1, $2, $3, $4, $5}')
        if [[ -n "${cron_cmd}" ]]; then
            echo "    ${cron_cmd}:  ${schedule}"
        fi
    done
    echo ""
fi

echo "Verify with: crontab -l"
