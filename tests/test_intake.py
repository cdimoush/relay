"""Tests for relay.intake — mock classifier subprocess, action routing."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from relay.agent import AgentResponse
from relay.intake import INTAKE_SYSTEM_PROMPT, IntakeResult, classify, handle_message

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


def _make_classify_process(action="forward", cleaned_message="Hello", returncode=0):
    """Create a mock process that returns a classifier response."""
    inner_json = json.dumps({"action": action, "cleaned_message": cleaned_message})
    outer_json = json.dumps({"result": inner_json})
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(outer_json.encode(), b""))
    proc.returncode = returncode
    proc.pid = 12345
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# --- classify tests ---


async def test_classify_forward():
    """Classify returns 'forward' action."""
    proc = _make_classify_process(action="forward", cleaned_message="Hello there")
    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("Hello there")
    assert result.action == "forward"
    assert result.cleaned_message == "Hello there"


async def test_classify_new_session():
    """Classify returns 'new_session' action."""
    proc = _make_classify_process(action="new_session", cleaned_message="")
    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("start over")
    assert result.action == "new_session"


async def test_classify_status():
    """Classify returns 'status' action."""
    proc = _make_classify_process(action="status", cleaned_message="")
    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("what's going on?")
    assert result.action == "status"


async def test_classify_unclear():
    """Classify returns 'unclear' action."""
    proc = _make_classify_process(action="unclear", cleaned_message="")
    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("asdfjkl")
    assert result.action == "unclear"


async def test_classify_timeout_defaults_to_forward():
    """Classifier timeout defaults to 'forward'."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.pid = 12345

    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("Hello")
    assert result.action == "forward"
    assert result.cleaned_message == "Hello"


