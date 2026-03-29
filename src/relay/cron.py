"""Relay-managed cron scheduler — runs agent cron jobs inside the relay process.

Replaces external crontab + agent-cron.sh with asyncio tasks that inject
synthetic messages into the existing agent pipeline. Cron jobs get full
tool access, session management, and Telegram delivery for free.
"""

import asyncio
import hashlib
import logging
import os
from dataclasses import replace
from datetime import datetime, timezone

from croniter import croniter

from relay import agent
from relay.config import AgentConfig, CronConfig, RelayConfig
from relay.store import Store

logger = logging.getLogger(__name__)

# Cron sessions use a dedicated chat_id space (large negative ints)
# to avoid colliding with real Telegram user chat_ids.
_CRON_CHAT_ID_BASE = -(10**12)


def _cron_chat_id(agent_name: str, cron_name: str) -> int:
    """Generate a stable, unique negative chat_id for a cron job."""
    h = hashlib.sha256(f"{agent_name}:{cron_name}".encode()).hexdigest()
    return _CRON_CHAT_ID_BASE - (int(h[:8], 16) % 10**9)


def _read_prompt(agent_config: AgentConfig, cron_config: CronConfig) -> str | None:
    """Read the prompt file for a cron job. Returns None if not found."""
    prompt_path = os.path.join(agent_config.project_dir, cron_config.prompt_file)
    if not os.path.isfile(prompt_path):
        logger.error(
            "cron=%s/%s prompt_file not found: %s",
            agent_config.name, cron_config.name, prompt_path,
        )
        return None
    with open(prompt_path) as f:
        return f.read().strip()


async def _check_skip(
    agent_name: str, store: Store, minutes: int = 30
) -> bool:
    """Return True if the agent has an active user session (should skip)."""
    return await store.has_recent_user_sessions(agent_name, minutes=minutes)


async def _run_cron_job(
    agent_name: str,
    agent_config: AgentConfig,
    cron_config: CronConfig,
    store: Store,
    send_telegram: object,  # async callable(chat_id, text)
) -> None:
    """Execute a single cron job: read prompt, call agent, send result."""
    job_label = f"{agent_name}/{cron_config.name}"

    # Skip if user is mid-conversation
    if cron_config.skip_if_active:
        if await _check_skip(agent_name, store):
            logger.info("cron=%s SKIP: agent has active user session", job_label)
            return

    # Read prompt
    prompt = _read_prompt(agent_config, cron_config)
    if not prompt:
        return

    chat_id = _cron_chat_id(agent_name, cron_config.name)

    # Build a cron-specific agent config (may override model)
    cron_agent_config = agent_config
    if cron_config.model:
        cron_agent_config = replace(agent_config, model=cron_config.model)

    logger.info("cron=%s START (chat_id=%d)", job_label, chat_id)

    try:
        response = await agent.send_message(
            agent_name, prompt, chat_id, store, cron_agent_config
        )
    except Exception:
        logger.exception("cron=%s agent.send_message failed", job_label)
        return

    logger.info(
        "cron=%s DONE cost=$%.4f duration=%dms turns=%d len=%d error=%s",
        job_label,
        response.cost_usd,
        response.duration_ms,
        response.num_turns,
        len(response.text),
        response.is_error,
    )

    # Send to Telegram if notify is enabled and there's output
    if cron_config.notify and response.text and not response.is_error:
        notify_chat_id = cron_config.notify_chat_id or agent_config.allowed_users[0]
        try:
            await send_telegram(notify_chat_id, response.text)
            logger.info("cron=%s notified chat_id=%d", job_label, notify_chat_id)
        except Exception:
            logger.exception("cron=%s Telegram send failed", job_label)


async def _cron_loop(
    agent_name: str,
    agent_config: AgentConfig,
    cron_config: CronConfig,
    store: Store,
    send_telegram: object,
) -> None:
    """Infinite loop: sleep until next cron trigger, then run the job."""
    job_label = f"{agent_name}/{cron_config.name}"
    logger.info(
        "cron=%s scheduled: '%s'", job_label, cron_config.schedule
    )

    while True:
        try:
            now = datetime.now(timezone.utc)
            cron = croniter(cron_config.schedule, now)
            next_run = cron.get_next(datetime)
            delay = (next_run - now).total_seconds()

            logger.info(
                "cron=%s next run at %s (in %.0fs)",
                job_label, next_run.isoformat(), delay,
            )

            await asyncio.sleep(delay)

            await _run_cron_job(
                agent_name, agent_config, cron_config, store, send_telegram
            )

        except asyncio.CancelledError:
            logger.info("cron=%s cancelled", job_label)
            raise
        except Exception:
            logger.exception("cron=%s loop error, retrying in 60s", job_label)
            await asyncio.sleep(60)


async def start_scheduler(
    config: RelayConfig,
    store: Store,
    bots: dict[str, object],  # agent_name -> Application
) -> list[asyncio.Task]:
    """Start cron loops for all configured cron jobs.

    Args:
        config: Relay config with agents and their cron definitions
        store: Initialized Store instance
        bots: Map of agent_name -> telegram Application (for sending messages)

    Returns:
        List of asyncio Tasks (caller should cancel on shutdown).
    """
    tasks: list[asyncio.Task] = []

    for agent_name, agent_config in config.agents.items():
        if not agent_config.crons:
            continue

        bot_app = bots.get(agent_name)
        if not bot_app:
            logger.warning(
                "cron: agent '%s' has crons but no bot — skipping", agent_name
            )
            continue

        async def _send_telegram(chat_id: int, text: str, _bot=bot_app.bot) -> None:
            """Send a message via the agent's Telegram bot, chunking if needed."""
            max_len = 4096
            for i in range(0, len(text), max_len):
                await _bot.send_message(chat_id=chat_id, text=text[i:i + max_len])

        for cron_config in agent_config.crons:
            task = asyncio.create_task(
                _cron_loop(
                    agent_name, agent_config, cron_config, store, _send_telegram
                ),
                name=f"cron:{agent_name}/{cron_config.name}",
            )
            tasks.append(task)

    if tasks:
        logger.info("Started %d cron job(s)", len(tasks))
    else:
        logger.info("No cron jobs configured")

    return tasks
