---
name: safe-restart
description: Validate relay changes and restart with automatic rollback on failure
triggers:
  - restart
  - restart relay
  - deploy
  - push changes
  - apply changes
  - reload service
  - go live
  - make it live
  - activate changes
  - restart the service
  - bounce the service
  - redeploy
allowed-tools: Bash, Read, Glob, Grep
---

# Safe Restart

Validate all relay changes through a gate pipeline, then restart the service with automatic rollback if it fails. This protects against losing Telegram access due to a bad deploy.

## When to Invoke

Run this skill whenever:
- The user asks to restart relay
- A code or config change requires `sudo systemctl restart relay` to take effect
- The agent would otherwise run `sudo systemctl restart relay` directly

**Never run `sudo systemctl restart relay` outside this skill.**

## Phase 1: Validation Gates

Run gates sequentially. If any **blocking** gate fails, stop immediately and report the failure. Do not restart.

### Gate 1: Git Safety Snapshot

```bash
git add -A && git commit -m "pre-restart snapshot (safe-restart)" --allow-empty
```

Capture the commit hash as `ROLLBACK_SHA`:
```bash
ROLLBACK_SHA=$(git rev-parse HEAD)
```

This is the revert target if restart fails. Report the short hash to the user.

### Gate 2: Syntax Check (blocking)

Compile every relay source module:
```bash
.venv/bin/python -m py_compile src/relay/main.py
.venv/bin/python -m py_compile src/relay/config.py
.venv/bin/python -m py_compile src/relay/telegram.py
.venv/bin/python -m py_compile src/relay/intake.py
.venv/bin/python -m py_compile src/relay/agent.py
.venv/bin/python -m py_compile src/relay/store.py
.venv/bin/python -m py_compile src/relay/voice.py
```

Run all in parallel. If any fails, report the exact error and **stop**.

### Gate 3: Import Check (blocking)

Verify the package loads in an isolated subprocess:
```bash
.venv/bin/python -c "
import relay.main
import relay.config
import relay.telegram
import relay.intake
import relay.agent
import relay.store
import relay.voice
print('OK')
"
```

Catches missing dependencies, circular imports, and top-level exceptions. If it fails, report stderr and **stop**.

### Gate 4: Config Validation (blocking)

Load `relay.yaml` without starting the service:
```bash
.venv/bin/python -c "
from relay.config import load_config
c = load_config()
print(f'{len(c.agents)} agent(s) configured')
for name in c.agents:
    print(f'  - {name}')
"
```

Catches bad YAML, unresolved env vars, missing fields, invalid project dirs. If it fails, report the error and **stop**.

### Gate 5: Test Suite (blocking)

```bash
.venv/bin/python -m pytest tests/ -x -q 2>&1
```

Fail-fast mode. If any test fails, report the output and **stop**.

### Gate 6: Lint (non-blocking)

```bash
.venv/bin/ruff check src/relay/ --quiet 2>&1 || true
```

Report any warnings but **do not block** the restart.

## Phase 2: Pre-Restart Report (send to user BEFORE restarting)

**Critical:** The validation summary MUST be sent to the user as a Telegram message BEFORE the restart happens. This is the user's last guaranteed communication — if the restart kills the process, they already have the results.

Build the report as a single message. Use this format:

**On gate failure (any blocking gate fails):**
```
Safe-restart validation:

[pass] Git snapshot (abc1234)
[pass] Syntax check (7 modules)
[FAIL] Import check:
  ImportError: No module named 'foo'

Restart aborted. Fix the error and retry /safe-restart.
```

Send this message and **stop**. Do not restart.

**On all gates passing:**
```
Safe-restart validation:

[pass] Git snapshot (abc1234)
[pass] Syntax check (7 modules)
[pass] Import check
[pass] Config valid (3 agents)
[pass] Tests passed (86 passed)
[warn] Lint: 2 warnings (non-blocking)

All gates passed. Restarting relay now...

If I don't respond after this, the restart may have failed.
Rollback SHA: abc1234
SSH recovery: git checkout abc1234 -- src/relay/ && sudo systemctl restart relay
```

Send this message, then proceed to Phase 3. The recovery instructions at the bottom are the user's lifeline — they tell the user exactly what to run via SSH if the restart fails and the bot goes silent.

## Phase 2.5: Delivery Delay

**Critical:** After sending the pre-restart report, wait 3 seconds to ensure the Telegram API has time to deliver the message. The relay process IS the agent — `systemctl restart` sends SIGTERM which kills everything. Without this delay, the message may be lost in flight.

```bash
sleep 3
```

## Phase 2.6: Launch Post-Restart Notifier

Before restarting, launch a background script that will survive the restart and send a confirmation message once relay is healthy (or report rollback status).

