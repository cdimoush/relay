#!/home/ubuntu/relay/.venv/bin/python
"""session-cleanup.py — Expire stale sessions and purge old messages.

Runs hourly via cron. Uses stdlib sqlite3 (standalone, not async).
Logs stats to stdout (cron captures to syslog).
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/home/ubuntu/relay/relay.db")


def main():
    if not DB_PATH.exists():
        print(f"[{datetime.now().isoformat()}] Database not found at {DB_PATH}, skipping.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        cursor = conn.cursor()

        # 1. Expire stale sessions (active but idle for >4 hours)
        cursor.execute("""
            UPDATE sessions
            SET status = 'expired'
            WHERE status = 'active'
              AND last_active_at < datetime('now', '-4 hours')
        """)
        expired_count = cursor.rowcount

        # 2. Find sessions closed/expired more than 7 days ago
        cursor.execute("""
            SELECT id FROM sessions
            WHERE status IN ('closed', 'expired')
              AND last_active_at < datetime('now', '-7 days')
        """)
        old_session_ids = [row["id"] for row in cursor.fetchall()]

        # 3. Delete messages for those old sessions
        deleted_messages = 0
        if old_session_ids:
            placeholders = ",".join("?" for _ in old_session_ids)
            cursor.execute(
                f"DELETE FROM messages WHERE session_id IN ({placeholders})",
                old_session_ids,
            )
            deleted_messages = cursor.rowcount

        conn.commit()

        # Log stats
        timestamp = datetime.now().isoformat()
        print(f"[{timestamp}] Session cleanup complete:")
        print(f"  Sessions expired (stale >4h): {expired_count}")
        print(f"  Old sessions found (>7d): {len(old_session_ids)}")
        print(f"  Messages deleted: {deleted_messages}")

    except sqlite3.Error as e:
        print(f"[{datetime.now().isoformat()}] SQLite error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
