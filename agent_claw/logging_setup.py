"""Structured logging. One root config; modules get their own logger."""
from __future__ import annotations
import logging
import sys

_CONFIGURED = False


def configure(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
    # silence noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
