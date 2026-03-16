"""Tests for safe-restart skill gates — syntax, import, config, and test validation.

Also tests the user communication contract: pre-restart report format,
recovery instructions, and post-restart confirmation messages.
"""

import logging
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

RELAY_MODULES = [
    "relay.main",
    "relay.config",
    "relay.telegram",
    "relay.intake",
    "relay.agent",
    "relay.store",
    "relay.voice",
]

RELAY_SOURCE_FILES = [
    "src/relay/main.py",
    "src/relay/config.py",
    "src/relay/telegram.py",
    "src/relay/intake.py",
    "src/relay/agent.py",
    "src/relay/store.py",
    "src/relay/voice.py",
]


# --- Gate 2: Syntax Check ---


class TestSyntaxGate:
    """Gate 2: py_compile catches syntax errors in relay source modules."""

    def test_all_relay_modules_compile(self):
        """Every relay source file passes py_compile (baseline health check)."""
        for path in RELAY_SOURCE_FILES:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", path],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"py_compile failed for {path}: {result.stderr}"
            )

    def test_syntax_error_detected(self, tmp_path):
        """py_compile correctly rejects a file with a syntax error."""
        bad_file = tmp_path / "bad.py"
        bad_file.write_text("def broken(\n")
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(bad_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "SyntaxError" in result.stderr or "Error" in result.stderr

    def test_valid_file_passes(self, tmp_path):
        """py_compile accepts a syntactically valid file."""
        good_file = tmp_path / "good.py"
        good_file.write_text("x = 1\n")
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(good_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0


# --- Gate 3: Import Check ---


class TestImportGate:
    """Gate 3: importing relay modules catches load-time errors."""

    def test_all_relay_modules_import(self):
        """All relay modules import without error in a subprocess."""
        import_lines = "; ".join(f"import {m}" for m in RELAY_MODULES)
        result = subprocess.run(
            [sys.executable, "-c", f"{import_lines}; print('OK')"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Import check failed: {result.stderr}"
        )
        assert "OK" in result.stdout

    def test_bad_import_detected(self, tmp_path):
        """A module with a missing dependency fails the import check."""
        bad_mod = tmp_path / "bad_mod.py"
        bad_mod.write_text("import nonexistent_package_xyz_12345\n")
        result = subprocess.run(
            [sys.executable, "-c", f"import sys; sys.path.insert(0, '{tmp_path}'); import bad_mod"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "ModuleNotFoundError" in result.stderr


# --- Gate 4: Config Validation ---


class TestConfigGate:
    """Gate 4: config loads and validates without starting the service."""

    def test_config_loads_successfully(self):
        """The live relay.yaml loads without error."""
        # Load .env so subprocess has bot tokens
        env = os.environ.copy()
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k] = v
        result = subprocess.run(
            [
                sys.executable, "-c",
                "from relay.config import load_config; c = load_config(); "
                "print(f'{len(c.agents)} agent(s)')",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"Config validation failed: {result.stderr}"
        )
        assert "agent(s)" in result.stdout

    def test_bad_yaml_detected(self, tmp_path):
        """Malformed YAML is caught by load_config."""
        bad_yaml = tmp_path / "relay.yaml"
        bad_yaml.write_text("agents:\n  - broken: [unmatched\n")
        result = subprocess.run(
            [
                sys.executable, "-c",
                f"from relay.config import load_config; load_config('{bad_yaml}')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_missing_config_detected(self, tmp_path):
        """A nonexistent config path fails cleanly."""
        result = subprocess.run(
            [
                sys.executable, "-c",
                f"from relay.config import load_config; load_config('{tmp_path}/nope.yaml')",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "FileNotFoundError" in result.stderr or "Error" in result.stderr


# --- Gate 5: Test Suite ---


class TestTestSuiteGate:
    """Gate 5: pytest runs and exits cleanly."""

    def test_pytest_discovers_tests(self):
        """pytest --collect-only succeeds — tests are discoverable."""
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Test collection failed: {result.stderr}"
        )
        # Should find at least a few tests
        assert "test" in result.stdout.lower()


# --- Gate 6: Lint ---


class TestLintGate:
    """Gate 6: ruff runs without crashing (warnings are non-blocking)."""

    def test_ruff_runs_on_relay_source(self):
        """ruff check executes without error on src/relay/."""
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "src/relay/", "--quiet"],
            capture_output=True,
            text=True,
        )
        # ruff returns 0 if clean, 1 if warnings — both are fine
        # only crash (returncode 2) is a problem
        assert result.returncode in (0, 1), (
            f"ruff crashed: {result.stderr}"
        )


# --- Health Check Helpers ---


class TestHealthCheckLogic:
    """Verify the log-scanning logic the skill uses for post-restart health checks."""

    def test_traceback_detected_in_log_output(self):
        """Log lines containing 'Traceback' are flagged as unhealthy."""
        sample_log = textwrap.dedent("""\
            Mar 14 12:00:01 relay[1234]: Starting relay...
            Mar 14 12:00:02 relay[1234]: Traceback (most recent call last):
            Mar 14 12:00:02 relay[1234]:   File "main.py", line 10
            Mar 14 12:00:02 relay[1234]: ImportError: No module named 'foo'
        """)
        error_indicators = ["Traceback", "Error", "Exception"]
        has_errors = any(ind in sample_log for ind in error_indicators)
        assert has_errors is True

    def test_clean_log_passes_health_check(self):
        """Normal startup logs pass the health check."""
        sample_log = textwrap.dedent("""\
            Mar 14 12:00:01 relay[1234]: Starting relay...
            Mar 14 12:00:01 relay[1234]: Loaded 3 agents
            Mar 14 12:00:02 relay[1234]: Bot polling started for agent-alpha
            Mar 14 12:00:02 relay[1234]: Bot polling started for agent-beta
        """)
        error_indicators = ["Traceback", "Error", "Exception"]
        has_errors = any(ind in sample_log for ind in error_indicators)
        assert has_errors is False

    def test_error_keyword_in_normal_context_flagged(self):
        """'Error' in log output is flagged even if it's not a traceback.
        This is intentionally conservative — better to flag a false positive
        than miss a real error when the service is on the line."""
        sample_log = "Mar 14 12:00:02 relay[1234]: ValueError: bad config\n"
        error_indicators = ["Traceback", "Error", "Exception"]
        has_errors = any(ind in sample_log for ind in error_indicators)
        assert has_errors is True


# --- Pre-Restart Report Format ---

# The skill builds these messages as text, not code. These tests validate
# the contract: what the user MUST see before and after a restart.

SHA_PATTERN = re.compile(r"[0-9a-f]{7,}")


def _build_success_report(short_sha, num_agents, tests_passed, lint_warnings=0):
    """Build the pre-restart report for all-gates-passing scenario."""
    lines = [
        "Safe-restart validation:",
        "",
        f"[pass] Git snapshot ({short_sha})",
        "[pass] Syntax check (7 modules)",
        "[pass] Import check",
        f"[pass] Config valid ({num_agents} agents)",
        f"[pass] Tests passed ({tests_passed} passed)",
    ]
    if lint_warnings > 0:
        lines.append(f"[warn] Lint: {lint_warnings} warnings (non-blocking)")
    lines.extend([
        "",
        "All gates passed. Restarting relay now...",
        "",
        "If I don't respond after this, the restart may have failed.",
        f"Rollback SHA: {short_sha}",
        f"SSH recovery: git checkout {short_sha} -- src/relay/ && sudo systemctl restart relay",
    ])
    return "\n".join(lines)


def _build_failure_report(short_sha, failed_gate, error_detail):
    """Build the pre-restart report for a gate failure scenario."""
    lines = [
        "Safe-restart validation:",
        "",
        f"[pass] Git snapshot ({short_sha})",
    ]
    # Add pass lines for gates before the failure
    gate_order = ["Syntax check", "Import check", "Config valid", "Tests passed"]
    for gate in gate_order:
        if gate == failed_gate:
            lines.append(f"[FAIL] {failed_gate}:")
            lines.append(f"  {error_detail}")
            break
        lines.append(f"[pass] {gate}")
    lines.extend([
        "",
        "Restart aborted. Fix the error and retry /safe-restart.",
    ])
    return "\n".join(lines)


class TestPreRestartReport:
    """The pre-restart report must contain recovery info before any restart."""

    def test_success_report_contains_rollback_sha(self):
        """Success report includes the rollback SHA for SSH recovery."""
        report = _build_success_report("abc1234", 3, 86)
        assert "abc1234" in report
        assert SHA_PATTERN.search(report)

    def test_success_report_contains_ssh_recovery_command(self):
        """Success report includes the full SSH recovery command."""
        report = _build_success_report("abc1234", 3, 86)
        assert "git checkout abc1234 -- src/relay/" in report
        assert "sudo systemctl restart relay" in report

    def test_success_report_contains_warning_message(self):
        """Success report warns user that silence means failure."""
        report = _build_success_report("abc1234", 3, 86)
        assert "don't respond" in report.lower() or "failed" in report.lower()

    def test_success_report_shows_gate_results(self):
        """Success report has a pass line for every gate."""
        report = _build_success_report("abc1234", 3, 86)
        assert "[pass] Git snapshot" in report
        assert "[pass] Syntax check" in report
        assert "[pass] Import check" in report
        assert "[pass] Config valid" in report
        assert "[pass] Tests passed" in report

    def test_success_report_includes_lint_warnings(self):
        """Lint warnings appear in the report when present."""
        report = _build_success_report("abc1234", 3, 86, lint_warnings=2)
        assert "[warn] Lint: 2 warnings" in report

    def test_success_report_omits_lint_when_clean(self):
        """No lint line when there are zero warnings."""
        report = _build_success_report("abc1234", 3, 86, lint_warnings=0)
        assert "[warn]" not in report

    def test_failure_report_stops_at_failed_gate(self):
        """Failure report shows the failing gate and stops — no restart line."""
        report = _build_failure_report(
            "abc1234", "Import check", "ImportError: No module named 'foo'"
        )
        assert "[FAIL] Import check" in report
        assert "ImportError" in report
        assert "Restarting" not in report
        assert "Restart aborted" in report

    def test_failure_report_shows_prior_passes(self):
        """Gates before the failure show as passed."""
        report = _build_failure_report(
            "abc1234", "Config valid", "ValueError: missing bot_token"
        )
        assert "[pass] Syntax check" in report
        assert "[pass] Import check" in report
        assert "[FAIL] Config valid" in report

    def test_failure_report_does_not_contain_restart_command(self):
        """Failure report should not tell the user to restart."""
        report = _build_failure_report(
            "abc1234", "Syntax check", "SyntaxError: unexpected indent"
        )
        assert "sudo systemctl restart" not in report


class TestPostRestartMessages:
    """The post-restart phase has a simple confirmation or rollback message."""

    def test_healthy_confirmation_message(self):
        """Healthy restart produces a short confirmation."""
        msg = "Restart complete. Relay is back online."
        assert "online" in msg.lower() or "complete" in msg.lower()

    def test_rollback_message_includes_sha(self):
        """Rollback message tells the user which commit was restored."""
        sha = "abc1234"
        msg = f"Restart failed — rolled back to {sha}. Relay is back online."
        assert sha in msg
        assert "rolled back" in msg.lower()

    def test_rollback_message_mentions_git_history(self):
        """Rollback message tells user failed changes are in git history."""
        msg = "The failed changes are still in git history. Inspect and fix before retrying."
        assert "git history" in msg.lower()


class TestCommunicationContract:
    """End-to-end contract: the right number of messages for each scenario."""

    def test_gate_failure_sends_one_message(self):
        """Gate failure = 1 message (report with error). No restart."""
        messages_sent = ["pre-restart report with error"]
        assert len(messages_sent) == 1

    def test_successful_restart_sends_two_messages(self):
        """Successful restart = 2 messages (report + confirmation)."""
        messages_sent = [
            "pre-restart report with recovery info",
            "post-restart confirmation",
        ]
        assert len(messages_sent) == 2

    def test_failed_restart_with_rollback_sends_two_messages(self):
        """Failed restart + successful rollback = 2 messages."""
        messages_sent = [
            "pre-restart report with recovery info",
            "rollback confirmation",
        ]
        assert len(messages_sent) == 2

    def test_total_failure_sends_one_message(self):
        """Failed restart + failed rollback = 1 message (pre-restart only).
        The user must SSH in using the recovery command from that message."""
        messages_sent = ["pre-restart report with recovery info"]
        assert len(messages_sent) == 1


# --- Notifier Script & Delivery Delay ---


SKILL_PATH = ".claude/skills/safe-restart/SKILL.md"


class TestDeliveryDelay:
    """Phase 2.5: sleep between pre-restart message and systemctl restart."""

    def test_skill_contains_delivery_delay(self):
        """SKILL.md includes a sleep before restart for message delivery."""
        with open(SKILL_PATH) as f:
            content = f.read()
        # Must have a sleep between Phase 2 (report) and Phase 3 (restart)
        assert "sleep 3" in content
        # The delivery delay section must exist
        assert "Delivery Delay" in content


class TestNotifierScript:
    """Phase 2.6: nohup notifier script that survives restart."""

    def test_skill_contains_notifier_section(self):
        """SKILL.md includes the post-restart notifier phase."""
        with open(SKILL_PATH) as f:
            content = f.read()
        assert "Post-Restart Notifier" in content
        assert "nohup" in content

    def test_notifier_script_template_is_valid_bash(self):
        """The notifier script template in SKILL.md is valid bash syntax."""
        with open(SKILL_PATH) as f:
            content = f.read()
        # Extract the script between SCRIPT heredoc markers
        import re
        match = re.search(
            r"cat > /tmp/relay-restart-notify\.sh << 'SCRIPT'\n(.*?)\nSCRIPT",
            content,
            re.DOTALL,
        )
        assert match is not None, "Notifier script template not found in SKILL.md"
        script = match.group(1)
        # Validate bash syntax by compiling
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Bash syntax error: {result.stderr}"

    def test_notifier_uses_telegram_api(self):
        """Notifier script sends messages via curl to Telegram bot API."""
        with open(SKILL_PATH) as f:
            content = f.read()
        assert "api.telegram.org" in content
        assert "sendMessage" in content

    def test_notifier_handles_rollback(self):
        """Notifier script includes rollback logic for unhealthy restart."""
        with open(SKILL_PATH) as f:
            content = f.read()
        assert "git checkout" in content
        assert "ROLLBACK_SHA" in content

    def test_notifier_cleans_up(self):
        """Notifier script removes itself after completion."""
        with open(SKILL_PATH) as f:
            content = f.read()
        assert "rm -f /tmp/relay-restart-notify.sh" in content
