"""Tests for relay.discord_adapter — Discord bot adapter layer."""

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from relay.config import AgentConfig, DiscordAgentConfig, DiscordConfig, VoiceConfig
from relay.intake import IntakeResult
from relay.discord_adapter import (
    DISCORD_MAX_LENGTH,
    _build_channel_agent_map,
    _extract_and_send_files,
    _send_chunked,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_discord_message(
    author_id=111111,
    channel_id=9876543210,
    content="hello",
    is_bot=False,
    attachments=None,
):
    """Create a mock Discord Message."""
    msg = MagicMock()
    msg.author = MagicMock()
    msg.author.id = author_id
    msg.author.bot = is_bot
    msg.channel = MagicMock()
    msg.channel.id = channel_id
    msg.channel.send = AsyncMock()
    msg.channel.typing = MagicMock(return_value=MagicMock(
        __aenter__=AsyncMock(),
        __aexit__=AsyncMock(),
    ))
    msg.content = content
    msg.attachments = attachments or []
    msg.flags = MagicMock()
    msg.flags.value = 0
    return msg


def _make_agent_config_with_discord(tmp_path, channel_id=9876543210):
    """Create an AgentConfig with Discord channel configured."""
    return AgentConfig(
        name="test-agent",
        bot_token="123456:ABC-DEF",
        allowed_users=[111111, 222222],
        project_dir=str(tmp_path),
        allowed_tools=["Read", "Write", "Bash"],
        discord=DiscordAgentConfig(chat_channel=channel_id),
    )


# ---------------------------------------------------------------------------
# _send_chunked tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_chunked_short_message():
    """Text under 2000 chars sends one message."""
    channel = MagicMock()
    channel.send = AsyncMock()
    await _send_chunked(channel, "short text")
    channel.send.assert_called_once_with("short text")


@pytest.mark.asyncio
async def test_send_chunked_empty_string():
    """Empty text sends '(empty response)'."""
    channel = MagicMock()
    channel.send = AsyncMock()
    await _send_chunked(channel, "")
    channel.send.assert_called_once_with("(empty response)")


@pytest.mark.asyncio
async def test_send_chunked_none_text():
    """None text sends '(empty response)'."""
    channel = MagicMock()
    channel.send = AsyncMock()
    await _send_chunked(channel, None)
    channel.send.assert_called_once_with("(empty response)")


@pytest.mark.asyncio
async def test_send_chunked_exact_boundary():
    """Exactly 2000 chars sends one message."""
    channel = MagicMock()
    channel.send = AsyncMock()
    text = "A" * DISCORD_MAX_LENGTH
    await _send_chunked(channel, text)
    channel.send.assert_called_once_with(text)


@pytest.mark.asyncio
async def test_send_chunked_splits_long():
    """2500 chars sends two messages (2000 + 500)."""
    channel = MagicMock()
    channel.send = AsyncMock()
    text = "B" * 2500
    await _send_chunked(channel, text)
    assert channel.send.call_count == 2
    calls = channel.send.call_args_list
    assert len(calls[0][0][0]) == DISCORD_MAX_LENGTH
    assert len(calls[1][0][0]) == 500


@pytest.mark.asyncio
async def test_send_chunked_multiple_chunks():
    """5000 chars sends three messages."""
    channel = MagicMock()
    channel.send = AsyncMock()
    text = "C" * 5000
    await _send_chunked(channel, text)
    assert channel.send.call_count == 3


# ---------------------------------------------------------------------------
# _build_channel_agent_map tests
# ---------------------------------------------------------------------------


def test_build_channel_agent_map(tmp_path):
    """Maps channel IDs to agent names from config."""
    from relay.config import RelayConfig, StorageConfig

    config = RelayConfig(
        agents={
            "alpha": _make_agent_config_with_discord(tmp_path, channel_id=111),
            "beta": _make_agent_config_with_discord(tmp_path, channel_id=222),
        },
        voice=VoiceConfig(),
        storage=StorageConfig(),
        discord=DiscordConfig(bot_token="tok", guild_id=1),
    )
    mapping = _build_channel_agent_map(config)
    assert mapping == {111: "alpha", 222: "beta"}


def test_build_channel_agent_map_skips_no_discord(tmp_path):
    """Agents without discord config are skipped."""
    from relay.config import RelayConfig, StorageConfig

    agent_no_discord = AgentConfig(
        name="plain",
        bot_token="tok",
        allowed_users=[1],
        project_dir=str(tmp_path),
        allowed_tools=["Read"],
    )
    config = RelayConfig(
        agents={
            "plain": agent_no_discord,
            "with_discord": _make_agent_config_with_discord(tmp_path, channel_id=333),
        },
        voice=VoiceConfig(),
        storage=StorageConfig(),
        discord=DiscordConfig(bot_token="tok", guild_id=1),
    )
    mapping = _build_channel_agent_map(config)
    assert mapping == {333: "with_discord"}


# ---------------------------------------------------------------------------
# _extract_and_send_files tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_files_no_markers():
    """Text without markers returned unchanged."""
    channel = MagicMock()
    channel.send = AsyncMock()
    result = await _extract_and_send_files(channel, "no files here")
    assert result == "no files here"
    channel.send.assert_not_called()


@pytest.mark.asyncio
async def test_extract_files_missing_file():
    """Missing file produces error note."""
    channel = MagicMock()
    channel.send = AsyncMock()
    result = await _extract_and_send_files(channel, "See [FILE:/nonexistent/file.txt] here")
    assert "(file not found:" in result


@pytest.mark.asyncio
async def test_extract_files_sends_file(tmp_path):
    """Existing file is sent via channel.send(file=...)."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello")

    channel = MagicMock()
    channel.send = AsyncMock()
    result = await _extract_and_send_files(channel, f"See [FILE:{test_file}] here")
    channel.send.assert_called_once()
    assert "file not found" not in result


# ---------------------------------------------------------------------------
# Config parsing tests (x1b.1 / x1b.4)
# ---------------------------------------------------------------------------


class TestDiscordConfig:
    """Tests for Discord config parsing."""

    def _write_yaml(self, tmp_path, content, filename="relay.yaml"):
        p = tmp_path / filename
        p.write_text(content)
        return str(p)

    def test_config_without_discord(self, tmp_path):
        """Config without discord section loads fine, discord is None."""
        from relay.config import load_config

        yaml_content = f"""\
agents:
  test:
    bot_token: "abc123"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"
"""
        path = self._write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.discord is None
        assert cfg.agents["test"].discord is None

    def test_config_with_discord(self, tmp_path):
        """Config with discord section parses correctly."""
        from relay.config import load_config

        yaml_content = f"""\
agents:
  test:
    bot_token: "abc123"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"
    discord:
      chat_channel: 9876543210

discord:
  bot_token: "discord-token-123"
  guild_id: 1234567890
  allowed_users:
    - 555555
"""
        path = self._write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.discord is not None
        assert cfg.discord.bot_token == "discord-token-123"
        assert cfg.discord.guild_id == 1234567890
        assert cfg.discord.allowed_users == [555555]
        assert cfg.agents["test"].discord is not None
        assert cfg.agents["test"].discord.chat_channel == 9876543210

    def test_discord_without_allowed_users(self, tmp_path):
        """Discord config without allowed_users is valid (None = no auth)."""
        from relay.config import load_config

        yaml_content = f"""\
agents:
  test:
    bot_token: "abc123"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"

discord:
  bot_token: "discord-token"
  guild_id: 1234567890
"""
        path = self._write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.discord.allowed_users is None

    def test_discord_missing_guild_id_raises(self, tmp_path):
        """Missing guild_id raises ValueError."""
        from relay.config import load_config

        yaml_content = f"""\
agents:
  test:
    bot_token: "abc123"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"

discord:
  bot_token: "discord-token"
"""
        path = self._write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="guild_id"):
            load_config(path)

    def test_discord_missing_bot_token_raises(self, tmp_path):
        """Missing discord bot_token raises ValueError."""
        from relay.config import load_config

        yaml_content = f"""\
agents:
  test:
    bot_token: "abc123"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"

discord:
  guild_id: 1234567890
"""
        path = self._write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="discord.bot_token"):
            load_config(path)

    def test_discord_agent_invalid_channel_raises(self, tmp_path):
        """Non-integer chat_channel raises ValueError."""
        from relay.config import load_config

        yaml_content = f"""\
agents:
  test:
    bot_token: "abc123"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"
    discord:
      chat_channel: "not-an-int"
"""
        path = self._write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="chat_channel"):
            load_config(path)


# ---------------------------------------------------------------------------
# Store platform isolation tests (x1b.2 / x1b.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_default_telegram(store):
    """Sessions created without platform default to 'telegram'."""
    session = await store.create_session(chat_id=100)
    assert session.platform == "telegram"


@pytest.mark.asyncio
async def test_platform_stored_and_retrieved(store):
    """Sessions store and return the platform."""
    session = await store.create_session(chat_id=100, platform="discord")
    assert session.platform == "discord"
    fetched = await store.get_session(session.id)
    assert fetched.platform == "discord"


@pytest.mark.asyncio
async def test_platform_session_isolation(store):
    """Same agent+chat_id on different platforms get separate sessions."""
    s_tg = await store.create_session(chat_id=100, agent_name="bot", platform="telegram")
    s_dc = await store.create_session(chat_id=100, agent_name="bot", platform="discord")

    assert s_tg.id != s_dc.id

    active_tg = await store.get_active_session(chat_id=100, agent_name="bot", platform="telegram")
    active_dc = await store.get_active_session(chat_id=100, agent_name="bot", platform="discord")
    assert active_tg.id == s_tg.id
    assert active_dc.id == s_dc.id


@pytest.mark.asyncio
async def test_platform_isolation_expire(store):
    """Expiring a telegram session doesn't affect discord session."""
    s_tg = await store.create_session(chat_id=100, agent_name="bot", platform="telegram")
    s_dc = await store.create_session(chat_id=100, agent_name="bot", platform="discord")

    await store.expire_session(s_tg.id)

    assert await store.get_active_session(chat_id=100, agent_name="bot", platform="telegram") is None
    active_dc = await store.get_active_session(chat_id=100, agent_name="bot", platform="discord")
    assert active_dc.id == s_dc.id


# ---------------------------------------------------------------------------
# Agent platform-aware prompt tests (x1b.3 / x1b.4)
# ---------------------------------------------------------------------------


def test_chat_system_prompts_exist():
    """Both telegram and discord prompts are defined."""
    from relay.agent import CHAT_SYSTEM_PROMPTS

    assert "telegram" in CHAT_SYSTEM_PROMPTS
    assert "discord" in CHAT_SYSTEM_PROMPTS
    assert "Telegram" in CHAT_SYSTEM_PROMPTS["telegram"]
    assert "Discord" in CHAT_SYSTEM_PROMPTS["discord"]


def test_intake_prompt_platform_neutral():
    """Intake system prompt says 'messaging relay' not 'Telegram'."""
    from relay.intake import INTAKE_SYSTEM_PROMPT

    assert "messaging relay" in INTAKE_SYSTEM_PROMPT
    assert "Telegram-to-agent" not in INTAKE_SYSTEM_PROMPT
