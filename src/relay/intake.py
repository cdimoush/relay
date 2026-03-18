"""Message classifier/router — decides what to do with each incoming message."""

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from relay import agent
from relay.config import AgentConfig
from relay.store import Store

logger = logging.getLogger(__name__)

INTAKE_SYSTEM_PROMPT = """You are the intake classifier for a Telegram-to-agent relay. Your ONLY job: read the user's message and return a JSON action.

## Actions

"forward" — Message is meant for the AI agent. This is the default. Use this for ANY question, request, instruction, code discussion, follow-up, or anything you're unsure about.

"new_session" — User wants to reset/start fresh. Examples:
- "new session", "start over", "fresh start", "reset"
- "forget everything", "clear history", "clean slate"
- "new conversation", "let's start again", "wipe it"

"status" — User wants to know what's happening with their session. Examples:
- "status", "what's going on", "are you there"
- "how long have you been running", "session info"
- "what are you working on", "still alive?"

"kill_sessions" — User wants to force-kill active sessions. Examples:
- "kill sessions", "kill it", "stop everything"
- "clear sessions", "abort", "cancel all"
- "nuke it", "shut it down", "force stop"

"unclear" — Message is completely unintelligible (keyboard mash, empty, accidental send). Almost nothing qualifies — if there's any plausible intent, classify as "forward".

## Rules

1. When in doubt, ALWAYS choose "forward". False forwards are harmless. False classifications break the user's flow.
2. A message can contain an action keyword AND agent content. If someone says "hey start a new session and help me with X", that's "new_session" — the "help me with X" will start the new session.
3. Ignore content after the first sentence for classification purposes. The intent is always at the top.
4. Voice transcription artifacts (uh, um, like, you know) are normal. Look past them.
5. Typos are normal. "stauts" = status, "knew session" = new session.

## Output format

Respond with JSON only:
{"action": "forward"|"new_session"|"status"|"kill_sessions"|"unclear", "cleaned_message": "..."}

cleaned_message: the original message with obvious typos fixed and filler removed, preserving intent. If action is not "forward", cleaned_message can be empty string.

## Examples

User: "what's the weather like" → {"action": "forward", "cleaned_message": "what's the weather like"}
User: "uh yeah start over please" → {"action": "new_session", "cleaned_message": ""}
User: "status" → {"action": "status", "cleaned_message": ""}
User: "kill sessions" → {"action": "kill_sessions", "cleaned_message": ""}
User: "asdfkjh" → {"action": "unclear", "cleaned_message": ""}
User: "can you check on the deployment and also what's the session status" → {"action": "forward", "cleaned_message": "can you check on the deployment and also what's the session status"}"""


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
        f"Classify this message:\n\n{message[:300]}",
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

    # Strip markdown code fences (Haiku sometimes wraps JSON in ```json ... ```)
    result_text = re.sub(r"^```(?:json)?\s*\n?", "", result_text.strip())
    result_text = re.sub(r"\n?```\s*$", "", result_text.strip())

    # Parse the inner JSON from the result field
    try:
        classification = json.loads(result_text)
    except json.JSONDecodeError:
        # If the model didn't return valid JSON, default to forward
        logger.warning("Intake classifier returned invalid JSON: %s", result_text)
        return IntakeResult(action="forward", cleaned_message=message)

    action = classification.get("action", "forward")
    cleaned = classification.get("cleaned_message", message)

    if action not in ("forward", "new_session", "status", "kill_sessions", "unclear"):
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

    logger.info(
        "event=intake_classified action=%s input_preview=%s",
        result.action,
        message[:80],
    )

    if on_classify:
        await on_classify(result)

    if result.action == "forward":
        # Always forward the original message — the classifier only sees the
        # first 300 chars so cleaned_message may be truncated for long inputs.
        response = await agent.send_message(
            agent_name, message, chat_id, store, agent_config
        )
        return response.text

    elif result.action == "new_session":
        return await agent.reset_session(agent_name, chat_id, store)

    elif result.action == "status":
        return await agent.get_session_info(agent_name, chat_id, store)

    elif result.action == "kill_sessions":
        return await agent.kill_all_sessions(agent_name, chat_id, store)

    elif result.action == "unclear":
        return "I didn't quite catch that. Could you rephrase?"

    # Fallback (shouldn't reach here)
    response = await agent.send_message(
        agent_name, message, chat_id, store, agent_config
    )
    return response.text
