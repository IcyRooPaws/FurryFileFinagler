"""MySQL connections for the three databases. Every FFF connection goes through here.

``connect("sorter")`` / ``connect("archive")`` / ``connect("fluffle")`` return a pooled
connection to that module's database (honouring ``databases:`` overrides, e.g. to
point everything at test_* schemas via the ``environment`` toggle).
``connect_database("some_db")`` connects to an arbitrary schema on the same server; in the
test environment a production schema name is redirected to its ``test_`` twin.
``connect_with(settings)`` connects using a module's settings dict (the ``mysql:`` /
``database:`` blocks of the legacy config views, or tag_sorting.yaml's ``e621_database``),
whose host/port/user/password may override config.yaml's ``database:`` block.
``connect_server()`` connects without selecting a schema (CREATE DATABASE, schema copies).

tests/test_db_layer.py fails if a module outside this file opens its own connection or pool.
"""
from __future__ import annotations

import logging
import threading

import mysql.connector
from mysql.connector import pooling

from fff.common import config as fff_config

logger = logging.getLogger(__name__)
_pools: dict[tuple, pooling.MySQLConnectionPool] = {}
_pools_lock = threading.Lock()
POOL_SIZE = 5
SERVER_KEYS = ("host", "port", "user", "password")


def _params(database: str, cfg: dict | None, extra: dict) -> dict:
    cfg = cfg if cfg is not None else fff_config.load()
    target = fff_config.redirect_database(cfg, database)
    if target != database:
        # Safety net for the test environment: never let a hard-coded prod name reach prod.
        logger.warning("Test environment: connecting to %s instead of %s", target, database)
    return fff_config.db_params(cfg, target) | extra


def get_pool(database: str, cfg: dict | None = None, *, pool_size: int = POOL_SIZE,
             **extra) -> pooling.MySQLConnectionPool:
    """The shared pool for these connection settings, created on first use.

    Callers asking for different settings (e.g. ``autocommit=True``) get different pools.
    """
    params = _params(database, cfg, extra)
    key = (pool_size, tuple(sorted((k, repr(v)) for k, v in params.items())))
    with _pools_lock:
        pool = _pools.get(key)
        if pool is None:
            pool = pooling.MySQLConnectionPool(
                pool_name=f"fff_{params['database']}"[:60], pool_size=pool_size,
                pool_reset_session=True, **params,
            )
            _pools[key] = pool
    return pool


def connect_database(database: str, cfg: dict | None = None, *, pooled: bool = True,
                     pool_size: int = POOL_SIZE, **extra):
    if pooled:
        return get_pool(database, cfg, pool_size=pool_size, **extra).get_connection()
    return mysql.connector.connect(**_params(database, cfg, extra))


def connect(module: str, cfg: dict | None = None, **kwargs):
    cfg = cfg if cfg is not None else fff_config.load()
    return connect_database(fff_config.database_name(cfg, module), cfg, **kwargs)


def server_overrides(settings: dict | None) -> dict:
    """The host/port/user/password a settings dict sets; empty values fall back to config.yaml."""
    settings = settings or {}
    overrides = {k: settings[k] for k in SERVER_KEYS if settings.get(k) not in (None, "")}
    if "port" in overrides:
        overrides["port"] = int(overrides["port"])
    return overrides


def connect_with(settings: dict | None, *, database: str | None = None, cfg: dict | None = None,
                 pooled: bool = False, **extra):
    """Connect using a module's settings dict; ``database`` overrides its ``database`` key.

    Unpooled by default: these callers open a connection, use it, and close it.
    """
    name = database or (settings or {}).get("database")
    if not name:
        raise ValueError("no database name given")
    return connect_database(name, cfg, pooled=pooled, **(server_overrides(settings) | extra))


def try_connect_with(settings: dict | None, *, database: str | None = None, **kwargs):
    """``connect_with`` for print-based commands: on failure, print the error and return None."""
    try:
        return connect_with(settings, database=database, **kwargs)
    except (mysql.connector.Error, ValueError) as e:
        name = database or (settings or {}).get("database")
        print(f"Error connecting to database '{name}': {e}")
        return None


def connect_server(cfg: dict | None = None, settings: dict | None = None, **extra):
    """An unpooled connection to the server with no schema selected."""
    cfg = cfg if cfg is not None else fff_config.load()
    params = fff_config.db_params(cfg, "")
    params.pop("database")
    return mysql.connector.connect(**(params | server_overrides(settings) | extra))
