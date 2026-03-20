"""Claude Code subprocess management — session lifecycle, response parsing."""

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone

from relay.config import AgentConfig
from relay.store import Store

logger = logging.getLogger(__name__)

CHAT_SYSTEM_PROMPTS = {
    "telegram": (
        "You are responding in a Telegram chat. Keep replies concise and conversational. "
        "Avoid markdown headers (# ## ###), horizontal rules, and excessive formatting. "
        "Use short paragraphs. Bold and inline code are fine sparingly. "
        "Skip preamble — get to the point."
    ),
    "discord": (
        "You are responding in a Discord channel. Keep replies concise and conversational. "
        "Use Discord markdown (bold, inline code, code blocks). Keep messages under 1800 chars "
        "when possible to avoid chunking. Skip preamble — get to the point."
    ),
}


@dataclass
class AgentResponse:
    text: str  # The agent's response text (from JSON "result" field)
    session_id: str | None  # Claude's session_id (for --resume)
    is_error: bool  # True if the response represents an error
    cost_usd: float  # total_cost_usd from JSON
    duration_ms: int  # duration_ms from JSON
    num_turns: int  # num_turns from JSON


async def _run_claude(
    message: str,
    claude_session_id: str | None,
    agent_config: AgentConfig,
    platform: str = "telegram",
) -> AgentResponse:
    """Low-level: spawn claude subprocess, parse output, return response."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # CRITICAL: prevent nested session error

    cmd = ["claude", "-p", message, "--output-format", "json"]

    if claude_session_id:
        cmd.extend(["--resume", claude_session_id])

    # Tool permissions
    cmd.append("--allowedTools")
    cmd.extend(agent_config.allowed_tools)

    # Model
    cmd.extend(["--model", agent_config.model])

    # Budget safety net
    cmd.extend(["--max-budget-usd", str(agent_config.max_budget)])

    # Chat formatting guidance (platform-specific)
    system_prompt = CHAT_SYSTEM_PROMPTS.get(platform, CHAT_SYSTEM_PROMPTS["telegram"])
    cmd.extend(["--append-system-prompt", system_prompt])

    # Skip interactive permission prompts
    cmd.append("--dangerously-skip-permissions")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_config.project_dir,  # Claude reads CLAUDE.md from here
        env=env,
        start_new_session=True,  # own process group for killpg
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=agent_config.timeout,
        )
    except asyncio.TimeoutError:
        try:
            # proc.pid == pgid because start_new_session=True makes the child the group leader
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        await proc.wait()
        logger.warning(
            "event=agent_timeout timeout_s=%d session_id=%s",
            agent_config.timeout,
            claude_session_id,
        )
        return AgentResponse(
            text=f"The agent timed out after {agent_config.timeout // 60} minutes.",
            session_id=claude_session_id,
            is_error=True,
            cost_usd=0.0,
            duration_ms=agent_config.timeout * 1000,
            num_turns=0,
        )

    if proc.returncode != 0:
        error_text = stderr.decode().strip()
        # Handle expired/missing session: retry without --resume
        if "No conversation found" in error_text and claude_session_id:
            logger.warning(
                "Session %s expired in Claude, starting fresh", claude_session_id
            )
            return await _run_claude(
                message, claude_session_id=None, agent_config=agent_config, platform=platform
            )
        logger.error(
            "event=agent_error returncode=%d stderr=%s",
            proc.returncode,
            (error_text or "empty")[:500],
        )
        return AgentResponse(
            text=f"Agent error: {error_text or 'unknown error'}",
            session_id=claude_session_id,
            is_error=True,
            cost_usd=0.0,
            duration_ms=0,
            num_turns=0,
        )

    # Parse JSON from stdout
    try:
        data = json.loads(stdout.decode())
    except json.JSONDecodeError:
        raw_preview = stdout.decode()[:500]
        logger.error("Failed to parse JSON from Claude stdout: %s", raw_preview)
        return AgentResponse(
            text=f"Agent error: failed to parse response. Raw output: {raw_preview}",
            session_id=claude_session_id,
            is_error=True,
            cost_usd=0.0,
            duration_ms=0,
            num_turns=0,
        )

    result_text = data.get("result", "")
    cost = data.get("total_cost_usd", 0.0)
    duration = data.get("duration_ms", 0)
    turns = data.get("num_turns", 0)
    stop = data.get("stop_reason", "unknown")

    # Detect budget exhaustion: empty response + stopped mid-tool-use
    if not result_text and stop == "tool_use":
        logger.warning(
            "event=agent_budget_exhausted cost_usd=%.4f num_turns=%d",
            cost, turns,
        )
        result_text = (
            f"Session hit its budget limit (${cost:.2f} spent). "
            "The work may be complete — send a follow-up message to check."
        )

    logger.info(
        "event=agent_complete cost_usd=%.4f duration_ms=%d num_turns=%d stop_reason=%s response_len=%d is_error=%s",
        cost, duration, turns, stop, len(result_text), data.get("is_error", False),
    )

    return AgentResponse(
        text=result_text,
        session_id=data.get("session_id"),
        is_error=data.get("is_error", False),
        cost_usd=cost,
        duration_ms=duration,
        num_turns=turns,
    )


async def send_message(
    agent_name: str,
    message: str,
    chat_id: int,
    store: Store,
    agent_config: AgentConfig,
    platform: str = "telegram",
) -> AgentResponse:
    """Send a message to the Claude agent and return the response.

    Handles the full session lifecycle:
    1. Look up active session for chat_id in store
    2. Check session_ttl — expire if stale, create new if needed
    3. Spawn claude subprocess with --resume if session exists
    4. Parse JSON response
    5. Store claude_session_id if this was the first call
    6. Log user message and assistant response to store
    7. Return AgentResponse
    """
    # 1. Look up active session
    session = await store.get_active_session(chat_id, agent_name=agent_name, platform=platform)

    # 2. Check TTL / create session
    if session:
        last_active = datetime.fromisoformat(session.last_active_at).replace(
            tzinfo=timezone.utc
        )
        now = datetime.now(timezone.utc)
        age_seconds = (now - last_active).total_seconds()

        if age_seconds > agent_config.session_ttl:
            logger.info(
                "agent=%s session %s expired (age=%.0fs, ttl=%ds)",
                agent_name,
                session.id,
                age_seconds,
                agent_config.session_ttl,
            )
            await store.expire_session(session.id)
            session = await store.create_session(chat_id, agent_name=agent_name, platform=platform)
        else:
            await store.touch_session(session.id)
    else:
        session = await store.create_session(chat_id, agent_name=agent_name, platform=platform)

    # 4. Log user message
    await store.add_message(session.id, "user", message)

    # 5. Call Claude
    response = await _run_claude(message, session.claude_session_id, agent_config, platform=platform)

    # 6. Store claude_session_id if first call
    if not session.claude_session_id and response.session_id:
        await store.update_session_claude_id(session.id, response.session_id)

    # 7. Log assistant message
    await store.add_message(session.id, "assistant", response.text)

    logger.info(
        "agent=%s response: cost=$%.4f, duration=%dms, turns=%d, error=%s",
        agent_name,
        response.cost_usd,
        response.duration_ms,
        response.num_turns,
        response.is_error,
    )

    return response


async def reset_session(agent_name: str, chat_id: int, store: Store, platform: str = "telegram") -> str:
    """Close the current session for chat_id and return a confirmation message."""
    logger.info("agent=%s resetting session for chat_id=%d", agent_name, chat_id)
    session = await store.get_active_session(chat_id, agent_name=agent_name, platform=platform)
    if not session:
        return "No active session to reset."

    await store.close_session(session.id)
    return "Session closed. Starting fresh next message."


async def kill_all_sessions(agent_name: str, chat_id: int, store: Store, platform: str = "telegram") -> str:
    """Close all active sessions for chat_id and return a confirmation message."""
    logger.info("agent=%s killing all sessions for chat_id=%d", agent_name, chat_id)
    session = await store.get_active_session(chat_id, agent_name=agent_name, platform=platform)
    if not session:
        return "No active sessions to kill."

    count = 0
    while session:
        await store.close_session(session.id)
        count += 1
        session = await store.get_active_session(chat_id, agent_name=agent_name, platform=platform)

    return f"Killed {count} session(s). All clear."


async def get_session_info(agent_name: str, chat_id: int, store: Store, platform: str = "telegram") -> str:
    """Return human-readable session info for the given chat_id."""
    logger.info("agent=%s getting session info for chat_id=%d", agent_name, chat_id)
    session = await store.get_active_session(chat_id, agent_name=agent_name, platform=platform)
    if not session:
        return "No active session."

    created = datetime.fromisoformat(session.created_at).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age = now - created
    total_minutes = int(age.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60

    if hours > 0:
        age_str = f"{hours}h {minutes}m"
    else:
        age_str = f"{minutes}m"

    msg_count = await store.count_messages(session.id)
    return f"Active session: {age_str} old, {msg_count} messages"
