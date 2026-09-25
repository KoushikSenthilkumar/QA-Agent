"""
Logging setup for QA Agent.

Uses Python's standard logging module.
Rich handler is used for human-readable terminal output.
A plain file handler is used for persistent log storage.
"""

from __future__ import annotations

import logging
from pathlib import Path

from rich.logging import RichHandler


_loggers: dict[str, logging.Logger] = {}


def setup_logging(
    log_level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """
    Configure root logging for the QA Agent.

    Should be called once at startup from the CLI entry point.

    Args:
        log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL
        log_file: Optional path to write logs to (in addition to console)
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [
        RichHandler(
            rich_tracebacks=True,
            markup=True,
            show_path=False,
            show_time=True,
        )
    ]

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
        force=True,
    )

    # Reduce noise from third-party libraries
    for noisy in ("asyncio", "playwright", "urllib3", "httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for the given module name.

    Usage:
        logger = get_logger(__name__)
        logger.info("Starting QA run")
    """
    if name not in _loggers:
        _loggers[name] = logging.getLogger(name)
    return _loggers[name]
