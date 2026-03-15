"""Tests for relay.agent — mock Claude subprocess, session resume logic, timeout."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from relay.agent import _run_claude, get_session_info, reset_session, send_message

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


def _make_claude_process(result_data=None, stderr=b"", returncode=0):
    """Create a mock subprocess returning JSON stdout."""
    if result_data is None:
        result_data = {
            "result": "Agent says hello",
            "session_id": "claude-session-abc",
            "is_error": False,
            "total_cost_usd": 0.005,
            "duration_ms": 2000,
            "num_turns": 1,
        }
    stdout = json.dumps(result_data).encode()
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.pid = 12345
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# --- _run_claude tests ---


async def test_run_claude_success(sample_agent_config):
    """Successful claude call parses JSON and returns AgentResponse."""
    proc = _make_claude_process()
    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        resp = await _run_claude("Hello", None, sample_agent_config)
    assert resp.text == "Agent says hello"
    assert resp.session_id == "claude-session-abc"
    assert resp.is_error is False
    assert resp.cost_usd == 0.005


async def test_run_claude_with_resume(sample_agent_config):
    """When claude_session_id is set, --resume is included in the command."""
    proc = _make_claude_process()
    calls = []

    async def capture_exec(*args, **kwargs):
        calls.append(args)
        return proc

    with patch("relay.agent.asyncio.create_subprocess_exec", side_effect=capture_exec):
        await _run_claude("Hello", "existing-session-id", sample_agent_config)

    cmd_args = calls[0]
    assert "--resume" in cmd_args
    assert "existing-session-id" in cmd_args


async def test_run_claude_timeout(sample_agent_config):
    """Timeout returns an error response and attempts to kill the process."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.pid = 12345
    proc.wait = AsyncMock()

    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        with patch("relay.agent.os.killpg") as mock_killpg:
            resp = await _run_claude("Hello", None, sample_agent_config)

    assert resp.is_error is True
    assert "timed out" in resp.text
    mock_killpg.assert_called_once()


async def test_run_claude_expired_session_retries(sample_agent_config):
    """'No conversation found' error triggers retry without --resume."""
    # First call fails with expired session error
    fail_proc = AsyncMock()
    fail_proc.communicate = AsyncMock(
        return_value=(b"", b"No conversation found with that ID")
    )
    fail_proc.returncode = 1
    fail_proc.pid = 12345

    # Second call succeeds
    success_proc = _make_claude_process()

    call_count = 0

    async def mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return fail_proc
        return success_proc

    with patch("relay.agent.asyncio.create_subprocess_exec", side_effect=mock_exec):
        resp = await _run_claude("Hello", "old-session-id", sample_agent_config)

    assert resp.is_error is False
    assert resp.text == "Agent says hello"
    assert call_count == 2


async def test_run_claude_nonzero_exit(sample_agent_config):
    """Non-zero exit without 'No conversation found' returns error."""
    proc = _make_claude_process(stderr=b"something broke", returncode=1)
    proc.communicate = AsyncMock(return_value=(b"", b"something broke"))
    proc.returncode = 1

    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        resp = await _run_claude("Hello", None, sample_agent_config)

    assert resp.is_error is True
    assert "something broke" in resp.text


