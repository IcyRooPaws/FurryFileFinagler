"""Unified configuration.

``config/config.yaml`` holds all settings with no secrets; ``config/secrets.yaml``
(gitignored) holds the DB password and API keys. Both are deep-merged at load.

Ported modules don't read the unified layout directly. They call
``legacy_view(<module>)``, which rebuilds the exact dict shape their original
project's config.yaml had, so their key lookups work unchanged (design note section4).
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from fff.common import paths
from fff.common.fileops import normalize_file_path

MODULES = ("fluffle", "archive", "sorter")

# Which database each module owns. The name used by ``fff.common.db.connect``.
DB_KEYS = {"fluffle": "image_search", "archive": "e621_posts_db", "sorter": "image_sorting_hashes"}


class ConfigError(RuntimeError):
    pass


def deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict: ``base`` with ``override`` merged in (dicts recursively, other values replaced)."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _read_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_cache: dict | None = None


def load(config_path: str | Path | None = None, secrets_path: str | Path | None = None,
         *, reload: bool = False) -> dict:
    """Load and cache config.yaml merged with secrets.yaml."""
    global _cache
    if _cache is not None and not reload and config_path is None and secrets_path is None:
        return _cache

    config_path = Path(config_path) if config_path else paths.CONFIG_DIR / "config.yaml"
    secrets_path = Path(secrets_path) if secrets_path else config_path.with_name("secrets.yaml")
    if not config_path.exists():
        raise ConfigError(
            f"{config_path} not found. Run 'fff setup' to build it from the original "
            f"projects, or copy config/config.example.yaml."
        )
    cfg = _read_yaml(config_path)
    if secrets_path.exists():
        cfg = deep_merge(cfg, _read_yaml(secrets_path))
    _cache = cfg
    return cfg


def set_active(cfg: dict) -> None:
    """Install an already-built config dict (used by tests and --config)."""
    global _cache
    _cache = cfg


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

def db_params(cfg: dict, database: str) -> dict:
    """Connection kwargs for one of the three databases (or a test_* variant)."""
    db = cfg.get("database", {})
    return {
        "host": db.get("host"),
        "port": int(db.get("port", 3306)),
        "user": db.get("user"),
        "password": db.get("password"),
        "database": database,
    }


# ---------------------------------------------------------------------------
# Environment toggle: prod or test databases
# ---------------------------------------------------------------------------
# ``environment: test`` (or FFF_ENV=test, or ``fff --env test``) makes every module use
# test_<name> instead of <name>. Precedence: --env > FFF_ENV > config.yaml > "prod".

ENVIRONMENTS = ("prod", "test")
TEST_PREFIX = "test_"
_env_override: str | None = None


def set_environment(env: str | None) -> None:
    """Force the environment for this process (the ``--env`` flag). None clears the override."""
    global _env_override
    if env is not None and env not in ENVIRONMENTS:
        raise ConfigError(f"environment must be one of {ENVIRONMENTS}, not {env!r}")
    _env_override = env


def environment(cfg: dict) -> str:
    env = _env_override or os.environ.get("FFF_ENV") or cfg.get("environment") or "prod"
    env = str(env).strip().lower()
    if env not in ENVIRONMENTS:
        raise ConfigError(f"environment must be one of {ENVIRONMENTS}, not {env!r}")
    return env


def is_test(cfg: dict) -> bool:
    return environment(cfg) == "test"


def prod_database_name(cfg: dict, module: str) -> str:
    """The production schema name for a module (``databases:`` in config.yaml, else the default)."""
    return cfg.get("databases", {}).get(module, DB_KEYS[module])


def database_name(cfg: dict, module: str) -> str:
    """The schema a module should use right now: ``test_<prod name>`` in the test environment."""
    name = prod_database_name(cfg, module)
    if is_test(cfg) and not name.startswith(TEST_PREFIX):
        return TEST_PREFIX + name
    return name


def database_names(cfg: dict) -> dict[str, str]:
    """module -> schema for the active environment. Ported code with cross-database SQL
    (e.g. ``image_sorting_hashes.all_files JOIN e621_posts_db.posts``) must build its
    table references from this instead of hard-coding the names."""
    return {module: database_name(cfg, module) for module in MODULES}


def redirect_database(cfg: dict, name: str) -> str:
    """In the test environment, map a production schema name to its test twin (safety net)."""
    if is_test(cfg):
        for module in MODULES:
            if name == prod_database_name(cfg, module):
                return database_name(cfg, module)
    return name


def e621_credentials(cfg: dict) -> dict:
    """The single shared e621 account (design note section11, decision 7)."""
    e = cfg.get("e621", {})
    return {"login": e.get("login"), "api_key": e.get("api_key"), "user_agent": e.get("user_agent")}


def _resolve_keys(section: dict, keys: list[str]) -> None:
    for key in keys:
        if key in section and isinstance(section[key], str):
            section[key] = paths.resolve(section[key])


# ---------------------------------------------------------------------------
# Legacy views
# ---------------------------------------------------------------------------

def legacy_view(module: str, cfg: dict | None = None) -> dict:
    """Rebuild the previous component's config dict for ``module``."""
    cfg = cfg if cfg is not None else load()
    builders = {"fluffle": _fluffle_view, "archive": _archive_view, "sorter": _sorter_view}
    if module not in builders:
        raise ValueError(f"unknown module {module!r}; expected one of {MODULES}")
    return builders[module](cfg)


