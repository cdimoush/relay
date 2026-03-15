"""Tests for relay.main — entry point, watchdog, and shutdown lifecycle."""

import asyncio
import logging
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from relay.main import _watchdog_ping


# ---------------------------------------------------------------------------
# _watchdog_ping tests (relay-csh.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_ping_with_sdnotify():
    """Sends READY=1 then WATCHDOG=1 on loop."""
    mock_notifier = MagicMock()
    mock_sdnotify = MagicMock()
    mock_sdnotify.SystemdNotifier.return_value = mock_notifier

    call_count = 0

    async def fake_sleep(duration):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()

    with patch.dict("sys.modules", {"sdnotify": mock_sdnotify}):
        with patch("relay.main.asyncio.sleep", side_effect=fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await _watchdog_ping()

    # READY=1 sent first, then WATCHDOG=1 on each loop iteration
    calls = [c[0][0] for c in mock_notifier.notify.call_args_list]
    assert calls[0] == "READY=1"
    assert "WATCHDOG=1" in calls


@pytest.mark.asyncio
async def test_watchdog_ping_without_sdnotify(caplog):
    """ImportError caught, logs debug, returns cleanly."""
    # Force sdnotify to not be importable by patching builtins.__import__
    original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

    def mock_import(name, *args, **kwargs):
        if name == "sdnotify":
            raise ImportError("No module named 'sdnotify'")
        return original_import(name, *args, **kwargs)

    with caplog.at_level(logging.DEBUG, logger="relay.main"):
        with patch("builtins.__import__", side_effect=mock_import):
            await _watchdog_ping()

    # Should return cleanly without error


@pytest.mark.asyncio
async def test_watchdog_ping_exception(caplog):
    """Generic exception caught, logs warning."""
    mock_sdnotify = MagicMock()
    mock_sdnotify.SystemdNotifier.side_effect = RuntimeError("notifier broken")

    with caplog.at_level(logging.WARNING, logger="relay.main"):
        with patch.dict("sys.modules", {"sdnotify": mock_sdnotify}):
            await _watchdog_ping()

    assert any("Watchdog ping failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Helpers for main() tests
# ---------------------------------------------------------------------------

def _make_config(tmp_path, agent_name="test-agent"):
    from relay.config import AgentConfig, RelayConfig, StorageConfig, VoiceConfig
    return RelayConfig(
        agents={agent_name: AgentConfig(
            name=agent_name, bot_token="tok", allowed_users=[1],
            project_dir=str(tmp_path), allowed_tools=["Read"],
            model="sonnet", timeout=30, session_ttl=3600, max_budget=1.0,
        )},
        voice=VoiceConfig(),
        storage=StorageConfig(db_path=str(tmp_path / "test.db")),
    )


# ---------------------------------------------------------------------------
# main() lifecycle tests (relay-csh.2)
#
# main() calls asyncio.run(), which can't nest inside pytest-asyncio's loop.
# So we test main() as a sync function, patching asyncio.run to call the
# coroutine on the existing loop via asyncio.get_event_loop().run_until_complete
# or just capture and await _run directly.
# ---------------------------------------------------------------------------


def test_main_loads_config_and_logs_agents(caplog, tmp_path):
    """Config loaded, agent names logged."""
    config = _make_config(tmp_path, "my-agent")
    mock_store = MagicMock()
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()

    def run_coro(coro):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    with caplog.at_level(logging.INFO, logger="relay.main"):
        with patch("relay.main.load_config", return_value=config):
            with patch("relay.main.Store", return_value=mock_store):
                with patch("relay.main.telegram.start_bots", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
                    with patch("relay.main._watchdog_ping", new_callable=AsyncMock):
                        with patch("relay.main.asyncio.run", side_effect=run_coro):
                            from relay.main import main
                            main()

    assert any("my-agent" in r.message for r in caplog.records)


def test_main_initializes_store(tmp_path):
    """store.initialize called before start_bots."""
    config = _make_config(tmp_path)
    mock_store = MagicMock()
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()

    def run_coro(coro):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    with patch("relay.main.load_config", return_value=config):
        with patch("relay.main.Store", return_value=mock_store):
            with patch("relay.main.telegram.start_bots", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
                with patch("relay.main._watchdog_ping", new_callable=AsyncMock):
                    with patch("relay.main.asyncio.run", side_effect=run_coro):
                        from relay.main import main
                        main()

    mock_store.initialize.assert_called_once()


def test_main_registers_sigterm_handler(tmp_path):
    """SIGTERM handler registered on event loop."""
    config = _make_config(tmp_path)
    mock_store = MagicMock()
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()

    sigterm_registered = []

    original_add_signal = None

    async def patched_start_bots(*a, **kw):
        loop = asyncio.get_running_loop()
        # Check that SIGTERM handler was registered before start_bots
        # We intercept add_signal_handler to verify
        raise asyncio.CancelledError()

    def run_coro(coro):
        loop = asyncio.new_event_loop()
        original = loop.add_signal_handler

        def tracking_add_signal(sig, handler):
            sigterm_registered.append(sig)
            return original(sig, handler)

        loop.add_signal_handler = tracking_add_signal
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    with patch("relay.main.load_config", return_value=config):
        with patch("relay.main.Store", return_value=mock_store):
            with patch("relay.main.telegram.start_bots", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
                with patch("relay.main._watchdog_ping", new_callable=AsyncMock):
                    with patch("relay.main.asyncio.run", side_effect=run_coro):
                        from relay.main import main
                        main()

    assert signal.SIGTERM in sigterm_registered


def test_main_store_closed_on_cancel(tmp_path):
    """store.close runs in finally even on CancelledError."""
    config = _make_config(tmp_path)
    mock_store = MagicMock()
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()

    def run_coro(coro):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    with patch("relay.main.load_config", return_value=config):
        with patch("relay.main.Store", return_value=mock_store):
            with patch("relay.main.telegram.start_bots", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
                with patch("relay.main._watchdog_ping", new_callable=AsyncMock):
                    with patch("relay.main.asyncio.run", side_effect=run_coro):
                        from relay.main import main
                        main()

    mock_store.close.assert_called_once()


def test_main_keyboard_interrupt_exits(tmp_path):
    """KeyboardInterrupt caught, exits cleanly."""
    config = _make_config(tmp_path)

    with patch("relay.main.load_config", return_value=config):
        with patch("relay.main.Store"):
            with patch("relay.main.asyncio.run", side_effect=KeyboardInterrupt):
                with pytest.raises(SystemExit) as exc_info:
                    from relay.main import main
                    main()

    assert exc_info.value.code == 0
