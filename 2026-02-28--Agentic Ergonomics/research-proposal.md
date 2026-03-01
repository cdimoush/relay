# Agentic Ergonomics: Research Proposal

## Problem Statement

We have a working Telegram bot (`services/telegram_bot.py`) that provides remote access to Claude Code for research tasks in the `isaac_research` repo. The bot works: text in, Claude response out, with voice transcription and local logging. But it's tightly coupled to this one repo and this one use case.

The goal is to make remote agent interaction **portable** — usable across multiple projects, with multiple concurrent agents, persistent chat history, and flexible messaging platform support (Telegram, Slack, Discord). The question is how to get there.

Three paths are on the table:

1. **Fully custom implementation** — Build everything from scratch. Maximum control, maximum maintenance burden. We own every line and every decision.
2. **Open-source adoption** — Use an existing agentic orchestration framework like OpenClaw to handle the hard parts. Faster to start, but we inherit their architecture decisions and maintenance trajectory.
3. **Hybrid approach** — Use open-source components where they fit (e.g., messaging adapters, queue systems) and build custom where we need control (agent lifecycle, security, project isolation).

The current bot has real limitations that motivate this work:

- **Single-project lock-in**: Hardcoded `REPO_ROOT`, `SYSTEM_PROMPT`, and `cwd` mean one bot = one project.
- **No chat history**: Every `claude -p` invocation is stateless. Context is lost between messages.
- **Single agent**: One Claude process at a time per message. No way to run specialized agents for different task types.
- **Platform coupling**: Built directly on `python-telegram-bot`. Adding Slack or Discord means rewriting handler logic.
- **No observability**: Logging is append-only markdown files. No way to query, search, or review past sessions.

---

## Research Questions

### 1. Research Lead — Prior Art & Codebase Constraints

**Focus area**: Existing patterns in `services/`, codebase constraints, and external prior art on agentic orchestrators.

#### Questions

- **What patterns in the current `services/telegram_bot.py` are worth preserving vs. replacing?**
  - The async subprocess model (`run_claude`) works but is stateless. Is there a way to maintain Claude sessions across messages without `claude -p`?
  - The auth model (env var allowlist) is simple and effective. Should it be preserved or replaced with something richer?
  - The voice transcription pipeline is self-contained and reusable. How should it be factored out?

- **What do existing agentic orchestration frameworks offer?**
  - Survey OpenClaw and similar projects (e.g., Rivet, LangGraph, CrewAI, AutoGen). What messaging platform integrations do they provide out of the box?
  - Which frameworks support persistent conversation state across messages?
  - What is the maintenance health of these projects (commit frequency, contributor count, issue resolution)?

- **What are the deployment constraints?**
  - The current bot runs as a systemd service on a single Ubuntu server. Does scaling to multiple agents/projects change this model?
  - How do other projects handle multi-tenant agent deployments on single machines vs. distributed setups?

### 2. Architecture Designer — System Structure & Integration

**Focus area**: How to structure the system, connect agents to messaging platforms, and support multiple projects.

#### Questions

- **What is the right abstraction boundary between messaging platforms and agent logic?**
  - Should we use an adapter pattern (one adapter per platform) or a message bus (all platforms push to a queue)?
  - How should inbound messages be routed to the correct agent/project? By chat ID? By command prefix? By separate bot instances?

- **How should agent lifecycle be managed?**
  - The current model spawns a new `claude -p` process per message. For persistent context, should we use long-running Claude sessions, or implement our own context window management?
  - How should multiple agents coexist — separate processes? Containerized? Worker pool with task routing?

- **How should chat history be stored and retrieved?**
  - Simple file-based storage (current markdown logs) vs. SQLite vs. a proper database?
  - What schema supports cross-project, cross-platform conversation history?
  - How much history should be injected into agent context on each message?

- **How should project isolation work?**
  - Each project needs its own `cwd`, system prompt, and potentially its own set of allowed tools/skills.
  - Should project config live in the project repo (like `CLAUDE.md`) or in a central registry?

### 3. Risk Analyst — Security, Trade-offs & Maintenance

**Focus area**: Security concerns, build-vs-buy trade-offs, and long-term maintenance burden.

#### Questions

- **What are the security implications of each approach?**
  - `--dangerously-skip-permissions` is currently used. What's the actual risk surface? Can we scope it down per-project?
  - How should secrets (API keys, bot tokens) be managed across multiple projects? Per-project `.env` files? A secrets manager?
  - If using open-source frameworks, what is their security posture? Have they been audited? Do they handle auth/authz?

- **What are the real trade-offs between custom vs. open-source?**
  - Custom: Full control, but we maintain everything. How many hours/week does maintenance realistically cost?
  - Open-source: Faster start, but we're locked into their abstractions. What happens when they make breaking changes or go unmaintained?
  - Hybrid: Best of both, but integration seams can be fragile. Where are the natural boundaries?

- **What is the migration risk from the current working system?**
  - The Telegram bot works today. Any new system must be at least as reliable.
  - What is the minimum viable migration path that doesn't break existing functionality?
  - Can we run old and new systems in parallel during transition?

- **What is the long-term maintenance burden?**
  - How many messaging platform SDKs do we realistically need to keep up with?
  - What happens when Claude Code's CLI interface changes (`claude -p` flags, session management)?
  - Is there a risk of over-engineering for a system that might only ever serve 1-2 users?

---

## Current System Reference

### `services/` Directory Structure

```
services/
├── telegram_bot.py          # Main bot: config, auth, handlers, Claude subprocess, logging
├── telegram-bot.service     # systemd unit file
├── requirements.txt         # python-telegram-bot, openai, pydub
├── bot.log                  # Runtime log
├── logs/                    # Saved prompt/response markdown files
└── tests/
    └── test_telegram_bot.py # Unit tests
```

### Key Implementation Details

| Aspect | Current State | Limitation |
|--------|--------------|------------|
| Agent invocation | `claude -p` subprocess | Stateless, no session persistence |
| Platform | Telegram only (`python-telegram-bot`) | No Slack/Discord support |
| Auth | Env var allowlist (`ALLOWED_USER_IDS`) | Single project, no per-project roles |
| Chat history | Markdown log files in `services/logs/` | Append-only, no retrieval or injection |
| Voice | Whisper transcription via OpenAI API | Works well, should be preserved |
| Deployment | systemd on single server | No multi-agent, no scaling |
| Project scope | Hardcoded to `isaac_research` | Cannot serve other repos |

---

## Scope Boundaries

**In scope for this research:**
- The `services/` directory and its patterns
- External agentic orchestration frameworks and their capabilities
- Architecture options for multi-project, multi-platform agent access
- Security and maintenance trade-off analysis

**Out of scope:**
- Isaac Sim / Isaac Lab source code (irrelevant to this problem)
- Actual implementation of a new system (this is research only)
- Pricing or cost analysis of API usage
- Mobile app development

---

## Research Findings: Research Lead

*Prior Art, Codebase Constraints, and External Framework Survey*

### Finding 1: Current Bot Patterns — Preserve vs. Replace

**Source**: `services/telegram_bot.py` (253 lines)

#### Worth Preserving

1. **Voice transcription pipeline** (`services/telegram_bot.py:72-120`)
   - `transcribe_voice()` → `chunk_audio()` → `transcribe()` chain is self-contained
   - Handles long audio via 5-minute chunking with 8-minute threshold (`CHUNK_DURATION_MS`, `CHUNK_THRESHOLD_MS` at lines 68-69)
   - Uses `gpt-4o-mini-transcribe` via OpenAI's async API (line 78)
   - Dependencies: `openai`, `pydub`, system `ffmpeg`
   - This pipeline is platform-agnostic — it takes `audio_bytes` in, returns `str` out. Can be extracted as a reusable module with zero changes.

2. **Auth model** (`services/telegram_bot.py:41-49`)
   - `parse_allowed_user_ids()` and `is_authorized()` are clean, testable functions
   - Env var allowlist (`ALLOWED_USER_IDS`) is simple and effective for single-user/small-team
   - Pattern is extensible: could add per-project scope without replacing the core mechanism (e.g., `ALLOWED_USER_IDS_projectname`)

