# Agent-Bot: Executive Summary

## Problem

We have a Telegram bot that provides remote access to Claude Code for research tasks. It works, but it has four limitations that block broader use:

- **Single project** — hardcoded to `isaac_research`. No way to route messages to other codebases.
- **Stateless** — every message spawns a fresh Claude process. No memory of previous conversation turns.
- **Single platform** — Telegram only. No path to Slack, Discord, or anything else.
- **No history** — conversations vanish. No search, no audit trail, no way to find what was discussed last week.

## What We're Building

**agent-bot** — a lightweight Python system (~1,500 lines) that makes remote agent interaction portable. Multiple projects, persistent chat sessions, searchable conversation history, and pluggable messaging platforms.

One system, one SQLite database, one systemd service.

## Architecture

```
~/.agent-bot/
├── bot.db                          # SQLite — conversations, messages, sessions
├── config/
│   ├── platforms.yaml              # Platform credentials (Telegram token, Slack token, etc.)
│   ├── routing.yaml                # Chat → project mapping
│   └── projects/
│       └── isaac_research.yaml     # Per-project: working dir, allowed tools, timeouts
└── src/
    ├── main.py                     # Entry point
    ├── router.py                   # Message routing + voice transcription dispatch
    ├── agent_manager.py            # Session lifecycle + Claude subprocess management
    ├── store.py                    # SQLite operations
    ├── config.py                   # YAML config loading + hot-reload
    ├── models.py                   # Data models
    ├── transcription.py            # Voice → text pipeline (OpenAI Whisper API)
    └── adapters/
        ├── base.py                 # PlatformAdapter ABC
        ├── telegram.py             # ~300 lines
        ├── slack.py                # ~300 lines (Phase 2)
        └── discord.py              # ~300 lines (future)
```

### Message Flow

```
┌──────────┐     ┌────────┐     ┌───────────────┐     ┌──────────────┐
│ Platform │────▶│ Router │────▶│ Agent Manager │────▶│ SQLite Store │
│ Adapter  │     │        │     │               │     │              │
│          │◀────│        │◀────│ claude --resume│◀────│   bot.db     │
└──────────┘     └────────┘     └───────────────┘     └──────────────┘
```

1. **Platform Adapter** receives a message, normalizes it to a common format.
2. **Router** transcribes voice (if needed), resolves which project the chat maps to.
3. **Agent Manager** looks up or creates a Claude session (`--resume` with mapped session ID), runs the subprocess, captures output.
4. **SQLite Store** logs the exchange — both the user message and the agent response.
5. Response flows back out through the adapter to the user.

## Core Technical Details

- **Session persistence**: Claude Code's `--resume` flag maps each `chat_id` to a `session_id`. Conversations maintain full context across messages.
- **Permission scoping**: `--allowedTools` defines what Claude can do per project. No blanket `--dangerously-skip-permissions`.
- **Platform adapters**: Each platform is a ~300-line module implementing a common `PlatformAdapter` ABC. Adding a platform means writing one file.
- **SQLite with WAL mode**: Zero-infrastructure storage. Concurrent reads, queryable history, embedded in the service.
- **YAML config**: Per-project settings in `~/.agent-bot/config/projects/`. Hot-reloadable via `SIGHUP`.
- **Subprocess-per-message**: Each user message runs `claude --resume <session_id>` as a subprocess. Simple, isolated, no long-running API connections to manage.

## Operational Specs

| Parameter | Default | Notes |
|---|---|---|
| Session TTL | 4 hours | Configurable per project |
| Max concurrent sessions | 8 | Configurable globally |
| Message timeout | 15 minutes | Configurable per project |
| Auth model | Fail-closed | Unrecognized users get no response |
| Config reload | `SIGHUP` | No restart required for config changes |
| Process model | Single systemd service | One service manages all platforms |

## Phases and Timeline

### Phase 0 — Session Persistence (1-2 days)
Add `--resume` and `--allowedTools` to the existing Telegram bot. ~50 lines changed. The current bot gains session memory and scoped permissions immediately. No new infrastructure.

### Phase 1 — Full System Build (1 week)
Build agent-bot from scratch. Extract the platform adapter pattern, add SQLite storage, YAML config, multi-project routing. Runs alongside the existing bot during validation, then replaces it.

### Phase 2 — Slack Adapter (1 week)
Add Slack support. The same project becomes accessible from both Telegram and Slack simultaneously. Conversations are platform-specific but history is unified in SQLite.

### Phase 3 — Ongoing Polish
- Response streaming (chunked replies instead of waiting for full completion)
- `/new` command for explicit session reset
- Health monitoring and alerting
- Discord adapter if needed

## Dependencies

**Runtime:**
- Python 3.11+
- python-telegram-bot, aiosqlite, PyYAML, openai, pydub, ffmpeg
- Claude Code CLI (`--resume`, `--output-format json`, `--allowedTools`)

**Phase 2 adds:**
- slack-bolt

**Infrastructure:**
- Single Ubuntu server (existing)
- systemd service (existing pattern)
- No containers, no cloud services, no databases beyond SQLite
