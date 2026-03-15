---
name: spawn
description: Scaffold a new bot into the relay organism — project dir, CLAUDE.md, .claude/, relay.yaml, .env, tracking beads
triggers:
  - spawn
  - create bot
  - new bot
  - add bot
  - make a bot
  - register bot
  - scaffold bot
  - bot factory
  - stand up a bot
  - spin up a bot
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Spawn — Bot Factory Skill

Scaffold a new bot into the relay organism. Creates the project directory, CLAUDE.md, .claude/ folder, relay.yaml entry, .env token line, and tracking beads. Handles two user postures: savvy (has token + details upfront) or exploratory (just a name).

## When to Invoke

Run this skill when the user wants to add a new bot/agent to the relay network. Recognize both explicit requests ("spawn a bot called wingman") and implicit ones ("I need a new bot for GTC").

## Inputs

Parse from the user's message:
- **bot_name** (required) — lowercase, underscores OK. This becomes the dir name and relay.yaml key.
- **purpose** (optional) — what the bot does, one sentence.
- **skills** (optional) — list of capabilities the bot should have. Only include what the user actually described.
- **bot_token** (optional) — Telegram bot token from @BotFather. User may provide upfront or later.
- **model** (optional) — Claude model to use. Default: "opus".

If bot_name is missing, ask for it. For everything else, don't ask — use what was given, leave out what wasn't.

## Process

### Step 1: Create Project Directory

```bash
mkdir -p /home/ubuntu/{bot_name}
mkdir -p /home/ubuntu/{bot_name}/notes  # if the bot takes notes
mkdir -p /home/ubuntu/{bot_name}/.claude/commands
```

Create only directories that make sense for the bot's stated purpose. Don't create empty structure for features the user didn't mention.

### Step 2: Generate CLAUDE.md

Write `/home/ubuntu/{bot_name}/CLAUDE.md`. Model after cyborg's structure:

```markdown
# {Bot Display Name}

You are {bot_name}, a {purpose} agent. You are accessed via Telegram through Relay — every message you receive is text (voice is already transcribed before it reaches you).

## How You Work

**Natural language is primary.** Most messages won't use slash commands. Recognize intent:

{Only include intents that match the bot's stated skills. Don't invent features.}

## Response Style

Keep responses **concise** — the user reads these on a phone screen. No walls of text. Use short paragraphs and bullet points. Skip preamble.

{Skill-specific sections — only for skills the user described}

## Available Commands

| Command | Purpose |
|---------|---------|
{Only commands that map to described skills}
```

Then append the cyborg bridge snippet from `/home/ubuntu/relay/docs/cyborg-bridge-snippet.md`.

**Critical rule: Do not add features, skills, or commands the user didn't ask for.** If the user said "notes bot," the CLAUDE.md says "you take notes." No contact management, no scheduling, no recall — unless asked.

### Step 3: Create .claude/ Configuration

Write `/home/ubuntu/{bot_name}/.claude/settings.json`:
```json
{
  "permissions": {
    "allow": [
      "Bash(mkdir:*)",
      "Bash(find:*)",
      "Bash(grep:*)",
      "Bash(ls:*)",
      "Bash(cat:*)",
      "Bash(touch:*)",
      "Bash(bd:*)",
      "Write",
      "Edit"
    ],
    "deny": [
      "Bash(rm -rf:*)"
    ]
  },
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bd prime 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

### Step 4: Append to relay.yaml

Read `/home/ubuntu/relay/relay.yaml`, then append:

```yaml

  {bot_name}:
    bot_token: ${{BOT_NAME_UPPER}_BOT_TOKEN}
    allowed_users:
      - 8352167398
    project_dir: "/home/ubuntu/{bot_name}"
    allowed_tools:
      - "Read"
      - "Glob"
      - "Grep"
      - "Edit"
      - "Write"
      - "Bash"
      - "Agent"
    model: "{model or opus}"
    timeout: 900
    session_ttl: 14400
    max_budget: 1.0
```

### Step 5: Append to .env

If the user provided a bot_token:
```
{BOT_NAME_UPPER}_BOT_TOKEN={actual_token}
```

If no token yet:
```
# {BOT_NAME_UPPER}_BOT_TOKEN=   # TODO: get from @BotFather
```

### Step 6: Generate Tracking Beads

Create an epic and sub-tasks to track the spawn process:

```bash
bd create --title="Spawn {bot_name}" --type=epic --priority=2 --description="Track spawning of {bot_name} bot into the organism"

# Task 1 — auto-close after step 5
bd create --title="Create project dir, CLAUDE.md, .claude/, relay config" --type=task --parent={epic_id} --priority=1
bd close {task1_id} --reason="Created by /spawn skill"

# Task 2 — only if no token provided
bd create --title="Create Telegram bot via @BotFather" --type=task --parent={epic_id} --priority=1 \
  --description="Go to @BotFather on Telegram. Send /newbot. Pick a display name and username. Copy the bot token and provide it here."

# Task 3 — only if no token provided
bd create --title="Register bot token in .env and relay.yaml" --type=task --parent={epic_id} --priority=1
bd dep add {task3_id} {task2_id}

# Task 4
bd create --title="Restart relay to activate {bot_name}" --type=task --parent={epic_id} --priority=1 \
  --description="Run /safe-restart to activate the new bot"

# Task 5
bd create --title="Verify {bot_name} responds on Telegram" --type=task --parent={epic_id} --priority=1 \
  --description="Send /start to the bot on Telegram and confirm it responds"
bd dep add {task5_id} {task4_id}
```

### Step 7: Report to User

**If token was provided (savvy user):**
```
Spawned {bot_name}:
• Project: /home/ubuntu/{bot_name}/
• CLAUDE.md: ✓ ({N} skills)
• relay.yaml: ✓
• .env: ✓

Ready for /safe-restart to activate.
```

**If no token (exploratory user):**
```
Spawned {bot_name}:
• Project: /home/ubuntu/{bot_name}/
• CLAUDE.md: ✓ ({N} skills)
• relay.yaml: ✓ (token placeholder)

Next step: Go to @BotFather on Telegram:
1. Send /newbot
2. Pick a display name (e.g., "GTC Wingman")
3. Pick a username (e.g., gtc_wingman_bot)
4. Copy the token and send it here

I'll finish the registration when you have the token.
```

## Rules

- **Don't invent features.** Only scaffold what the user described. A vague "notes bot" gets a minimal CLAUDE.md, not a full-featured knowledge system.
- **Don't over-engineer.** No databases, no APIs, no complex directory structures unless the user's requirements clearly need them.
- **Cyborg bridge always included.** Every bot gets read access to the brain.
- **Never store tokens in CLAUDE.md or committed files.** Tokens go in .env only.
- **Use /safe-restart for activation.** Never raw `sudo systemctl restart relay`.