Determine the bot token and chat_id from the current conversation context. Write and launch the notifier:

```bash
cat > /tmp/relay-restart-notify.sh << 'SCRIPT'
#!/bin/bash
BOT_TOKEN="$1"
CHAT_ID="$2"
ROLLBACK_SHA="$3"

# Wait for relay to come back
for i in $(seq 1 20); do
    sleep 3
    if sudo systemctl is-active relay >/dev/null 2>&1; then
        # Check logs for tracebacks
        LOGS=$(sudo journalctl -u relay --since "15 seconds ago" --no-pager -q 2>&1)
        if echo "$LOGS" | grep -qiE "Traceback|Error|Exception"; then
            # Unhealthy — attempt rollback
            cd /home/ubuntu/relay
            git checkout "$ROLLBACK_SHA" -- src/relay/
            sudo systemctl restart relay
            sleep 3
            if sudo systemctl is-active relay >/dev/null 2>&1; then
                MSG="Restart failed — rolled back to ${ROLLBACK_SHA:0:7}. Relay is back online. Inspect and fix before retrying."
            else
                # Rollback also failed — user must SSH in
                rm -f /tmp/relay-restart-notify.sh
                exit 1
            fi
        else
            MSG="Restart complete. Relay is back online."
        fi
        curl -s "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="$CHAT_ID" \
            -d text="$MSG" > /dev/null 2>&1
        rm -f /tmp/relay-restart-notify.sh
        exit 0
    fi
done

# Timed out waiting for relay — attempt rollback
cd /home/ubuntu/relay
git checkout "$ROLLBACK_SHA" -- src/relay/
sudo systemctl restart relay
sleep 3
if sudo systemctl is-active relay >/dev/null 2>&1; then
    MSG="Restart failed (service didn't come back within 60s) — rolled back to ${ROLLBACK_SHA:0:7}. Relay is back online."
    curl -s "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="$CHAT_ID" \
        -d text="$MSG" > /dev/null 2>&1
fi
rm -f /tmp/relay-restart-notify.sh
SCRIPT
chmod +x /tmp/relay-restart-notify.sh
```

Launch it with nohup so it survives the restart:
```bash
nohup /tmp/relay-restart-notify.sh "$BOT_TOKEN" "$CHAT_ID" "$ROLLBACK_SHA" > /tmp/relay-restart-notify.log 2>&1 &
```

The BOT_TOKEN and CHAT_ID must be extracted from the agent's config context. BOT_TOKEN comes from the relay agent's config in relay.yaml (after env var substitution). CHAT_ID is the current Telegram chat ID from the conversation.

To get these values:
```bash
# BOT_TOKEN: read from .env file
BOT_TOKEN=$(grep RELAY_BOT_TOKEN /home/ubuntu/relay/.env | cut -d= -f2)

# CHAT_ID: read from the most recent relay session in the database
CHAT_ID=$(.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('relay.db')
row = conn.execute(\"SELECT chat_id FROM sessions WHERE agent_name='relay' AND status='active' ORDER BY last_active_at DESC LIMIT 1\").fetchone()
print(row[0] if row else '')
conn.close()
")
```

## Phase 3: Restart

Only reached if all blocking gates passed, pre-restart report was sent, delivery delay elapsed, and notifier script is running.

### Step 1: Restart the service

```bash
sudo systemctl restart relay
```

The notifier script (Phase 2.6) handles everything from here — health check, rollback if needed, and sending the post-restart confirmation to the user via Telegram API.

The agent process is now dead. No further steps are executed by the skill.

## User Communication Contract

The skill sends exactly **two messages** on a successful restart:
1. **Pre-restart report** — validation results + recovery instructions (sent by the agent BEFORE restart)
2. **Post-restart confirmation** — "Relay is back online" (sent by the nohup notifier script via Telegram API AFTER healthy restart)

On failure:
- **Gate failure:** One message (validation report with error). No restart happens.
- **Restart failure + successful rollback:** Two messages (pre-restart report from agent, then rollback confirmation from notifier script).
- **Restart failure + rollback failure:** One message only (pre-restart report). User must SSH in using the recovery command from that message.

The key insight: the agent process dies on restart. All post-restart communication is handled by the nohup notifier script, which talks directly to the Telegram bot API via curl — it does not depend on relay being alive.

## Important Notes

- This skill never edits code. It only validates, restarts, and rolls back.
- The rollback uses `git checkout <sha> -- src/relay/` (targeted restore), NOT `git reset --hard`.
- `ROLLBACK_SHA` always points to a known-working state because it's committed before any restart attempt.
- If the user has uncommitted changes outside `src/relay/`, rollback won't touch them.
- The pre-restart message is the safety net. It MUST contain the rollback SHA and SSH recovery command.
