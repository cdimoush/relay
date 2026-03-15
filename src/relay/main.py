import asyncio
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler

from relay.config import load_config
from relay.store import Store
from relay import telegram

LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs")
LOG_FILE = os.path.join(LOG_DIR, "relay-ops.log")

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)

# File handler for relay agent access (no sudo needed)
os.makedirs(LOG_DIR, exist_ok=True)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=5)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
file_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(file_handler)

logger = logging.getLogger(__name__)


async def _watchdog_ping():
    """Ping systemd watchdog every 30 seconds. No-op if sdnotify not installed."""
    try:
        import sdnotify

        notifier = sdnotify.SystemdNotifier()
        notifier.notify("READY=1")
        logger.info("Systemd watchdog enabled, pinging every 30s")
        while True:
            notifier.notify("WATCHDOG=1")
            await asyncio.sleep(30)
    except ImportError:
        logger.debug("sdnotify not installed, watchdog disabled")
    except Exception:
        logger.warning("Watchdog ping failed", exc_info=True)


def main() -> None:
    """Entry point for Relay. Load config, init store, start bots."""
    config = load_config()

    agent_names = list(config.agents.keys())
    logger.info("Loaded config with agents: %s", agent_names)
    for name, ac in config.agents.items():
        logger.info("  agent '%s': project=%s", name, ac.project_dir)

    store = Store(config.storage.db_path)

    async def _run():
        await store.initialize()

        # Handle SIGTERM (from systemd stop) by cancelling the main task.
        # SIGINT (Ctrl+C) is already handled by asyncio.run() which raises KeyboardInterrupt.
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()

        def _sigterm_handler():
            logger.info("Received SIGTERM, shutting down gracefully...")
            main_task.cancel()

        loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)

        try:
            asyncio.create_task(_watchdog_ping())
            await telegram.start_bots(config, store)
        except asyncio.CancelledError:
            logger.info("Main task cancelled, cleaning up...")
        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)


if __name__ == "__main__":
    main()
