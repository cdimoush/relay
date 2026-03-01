"""Integration test — end-to-end message loop with mocked externals."""

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest

from relay.intake import IntakeResult, handle_message

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


async def test_full_message_loop(store, sample_agent_config):
    """End-to-end: classify -> forward -> agent -> store messages -> response.

    Mocks the classifier subprocess and the agent subprocess to verify the
    full pipeline from intake through agent and back.
    """
    chat_id = 42

    # Mock classify to return "forward"
    mock_classify = AsyncMock(
        return_value=IntakeResult(action="forward", cleaned_message="What is 2+2?")
    )

    # Mock the agent subprocess
    agent_result = {
        "result": "2+2 = 4",
        "session_id": "claude-session-xyz",
        "is_error": False,
        "total_cost_usd": 0.003,
        "duration_ms": 1500,
        "num_turns": 1,
    }
    agent_proc = AsyncMock()
    agent_proc.communicate = AsyncMock(
        return_value=(json.dumps(agent_result).encode(), b"")
    )
    agent_proc.returncode = 0
    agent_proc.pid = 99999

    with patch("relay.intake.classify", mock_classify):
        with patch(
            "relay.agent.asyncio.create_subprocess_exec", return_value=agent_proc
        ):
            response_text = await handle_message(
                "What is 2+2?", chat_id, store, sample_agent_config
            )

    # Verify response
    assert response_text == "2+2 = 4"

    # Verify session was created and updated
    session = await store.get_active_session(chat_id)
    assert session is not None
    assert session.claude_session_id == "claude-session-xyz"

    # Verify messages were logged
    messages = await store.get_messages(session.id)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "What is 2+2?"
    assert messages[1].role == "assistant"
    assert messages[1].content == "2+2 = 4"


async def test_multi_turn_conversation(store, sample_agent_config):
    """Multiple messages in the same session maintain continuity."""
    chat_id = 42

    for i, (user_msg, agent_reply) in enumerate(
        [
            ("Hello", "Hi there!"),
            ("How are you?", "I'm doing well."),
            ("Tell me a joke", "Why did the chicken cross the road?"),
        ]
    ):
        agent_result = {
            "result": agent_reply,
            "session_id": "claude-session-multi",
            "is_error": False,
            "total_cost_usd": 0.001 * (i + 1),
            "duration_ms": 1000,
            "num_turns": 1,
        }
        agent_proc = AsyncMock()
        agent_proc.communicate = AsyncMock(
            return_value=(json.dumps(agent_result).encode(), b"")
        )
        agent_proc.returncode = 0
        agent_proc.pid = 99999

        with patch(
            "relay.intake.classify",
            AsyncMock(
                return_value=IntakeResult(action="forward", cleaned_message=user_msg)
            ),
        ):
            with patch(
                "relay.agent.asyncio.create_subprocess_exec", return_value=agent_proc
            ):
                response = await handle_message(
                    user_msg, chat_id, store, sample_agent_config
                )
        assert response == agent_reply

    # Verify all messages logged in one session
    session = await store.get_active_session(chat_id)
    messages = await store.get_messages(session.id)
    assert len(messages) == 6  # 3 user + 3 assistant


async def test_session_reset_flow(store, sample_agent_config):
    """User sends message, then resets, then sends again — two separate sessions."""
    chat_id = 42

    # First message
    agent_result = {
        "result": "First response",
        "session_id": "session-1",
        "is_error": False,
        "total_cost_usd": 0.01,
        "duration_ms": 1000,
        "num_turns": 1,
    }
    agent_proc = AsyncMock()
    agent_proc.communicate = AsyncMock(
        return_value=(json.dumps(agent_result).encode(), b"")
    )
    agent_proc.returncode = 0
    agent_proc.pid = 99999

    with patch(
        "relay.intake.classify",
        AsyncMock(return_value=IntakeResult(action="forward", cleaned_message="Hello")),
    ):
        with patch(
            "relay.agent.asyncio.create_subprocess_exec", return_value=agent_proc
        ):
            resp1 = await handle_message("Hello", chat_id, store, sample_agent_config)
    assert resp1 == "First response"

    first_session = await store.get_active_session(chat_id)
    first_session_id = first_session.id

    # Reset
    with patch(
        "relay.intake.classify",
        AsyncMock(return_value=IntakeResult(action="new_session", cleaned_message="")),
    ):
        resp2 = await handle_message("start over", chat_id, store, sample_agent_config)
    assert "closed" in resp2.lower() or "fresh" in resp2.lower()

    # Verify old session is closed
    assert await store.get_active_session(chat_id) is None

    # New message creates new session
    agent_result2 = {
        "result": "Fresh start",
        "session_id": "session-2",
        "is_error": False,
        "total_cost_usd": 0.01,
        "duration_ms": 1000,
        "num_turns": 1,
    }
    agent_proc2 = AsyncMock()
    agent_proc2.communicate = AsyncMock(
        return_value=(json.dumps(agent_result2).encode(), b"")
    )
    agent_proc2.returncode = 0
    agent_proc2.pid = 99999

    with patch(
        "relay.intake.classify",
        AsyncMock(
            return_value=IntakeResult(action="forward", cleaned_message="Hi again")
        ),
    ):
        with patch(
            "relay.agent.asyncio.create_subprocess_exec", return_value=agent_proc2
        ):
            resp3 = await handle_message(
                "Hi again", chat_id, store, sample_agent_config
            )
    assert resp3 == "Fresh start"

    new_session = await store.get_active_session(chat_id)
    assert new_session.id != first_session_id


async def test_agent_error_propagates(store, sample_agent_config):
    """Agent error response is returned to the user."""
    chat_id = 42

    agent_proc = AsyncMock()
    agent_proc.communicate = AsyncMock(return_value=(b"", b"Internal error"))
    agent_proc.returncode = 1
    agent_proc.pid = 99999

    with patch(
        "relay.intake.classify",
        AsyncMock(
            return_value=IntakeResult(action="forward", cleaned_message="Do something")
        ),
    ):
        with patch(
            "relay.agent.asyncio.create_subprocess_exec", return_value=agent_proc
        ):
            response = await handle_message(
                "Do something", chat_id, store, sample_agent_config
            )

    assert "error" in response.lower()
