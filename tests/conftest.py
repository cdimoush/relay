"""Shared fixtures for Relay tests."""

import logging

import pytest
import pytest_asyncio

from relay.config import (
    AgentConfig,
    RelayConfig,
    StorageConfig,
    VoiceConfig,
)
from relay.store import Store

logger = logging.getLogger(__name__)


@pytest_asyncio.fixture
async def store(tmp_path):
    """Provide an initialized Store with a temp database."""
    db_path = str(tmp_path / "test.db")
    s = Store(db_path)
    await s.initialize()
    yield s
    await s.close()


@pytest.fixture
def sample_agent_config(tmp_path):
    """Return a minimal AgentConfig pointing at a real temp directory."""
    return AgentConfig(
        name="test-agent",
        bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        allowed_users=[111111, 222222],
        project_dir=str(tmp_path),
        allowed_tools=["Read", "Write", "Bash"],
        model="sonnet",
        timeout=30,
        session_ttl=3600,
        max_budget=0.50,
    )


@pytest.fixture
def sample_relay_config(sample_agent_config, tmp_path):
    """Return a full RelayConfig for integration tests."""
    return RelayConfig(
        agents={"test-agent": sample_agent_config},
        voice=VoiceConfig(backend="vox"),
        storage=StorageConfig(db_path=str(tmp_path / "test.db")),
    )
