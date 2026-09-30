"""MySQL connections for the three databases.

``connect("sorter")`` / ``connect("archive")`` / ``connect("fluffle")`` return a pooled
connection to that module's database (honouring ``databases:`` overrides, e.g. to
point everything at test_* schemas via the ``environment`` toggle).
``connect_database("some_db")`` connects to an arbitrary schema on the same server; in the
test environment a production schema name is redirected to its ``test_`` twin.
"""
from __future__ import annotations

import logging

import mysql.connector
from mysql.connector import pooling

from fff.common import config as fff_config

logger = logging.getLogger(__name__)
_pools: dict[str, pooling.MySQLConnectionPool] = {}
POOL_SIZE = 5


def connect_database(database: str, cfg: dict | None = None, *, pooled: bool = True, **extra):
    cfg = cfg if cfg is not None else fff_config.load()
    target = fff_config.redirect_database(cfg, database)
    if target != database:
        # Safety net for the test environment: never let a hard-coded prod name reach prod.
        logger.warning("Test environment: connecting to %s instead of %s", target, database)
        database = target
    params = fff_config.db_params(cfg, database) | extra
    if not pooled:
        return mysql.connector.connect(**params)
    key = f"{params['host']}:{params['port']}/{database}"
    if key not in _pools:
        _pools[key] = pooling.MySQLConnectionPool(
            pool_name=f"fff_{database}"[:60], pool_size=POOL_SIZE, pool_reset_session=True, **params
        )
    return _pools[key].get_connection()


def connect(module: str, cfg: dict | None = None, **kwargs):
    cfg = cfg if cfg is not None else fff_config.load()
    return connect_database(fff_config.database_name(cfg, module), cfg, **kwargs)
