"""Tests for relay.voice — mock vox subprocess, mock OpenAI fallback."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from relay.voice import TranscriptionError, transcribe

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.asyncio


def _make_mock_process(stdout=b"", stderr=b"", returncode=0):
    """Create a mock subprocess with the given outputs."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.pid = 12345
    return proc


# --- vox backend ---


async def test_vox_success():
    """Successful vox transcription returns the text."""
    proc = _make_mock_process(stdout=b"Hello world")
    with patch("relay.voice.asyncio.create_subprocess_exec", return_value=proc):
        result = await transcribe("/tmp/audio.ogg", backend="vox")
    assert result == "Hello world"


async def test_vox_failure_nonzero_exit():
    """Non-zero exit code from vox raises TranscriptionError."""
    proc = _make_mock_process(stderr=b"decode error", returncode=1)
    with patch("relay.voice.asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(TranscriptionError, match="Vox failed"):
            await transcribe("/tmp/audio.ogg", backend="vox")


async def test_vox_empty_output():
    """Empty stdout from vox raises TranscriptionError."""
    proc = _make_mock_process(stdout=b"   ")
    with patch("relay.voice.asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(TranscriptionError, match="empty transcription"):
            await transcribe("/tmp/audio.ogg", backend="vox")


async def test_vox_timeout():
    """Vox process that exceeds timeout raises TranscriptionError."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.pid = 12345

    with patch("relay.voice.asyncio.create_subprocess_exec", return_value=proc):
        with pytest.raises(TranscriptionError, match="timed out"):
            await transcribe("/tmp/audio.ogg", backend="vox")


async def test_vox_not_found_falls_back_to_openai(tmp_path):
    """When vox binary is not found, falls back to openai backend."""
    audio_file = tmp_path / "audio.ogg"
    audio_file.write_bytes(b"fake audio data")

    mock_openai_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Transcribed via OpenAI"
    mock_openai_client.audio.transcriptions.create = AsyncMock(
        return_value=mock_response
    )

    with patch(
        "relay.voice.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError("vox not found"),
    ):
        with patch("openai.AsyncOpenAI", return_value=mock_openai_client):
            result = await transcribe(str(audio_file), backend="vox")

    assert result == "Transcribed via OpenAI"


async def test_vox_permission_error_falls_back_to_openai(tmp_path):
    """When vox binary is not executable, falls back to openai backend."""
    audio_file = tmp_path / "audio.ogg"
    audio_file.write_bytes(b"fake audio data")

    mock_openai_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Transcribed via OpenAI"
    mock_openai_client.audio.transcriptions.create = AsyncMock(
        return_value=mock_response
    )

    with patch(
        "relay.voice.asyncio.create_subprocess_exec",
        side_effect=PermissionError("not executable"),
    ):
        with patch("openai.AsyncOpenAI", return_value=mock_openai_client):
            result = await transcribe(str(audio_file), backend="vox")

    assert result == "Transcribed via OpenAI"


# --- openai backend ---


async def test_openai_success(tmp_path):
    """Successful OpenAI transcription returns the text."""
    # Create a real temp file so open() works
    audio_file = tmp_path / "audio.ogg"
    audio_file.write_bytes(b"fake audio data")

    mock_openai_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.text = "Hello from OpenAI"
    mock_openai_client.audio.transcriptions.create = AsyncMock(
        return_value=mock_response
    )

    with patch("openai.AsyncOpenAI", return_value=mock_openai_client):
        result = await transcribe(str(audio_file), backend="openai")

    assert result == "Hello from OpenAI"


async def test_openai_failure(tmp_path):
    """OpenAI API error raises TranscriptionError."""
    audio_file = tmp_path / "audio.ogg"
    audio_file.write_bytes(b"fake audio data")

    mock_openai_client = AsyncMock()
    mock_openai_client.audio.transcriptions.create = AsyncMock(
        side_effect=Exception("API rate limited")
    )

    with patch("openai.AsyncOpenAI", return_value=mock_openai_client):
        with pytest.raises(TranscriptionError, match="OpenAI transcription failed"):
            await transcribe(str(audio_file), backend="openai")


# --- unknown backend ---


async def test_unknown_backend_raises():
    """Unknown backend raises ValueError."""
    with pytest.raises(ValueError, match="Unknown voice backend"):
        await transcribe("/tmp/audio.ogg", backend="whisperx")
