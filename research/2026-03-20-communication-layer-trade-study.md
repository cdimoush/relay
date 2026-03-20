# Communication Layer Trade Study — Research Report
**Date:** 2026-03-20
**Beads:** relay-7vp (parent), relay-46y (Discord research), relay-kde (OpenClaw research), relay-cl6 (Discord sprint)

---

## Executive Summary

After deep research across 14 questions with web searches, community analysis, and codebase review, the recommendation is clear:

**Stay on Telegram + Add Discord as a fleet management layer (relay-7vp.6).**

OpenClaw does NOT offer a meaningful capability boost over relay for the Claude Code subprocess use case. Discord adds the fleet management, inter-agent communication, and threading that Telegram lacks — for free, with ~3-5 days of work that fits cleanly into relay's existing architecture.

---

## Part 1: Discord for Agentic Workflows (relay-46y)

### Server Patterns (relay-46y.1)
The dominant pattern is **channel-per-agent with categories**. Real examples:
- Daniel Georgiev ran **16 AI agents on one VPS** with 5 Discord bots serving channels across a private server. A coordinator agent delegated to specialist agents via @mentions. Lessons: session conflicts when agents share CLI backends, identity bleed when agents read each other's history.
- The **openclaw-multi-agent-kit** (production-tested with 10 agents) uses Telegram supergroups + Discord for coordination.
- Anthropic officially launched **Claude Code Channels** for Discord on 2026-03-20, validating the platform.

### Inter-Agent Communication (relay-46y.2)
- Bots CAN read each other's messages in shared channels (need MESSAGE_CONTENT privileged intent).
- discord.py's commands framework ignores bot messages by default — use raw `on_message` handler instead.
- Bots CANNOT DM each other.
- Rate limit: 5 messages per 5 seconds per channel — fine for agent coordination, not for high-throughput.
- Best pattern: shared channels for coordination, dedicated per-agent channels for work, database backing for anything that needs reliability.

### Community Vibes (relay-46y.3)
- **Credible in AI/open-source**: OpenAI, Anthropic, Midjourney, Stability AI all run communities on Discord.
- **Weak in enterprise/B2B**: Slack has 2,600+ native integrations vs Discord's ~50. Gaming stigma persists.
- **For personal/small-team agent orchestration**: Discord is increasingly common and well-supported.
- One startup saved ~$3.6K/year using Discord instead of Slack.
- Anthropic shipping Claude Code Channels for Discord is the strongest legitimacy signal.

### Forum Channels for Tasks (relay-46y.4)
- Forum channels (type 15) have solid API support for creating posts/threads.
- Limitation: bots CANNOT create forum channels themselves — must be created manually.
- No API access to archived thread listings.
- Auto-archive behavior (configurable: 1h, 24h, 3d, 7d) can hide inactive tasks.
- Real projects using this: GitHub-Issues-Discord-Threads-Bot, KanbanCord, Python Discord help channels.
- Good fit for task-scoped agent conversations with tag-based status tracking.

### Voice Channels (relay-46y.5)
- Multiple working implementations exist: Discord-VC-LLM, OpenClaw Discord Voice skill, AssemblyAI tutorial.
- Pipeline: audio capture → STT (Whisper/Deepgram) → LLM → TTS (ElevenLabs/Kokoro) → playback.
- Round-trip latency: 2-5 seconds depending on providers.
- OpenClaw's voice skill supports barge-in (interrupt mid-speech), streaming STT, automatic reconnect.
- **Future option for relay** — not needed for Phase 1 but technically feasible.

---

## Part 2: OpenClaw vs Relay Gap Analysis (relay-kde)

