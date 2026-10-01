"""Generate sanitized example configuration files."""
from __future__ import annotations

import copy
import re

import yaml

from fff.common import paths

SECRET_PLACEHOLDER = "CHANGE_ME"

_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
EXAMPLE_ROOTS = {
    "p:/ipl": "D:/Media",
    "p:/archive": "D:/Archive",
    "e:/work": "D:/Work",
    "c:/users/ipl": "C:/Users/you",
}
EXAMPLE_VALUES = {
    "user_agent": "YourAppName/1.0 (by your_e621_username)",
    "favorites_username": "your_e621_username",
    "host": "localhost",
    "user": "your_mysql_user",
    "blacklist": ["example_tag"],
}


def _example_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    lowered = normalized.lower()
    for root in sorted(EXAMPLE_ROOTS, key=len, reverse=True):
        if lowered == root or lowered.startswith(root + "/"):
            return EXAMPLE_ROOTS[root] + normalized[len(root):]
    parts = normalized.split("/")
    return "/".join(["D:/Example"] + parts[2:])


def make_example(obj, key: str | None = None):
    """Return a sanitized config copy with generic paths and personal values removed."""
    if key in EXAMPLE_VALUES and obj is not None:
        return copy.deepcopy(EXAMPLE_VALUES[key])
    if isinstance(obj, dict):
        return {k: make_example(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [make_example(v) for v in obj]
    if isinstance(obj, str):
        if "\n" in obj:
            return "\n".join(_example_path(line) if _DRIVE_PATH.match(line.strip()) else line
                             for line in obj.split("\n"))
        if _DRIVE_PATH.match(obj):
            return _example_path(obj)
    return obj


EXAMPLE_HEADER = ("# FurryFileFinagler example configuration. Copy to config.yaml and edit,\n"
                  "# or run 'fff setup' for guided configuration.\n"
                  "# Regenerate this file from your config with 'fff config make-example --force'.\n"
                  "# Secrets go in secrets.yaml (see secrets.example.yaml). Relative paths are\n"
                  "# relative to the project root.\n"
                  "# environment: prod | test. test makes every module use test_<name> databases\n"
                  "# (override per run with FFF_ENV=test or 'fff --env test ...').\n\n")


def write_example(cfg: dict, *, force: bool = False) -> str:
    path = paths.CONFIG_DIR / "config.example.yaml"
    if path.exists() and not force:
        return f"SKIP  {path} (exists; use --force to overwrite)"
    with open(path, "w", encoding="utf-8") as f:
        f.write(EXAMPLE_HEADER)
        yaml.safe_dump(make_example(cfg), f, sort_keys=False, allow_unicode=True, width=120)
    return f"WRITE {path}"