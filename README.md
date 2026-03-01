# Relay

Portable remote access to Claude Code agents — multi-project, multi-platform, with persistent sessions and searchable history.

## What is this?

Relay connects messaging platforms (Telegram, Slack, Discord) to Claude Code agents. You text a bot, it routes your message to the right project, Claude responds with full session context, and the conversation is stored in SQLite.

~1,500 lines of Python. No frameworks. No containers. Just adapters, a router, and Claude Code's CLI.

## Status

In development. First implementation pass complete — Telegram adapter, Claude agent subprocess management, message routing, SQLite store, and voice transcription.
