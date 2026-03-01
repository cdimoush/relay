"""Tests for relay.config — YAML loading, env var substitution, validation errors."""

import logging
import os

import pytest

from relay.config import load_config

logger = logging.getLogger(__name__)


def _write_yaml(tmp_path, content, filename="relay.yaml"):
    """Helper: write a YAML string to a file and return its path."""
    p = tmp_path / filename
    p.write_text(content)
    return str(p)


def _minimal_agents_yaml(tmp_path, **overrides):
    """Return a valid minimal YAML config string using new agents format."""
    project_dir = overrides.pop("project_dir", str(tmp_path))
    bot_token = overrides.pop("bot_token", "123456:TESTTOKEN")
    return f"""\
agents:
  test:
    bot_token: "{bot_token}"
    allowed_users:
      - 111111
    project_dir: "{project_dir}"
    allowed_tools:
      - "Read"
      - "Write"

voice:
  backend: "vox"

storage:
  db_path: "test.db"
"""


def _minimal_legacy_yaml(tmp_path, **overrides):
    """Return a valid minimal YAML config string using old telegram+agent format."""
    project_dir = overrides.pop("project_dir", str(tmp_path))
    bot_token = overrides.pop("bot_token", "123456:TESTTOKEN")
    return f"""\
telegram:
  bot_token: "{bot_token}"
  allowed_users:
    - 111111

agent:
  name: "test"
  project_dir: "{project_dir}"
  allowed_tools:
    - "Read"
    - "Write"

voice:
  backend: "vox"

storage:
  db_path: "test.db"
"""


class TestLoadConfigNewFormat:
    """Tests for load_config with new agents dict format."""

    def test_valid_config(self, tmp_path):
        """A valid config loads without error and returns correct values."""
        path = _write_yaml(tmp_path, _minimal_agents_yaml(tmp_path))
        cfg = load_config(path)
        assert "test" in cfg.agents
        agent = cfg.agents["test"]
        assert agent.bot_token == "123456:TESTTOKEN"
        assert agent.allowed_users == [111111]
        assert agent.name == "test"
        assert agent.project_dir == str(tmp_path)
        assert agent.allowed_tools == ["Read", "Write"]
        assert cfg.voice.backend == "vox"

    def test_multiple_agents(self, tmp_path):
        """Multiple agents load correctly."""
        project_dir = str(tmp_path)
        yaml_content = f"""\
agents:
  alpha:
    bot_token: "token-alpha"
    allowed_users:
      - 111111
    project_dir: "{project_dir}"
    allowed_tools:
      - "Read"
    model: "opus"
    timeout: 600
  beta:
    bot_token: "token-beta"
    allowed_users:
      - 222222
      - 333333
    project_dir: "{project_dir}"
    allowed_tools:
      - "Read"
      - "Write"
    max_budget: 2.5
"""
        path = _write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert len(cfg.agents) == 2
        assert cfg.agents["alpha"].bot_token == "token-alpha"
        assert cfg.agents["alpha"].model == "opus"
        assert cfg.agents["alpha"].timeout == 600
        assert cfg.agents["beta"].bot_token == "token-beta"
        assert cfg.agents["beta"].allowed_users == [222222, 333333]
        assert cfg.agents["beta"].max_budget == 2.5

    def test_env_var_substitution(self, tmp_path, monkeypatch):
        """Environment variables referenced with ${VAR} are resolved."""
        monkeypatch.setenv("TEST_BOT_TOKEN", "env-resolved-token")
        yaml_content = f"""\
agents:
  test:
    bot_token: "${{TEST_BOT_TOKEN}}"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"
"""
        path = _write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.agents["test"].bot_token == "env-resolved-token"

    def test_unresolved_env_var_raises(self, tmp_path, monkeypatch):
        """An unresolved env var in bot_token raises ValueError."""
        monkeypatch.delenv("UNSET_VAR_XYZ_12345", raising=False)
        yaml_content = f"""\
agents:
  test:
    bot_token: "${{UNSET_VAR_XYZ_12345}}"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="unresolved env var"):
            load_config(path)

    def test_missing_agents_section_raises(self, tmp_path):
        """Missing agents section raises ValueError."""
        yaml_content = """\
voice:
  backend: "vox"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="agents"):
            load_config(path)

    def test_missing_bot_token_raises(self, tmp_path):
        """Missing bot_token in agent raises ValueError."""
        yaml_content = f"""\
agents:
  test:
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="bot_token"):
            load_config(path)

    def test_invalid_project_dir_raises(self, tmp_path):
        """Non-existent project_dir raises ValueError."""
        yaml_content = """\
