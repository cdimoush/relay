# Claude CLI Subprocess Behavior Research

Investigated: 2026-03-01

## 1. JSON Output Structure from `-p` Mode

**Command:** `claude -p "Say exactly: hello relay test" --output-format json`

**JSON structure returned:**

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "duration_ms": 1885,
  "duration_api_ms": 1707,
  "num_turns": 1,
  "result": "hello relay test",
  "stop_reason": null,
  "session_id": "d717019b-ca96-4902-b29b-d52b1e6d705a",
  "total_cost_usd": 0.0174695,
  "usage": {
    "input_tokens": 3,
    "cache_creation_input_tokens": 1256,
    "cache_read_input_tokens": 18909,
    "output_tokens": 6,
    "server_tool_use": { "web_search_requests": 0, "web_fetch_requests": 0 },
    "service_tier": "standard"
  },
  "modelUsage": {
    "claude-opus-4-6": {
      "inputTokens": 3,
      "outputTokens": 6,
      "cacheReadInputTokens": 18909,
      "cacheCreationInputTokens": 1256,
      "costUSD": 0.0174695,
      "contextWindow": 200000,
      "maxOutputTokens": 32000
    }
  },
  "permission_denials": [],
  "fast_mode_state": "off",
  "uuid": "c120dc52-c4bf-4dca-94cc-8551969e5a40"
}
```

**Key fields for Relay:**
- `result` -- the agent's text response (what to send back to the user)
- `session_id` -- UUID to store for `--resume` on subsequent messages
- `is_error` -- boolean, check this for error handling
- `total_cost_usd` -- useful for logging/monitoring
- `duration_ms` -- wall clock time
- `num_turns` -- how many tool-use turns the agent took
- `usage.output_tokens` -- for tracking token usage
- `uuid` -- unique message ID (different from session_id)

**First call behavior:** Works fine with no prior session. A new `session_id` is generated automatically and returned in the JSON.

**Invalid session resume behavior:** Exit code 1, stderr message:
```
No conversation found with session ID: 00000000-0000-0000-0000-000000000000
```
Relay should catch this and start a fresh session instead of surfacing the error.

## 2. `--allowedTools` Interaction with `-p` Mode

**Command:** `claude -p "..." --allowedTools Read Glob Grep --output-format json`

**Result:** Works. Space-separated tool names are accepted. The help text confirms:
```
--allowedTools, --allowed-tools <tools...>  Comma or space-separated list of tool names to allow (e.g. "Bash(git:*) Edit")
```

Both comma-separated and space-separated work. Tool names match the internal tool names: `Read`, `Glob`, `Grep`, `Edit`, `Write`, `Bash`, `Agent`, `WebSearch`, `WebFetch`.

You can also scope tools: `Bash(git:*)` allows only git commands in Bash.

**For Relay v1.0 config:**
```
--allowedTools Read Glob Grep Edit Write Bash Agent
```

## 3. `--append-system-prompt` Alongside CLAUDE.md

**Command:** `claude -p "What project are you in?" --append-system-prompt "You are a test agent." --output-format json` (cwd = `/home/ubuntu/isaac_research` which has a CLAUDE.md)

**Result:** Both CLAUDE.md AND the appended system prompt are active. The agent correctly identified the project from CLAUDE.md content and responded concisely per the appended prompt.

**Also tested `--system-prompt` (replaces, not appends):** Even with `--system-prompt`, CLAUDE.md still loads. The `--system-prompt` replaces the *default* system prompt but CLAUDE.md is loaded separately as project instructions. This means:

- CLAUDE.md always loads when the cwd contains one, regardless of `-p` mode or `--system-prompt`/`--append-system-prompt` usage
- `--append-system-prompt` adds to the default system prompt AND CLAUDE.md still loads
- `--system-prompt` replaces the default system prompt but CLAUDE.md still loads

**Recommendation for Relay:** Use `--append-system-prompt` with purpose.md content. CLAUDE.md in the project directory loads automatically -- no special handling needed. This is the best of both worlds: the agent gets its purpose/identity from Relay AND the project's native CLAUDE.md instructions.

## 4. Existing Isaac Research Telegram Bot Pattern

**File:** `/home/ubuntu/isaac_research/services/telegram_bot.py`

**Key patterns to preserve in Relay:**

### Subprocess spawning
```python
async def run_claude(prompt: str) -> str:
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # CRITICAL: prevents "nested session" error

    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt,
        "--dangerously-skip-permissions",
        "--append-system-prompt", SYSTEM_PROMPT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=REPO_ROOT,
        env=env,
        start_new_session=True,  # own process group for kill
    )
