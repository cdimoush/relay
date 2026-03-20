"""Discord bot adapter — single bot, channel-based routing, message handling.

Handles incoming messages from Discord, authorizes users, downloads voice
and file attachments, and routes everything through the intake pipeline.

Single-bot: one discord.Client with channel-to-agent routing. Each agent
maps to a Discord channel via config. All run in the same asyncio event loop.
"""

import logging
import os
import re

import discord

from relay import intake, voice
from relay.config import RelayConfig
from relay.intake import IntakeResult
from relay.store import Store

logger = logging.getLogger(__name__)

DISCORD_MAX_LENGTH = 2000
DISCORD_MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB
FILE_MARKER_RE = re.compile(r"\[FILE:(.*?)\]")

AUDIO_EXTENSIONS = {".ogg", ".oga", ".mp3", ".wav"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


async def _extract_and_send_files(channel: discord.TextChannel, text: str) -> str:
    """Parse [FILE:/path] markers from text, send each as a Discord file.

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
        elif os.path.getsize(path) > DISCORD_MAX_FILE_SIZE:
            size_mb = os.path.getsize(path) / (1024 * 1024)
            replacement = f"(file too large: {path} — {size_mb:.1f} MB, limit 25 MB)"
            logger.warning("File marker references oversized file: %s (%.1f MB)", path, size_mb)
        else:
            try:
                await channel.send(file=discord.File(path))
                replacement = ""
                logger.info("Sent file to Discord: %s", path)
            except Exception as e:
                replacement = f"(failed to send file: {path} — {e})"
                logger.exception("Failed to send file: %s", path)

        text = text[:start] + replacement + text[end:]

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


async def _send_chunked(channel: discord.TextChannel, text: str) -> None:
    """Send a response, splitting into multiple messages if needed."""
    if not text:
        text = "(empty response)"

    chunks = [
        text[i : i + DISCORD_MAX_LENGTH]
        for i in range(0, len(text), DISCORD_MAX_LENGTH)
    ]
    for chunk in chunks:
        await channel.send(chunk)


def _build_channel_agent_map(config: RelayConfig) -> dict[int, str]:
    """Build {channel_id: agent_name} from config."""
    mapping = {}
    for name, agent_config in config.agents.items():
        if agent_config.discord and agent_config.discord.chat_channel:
            mapping[agent_config.discord.chat_channel] = name
    return mapping


async def start(config: RelayConfig, store: Store) -> None:
    """Start the Discord bot. Blocks until cancelled.

    Args:
        config: Full RelayConfig with discord section
        store: Initialized Store instance
    """
    if not config.discord or not config.discord.bot_token:
        logger.warning("Discord start called but no discord config — skipping")
        return

    channel_agent_map = _build_channel_agent_map(config)
    if not channel_agent_map:
        logger.warning("Discord enabled but no agents have discord.chat_channel configured")
        return

    discord_config = config.discord
    voice_config = config.voice

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        logger.info(
            "Discord bot connected as %s (guild_id=%d, channels=%s)",
            client.user,
            discord_config.guild_id,
            list(channel_agent_map.keys()),
        )

    @client.event
    async def on_message(message: discord.Message):
        # Skip bot messages
        if message.author.bot:
            return

        # Check channel routing
        agent_name = channel_agent_map.get(message.channel.id)
        if not agent_name:
            return

        # Auth check
        if discord_config.allowed_users and message.author.id not in discord_config.allowed_users:
            logger.warning(
                "Unauthorized Discord message from user %s in channel %s",
                message.author.id,
                message.channel.id,
            )
            return

        agent_config = config.agents[agent_name]

        # Handle attachments
        if message.attachments:
            await _handle_attachments(
                message, agent_name, agent_config, voice_config, store
            )
            return

        # Text messages
        if not message.content:
            return

        async with message.channel.typing():
            async def _ack(result: IntakeResult) -> None:
                if result.action == "forward":
                    await message.channel.send("On it...")

            try:
                response_text = await intake.handle_message(
                    agent_name,
                    message.content,
                    message.channel.id,
                    store,
                    agent_config,
                    on_classify=_ack,
                    platform="discord",
                )
            except Exception as e:
                logger.exception("Error handling Discord message")
                response_text = f"Something went wrong: {e}"

        response_text = await _extract_and_send_files(message.channel, response_text)
        await _send_chunked(message.channel, response_text)

    async def _handle_attachments(
        message: discord.Message,
        agent_name: str,
        agent_config,
        voice_config,
        store: Store,
    ):
        """Handle Discord message attachments: voice, audio, images, other files."""
        staging_dir = f"/tmp/relay-{agent_name}"
        os.makedirs(staging_dir, exist_ok=True)

        for attachment in message.attachments:
            filename = attachment.filename
            suffix = os.path.splitext(filename)[1].lower()
            dest_path = os.path.join(staging_dir, filename)

            # Check if this is a voice message (Discord voice messages have specific flag)
            is_voice = bool(message.flags.value & (1 << 13))  # MessageFlags.voice

            try:
                await attachment.save(dest_path)
                logger.info(
                    "Discord attachment saved: agent=%s file=%s size=%d",
                    agent_name, dest_path, attachment.size,
                )

                if is_voice or suffix in AUDIO_EXTENSIONS:
                    # Voice/audio — transcribe
                    try:
                        transcript = await voice.transcribe(dest_path, backend=voice_config.backend)
                        preview = transcript[:100] + ("..." if len(transcript) > 100 else "")
                        await message.channel.send(f"Heard: {preview}")

                        async def _ack(result: IntakeResult) -> None:
                            if result.action == "forward":
                                await message.channel.send("On it...")

                        response_text = await intake.handle_message(
                            agent_name,
                            transcript,
                            message.channel.id,
                            store,
                            agent_config,
                            on_classify=_ack,
                            platform="discord",
                        )
                    except voice.TranscriptionError as e:
                        response_text = f"Couldn't transcribe your voice message: {e}"
                    except Exception as e:
                        logger.exception("Error handling Discord voice attachment")
                        response_text = f"Something went wrong: {e}"
                    finally:
                        try:
                            os.unlink(dest_path)
                        except OSError:
                            pass

                elif suffix in IMAGE_EXTENSIONS:
                    # Image
                    caption = message.content or ""
                    parts = [f"[Photo received: {dest_path}]"]
                    if caption:
                        parts.append(caption)
                    message_text = " ".join(parts)

                    async def _ack(result: IntakeResult) -> None:
                        if result.action == "forward":
                            await message.channel.send("On it...")

                    try:
                        response_text = await intake.handle_message(
                            agent_name,
                            message_text,
                            message.channel.id,
                            store,
                            agent_config,
                            on_classify=_ack,
                            platform="discord",
                        )
                    except Exception as e:
                        logger.exception("Error handling Discord image")
                        response_text = f"Something went wrong: {e}"

                else:
                    # Other file
                    caption = message.content or ""
                    parts = [f"[File received: {dest_path}]"]
                    if caption:
                        parts.append(caption)
                    message_text = " ".join(parts)

                    async def _ack(result: IntakeResult) -> None:
                        if result.action == "forward":
                            await message.channel.send("On it...")

                    try:
                        response_text = await intake.handle_message(
                            agent_name,
                            message_text,
                            message.channel.id,
                            store,
                            agent_config,
                            on_classify=_ack,
                            platform="discord",
                        )
                    except Exception as e:
                        logger.exception("Error handling Discord file")
                        response_text = f"Something went wrong: {e}"

            except Exception as e:
                logger.exception("Failed to save Discord attachment: %s", filename)
                response_text = f"Failed to save attachment: {e}"

            response_text = await _extract_and_send_files(message.channel, response_text)
            await _send_chunked(message.channel, response_text)

    try:
        await client.start(discord_config.bot_token)
    except Exception:
        logger.exception("Discord bot crashed")
        raise