agents:
  test:
    bot_token: "abc123"
    allowed_users:
      - 111111
    project_dir: "/nonexistent/path/xyz"
    allowed_tools:
      - "Read"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="project_dir"):
            load_config(path)

    def test_missing_allowed_users_raises(self, tmp_path):
        """Missing allowed_users in agent raises ValueError."""
        yaml_content = f"""\
agents:
  test:
    bot_token: "abc123"
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="allowed_users"):
            load_config(path)

    def test_defaults_applied(self, tmp_path):
        """Optional fields get their default values when omitted."""
        path = _write_yaml(tmp_path, _minimal_agents_yaml(tmp_path))
        cfg = load_config(path)
        agent = cfg.agents["test"]
        assert agent.model == "sonnet"
        assert agent.timeout == 900
        assert agent.session_ttl == 14400
        assert agent.max_budget == 1.0

    def test_relative_db_path_resolved(self, tmp_path):
        """A relative db_path is resolved relative to the config file directory."""
        path = _write_yaml(tmp_path, _minimal_agents_yaml(tmp_path))
        cfg = load_config(path)
        assert os.path.isabs(cfg.storage.db_path)
        assert cfg.storage.db_path.startswith(str(tmp_path))

    def test_file_not_found(self, tmp_path):
        """FileNotFoundError when config file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nonexistent.yaml"))

    def test_invalid_voice_backend_raises(self, tmp_path):
        """Unknown voice backend raises ValueError."""
        yaml_content = f"""\
agents:
  test:
    bot_token: "abc123"
    allowed_users:
      - 111111
    project_dir: "{tmp_path}"
    allowed_tools:
      - "Read"

voice:
  backend: "whisperx"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="voice.backend"):
            load_config(path)


class TestLoadConfigLegacyFormat:
    """Tests for backwards compatibility with old telegram+agent format."""

    def test_legacy_format_loads(self, tmp_path):
        """Old telegram+agent format auto-converts and loads correctly."""
        path = _write_yaml(tmp_path, _minimal_legacy_yaml(tmp_path))
        cfg = load_config(path)
        assert "test" in cfg.agents
        agent = cfg.agents["test"]
        assert agent.bot_token == "123456:TESTTOKEN"
        assert agent.allowed_users == [111111]
        assert agent.name == "test"
        assert agent.project_dir == str(tmp_path)
        assert agent.allowed_tools == ["Read", "Write"]

    def test_legacy_env_var_substitution(self, tmp_path, monkeypatch):
        """Environment variables are resolved in legacy format."""
        monkeypatch.setenv("TEST_BOT_TOKEN", "env-resolved-token")
        yaml_content = f"""\
telegram:
  bot_token: "${{TEST_BOT_TOKEN}}"
  allowed_users:
    - 111111

agent:
  name: "test"
  project_dir: "{tmp_path}"
  allowed_tools:
    - "Read"
"""
        path = _write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.agents["test"].bot_token == "env-resolved-token"

    def test_legacy_unresolved_env_var_raises(self, tmp_path, monkeypatch):
        """An unresolved env var in legacy bot_token raises ValueError."""
        monkeypatch.delenv("UNSET_VAR_XYZ_12345", raising=False)
        yaml_content = f"""\
telegram:
  bot_token: "${{UNSET_VAR_XYZ_12345}}"
  allowed_users:
    - 111111

agent:
  name: "test"
  project_dir: "{tmp_path}"
  allowed_tools:
    - "Read"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="unresolved env var"):
            load_config(path)

    def test_legacy_defaults_applied(self, tmp_path):
        """Optional fields get defaults in legacy format."""
        path = _write_yaml(tmp_path, _minimal_legacy_yaml(tmp_path))
        cfg = load_config(path)
        agent = cfg.agents["test"]
        assert agent.model == "sonnet"
        assert agent.timeout == 900
        assert agent.session_ttl == 14400
        assert agent.max_budget == 1.0

    def test_legacy_missing_telegram_raises(self, tmp_path):
        """Legacy format missing telegram section raises ValueError."""
        yaml_content = """\
agent:
  name: "test"
  project_dir: "/tmp"
  allowed_tools:
    - "Read"
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="agents"):
            load_config(path)

    def test_legacy_missing_agent_raises(self, tmp_path):
        """Legacy format missing agent section raises ValueError."""
        yaml_content = """\
telegram:
  bot_token: "abc"
  allowed_users:
    - 111111
"""
        path = _write_yaml(tmp_path, yaml_content)
        with pytest.raises(ValueError, match="agents"):
            load_config(path)