def _fluffle_view(cfg: dict) -> dict:
    src = copy.deepcopy(cfg.get("fluffle", {}))
    creds = e621_credentials(cfg)
    params = db_params(cfg, database_name(cfg, "fluffle"))

    view = {
        "database": {k: params[k] for k in ("host", "user", "password", "database")} | {"port": params["port"]},
        "search": src.get("search", {}),
        "fuzzysearch": src.get("fuzzysearch", {}),
        "e621": deep_merge(src.get("e621", {}), creds),
        "furaffinity": src.get("furaffinity", {}),
    }
    fz_key = cfg.get("fuzzysearch", {}).get("api_key")
    if fz_key is not None:
        view["fuzzysearch"]["api_key"] = fz_key

    # Response caches live under data/cache now (design note section6). The e621 post cache is
    # shared with the archive; reverse search's own JSON goes to its own folder.
    view["search"].setdefault("responses_directory", str(paths.FLUFFLE_RESPONSES))
    view["e621"].setdefault("responses_directory", str(paths.E621_POST_CACHE))
    _resolve_keys(view["search"], ["responses_directory"])
    _resolve_keys(view["e621"], ["responses_directory"])
    return view


def _archive_view(cfg: dict) -> dict:
    src = copy.deepcopy(cfg.get("archive", {}))
    params = db_params(cfg, database_name(cfg, "archive"))
    mysql = {k: params[k] for k in ("host", "user", "password", "database")}
    mysql["port"] = params["port"]
    mysql.update(src.pop("mysql", {}))
    # The environment decides schema names; any name stored in the config is ignored.
    mysql["database"] = database_name(cfg, "archive")
    mysql.setdefault("image_hash_db", {})["database"] = database_name(cfg, "sorter")

    view = {
        "e621": deep_merge(src.pop("e621", {}), e621_credentials(cfg)),
        "download": src.pop("download", {}),
        "mysql": mysql,
        "db_export": src.pop("db_export", {}),
        "log": src.pop("log", {"directory": str(paths.LOG_DIR / "archive")}),
        "media_hasher": src.pop("media_hasher", {}),
    }
    view.update(src)  # anything else (e.g. perceptual_matching) passes through

    # Relative folders were relative to the e621 archive cwd; anchor them to the project.
    _resolve_keys(view["download"], ["duplicates_directory", "cache_directory"])
    _resolve_keys(view["db_export"], ["directory"])
    _resolve_keys(view["log"], ["directory"])
    _resolve_keys(view["e621"], ["api_response_folder"])
    return normalize_config_paths(view)


# Copied from e621 archive/modules/config.py; its load_config applied this to every config.
def normalize_config_paths(config):
    """
    Recursively normalize all file paths in configuration dictionary.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Configuration dictionary with normalized paths
    """
    if not isinstance(config, dict):
        return config
    
    # Keys that contain file paths
    path_keys = {
        'directory', 'folder', 'path', 'dir',
        'missing_files_directory', 'duplicates_directory', 
        'cache_directory', 'all_star_favs', 'default',
        'api_response_folder'
    }
    
    normalized = {}
    for key, value in config.items():
        # Check if key indicates this is a path
        is_path_key = any(pk in key.lower() for pk in path_keys)
        
        if isinstance(value, str) and is_path_key:
            # Normalize path strings
            normalized[key] = normalize_file_path(value)
        elif isinstance(value, list):
            # Handle lists (e.g., extra_search_dirs)
            normalized[key] = [
                normalize_file_path(item) if isinstance(item, str) else item
                for item in value
            ]
        elif isinstance(value, dict):
            # Recursively normalize nested dictionaries
            normalized[key] = normalize_config_paths(value)
        else:
            normalized[key] = value
    
    return normalized


def _sorter_view(cfg: dict) -> dict:
    view = copy.deepcopy(cfg.get("sorter", {}))
    params = db_params(cfg, database_name(cfg, "sorter"))
    mysql = view.setdefault("mysql", {})
    for key in ("host", "port", "user", "password"):
        mysql.setdefault(key, params[key])
    mysql["database"] = params["database"]  # the environment decides schema names
    if isinstance(mysql.get("image_hash_db"), dict):
        mysql["image_hash_db"]["database"] = params["database"]
    other = view.setdefault("other", {})
    other.setdefault("log_directory", str(paths.LOG_DIR / "sorter"))
    _resolve_keys(other, ["log_directory"])
    return view
