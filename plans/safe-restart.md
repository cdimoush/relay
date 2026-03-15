# Safe Restart Skill Design

## Problem

When the admin agent (me) needs to restart the relay service after code changes, a broken restart means **total loss of communication** — the user can only recover via SSH. There's no safety net between "edit code" and "restart service."

## Goal

A skill (`/safe-restart`) that validates changes before restarting relay, and provides automatic rollback if the restart fails. The skill should be triggered whenever a workflow would end in `sudo systemctl restart relay`.

## Trigger Conditions

Invoke this skill when:
- User explicitly asks to restart relay
- A code change workflow concludes and needs a restart to take effect
- Config changes to `relay.yaml` or `.env` require a restart

## Validation Pipeline

The skill runs a sequence of gates. If any gate fails, it **stops and reports** — no restart happens.

### Gate 1: Git Safety Snapshot
```bash
git add -A && git commit -m "pre-restart snapshot (safe-restart)"
```
Capture the current commit hash as `ROLLBACK_SHA`. This is the revert target if restart fails.

### Gate 2: Syntax Check
Run `py_compile` on every relay source module:
```bash
python -m py_compile src/relay/main.py
python -m py_compile src/relay/config.py
python -m py_compile src/relay/telegram.py
python -m py_compile src/relay/intake.py
python -m py_compile src/relay/agent.py
python -m py_compile src/relay/store.py
python -m py_compile src/relay/voice.py
```
All must pass. Any syntax error is a hard stop.

### Gate 3: Import Check
Verify the relay package loads cleanly in a subprocess:
```bash
python -c "import relay.main; import relay.config; import relay.telegram; import relay.intake; import relay.agent; import relay.store; import relay.voice; print('OK')"
```
Catches missing dependencies, circular imports, top-level runtime errors.

### Gate 4: Config Validation
Load and validate `relay.yaml` without starting the service:
```bash
python -c "from relay.config import load_config; c = load_config(); print(f'{len(c.agents)} agents configured')"
```
Catches bad YAML, missing env vars, invalid config fields.

### Gate 5: Test Suite
```bash
.venv/bin/python -m pytest tests/ -x -q
```
Run tests with fail-fast (`-x`). Any test failure is a hard stop.

### Gate 6: Lint (non-blocking warning)
```bash
ruff check src/relay/ --quiet
```
Report warnings but don't block restart on lint issues.

## Restart with Health Check

If all gates pass:

1. **Record pre-restart state:**
   - Save `ROLLBACK_SHA` (from Gate 1)
   - Save current systemd status

2. **Restart:**
   ```bash
   sudo systemctl restart relay
   ```

3. **Health check** (wait up to 10 seconds):
   ```bash
   sleep 3
   sudo systemctl is-active relay
   ```
   - If `active` → check journal for startup errors:
     ```bash
     sudo journalctl -u relay --since "10 seconds ago" --no-pager
     ```
   - Look for Python tracebacks or "error" in recent logs

4. **If healthy:** Report success with summary of what changed.

5. **If failed (not active or crash-looping):**
   - Immediately roll back:
     ```bash
     git checkout <ROLLBACK_SHA> -- src/relay/
     sudo systemctl restart relay
     ```
   - Wait and verify the rollback restart succeeds
   - Report what went wrong + the rollback

## Rollback Strategy

Two layers of safety:

1. **Pre-restart commit** — we can always `git checkout <sha> -- src/relay/` to restore the working version
2. **Systemd Restart=always** — even if the rollback restart takes a moment, systemd will keep trying

The rollback does NOT use `git reset --hard` — it only restores the `src/relay/` directory to avoid touching unrelated work.

## Skill UX Flow

```
User: "restart relay" or code change that needs restart

Agent: Running safe-restart validation...

  [✓] Git snapshot saved (abc1234)
  [✓] Syntax check passed (7 modules)
  [✓] Import check passed
  [✓] Config valid (3 agents)
  [✓] Tests passed (12/12)
  [~] Lint: 2 warnings (non-blocking)

  All gates passed. Restarting relay...

  [✓] Service restarted
  [✓] Health check passed — relay is active

  Restart complete. Rollback point: abc1234
```

Or on failure:
```
  [✓] Git snapshot saved (abc1234)
  [✗] Syntax check FAILED:
      src/relay/agent.py:42 — SyntaxError: unexpected indent

  Restart aborted. Fix the error and try again.
```

## File Location

```
.claude/skills/safe-restart/SKILL.md
```

## Skill Metadata

```yaml
name: safe-restart
description: Validate relay changes and restart with automatic rollback on failure
allowed-tools: Bash, Read, Glob, Grep
```

The skill should NOT need Write/Edit — it only validates and restarts, never modifies code itself.
