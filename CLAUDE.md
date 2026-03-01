# Relay

Relay routes messages between Telegram bots and Claude Code agents. Each project gets its own bot — agent identity lives in the project's CLAUDE.md, not in Relay. Relay is just plumbing.

## Architecture

```
Telegram bot A ──► telegram.py ──► intake.py ──► agent.py ──► claude -p ... (cwd=project_dir_A)
Telegram bot B ──► telegram.py ──► intake.py ──► agent.py ──► claude -p ... (cwd=project_dir_B)
                                                    │
                                               store.py (SQLite)
```

One process, N bots. Each bot runs its own polling loop via `python-telegram-bot`. Sessions are keyed by `(agent_name, chat_id)`.

## Modules

| Module | Purpose |
|--------|---------|
| `config.py` | Load `relay.yaml`, validate, return typed `RelayConfig` |
| `telegram.py` | Per-agent Telegram bot adapters (auth, voice download, chunking) |
| `intake.py` | Classify messages (forward/new_session/status) via Claude haiku |
| `agent.py` | Spawn `claude -p` subprocesses, manage session resume/expiry |
| `store.py` | SQLite CRUD for sessions and messages |
| `voice.py` | Voice transcription via vox or OpenAI Whisper |

## Config

`relay.yaml` (gitignored) — see `relay.yaml.example` for template:

```yaml
agents:
  agent_name:
    bot_token: ${ENV_VAR}
    allowed_users: [telegram_user_id]
    project_dir: "/path/to/project"
    allowed_tools: ["Read", "Glob", "Grep", "Write", "Edit", "Bash", "Agent"]
    model: "sonnet"
    timeout: 900
    session_ttl: 14400
    max_budget: 1.0
```

Bot tokens and API keys live in `.env` (loaded by systemd).

## Adding a New Agent

1. Create a Telegram bot via @BotFather
2. Add a CLAUDE.md to your project directory
3. Add 10 lines to `relay.yaml`
4. Add bot token to `.env`
5. `sudo systemctl restart relay`

## Running

```bash
sudo systemctl start relay    # start
sudo systemctl status relay   # check health
sudo journalctl -u relay -f   # tail logs
```

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```
