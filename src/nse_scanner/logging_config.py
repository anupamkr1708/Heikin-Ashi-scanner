"""Structured logging setup.

Every failure logged anywhere in this package should include, where applicable: timestamp,
run_id, security, stage, provider, error_type, message (PART 76). This module gives a single
place to configure that format; callers get a logger via ``get_logger(__name__)``.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configure_logging(level: int = logging.INFO, logfile: str | None = None) -> None:
    """Idempotent: safe to call multiple times (e.g. once per script + once per test)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if logfile:
        handlers.append(logging.FileHandler(logfile))
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def log_failure(
    logger: logging.Logger, *, run_id: str, security: str, stage: str, provider: str, exc: Exception
) -> None:
    """Standard one-line structured failure log used by the ingestion/scan pipelines.

    Never logs full OHLCV payloads (PART 76) — only identifiers and the exception summary.
    """
    logger.warning(
        "run_id=%s security=%s stage=%s provider=%s error_type=%s message=%s",
        run_id,
        security,
        stage,
        provider,
        type(exc).__name__,
        str(exc),
    )
