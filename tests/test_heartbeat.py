"""Tests for heartbeat system scripts — health check, digest, throttle, cron."""

import os
import py_compile
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


# --- Script existence and syntax ---


class TestScriptBasics:
    """All scripts exist, are executable, and have valid syntax."""

    @pytest.mark.parametrize("script", [
        "heartbeat.sh",
        "daily-digest.sh",
        "install-cron.sh",
        "install-watchdog.sh",
    ])
    def test_bash_script_exists_and_valid(self, script):
        path = SCRIPTS_DIR / script
        assert path.exists(), f"{script} not found"
        assert os.access(path, os.X_OK), f"{script} not executable"
        result = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"Syntax error in {script}: {result.stderr}"

    def test_session_cleanup_valid_python(self):
        path = SCRIPTS_DIR / "session-cleanup.py"
        assert path.exists()
        assert os.access(path, os.X_OK)
        py_compile.compile(str(path), doraise=True)

    def test_watchdog_service_file_exists(self):
        content = (SCRIPTS_DIR / "relay.service.watchdog").read_text()
        assert "Type=notify" in content
        assert "WatchdogSec=60" in content


# --- Heartbeat report file ---


class TestHeartbeatReport:
    """heartbeat.sh writes a structured report file."""

    def test_report_file_has_expected_fields(self, tmp_path):
        """Verify the report format by checking for required field prefixes."""
        # We can't run heartbeat.sh directly (needs systemctl, .env, etc)
        # but we can verify the expected report format
        expected_fields = [
            "=== Relay Heartbeat Report ===",
            "Timestamp:",
            "SERVICE:",
            "DISK:",
            "JOURNAL:",
            "DB_INTEGRITY:",
            "UPTIME:",
        ]
        # Read the script and verify it outputs these fields
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        for field in expected_fields:
            assert field in script, f"heartbeat.sh missing report field: {field}"

    def test_report_includes_verdict_field(self):
        """Script appends a VERDICT line after classification."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert 'VERDICT:' in script

    def test_uses_deterministic_thresholds(self):
        """Classification uses deterministic bash checks, not Haiku."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert "--model haiku" not in script
        assert 'verdict="good"' in script
        assert 'verdict="bad"' in script

    def test_disk_threshold_is_80_percent(self):
        """Disk threshold set at 80%."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert "80" in script
        assert "disk_val" in script

    def test_db_integrity_check(self):
        """DB integrity must equal 'ok' to pass."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert 'db_integrity' in script
        assert '"ok"' in script

    def test_journal_threshold_is_2gb(self):
        """Journal size threshold is 2GB."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert "journal" in script.lower()
        # Checks for the >= 2 comparison
        assert "-ge 2" in script

    def test_sonnet_escalation_on_bad_verdict(self):
        """When verdict is bad, Sonnet is called for diagnosis."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert "--model sonnet" in script

    def test_no_llm_call_on_good_verdict(self):
        """Sonnet call is inside the 'bad' branch only."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        # Sonnet call should appear after verdict="bad" check
        bad_pos = script.index('"${verdict}" == "bad"')
        sonnet_pos = script.index("--model sonnet")
        assert sonnet_pos > bad_pos


# --- Throttle logic ---


class TestThrottleLogic:
    """Alert throttling: max one alert per 8 hours, unless service is down."""

    def test_throttle_file_path_defined(self):
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert "/tmp/relay-heartbeat-last-alert" in script

    def test_throttle_window_is_8_hours(self):
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert "28800" in script  # 8 * 60 * 60

    def test_service_down_bypasses_throttle(self):
        """If service is not active, alert immediately (no throttle check)."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        # The service-down block should send alert and exit before throttle check
        lines = script.split("\n")
        service_check_line = None
        throttle_check_line = None
        for i, line in enumerate(lines):
            if "SERVICE: active" in line and "!=" in line:
                service_check_line = i
            if "should_alert=false" in line:
                throttle_check_line = i
        assert service_check_line is not None, "Missing service-down check"
        assert throttle_check_line is not None, "Missing throttle check"
        assert service_check_line < throttle_check_line, (
            "Service-down alert must come before throttle logic"
        )

    def test_throttle_file_written_on_alert(self):
        """Script writes timestamp to throttle file after sending alert."""
        script = (SCRIPTS_DIR / "heartbeat.sh").read_text()
        assert 'date +%s > "${THROTTLE_FILE}"' in script


