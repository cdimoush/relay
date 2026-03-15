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

## Phase 3: Restart with Health Check

Only reached if all blocking gates passed AND the pre-restart report was sent.

### Step 1: Restart the service

```bash
sudo systemctl restart relay
```

### Step 2: Health check (wait, then verify)

```bash
sleep 3
sudo systemctl is-active relay
```

If status is `active`, check logs for hidden errors:
```bash
sudo journalctl -u relay --since "10 seconds ago" --no-pager -q
```

Scan the log output for Python tracebacks (`Traceback`, `Error`, `Exception`). If found, treat as **unhealthy**.

### Step 3: Healthy → Send post-restart confirmation

If the service came back healthy, send:
```
Restart complete. Relay is back online.
```

This confirms to the user that the restart succeeded and the bot is responsive.

### Step 4: Unhealthy → Automatic rollback

If the service is not `active` or logs contain tracebacks:

1. Restore source from the snapshot:
   ```bash
   git checkout {ROLLBACK_SHA} -- src/relay/
   ```

2. Restart again:
   ```bash
   sudo systemctl restart relay
   sleep 3
   sudo systemctl is-active relay
   ```

3. If rollback succeeds, send:
   ```
   Restart failed — rolled back to {ROLLBACK_SHA_short}. Relay is back online.
   The failed changes are still in git history. Inspect and fix before retrying.
   ```

4. If rollback also fails, the user is on their own via SSH.
   The pre-restart report already gave them the recovery command.

## User Communication Contract

The skill sends exactly **two messages** on a successful restart:
1. **Pre-restart report** — validation results + recovery instructions (sent BEFORE restart)
2. **Post-restart confirmation** — "Relay is back online" (sent AFTER healthy restart)

On failure:
- **Gate failure:** One message (validation report with error). No restart happens.
- **Restart failure + successful rollback:** Two messages (pre-restart report, then rollback confirmation).
- **Restart failure + rollback failure:** One message only (pre-restart report). User must SSH in using the recovery command from that message.

## Important Notes

- This skill never edits code. It only validates, restarts, and rolls back.
- The rollback uses `git checkout <sha> -- src/relay/` (targeted restore), NOT `git reset --hard`.
- `ROLLBACK_SHA` always points to a known-working state because it's committed before any restart attempt.
- If the user has uncommitted changes outside `src/relay/`, rollback won't touch them.
- The pre-restart message is the safety net. It MUST contain the rollback SHA and SSH recovery command.
