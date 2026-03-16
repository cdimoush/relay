#!/usr/bin/env bash
# memories-question.sh — Send a daily interview question to Telegram.
# Runs once daily via cron (9am CT = 15:00 UTC).
# Reads profile + schedule state from /home/ubuntu/memories/ to pick the next question.

set -uo pipefail

RELAY_DIR="/home/ubuntu/relay"
MEMORIES_DIR="/home/ubuntu/memories"
CHAT_ID="8352167398"
BANK_FILE="${MEMORIES_DIR}/questions/bank.yaml"
SCHEDULE_FILE="${MEMORIES_DIR}/questions/schedule.yaml"
PROFILE_FILE="${MEMORIES_DIR}/profile/profile.yaml"

# Source .env for bot token
if [[ -f "${RELAY_DIR}/.env" ]]; then
    set -a
    source "${RELAY_DIR}/.env"
    set +a
fi

if [[ -z "${MEMORIES_BOT_TOKEN:-}" ]]; then
    echo "ERROR: MEMORIES_BOT_TOKEN not set"
    exit 1
fi

# Use Python to select the next question (YAML parsing + logic)
QUESTION=$("${RELAY_DIR}/.venv/bin/python" - <<'PYEOF'
import yaml
import random
import sys
from pathlib import Path
from datetime import date

MEMORIES_DIR = "/home/ubuntu/memories"
bank_path = Path(MEMORIES_DIR) / "questions" / "bank.yaml"
schedule_path = Path(MEMORIES_DIR) / "questions" / "schedule.yaml"
profile_path = Path(MEMORIES_DIR) / "profile" / "profile.yaml"

# Load files
with open(bank_path) as f:
    bank = yaml.safe_load(f)
with open(schedule_path) as f:
    schedule = yaml.safe_load(f)
with open(profile_path) as f:
    profile = yaml.safe_load(f)

questions = bank.get("questions", [])
asked_ids = {a["id"] for a in (schedule.get("asked") or [])}
phase = (profile.get("meta", {}).get("current_phase") or "trust_building")
phase_config = schedule.get("phase_config", {}).get(phase, {})
allowed_themes = phase_config.get("allowed_themes", ["identity", "values", "energy"])
max_depth = phase_config.get("max_depth", 1)

# Get depth scores to prioritize low-depth themes
depth_scores = profile.get("depth_scores", {})

# Filter to eligible questions
eligible = [
    q for q in questions
    if q["id"] not in asked_ids
    and q["theme"] in allowed_themes
    and q["depth_level"] <= max_depth
]

# If all eligible exhausted, allow repeats from allowed themes (but still respect depth)
if not eligible:
    eligible = [
        q for q in questions
        if q["theme"] in allowed_themes
        and q["depth_level"] <= max_depth
    ]

if not eligible:
    # Absolute fallback
    eligible = [q for q in questions if q["theme"] in allowed_themes]

if not eligible:
    print("What's on your mind today?")
    sys.exit(0)

# Weight toward themes with lower depth scores
def theme_weight(q):
    score = depth_scores.get(q["theme"], 0)
    return max(1, 10 - score)  # Lower depth = higher weight

weighted = []
for q in eligible:
    weighted.extend([q] * theme_weight(q))

selected = random.choice(weighted)

# Update schedule
if schedule.get("asked") is None:
    schedule["asked"] = []
schedule["asked"].append({
    "id": selected["id"],
    "date": str(date.today()),
    "depth_at_time": depth_scores.get(selected["theme"], 0)
})

with open(schedule_path, "w") as f:
    yaml.dump(schedule, f, default_flow_style=False, sort_keys=False)

print(selected["text"])
PYEOF
)

if [[ -z "${QUESTION}" ]]; then
    echo "ERROR: No question selected"
    exit 1
fi

# Send via Telegram
curl -s -X POST "https://api.telegram.org/bot${MEMORIES_BOT_TOKEN}/sendMessage" \
    -d chat_id="${CHAT_ID}" \
    -d text="${QUESTION}" > /dev/null 2>&1

echo "Sent question: ${QUESTION}"
