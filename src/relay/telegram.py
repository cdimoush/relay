"""Telegram bot adapter — polling, auth, voice/document download, response chunking.

Handles incoming messages from Telegram, authorizes users, downloads voice
and document files, and routes everything through the intake pipeline.

Multi-bot: one Application per agent in config.agents, each with its own
bot_token, handlers, and polling loop, all running concurrently.
"""

import asyncio
import logging
import os
import re
import tempfile

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from relay import intake, voice
from relay.config import AgentConfig, RelayConfig, VoiceConfig
from relay.intake import IntakeResult
from relay.store import Store

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096
TELEGRAM_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
FILE_MARKER_RE = re.compile(r"\[FILE:(.*?)\]")


async def _extract_and_send_files(update: Update, text: str) -> str:
    """Parse [FILE:/path] markers from text, send each as a Telegram document.

    Returns the text with markers stripped (or replaced with error notes).
    """
    markers = list(FILE_MARKER_RE.finditer(text))
    if not markers:
        return text

    for match in reversed(markers):
        path = match.group(1).strip()
        start, end = match.start(), match.end()

        if not os.path.isfile(path):
            replacement = f"(file not found: {path})"
            logger.warning("File marker references missing file: %s", path)
        elif os.path.getsize(path) > TELEGRAM_MAX_FILE_SIZE:
            size_mb = os.path.getsize(path) / (1024 * 1024)
            replacement = f"(file too large: {path} — {size_mb:.1f} MB, limit 50 MB)"
            logger.warning("File marker references oversized file: %s (%.1f MB)", path, size_mb)
        else:
            try:
                with open(path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(path),
                    )
                replacement = ""
                logger.info("Sent file to user: %s", path)
            except Exception as e:
                replacement = f"(failed to send file: {path} — {e})"
                logger.exception("Failed to send file: %s", path)

        text = text[:start] + replacement + text[end:]

    # Clean up extra blank lines left by stripped markers
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


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


