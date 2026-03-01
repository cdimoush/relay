"""YAML configuration loader for Relay.

Reads relay.yaml, resolves environment variables, validates required fields,
and returns a typed RelayConfig dataclass.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class TelegramConfig:
    bot_token: str
    allowed_users: list[int]


@dataclass
class AgentConfig:
    name: str
    project_dir: str
    allowed_tools: list[str]
    model: str = "sonnet"
    timeout: int = 900
    session_ttl: int = 14400
    max_budget: float = 1.0
    append_system_prompt: str = ""


@dataclass
class VoiceConfig:
    backend: str = "vox"  # "vox" or "openai"


@dataclass
class StorageConfig:
    db_path: str = "relay.db"


@dataclass
class RelayConfig:
    telegram: TelegramConfig
    agent: AgentConfig
    voice: VoiceConfig
    storage: StorageConfig


def load_config(config_path: str = "relay.yaml") -> RelayConfig:
    """Load relay.yaml, substitute env vars, validate, return RelayConfig.

    Raises:
        FileNotFoundError: if config_path does not exist
        ValueError: if required fields are missing or invalid
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    raw_text = config_file.read_text()
    expanded = os.path.expandvars(raw_text)
    data = yaml.safe_load(expanded)

    if not isinstance(data, dict):
        raise ValueError("Config file must contain a YAML mapping at the top level")

    # --- Telegram ---
    tg_data = data.get("telegram")
    if not tg_data or not isinstance(tg_data, dict):
        raise ValueError("Missing required section: telegram")

    bot_token = tg_data.get("bot_token", "")
    if not bot_token or not isinstance(bot_token, str):
        raise ValueError(
            "telegram.bot_token is required and must be a non-empty string"
        )
    if "${" in bot_token:
        raise ValueError(
            "telegram.bot_token contains unresolved env var — check your environment"
        )

    allowed_users = tg_data.get("allowed_users")
    if not allowed_users or not isinstance(allowed_users, list):
        raise ValueError("telegram.allowed_users must be a non-empty list of integers")
    for uid in allowed_users:
        if not isinstance(uid, int):
            raise ValueError(
                f"telegram.allowed_users must contain integers, got {type(uid).__name__}: {uid}"
            )

    telegram = TelegramConfig(bot_token=bot_token, allowed_users=allowed_users)

    # --- Agent ---
    agent_data = data.get("agent")
    if not agent_data or not isinstance(agent_data, dict):
        raise ValueError("Missing required section: agent")

    agent_name = agent_data.get("name")
    if not agent_name or not isinstance(agent_name, str):
        raise ValueError("agent.name is required and must be a non-empty string")

    project_dir = agent_data.get("project_dir")
    if not project_dir or not isinstance(project_dir, str):
        raise ValueError("agent.project_dir is required and must be a non-empty string")
    if not Path(project_dir).is_dir():
        raise ValueError(f"agent.project_dir '{project_dir}' does not exist")

    allowed_tools = agent_data.get("allowed_tools")
    if not allowed_tools or not isinstance(allowed_tools, list):
        raise ValueError("agent.allowed_tools must be a non-empty list of strings")
    for tool in allowed_tools:
        if not isinstance(tool, str):
            raise ValueError(
                f"agent.allowed_tools must contain strings, got {type(tool).__name__}: {tool}"
            )

    agent = AgentConfig(
        name=agent_name,
        project_dir=project_dir,
        allowed_tools=allowed_tools,
        model=agent_data.get("model", "sonnet"),
        timeout=agent_data.get("timeout", 900),
        session_ttl=agent_data.get("session_ttl", 14400),
        max_budget=agent_data.get("max_budget", 1.0),
        append_system_prompt=agent_data.get("append_system_prompt", ""),
    )

    # --- Voice ---
    voice_data = data.get("voice", {})
    if not isinstance(voice_data, dict):
        voice_data = {}
    voice_backend = voice_data.get("backend", "vox")
    if voice_backend not in ("vox", "openai"):
        raise ValueError(
            f"voice.backend must be 'vox' or 'openai', got '{voice_backend}'"
        )
    voice = VoiceConfig(backend=voice_backend)

    # --- Storage ---
    storage_data = data.get("storage", {})
    if not isinstance(storage_data, dict):
        storage_data = {}
    db_path = storage_data.get("db_path", "relay.db")

    # Resolve relative db_path relative to the directory containing relay.yaml
    if not os.path.isabs(db_path):
        config_dir = config_file.resolve().parent
        db_path = str(config_dir / db_path)

    storage = StorageConfig(db_path=db_path)

    logger.info("Loaded config from %s (agent=%s)", config_path, agent.name)

    return RelayConfig(
        telegram=telegram,
        agent=agent,
        voice=voice,
        storage=storage,
    )
