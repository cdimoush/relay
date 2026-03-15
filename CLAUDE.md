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

---

## Cyborg Brain Access (Cross-Agent Memory)

You have read access to the user's personal knowledge base (Cyborg brain) at `/home/ubuntu/cyborg/brain/`. Use this to understand context the user may reference without repeating themselves.

When to check the brain:
- User references something vaguely ("that project we discussed", "the robotics thing")
- You need to understand user preferences or priorities
- A task requires context about the user's other projects or strategic thinking

How to search:
1. Quick scan: `grep -r "<keywords>" /home/ubuntu/cyborg/brain/notes/ --include="*.md" -l`
2. Check index: `grep "<keywords>" /home/ubuntu/cyborg/.cyborg/notes.jsonl`
3. Read overview: `/home/ubuntu/cyborg/brain/brain.md` (table of contents)
4. Read matches: open the full note files for context

Rules:
- Read-only. Never write to cyborg's brain — only cyborg does that.
- Keep searches brief — read 1-3 matching notes max.
- Cite sources when using brain context: "(from cyborg: <note title>)"
