"""One logging setup for every command (replaces the ~5 per-project variants).

Each command group logs to ``logs/<group>/<YYYYmmdd_HHMMSS>.log`` plus the console.
"""
from __future__ import annotations

import functools
import logging
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fff.common import paths

FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


class PrintToLog:
    """``sys.stdout`` stand-in for print-based scripts: the text still reaches the console
    unchanged, and each non-blank line is also logged (``[ERROR]``/``[WARNING]`` lines at
    those levels). The records carry ``from_print`` so the console handler skips them."""

    def __init__(self, stream, logger: logging.Logger):
        self._stream = stream
        self._logger = logger
        self._buffer = ""

    def write(self, text: str) -> int:
        self._stream.write(text)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._log(line)
        return len(text)

    def flush(self) -> None:
        self._stream.flush()

    def close_buffer(self) -> None:
        """Log a trailing line that never got its newline."""
        self._log(self._buffer)
        self._buffer = ""

    def _log(self, line: str) -> None:
        line = line.rstrip()
        if not line.strip():
            return
        lowered = line.lower()
        if "[error]" in lowered or lowered.lstrip().startswith("error"):
            level = logging.ERROR
        elif "[warning]" in lowered or lowered.lstrip().startswith("warning"):
            level = logging.WARNING
        else:
            level = logging.INFO
        self._logger.log(level, line, extra={"from_print": True})

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextmanager
def capture_print(logger: logging.Logger):
    """Log everything ``print()`` writes while the block runs (see :class:`PrintToLog`)."""
    original = sys.stdout
    tee = PrintToLog(original, logger)
    sys.stdout = tee
    try:
        yield tee
    finally:
        tee.close_buffer()
        sys.stdout = original


def logged_command(group: str, name: str, logger_name: str):
    """Decorator for a print-based command's ``main``: logs the run to ``logs/<group>/<stamp>_<name>.log``,
    including everything it prints and the traceback if it crashes."""
    def decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            log_file = setup_logging(group, name=name)
            logger = logging.getLogger(logger_name)
            logger.info("Logging initialized. Log file: %s", log_file)
            with capture_print(logger):
                try:
                    result = func(*args, **kwargs)
                except Exception:
                    logger.exception("%s failed", name)
                    raise
            logger.info("%s finished", name)
            return result
        return wrapper
    return decorate


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
        stream.addFilter(lambda record: not getattr(record, "from_print", False))  # already on the console
        root.addHandler(stream)

    return log_file