def _make_text_handler(
    agent_name: str,
    agent_config: AgentConfig,
    voice_config: VoiceConfig,
    store: Store,
):
    """Create a text message handler closure capturing per-agent state."""

    async def _handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming text messages."""
        if not update.effective_user:
            return
        if update.effective_user.id not in agent_config.allowed_users:
            logger.warning(
                "Unauthorized message from user %s on bot %s",
                update.effective_user.id,
                agent_name,
            )
            return

        chat_id = update.effective_chat.id
        message_text = update.message.text

        # Immediate ack so user knows the message was received
        await update.effective_chat.send_action("typing")

        async def _ack(result: IntakeResult) -> None:
            if result.action == "forward":
                await update.message.reply_text("On it...")

        try:
            response_text = await intake.handle_message(
                agent_name,
                message_text,
                chat_id,
                store,
                agent_config,
                on_classify=_ack,
            )
        except Exception as e:
            logger.exception("Error handling message")
            response_text = f"Something went wrong: {e}"

        response_text = await _extract_and_send_files(update, response_text)
        await _send_chunked(update, response_text)

    return _handle_text


def _make_voice_handler(
    agent_name: str,
    agent_config: AgentConfig,
    voice_config: VoiceConfig,
    store: Store,
):
    """Create a voice message handler closure capturing per-agent state."""

    async def _handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming voice messages."""
        if not update.effective_user:
            return
        if update.effective_user.id not in agent_config.allowed_users:
            logger.warning(
                "Unauthorized message from user %s on bot %s",
                update.effective_user.id,
                agent_name,
            )
            return

        chat_id = update.effective_chat.id

        # Download voice file to temp path
        voice_msg = update.message.voice
        file = await context.bot.get_file(voice_msg.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await file.download_to_drive(tmp_path)

            # Transcribe — uses global voice config, not per-agent
            transcript = await voice.transcribe(tmp_path, backend=voice_config.backend)

            # Send "Heard: ..." preview to user
            preview = transcript[:100] + ("..." if len(transcript) > 100 else "")
            await update.message.reply_text(f"Heard: {preview}")

            async def _ack(result: IntakeResult) -> None:
                if result.action == "forward":
                    await update.message.reply_text("On it...")

            # Route through intake
            response_text = await intake.handle_message(
                agent_name,
                transcript,
                chat_id,
                store,
                agent_config,
                on_classify=_ack,
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

        response_text = await _extract_and_send_files(update, response_text)
        await _send_chunked(update, response_text)

    return _handle_voice


def _make_document_handler(
    agent_name: str,
    agent_config: AgentConfig,
    voice_config: VoiceConfig,
    store: Store,
):
    """Create a document message handler closure capturing per-agent state."""

    async def _handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming document/file messages."""
        if not update.effective_user:
            return
        if update.effective_user.id not in agent_config.allowed_users:
            logger.warning(
                "Unauthorized document from user %s on bot %s",
                update.effective_user.id,
                agent_name,
            )
            return

        chat_id = update.effective_chat.id
        doc = update.message.document
        file = await context.bot.get_file(doc.file_id)
        filename = doc.file_name or f"file_{doc.file_id}"

        # Create staging dir on demand
        staging_dir = f"/tmp/relay-{agent_name}"
        os.makedirs(staging_dir, exist_ok=True)
        dest_path = os.path.join(staging_dir, filename)

        await update.effective_chat.send_action("typing")

        try:
            await file.download_to_drive(dest_path)
            logger.info(
                "Document saved: agent=%s file=%s size=%d",
                agent_name, dest_path, doc.file_size or 0,
            )

            # Build message for agent
            caption = update.message.caption or ""
            suffix = os.path.splitext(filename)[1].lower()

            # Transcribe audio files and include transcript
            transcript = ""
            if suffix in (".ogg", ".oga"):
                try:
                    transcript = await voice.transcribe(dest_path, backend=voice_config.backend)
                    preview = transcript[:100] + ("..." if len(transcript) > 100 else "")
                    await update.message.reply_text(f"Heard: {preview}")
                except voice.TranscriptionError as e:
                    logger.warning("Document audio transcription failed: %s", e)
                    transcript = f"(transcription failed: {e})"

            # Compose the forwarded message
            parts = [f"[File received: {dest_path}]"]
            if transcript:
                parts.append(f"[Transcript: {transcript}]")
            if caption:
                parts.append(caption)
            message_text = " ".join(parts)

            async def _ack(result: IntakeResult) -> None:
                if result.action == "forward":
                    await update.message.reply_text("On it...")

            response_text = await intake.handle_message(
                agent_name,
                message_text,
                chat_id,
                store,
                agent_config,
                on_classify=_ack,
            )
        except Exception as e:
            logger.exception("Error handling document")
            response_text = f"Something went wrong: {e}"

        response_text = await _extract_and_send_files(update, response_text)
        await _send_chunked(update, response_text)

    return _handle_document


async def start_bots(config: RelayConfig, store: Store) -> None:
    """Start one Telegram bot per agent, all polling concurrently.

    Each agent in config.agents gets its own Application with its own bot_token
    and closure-based handlers. All run in the same asyncio event loop.

    Args:
        config: Full RelayConfig with agents dict
        store: Initialized Store instance
    """
    apps: list[Application] = []

    for name, agent_config in config.agents.items():
        app = Application.builder().token(agent_config.bot_token).build()

        text_handler = _make_text_handler(name, agent_config, config.voice, store)
        voice_handler = _make_voice_handler(name, agent_config, config.voice, store)
        doc_handler = _make_document_handler(name, agent_config, config.voice, store)

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
        app.add_handler(MessageHandler(filters.VOICE, voice_handler))
        app.add_handler(MessageHandler(filters.Document.ALL, doc_handler))

        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        apps.append(app)

        logger.info("Started Telegram bot for agent '%s'", name)

    agent_names = list(config.agents.keys())
    logger.info("All bots started: %s", agent_names)

    # Block until cancelled (SIGTERM/SIGINT triggers CancelledError via main.py)
    try:
        await asyncio.Event().wait()
    finally:
        logger.info("Stopping all Telegram bots...")
        for app in apps:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
