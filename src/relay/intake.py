"""Message classifier/router — decides what to do with each incoming message."""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from relay import agent
from relay.config import AgentConfig
from relay.store import Store

logger = logging.getLogger(__name__)

INTAKE_SYSTEM_PROMPT = """You are a message classifier for a Telegram-to-agent relay system.
Given a user message, classify it into one of these actions:
- "forward": The message is for the agent (default — most messages are this)
- "new_session": The user wants to start over / reset / new session / forget everything
- "status": The user wants to know session status / what's going on / how long
- "unclear": The message is gibberish, accidental, or completely unintelligible

Respond with JSON only: {"action": "forward"|"new_session"|"status"|"unclear", "cleaned_message": "..."}
The cleaned_message should be the original message, lightly cleaned up (fix obvious typos from voice, remove filler words) but preserving the user's intent. If action is not "forward", cleaned_message can be empty.

Bias heavily toward "forward" — when in doubt, forward to the agent."""


@dataclass
class IntakeResult:
    action: str  # "forward" | "new_session" | "status" | "unclear"
    cleaned_message: str  # The message to forward (may be cleaned up from original)


async def classify(message: str) -> IntakeResult:
    """Classify a user message into an action.

    Uses a lightweight Claude -p call with --output-format json.
    Fast and stateless — no session context.

    Args:
        message: The user's raw message text

    Returns:
        IntakeResult with the classification and cleaned message.
    """
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cmd = [
        "claude",
        "-p",
        f"Classify this message:\n\n{message}",
        "--output-format",
        "json",
        "--system-prompt",
        INTAKE_SYSTEM_PROMPT,
        "--model",
        "haiku",
        "--max-budget-usd",
        "0.01",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        # On timeout, default to forward (don't block the user)
        logger.warning("Intake classifier timed out")
        return IntakeResult(action="forward", cleaned_message=message)

    if proc.returncode != 0:
        # On error, default to forward
        logger.warning("Intake classifier failed: %s", stderr.decode().strip())
        return IntakeResult(action="forward", cleaned_message=message)

    data = json.loads(stdout.decode())
    result_text = data.get("result", "{}")

    # Parse the inner JSON from the result field
    try:
        classification = json.loads(result_text)
    except json.JSONDecodeError:
        # If the model didn't return valid JSON, default to forward
        logger.warning("Intake classifier returned invalid JSON: %s", result_text)
        return IntakeResult(action="forward", cleaned_message=message)

    action = classification.get("action", "forward")
    cleaned = classification.get("cleaned_message", message)

    if action not in ("forward", "new_session", "status", "unclear"):
        action = "forward"

    return IntakeResult(action=action, cleaned_message=cleaned or message)


async def handle_message(
    agent_name: str,
    message: str,
    chat_id: int,
    store: Store,
    agent_config: AgentConfig,
    on_classify: Callable[[IntakeResult], Awaitable[None]] | None = None,
) -> str:
    """Full intake pipeline: classify -> route -> return response text.

    This is the main entry point called by telegram.py for every text message.

    1. Classify the message
    2. Fire on_classify callback (if provided) so caller can send ack
    3. If "forward" -> call agent.send_message(), return agent's response
    4. If "new_session" -> call agent.reset_session(), return confirmation
    5. If "status" -> call agent.get_session_info(), return info
    6. If "unclear" -> return a brief "I didn't understand" message

    Returns:
        The response text to send back to the user.
    """
    result = await classify(message)

    if on_classify:
        await on_classify(result)

    if result.action == "forward":
        response = await agent.send_message(
            agent_name, result.cleaned_message, chat_id, store, agent_config
        )
        return response.text

    elif result.action == "new_session":
        return await agent.reset_session(agent_name, chat_id, store)

    elif result.action == "status":
        return await agent.get_session_info(agent_name, chat_id, store)

    elif result.action == "unclear":
        return "I didn't quite catch that. Could you rephrase?"

    # Fallback (shouldn't reach here)
    response = await agent.send_message(
        agent_name, message, chat_id, store, agent_config
    )
    return response.text
