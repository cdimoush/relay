"""Tests for relay.telegram — Telegram bot adapter layer."""

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from relay.config import AgentConfig, VoiceConfig
from relay.intake import IntakeResult
from relay.telegram import (
    TELEGRAM_MAX_LENGTH,
    _make_text_handler,
    _make_voice_handler,
    _send_chunked,
    start_bots,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update(user_id=111111, chat_id=100, text="hello"):
    """Create a mock telegram Update with configurable user/chat/text."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.send_action = AsyncMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_voice_update(user_id=111111, chat_id=100, file_id="file123"):
    """Create a mock telegram Update with a voice message."""
    update = _make_update(user_id=user_id, chat_id=chat_id, text=None)
    update.message.voice = MagicMock()
    update.message.voice.file_id = file_id
    return update


def _make_context(download_bytes=b"audio-data"):
    """Create a mock telegram context with bot.get_file."""
    ctx = MagicMock()
    mock_file = MagicMock()
    mock_file.download_to_drive = AsyncMock()
    ctx.bot.get_file = AsyncMock(return_value=mock_file)
    return ctx


# ---------------------------------------------------------------------------
# _send_chunked tests (relay-lyt.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_chunked_short_message():
    """Text under 4096 chars sends one message."""
    update = _make_update()
    await _send_chunked(update, "short text")
    update.message.reply_text.assert_called_once_with("short text")


@pytest.mark.asyncio
async def test_send_chunked_empty_string():
    """Empty text sends '(empty response)'."""
    update = _make_update()
    await _send_chunked(update, "")
    update.message.reply_text.assert_called_once_with("(empty response)")


@pytest.mark.asyncio
async def test_send_chunked_none_text():
    """None text sends '(empty response)'."""
    update = _make_update()
    await _send_chunked(update, None)
    update.message.reply_text.assert_called_once_with("(empty response)")


@pytest.mark.asyncio
async def test_send_chunked_exact_boundary():
    """Exactly 4096 chars sends one message."""
    update = _make_update()
    text = "A" * TELEGRAM_MAX_LENGTH
    await _send_chunked(update, text)
    update.message.reply_text.assert_called_once_with(text)


@pytest.mark.asyncio
async def test_send_chunked_splits_long():
    """5000 chars sends two messages (4096 + 904)."""
    update = _make_update()
    text = "B" * 5000
    await _send_chunked(update, text)
    assert update.message.reply_text.call_count == 2
    calls = update.message.reply_text.call_args_list
    assert len(calls[0][0][0]) == TELEGRAM_MAX_LENGTH
    assert len(calls[1][0][0]) == 904


@pytest.mark.asyncio
async def test_send_chunked_multiple_chunks():
    """10000 chars sends three messages."""
    update = _make_update()
    text = "C" * 10000
    await _send_chunked(update, text)
    assert update.message.reply_text.call_count == 3


# ---------------------------------------------------------------------------
# _make_text_handler tests (relay-lyt.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_handler_authorized_user(sample_agent_config, store):
    """Allowed user's message routes to intake and response is sent back."""
    handler = _make_text_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_update(user_id=111111, text="hello agent")
    ctx = MagicMock()

    with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock, return_value="response text") as mock_intake:
        await handler(update, ctx)

    mock_intake.assert_called_once()
    # Response should have been sent
    assert any("response text" in str(c) for c in update.message.reply_text.call_args_list)


@pytest.mark.asyncio
async def test_text_handler_unauthorized_user(sample_agent_config, store):
    """Non-allowed user is silently ignored, intake never called."""
    handler = _make_text_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_update(user_id=999999, text="sneaky")
    ctx = MagicMock()

    with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock) as mock_intake:
        await handler(update, ctx)

    mock_intake.assert_not_called()


@pytest.mark.asyncio
async def test_text_handler_no_effective_user(sample_agent_config, store):
    """Update with no effective_user returns early."""
    handler = _make_text_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_update()
    update.effective_user = None
    ctx = MagicMock()

    with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock) as mock_intake:
        await handler(update, ctx)

    mock_intake.assert_not_called()