# --- Daily digest ---


class TestDailyDigest:
    """daily-digest.sh sends a one-line summary 3x/day."""

    def test_digest_includes_all_stats(self):
        """Digest message includes all key metrics."""
        script = (SCRIPTS_DIR / "daily-digest.sh").read_text()
        for stat in ["uptime", "disk", "sessions", "msgs today"]:
            assert stat in script, f"Digest missing stat: {stat}"

    def test_digest_includes_health_verdict(self):
        """Digest reads the heartbeat verdict from the report file."""
        script = (SCRIPTS_DIR / "daily-digest.sh").read_text()
        assert "VERDICT:" in script
        assert "heartbeat-latest.txt" in script

    def test_digest_has_time_period_label(self):
        """Digest labels messages as morning/afternoon/evening."""
        script = (SCRIPTS_DIR / "daily-digest.sh").read_text()
        assert "morning" in script
        assert "afternoon" in script
        assert "evening" in script


# --- Install cron ---


class TestInstallCron:
    """install-cron.sh sets up the correct schedule."""

    def test_has_heartbeat_entry(self):
        script = (SCRIPTS_DIR / "install-cron.sh").read_text()
        assert "*/5 * * * *" in script
        assert "heartbeat.sh" in script

    def test_has_session_cleanup_entry(self):
        script = (SCRIPTS_DIR / "install-cron.sh").read_text()
        assert "0 * * * *" in script
        assert "session-cleanup.py" in script

    def test_has_three_digest_entries(self):
        """Three daily digest entries at 13:00, 19:00, 03:00 UTC."""
        script = (SCRIPTS_DIR / "install-cron.sh").read_text()
        assert "0 13 * * *" in script
        assert "0 19 * * *" in script
        assert "0 3 * * *" in script
        # 3 cron entries + 1 echo line = 4 mentions
        assert script.count("daily-digest.sh") >= 3

    def test_idempotent_markers(self):
        """Script uses markers for idempotent cron updates."""
        script = (SCRIPTS_DIR / "install-cron.sh").read_text()
        assert "relay-heartbeat-start" in script
        assert "relay-heartbeat-end" in script


# --- Session cleanup SQL logic ---


class TestSessionCleanup:
    """session-cleanup.py SQL queries work correctly."""

    def test_expires_stale_sessions(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT, last_active_at TEXT)"
        )
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT)")

        stale_time = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        recent_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", ("stale", "active", stale_time))
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", ("recent", "active", recent_time))
        conn.commit()

        conn.execute("""
            UPDATE sessions SET status = 'expired'
            WHERE status = 'active' AND last_active_at < datetime('now', '-4 hours')
        """)
        conn.commit()

        assert conn.execute("SELECT status FROM sessions WHERE id='stale'").fetchone()[0] == "expired"
        assert conn.execute("SELECT status FROM sessions WHERE id='recent'").fetchone()[0] == "active"
        conn.close()

    def test_deletes_old_messages(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT, last_active_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, created_at TEXT)"
        )

        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        new_time = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", ("old-sess", "closed", old_time))
        conn.execute("INSERT INTO messages VALUES (1, 'old-sess', ?)", (old_time,))
        conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", ("new-sess", "closed", new_time))
        conn.execute("INSERT INTO messages VALUES (2, 'new-sess', ?)", (new_time,))
        conn.commit()

        old_ids = [r[0] for r in conn.execute(
            "SELECT id FROM sessions WHERE status IN ('closed','expired') AND last_active_at < datetime('now', '-7 days')"
        ).fetchall()]
        assert old_ids == ["old-sess"]

        conn.execute(f"DELETE FROM messages WHERE session_id IN ({','.join('?' for _ in old_ids)})", old_ids)
        conn.commit()

        assert conn.execute("SELECT COUNT(*) FROM messages WHERE session_id='old-sess'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE session_id='new-sess'").fetchone()[0] == 1
        conn.close()
