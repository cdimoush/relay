"""Telegram bot adapter — polling, auth, voice download, response chunking.

Handles incoming messages from Telegram, authorizes users, downloads voice
files, and routes everything through the intake pipeline.
"""

import asyncio
import logging
import os
import tempfile

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from relay import intake, voice
from relay.config import RelayConfig
from relay.store import Store

logger = logging.getLogger(__name__)

# Note: telegram.py uses module-level mutable globals for shared state across handlers.
# This is an intentional exception to the "no classes" convention — python-telegram-bot's
# handler registration model requires shared state, and module globals with a one-time
# init in start_bot() are the simplest approach without introducing a class.
_config: RelayConfig | None = None
_store: Store | None = None

TELEGRAM_MAX_LENGTH = 4096


def _is_authorized(user_id: int) -> bool:
    """Check if a Telegram user ID is in the allowed list."""
    return user_id in _config.telegram.allowed_users


async def _check_auth(update: Update) -> bool:
    """Check authorization. Silently drop unauthorized messages. Returns True if authorized."""
    if not update.effective_user:
        return False
    if not _is_authorized(update.effective_user.id):
        logger.warning("Unauthorized message from user %s", update.effective_user.id)
        return False
    return True


async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    if not await _check_auth(update):
        return

    chat_id = update.effective_chat.id
    message_text = update.message.text

    # Send "thinking" indicator
    await update.effective_chat.send_action("typing")

    try:
        response_text = await intake.handle_message(
            message_text,
            chat_id,
            _store,
            _config.agent,
        )
    except Exception as e:
        logger.exception("Error handling message")
        response_text = f"Something went wrong: {e}"

    await _send_chunked(update, response_text)


async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages."""
    if not await _check_auth(update):
        return

    chat_id = update.effective_chat.id

    # Download voice file to temp path
    voice_msg = update.message.voice
    file = await context.bot.get_file(voice_msg.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await file.download_to_drive(tmp_path)

        # Transcribe
        transcript = await voice.transcribe(tmp_path, backend=_config.voice.backend)

        # Send "Heard: ..." preview to user
        preview = transcript[:100] + ("..." if len(transcript) > 100 else "")
        await update.message.reply_text(f"Heard: {preview}")

        # Send typing indicator
        await update.effective_chat.send_action("typing")

        # Route through intake
        response_text = await intake.handle_message(
            transcript,
            chat_id,
            _store,
            _config.agent,
        )
    except voice.TranscriptionError as e:
        response_text = f"Couldn't transcribe your voice message: {e}"
    except Exception as e:
        logger.exception("Error handling voice message")
        response_text = f"Something went wrong: {e}"
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    await _send_chunked(update, response_text)


async def _send_chunked(update: Update, text: str) -> None:
    """Send a response, splitting into multiple messages if needed."""
    if not text:
        text = "(empty response)"

    chunks = [
        text[i : i + TELEGRAM_MAX_LENGTH]
        for i in range(0, len(text), TELEGRAM_MAX_LENGTH)
    ]
    for chunk in chunks:
        await update.message.reply_text(chunk)


async def start_bot(config: RelayConfig, store: Store) -> None:
    """Start the Telegram bot with long-polling.

    Sets up message handlers, initializes the bot, and runs polling.
    Starts polling in the background. The caller is responsible for keeping
    the event loop alive and handling shutdown signals.

    Args:
        config: Full RelayConfig from config.py
        store: Initialized Store instance from store.py
    """
    global _config, _store
    _config = config
    _store = store

    app = Application.builder().token(config.telegram.bot_token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))
    app.add_handler(MessageHandler(filters.VOICE, _handle_voice))

    logger.info("Starting Telegram bot polling...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Block until cancelled (SIGTERM/SIGINT triggers CancelledError via main.py)
    try:
        await asyncio.Event().wait()
    finally:
        logger.info("Stopping Telegram bot...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