@pytest.mark.asyncio
async def test_text_handler_sends_typing_action(sample_agent_config, store):
    """Typing indicator sent before processing."""
    handler = _make_text_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_update(user_id=111111)
    ctx = MagicMock()

    with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock, return_value="ok"):
        await handler(update, ctx)

    update.effective_chat.send_action.assert_called_once_with("typing")


@pytest.mark.asyncio
async def test_text_handler_ack_on_forward(sample_agent_config, store):
    """'On it...' sent when intake classifies as forward."""
    handler = _make_text_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_update(user_id=111111)
    ctx = MagicMock()

    async def fake_handle_message(*args, on_classify=None, **kwargs):
        if on_classify:
            await on_classify(IntakeResult(action="forward", cleaned_message="hello"))
        return "done"

    with patch("relay.telegram.intake.handle_message", side_effect=fake_handle_message):
        await handler(update, ctx)

    # Check "On it..." was sent
    reply_calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("On it..." in c for c in reply_calls)


@pytest.mark.asyncio
async def test_text_handler_no_ack_on_status(sample_agent_config, store):
    """No 'On it...' for non-forward actions."""
    handler = _make_text_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_update(user_id=111111)
    ctx = MagicMock()

    async def fake_handle_message(*args, on_classify=None, **kwargs):
        if on_classify:
            await on_classify(IntakeResult(action="status", cleaned_message=""))
        return "session info"

    with patch("relay.telegram.intake.handle_message", side_effect=fake_handle_message):
        await handler(update, ctx)

    reply_calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert not any("On it..." in c for c in reply_calls)


@pytest.mark.asyncio
async def test_text_handler_intake_exception(sample_agent_config, store):
    """Exception in intake caught, user sees 'Something went wrong'."""
    handler = _make_text_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_update(user_id=111111)
    ctx = MagicMock()

    with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        await handler(update, ctx)

    reply_calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("Something went wrong" in c for c in reply_calls)


@pytest.mark.asyncio
async def test_text_handler_logs_unauthorized(sample_agent_config, store, caplog):
    """Warning logged with user ID and agent name for unauthorized access."""
    handler = _make_text_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_update(user_id=999999)
    ctx = MagicMock()

    with caplog.at_level(logging.WARNING, logger="relay.telegram"):
        with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock) as mock_intake:
            await handler(update, ctx)

    assert any("999999" in r.message and "test-agent" in r.message for r in caplog.records)
    mock_intake.assert_not_called()


# ---------------------------------------------------------------------------
# _make_voice_handler tests (relay-lyt.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_handler_authorized(sample_agent_config, store):
    """Downloads file, transcribes, sends preview, routes through intake."""
    handler = _make_voice_handler("test-agent", sample_agent_config, VoiceConfig(backend="vox"), store)
    update = _make_voice_update(user_id=111111)
    ctx = _make_context()

    with patch("relay.telegram.voice.transcribe", new_callable=AsyncMock, return_value="transcribed text"):
        with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock, return_value="agent reply"):
            with patch("relay.telegram.os.unlink"):
                await handler(update, ctx)

    # Preview sent
    reply_calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("Heard:" in c for c in reply_calls)
    # Response sent
    assert any("agent reply" in c for c in reply_calls)


@pytest.mark.asyncio
async def test_voice_handler_unauthorized(sample_agent_config, store):
    """Non-allowed user silently ignored."""
    handler = _make_voice_handler("test-agent", sample_agent_config, VoiceConfig(), store)
    update = _make_voice_update(user_id=999999)
    ctx = _make_context()

    with patch("relay.telegram.voice.transcribe", new_callable=AsyncMock) as mock_transcribe:
        await handler(update, ctx)

    mock_transcribe.assert_not_called()


@pytest.mark.asyncio
async def test_voice_handler_transcription_error(sample_agent_config, store):
    """TranscriptionError caught, friendly error shown."""
    from relay.voice import TranscriptionError

    handler = _make_voice_handler("test-agent", sample_agent_config, VoiceConfig(backend="vox"), store)
    update = _make_voice_update(user_id=111111)
    ctx = _make_context()

    with patch("relay.telegram.voice.transcribe", new_callable=AsyncMock, side_effect=TranscriptionError("vox failed")):
        with patch("relay.telegram.os.unlink"):
            await handler(update, ctx)

    reply_calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("Couldn't transcribe" in c for c in reply_calls)


