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
class AgentConfig:
    name: str
    bot_token: str
    allowed_users: list[int]
    project_dir: str
    allowed_tools: list[str]
    model: str = "sonnet"
    timeout: int = 900
    session_ttl: int = 14400
    max_budget: float = 1.0


@dataclass
class VoiceConfig:
    backend: str = "vox"  # "vox" or "openai"


@dataclass
class StorageConfig:
    db_path: str = "relay.db"


@dataclass
class RelayConfig:
    agents: dict[str, AgentConfig]
    voice: VoiceConfig
    storage: StorageConfig


def _validate_agent(name: str, agent_data: dict) -> AgentConfig:
    """Validate and construct an AgentConfig from a dict.

    Raises:
        ValueError: if required fields are missing or invalid
    """
    bot_token = agent_data.get("bot_token", "")
    if not bot_token or not isinstance(bot_token, str):
        raise ValueError(
            f"agents.{name}.bot_token is required and must be a non-empty string"
        )
    if "${" in bot_token:
        raise ValueError(
            f"agents.{name}.bot_token contains unresolved env var — check your environment"
        )

    allowed_users = agent_data.get("allowed_users")
    if not allowed_users or not isinstance(allowed_users, list):
        raise ValueError(
            f"agents.{name}.allowed_users must be a non-empty list of integers"
        )
    for uid in allowed_users:
        if not isinstance(uid, int):
            raise ValueError(
                f"agents.{name}.allowed_users must contain integers, got {type(uid).__name__}: {uid}"
            )

    project_dir = agent_data.get("project_dir")
    if not project_dir or not isinstance(project_dir, str):
        raise ValueError(
            f"agents.{name}.project_dir is required and must be a non-empty string"
        )
    if not Path(project_dir).is_dir():
        raise ValueError(f"agents.{name}.project_dir '{project_dir}' does not exist")

    allowed_tools = agent_data.get("allowed_tools")
    if not allowed_tools or not isinstance(allowed_tools, list):
        raise ValueError(
            f"agents.{name}.allowed_tools must be a non-empty list of strings"
        )
    for tool in allowed_tools:
        if not isinstance(tool, str):
            raise ValueError(
                f"agents.{name}.allowed_tools must contain strings, got {type(tool).__name__}: {tool}"
            )

    return AgentConfig(
        name=name,
        bot_token=bot_token,
        allowed_users=allowed_users,
        project_dir=project_dir,
        allowed_tools=allowed_tools,
        model=agent_data.get("model", "sonnet"),
        timeout=agent_data.get("timeout", 900),
        session_ttl=agent_data.get("session_ttl", 14400),
        max_budget=agent_data.get("max_budget", 1.0),
    )


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

    # --- Agents ---
    agents_data = data.get("agents")
    if not agents_data or not isinstance(agents_data, dict):
        raise ValueError("Missing required section: agents")

    agents: dict[str, AgentConfig] = {}
    for agent_name, agent_data in agents_data.items():
        if not isinstance(agent_data, dict):
            raise ValueError(f"agents.{agent_name} must be a YAML mapping")
        agents[agent_name] = _validate_agent(agent_name, agent_data)

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

    agent_names = list(agents.keys())
    logger.info("Loaded config from %s (agents=%s)", config_path, agent_names)

    return RelayConfig(
        agents=agents,
        voice=voice,
        storage=storage,
    )