async def test_classify_error_defaults_to_forward():
    """Classifier subprocess error defaults to 'forward'."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b"some error"))
    proc.returncode = 1
    proc.pid = 12345

    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("Hello")
    assert result.action == "forward"
    assert result.cleaned_message == "Hello"


async def test_classify_malformed_json_defaults_to_forward():
    """Malformed JSON from classifier defaults to 'forward'."""
    outer_json = json.dumps({"result": "not valid json {{"})
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(outer_json.encode(), b""))
    proc.returncode = 0
    proc.pid = 12345

    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("Hello")
    assert result.action == "forward"


async def test_classify_unknown_action_defaults_to_forward():
    """Unknown action from classifier is corrected to 'forward'."""
    proc = _make_classify_process(action="banana", cleaned_message="Hello")
    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("Hello")
    assert result.action == "forward"


# --- handle_message tests ---


async def test_handle_message_forward(store, sample_agent_config):
    """handle_message with 'forward' calls agent.send_message."""
    mock_response = AgentResponse(
        text="Agent reply",
        session_id="s1",
        is_error=False,
        cost_usd=0.01,
        duration_ms=1000,
        num_turns=1,
    )

    with patch(
        "relay.intake.classify",
        return_value=IntakeResult(action="forward", cleaned_message="Hello"),
    ):
        with patch(
            "relay.intake.agent.send_message", return_value=mock_response
        ) as mock_send:
            result = await handle_message(
                "test-agent", "Hello", 100, store, sample_agent_config
            )

    assert result == "Agent reply"
    mock_send.assert_called_once_with(
        "test-agent", "Hello", 100, store, sample_agent_config
    )


async def test_handle_message_new_session(store, sample_agent_config):
    """handle_message with 'new_session' calls agent.reset_session."""
    with patch(
        "relay.intake.classify",
        return_value=IntakeResult(action="new_session", cleaned_message=""),
    ):
        with patch(
            "relay.intake.agent.reset_session", return_value="Session closed."
        ) as mock_reset:
            result = await handle_message(
                "test-agent", "start over", 100, store, sample_agent_config
            )

    assert result == "Session closed."
    mock_reset.assert_called_once_with("test-agent", 100, store)


async def test_handle_message_status(store, sample_agent_config):
    """handle_message with 'status' calls agent.get_session_info."""
    with patch(
        "relay.intake.classify",
        return_value=IntakeResult(action="status", cleaned_message=""),
    ):
        with patch(
            "relay.intake.agent.get_session_info",
            return_value="Active session: 5m old, 3 messages",
        ) as mock_info:
            result = await handle_message(
                "test-agent", "status", 100, store, sample_agent_config
            )

    assert result == "Active session: 5m old, 3 messages"
    mock_info.assert_called_once_with("test-agent", 100, store)


async def test_handle_message_unclear(store, sample_agent_config):
    """handle_message with 'unclear' returns the fallback message."""
    with patch(
        "relay.intake.classify",
        return_value=IntakeResult(action="unclear", cleaned_message=""),
    ):
        result = await handle_message(
            "test-agent", "asdfjkl", 100, store, sample_agent_config
        )

    assert "didn't quite catch that" in result.lower()


# --- system prompt coverage tests ---


class TestIntakeSystemPrompt:
    """Verify the intake system prompt covers all actions and phrasings."""

    def test_prompt_contains_all_actions(self):
        """System prompt defines all 5 action types."""
        for action in ("forward", "new_session", "status", "kill_sessions", "unclear"):
            assert f'"{action}"' in INTAKE_SYSTEM_PROMPT

    def test_prompt_has_few_shot_examples(self):
        """System prompt includes few-shot classification examples."""
        assert "User:" in INTAKE_SYSTEM_PROMPT
        assert '"action":' in INTAKE_SYSTEM_PROMPT

    def test_prompt_has_typo_guidance(self):
        """System prompt instructs on handling typos."""
        assert "typo" in INTAKE_SYSTEM_PROMPT.lower()

    def test_prompt_has_voice_guidance(self):
        """System prompt instructs on voice transcription artifacts."""
        assert "voice" in INTAKE_SYSTEM_PROMPT.lower()

    def test_prompt_biases_toward_forward(self):
        """System prompt makes forward the default/safe choice."""
        assert "forward" in INTAKE_SYSTEM_PROMPT.lower()
        assert "default" in INTAKE_SYSTEM_PROMPT.lower()


# --- truncation test ---


async def test_classify_truncates_input_to_300_chars():
    """classify() truncates message to 300 chars before sending to subprocess."""
    long_message = "x" * 500
    proc = _make_classify_process(action="forward", cleaned_message=long_message)

    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await classify(long_message)

    # The prompt arg (index 2) should contain truncated message
    call_args = mock_exec.call_args[0]
    prompt_arg = call_args[2]  # "Classify this message:\n\n{message[:300]}"
    message_in_prompt = prompt_arg.split("\n\n", 1)[1]
    assert len(message_in_prompt) == 300


# --- kill_sessions routing test ---


async def test_classify_kill_sessions():
    """Classify returns 'kill_sessions' action."""
    proc = _make_classify_process(action="kill_sessions", cleaned_message="")
    with patch("relay.intake.asyncio.create_subprocess_exec", return_value=proc):
        result = await classify("kill sessions")
    assert result.action == "kill_sessions"


async def test_handle_message_kill_sessions(store, sample_agent_config):
    """handle_message with 'kill_sessions' calls agent.kill_all_sessions."""
    with patch(
        "relay.intake.classify",
        return_value=IntakeResult(action="kill_sessions", cleaned_message=""),
    ):
        with patch(
            "relay.intake.agent.kill_all_sessions",
            return_value="Killed 2 session(s). All clear.",
        ) as mock_kill:
            result = await handle_message(
                "test-agent", "kill sessions", 100, store, sample_agent_config
            )

    assert result == "Killed 2 session(s). All clear."
    mock_kill.assert_called_once_with("test-agent", 100, store)


# --- lifecycle logging tests ---


async def test_handle_message_logs_classification(store, sample_agent_config, caplog):
    """handle_message logs the intake classification result."""
    mock_response = AgentResponse(
        text="Reply", session_id="s1", is_error=False,
        cost_usd=0.01, duration_ms=1000, num_turns=1,
    )
    with caplog.at_level(logging.INFO, logger="relay.intake"):
        with patch(
            "relay.intake.classify",
            return_value=IntakeResult(action="forward", cleaned_message="hello world"),
        ):
            with patch("relay.intake.agent.send_message", return_value=mock_response):
                await handle_message(
                    "test-agent", "hello world", 100, store, sample_agent_config
                )

    assert any("event=intake_classified" in r.message and "action=forward" in r.message for r in caplog.records)
