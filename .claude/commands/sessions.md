List active relay sessions.

## Usage

- `/sessions` — show all active sessions across all bots
- `/sessions gtc_wingman` — show active sessions for a specific bot

## Process

Run this Python command against relay.db:

```bash
.venv/bin/python -c "
import sqlite3, sys
conn = sqlite3.connect('relay.db')
conn.row_factory = sqlite3.Row
bot_filter = sys.argv[1] if len(sys.argv) > 1 else None
if bot_filter:
    rows = conn.execute('SELECT agent_name, id, chat_id, last_active_at FROM sessions WHERE status=\"active\" AND agent_name=? ORDER BY last_active_at DESC', (bot_filter,)).fetchall()
else:
    rows = conn.execute('SELECT agent_name, id, chat_id, last_active_at FROM sessions WHERE status=\"active\" ORDER BY agent_name, last_active_at DESC').fetchall()
if not rows:
    print('No active sessions' + (f' for {bot_filter}' if bot_filter else ''))
else:
    print(f'Active sessions: {len(rows)}')
    print(f'{\"agent\":<15} {\"session_id\":<34} {\"chat_id\":<12} {\"last_active\"}')
    print('-' * 80)
    for r in rows:
        print(f'{r[\"agent_name\"]:<15} {r[\"id\"][:32]:<34} {r[\"chat_id\"]:<12} {r[\"last_active_at\"]}')
conn.close()
" BOT_NAME_OR_EMPTY
```

If no argument provided, omit the trailing argument. If a bot name is provided, pass it as the argument.

Report the output to the user as-is.
