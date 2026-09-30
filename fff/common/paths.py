"""Project-root-relative paths.

Every path the tool writes to is resolved from the project root, never the
current working directory, so commands behave the same from any folder.
"""
from __future__ import annotations

import os
from pathlib import Path

# FFF_HOME lets tests (or a second install) point at a different root.
ROOT = Path(os.environ.get("FFF_HOME", Path(__file__).resolve().parents[2]))

CONFIG_DIR = ROOT / "config"
LISTS_DIR = CONFIG_DIR / "lists"
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = ROOT / "logs"
SQL_DIR = ROOT / "sql"

# Caches that used to live inside each previous component (design note section6).
E621_PAGE_CACHE = CACHE_DIR / "e621_pages"      # was e621 archive/temp_responses
E621_POST_CACHE = CACHE_DIR / "e621_posts"      # was reverse search responses/e621_*.json
FLUFFLE_RESPONSES = CACHE_DIR / "fluffle"       # was reverse search responses/*.json


def resolve(path: str | os.PathLike | None, base: Path = ROOT) -> str | None:
    """Return ``path`` as an absolute string, treating relative paths as relative to ``base``."""
    if path is None or path == "":
        return path
    p = Path(path)
    if not p.is_absolute():
        p = base / p
    return str(p)
