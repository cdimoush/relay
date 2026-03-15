"""Tests for auto-blueprint script — concept promotion cron job."""

import os
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
RELAY_DIR = Path(__file__).parent.parent


class TestScriptBasics:
    """auto-blueprint.sh exists, is executable, and has valid syntax."""

    def test_script_exists_and_executable(self):
        path = SCRIPTS_DIR / "auto-blueprint.sh"
        assert path.exists(), "auto-blueprint.sh not found"
        assert os.access(path, os.X_OK), "auto-blueprint.sh not executable"

    def test_script_valid_bash_syntax(self):
        path = SCRIPTS_DIR / "auto-blueprint.sh"
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"


class TestSessionCheck:
    """Script should skip when active sessions exist."""

    def test_active_session_query(self, tmp_path):
        """Verify the active session detection query works correctly."""
        db_path = tmp_path / "relay.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                chat_id INTEGER,
                claude_session_id TEXT,
                created_at TEXT,
                last_active_at TEXT,
                status TEXT,
                agent_name TEXT
            )
        """)
        # Insert a recently active session
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, 'active', 'test')",
            ("sess1", 123, None, now, now),
        )
        conn.commit()

        # Run the same query the script uses
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status='active' AND last_active_at > ?",
            (cutoff,),
        ).fetchone()[0]
        conn.close()

        assert count == 1, "Should find 1 active session"

    def test_stale_session_not_counted(self, tmp_path):
        """Sessions idle >1h should not block the auto-blueprinter."""
        db_path = tmp_path / "relay.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                chat_id INTEGER,
                claude_session_id TEXT,
                created_at TEXT,
                last_active_at TEXT,
                status TEXT,
                agent_name TEXT
            )
        """)
        # Insert a session that's been idle for 2 hours
        stale_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, 'active', 'test')",
            ("sess1", 123, None, stale_time, stale_time),
        )
        conn.commit()

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status='active' AND last_active_at > ?",
            (cutoff,),
        ).fetchone()[0]
        conn.close()

        assert count == 0, "Stale session should not block"

    def test_expired_session_not_counted(self, tmp_path):
        """Expired sessions should not block even if recently active."""
        db_path = tmp_path / "relay.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                chat_id INTEGER,
                claude_session_id TEXT,
                created_at TEXT,
                last_active_at TEXT,
                status TEXT,
                agent_name TEXT
            )
        """)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, 'expired', 'test')",
            ("sess1", 123, None, now, now),
        )
        conn.commit()

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status='active' AND last_active_at > ?",
            (cutoff,),
        ).fetchone()[0]
        conn.close()

        assert count == 0, "Expired session should not block"


class TestCronEntry:
    """install-cron.sh includes auto-blueprint at the right time."""

    def test_cron_entry_present(self):
        script = (SCRIPTS_DIR / "install-cron.sh").read_text()
        assert "auto-blueprint.sh" in script, "auto-blueprint.sh missing from install-cron.sh"

    def test_cron_entry_correct_time(self):
        """Should run at 8:00 UTC (2am CT)."""
        script = (SCRIPTS_DIR / "install-cron.sh").read_text()
        # Look for "0 8 * * *" pattern for auto-blueprint
        assert "0 8 * * *" in script, "Cron should be scheduled at 0 8 * * * (8:00 UTC / 2am CT)"

    def test_cron_entry_logs(self):
        """Output should be redirected to auto-blueprint.log."""
        script = (SCRIPTS_DIR / "install-cron.sh").read_text()
        assert "auto-blueprint.log" in script, "Should log to auto-blueprint.log"


class TestScriptStructure:
    """Verify the script has the expected structure and safety checks."""

    def test_script_checks_active_sessions(self):
        script = (SCRIPTS_DIR / "auto-blueprint.sh").read_text()
        assert "active" in script and "sessions" in script.lower(), (
            "Script should check for active sessions"
        )

    def test_script_uses_bd_list(self):
        script = (SCRIPTS_DIR / "auto-blueprint.sh").read_text()
        assert "bd list" in script, "Script should use bd list to find concepts"

    def test_script_uses_claude_p(self):
        script = (SCRIPTS_DIR / "auto-blueprint.sh").read_text()
        assert "claude -p" in script, "Script should invoke claude -p for blueprint promotion"

    def test_script_uses_sonnet(self):
        script = (SCRIPTS_DIR / "auto-blueprint.sh").read_text()
        assert "sonnet" in script, "Script should use sonnet model (cheap enough for bead work)"

    def test_script_sends_telegram_notification(self):
        script = (SCRIPTS_DIR / "auto-blueprint.sh").read_text()
        assert "sendMessage" in script, "Script should notify user via Telegram"

    def test_script_sources_env(self):
        script = (SCRIPTS_DIR / "auto-blueprint.sh").read_text()
        assert ".env" in script, "Script should source .env for bot tokens"

    def test_script_skips_when_no_concepts(self):
        script = (SCRIPTS_DIR / "auto-blueprint.sh").read_text()
        assert "no open concepts" in script.lower() or "SKIP" in script, (
            "Script should handle empty concept backlog"
        )
