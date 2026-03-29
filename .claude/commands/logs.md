Tail recent relay logs, optionally filtered to a specific bot.

## Usage

- `/logs` — show last 10 minutes of relay logs
- `/logs my_agent` — show logs filtered to a specific bot

## Process

Use a **haiku sub-agent** to keep costs low — this is a read-only operation.

Run the appropriate command:

**All logs:**
```bash
sudo journalctl -u relay --since "10 min ago" --no-pager -q 2>&1 | grep -v "getUpdates\|httpx" | tail -30
```

**Filtered to a bot:**
```bash
sudo journalctl -u relay --since "10 min ago" --no-pager -q 2>&1 | grep -i "BOT_NAME" | grep -v "getUpdates\|httpx" | tail -30
```

Replace `BOT_NAME` with the argument the user provided.

Summarize the output: any errors, response costs, session activity. Keep it brief.
