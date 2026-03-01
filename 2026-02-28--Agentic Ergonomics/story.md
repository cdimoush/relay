# How We Got Here

## A 253-line bot and its problems

Since mid-February 2026, we've had a Telegram bot running on an Ubuntu server. You text it from your phone, it spawns `claude -p` with your message, Claude does its thing in the `isaac_research` repo, and the bot sends back the response. Voice messages too — Whisper transcription, the whole pipeline. It's 253 lines of Python, managed by systemd, auto-restarts on crash.

And honestly? It's been great. Texting a Claude session from the couch while you think through some research question is a genuinely different workflow than sitting at a laptop. You think more loosely. You ask weirder questions. Sometimes that's exactly what research needs.

But the bot has problems. Four of them, specifically, and they range from "annoying" to "we really should fix that."

**Amnesia.** Every message is a fresh subprocess. Claude has zero memory of what you just asked it. You say "what did you find about actuator models?" and Claude says "I have no idea what you're talking about." Because it doesn't. You just spawned a brand new process. All that context from your last three messages? Gone. You end up re-explaining things constantly, which defeats the purpose of having a conversational agent.

**Hardcoded to one repo.** There's a line in the code that says `REPO_ROOT = "/home/ubuntu/isaac_research"`. That's it. That's the routing system. Want to use this bot for a different project? Cool, copy the entire bot, change one string, run a second systemd service. It's the kind of thing that works until you have three projects and three bots and you're debugging which one is eating your server's memory.

**The permissions problem.** Every invocation uses `--dangerously-skip-permissions`. The flag is named that way for a reason. Every Telegram message becomes potential remote code execution on the server. We knew this was bad. We kept using it because the alternative was not having the bot at all, and the bot was useful enough to justify the risk. But "we know it's bad" is not a security policy.

**Write-only history.** Messages get appended to markdown files. That's the logging system. You cannot search them. You cannot inject them back into context. They're write-only. Three weeks from now, when you're trying to remember what Claude said about some specific Isaac Sim extension, you're going to `grep` through markdown files like it's 2003.

None of these are showstoppers individually. Together, they make the bot feel like a prototype. Because it is one.


## Three perspectives, one sprint

We decided to do this properly. Three research roles — Research Lead (prior art and codebase analysis), Architecture Designer (system structure), Risk Analyst (security and trade-offs). Each worked independently, then cross-reviewed each other's findings. The idea was to avoid groupthink: if everyone reads the same things in the same order, you converge on the same conclusions for the same reasons, and you miss whatever those reasons didn't account for.

The Research Lead went wide. Scanned the landscape for existing solutions — frameworks, tools, anything that solves "human texts bot, Claude responds." The Architecture Designer started from our constraints: Python, subprocess-based Claude access, single server, low message volume. The Risk Analyst looked at every option through the lens of what could go wrong.

What came back was more interesting than expected.


## OpenClaw: the right idea, the wrong adoption

The biggest discovery was OpenClaw. It's essentially an operating system for AI agents — a hub-and-spoke system with adapters for everything. Telegram, Slack, Discord, WhatsApp, Signal, iMessage. Session management. Vector memory. A plugin marketplace. It's the kind of project where you read the README and think "oh, someone already solved this."

Then you dig deeper.

It's Node.js. We're a Python shop. That's not fatal, but it's friction — different dependency chains, different debugging tools, different mental models. More importantly, our core integration is with Claude Code's CLI, which means subprocess management, and Python's `asyncio.create_subprocess_exec` is a lot more natural for that than Node's `child_process.spawn`.

The governance situation is shaky. OpenClaw's creator joined OpenAI in February 2026. The project isn't abandoned, but single-maintainer open source with the maintainer at a competing company is a risk profile we'd rather not depend on.

And then there's CVE-2026-25253.

A one-click remote code execution vulnerability. Censys tracked 21,000+ exposed instances. NCC Group's analysis was blunt: OpenClaw "fundamentally lacks security levers." The architecture assumes trusted inputs at layers that face the internet. On top of that, an audit of the skill marketplace found 7.1% of published skills contained credential-leaking instructions. Not bugs — instructions that told the agent to exfiltrate credentials. In a marketplace that users were encouraged to install from.

So OpenClaw became two things for us: an architecture reference and a cautionary tale. The adapter pattern, the session management model, the hub-and-spoke topology — all good ideas, validated at scale. The codebase itself? Not something we want to depend on.


## The flag that changed everything

Here's the moment the whole project shifted.

Claude Code's CLI now supports `--resume <session_id>`. You run a command, get back a session ID in the JSON output. Next message, you pass that session ID back. Claude picks up exactly where it left off — full context, full memory of the conversation.

That's it. That's the fix for our biggest problem.

Not a framework. Not a vector database. Not a RAG pipeline. A command-line flag. The amnesia problem — the one that made the bot feel like talking to someone with anterograde memory loss — is solvable by passing one extra argument to the subprocess.

And there's more. `--allowedTools` lets you scope permissions per invocation. Instead of `--dangerously-skip-permissions` (which is basically "Claude can do anything, including `rm -rf /`"), you specify exactly which tools Claude can use. Read files? Yes. Write files? For this project, yes. Execute arbitrary bash? Maybe not from a Telegram message sent over the internet.