async def test_run_claude_invalid_json(sample_agent_config):
    """Invalid JSON from stdout returns error response."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"not valid json {{{", b""))
    proc.returncode = 0
    proc.pid = 12345

    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        resp = await _run_claude("Hello", None, sample_agent_config)

    assert resp.is_error is True
    assert "failed to parse" in resp.text


# --- send_message tests ---


async def test_send_message_creates_session(store, sample_agent_config):
    """send_message creates a new session when none exists."""
    proc = _make_claude_process()
    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        resp = await send_message(
            "test-agent",
            "Hello",
            chat_id=100,
            store=store,
            agent_config=sample_agent_config,
        )

    assert resp.text == "Agent says hello"
    session = await store.get_active_session(100, agent_name="test-agent")
    assert session is not None
    assert session.claude_session_id == "claude-session-abc"


async def test_send_message_stores_claude_session_id(store, sample_agent_config):
    """First call stores the claude_session_id in the session."""
    proc = _make_claude_process()
    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        await send_message(
            "test-agent",
            "Hello",
            chat_id=100,
            store=store,
            agent_config=sample_agent_config,
        )

    session = await store.get_active_session(100, agent_name="test-agent")
    assert session.claude_session_id == "claude-session-abc"


async def test_send_message_logs_messages(store, sample_agent_config):
    """send_message logs both user and assistant messages."""
    proc = _make_claude_process()
    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        await send_message(
            "test-agent",
            "Hello",
            chat_id=100,
            store=store,
            agent_config=sample_agent_config,
        )

    session = await store.get_active_session(100, agent_name="test-agent")
    messages = await store.get_messages(session.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Hello"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Agent says hello"


async def test_send_message_reuses_existing_session(store, sample_agent_config):
    """Subsequent calls reuse the same session."""
    proc = _make_claude_process()
    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        await send_message(
            "test-agent",
            "First",
            chat_id=100,
            store=store,
            agent_config=sample_agent_config,
        )
        await send_message(
            "test-agent",
            "Second",
            chat_id=100,
            store=store,
            agent_config=sample_agent_config,
        )

    session = await store.get_active_session(100, agent_name="test-agent")
    messages = await store.get_messages(session.id)
    # 2 user + 2 assistant = 4 messages
    assert len(messages) == 4


async def test_send_message_expires_stale_session(store, sample_agent_config):
    """A session older than session_ttl is expired and a new one is created."""
    # Set a very short TTL
    sample_agent_config.session_ttl = 0

    proc = _make_claude_process()
    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
        await send_message(
            "test-agent",
            "First",
            chat_id=100,
            store=store,
            agent_config=sample_agent_config,
        )

    # The session should have been created, and on next call it should be expired
    # because TTL is 0
    proc2 = _make_claude_process(
        result_data={
            "result": "New session response",
            "session_id": "claude-session-new",
            "is_error": False,
            "total_cost_usd": 0.01,
            "duration_ms": 1000,
            "num_turns": 1,
        }
    )
    with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc2):
        resp = await send_message(
            "test-agent",
            "Second",
            chat_id=100,
            store=store,
            agent_config=sample_agent_config,
        )

    assert resp.text == "New session response"


# --- reset_session tests ---


async def test_reset_session_closes_active(store):
    """reset_session closes the active session."""
    await store.create_session(chat_id=100, agent_name="test-agent")
    msg = await reset_session(agent_name="test-agent", chat_id=100, store=store)
    assert "closed" in msg.lower() or "fresh" in msg.lower()
    assert await store.get_active_session(100, agent_name="test-agent") is None


async def test_reset_session_no_active(store):
    """reset_session with no active session returns appropriate message."""
    msg = await reset_session(agent_name="test-agent", chat_id=100, store=store)
    assert "no active session" in msg.lower()


# --- get_session_info tests ---


async def test_get_session_info_with_session(store):
    """get_session_info returns human-readable info."""
    session = await store.create_session(chat_id=100, agent_name="test-agent")
    await store.add_message(session.id, "user", "Hello")
    await store.add_message(session.id, "assistant", "Hi")

    info = await get_session_info(agent_name="test-agent", chat_id=100, store=store)
    assert "active session" in info.lower() or "Active" in info
    assert "2 messages" in info


async def test_get_session_info_no_session(store):
    """get_session_info with no active session."""
    info = await get_session_info(agent_name="test-agent", chat_id=100, store=store)
    assert "no active session" in info.lower()


# --- lifecycle logging tests ---


async def test_run_claude_logs_on_success(sample_agent_config, caplog):
    """Successful _run_claude logs agent_complete with cost/duration/turns."""
    proc = _make_claude_process()
    with caplog.at_level(logging.INFO, logger="relay.agent"):
        with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
            await _run_claude("Hello", None, sample_agent_config)

    assert any(
        "event=agent_complete" in r.message and "cost_usd=" in r.message
        for r in caplog.records
    )


async def test_run_claude_logs_on_error(sample_agent_config, caplog):
    """Failed _run_claude logs agent_error with returncode and stderr."""
    proc = _make_claude_process(stderr=b"something broke", returncode=1)
    with caplog.at_level(logging.ERROR, logger="relay.agent"):
        with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
            await _run_claude("Hello", None, sample_agent_config)

    assert any(
        "event=agent_error" in r.message and "returncode=1" in r.message
        for r in caplog.records
    )


async def test_run_claude_logs_on_timeout(sample_agent_config, caplog):
    """Timeout _run_claude logs agent_timeout."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.pid = 12345
    proc.wait = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="relay.agent"):
        with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
            with patch("relay.agent.os.killpg"):
                await _run_claude("Hello", None, sample_agent_config)

    assert any("event=agent_timeout" in r.message for r in caplog.records)


async def test_run_claude_budget_exhaustion_message(sample_agent_config, caplog):
    """Empty response + stop_reason=tool_use produces budget exhaustion message."""
    proc = _make_claude_process(result_data={
        "result": "",
        "session_id": "s1",
        "is_error": False,
        "total_cost_usd": 2.0037,
        "duration_ms": 278805,
        "num_turns": 20,
        "stop_reason": "tool_use",
    })
    with caplog.at_level(logging.WARNING, logger="relay.agent"):
        with patch("relay.agent.asyncio.create_subprocess_exec", return_value=proc):
            resp = await _run_claude("Hello", None, sample_agent_config)

    assert "budget limit" in resp.text
    assert "$2.00" in resp.text
    assert any("event=agent_budget_exhausted" in r.message for r in caplog.records)
