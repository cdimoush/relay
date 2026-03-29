Quick health check across all relay bots.

## Usage

- `/health` — full health report

## Process

Use a **haiku sub-agent** to keep costs low — this is a read-only operation.

Gather these data points:

**1. Service status:**
```bash
sudo systemctl is-active relay
```

**2. Active sessions per bot:**
```bash
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('relay.db')
rows = conn.execute('SELECT agent_name, COUNT(*) FROM sessions WHERE status=\"active\" GROUP BY agent_name').fetchall()
total = sum(r[1] for r in rows)
print(f'Active sessions: {total}')
for name, count in rows:
    print(f'  {name}: {count}')
if not rows:
    print('  (none)')
conn.close()
"
```

**3. Recent errors (last 10 min):**
```bash
sudo journalctl -u relay --since "10 min ago" --no-pager -q 2>&1 | grep -i "error\|traceback\|exception" | grep -v "httpx\|getUpdates" | tail -5
```

**4. Uptime and disk:**
```bash
uptime -p && df -h / | tail -1 | awk '{print "Disk: " $5 " used (" $4 " free)"}'
```

Format as a compact report:
```
Relay: active | uptime: 3 days
Sessions: 2 (my_agent: 1, another_agent: 1)
Errors (10min): none
Disk: 18% used (39G free)
```
