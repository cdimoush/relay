# Relay

Route Telegram messages to Claude Code agents. One process, multiple bots, persistent sessions.

## What is this?

Relay connects Telegram bots to Claude Code agents running in project directories. Each project gets its own bot — you text a bot, Relay spawns `claude -p` in the project's directory (where it reads the project's CLAUDE.md), and sends the response back. Sessions persist across messages via `--resume`. Voice messages are transcribed automatically.

## Quick Start

```bash
# 1. Clone and install
git clone <repo> && cd relay
python -m venv .venv && .venv/bin/pip install -e .

# 2. Configure
cp relay.yaml.example relay.yaml   # edit with your bot tokens + project dirs
cp .env.example .env               # add bot tokens and API keys

# 3. Run
.venv/bin/python -m relay.main
```

## Configuration

Each agent in `relay.yaml` maps a Telegram bot to a project directory:

```yaml
agents:
  mybot:
    bot_token: ${MY_BOT_TOKEN}        # from @BotFather
    allowed_users: [123456789]        # your Telegram user ID
    project_dir: "/home/ubuntu/myproject"
    allowed_tools: ["Read", "Glob", "Grep", "Write", "Edit", "Bash", "Agent"]
    model: "sonnet"
    timeout: 900         # 15 min per call
    session_ttl: 14400   # 4 hour sessions
    max_budget: 1.0      # $1 per call

voice:
  backend: "vox"         # or "openai"

storage:
  db_path: "relay.db"
```

Agent identity (personality, instructions, available commands) lives in the project's own `CLAUDE.md` — Relay doesn't know or care what the agent does.

## Production

```bash
# Install systemd service
sudo cp relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now relay

# Manage
sudo systemctl status relay       # health check
sudo journalctl -u relay -f       # live logs
sudo systemctl restart relay      # after config changes
```

Auto-restarts on crash. Starts on boot.

## Architecture

~1,500 lines of Python. No frameworks, no containers.

```
Telegram ──► telegram.py ──► intake.py ──► agent.py ──► claude -p (cwd=project_dir)
                                              │
                                         store.py (SQLite)
```

- **telegram.py** — One polling loop per bot. Auth, voice download, response chunking.
- **intake.py** — Classifies messages (forward to agent / new session / status query).
- **agent.py** — Spawns `claude -p` subprocesses with `--resume` for session continuity.
- **store.py** — SQLite for sessions and message history. Keyed by `(agent_name, chat_id)`.
- **voice.py** — Transcription via [vox](https://github.com/cdimoush/vox) or OpenAI Whisper.

## Tests

```bash
.venv/bin/python -m pytest tests/ -v    # 73 tests
```