@pytest.mark.asyncio
async def test_voice_handler_generic_exception(sample_agent_config, store):
    """Other exceptions caught, 'Something went wrong' shown."""
    handler = _make_voice_handler("test-agent", sample_agent_config, VoiceConfig(backend="vox"), store)
    update = _make_voice_update(user_id=111111)
    ctx = _make_context()

    with patch("relay.telegram.voice.transcribe", new_callable=AsyncMock, side_effect=RuntimeError("unexpected")):
        with patch("relay.telegram.os.unlink"):
            await handler(update, ctx)

    reply_calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("Something went wrong" in c for c in reply_calls)


@pytest.mark.asyncio
async def test_voice_handler_temp_cleanup_on_success(sample_agent_config, store):
    """Temp file deleted after successful transcription."""
    handler = _make_voice_handler("test-agent", sample_agent_config, VoiceConfig(backend="vox"), store)
    update = _make_voice_update(user_id=111111)
    ctx = _make_context()

    with patch("relay.telegram.voice.transcribe", new_callable=AsyncMock, return_value="text"):
        with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock, return_value="reply"):
            with patch("relay.telegram.os.unlink") as mock_unlink:
                await handler(update, ctx)

    mock_unlink.assert_called_once()


@pytest.mark.asyncio
async def test_voice_handler_temp_cleanup_on_error(sample_agent_config, store):
    """Temp file deleted even when transcription fails."""
    from relay.voice import TranscriptionError

    handler = _make_voice_handler("test-agent", sample_agent_config, VoiceConfig(backend="vox"), store)
    update = _make_voice_update(user_id=111111)
    ctx = _make_context()

    with patch("relay.telegram.voice.transcribe", new_callable=AsyncMock, side_effect=TranscriptionError("fail")):
        with patch("relay.telegram.os.unlink") as mock_unlink:
            await handler(update, ctx)

    mock_unlink.assert_called_once()


@pytest.mark.asyncio
async def test_voice_handler_preview_truncation(sample_agent_config, store):
    """Preview capped at 100 chars with ellipsis."""
    handler = _make_voice_handler("test-agent", sample_agent_config, VoiceConfig(backend="vox"), store)
    update = _make_voice_update(user_id=111111)
    ctx = _make_context()

    long_text = "A" * 200

    with patch("relay.telegram.voice.transcribe", new_callable=AsyncMock, return_value=long_text):
        with patch("relay.telegram.intake.handle_message", new_callable=AsyncMock, return_value="reply"):
            with patch("relay.telegram.os.unlink"):
                await handler(update, ctx)

    # Find the preview reply
    reply_calls = update.message.reply_text.call_args_list
    preview_call = [c for c in reply_calls if "Heard:" in str(c)][0]
    preview_text = preview_call[0][0]
    assert preview_text == f"Heard: {'A' * 100}..."


@pytest.mark.asyncio
async def test_voice_handler_ack_on_forward(sample_agent_config, store):
    """'On it...' sent for forward classification."""
    handler = _make_voice_handler("test-agent", sample_agent_config, VoiceConfig(backend="vox"), store)
    update = _make_voice_update(user_id=111111)
    ctx = _make_context()

    async def fake_handle_message(*args, on_classify=None, **kwargs):
        if on_classify:
            await on_classify(IntakeResult(action="forward", cleaned_message="hello"))
        return "done"

    with patch("relay.telegram.voice.transcribe", new_callable=AsyncMock, return_value="hello"):
        with patch("relay.telegram.intake.handle_message", side_effect=fake_handle_message):
            with patch("relay.telegram.os.unlink"):
                await handler(update, ctx)

    reply_calls = [str(c) for c in update.message.reply_text.call_args_list]
    assert any("On it..." in c for c in reply_calls)


