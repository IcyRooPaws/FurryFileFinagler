"""Copy database schemas (no data) to their test_ twins: ``fff db copy-schema``.

Merges e621 archive/scripts/database/copy_schema_to_test_databases.py and
FFF/scripts/copy_schema_to_test_database.py; their schema functions were identical
and are copied verbatim below. fff changes:

* References to *all three* production databases are rewritten to their test names, not
  just the database being copied, so a cross-database view in a test schema can't read
  production data.
* The target must start with ``test_`` and differ from the source. The copy drops and
  recreates every table in the target, so pointing it at production would wipe it.
* Covers image_search (reverse search) too.
"""
import logging
import re

from mysql.connector import Error

from fff.common import config as fff_config
from fff.common import db


class UnsafeTargetError(RuntimeError):
    pass


def rewrite_database_references(sql, references):
    """Apply update_database_references for every (production -> test) pair."""
    for original_db, test_db in references.items():
        sql = update_database_references(sql, original_db, test_db)
    return sql


def get_database_schema(conn, database_name):
    """Get the complete schema for a database including tables, views, and other objects."""
    cursor = conn.cursor()
    schema = {
        'tables': {},
        'views': {},
        'triggers': {},
        'procedures': {},
        'functions': {}
    }

    try:
        # Get all tables
        cursor.execute(f"SHOW TABLES FROM `{database_name}`")
        tables = [row[0] for row in cursor.fetchall()]

        for table_name in tables:
            # Check if it's a view or table
            cursor.execute(f"""
                SELECT TABLE_TYPE
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """, (database_name, table_name))

            result = cursor.fetchone()
            if not result:
                continue

            table_type = result[0]

            if table_type == 'BASE TABLE':
                # Get CREATE TABLE statement
                cursor.execute(f"SHOW CREATE TABLE `{database_name}`.`{table_name}`")
                create_statement = cursor.fetchone()[1]
                schema['tables'][table_name] = create_statement
                logging.info(f"  Copied table structure: {table_name}")

            elif table_type == 'VIEW':
                # Get CREATE VIEW statement
                cursor.execute(f"SHOW CREATE VIEW `{database_name}`.`{table_name}`")
                create_statement = cursor.fetchone()[1]
                schema['views'][table_name] = create_statement
                logging.info(f"  Copied view: {table_name}")

        # Get triggers
        cursor.execute(f"""
            SELECT TRIGGER_NAME
            FROM information_schema.TRIGGERS
            WHERE TRIGGER_SCHEMA = %s
        """, (database_name,))

        triggers = [row[0] for row in cursor.fetchall()]
        for trigger_name in triggers:
            cursor.execute(f"SHOW CREATE TRIGGER `{database_name}`.`{trigger_name}`")
            result = cursor.fetchone()
            if result and len(result) > 2:
                schema['triggers'][trigger_name] = result[2]
                logging.info(f"  Copied trigger: {trigger_name}")

        # Get stored procedures
        cursor.execute(f"SHOW PROCEDURE STATUS WHERE Db = %s", (database_name,))
        procedures = [row[1] for row in cursor.fetchall()]
        for proc_name in procedures:
            cursor.execute(f"SHOW CREATE PROCEDURE `{database_name}`.`{proc_name}`")
            result = cursor.fetchone()
            if result and len(result) > 2:
                schema['procedures'][proc_name] = result[2]
                logging.info(f"  Copied procedure: {proc_name}")

        # Get functions
        cursor.execute(f"SHOW FUNCTION STATUS WHERE Db = %s", (database_name,))
        functions = [row[1] for row in cursor.fetchall()]
        for func_name in functions:
            cursor.execute(f"SHOW CREATE FUNCTION `{database_name}`.`{func_name}`")
            result = cursor.fetchone()
            if result and len(result) > 2:
                schema['functions'][func_name] = result[2]
                logging.info(f"  Copied function: {func_name}")

    except Error as e:
        logging.error(f"Error getting schema: {e}")
    finally:
        cursor.close()

    return schema


