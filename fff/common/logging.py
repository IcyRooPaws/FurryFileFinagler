"""One logging setup for every command (replaces the ~5 per-project variants).

Each command group logs to ``logs/<group>/<YYYYmmdd_HHMMSS>.log`` plus the console.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from fff.common import paths

FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def setup_logging(group: str = "fff", level: str | int = "INFO", *, console: bool = True,
                  log_dir: str | Path | None = None, name: str | None = None) -> Path:
    """Configure the root logger. Returns the log file path."""
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    directory = Path(log_dir) if log_dir else paths.LOG_DIR / group
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = directory / (f"{stamp}_{name}.log" if name else f"{stamp}.log")

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(stream)

    return log_file