```

### Critical details:
1. **`env.pop("CLAUDECODE", None)`** -- Must unset this env var or Claude refuses to start with "cannot be launched inside another Claude Code session" error.
2. **`start_new_session=True`** -- Creates own process group so `os.killpg(proc.pid, SIGTERM)` kills the entire tree (claude + subagents).
3. **`stderr=asyncio.subprocess.STDOUT`** -- Merges stderr into stdout. For Relay with `--output-format json`, we may want `stderr=asyncio.subprocess.PIPE` separately to parse JSON from stdout and log errors from stderr.
4. **Timeout with `asyncio.wait_for`** -- 15 minute timeout with process group kill on expiry.
5. **Response chunking** -- Telegram has a 4096-char limit per message. The bot chunks responses.

### What the isaac bot does NOT have (Relay adds):
- No `--resume` / session persistence
- No `--output-format json` (just raw text)
- No intake router
- No `--allowedTools` (uses `--dangerously-skip-permissions` instead)
- No voice transcription via vox (uses OpenAI Whisper directly)

## 5. Deterministic Session IDs with `--session-id`

**Command:** `claude --session-id aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee -p "Say: deterministic session test" --output-format json`

**Result:** Works perfectly. The returned `session_id` matches exactly what was passed in:
```json
"session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
```

**Resume with that ID also works:**
```
claude --resume aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee -p "What did I ask?" --output-format json
```
Returns the correct context from the previous message.

**Implications for Relay:**
- We can generate our own UUIDs (e.g., based on chat_id + timestamp) and pass them with `--session-id` on first message
- OR we can let Claude generate the session_id on first call and store it for subsequent `--resume` calls
- **Recommendation:** Let Claude generate the session_id naturally (simpler, no edge cases). Store the returned `session_id` in SQLite on first call, use `--resume` for subsequent calls.

## Additional Findings

### `--model` flag
Works with aliases: `--model sonnet`, `--model opus`. The `modelUsage` field in JSON confirms the actual model used (e.g., `claude-sonnet-4-6`).

### `--no-session-persistence` flag
Available for cases where we don't want sessions saved to disk. Not needed for Relay (we want persistence).

### `--max-budget-usd` flag
Can set a per-call budget limit. Useful safety net: `--max-budget-usd 1.00` prevents runaway tool-use loops.

### `--fallback-model` flag
Enables automatic fallback when default model is overloaded. Only works with `--print`. Could be useful for reliability: `--model opus --fallback-model sonnet`.

### Env var: `CLAUDECODE`
Must be unset (`env.pop("CLAUDECODE", None)`) when spawning Claude as a subprocess from within a Claude session. Without this, Claude exits with code 1 and error message about nested sessions.

## Recommended Subprocess Pattern for Relay

```python
async def run_claude(prompt: str, session_id: str | None = None) -> dict:
    """Run claude -p and return parsed JSON response."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cmd = ["claude", "-p", prompt, "--output-format", "json"]

    if session_id:
        cmd.extend(["--resume", session_id])

    cmd.extend(["--allowedTools", "Read", "Glob", "Grep", "Edit", "Write", "Bash", "Agent"])
    cmd.extend(["--append-system-prompt", purpose_prompt])
    cmd.extend(["--model", config.model])
    cmd.extend(["--max-budget-usd", str(config.max_budget)])
    cmd.extend(["--dangerously-skip-permissions"])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=config.project_dir,
        env=env,
        start_new_session=True,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=config.timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        await proc.wait()
        return {"is_error": True, "result": f"Timed out after {config.timeout // 60} minutes."}

    if proc.returncode != 0:
        # Handle invalid session_id (expired/missing) by retrying without --resume
        error_text = stderr.decode()
        if "No conversation found" in error_text and session_id:
            return await run_claude(prompt, session_id=None)  # retry fresh
        return {"is_error": True, "result": error_text}

    return json.loads(stdout.decode())
```

**Key design decisions in this pattern:**
1. Separate stdout/stderr (JSON comes from stdout, errors from stderr)
2. Auto-retry on expired session (drop `--resume`, start fresh)
3. Process group kill on timeout
4. `--max-budget-usd` as safety net
5. `--append-system-prompt` for purpose.md (CLAUDE.md loads automatically from cwd)
