"""Keep two copies of a long-running command from overlapping.

The lock is an OS file lock on ``data/locks/<name>.lock`` (``data/test/locks`` in the test
environment), so it is released when the process exits, however it exits: a crashed or
killed run never leaves a stale lock behind.
"""
from __future__ import annotations

import functools
import os
from contextlib import contextmanager

from fff.common import paths

if os.name == "nt":
    import msvcrt

    def _try_lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
else:
    import fcntl

    def _try_lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def single_instance(name: str):
    """Hold the ``name`` lock for the block; raise :class:`AlreadyRunning` if another process has it."""
    directory = paths.for_env(paths.LOCK_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / f"{name}.lock", "a+")
    try:
        try:
            _try_lock(handle)
        except OSError:
            raise AlreadyRunning(f"another 'fff {name.replace('_', ' ')}' is already running") from None
        yield
    finally:
        handle.close()  # closing releases the lock


def exclusive_command(name: str):
    """Decorator for a command's ``main``: refuse to start (exit code 1) while another copy runs."""
    def decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                with single_instance(name):
                    return func(*args, **kwargs)
            except AlreadyRunning as exc:
                print(f"[ERROR] Not starting: {exc}. Wait for it to finish, or stop it first.")
                return 1
        return wrapper
    return decorate
