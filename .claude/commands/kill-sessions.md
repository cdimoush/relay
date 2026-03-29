Kill active relay sessions. Stops runaway bots and clears stale state.

## Usage

- `/kill-sessions` — expire ALL active sessions across all bots
- `/kill-sessions my_agent` — expire active sessions for a specific bot

## Process

1. Run the appropriate Python command against relay.db:

**All sessions (no argument):**
```bash
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('relay.db')
cur = conn.execute(\"UPDATE sessions SET status='expired' WHERE status='active'\")
conn.commit()
print(f'{cur.rowcount} session(s) expired across all bots')
conn.close()
"
```

**Specific bot (argument provided):**
```bash
.venv/bin/python -c "
import sqlite3, sys
bot = sys.argv[1]
conn = sqlite3.connect('relay.db')
cur = conn.execute(\"UPDATE sessions SET status='expired' WHERE status='active' AND agent_name=?\", (bot,))
conn.commit()
print(f'{cur.rowcount} session(s) expired for {bot}')
conn.close()
" BOT_NAME
```

Replace `BOT_NAME` with the argument the user provided.

2. Report the result: "{N} session(s) expired [for {bot_name}]"

## Notes
- This does NOT restart relay or kill running claude processes — it just marks sessions as expired in SQLite so the next message starts fresh.
- Safe to run anytime. Expired sessions are normal state.
