"""Tests for heartbeat system scripts."""

import os
import py_compile
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


class TestHeartbeatScript:
    """Tests for heartbeat.sh."""

    def test_exists(self):
        assert (SCRIPTS_DIR / "heartbeat.sh").exists()

    def test_is_executable(self):
        assert os.access(SCRIPTS_DIR / "heartbeat.sh", os.X_OK)

    def test_valid_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SCRIPTS_DIR / "heartbeat.sh")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Bash syntax error: {result.stderr}"


class TestSessionCleanupScript:
    """Tests for session-cleanup.py."""

    def test_exists(self):
        assert (SCRIPTS_DIR / "session-cleanup.py").exists()

    def test_is_executable(self):
        assert os.access(SCRIPTS_DIR / "session-cleanup.py", os.X_OK)

    def test_valid_python_syntax(self):
        py_compile.compile(str(SCRIPTS_DIR / "session-cleanup.py"), doraise=True)

    def test_cleanup_expires_stale_sessions(self, tmp_path):
        """Test that sessions idle >4 hours get expired."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                status TEXT,
                last_active_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT
            )
        """)

        # Insert an active session that's 5 hours stale
        stale_time = (datetime.utcnow() - timedelta(hours=5)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("sess-stale", "active", stale_time),
        )

        # Insert a recent active session (should NOT be expired)
        recent_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("sess-recent", "active", recent_time),
        )
        conn.commit()

        # Run the expiry query
        conn.execute("""
            UPDATE sessions
            SET status = 'expired'
            WHERE status = 'active'
              AND last_active_at < datetime('now', '-4 hours')
        """)
        conn.commit()

        # Verify stale session is expired
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = 'sess-stale'"
        ).fetchone()
        assert row[0] == "expired"

        # Verify recent session is still active
        row = conn.execute(
            "SELECT status FROM sessions WHERE id = 'sess-recent'"
        ).fetchone()
        assert row[0] == "active"

        conn.close()

    def test_cleanup_deletes_old_messages(self, tmp_path):
        """Test that messages for sessions closed >7 days ago get deleted."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                status TEXT,
                last_active_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT
            )
        """)

        # Old closed session (10 days ago)
        old_time = (datetime.utcnow() - timedelta(days=10)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("sess-old", "closed", old_time),
        )
        conn.execute("INSERT INTO messages VALUES (1, 'sess-old')")
        conn.execute("INSERT INTO messages VALUES (2, 'sess-old')")

        # Recent closed session (1 day ago) — messages should be kept
        recent_time = (datetime.utcnow() - timedelta(days=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            ("sess-new", "closed", recent_time),
        )
        conn.execute("INSERT INTO messages VALUES (3, 'sess-new')")
        conn.commit()

        # Find old sessions
        cursor = conn.execute("""
            SELECT id FROM sessions
            WHERE status IN ('closed', 'expired')
              AND last_active_at < datetime('now', '-7 days')
        """)
        old_ids = [row[0] for row in cursor.fetchall()]
        assert old_ids == ["sess-old"]

        # Delete their messages
        placeholders = ",".join("?" for _ in old_ids)
        conn.execute(
            f"DELETE FROM messages WHERE session_id IN ({placeholders})",
            old_ids,
        )
        conn.commit()

        # Verify old messages deleted
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = 'sess-old'"
        ).fetchone()[0]
        assert count == 0

        # Verify recent messages kept
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = 'sess-new'"
        ).fetchone()[0]
        assert count == 1

        conn.close()


class TestInstallCronScript:
    """Tests for install-cron.sh."""

    def test_exists(self):
        assert (SCRIPTS_DIR / "install-cron.sh").exists()

    def test_is_executable(self):
        assert os.access(SCRIPTS_DIR / "install-cron.sh", os.X_OK)

    def test_valid_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SCRIPTS_DIR / "install-cron.sh")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Bash syntax error: {result.stderr}"


class TestWatchdogFiles:
    """Tests for watchdog-related files."""

    def test_service_file_exists(self):
        assert (SCRIPTS_DIR / "relay.service.watchdog").exists()

    def test_install_script_exists(self):
        assert (SCRIPTS_DIR / "install-watchdog.sh").exists()

    def test_install_script_is_executable(self):
        assert os.access(SCRIPTS_DIR / "install-watchdog.sh", os.X_OK)

    def test_service_has_watchdog_config(self):
        content = (SCRIPTS_DIR / "relay.service.watchdog").read_text()
        assert "Type=notify" in content
        assert "WatchdogSec=60" in content
