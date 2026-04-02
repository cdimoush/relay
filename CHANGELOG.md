# Changelog

## v0.2.0 (2026-03-29)

### Features

- **Cron scheduler** — relay-managed cron jobs with config, scheduler module, and prompt files (`cron.py`, `CronConfig`)
- **File delivery** — `[FILE:]` marker parsing, `send_document` support, photo handler for camera/compressed images, document handler for attachments
- **Safe-restart** — delivery delay + nohup post-restart notifier with validation and rollback
- **Lifecycle logging** — structured log lines with rotating file handler
- **Intake improvements** — rich system prompt, 300-char truncation, `kill_sessions` action, markdown fence stripping
- **Haiku reliability** — deterministic heartbeat thresholds, Sonnet escalation
- **Auto-blueprinter** — nightly concept-to-blueprint promotion with epic-view Telegram formatting
- **Ops commands** — `/kill-sessions`, `/sessions`, `/logs`, `/health` skills
- **Spawn skill** — bot factory for scaffolding new bots into the relay organism
- **Memories bot** — project scaffold + daily question cron
- **Trade-study skill** — structured comparison of implementation variants
- **Concept/blueprint/build workflow** — planning skills in CLAUDE.md
- **Chat UX** — immediate typing indicator, "On it..." ack for voice, chat formatting system prompt
- **Budget exhaustion UX** — detect and show helpful message when budget runs out
- **Clone crons** — think, distill, prune scripts with generic agent-cron wrapper
- **GTC Dropzone** — cloudflared + Flask file transfer blueprint

### Fixes

- Forward original message in intake instead of truncated `cleaned_message`
- Load `.env` in config gate subprocess so new bot tokens resolve
- Strip markdown fences from Haiku JSON responses
- Send text (not just typing indicator) for voice forwarding

### Tests

- 27 tests for `telegram.py`
- 8 tests for `main.py` lifecycle
- Lifecycle logging tests for agent + intake
- Classifier test bank for intake prompt + heartbeat thresholds
- Safe-restart notifier + delivery delay tests

## v0.1.0

Initial multi-agent relay release.
