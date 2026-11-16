"""Protected directories: nothing inside them is ever moved out, renamed, deleted or overwritten.

List them under ``protected_directories:`` in config/config.yaml. Files may still be copied
out of, or moved into, a protected directory (as long as nothing there gets overwritten).

``install()`` runs when the ``fff`` package is imported. It wraps the standard file functions
(shutil.move/rmtree, os.rename/replace/remove/unlink/rmdir, Path.rename/replace/unlink/rmdir,
send2trash) so the rule holds for every code path in the toolkit, not just the ones that
remember to check. A blocked call raises ``ProtectedPathError`` (a ``PermissionError``), which
callers already treat like any other failed move.
"""
from __future__ import annotations

import functools
import logging
import os
import pathlib
import shutil
import threading

logger = logging.getLogger("fff.protected")

CONFIG_KEY = "protected_directories"


class ProtectedPathError(PermissionError):
    pass


_dirs: tuple[str, ...] | None = None
_loading = threading.local()


def _norm(path) -> str | None:
    if isinstance(path, int):  # a file descriptor, not a path
        return None
    try:
        return os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(path))))
    except (TypeError, ValueError):
        return None


def set_directories(dirs) -> None:
    """Replace the protected list (tests, or callers with an explicit config)."""
    global _dirs
    _dirs = tuple(d.rstrip("\\/") for d in (_norm(p) for p in dirs or []) if d)


def directories() -> tuple[str, ...]:
    """Normalized protected directories, read from config.yaml on first use."""
    if _dirs is None:
        if getattr(_loading, "active", False):
            return ()
        _loading.active = True
        try:
            from fff.common import config as fff_config
            set_directories(fff_config.load().get(CONFIG_KEY) or [])
        except Exception as e:  # no config.yaml (CI, fresh checkout): nothing is protected
            logger.debug(f"Could not load {CONFIG_KEY}: {e}")
            return ()
        finally:
            _loading.active = False
    return _dirs


def protecting_dir(path) -> str | None:
    """The protected directory containing ``path`` (or equal to it), else None."""
    p = _norm(path)
    if not p:
        return None
    for d in directories():
        if p == d or p.startswith(d + os.sep):
            return d
    return None


def is_protected(path) -> bool:
    return protecting_dir(path) is not None


def check(path, action: str) -> None:
    """Raise ProtectedPathError if ``path`` is inside a protected directory."""
    d = protecting_dir(path)
    if d:
        msg = f"Refused to {action} '{path}': it is inside protected directory '{d}'"
        logger.warning(msg)
        raise ProtectedPathError(msg)


def check_overwrite(dst, action: str) -> None:
    """Raise if ``dst`` is an existing file inside a protected directory."""
    if is_protected(dst) and os.path.lexists(dst) and not os.path.isdir(dst):
        check(dst, f"{action} over")


# --- process-wide guard -------------------------------------------------------------------

_installed = False


def _wrap_src_dst(func, action):
    @functools.wraps(func)
    def wrapper(src, dst, *args, **kwargs):
        check(src, action)
        check_overwrite(dst, action)
        return func(src, dst, *args, **kwargs)
    wrapper.__fff_protected__ = True
    return wrapper


def _wrap_path(func, action):
    @functools.wraps(func)
    def wrapper(path, *args, **kwargs):
        check(path, action)
        return func(path, *args, **kwargs)
    wrapper.__fff_protected__ = True
    return wrapper


def _wrap_shutil_move(func):
    @functools.wraps(func)
    def wrapper(src, dst, *args, **kwargs):
        check(src, "move")
        real_dst = dst
        if os.path.isdir(dst):
            real_dst = os.path.join(dst, os.path.basename(os.fspath(src).rstrip("\\/")))
        check_overwrite(real_dst, "move")
        return func(src, dst, *args, **kwargs)
    wrapper.__fff_protected__ = True
    return wrapper


def _wrap_send2trash(func):
    @functools.wraps(func)
    def wrapper(paths, *args, **kwargs):
        for p in paths if isinstance(paths, (list, tuple)) else [paths]:
            check(p, "send to trash")
        return func(paths, *args, **kwargs)
    wrapper.__fff_protected__ = True
    return wrapper


def _patch(owner, name, make):
    current = getattr(owner, name, None)
    if current is not None and not getattr(current, "__fff_protected__", False):
        setattr(owner, name, make(current))


def install() -> None:
    """Guard the standard file functions for this process. Safe to call more than once."""
    global _installed
    if _installed:
        return
    _installed = True

    _patch(shutil, "move", _wrap_shutil_move)
    _patch(shutil, "rmtree", lambda f: _wrap_path(f, "delete"))
    _patch(os, "rename", lambda f: _wrap_src_dst(f, "rename"))
    _patch(os, "replace", lambda f: _wrap_src_dst(f, "replace"))
    _patch(os, "renames", lambda f: _wrap_src_dst(f, "rename"))
    _patch(os, "remove", lambda f: _wrap_path(f, "delete"))
    _patch(os, "unlink", lambda f: _wrap_path(f, "delete"))
    _patch(os, "rmdir", lambda f: _wrap_path(f, "delete"))

    # Path methods call os.* through their own references on some Python versions, so wrap
    # them directly too.
    _patch(pathlib.Path, "rename", lambda f: _wrap_src_dst(f, "rename"))
    _patch(pathlib.Path, "replace", lambda f: _wrap_src_dst(f, "replace"))
    _patch(pathlib.Path, "unlink", lambda f: _wrap_path(f, "delete"))
    _patch(pathlib.Path, "rmdir", lambda f: _wrap_path(f, "delete"))

    try:
        import send2trash
    except ImportError:
        pass
    else:
        _patch(send2trash, "send2trash", _wrap_send2trash)