def update_database_references(sql, original_db, test_db):
    """Update database references in SQL statements from original to test database."""
    # Replace database name in various contexts
    patterns = [
        (f"`{original_db}`", f"`{test_db}`"),
        (f"'{original_db}'", f"'{test_db}'"),
        (f'"{original_db}"', f'"{test_db}"'),
        (f" {original_db}.", f" {test_db}."),
    ]

    updated_sql = sql
    for old, new in patterns:
        updated_sql = updated_sql.replace(old, new)

    return updated_sql


def create_test_database(conn, test_db_name, dry_run=False):
    """Create test database if it doesn't exist."""
    cursor = conn.cursor()
    try:
        if dry_run:
            logging.info(f"[DRY RUN] Would create database: {test_db_name}")
            return True

        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{test_db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        logging.info(f"Created database: {test_db_name}")
        return True
    except Error as e:
        logging.error(f"Error creating database {test_db_name}: {e}")
        return False
    finally:
        cursor.close()


def apply_schema_to_database(conn, test_db_name, schema, original_db_name, dry_run=False, references=None):
    """Apply schema to test database."""
    cursor = conn.cursor()

    try:
        if not dry_run:
            cursor.execute(f"USE `{test_db_name}`")
            # Temporarily disable foreign key checks to allow creating tables in any order
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")

        # Apply tables first
        logging.info(f"\nCreating {len(schema['tables'])} tables...")
        for table_name, create_statement in schema['tables'].items():
            updated_sql = rewrite_database_references(create_statement, references or {original_db_name: test_db_name})

            # Add DROP TABLE IF EXISTS for clean re-runs
            drop_sql = f"DROP TABLE IF EXISTS `{table_name}`"

            if dry_run:
                logging.info(f"[DRY RUN] Would execute: {drop_sql}")
                logging.debug(f"[DRY RUN] Would execute: {updated_sql[:200]}...")
            else:
                try:
                    cursor.execute(drop_sql)
                    cursor.execute(updated_sql)
                    logging.info(f"  Created table: {table_name}")
                except Error as e:
                    logging.error(f"  Error creating table {table_name}: {e}")

        # Apply views (after tables since views may depend on tables)
        logging.info(f"\nCreating {len(schema['views'])} views...")
        for view_name, create_statement in schema['views'].items():
            updated_sql = rewrite_database_references(create_statement, references or {original_db_name: test_db_name})

            # Add DROP VIEW IF EXISTS for clean re-runs
            drop_sql = f"DROP VIEW IF EXISTS `{view_name}`"

            # Remove the DEFINER clause if present (can cause permission issues)
            updated_sql = re.sub(r'DEFINER\s*=\s*`[^`]+`@`[^`]+`\s+', '', updated_sql, flags=re.IGNORECASE)

            if dry_run:
                logging.info(f"[DRY RUN] Would execute: {drop_sql}")
                logging.debug(f"[DRY RUN] Would execute: {updated_sql[:200]}...")
            else:
                try:
                    cursor.execute(drop_sql)
                    cursor.execute(updated_sql)
                    logging.info(f"  Created view: {view_name}")
                except Error as e:
                    logging.error(f"  Error creating view {view_name}: {e}")
                    logging.debug(f"  SQL: {updated_sql}")

        # Apply triggers
        if schema['triggers']:
            logging.info(f"\nCreating {len(schema['triggers'])} triggers...")
            for trigger_name, create_statement in schema['triggers'].items():
                updated_sql = rewrite_database_references(create_statement, references or {original_db_name: test_db_name})
                updated_sql = re.sub(r'DEFINER\s*=\s*`[^`]+`@`[^`]+`\s+', '', updated_sql, flags=re.IGNORECASE)

                drop_sql = f"DROP TRIGGER IF EXISTS `{trigger_name}`"

                if dry_run:
                    logging.info(f"[DRY RUN] Would execute: {drop_sql}")
                    logging.debug(f"[DRY RUN] Would execute: {updated_sql[:200]}...")
                else:
                    try:
                        cursor.execute(drop_sql)
                        cursor.execute(updated_sql)
                        logging.info(f"  Created trigger: {trigger_name}")
                    except Error as e:
                        logging.error(f"  Error creating trigger {trigger_name}: {e}")

        # Apply procedures
        if schema['procedures']:
            logging.info(f"\nCreating {len(schema['procedures'])} procedures...")
            for proc_name, create_statement in schema['procedures'].items():
                updated_sql = rewrite_database_references(create_statement, references or {original_db_name: test_db_name})
                updated_sql = re.sub(r'DEFINER\s*=\s*`[^`]+`@`[^`]+`\s+', '', updated_sql, flags=re.IGNORECASE)

                drop_sql = f"DROP PROCEDURE IF EXISTS `{proc_name}`"

                if dry_run:
                    logging.info(f"[DRY RUN] Would execute: {drop_sql}")
                    logging.debug(f"[DRY RUN] Would execute: {updated_sql[:200]}...")
                else:
                    try:
                        cursor.execute(drop_sql)
                        cursor.execute(updated_sql)
                        logging.info(f"  Created procedure: {proc_name}")
                    except Error as e:
                        logging.error(f"  Error creating procedure {proc_name}: {e}")

        # Apply functions
        if schema['functions']:
            logging.info(f"\nCreating {len(schema['functions'])} functions...")
            for func_name, create_statement in schema['functions'].items():
                updated_sql = rewrite_database_references(create_statement, references or {original_db_name: test_db_name})
                updated_sql = re.sub(r'DEFINER\s*=\s*`[^`]+`@`[^`]+`\s+', '', updated_sql, flags=re.IGNORECASE)

                drop_sql = f"DROP FUNCTION IF EXISTS `{func_name}`"

                if dry_run:
                    logging.info(f"[DRY RUN] Would execute: {drop_sql}")
                    logging.debug(f"[DRY RUN] Would execute: {updated_sql[:200]}...")
                else:
                    try:
                        cursor.execute(drop_sql)
                        cursor.execute(updated_sql)
                        logging.info(f"  Created function: {func_name}")
                    except Error as e:
                        logging.error(f"  Error creating function {func_name}: {e}")

        # Re-enable foreign key checks
        if not dry_run:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            logging.info("\n✓ Foreign key checks re-enabled")

    except Error as e:
        logging.error(f"Error applying schema: {e}")
        if not dry_run:
            try:
                cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            except:
                pass
    finally:
        cursor.close()


# --- schema copy orchestration ---------------------------------------------------------

def test_database_pairs(cfg, modules=None):
    """(module, production schema, test schema) for the requested modules."""
    modules = modules or fff_config.MODULES
    return [(m, fff_config.prod_database_name(cfg, m),
             fff_config.TEST_PREFIX + fff_config.prod_database_name(cfg, m)) for m in modules]


def check_target(original_db, test_db, cfg):
    prod_names = {fff_config.prod_database_name(cfg, m) for m in fff_config.MODULES}
    if not test_db.startswith(fff_config.TEST_PREFIX) or test_db == original_db or test_db in prod_names:
        raise UnsafeTargetError(f"refusing to copy {original_db} into {test_db}: the target's tables are "
                                f"dropped and recreated, so it must be a {fff_config.TEST_PREFIX}* database")


def copy_database_schema(cfg, original_db, test_db, dry_run=False, references=None):
    """Copy schema from original database to test database."""
    check_target(original_db, test_db, cfg)
    logging.info(f"\n{'='*60}")
    logging.info(f"Copying schema: {original_db} -> {test_db}")
    logging.info(f"{'='*60}\n")

    try:
        conn = db.connect_server(cfg)
    except Error as e:
        logging.error(f"Error connecting to MySQL: {e}")
        return False

    try:
        logging.info(f"Reading schema from: {original_db}")
        schema = get_database_schema(conn, original_db)

        total_objects = sum(len(schema[k]) for k in ('tables', 'views', 'triggers', 'procedures', 'functions'))
        logging.info(f"\nFound {total_objects} objects to copy:")
        for kind in ('tables', 'views', 'triggers', 'procedures', 'functions'):
            logging.info(f"  - {kind.capitalize()}: {len(schema[kind])}")

        logging.info(f"\nCreating test database: {test_db}")
        if not create_test_database(conn, test_db, dry_run):
            return False

        logging.info(f"\nApplying schema to: {test_db}")
        apply_schema_to_database(conn, test_db, schema, original_db, dry_run, references=references)
        logging.info(f"\nSchema copy completed: {original_db} -> {test_db}")
        return True
    finally:
        conn.close()


def copy_all(cfg, modules=None, dry_run=False):
    """Copy every requested module's schema to its test twin. Returns True if all succeeded."""
    # Rewrite references to every production DB, whichever one is being copied.
    references = {prod: test for _, prod, test in test_database_pairs(cfg)}
    ok = True
    for module, prod, test in test_database_pairs(cfg, modules):
        ok &= bool(copy_database_schema(cfg, prod, test, dry_run, references=references))
    return ok


# --- fff: copy production data into the test databases -------------------------

def copy_database_data(cfg, original_db, test_db, dry_run=False, references=None):
    """Refresh test_db's schema from original_db, then copy every table's rows.

    Production is only read (INSERT INTO test_db.t SELECT ... FROM original_db.t, server-side).
    Generated columns are skipped (MySQL computes them). Returns {table: (prod_rows, test_rows)}.
    """
    check_target(original_db, test_db, cfg)
    if not copy_database_schema(cfg, original_db, test_db, dry_run, references=references):
        return None

    conn = db.connect_server(cfg)
    results = {}
    try:
        cur = conn.cursor()
        cur.execute("SET SESSION sql_mode = ''")  # accept legacy zero dates etc. exactly as stored
        cur.execute("SET SESSION FOREIGN_KEY_CHECKS = 0")
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s "
                    "AND table_type = 'BASE TABLE' ORDER BY table_name", (original_db,))
        tables = [r[0] for r in cur.fetchall()]
        for table in tables:
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s "
                        "AND table_name = %s AND extra NOT LIKE '%VIRTUAL GENERATED%' "
                        "AND extra NOT LIKE '%STORED GENERATED%' ORDER BY ordinal_position",
                        (original_db, table))
            cols = ", ".join(f"`{r[0]}`" for r in cur.fetchall())
            cur.execute(f"SELECT COUNT(*) FROM `{original_db}`.`{table}`")
            prod_rows = cur.fetchone()[0]
            if dry_run:
                logging.info(f"[DRY RUN] Would copy {prod_rows} rows: {original_db}.{table} -> {test_db}.{table}")
                results[table] = (prod_rows, None)
                continue
            cur.execute(f"INSERT INTO `{test_db}`.`{table}` ({cols}) SELECT {cols} FROM `{original_db}`.`{table}`")
            conn.commit()
            cur.execute(f"SELECT COUNT(*) FROM `{test_db}`.`{table}`")
            test_rows = cur.fetchone()[0]
            results[table] = (prod_rows, test_rows)
            level = logging.INFO if test_rows == prod_rows else logging.ERROR
            logging.log(level, f"  {table}: {test_rows}/{prod_rows} rows")
        cur.execute("SET SESSION FOREIGN_KEY_CHECKS = 1")
    finally:
        conn.close()
    return results


def copy_all_data(cfg, modules=None, dry_run=False):
    """Copy schema and data for the requested modules. Returns True if every row count matched."""
    references = {prod: test for _, prod, test in test_database_pairs(cfg)}
    ok = True
    for module, prod, test in test_database_pairs(cfg, modules):
        results = copy_database_data(cfg, prod, test, dry_run, references=references)
        if results is None:
            ok = False
            continue
        if not dry_run:
            mismatched = {t: r for t, r in results.items() if r[0] != r[1]}
            total = sum(r[1] for r in results.values())
            logging.info(f"{module}: copied {total} rows in {len(results)} tables into {test}"
                         + (f"; MISMATCHED: {mismatched}" if mismatched else ""))
            ok &= not mismatched
    return ok
