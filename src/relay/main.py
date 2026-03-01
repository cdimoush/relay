import asyncio
import logging
import signal
import sys

from relay.config import load_config
from relay.store import Store
from relay import telegram


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Entry point for Relay. Load config, init store, start bot."""
    config = load_config()
    logger.info(
        "Loaded config: agent=%s, project=%s",
        config.agent.name,
        config.agent.project_dir,
    )

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
            await telegram.start_bot(config, store)
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