The combination of `--resume` and `--allowedTools` meant that two of our four problems — amnesia and permissions — were solvable with flags we weren't using. No new infrastructure. No new dependencies. Just better invocations of the tool we already had.

This is the kind of thing that makes you feel slightly foolish and deeply relieved at the same time.


## What about the frameworks?

We looked at the usual suspects: LangGraph, CrewAI, AutoGen. They're all Python, which was appealing. They all have sophisticated orchestration capabilities.

They also all solve the wrong problem.

These frameworks are built for agent-to-agent orchestration — multiple AI agents collaborating on a task, passing messages between themselves, managing shared state. That's genuinely useful if you're building a system where a "researcher" agent hands off to a "writer" agent who consults a "fact-checker" agent.

We need human-to-agent access. One human texts a bot. One Claude responds. The complexity isn't in the agent topology — it's in the plumbing: messaging platform adapters, session routing, conversation history. None of these frameworks have Telegram adapters. None of them manage Slack bot connections. We'd still be building all the hard parts ourselves, but now with a framework's abstractions sitting between us and the subprocess calls we actually need to make.

When you find yourself writing adapter code to make a framework talk to the thing you could just talk to directly, the framework isn't helping.


## What we decided to build

About 1,500 lines of Python. Here's why that number isn't pulled from thin air.

OpenClaw's platform adapters run 200-400 lines each. That tracks with the Telegram bot we already have — it's 253 lines, and it does more than just adapt messages (it also runs Claude and handles voice). A clean adapter that only normalizes messages is probably 200-300 lines per platform.

On top of that, we need a router (match incoming messages to projects), an agent manager (track Claude sessions, handle `--resume`), a SQLite store (conversation history), and a config loader (per-project settings). Each of these is a focused module — maybe 150-250 lines apiece.

Add a main entry point and data models, and you're in the 1,200-1,500 line range. That's auditable. Any developer — human or AI — can read the entire system in an afternoon. If something breaks at 2 AM, you can understand the whole codebase before your coffee gets cold.

**SQLite for storage.** The current system writes to markdown files that you can never usefully read again. SQLite gives us queryable history in a single file. No Postgres to install. No Redis to monitor. No connection strings. The schema is three tables: projects, conversations (mapping platform chats to Claude sessions), and messages (full history with timestamps). WAL mode for concurrent access, because multiple adapters might be writing simultaneously.

**Adapters, not a message bus.** We considered Redis or RabbitMQ as a message bus between adapters and the core system. Then we remembered we get 10-50 messages a day. We don't have a scaling problem. We have an organization problem. Lightweight adapters that normalize messages to a common `InboundMessage` format — platform, user ID, chat ID, text, audio bytes — and hand them to the router. Done.

**Telegram first, Slack second, Discord later.** Telegram has the best voice message support, the most generous rate limits, the simplest bot API, and we already have a working implementation. Slack is the natural next step for team use. Discord's 2,000-character message limit makes it actively hostile to agent responses, so it goes to the back of the line.


## The plan, in phases

The clever part is Phase 0.

Before building anything new, we modify about 50 lines of the existing 253-line bot. Add `--resume` to maintain sessions across messages. Add `--output-format json` to get structured responses with session IDs. Swap `--dangerously-skip-permissions` for `--allowedTools` with a sensible scope.

That's it. One to two days of work. The bot we already have — the one running right now — gains session persistence and loses its worst security hole. No new architecture. No new dependencies. Just better flags.

This matters because it means we're not waiting for the full system to get the two most important improvements. The amnesia fix and the permissions fix ship this week. Everything else can take its time.

Phase 1 is the real build. Extract the adapter pattern from the existing bot, build the router, agent manager, SQLite store, and YAML config system. Run the new system alongside the old bot. Do not kill the old bot until the new one has been stable for at least two weeks. This is the "measure twice, cut once" phase — we already know the architecture works because OpenClaw proved it, and we already know the Claude integration works because we've been running it for two weeks. We're just putting them together cleanly.

Phase 2 adds Slack. Same adapter interface, different platform SDK. If the abstraction is right — and it should be, because we designed it with exactly this test in mind — this is a week of work. The same project becomes accessible from both Telegram and Slack simultaneously. Conversations are platform-specific, but history is unified in SQLite.

Phase 3 is polish. Streaming responses, so you're not staring at "Thinking..." for ten minutes while Claude reads through Isaac Sim's extension registry. A `/new` command for explicit session reset. Health monitoring. Maybe Discord, if we can figure out a good chunking strategy for long responses.


## Where this lands

The end state looks like this: you text a bot from your phone, or from Slack, or from wherever. It knows which project you're talking to based on which chat you're in. It remembers your conversation because `--resume` handles that. It has scoped permissions for that specific project because `--allowedTools` handles that. Your conversation history lives in SQLite, queryable and searchable.

The whole thing runs as a systemd service on the same Ubuntu server we're already using. No framework dependencies. No Docker. No Redis. No message queue. Python, SQLite, and Claude Code's CLI.

The 253-line Telegram bot was a prototype that proved the concept: remote Claude access from a phone is genuinely useful, not just a novelty. Two weeks of daily use confirmed that. Now we know exactly what it needs to become, and the distance between "prototype" and "real system" is about 1,200 lines of Python and a few well-chosen CLI flags.

Sometimes the best architecture decision is realizing that the tool you're already using just added the feature you were about to build.