# ---------------------------------------------------------------------------
# start_bots tests (relay-lyt.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_bots_creates_app_per_agent(sample_relay_config, store):
    """One Application created per config agent."""
    mock_app = MagicMock()
    mock_app.initialize = AsyncMock()
    mock_app.start = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.start_polling = AsyncMock()
    mock_app.updater.stop = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    mock_app.add_handler = MagicMock()

    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app

    # Cancel after apps start to avoid blocking forever
    async def cancel_after_start(*args, **kwargs):
        raise asyncio.CancelledError()

    with patch("relay.telegram.Application.builder", return_value=mock_builder):
        with patch("relay.telegram.asyncio.Event") as mock_event:
            mock_event.return_value.wait = AsyncMock(side_effect=asyncio.CancelledError)
            with pytest.raises(asyncio.CancelledError):
                await start_bots(sample_relay_config, store)

    mock_builder.token.assert_called_once_with(sample_relay_config.agents["test-agent"].bot_token)
    mock_app.initialize.assert_called_once()
    mock_app.start.assert_called_once()


@pytest.mark.asyncio
async def test_start_bots_registers_text_and_voice_handlers(sample_relay_config, store):
    """Both text and voice handler types registered."""
    mock_app = MagicMock()
    mock_app.initialize = AsyncMock()
    mock_app.start = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.start_polling = AsyncMock()
    mock_app.updater.stop = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    mock_app.add_handler = MagicMock()

    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app

    with patch("relay.telegram.Application.builder", return_value=mock_builder):
        with patch("relay.telegram.asyncio.Event") as mock_event:
            mock_event.return_value.wait = AsyncMock(side_effect=asyncio.CancelledError)
            with pytest.raises(asyncio.CancelledError):
                await start_bots(sample_relay_config, store)

    assert mock_app.add_handler.call_count == 2


@pytest.mark.asyncio
async def test_start_bots_initializes_and_starts_polling(sample_relay_config, store):
    """app.initialize, app.start, updater.start_polling called in order."""
    call_order = []

    mock_app = MagicMock()
    mock_app.initialize = AsyncMock(side_effect=lambda: call_order.append("initialize"))
    mock_app.start = AsyncMock(side_effect=lambda: call_order.append("start"))
    mock_app.updater = MagicMock()
    mock_app.updater.start_polling = AsyncMock(side_effect=lambda: call_order.append("polling"))
    mock_app.updater.stop = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    mock_app.add_handler = MagicMock()

    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app

    with patch("relay.telegram.Application.builder", return_value=mock_builder):
        with patch("relay.telegram.asyncio.Event") as mock_event:
            mock_event.return_value.wait = AsyncMock(side_effect=asyncio.CancelledError)
            with pytest.raises(asyncio.CancelledError):
                await start_bots(sample_relay_config, store)

    assert call_order == ["initialize", "start", "polling"]


@pytest.mark.asyncio
async def test_start_bots_cleanup_on_cancel(sample_relay_config, store):
    """CancelledError triggers stop/shutdown on all apps."""
    mock_app = MagicMock()
    mock_app.initialize = AsyncMock()
    mock_app.start = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.start_polling = AsyncMock()
    mock_app.updater.stop = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    mock_app.add_handler = MagicMock()

    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app

    with patch("relay.telegram.Application.builder", return_value=mock_builder):
        with patch("relay.telegram.asyncio.Event") as mock_event:
            mock_event.return_value.wait = AsyncMock(side_effect=asyncio.CancelledError)
            with pytest.raises(asyncio.CancelledError):
                await start_bots(sample_relay_config, store)

    mock_app.updater.stop.assert_called_once()
    mock_app.stop.assert_called_once()
    mock_app.shutdown.assert_called_once()


@pytest.mark.asyncio
async def test_start_bots_logs_agent_names(sample_relay_config, store, caplog):
    """Agent names logged on startup."""
    mock_app = MagicMock()
    mock_app.initialize = AsyncMock()
    mock_app.start = AsyncMock()
    mock_app.updater = MagicMock()
    mock_app.updater.start_polling = AsyncMock()
    mock_app.updater.stop = AsyncMock()
    mock_app.stop = AsyncMock()
    mock_app.shutdown = AsyncMock()
    mock_app.add_handler = MagicMock()

    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_app

    with caplog.at_level(logging.INFO, logger="relay.telegram"):
        with patch("relay.telegram.Application.builder", return_value=mock_builder):
            with patch("relay.telegram.asyncio.Event") as mock_event:
                mock_event.return_value.wait = AsyncMock(side_effect=asyncio.CancelledError)
                with pytest.raises(asyncio.CancelledError):
                    await start_bots(sample_relay_config, store)

    assert any("test-agent" in r.message for r in caplog.records)