3. **Response chunking** (`services/telegram_bot.py:165-172`)
   - `send_response()` splits at 4096 chars (Telegram's limit)
   - Pattern is sound: each platform adapter would need its own variant with platform-specific limits (Slack: 40,000 chars in blocks; Discord: 2,000 chars)

4. **Process group management** (`services/telegram_bot.py:150-161`)
   - `start_new_session=True` + `os.killpg()` for timeout cleanup ensures Claude subprocesses don't orphan
   - 15-minute timeout (`TIMEOUT = 900`, line 54) is reasonable for research tasks

#### Must Replace

1. **Hardcoded `REPO_ROOT`** (`services/telegram_bot.py:53`)
   ```python
   REPO_ROOT = "/home/ubuntu/isaac_research"
   ```
   Must become configurable per-project. Options: env var, config file, or chat-to-project routing.

2. **Hardcoded `SYSTEM_PROMPT`** (`services/telegram_bot.py:123-134`)
   ```python
   SYSTEM_PROMPT = (
       "You are running in the isaac_research repo. "
       ...
   )
   ```
   References `isaac_research` specifically. Should load from project config (CLAUDE.md already exists per-project).

3. **Stateless `claude -p` invocation** (`services/telegram_bot.py:137-162`)
   ```python
   async def run_claude(prompt: str) -> str:
       proc = await asyncio.create_subprocess_exec(
           "claude", "-p", prompt,
           "--dangerously-skip-permissions",
           ...
       )
   ```
   Every message spawns a fresh subprocess with no session continuity. This is the **#1 limitation** — see Finding 3 for the solution.

4. **Append-only markdown logging** (`services/telegram_bot.py:175-187`)
   ```python
   def save_locally(prompt: str, response: str, is_voice: bool = False) -> Path:
       log_file = LOG_DIR / f"{timestamp}.md"
   ```
   Write-only files in `services/logs/`. No retrieval, no search, no history injection into subsequent prompts.

#### Deployment Context

- **systemd service**: `services/telegram-bot.service` runs as `User=ubuntu`, loads env from `services/.env`, auto-restarts on failure (`Restart=always`, `RestartSec=5`)
- **Dependencies**: `services/requirements.txt` — `python-telegram-bot>=21.0`, `openai>=1.0`, `pydub>=0.25.1`, system `ffmpeg`
- Single-process model: one bot, one project, one platform

### Finding 2: OpenClaw — Most Relevant Prior Art

**Source**: [OpenClaw GitHub](https://github.com/nicepkg/openclaw) | [Architecture Overview](https://ppaolo.substack.com/p/openclaw-system-architecture-overview)

OpenClaw is a free, open-source AI agent framework by Peter Steinberger that functions as "an operating system for AI agents." It's the closest existing solution to what this project needs.

#### Architecture: Hub-and-Spoke

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Telegram    │  │   Discord   │  │    Slack    │
│  Adapter     │  │   Adapter   │  │   Adapter   │
└──────┬───────┘  └──────┬──────┘  └──────┬──────┘
       │                 │                 │
       └────────┬────────┴────────┬────────┘
                │                 │
         ┌──────▼──────────────────▼──────┐
         │     Gateway Control Plane      │
         │  (Node.js WebSocket server)    │
         │  127.0.0.1:18789              │
         └──────────────┬─────────────────┘
                        │
         ┌──────────────▼─────────────────┐
         │       Agent Runtime            │
         │  (PiEmbeddedRunner)            │
         │  Session → Context → Model →   │
         │  Tools → Persist               │
         └────────────────────────────────┘
```

#### Key Components

1. **Channel Adapters** — Built-in: Telegram (grammY), Discord (discord.js), Slack, WhatsApp (Baileys), Signal, iMessage. Each adapter handles:
   - Platform auth/credentials
   - Inbound message normalization to common format
   - Access control (allowlists, DM policies, group mention requirements)
   - Outbound formatting (respecting platform limits, markdown dialects)

2. **Session Management** — Sessions keyed as `agent:<agentId>:<channel>:<type>:<id>`:
   - Main sessions (`agent:X:main`): Full host access, no sandbox
   - DM sessions (`agent:X:telegram:dm:123`): Docker-sandboxed by default
   - Group sessions: Also sandboxed
   - Persistent JSON event logs per session with automatic compaction via summarization

3. **Memory System** — `~/.openclaw/memory/<agentId>.sqlite`:
   - Vector embeddings for semantic search
   - Hybrid BM25 + vector similarity retrieval
   - Daily notes (`memory/YYYY-MM-DD.md`) for reference context

4. **System Prompt Composition** — Stacked sources in precedence order:
   - `AGENTS.md` → `SOUL.md` → `TOOLS.md` → Skills → Memory search → Dynamic tool definitions

5. **Plugin System** — Discovery-based in `extensions/`:
   - Channel plugins (new messaging platforms)
   - Memory plugins (alternative storage)
   - Tool plugins (custom capabilities)
   - Provider plugins (self-hosted models)

6. **Message Flow** — Six phases: Ingestion → Access Control → Context Assembly → Model Invocation → Tool Execution → Response Delivery

#### Assessment for Our Needs

**Pros:**
- Solves messaging adapter abstraction, session persistence, multi-project isolation, and chat history out of the box
- Proven architecture with real-world usage
- Plugin system enables extensibility without forking

**Cons:**
- **Heavyweight**: Full Node.js ecosystem, not Python. Our existing code is Python.
- **Governance uncertainty**: Creator (Steinberger) announced joining OpenAI on Feb 14, 2026; project transitioning to an open-source foundation
- **Over-scoped**: Includes Canvas/A2UI, voice wake, scheduled actions, webhook triggers — features we don't need
- **Opinionated session model**: Docker sandboxing for DM/group sessions adds complexity we may not need for a 1-2 user system

#### Other Frameworks Compared

| Framework | Focus | Messaging Integration | Session Persistence | Language |
|-----------|-------|----------------------|-------------------|----------|
| **OpenClaw** | Full AI agent OS | Built-in (7+ platforms) | Yes (JSON + SQLite) | Node.js |
| **LangGraph** | Workflow DAGs | None built-in | Checkpointers (SQLite, Postgres) | Python |
| **CrewAI** | Role-based agent teams | None built-in | Limited | Python |
| **AutoGen (AG2)** | Conversational agents | None built-in | Memory stores | Python |

**Bottom line**: OpenClaw is the only framework that directly addresses messaging platform integration + agent session management. The others are agent orchestration frameworks that we'd still need to wrap with our own messaging layer.

### Finding 3: Claude Code Session Persistence — The Game Changer

**Source**: [Claude Code headless/SDK docs](https://code.claude.com/docs/en/headless)

Claude Code now supports **session continuity** programmatically via the CLI. This directly solves the stateless `claude -p` problem — the **#1 limitation** of the current bot.

#### Key Capabilities

1. **Session capture** via JSON output:
   ```bash
   claude -p "Start researching actuator models" --output-format json
   # Returns: { "result": "...", "session_id": "abc-123", ... }
   ```

2. **Session resume** with `--resume`:
   ```bash
   claude -p "Now look at the PD controller implementation" --resume "abc-123"
   # Continues with full context from previous exchange
   ```

3. **Continue most recent** with `--continue`:
   ```bash
   claude -p "Summarize your findings" --continue
   # Continues the last session without needing the ID
   ```

4. **Real-time streaming** with `--output-format stream-json`:
   ```bash
   claude -p "Explain recursion" --output-format stream-json --verbose --include-partial-messages
   # Newline-delimited JSON events for live token streaming
   ```

5. **Granular tool permissions** with `--allowedTools`:
   ```bash
   claude -p "Review this code" --allowedTools "Read,Grep,Glob"
   # Per-project tool scoping — replaces --dangerously-skip-permissions
   ```

#### Integration with Current Bot

The existing `run_claude()` function at `services/telegram_bot.py:137-162` can be modified to:

```python
# Pseudocode for session-aware invocation
async def run_claude(prompt: str, session_id: str = None) -> tuple[str, str]:
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if session_id:
        cmd.extend(["--resume", session_id])
    cmd.extend(["--allowedTools", project_config.allowed_tools])
    # ... subprocess execution ...
    result = json.loads(stdout)
    return result["result"], result["session_id"]
```

With a mapping of `telegram_chat_id → claude_session_id`, each Telegram chat maintains a persistent Claude session. This is the **single highest-impact improvement** — implementable in the existing bot with minimal changes, no framework adoption required.

#### Full Agent SDK

Beyond the CLI, Anthropic offers Python and TypeScript SDK packages (`claude_agent_sdk` / `@anthropic-ai/claude-agent-sdk`) with:
- Structured outputs and tool approval callbacks
- Native message objects for deeper integration
- Programmatic streaming with event callbacks

This provides a migration path from subprocess-based invocation to native SDK integration when needed.

### Finding 4: Messaging Platform API Comparison

**Sources**: [Telegram Bot Dev Guide 2025](https://wnexus.io/the-complete-guide-to-telegram-bot-development-in-2025/) | [Slack vs Discord vs Telegram 2025](https://ts2.tech/en/slack-vs-discord-vs-telegram-in-2025-which-one-is-really-best-for-you/)

#### Detailed Comparison

| Aspect | Telegram | Slack | Discord |
|--------|----------|-------|---------|
| **Primary SDK** | `python-telegram-bot` (Python) | `slack-bolt` (Python) | `discord.py` (Python) |
| **Alt SDKs** | `grammY` (TS), `Telegraf` (TS) | `slack-sdk` (Python) | `discord.js` (JS) |
| **Auth model** | Bot token + user ID allowlist | OAuth2 + workspace scoping | Bot token + guild permissions |
| **Message limit** | 4,096 chars | 40,000 chars (blocks) | 2,000 chars |
| **Delivery model** | Polling or Webhooks | Events API (webhooks) | Gateway (WebSocket) |
| **Voice messages** | Native OGG/Opus, easy download | Huddles (limited bot access) | Voice channels (complex, opus streams) |
| **File upload** | Bot API direct upload/download | Files API | Attachments API |
| **Rate limits** | ~30 msg/sec | 1 msg/sec per channel | 5 msg/sec per channel |
| **Threading** | Reply-to chains | Native threads | Native threads |
| **Markdown** | Custom (MarkdownV2, HTML) | `mrkdwn` (Slack-flavored) | Standard Markdown subset |
| **Best for** | Personal/mobile, voice, privacy | Enterprise/team, integrations | Community, real-time |
| **Bot discovery** | @BotFather, inline search | App Directory (vetted) | Bot listing sites |

#### Adapter Pattern Feasibility

OpenClaw demonstrates that each platform adapter is ~200-400 lines of code. The adapter's responsibility is:

```
Platform Message → normalize → InboundMessage(text, sender_id, chat_id, platform, attachments)
OutboundMessage(text, attachments) → format → Platform API call
```

A Python adapter interface would look like:

```python
class PlatformAdapter(ABC):
    async def start(self): ...
    async def send(self, chat_id: str, text: str): ...
    def on_message(self, callback: Callable[[InboundMessage], Awaitable[None]]): ...
```

#### Recommendation

1. **Keep Telegram as primary** — best voice support, generous rate limits, simplest bot API, already working
2. **Slack as second platform** — natural for enterprise/team use, `slack-bolt` has similar async patterns to `python-telegram-bot`
3. **Discord lowest priority** — 2,000 char limit is painful for agent responses, voice integration is complex, rate limits are tight

### Finding 5: Chat History Persistence Patterns

**Sources**: [OpenAI Agents SDK Sessions](https://openai.github.io/openai-agents-python/sessions/) | [LangGraph Memory Customization](https://focused.io/lab/customizing-memory-in-langgraph-agents-for-better-conversations) | [Microsoft Agent Memory](https://learn.microsoft.com/en-us/agent-framework/user-guide/agents/agent-memory) | [Unified Chat History (Medium)](https://medium.com/@mbonsign/unified-chat-history-and-logging-system-a-comprehensive-approach-to-ai-conversation-management-dc3b5d75499f)

#### Recommended: SQLite with Conversation Threading

**Why SQLite over the current markdown files:**
- Current `services/logs/` files are append-only — `services/telegram_bot.py:175-187` writes `{timestamp}.md` files that cannot be queried
- SQLite is embedded, zero-config, single-file — no database server needed
- Supports full SQL querying for search, filtering, and history retrieval
- Python's `sqlite3` is in the standard library; `aiosqlite` for async

#### Schema Design

```sql
-- Projects: multi-project support
CREATE TABLE projects (
    id          TEXT PRIMARY KEY,       -- e.g., "isaac_research"
    repo_root   TEXT NOT NULL,          -- e.g., "/home/ubuntu/isaac_research"
    system_prompt TEXT,                 -- loaded from CLAUDE.md or custom
    allowed_tools TEXT,                 -- comma-separated tool list
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Conversations: maps platform chats to Claude sessions
CREATE TABLE conversations (
    id              TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES projects(id),
    platform        TEXT NOT NULL,          -- "telegram", "slack", "discord"
    platform_chat_id TEXT NOT NULL,         -- platform-specific chat identifier
    claude_session_id TEXT,                 -- from --output-format json → session_id
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active     TIMESTAMP,
    UNIQUE(platform, platform_chat_id, project_id)
);

-- Messages: full history for search and retrieval
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT REFERENCES conversations(id),
    role            TEXT NOT NULL,          -- "user", "assistant"
    content         TEXT NOT NULL,
    is_voice        BOOLEAN DEFAULT FALSE,
    token_count     INTEGER,               -- for context window management
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for fast conversation lookups
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX idx_conversations_platform ON conversations(platform, platform_chat_id);
```

#### Context Window Management Strategy

1. **Primary**: Use Claude Code's built-in `--resume` for session continuity. Claude handles its own context compaction internally.
2. **Session recovery**: When a Claude session expires or errors, create a new session and inject a summary of recent messages from SQLite:
   ```python
   recent = db.execute(
       "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 20",
       (conv_id,)
   ).fetchall()
   summary_prompt = f"Previous conversation context:\n{format_messages(recent)}\n\nNew message: {user_input}"
   ```
3. **Long-term**: Optionally summarize older conversations (30+ messages) and store as a condensed entry, similar to OpenClaw's compaction approach.

#### Multi-Tier Pattern (from industry research)

For scaling beyond the basics:

| Tier | Storage | Purpose | Retention |
|------|---------|---------|-----------|
| Hot | SQLite (recent) | Fast retrieval for active conversations | Last 100 messages per conversation |
| Warm | SQLite (archive) | Searchable history | All messages, queryable |
| Cold | Compressed files | Long-term backup | Monthly archives |

For a 1-2 user system, the hot tier alone (SQLite) is sufficient. The warm/cold tiers are future optimization.

#### OpenClaw's Approach (for reference)

- Append-only event logs in `~/.openclaw/sessions/` — one file per session
- Vector embeddings in `memory/<agentId>.sqlite` using hybrid BM25 + vector similarity
- Automatic compaction: older conversations summarized to stay within context limits
- This is more sophisticated than needed initially but shows the natural scaling path

---

### Summary: Recommended Path

The research strongly suggests a **hybrid approach** (Option 3):

1. **Immediate win** (days): Add `--resume` / `--output-format json` to `run_claude()` in existing bot. Store `session_id` per chat. This alone solves the #1 pain point.
2. **Short-term** (1-2 weeks): Extract messaging adapter interface, add SQLite conversation history, make `REPO_ROOT`/`SYSTEM_PROMPT` configurable per-project.
3. **Medium-term** (weeks): Add Slack adapter using the same interface pattern. Factor voice pipeline into reusable module.
4. **Reference design**: Use OpenClaw's architecture as inspiration (hub-and-spoke, adapter pattern, session model) but build lightweight Python equivalents rather than adopting the full Node.js framework.

The current 253-line bot is close to being significantly more capable. The biggest gap (stateless sessions) is now solvable with Claude Code's `--resume` flag without any framework adoption.

---

## Research Findings: Architecture Designer

*System Structure, Integration Design, and Component Specifications*

This section answers the Architecture Designer research questions: abstraction boundaries, agent lifecycle, chat history storage, and project isolation. It builds on the Research Lead's findings above — particularly Finding 3 (`--resume` session persistence) and Finding 4 (platform API comparison) — and translates them into concrete system designs.

### Design 1: Messaging Abstraction Layer

**Question answered**: What is the right abstraction boundary between messaging platforms and agent logic?

#### Decision: Adapter Pattern (not Message Bus)

A message bus (Redis, RabbitMQ, NATS) adds infrastructure that must be deployed, monitored, and maintained. For a system serving 1-2 users on a single server, this complexity isn't justified. Instead, we use lightweight platform adapters that normalize messages to a common envelope — the same architectural choice OpenClaw makes (see Finding 2 above), but implemented as simple Python classes rather than a full plugin SDK.

**Why not a message bus:**
- Adds a runtime dependency (Redis/RabbitMQ server) to a single-machine deployment
- Introduces failure modes (queue down, message loss) that don't exist with direct adapters
- The current system handles ~10-50 messages/day — queuing infrastructure solves scaling problems we don't have
- If scaling needs change later, adapters can be refactored to push to a queue without changing the agent layer

#### Common Message Envelope

Every platform adapter normalizes inbound messages into this structure:

```python
@dataclass
class InboundMessage:
    """Platform-normalized inbound message."""
    platform: str              # "telegram" | "slack" | "discord"
    platform_user_id: str      # Platform-specific user identifier
    platform_chat_id: str      # Platform-specific chat/channel/DM ID
    text: str                  # Normalized text content (post-transcription for voice)
    audio_bytes: bytes | None  # Raw audio if voice message, None otherwise
    timestamp: datetime
    reply_to_message_id: str | None  # For threading context

@dataclass
class OutboundMessage:
    """Response to be sent back through a platform adapter."""
    text: str
    reply_to: str | None       # Platform message ID to reply to (for threading)
```

Response chunking (currently `send_response()` at `services/telegram_bot.py:165-172`) moves into each adapter, since limits differ by platform (Telegram: 4,096; Slack: 40,000 in blocks; Discord: 2,000 — per Finding 4).

#### Adapter Interface

```python
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

class PlatformAdapter(ABC):
    """Base class for messaging platform adapters."""

    @abstractmethod
    async def start(self) -> None:
        """Connect to platform and begin receiving messages."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect from platform."""
        ...

    @abstractmethod
    async def send(self, chat_id: str, message: OutboundMessage) -> None:
        """Send a message to a specific chat, handling platform-specific
        chunking, formatting, and rate limits."""
        ...

    def set_on_message(self, callback: Callable[[InboundMessage], Awaitable[None]]) -> None:
        """Register the callback invoked when a normalized message arrives."""
        self._on_message = callback
```

Each adapter implementation (~200-400 lines per OpenClaw's experience) handles:
1. **Platform SDK lifecycle** — bot token auth, polling/websocket connection
2. **Inbound normalization** — convert platform-specific `Update`/`Event` objects to `InboundMessage`
3. **Auth enforcement** — check allowlists before invoking `_on_message` (preserving the pattern from `services/telegram_bot.py:48-49`)
4. **Outbound formatting** — chunk text, convert markdown dialects, respect rate limits
5. **Voice handling** — download audio bytes from platform, populate `audio_bytes` field

The voice transcription pipeline (Finding 1, "Worth Preserving" #1) stays as a shared utility. Adapters that receive audio populate `audio_bytes`; a transcription step in the router converts it to `text` before routing to the agent.

#### System Topology

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Telegram    │  │   Slack     │  │  Discord    │
│  Adapter     │  │   Adapter   │  │  Adapter    │
└──────┬───────┘  └──────┬──────┘  └──────┬──────┘
       │                 │                │
       └────────┬────────┴────────┬───────┘
                │  InboundMessage │
         ┌──────▼─────────────────▼──────┐
         │          Router               │
         │  1. Transcribe voice (if any) │
         │  2. Resolve chat → project    │
         │  3. Dispatch to AgentManager  │
         └──────────────┬────────────────┘
                        │
         ┌──────────────▼────────────────┐
         │       Agent Manager           │
         │  (session lifecycle, claude)  │
         └──────────────┬────────────────┘
                        │
         ┌──────────────▼────────────────┐
         │       SQLite Store            │
         │  (history, sessions, config)  │
         └───────────────────────────────┘
```

#### Migration Path from Current Bot

The existing `telegram_bot.py` maps cleanly onto this architecture:

| Current code | New component |
|---|---|
| `handle_message()`, `handle_voice()` (lines 202-232) | `TelegramAdapter` |
| `is_authorized()` (line 48) | Auth check inside adapter |
| `transcribe_voice()` (lines 102-120) | Shared `VoiceTranscriber` utility |
| `run_claude()` (lines 137-162) | `AgentManager.run_prompt()` |
| `save_locally()` (lines 175-187) | `SQLiteStore.save_message()` |
| `send_response()` (lines 165-172) | `TelegramAdapter.send()` |
| `load_config()` (lines 15-38) | Per-project YAML config loading |

### Design 2: Agent Lifecycle Management

**Question answered**: How should agent lifecycle be managed? How should multiple agents coexist?

#### Session-Persistent Agent Model

Building on Finding 3 (`--resume` as the game changer), the agent manager maintains a mapping from `(project_id, platform_chat_id)` to a Claude Code session:

```python
@dataclass
class AgentSession:
    """Tracks a persistent Claude Code session."""
    session_id: str           # Claude Code session ID (from --output-format json)
    project_id: str           # Which project this session serves
    platform: str             # Which platform originated this session
    platform_chat_id: str     # Which chat this session belongs to
    created_at: datetime
    last_active: datetime
    status: str               # "active" | "idle" | "expired"

class AgentManager:
    """Manages Claude Code sessions across projects and chats."""

    async def get_or_create_session(
        self, project_id: str, platform: str, chat_id: str
    ) -> AgentSession:
        """Look up existing active session or create a new one.
        Returns session with valid session_id for --resume."""
        ...

    async def run_prompt(
        self, session: AgentSession, prompt: str, project: ProjectConfig
    ) -> tuple[str, str]:
        """Execute prompt against Claude Code, using --resume if session exists.
        Returns (response_text, updated_session_id)."""
        cmd = ["claude", "-p", prompt, "--output-format", "json"]
        if session.session_id:
            cmd.extend(["--resume", session.session_id])
        cmd.extend(["--allowedTools", project.allowed_tools])
        cmd.extend(["--append-system-prompt", project.system_prompt])
        # ... subprocess execution with process group management ...
        result = json.loads(stdout)
        return result["result"], result["session_id"]

    async def expire_idle_sessions(self, max_idle: timedelta) -> list[str]:
        """Mark sessions as expired if idle beyond threshold.
        Expired sessions start fresh on next message."""
        ...

    async def reset_session(self, project_id: str, chat_id: str) -> None:
        """Explicitly reset a session (user /new command)."""
        ...
```

#### Process Management: Serial per Chat, Concurrent across Chats

**Current model** (proven reliable): Each message spawns a `claude -p` subprocess, at most one per chat at a time. The bot sends "Thinking..." and blocks until the subprocess completes or times out (15 min).

**Extended model**: Keep the same serial-per-chat guarantee, but allow concurrent subprocesses across different chats/projects. This is already naturally supported by Python's `asyncio` — each `run_prompt()` call awaits its own subprocess independently.

```
Chat A (isaac_research):  msg1 ──[claude -p --resume A]──→ response
Chat B (other_project):   msg1 ──[claude -p --resume B]──→ response  (concurrent)
Chat A (isaac_research):  msg2 ──[waits for msg1]──[claude -p --resume A]──→ response
```

No worker pools, containers, or distributed systems needed. The OS handles process scheduling.

#### Session Expiry Strategy

Claude sessions accumulate context. At some point, the context window fills and performance degrades. The session expiry policy handles this:

1. **Time-based expiry**: Sessions idle for >4 hours are marked `expired`. Next message starts a fresh session. The 4-hour threshold is configurable per project.
2. **Explicit reset**: User sends `/new` to force a fresh session immediately.
3. **Error recovery**: If `--resume` fails (session corrupted, Claude Code upgraded), fall back to creating a new session and injecting recent history from SQLite (see Design 3).
4. **No proactive cleanup**: Don't kill idle Claude processes — they don't exist. Sessions are just IDs; the subprocess exits after each message. Only the session ID persists.

#### Why NOT Long-Running Processes

An alternative is keeping a Claude process alive between messages (e.g., via the interactive mode or Agent SDK with a persistent connection). Reasons to avoid this:

- **Resource cost**: Each idle Claude process holds memory for no benefit
- **Crash recovery**: If a long-running process dies, all session state is lost. With `--resume`, the session ID is stored in SQLite and survives process restarts, bot restarts, even server reboots
- **Simplicity**: Subprocess-per-message is the current proven model. `--resume` adds persistence without changing the execution model
- **Future path**: If we later need long-running agents (e.g., for streaming or interactive workflows), the Agent SDK provides that option. But the subprocess model handles the request-response pattern well.

### Design 3: Chat History Storage

**Question answered**: How should chat history be stored and retrieved? What schema supports cross-project, cross-platform history?

#### SQLite — Rationale

This design aligns with and expands the Research Lead's schema in Finding 5. The rationale is restated for completeness:

- Current markdown logs (`services/logs/*.md`) are write-only — no retrieval, no search, no injection into agent context
- SQLite is zero-infrastructure: single file, embedded, no server process, standard library support (`sqlite3`), async via `aiosqlite`
- Fits the deployment model: systemd service on a single Ubuntu server
- OpenClaw uses SQLite for its memory system (`~/.openclaw/memory/<agentId>.sqlite`) — validated at scale

#### Full Schema (refined from Finding 5)

```sql
-- Projects: one row per managed repository
CREATE TABLE projects (
    id              TEXT PRIMARY KEY,       -- slug, e.g., "isaac_research"
    name            TEXT NOT NULL,          -- human-readable name
    repo_path       TEXT NOT NULL,          -- absolute path, e.g., "/home/ubuntu/isaac_research"
    system_prompt   TEXT,                   -- appended via --append-system-prompt
    allowed_tools   TEXT,                   -- for --allowedTools, e.g., "Read,Edit,Bash,Glob,Grep"
    timeout_seconds INTEGER DEFAULT 900,    -- per-project subprocess timeout
    session_ttl_hours INTEGER DEFAULT 4,    -- session expiry threshold
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Conversations: maps (platform, chat) → project + Claude session
CREATE TABLE conversations (
    id                TEXT PRIMARY KEY,     -- UUID
    project_id        TEXT NOT NULL REFERENCES projects(id),
    platform          TEXT NOT NULL,        -- "telegram" | "slack" | "discord"
    platform_chat_id  TEXT NOT NULL,        -- platform-specific identifier
    claude_session_id TEXT,                 -- from claude -p --output-format json
    status            TEXT DEFAULT 'active', -- "active" | "expired" | "archived"
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active       TIMESTAMP,
    UNIQUE(platform, platform_chat_id, project_id)
);

-- Messages: complete history for audit, search, and recovery
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,          -- "user" | "assistant"
    content         TEXT NOT NULL,
    is_voice        BOOLEAN DEFAULT FALSE,  -- was this transcribed from voice?
    platform_msg_id TEXT,                   -- for reply threading on platform
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Users: cross-platform identity (optional, for multi-user scenarios)
CREATE TABLE users (
    id              TEXT PRIMARY KEY,       -- internal user ID
    display_name    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_platform_ids (
    user_id         TEXT NOT NULL REFERENCES users(id),
    platform        TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    PRIMARY KEY (platform, platform_user_id)
);

-- Indexes for common queries
CREATE INDEX idx_conv_project ON conversations(project_id);
CREATE INDEX idx_conv_platform ON conversations(platform, platform_chat_id);
CREATE INDEX idx_msg_conversation ON messages(conversation_id, created_at);
```

#### Context Recovery When Sessions Expire

When a Claude session expires (idle >4h) or `--resume` fails, the system creates a new session with context recovered from SQLite:

```python
async def recover_context(self, conversation_id: str, new_prompt: str) -> str:
    """Build a context-recovery prompt from recent message history."""
    recent = await self.db.execute(
        """SELECT role, content, created_at FROM messages
           WHERE conversation_id = ?
           ORDER BY created_at DESC LIMIT 20""",
        (conversation_id,)
    )
    messages = reversed(recent.fetchall())  # chronological order

    context_lines = []
    for role, content, ts in messages:
        prefix = "User" if role == "user" else "Assistant"
        # Truncate long assistant responses to save context space
        if role == "assistant" and len(content) > 500:
            content = content[:500] + "... [truncated]"
        context_lines.append(f"[{prefix}]: {content}")

    recovery = "\n".join(context_lines)
    return (
        f"Previous conversation context (session recovered):\n"
        f"---\n{recovery}\n---\n\n"
        f"New message: {new_prompt}"
    )
```

This is a fallback, not the primary path. Most of the time, `--resume` handles context natively and the SQLite store serves as audit trail.

#### Database Location

```
~/.agent-bot/
├── bot.db              # SQLite database
├── config/             # Project configs (see Design 4)
└── bot.log             # Runtime log (replaces services/bot.log)
```

Using `~/.agent-bot/` rather than `services/` decouples the bot's state from any single project repo. The bot serves multiple projects but its own state is independent.

### Design 4: Per-Project Agent Configuration

**Question answered**: How should project isolation work? Should config live in the project repo or a central registry?

#### Decision: Central Registry with Per-Project Files

Project config must live **outside** the project repos, because:
1. The bot needs to enumerate all known projects at startup (before entering any repo)
2. Routing decisions (which chat maps to which project) are cross-cutting — they don't belong in any single repo
3. In-repo config like `CLAUDE.md` serves a different purpose: it configures Claude's behavior *inside* a session, not the bot's routing and lifecycle management

However, `CLAUDE.md` files inside project repos are still used — they're automatically loaded by Claude Code when `cwd` is set to the project root. The bot's project config supplements this, not replaces it.

#### Config Format

```yaml
# ~/.agent-bot/config/projects/isaac_research.yaml
id: isaac_research
name: "Isaac Research"
repo_path: /home/ubuntu/isaac_research
system_prompt: |
  You are running in the isaac_research repo.
  You have full access to tools (file read, grep, glob, bash, web search).
  When asked to research something, actively use your tools to search the codebase,
  read files, and gather information. Do not just answer from memory — investigate.
allowed_tools: "Read,Edit,Bash,Glob,Grep,WebSearch,Task"
timeout: 900          # seconds
session_ttl: 4        # hours before session expires
permissions: dangerously-skip  # or explicit allowlist above
```

```yaml
# ~/.agent-bot/config/routing.yaml
# Maps platform identities to projects
telegram:
  "123456789": isaac_research     # user ID → default project
  "987654321": other_project
slack:
  "#research-bot": isaac_research # channel name → project
discord:
  "channel-id": isaac_research

# Fallback when no routing rule matches
default_project: isaac_research
```

#### Platform Credentials

Each adapter needs its own credentials, stored separately from project config:

```yaml
# ~/.agent-bot/config/platforms.yaml
telegram:
  bot_token: "${TELEGRAM_BOT_TOKEN}"  # resolved from env
  allowed_user_ids: [123456789]
slack:
  bot_token: "${SLACK_BOT_TOKEN}"
  app_token: "${SLACK_APP_TOKEN}"     # for Socket Mode
discord:
  bot_token: "${DISCORD_BOT_TOKEN}"
  guild_ids: [111222333]
```

Actual secrets stay in environment variables or `.env` files (preserving the current pattern from `services/.env`). The YAML references env vars using `${VAR}` syntax, resolved at load time.

#### Hot Reload

Project configs should be hot-reloadable without restarting the bot. On SIGHUP or a `/reload` command:
1. Re-read all `config/projects/*.yaml` files
2. Update the routing table
3. Existing sessions continue with their current config; new sessions pick up changes

This avoids downtime when adding a new project or adjusting settings.

### Design 5: Framework Assessment — Build Custom vs. Adopt

**Question answered**: How do OpenClaw and similar frameworks architect agent-to-platform connections, and should we adopt one?

#### OpenClaw: Right Architecture, Wrong Fit

The Research Lead's Finding 2 provides a thorough analysis of OpenClaw. From an architecture perspective, OpenClaw validates our design choices:

| OpenClaw component | Our equivalent | Complexity comparison |
|---|---|---|
| Channel adapters (grammY, discord.js, etc.) | `PlatformAdapter` ABC + per-platform impl | Similar (~200-400 lines each) |
| Gateway control plane (Node.js WebSocket) | Python async router (in-process) | Ours is simpler — no WebSocket server |
| Session management (`agent:X:channel:type:id`) | `AgentSession` + SQLite conversations table | Ours is simpler — no Docker sandboxing |
| Memory system (SQLite + vector embeddings) | SQLite messages table + `--resume` | Ours is simpler — Claude handles its own context |
| Plugin SDK | Direct Python imports | Ours is simpler — no discovery/registration |

**Key architectural lesson from OpenClaw**: The adapter-to-gateway boundary is clean because adapters only handle platform mechanics (auth, message format, rate limits) and the gateway only handles agent logic (routing, session, context). We replicate this separation without the gateway's WebSocket infrastructure.

#### CrewAI / LangGraph / AutoGen: Wrong Problem Domain

These frameworks solve **agent-to-agent orchestration** (multiple AI agents collaborating on tasks). Our problem is **human-to-agent access** (a human sends messages via chat platforms, one Claude agent responds). The frameworks offer:

- No messaging platform adapters (we'd still build our own)
- Multi-agent coordination we don't need (we run one Claude instance per project)
- Python-based (good), but their abstractions (DAGs, crews, conversations) don't map to our use case

**Verdict**: Don't adopt any framework. Build the ~800-1200 lines of custom Python that gives us exactly what we need: adapters, routing, session management, history storage.

#### Why Custom is the Right Call at This Scale

1. **Total code estimate**: ~1000 lines for the core system (router, agent manager, SQLite store, config loader) + ~300 lines per adapter. The current bot is 253 lines. This is a manageable codebase.
2. **No dependency risk**: OpenClaw's governance is uncertain (creator joining OpenAI, foundation transition). Custom code has no external governance risk.
3. **Exact fit**: We need `claude -p --resume` subprocess management. No framework provides this — they all assume API-based LLM access, not CLI-based agent invocation.
4. **Iteration speed**: With ~1500 total lines, any developer (human or AI) can understand the full system. Framework abstractions would obscure the simple underlying logic.

### Design 6: Overall System Architecture — Recommended Approach

Synthesizing all five designs above into a concrete recommendation:

```
~/.agent-bot/
├── bot.db                          # SQLite (conversations, messages, sessions)
├── config/
│   ├── platforms.yaml              # Platform credentials (refs env vars)
│   ├── routing.yaml                # Chat → project mapping
│   └── projects/
│       ├── isaac_research.yaml     # Per-project config
│       └── other_project.yaml
├── bot.log                         # Runtime log
└── src/                            # (or installed as a package)
    ├── main.py                     # Entry point, loads config, starts adapters
    ├── router.py                   # Message routing + voice transcription
    ├── agent_manager.py            # Session lifecycle + claude subprocess
    ├── store.py                    # SQLite operations
    ├── config.py                   # YAML config loading
    ├── models.py                   # InboundMessage, OutboundMessage, AgentSession, etc.
    ├── transcription.py            # Voice pipeline (extracted from current bot)
    └── adapters/
        ├── base.py                 # PlatformAdapter ABC
        ├── telegram.py             # TelegramAdapter (~300 lines)
        ├── slack.py                # SlackAdapter (~300 lines, future)
        └── discord.py              # DiscordAdapter (~300 lines, future)
```

#### Implementation Phases

Building on the Research Lead's phased roadmap (end of Finding 5 summary):

**Phase 0 — Immediate win (1-2 days)**
Modify `run_claude()` in the existing `services/telegram_bot.py` to use `--output-format json` and `--resume`. Store session IDs in a simple dict or JSON file. This alone delivers session persistence with <50 lines changed, zero new dependencies.

**Phase 1 — Extract and restructure (1 week)**
- Extract `InboundMessage`/`OutboundMessage` models
- Create `TelegramAdapter` wrapping current handler logic
- Create `AgentManager` with `--resume` session tracking
- Add SQLite store (replace markdown logs)
- Load project config from YAML
- Deploy as new systemd service alongside the old bot for parallel testing

**Phase 2 — Second platform (1 week)**
- Implement `SlackAdapter` using `slack-bolt`
- Validate that the adapter interface works for a second platform
- Add Slack-specific formatting (mrkdwn, blocks for long responses)

**Phase 3 — Polish (ongoing)**
- Add `/new` command for session reset
- Add response streaming via `--output-format stream-json`
- Add conversation search/export from SQLite
- Consider Discord adapter if needed

#### Key Architectural Decisions Summary

| Decision | Choice | Rationale |
|---|---|---|
| Abstraction model | Adapter pattern | Simpler than message bus, sufficient for scale |
| Language | Python | Preserves existing investment, same as current bot |
| Session persistence | `claude -p --resume` | Native Claude Code feature, zero custom context management |
| History storage | SQLite | Zero infrastructure, queryable, standard library support |
| Config location | Central `~/.agent-bot/config/` | Cross-project routing needs, hot-reloadable |
| Process model | Subprocess per message, serial per chat | Proven reliable, `--resume` adds persistence |
| Framework adoption | None | Custom ~1500 lines is simpler than any framework's overhead |
| Deployment | Single systemd service | Matches current model, adapters run in-process as async tasks |

---

## Research Findings: Risk/Trade-off Analyst

*Security Concerns, Build-vs-Buy Trade-offs, and Long-Term Maintenance Burden*

This section assesses the security implications, trade-offs, and maintenance costs of the system described in the Research Lead and Architecture Designer findings above. Each risk is assigned a severity level (Critical / High / Medium / Low), concrete mitigations, and references to the architectural decisions that interact with it.

### Risk Inventory

#### CRITICAL SEVERITY

**R1: `--dangerously-skip-permissions` + Messaging Platform Exposure = RCE Primitive**

- **Current state**: The bot runs `claude -p --dangerously-skip-permissions` (`services/telegram_bot.py:143`) with input sourced directly from Telegram messages. This means any successful prompt injection becomes immediate remote code execution — no confirmation step, no human in the loop.
- **Real-world precedent**: OpenClaw suffered CVE-2026-25253 (CVSS 8.8) in January 2026 — a one-click RCE via stolen auth tokens that disabled execution confirmations. Censys tracked 21,000+ exposed instances. The attack pattern is identical: bypass the confirmation mechanism, then execute arbitrary commands.
- **Interaction with Architecture Designer's Design 2**: The proposed `AgentManager` (Design 2) replaces `--dangerously-skip-permissions` with `--allowedTools` for per-project tool scoping. This is the correct architectural response — it eliminates the blanket permission bypass and replaces it with explicit tool allowlists. **This migration is the single highest-priority security improvement.**
- **Mitigations**:
  1. Replace `--dangerously-skip-permissions` with `--allowedTools` per-project (as specified in Design 2's `run_prompt()`)
  2. Run Claude subprocesses in Anthropic's reference devcontainer with network-isolated firewall (whitelist: npm registry, GitHub, Claude API only) for projects that need elevated tool access
  3. Never expose `--dangerously-skip-permissions` to any message stream, trusted or otherwise
  4. Input validation layer in the Router (Design 1) to reject messages matching known injection patterns before they reach Claude

**R2: Indirect Prompt Injection via Shared Content**

- **Current risk**: Low (current bot is text-only, Telegram-only). **Future risk**: Critical if Slack or Discord adapters are added.
- **Attack vector**: On platforms like Slack, where file uploads, linked documents, and link previews are processed by AI, attackers can embed instructions in documents that the agent ingests. PromptArmor demonstrated this against Slack AI in August 2024 — link previews became a zero-click data exfiltration channel. The Register reported in February 2026 that this class of attack remains exploitable.
- **Interaction with Architecture Designer's Design 1**: The `InboundMessage` envelope (Design 1) does not currently include an `attachments` field for file content. When adding file support to adapters, all ingested content must be treated as untrusted and sanitized before inclusion in prompts.
- **Mitigations**:
  1. Disable link previews in all agent-generated responses
  2. Do not auto-process file attachments — require explicit user confirmation before ingesting files into agent context
  3. Strip or sanitize embedded instructions from file content before passing to Claude
  4. Monitor Claude's output for URLs containing encoded data (potential exfiltration attempts)

**R3: Bot Token and API Key Exposure**

- **Current state**: The `.env` file at `services/.env` contains live Telegram bot token and OpenAI API key in plaintext. While gitignored, the file is on disk and readable by any process running as the `ubuntu` user — including a prompt-injected Claude subprocess running with `--dangerously-skip-permissions`.
- **Multi-project amplification**: The Architecture Designer's `platforms.yaml` (Design 4) adds more tokens (Slack, Discord). More tokens = more exposure surface. The YAML config references env vars via `${VAR}` syntax, which is correct — actual secrets stay in environment variables, not config files.
- **Mitigations**:
  1. Restrict Claude's working directory to the project repo only — use `--allowedTools` to prevent reading files outside `cwd`
  2. Store tokens in environment variables only (preserving current pattern), never in config files
  3. For multi-platform deployments, consider systemd `LoadCredential=` or a lightweight secrets manager
  4. Implement secret pattern detection in Claude's output (scan for token-shaped strings before sending to messaging platforms)
  5. Rotate all tokens immediately if any exposure is suspected — Telegram tokens are revocable via @BotFather, OpenAI keys via dashboard

#### HIGH SEVERITY

**R4: Privilege Escalation via Agent Identity**

- **Attack vector**: When Claude executes actions, it operates with the permissions of the `ubuntu` user (per `services/telegram-bot.service` line 6). A messaging user authorized for Project A could instruct the agent to read/modify files in Project B's directory, since both share the same OS user.
- **Interaction with Architecture Designer's Design 4**: The per-project `allowed_tools` config and `repo_path` binding partially mitigate this — Claude's `cwd` is set per-project, and `--allowedTools` can restrict operations. However, if `Bash` is in the allowed tools list, the agent can `cd` to any directory the `ubuntu` user can access.
- **Mitigations**:
  1. Run each project's Claude subprocess as a dedicated OS user with filesystem access restricted to that project's repo (strongest isolation)
  2. If using a single user, use `--allowedTools` to exclude `Bash` for projects that don't need it, or use Anthropic's devcontainer for sandboxing
  3. Per-project session isolation in the `AgentManager` — never allow a session started for Project A to access Project B's context

**R5: Credential Leaks Through Agent Responses**

- **Mechanisms**: (1) Agent reads `.env` files when instructed, (2) environment variables echoed in debug output, (3) secrets encoded as URL parameters triggering link preview exfiltration. Snyk found ~7.1% of OpenClaw's ClawHub marketplace skills contained credential-leaking instructions (February 2026).
- **Interaction with Architecture Designer's Design 3**: The SQLite store logs all messages (`messages` table). If a credential accidentally appears in a response, it's persisted in the database indefinitely. The `messages.content` column has no scrubbing.
- **Mitigations**:
  1. Output filtering in the Router: scan Claude's responses for known secret patterns (API key formats, bot tokens) before forwarding to messaging platforms
  2. Exclude `.env`, `credentials.json`, and similar files from Claude's accessible paths via `--allowedTools` configuration or `.claude/settings.json` deny patterns
  3. Periodic audit of SQLite `messages` table for leaked credentials
  4. Never pass secrets through the LLM context window — use tool-based secret injection where possible

**R6: Session Persistence Creates New Attack Surface**

- **Trade-off**: The current stateless `claude -p` model (each message independent) has a security advantage — each invocation is clean, with no poisoned context from previous messages. The Architecture Designer's `--resume` session model (Design 2) introduces session persistence, which means a poisoned early message in a session influences all subsequent interactions.
- **Attack scenario**: User sends a benign message, then a crafted injection that modifies Claude's behavior. All subsequent messages in that session operate under the injected instructions. With stateless invocation, the injection's effect is limited to one response.
- **Mitigations**:
  1. Session TTL (4 hours, configurable per Design 4) limits the blast radius of a poisoned session
  2. `/new` command (Phase 3) allows explicit session reset if behavior seems compromised
  3. System prompt is appended fresh on every invocation via `--append-system-prompt` (not stored in session), providing a consistent safety baseline regardless of session state
  4. Consider implementing a "session health check" — if Claude's responses deviate from expected patterns, automatically expire the session

#### MEDIUM SEVERITY

**R7: Build vs. Buy — The OpenClaw Cautionary Tale**

The three paths assessed against the Architecture Designer's recommended approach:

| Approach | Effort | Security Posture | Maintenance | Fit |
|---|---|---|---|---|
| **Custom (recommended)** | ~1500 lines Python, 2-3 weeks | Full control, right-sized security | We own all maintenance | Exact fit for `claude -p --resume` model |
| **OpenClaw adoption** | Days to deploy | Alarming track record: CVE-2026-25253, ClawHub credential leaks, plaintext memory, reverse proxy auth bypasses. NCC Group: "fundamentally lacks security levers" | Governance uncertain (creator joined OpenAI Feb 2026, foundation transition) | Over-scoped: Canvas/A2UI, voice wake, scheduled actions — features we don't need |
| **LangGraph/CrewAI/AutoGen** | Weeks to integrate + build messaging layer | Framework-dependent; penetration testing (arxiv:2512.14860) found 65-97% attack success rates across all three | Mature projects but none solve our messaging problem | Wrong problem domain: agent-to-agent orchestration, not human-to-agent access |

**Verdict**: The Architecture Designer's recommendation to build custom is the risk-optimal path. OpenClaw is a liability. Framework adoption adds complexity without solving the messaging adapter problem. The ~1500-line custom Python codebase is auditable, understandable, and maintainable.

**R8: Platform API Maintenance Burden**

Each messaging platform imposes ongoing maintenance costs:

| Platform | Stability | Breaking Changes | Annual Maintenance Estimate |
|---|---|---|---|
| **Telegram** | High — API changes are additive, no forced migrations | `python-telegram-bot` v20+ was a major rewrite, but the underlying Bot API is stable | 4-8 hours/year |
| **Slack** | Low — aggressive deprecation posture | 32 tagged breaking changes. Legacy bots deprecated Sep 2024, stopped working March 2025. `files.upload` deprecated. Classic apps end Nov 2026 | 16-24 hours/year |
| **Discord** | Moderate — versioned API with 1+ year deprecation windows | Orderly but periodic: voice encryption modes, permissions splits, guild restrictions | 8-12 hours/year |

**Total for all three platforms**: ~30-40 hours/year of SDK and API maintenance.

**Recommendation**: The Architecture Designer's phased rollout (Design 6) is correct — start with Telegram (lowest maintenance), add Slack second (highest value for team use despite highest maintenance), defer Discord (moderate value, 2000-char limit is painful for agent responses).

**R9: Migration Risk from Working System**

- **Current state**: The Telegram bot works today. It runs as a systemd service, auto-restarts on failure, and has been in use since February 16, 2026 (earliest log file: `services/logs/2026-02-16_02-43-46.md`).
- **"Second system effect"**: Over-engineering the replacement kills momentum. The current system serves 1-2 users effectively. A multi-platform, multi-project, multi-agent system may never be needed.
- **Architecture Designer's mitigation**: Design 6 Phase 0 is the right approach — modify the existing bot minimally (`--output-format json` + `--resume`) before building the full system. This delivers immediate value and validates the core mechanism.
- **Mitigations**:
  1. Run old and new systems in parallel during Phase 1 transition (as specified in Design 6)
  2. Feature-flag new capabilities — new bot should be at least as reliable as the old one before switching
  3. Set a concrete "old system shutdown" date only after the new system has been stable for 2+ weeks
  4. Preserve all existing functionality: auth model, voice transcription, timeout handling, response chunking

#### LOW SEVERITY

**R10: Over-Engineering for 1-2 Users**

- **Reality check**: The proposal describes a system serving potentially 1-2 users. The engineering effort for multi-platform, multi-project, multi-agent support may exceed the value delivered.
- **Framework landscape**: LangGraph, CrewAI, and AutoGen all lack first-class messaging platform support — adopting any of them still requires building custom adapters. Security research (arxiv:2512.14860) found 65-97% attack success rates across all frameworks. Security is hard regardless of framework choice.
- **The Architecture Designer's ~1500-line estimate** (Design 6) is reasonable and proportionate. The risk is not in the initial build but in scope creep — adding features like vector-based memory search, scheduled actions, webhook triggers, or multi-model support that aren't needed.
- **Mitigation**: Define concrete use cases that the current bot cannot serve *before* building each new capability. The cheapest high-value change is Phase 0 (add `--resume` to existing bot). Everything after that should be justified by actual need.

### Alternative Approaches Assessment

| Approach | Pros | Cons | Risk Level | Recommended? |
|---|---|---|---|---|
| **Keep current bot + `--resume`** | Working, simple, minimal attack surface, immediate win | Still single-project, no history beyond sessions | Low | **Yes — Phase 0** |
| **Custom multi-platform adapter layer** | Full security control, right-sized, auditable | 2-3 weeks development, ongoing maintenance | Medium | **Yes — Phases 1-2** |
| **OpenClaw adoption** | Fast multi-platform, large community, proven architecture | Severe security track record, Node.js (not Python), governance uncertain, over-scoped | High | **No** |
| **LangGraph-based orchestration** | Mature, stateful, graph-based control flow | No messaging integrations, heavyweight, wrong problem domain | Medium | **No** |
| **Hybrid: custom + framework components** | Could use framework for agent logic, custom for messaging | Integration seams, two dependency ecosystems, unclear benefit | Medium-High | **No — custom is simpler at this scale** |

### Security Recommendations Summary

Ordered by priority (highest impact, lowest effort first):

1. **Replace `--dangerously-skip-permissions` with `--allowedTools`** — eliminates the RCE primitive. Zero infrastructure cost, just a CLI flag change. This should be done even before any architectural work.
2. **Output filtering for secrets** — scan Claude responses for API key patterns before sending to messaging platforms. ~50 lines of regex matching.
3. **Restrict Claude's filesystem access** — ensure `cwd` is set per-project and Claude cannot read `.env` files or navigate to other projects.
4. **Session TTL enforcement** — the 4-hour default (Design 4) limits blast radius of session poisoning. Implement with the session management system.
5. **Per-project OS user isolation** — strongest form of project isolation, but highest implementation cost. Defer to Phase 3 unless multi-tenant use cases emerge.
6. **Devcontainer sandboxing** — for projects requiring `Bash` tool access, run Claude in Anthropic's reference devcontainer with network firewall. Defer until needed.

### Testing Requirements

The following testing should accompany each implementation phase:

**Phase 0 (add `--resume` to existing bot):**
- Verify `--resume` correctly maintains conversation context across messages
- Verify session ID is persisted and recovered after bot restart
- Verify `--resume` with an expired/invalid session ID falls back gracefully (new session)
- Verify `--output-format json` output parsing handles all response types

**Phase 1 (extract and restructure):**
- Unit tests for `InboundMessage`/`OutboundMessage` serialization
- Unit tests for `AgentManager` session lifecycle (create, resume, expire, reset)
- Unit tests for SQLite store operations (save message, retrieve history, conversation lookup)
- Integration test: Telegram message → adapter → router → agent → response → adapter → Telegram
- Verify existing voice transcription pipeline works through new adapter layer
- Verify auth allowlist enforcement in adapter (unauthorized users get no response)

**Phase 2 (Slack adapter):**
- Slack adapter unit tests (message normalization, response chunking at 40k chars, mrkdwn formatting)
- Cross-platform test: same project accessible from both Telegram and Slack simultaneously
- Verify session isolation: Telegram and Slack chats for the same project maintain separate Claude sessions
- Verify Slack-specific: OAuth token refresh, Socket Mode reconnection, rate limit handling

**Security testing (all phases):**
- Prompt injection resistance: send known injection patterns ("ignore previous instructions...", encoded payloads) and verify Claude does not execute unintended actions
- Secret leakage: instruct Claude to read `.env` — verify `--allowedTools` prevents access or output filtering catches the response
- Cross-project isolation: from Project A's chat, attempt to read Project B's files — verify failure
- Session poisoning: inject a behavioral modification early in a session, verify it doesn't persist after session expiry/reset

---

## Cross-Review Notes

### Architecture Designer Notes

**On Research Lead's Findings:**

**Agreements:**

- **Finding 3 (`--resume` as game changer) is architecturally validated.** The session persistence mechanism maps directly to the `AgentManager` design (Design 2). The Research Lead's pseudocode for session-aware `run_claude()` aligns with the `AgentManager.run_prompt()` specification. No translation gap between the research finding and the proposed implementation.
- **Finding 4 (platform comparison) directly informed the adapter interface design.** The per-platform message limits (Telegram 4096, Slack 40000, Discord 2000) and delivery models (polling vs. webhooks vs. WebSocket gateway) validated the decision to put chunking and formatting inside each adapter rather than in a shared layer. The Research Lead's adapter interface sketch (`PlatformAdapter` ABC) was adopted nearly verbatim in Design 1.
- **Finding 2 (OpenClaw assessment) is thorough and the "right architecture, wrong fit" conclusion is correct.** The hub-and-spoke topology validated our adapter pattern choice. The concerns about Node.js ecosystem mismatch and governance uncertainty are architectural risks, not just operational ones — migrating to a different language runtime would require rewriting the voice pipeline and all existing Python tooling.

**Concerns:**

- **Finding 3 understates `--resume` failure modes.** The pseudocode shows a clean happy path, but Claude Code sessions can fail silently — the session ID may become invalid after Claude Code updates, context window exhaustion, or server-side session cleanup. Design 2 addresses this with the error recovery path (fall back to new session + SQLite history injection), but the Research Lead's finding doesn't flag this as a risk. The Risk Analyst's R6 (session persistence attack surface) partially covers this, but from a security angle rather than a reliability angle.
- **Finding 5 (chat history) proposes a `token_count` column in the messages table.** This implies client-side token counting, which is unreliable for Claude models (tokenizer is not publicly available for exact counts). Design 3's refined schema dropped this column in favor of letting Claude's `--resume` handle context management natively. The Research Lead should clarify whether token counting was aspirational or load-bearing for the context window management strategy.
- **Claude Agent SDK maturity is unaddressed.** Finding 3 mentions `claude_agent_sdk` as a future migration path but doesn't assess its stability, API surface, or whether it supports the same `--resume` semantics as the CLI. If the SDK doesn't support session resume, the "migration path" claim is premature.

**Gaps Identified:**

- **No analysis of Claude Code's `--output-format stream-json` for real-time feedback.** The current bot sends "Thinking..." and blocks for up to 15 minutes. Stream-json could enable progressive response delivery (typing indicators, partial results). This is a Phase 3 feature but should have been surfaced in the research as a capability assessment.
- **Missing analysis of concurrent session limits.** If we run multiple projects with multiple chats, how many concurrent Claude sessions can a single machine sustain? Claude Code spawns Node.js processes — each has a memory footprint. The Research Lead's deployment constraints section mentions "single Ubuntu server" but doesn't estimate resource requirements for multi-session operation.

**Additional Context:**

- The Research Lead's phased roadmap (end of Finding 5 summary) aligns with Design 6's implementation phases. The only difference is granularity: the Research Lead proposes 4 phases while Design 6 proposes Phase 0 + Phases 1-3. They're compatible — Phase 0 is the Research Lead's "Immediate win" and Phase 1 maps to "Short-term."

### Risk Analyst Notes

**On Research Lead's Findings:**

- **Agreement on OpenClaw rejection (Finding 2).** The security evidence is overwhelming: CVE-2026-25253, 21,000+ exposed instances, ClawHub credential leaks, NCC Group's assessment. The Research Lead's "right architecture, wrong fit" framing is generous — from a risk perspective, OpenClaw is a liability regardless of fit. The architecture is instructive; the codebase is not adoptable.
- **Finding 3's `--allowedTools` recommendation is the single most important security improvement.** Agree strongly. However, the Research Lead presents it primarily as a feature (`--allowedTools` enables per-project tool scoping) rather than as a security fix (eliminating `--dangerously-skip-permissions` closes an RCE primitive). The framing matters for prioritization — this should be treated as a P0 security fix, not a P2 feature enhancement.
- **Finding 4's platform comparison omits security posture differences.** Telegram's Bot API is HTTPS-only with long-polling (no inbound webhook exposure required). Slack requires OAuth2 with token refresh — a richer but more complex auth surface. Discord's gateway uses WebSocket with heartbeat-based auth. Each has different attack surfaces that affect adapter implementation security requirements.

**On Architecture Designer's Findings:**

- **Design 1 (adapter pattern) correctly avoids a message bus**, but the `InboundMessage` dataclass lacks an `origin_verified` field. When adding Slack adapters, we need to verify request signatures (Slack signs every webhook request with HMAC-SHA256). The adapter should mark whether the inbound message passed platform-level signature verification, so the router can reject unverified messages.
- **Design 2 (agent lifecycle) correctly implements serial-per-chat concurrency.** This prevents a class of race conditions where two concurrent Claude processes in the same project could conflict on file writes. The risk of this race is low but the mitigation (serial execution) is free, so the design is sound.
- **Design 4 (per-project config) uses `${VAR}` syntax for secret references in YAML.** This is good — secrets stay in environment variables. However, the YAML files themselves (`platforms.yaml`, `routing.yaml`) contain sensitive routing information (user IDs, channel mappings). These files should be mode `600` (owner-only) and the `~/.agent-bot/` directory should be mode `700`. The design doesn't specify filesystem permissions.
- **Design 6's Phase 0 is the correct minimal-risk starting point.** Modifying <50 lines of the existing working bot to add `--resume` delivers the highest-value feature with the lowest blast radius. The risk of Phase 0 is near-zero: if `--resume` breaks, the system falls back to stateless behavior (current state). This is a safe, reversible change.

**Risks from Proposed Approach:**

- **SQLite single-writer constraint.** Design 3 uses SQLite for all storage. SQLite supports concurrent readers but only one writer at a time. If two adapters (Telegram + Slack) try to write messages simultaneously, one will block. For 10-50 messages/day this is irrelevant, but it's a scaling ceiling to be aware of. Mitigation: use WAL mode (`PRAGMA journal_mode=WAL`) for better concurrency — handles concurrent reads during writes.
- **systemd restart behavior with multiple adapters.** The current `telegram-bot.service` uses `Restart=always`. If the new multi-adapter bot crashes in one adapter (e.g., Slack token expires), `Restart=always` restarts the entire process, disconnecting all platforms. Consider implementing adapter-level error isolation so one adapter's failure doesn't bring down the others.

**Gaps Identified:**

- **No disaster recovery plan.** The SQLite database is a single file. If it's corrupted or lost, all conversation history, session mappings, and project config linkages are gone. A nightly backup (simple `cp bot.db bot.db.bak` cron job) would mitigate this at near-zero cost.
- **Architect's concern about concurrent session resource limits is valid and unaddressed.** Each Claude Code subprocess consumes ~100-300MB of memory (Node.js process). On a typical 4GB Ubuntu server, this limits concurrent sessions to roughly 8-12 before memory pressure causes issues. This should be documented as an operational constraint with a configurable `max_concurrent_sessions` parameter in the agent manager.

### Research Lead Notes

**Responses to Concerns:**

- **Architect's concern on `--resume` failure modes — accepted.** The Finding 3 pseudocode intentionally showed the happy path to illustrate the mechanism, but you're right that it understates silent failures. The correct behavior is: attempt `--resume`, if the subprocess returns a non-zero exit code or the JSON output contains an error field, fall back to a new session with SQLite history injection. The `AgentManager.run_prompt()` in Design 2 handles this correctly. I should have flagged the failure modes explicitly in Finding 3.
- **Architect's concern on `token_count` column — clarified.** The `token_count` field in Finding 5's schema was aspirational, not load-bearing. The intent was to enable future heuristics (e.g., "this conversation has ~50k tokens of history, summarize before injecting"). Since Claude's tokenizer isn't publicly available for exact counts, a character-based estimate (chars / 4 ≈ tokens) would be sufficient for rough sizing. Design 3 correctly dropped it — `--resume` handles context management natively, making client-side token counting unnecessary for the core flow. It could be added later as a monitoring metric.
- **Architect's concern on Claude Agent SDK maturity — acknowledged.** I should not have presented the SDK as a validated migration path without assessing its stability. As of February 2026, `claude_agent_sdk` (Python) and `@anthropic-ai/claude-agent-sdk` (TypeScript) are available but documentation is sparse on session resume semantics. The CLI `--resume` flag is the proven, documented mechanism. The SDK is a "watch this space" option, not a planned dependency. Finding 3 should have been clearer about this distinction.
- **Risk Analyst's reframing of `--allowedTools` as P0 security fix — agreed.** Finding 3 presented it as a capability unlock. The Risk Analyst is correct that eliminating `--dangerously-skip-permissions` is fundamentally a security fix that happens to also enable per-project tool scoping. The implementation priority should reflect this: do it first, in Phase 0, alongside `--resume`.

**Clarifications:**

- **On stream-json (Architect's gap).** The `--output-format stream-json` capability was discovered during research but omitted from findings because it's a UX enhancement rather than an architectural decision. That said, it has architectural implications: streaming requires the adapter's `send()` method to support incremental delivery (edit a previous message or send multiple messages). Telegram supports `editMessageText` for this; Slack supports `chat.update`. Discord supports message editing. This is feasible but adds complexity to the adapter interface — a `send_streaming()` method or similar. Correctly deferred to Phase 3.
- **On concurrent session limits (Architect + Risk Analyst).** The deployment constraints section in Finding 1 mentions "single Ubuntu server" but doesn't quantify resource requirements. The Risk Analyst's estimate of ~100-300MB per Claude subprocess and ~8-12 concurrent session ceiling on 4GB RAM is reasonable. For the 1-2 user, 2-3 project use case, this ceiling is unlikely to be hit (typical concurrent sessions: 1-3). The `max_concurrent_sessions` parameter suggested by the Risk Analyst is a good safeguard — the `AgentManager` should queue messages beyond the limit rather than spawning unbounded subprocesses.

**Final Agreements:**

- All three workers converge on the hybrid approach: custom Python adapters + `--resume` session persistence + SQLite storage. No framework adoption.
- Phase 0 (`--resume` + `--allowedTools` in existing bot) is the unanimous first step.
- The adapter pattern (not message bus) is validated by all perspectives: architecture (right abstraction), security (simpler attack surface), and research (matches OpenClaw's proven topology without its overhead).

**Remaining Open Questions:**

- **Monitoring and alerting.** None of the findings address operational monitoring beyond logging. For a system that's meant to be accessed remotely (the whole point), how do we know when the bot is down? A simple health-check endpoint or heartbeat to an external monitoring service would be valuable. This doesn't block any implementation phase but should be on the radar.
- **Multi-user authorization model.** The current allowlist (`ALLOWED_USER_IDS`) is per-bot. The proposed `routing.yaml` maps user IDs to projects. But what happens when User A, authorized for Project X, messages the bot from a platform where they're not in the routing table? The system should fail-closed (reject) rather than fail-open (route to default project). This needs explicit specification in the router logic.

---

## Implementation Plan

### Overview

The research unanimously recommends a **hybrid custom approach**: build a lightweight Python system (~1500 lines) using Claude Code's native `--resume` session persistence and `--allowedTools` permission scoping, with platform adapters for messaging integration and SQLite for conversation history. No external framework adoption — OpenClaw's architecture validated our design choices but its security track record and Node.js ecosystem make it unsuitable for adoption. LangGraph, CrewAI, and AutoGen solve the wrong problem (agent-to-agent orchestration, not human-to-agent access).

The implementation follows a phased rollout starting with a near-zero-risk modification to the existing working bot (Phase 0), progressing through architectural extraction (Phase 1), multi-platform expansion (Phase 2), and polish (Phase 3). Each phase delivers standalone value and can be paused without leaving the system in a broken state. The existing Telegram bot runs in parallel with the new system until the new system proves equally reliable.

Three cross-cutting concerns emerged from the cross-review process and are addressed throughout the plan: (1) `--dangerously-skip-permissions` must be replaced with `--allowedTools` as a P0 security fix, not deferred as a feature; (2) concurrent Claude session memory limits (~100-300MB per subprocess) require a `max_concurrent_sessions` guard; (3) the router must fail-closed on unrecognized users/platforms rather than routing to a default project.

### Out of Scope / Intentionally Not Changed

- **Isaac Sim / Isaac Lab source code** — irrelevant to this problem; this is about agent access infrastructure
- **Mobile app development** — messaging platforms provide the mobile interface
- **Multi-model support** — the system targets Claude Code exclusively; no abstraction over LLM providers
- **Vector-based semantic memory** — OpenClaw uses this but it's over-scoped for 1-2 users; SQLite full-text search is sufficient
- **Docker/container sandboxing** — deferred unless multi-tenant use cases emerge; `--allowedTools` provides adequate per-project isolation
- **Scheduled/cron-based agent actions** — not part of the human-to-agent access problem
- **Voice wake / ambient listening** — out of scope; the existing voice transcription pipeline (receive audio, transcribe, process as text) is preserved as-is

### Files to Create/Modify

**Phase 0 (modify existing):**
- `services/telegram_bot.py`: Modify `run_claude()` to use `--output-format json`, `--resume`, and `--allowedTools`. Add session ID tracking dict. Replace `--dangerously-skip-permissions`. (~50 lines changed)

**Phase 1 (new system alongside existing):**
```
~/.agent-bot/
├── bot.db                          # SQLite database (auto-created)
├── config/
│   ├── platforms.yaml              # Platform credentials (refs env vars)
│   ├── routing.yaml                # Chat → project mapping (fail-closed)
│   └── projects/
│       └── isaac_research.yaml     # Per-project config
├── bot.log                         # Runtime log
└── src/
    ├── main.py                     # Entry point: load config, start adapters, run event loop
    ├── router.py                   # Message routing + voice transcription dispatch
    ├── agent_manager.py            # Session lifecycle + claude subprocess + max_concurrent guard
    ├── store.py                    # SQLite operations (WAL mode, async via aiosqlite)
    ├── config.py                   # YAML config loading with ${VAR} env resolution
    ├── models.py                   # InboundMessage, OutboundMessage, AgentSession dataclasses
    ├── transcription.py            # Voice pipeline extracted from current bot
    └── adapters/
        ├── base.py                 # PlatformAdapter ABC
        └── telegram.py            # TelegramAdapter (~300 lines)
```
- `services/agent-bot.service`: New systemd unit file for the multi-platform bot

**Phase 2 (add Slack):**
- `~/.agent-bot/src/adapters/slack.py`: SlackAdapter (~300 lines)
- `~/.agent-bot/config/platforms.yaml`: Add Slack credentials
- `~/.agent-bot/config/routing.yaml`: Add Slack channel → project mappings

### Implementation Steps

**Phase 0 — Security Fix + Session Persistence (1-2 days)**

1. **Replace `--dangerously-skip-permissions` with `--allowedTools` in `services/telegram_bot.py`** (P0 security fix). Define the tool allowlist for the isaac_research project: `"Read,Edit,Bash,Glob,Grep,WebSearch,Task,Write"`. Test that Claude can still execute research tasks with this scoped permission set.

2. **Modify `run_claude()` to use `--output-format json` and parse the response.** Extract `session_id` from the JSON output. Store a `chat_id → session_id` mapping in a module-level dict (in-memory is fine for Phase 0 — bot restarts clear sessions, which is acceptable since `--resume` with an invalid ID falls back gracefully).

3. **Add `--resume` to `run_claude()` when a session ID exists for the chat.** If the subprocess exits with an error when resuming, fall back to a new session (no `--resume` flag) and log the failure.

4. **Test the modified bot** against the existing Telegram chat. Verify: (a) first message creates a new session, (b) follow-up messages resume the session with full context, (c) bot restart clears sessions and new messages start fresh, (d) the `--allowedTools` flag doesn't break existing research workflows.

**Phase 1 — Extract and Restructure (1 week)**

5. **Create `~/.agent-bot/` directory structure** and write initial config files (`platforms.yaml`, `routing.yaml`, `projects/isaac_research.yaml`). Secrets reference env vars via `${VAR}` syntax. Set directory permissions to `700`, file permissions to `600`.

6. **Implement `models.py`**: `InboundMessage`, `OutboundMessage`, `AgentSession`, `ProjectConfig` dataclasses. The `InboundMessage` includes `platform`, `platform_user_id`, `platform_chat_id`, `text`, `audio_bytes`, `timestamp`.

7. **Implement `store.py`**: SQLite operations using `aiosqlite`. Schema from Design 3 (projects, conversations, messages tables). Enable WAL mode (`PRAGMA journal_mode=WAL`). Methods: `save_message()`, `get_recent_messages()`, `get_or_create_conversation()`, `update_session_id()`.

8. **Implement `agent_manager.py`**: Session lifecycle with `--resume`. Serial-per-chat execution (asyncio lock per chat_id). `max_concurrent_sessions` guard (default: 8). Session TTL (default: 4 hours). Error recovery: if `--resume` fails, create new session with context recovery from SQLite (last 20 messages). The `run_prompt()` method uses `--allowedTools` from project config.

9. **Implement `transcription.py`**: Extract voice pipeline from `services/telegram_bot.py:72-120`. Same `transcribe_voice()` → `chunk_audio()` → `transcribe()` chain. No behavior changes — just extraction into a standalone module.

10. **Implement `adapters/base.py` and `adapters/telegram.py`**: The `TelegramAdapter` wraps `python-telegram-bot`, normalizes messages to `InboundMessage`, handles auth via allowlist from `platforms.yaml`, chunks responses per Telegram's 4096-char limit.

11. **Implement `router.py`**: Receives `InboundMessage` from any adapter, resolves chat → project mapping from `routing.yaml` (fail-closed: reject unrecognized users), dispatches voice transcription if `audio_bytes` present, calls `AgentManager.run_prompt()`, returns `OutboundMessage` to the originating adapter.

12. **Implement `main.py`**: Entry point that loads config, initializes SQLite, creates adapters and agent manager, starts the asyncio event loop. Handles SIGHUP for config hot-reload.

13. **Create `services/agent-bot.service`**: systemd unit file for the new bot. Run alongside `services/telegram-bot.service` during transition. Both bots can't share the same Telegram bot token, so either: (a) create a second test bot via @BotFather for parallel testing, or (b) stop the old bot before starting the new one.

14. **Validate Phase 1**: Run the new bot with the existing Telegram chat. Verify all existing functionality works: text messages, voice transcription, auth, response chunking, timeouts. Verify new functionality: session persistence across messages, conversation history in SQLite, session expiry after 4 hours.

**Phase 2 — Slack Adapter (1 week)**

15. **Implement `adapters/slack.py`**: Use `slack-bolt` with Socket Mode (no inbound webhook exposure). Normalize Slack events to `InboundMessage`. Handle Slack-specific formatting: convert Claude's markdown to Slack `mrkdwn`, chunk responses using Slack blocks for messages >4000 chars. Verify HMAC-SHA256 request signatures.

16. **Update config**: Add Slack credentials to `platforms.yaml`, add channel → project mappings to `routing.yaml`.

17. **Cross-platform validation**: Same project accessible from both Telegram and Slack simultaneously. Verify session isolation (separate Claude sessions per platform chat). Verify SQLite handles concurrent writes from both adapters (WAL mode).

**Phase 3 — Polish (ongoing)**

18. Add `/new` command (explicit session reset) to all adapters.
19. Add response streaming via `--output-format stream-json` with `editMessageText` (Telegram) / `chat.update` (Slack) for progressive delivery.
20. Add nightly SQLite backup (cron job: `cp bot.db bot.db.$(date +%Y%m%d).bak`).
21. Add basic health monitoring (periodic self-check, optional heartbeat to external service).
22. Consider Discord adapter if needed (lowest priority per platform assessment).

### Dependencies

- **Claude Code CLI** with `--resume`, `--output-format json`, and `--allowedTools` flags (available as of current version)
- **Python 3.11+** (for `asyncio` improvements and dataclass features)
- **python-telegram-bot >= 21.0** (existing dependency, preserved)
- **aiosqlite** (new dependency for async SQLite)
- **PyYAML** (new dependency for config loading)
- **slack-bolt** (Phase 2 only, for Slack adapter)
- **openai** (existing dependency for voice transcription)
- **pydub** (existing dependency for audio chunking)
- **ffmpeg** (existing system dependency for audio processing)

### Risk Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **R1: RCE via `--dangerously-skip-permissions`** | Critical | Replace with `--allowedTools` in Phase 0 (step 1). This is the first change made. |
| **R2: Indirect prompt injection** | Critical | Disable link previews in responses. Don't auto-process file attachments. Strip embedded instructions from ingested content. |
| **R3: Bot token / API key exposure** | Critical | Secrets in env vars only (never in config files). Restrict Claude's filesystem access via `--allowedTools`. Mode `600`/`700` on config files/directories. |
| **R6: Session poisoning** | High | 4-hour session TTL. `/new` command for manual reset. System prompt appended fresh on every invocation via `--append-system-prompt`. |
| **R9: Migration breaks working system** | Medium | Phase 0 modifies <50 lines of existing bot. Phase 1 runs new system in parallel. Old bot not decommissioned until new system stable for 2+ weeks. |
| **R10: Over-engineering** | Medium | Each phase delivers standalone value. No phase depends on subsequent phases. Stop after Phase 0 if it's sufficient. |
| **SQLite single-writer** | Low | WAL mode (`PRAGMA journal_mode=WAL`). At 10-50 messages/day, write contention is negligible. |
| **Concurrent session memory** | Low | `max_concurrent_sessions` parameter (default: 8). Queue excess messages. Document operational limits. |
| **Disaster recovery** | Low | Nightly SQLite backup via cron (Phase 3, step 20). |

### Testing Strategy

- **Unit tests**: `models.py` serialization, `store.py` CRUD operations, `agent_manager.py` session lifecycle (create/resume/expire/reset/error-recovery), `config.py` YAML loading with env var resolution, `transcription.py` audio chunking
- **Integration tests**: Full message flow (adapter → router → agent → store → response → adapter). Voice message flow. Auth rejection for unauthorized users. Session persistence across multiple messages. Session recovery after simulated `--resume` failure.
- **Cross-platform tests** (Phase 2): Same project accessible from Telegram and Slack. Session isolation between platforms. Concurrent write handling in SQLite.
- **Security tests**: Prompt injection patterns rejected or contained. `--allowedTools` prevents `.env` file reads. Cross-project file access denied. Session poisoning doesn't survive TTL expiry. Output filtering catches API key patterns before platform delivery.
- **Manual testing**: Voice transcription through new adapter layer. Response chunking at platform-specific limits. Config hot-reload via SIGHUP. Bot restart preserves session IDs (from SQLite). 15-minute timeout still works.

### Complexity Estimate

- **Files affected**: Phase 0: 1 file modified. Phase 1: ~12 new files created, 1 service file. Phase 2: 1 new adapter file, 2 config files updated.
- **Total new code**: ~1500 lines (core: ~1000, Telegram adapter: ~300, Slack adapter: ~300)
- **Testing effort**: ~400 lines of tests across unit, integration, and security test suites
- **Estimated time**: Phase 0: 1-2 days. Phase 1: 1 week. Phase 2: 1 week. Phase 3: ongoing as needed.

---

## Final Approvals

### Research Lead Review

**Status**: ✅ Approved

**Research Incorporated**: Yes — all five findings are reflected in the implementation plan. Finding 3 (`--resume` session persistence) drives Phase 0 and the `AgentManager` design. Finding 2 (OpenClaw assessment) informed the "no framework adoption" decision. Finding 4 (platform comparison) shaped the adapter priority order. Finding 5 (chat history) maps directly to the SQLite schema and context recovery logic. Finding 1 (preserve vs. replace analysis) guided the migration mapping table.

**Concerns Addressed**: Yes — the Architect's concerns about `--resume` failure modes are addressed in step 8 (error recovery with SQLite fallback). The `token_count` column was correctly dropped. The SDK migration path is appropriately deferred. The Risk Analyst's reframing of `--allowedTools` as a P0 security fix is reflected in step 1 being the first implementation action.

**Remaining Concerns**:
- The monitoring gap (raised in my cross-review) is addressed in Phase 3 step 21 but is underspecified. For a system designed for remote access, "how do I know the bot is down?" should have a concrete answer before Phase 1 deployment. Recommendation: at minimum, implement a `/status` command that returns uptime, active sessions, and last message timestamp.
- The multi-user authorization fail-closed behavior is mentioned in the overview but not explicit in any implementation step. Step 11 (router) should specify: "reject with a logged warning and no response" for unrecognized users, not a friendly error message that confirms the bot exists.

**Recommendation**: Proceed with implementation. Phase 0 is low-risk and high-value. The plan is actionable and well-structured.

### Architecture Designer Review

**Status**: ✅ Approved

**Research Incorporated**: Yes — all six designs are faithfully reflected. Design 1 (adapter pattern) maps to steps 6, 10, and the `adapters/` directory structure. Design 2 (agent lifecycle) maps to step 8 with the `max_concurrent_sessions` guard I flagged in cross-review. Design 3 (SQLite storage) maps to step 7 with WAL mode as the Risk Analyst recommended. Design 4 (per-project config) maps to step 5 with proper filesystem permissions. Design 5 (framework assessment) drives the "no adoption" decision. Design 6 (overall architecture) is the implementation plan's backbone.

**Concerns Addressed**: Yes — my three cross-review concerns are resolved: (1) `--resume` failure modes are handled in step 8's error recovery path; (2) `token_count` was correctly dropped from the schema; (3) Claude Agent SDK is deferred as "watch this space" rather than a planned dependency. The stream-json gap I identified is addressed in Phase 3 step 19.

**Remaining Concerns**:
- Step 13 identifies the parallel testing problem (two bots can't share a Telegram bot token) but offers two solutions without choosing one. Recommendation: create a second test bot via @BotFather. It's free, takes 30 seconds, and allows true parallel operation. This should be the default approach, not an either/or.
- The `config.py` module in step 5 needs to handle malformed YAML gracefully — a syntax error in `routing.yaml` shouldn't crash the entire bot. Fail with a clear error message at startup, not a Python traceback mid-operation.

**Recommendation**: Proceed. The architecture is clean, proportionate to the problem, and each phase is independently valuable. The ~1500-line estimate is realistic.

### Risk Analyst Review

**Status**: ✅ Approved

**Research Incorporated**: Yes — all 10 risks (R1-R10) are reflected in the Risk Mitigations table. R1 (RCE via `--dangerously-skip-permissions`) is correctly prioritized as the first implementation step. R6 (session poisoning) has the 4-hour TTL and `/new` command mitigations. R9 (migration risk) is addressed by the parallel operation strategy. The security testing section covers prompt injection, secret leakage, cross-project isolation, and session poisoning — all key attack vectors from my analysis.

**Concerns Addressed**: Yes — SQLite WAL mode is specified in step 7. The `max_concurrent_sessions` guard is in step 8. The nightly backup is in Phase 3 step 20. Adapter error isolation is partially addressed — the plan doesn't explicitly mention per-adapter error boundaries, but the asyncio task model naturally provides this (one adapter's exception doesn't propagate to others if properly caught). This should be explicit in step 12's `main.py` implementation: wrap each adapter's `start()` in a `try/except` with logging and restart logic.

**Remaining Concerns**:
- **Output filtering for secrets** (my recommendation #2, ~50 lines of regex) is mentioned in the security testing section but not as an implementation step. It should be added to Phase 1 as a router-level filter: scan Claude's responses for patterns matching API keys (`sk-...`, `bot...:`), environment variable dumps, and file path patterns for `.env` files before forwarding to the messaging platform. This is cheap insurance.
- **The `--allowedTools` flag's exact behavior with `Bash` needs verification.** If `Bash` is in the allowed tools list, Claude can still `cat /home/ubuntu/.env` or `cat ~/.agent-bot/config/platforms.yaml`. The `--allowedTools` mechanism scopes *which tools* are available, not *what those tools can access*. For projects that include `Bash`, additional sandboxing (filesystem restrictions or devcontainer) is needed. This isn't blocking for Phase 0 (the current bot already uses `--dangerously-skip-permissions` which is worse), but it should be a documented known limitation.

**Recommendation**: Proceed. The plan correctly sequences risk reduction (Phase 0 eliminates the worst vulnerability first) and the phased approach ensures we always have a working fallback. The security posture improves monotonically across phases.

---

## Acceptance Criteria

### Functional Requirements

**Phase 0:**
- [ ] `services/telegram_bot.py` uses `--allowedTools` instead of `--dangerously-skip-permissions`
- [ ] `run_claude()` uses `--output-format json` and parses `session_id` from response
- [ ] Follow-up messages in the same Telegram chat resume the Claude session via `--resume`
- [ ] If `--resume` fails (invalid session ID), the system falls back to a new session without error to the user
- [ ] Existing research workflows (text messages, voice transcription, /research skill) work with scoped `--allowedTools`

**Phase 1:**
- [ ] New bot (`~/.agent-bot/`) starts and connects to Telegram using config from `platforms.yaml`
- [ ] Messages are routed to the correct project based on `routing.yaml`
- [ ] Unrecognized users receive no response (fail-closed, logged warning)
- [ ] Conversation history is stored in SQLite and queryable
- [ ] Session persistence works across messages (Claude retains context within a session)
- [ ] Sessions expire after configurable TTL (default 4 hours) and new sessions recover context from SQLite
- [ ] Voice transcription works through the new adapter layer
- [ ] Response chunking respects Telegram's 4096-char limit
- [ ] Per-project config (`allowed_tools`, `system_prompt`, `timeout`, `session_ttl`) is loaded from YAML
- [ ] Config hot-reload via SIGHUP updates routing and project settings without restart

**Phase 2:**
- [ ] Slack adapter connects via Socket Mode and receives/sends messages
- [ ] Same project is accessible from both Telegram and Slack simultaneously
- [ ] Telegram and Slack chats for the same project maintain separate Claude sessions
- [ ] Slack responses use `mrkdwn` formatting and handle the 40,000-char block limit
- [ ] SQLite handles concurrent writes from both adapters without errors (WAL mode)

### Non-Functional Requirements

- [ ] Concurrent Claude sessions limited to `max_concurrent_sessions` (default: 8); excess messages queued
- [ ] Bot process consumes <100MB RAM at idle (excluding Claude subprocesses)
- [ ] Config files are mode `600`, config directory is mode `700`
- [ ] Secrets are resolved from environment variables only — no plaintext secrets in config files
- [ ] Bot auto-restarts on crash via systemd `Restart=always`
- [ ] SQLite database has nightly backups (Phase 3)

### Testing Requirements

- [ ] Unit tests for `models.py`, `store.py`, `agent_manager.py`, `config.py`, `transcription.py`
- [ ] Integration test: end-to-end message flow from adapter to Claude response and back
- [ ] Security test: `--allowedTools` prevents reading `.env` files
- [ ] Security test: prompt injection patterns do not result in unintended command execution
- [ ] Security test: cross-project file access is denied
- [ ] Manual test: voice message transcription and response works through new adapter
- [ ] Manual test: bot restart preserves session state (session IDs in SQLite survive restart)

### Documentation Requirements

- [ ] `~/.agent-bot/config/` has example config files with comments explaining each field
- [ ] systemd service file has comments for deployment
- [ ] Known limitation documented: `--allowedTools` with `Bash` does not restrict filesystem access within Bash

## Success Metrics

- **Research coverage**: 5 research findings, 6 architecture designs, 10 risk assessments covering codebase patterns, external frameworks, platform APIs, session persistence, chat history, project isolation, security vulnerabilities, maintenance burden, and migration risk
- **Cross-review engagement**: 3 cross-reviews with 8 concerns raised, all addressed in the implementation plan or documented as known limitations
- **Implementation clarity**: 22 numbered implementation steps across 4 phases, each with concrete deliverables. Any developer (human or agent) can execute this plan without additional context.
- **Risk mitigation**: All 3 Critical risks, 3 High risks, and 4 Medium/Low risks have documented mitigations with specific implementation steps
- **Worker approval**: All 3 workers approved (✅). No blocking concerns. Minor notes integrated as implementation refinements.

## Next Steps

1. **Immediate**: Execute Phase 0 — modify `services/telegram_bot.py` to replace `--dangerously-skip-permissions` with `--allowedTools` and add `--resume` session persistence. This is a 1-2 day effort that delivers the highest-value improvement with near-zero risk.
2. **After Phase 0 validation**: Create a new project repository for the multi-platform agent bot (or a new directory in an existing project). Implement Phase 1 with the `~/.agent-bot/` structure.
3. **Decision point after Phase 1**: Evaluate whether Slack support (Phase 2) is needed based on actual usage patterns. The architecture supports it, but the cost (Slack SDK maintenance at 16-24 hours/year) should be justified by actual demand.

---

*Proposal completed on 2026-02-28*
*All workers: ✅ Research Lead Approved | ✅ Architecture Designer Approved | ✅ Risk Analyst Approved*
