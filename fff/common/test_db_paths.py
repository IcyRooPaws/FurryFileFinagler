"""``fff db rewrite-test-paths``: point file paths stored in the test databases at the test tree.

After ``fff db copy-data`` the test_* databases hold prod paths (P:\\IPL\\...). A test-mode
run would then find, move or rename the real files. This rewrites every stored absolute
path in the test databases with ``fff.common.config.test_path`` (the same rule the config
views use), e.g. P:\\IPL\\x -> E:\\Testing\\P\\IPL\\x, keeping each value's slash style.

Only test_* databases are touched; production is never read or written here.
Views (all_files ...) are skipped; they reflect their base tables. Values that aren't
drive paths (e.g. directory labels) and values already under a test location are left alone.
"""
import logging
import re

from fff.common import config as fff_config
from fff.common import db

DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
TEXT_TYPES = ("varchar", "char", "text", "tinytext", "mediumtext", "longtext")


def _test_cfg(cfg):
    return fff_config.deep_merge(cfg, {"environment": "test"})


def path_columns(cur, schema):
    cur.execute("""SELECT c.table_name, c.column_name FROM information_schema.columns c
                   JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                   WHERE c.table_schema = %s AND t.table_type = 'BASE TABLE'
                   AND (c.column_name LIKE %s OR c.column_name LIKE %s) AND c.data_type IN ({})
                   ORDER BY c.table_name, c.column_name""".format(",".join(["%s"] * len(TEXT_TYPES))),
                (schema, "%path%", "%directory%", *TEXT_TYPES))
    return cur.fetchall()


def primary_key(cur, schema, table):
    cur.execute("""SELECT column_name FROM information_schema.key_column_usage
                   WHERE table_schema = %s AND table_name = %s AND constraint_name = 'PRIMARY'
                   ORDER BY ordinal_position""", (schema, table))
    return [r[0] for r in cur.fetchall()]


def rewrite(cfg, modules=None, dry_run=False, batch=5000):
    """Returns {"schema.table.column": number of values rewritten (or to rewrite)}."""
    test_cfg = _test_cfg(cfg)
    if fff_config.test_paths_root(test_cfg) is None:
        raise fff_config.ConfigError("test_paths_root is null: nothing to rewrite paths to")
    results = {}
    for module in modules or fff_config.MODULES:
        schema = fff_config.database_name(test_cfg, module)
        if not schema.startswith(fff_config.TEST_PREFIX):
            raise RuntimeError(f"refusing to rewrite paths in non-test database {schema}")
        conn = db.connect_database(schema, cfg, pooled=False)
        try:
            cur = conn.cursor()
            for table, column in path_columns(cur, schema):
                pk = primary_key(cur, schema, table)
                key_sql = ", ".join(f"`{k}`" for k in pk) if pk else f"`{column}`"
                cur.execute(f"SELECT {key_sql}, `{column}` FROM `{table}` WHERE `{column}` REGEXP %s",
                            (r"^[A-Za-z]:[\\/]",))
                updates = []
                for row in cur.fetchall():
                    keys, value = row[:-1], row[-1]
                    new = fff_config.test_path(value, test_cfg)
                    if new != value:
                        updates.append((new, *keys))
                results[f"{schema}.{table}.{column}"] = len(updates)
                if dry_run or not updates:
                    continue
                where = " AND ".join(f"`{k}` = %s" for k in pk) if pk else f"`{column}` = %s"
                sql = f"UPDATE `{table}` SET `{column}` = %s WHERE {where}"
                for i in range(0, len(updates), batch):
                    cur.executemany(sql, updates[i:i + batch])
                    conn.commit()
                logging.info(f"  {schema}.{table}.{column}: rewrote {len(updates)} paths")
        finally:
            conn.close()
    return results


def remaining_prod_paths(cfg, modules=None):
    """Count stored drive paths in the test DBs that still point outside the test locations."""
    test_cfg = _test_cfg(cfg)
    leftovers = {}
    for module in modules or fff_config.MODULES:
        schema = fff_config.database_name(test_cfg, module)
        conn = db.connect_database(schema, cfg, pooled=False)
        try:
            cur = conn.cursor()
            for table, column in path_columns(cur, schema):
                cur.execute(f"SELECT `{column}` FROM `{table}` WHERE `{column}` REGEXP %s", (r"^[A-Za-z]:[\\/]",))
                n = sum(1 for (v,) in cur.fetchall() if fff_config.test_path(v, test_cfg) != v)
                if n:
                    leftovers[f"{schema}.{table}.{column}"] = n
        finally:
            conn.close()
    return leftovers
