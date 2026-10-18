"""Logging setup.

The daemon writes to stdout so systemd and journald capture the output. Each
job also gets its own log file under the logs directory.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

LOGGER_NAME = "mkv-transcode"
FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup(level: str = "info", stream=None) -> logging.Logger:
    """Configure the root logger once and return the package logger."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if getattr(logger, "_mkvtc_configured", False):
        return logger
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    logger._mkvtc_configured = True  # type: ignore[attr-defined]
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return the package logger, or a child logger for a module."""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger("%s.%s" % (LOGGER_NAME, name))


class FileLog:
    """A per-job log file that also forwards records to the console logger."""

    def __init__(self, path: str, logger: Optional[logging.Logger] = None):
        self.path = path
        self.logger = logger or get_logger()
        self.level = getattr(logging, "INFO")
        self._handle = None

    def __enter__(self) -> "FileLog":
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._handle = open(self.path, "a", buffering=1, encoding="utf-8")
        self.write("---- log started ----")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle:
            self.write("---- log closed ----")
            self._handle.close()
            self._handle = None

    def write(self, message: str) -> None:
        """Append a line to the file."""
        if self._handle is None:
            return
        self._handle.write(message.rstrip("\n") + "\n")

    def log(self, message: str, level: int = logging.INFO) -> None:
        """Write to the file and to the console logger."""
        self.write(message)
        self.logger.log(level, message)
