"""Voice transcription module.

Primary backend: vox CLI (subprocess). Fallback: direct OpenAI Whisper API.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    """Raised when transcription fails."""

    pass


async def _transcribe_vox(audio_path: str) -> str:
    """Transcribe audio using vox CLI subprocess."""
    proc = await asyncio.create_subprocess_exec(
        "vox",
        "file",
        audio_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TranscriptionError("Vox transcription timed out after 120 seconds")

    if proc.returncode != 0:
        raise TranscriptionError(
            f"Vox failed (exit {proc.returncode}): {stderr.decode().strip()}"
        )

    text = stdout.decode().strip()
    if not text:
        raise TranscriptionError("Vox returned empty transcription")
    return text


async def _transcribe_openai(audio_path: str) -> str:
    """Direct OpenAI Whisper API call. Used when vox is not available."""
    import openai

    client = openai.AsyncOpenAI()  # uses OPENAI_API_KEY from env
    try:
        with open(audio_path, "rb") as f:
            response = await client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=f,
            )
    except Exception as exc:
        raise TranscriptionError(f"OpenAI transcription failed: {exc}") from exc

    text = response.text.strip()
    if not text:
        raise TranscriptionError("OpenAI returned empty transcription")
    return text


async def transcribe(audio_path: str, backend: str = "vox") -> str:
    """Transcribe an audio file to text.

    Args:
        audio_path: Path to audio file (OGG, WAV, MP3, M4A, WebM)
        backend: "vox" (default) or "openai"

    Returns:
        Transcribed text string. Never empty — raises on failure.

    Raises:
        TranscriptionError: if transcription fails for any reason.
    """
    if backend == "vox":
        try:
            return await _transcribe_vox(audio_path)
        except (FileNotFoundError, PermissionError):
            logger.warning("vox not available, falling back to openai backend")
            return await _transcribe_openai(audio_path)
    elif backend == "openai":
        return await _transcribe_openai(audio_path)
    else:
        raise ValueError(f"Unknown voice backend: {backend}")
