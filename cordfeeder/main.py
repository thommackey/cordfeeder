"""Main entry point and structured JSON logging for CordFeeder."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import traceback
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter -- one JSON object per line."""

    _hostname = socket.gethostname()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "host": self._hostname,
            "app": "cordfeeder",
        }

        # Merge extra fields (anything the caller passed via `extra={}`)
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "created", "relativeCreated",
                "exc_info", "exc_text", "stack_info", "lineno", "funcName",
                "pathname", "filename", "module", "levelname", "levelno",
                "msecs", "thread", "threadName", "taskName",
                "process", "processName", "message",
            } and key not in payload:
                payload[key] = value

        if record.exc_info and record.exc_info[1] is not None:
            exc_type, exc_val, exc_tb = record.exc_info
            payload["err.type"] = type(exc_val).__qualname__
            payload["err.msg"] = str(exc_val)
            payload["err.stack"] = "\\n".join(
                traceback.format_exception(exc_type, exc_val, exc_tb)
            ).rstrip()

        return json.dumps(payload, default=str)


def setup_logging(level: str) -> None:
    """Configure root logger with structured JSON output to stdout."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


def main() -> None:
    """Entry point for CordFeeder."""
    from cordfeeder.bot import CordFeederBot
    from cordfeeder.config import Config
    from cordfeeder.database import Database

    config = Config.from_env()
    setup_logging(config.log_level)

    logger = logging.getLogger(__name__)
    logger.info("starting cordfeeder")

    db = Database(config.database_path)

    async def _run() -> None:
        await db.initialise()
        bot = CordFeederBot(config=config, db=db)
        async with bot:
            await bot.start(config.discord_token)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("shutting down (keyboard interrupt)")


if __name__ == "__main__":
    main()