### Long-Running Tasks (relay-kde.1)
OpenClaw has auto-compaction (summarizes older messages when context fills). But in practice:
- Silent failures where context exceeds window and model returns empty replies
- Compaction timeouts causing total context loss (Issue #18194)
- Users report 9+ session resets per day losing task state (Issue #2597)

**Relay's approach is better here.** Claude Code CLI handles its own context management internally. `claude --resume` + session TTL is simpler and more reliable than adding a second compaction layer.

### Memory/RAG (relay-kde.2)
OpenClaw has persistent memory (markdown files), RAG search (sqlite-vec + FTS5), cross-session recall. The system was open-sourced as "memsearch."

**Relay already has functional equivalents:**
- CLAUDE.md per project = persistent project instructions
- Beads = structured task state persisting across sessions
- Cyborg brain = cross-project knowledge base

The genuine gap is automatic cross-session memory formation. But for coding agents, **the codebase IS the memory** — grep, git history, CLAUDE.md. RAG would be marginal benefit.

### Plugin Ecosystem (relay-kde.3)
Of 13,700+ skills, the useful ones for a coding fleet:
- **ClaWatch** (observability, cost tracking, auto-pause runaway agents) — **the single most useful thing in OpenClaw's ecosystem.** Relay has nothing equivalent.
- Fleet skill for multi-gateway orchestration
- GitHub MCP integration (structured event-driven reactions)
- OpenTelemetry observability plugin

**Verdict: ClaWatch-style observability is worth stealing, not migrating for.** ~200 lines to build equivalent cost tracking into relay.

### People Using OpenClaw Like Relay (relay-kde.4)
- **claudeclaw** (earlyaidopters/claudeclaw) — "Claude Code CLI as a personal Telegram bot." Essentially relay built on OpenClaw.
- **openclaw-multi-agent-kit** — 10 agent personalities with Telegram integration.

**Known problems:**
- Claude Code skill hangs on interactive approval prompts (Issue #28261)
- Long-running tasks block the agent's turn, preventing other messages (Issue #9457)
- OpenClaw's fix: ACP (Agent Client Protocol) for external process supervision

**Relay handles all of these natively.** Async subprocess management, --dangerously-skip-permissions, non-blocking architecture. People building relay-like systems on OpenClaw are fighting the framework.

### Multi-Model Access (relay-kde.5)
OpenClaw adds: automatic cross-provider failover, runtime model switching, cost-optimized routing.

**Relay already does tiered models** (Haiku for triage, Sonnet/Opus for work via config). The only genuinely new thing is automatic failover when Anthropic goes down. For a personal fleet, this is not critical.

### OpenClaw Bottom Line
| Capability | Worth Migrating For? |
|---|---|
| Context compaction | No — Claude Code handles internally, OpenClaw's is buggy |
| Memory/RAG | No — CLAUDE.md + beads + cyborg brain sufficient |
| Observability | Build it, don't migrate for it (~200 lines) |
| Plugin ecosystem | No — most plugins irrelevant to coding fleet |
| Claude Code integration | No — relay does this better |
| Multi-model fallback | Minor — only for uptime needs |
| Inter-agent comms | Interesting but buildable without OpenClaw |

**There is no clear capability boost that justifies migrating relay to OpenClaw.**

---

## Part 3: Discord Sprint Plan (relay-cl6)

### discord.py Architecture (relay-cl6.1)
- **Recommended: One bot, channel-based routing** (not N bots like Telegram).
- discord.py is fully async, runs natively in asyncio.
- Single gateway WebSocket per client (vs Telegram's HTTP polling per bot).
- One bot simplifies: single gateway connection, one set of permissions, channel-based routing is natural.
- Agent identity comes from CLAUDE.md, not the bot name. Use webhooks for per-agent names/avatars.

Key differences from python-telegram-bot:
| | Telegram | Discord |
|---|---|---|
| Connection | HTTP long-polling per bot | WebSocket per bot |
| Routing | By bot token | By channel ID |
| Message limit | 4096 chars | 2000 chars |
| File limit | 50 MB | 25 MB |
| Threading | Not native | Native threads + forums |

### Architecture Changes (relay-cl6.2)
**Minimal changes needed.** The platform-specific code is entirely in telegram.py.

Changes per module:
- **intake.py**: Update system prompt from "Telegram" to "messaging relay" (cosmetic)
- **agent.py**: Make CHAT_SYSTEM_PROMPT platform-aware (5-line change)
- **store.py**: Add `platform` column to sessions table (migration)
- **main.py**: Start Discord adapter alongside Telegram (3 lines)
- **New file**: `discord_adapter.py` (~200-300 lines)

Do NOT over-abstract. Don't build an adapter interface yet. Just build discord_adapter.py that calls intake.handle_message() the same way telegram.py does.

### Server Structure (relay-cl6.3)
```
SERVER: Relay Fleet
├── Fleet Operations
│   ├── #dashboard (status embeds, edited in-place)
│   ├── #errors (all agents post here)
│   └── #fleet-commands (operator commands)
├── Isaac
│   ├── #isaac-chat
│   ├── #isaac-tasks (FORUM)
│   └── #isaac-output
├── [Agent Category per agent...]
├── Inter-Agent
│   ├── #agent-to-agent
│   └── #handoffs (FORUM)
└── Voice (future)
```

Dashboard uses Discord embeds updated every 60 seconds — one embed per agent with status, cost, session info.

### Config Schema (relay-cl6.4)
Backward compatible. Discord config is additive:

```yaml
agents:
  isaac:
    bot_token: ${TELEGRAM_BOT_TOKEN_ISAAC}  # unchanged
    # ... existing config unchanged ...
    discord:
      chat_channel: 1234567890123456789
      task_forum: 1234567890123456790
      output_channel: 1234567890123456791

discord:  # new top-level section, optional
  bot_token: ${DISCORD_BOT_TOKEN}
  guild_id: 1234567890123456788
  dashboard_channel: 1234567890123456795
  errors_channel: 1234567890123456796
```

### Sprint Phases
1. **Phase 1 (half day): Plumbing** — config dataclasses, store migration, platform-aware prompts
2. **Phase 2 (half day): Discord read path** — discord_adapter.py, message routing, response chunking
3. **Phase 3 (half day): Dashboard** — status embeds, error channel, forum post creation
4. **Phase 4 (later): Inter-agent communication** — agent-to-agent routing, handoff threads

**Total: ~2 days for Phases 1-3. Phase 4 when needed.**

---

## Recommendation

**Do relay-7vp.6: Telegram + Discord Hybrid.**

Rationale:
1. Telegram stays untouched — voice workflow preserved
2. Discord adds fleet management, threading, inter-agent messaging — all free
3. ~2-3 days of work, fits cleanly into relay's architecture
4. Keeps full custom control — no framework dependency
5. OpenClaw doesn't offer enough to justify migration
6. Steal ClaWatch-style observability as a separate improvement (relay-kde.3)
7. Relay was always designed to be multi-platform — this realizes that vision

What to steal from OpenClaw (build into relay, don't migrate):
- Per-agent cost dashboards and alerting
- Auto-pause on runaway cost (relay already has max_budget, just needs alerting)

What to skip:
- Memory/RAG (coding agents don't need it)
- Plugin ecosystem (most irrelevant)
- Multi-model routing (manual config is fine)
- Context compaction (Claude Code handles this internally)
