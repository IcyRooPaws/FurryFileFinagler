"""Database schema: build every table FFF uses, keep the all_files view current, check for gaps.

The CREATE TABLE statements still live with the code that uses each table (archive/db.py,
fluffle/search.py, the sorter's DatabaseManager ...); ``init`` runs all of them in one go, so
an empty server becomes a working install without opening each tool first. Tables that only
a GUI used to create (the Duplicates tool's) have their DDL here.

``all_files`` (a UNION over every per-folder hash table in the sorter database) is generated
from the tables that actually exist, instead of a hand-kept SQL file that drifted: it had
missed manual_sort_gifs. The sorter refreshes it whenever it creates a new hash table.

``snapshot`` writes the live schema to sql/schema/ for reference and review; the reporting
views and stored procedures that FFF itself never uses exist only there and on the server.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from fff.common import config as fff_config
from fff.common import db, paths

logger = logging.getLogger(__name__)

MODULES = ("fluffle", "archive", "sorter")

# Columns every per-folder hash table has, in the order all_files exposes them.
ALL_FILES_COLUMNS = (
    "file_name", "perceptual_hash_dhash", "perceptual_hash_phash", "perceptual_hash_average",
    "perceptual_hash_whash", "file_size", "file_modified_at", "file_path", "md5_hash",
    "e621_post_id", "hash_type", "file_created_at", "last_artist_check", "artist_matched",
)
# Union order: the first match of a "... FROM all_files WHERE md5_hash = %s LIMIT 1" comes from
# the earliest table, so favorites and the main library come before archives.
_PREFERRED_ORDER = ("favorites", "manual_sort_images", "manual_sort_videos", "manual_sort_gifs",
                    "sfw_image", "sfw_video", "sfw_gif", "nsfw_image", "nsfw_video", "nsfw_gif")

# Hash tables the sorter uses besides its destination folders and archives (hash_manager phases).
SORTER_SPECIAL_TABLES = ("favorites", "manual_sort_images", "manual_sort_videos", "manual_sort_gifs",
                         "possible_missing_files", "other_directories")

# Tables FFF needs that are not per-folder hash tables.
REQUIRED_TABLES = {
    "fluffle": ("images", "e621_links", "furaffinity_links", "weasyl_links", "twitter_links",
                "bluesky_links", "deviantart_links", "other_links", "external_fa_sources"),
    "archive": ("posts", "tags", "artists", "posts_artists", "posts_tags", "source_links", "pools",
                "posts_pools", "e621_perceptual_hashes", "artist_file_renames", "post_relationships",
                "takedowns", "post_takedowns", "unfavorited_posts_archive", "ai_generated_posts_archive",
                "ai_flagged_artists", "fa_pending_unfavorites"),
    "sorter": ("file_operations", "video_signatures", "similarity_checkpoint", "non_duplicate_groups",
               "duplicate_sessions"),
}

# The Duplicates tool's tables (DDL used to exist only inside the GUI class).
DUPLICATE_TOOL_TABLES = {
    "similarity_checkpoint": """
        CREATE TABLE IF NOT EXISTS `{db}`.`similarity_checkpoint` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `session_id` VARCHAR(64) NOT NULL,
            `last_index` INT NOT NULL,
            `threshold` INT NOT NULL,
            `total_files` INT NOT NULL,
            `similar_pairs` LONGTEXT NOT NULL,
            `processed_files` LONGTEXT NOT NULL,
            `comparisons_made` BIGINT NOT NULL DEFAULT 0,
            `comparisons_skipped` BIGINT NOT NULL DEFAULT 0,
            `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX `idx_session` (`session_id`),
            INDEX `idx_updated` (`updated_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "non_duplicate_groups": """
        CREATE TABLE IF NOT EXISTS `{db}`.`non_duplicate_groups` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `hash_column` VARCHAR(128) NOT NULL,
            `group_signature` CHAR(64) NOT NULL,
            `file_count` INT NOT NULL,
            `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY `uq_non_duplicate_group` (`hash_column`, `group_signature`),
            INDEX `idx_non_duplicate_hash_column` (`hash_column`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "duplicate_sessions": """
        CREATE TABLE IF NOT EXISTS `{db}`.`duplicate_sessions` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `session_name` VARCHAR(255) NOT NULL UNIQUE,
            `duplicate_groups` LONGTEXT NOT NULL,
            `current_index` INT NOT NULL DEFAULT 0,
            `hash_type` VARCHAR(50) NOT NULL,
            `threshold` INT NOT NULL,
            `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX `idx_name` (`session_name`),
            INDEX `idx_updated` (`updated_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
}

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_]+$")


def _check_name(name: str) -> str:
    if not _SAFE_NAME.match(name):
        raise ValueError(f"unexpected table or schema name {name!r}")
    return name


def ensure_duplicate_tool_table(conn, sorter_db: str, table: str) -> None:
    cursor = conn.cursor()
    try:
        cursor.execute(DUPLICATE_TOOL_TABLES[table].format(db=_check_name(sorter_db)))
        conn.commit()
    finally:
        cursor.close()


# --- all_files -------------------------------------------------------------------------

def hash_tables(conn, sorter_db: str) -> list[str]:
    """Base tables in the sorter database that have every all_files column, in union order."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT c.TABLE_NAME FROM information_schema.COLUMNS c "
            "JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
            f"WHERE c.TABLE_SCHEMA = %s AND t.TABLE_TYPE = 'BASE TABLE' "
            f"AND c.COLUMN_NAME IN ({','.join(['%s'] * len(ALL_FILES_COLUMNS))}) "
            "GROUP BY c.TABLE_NAME HAVING COUNT(*) = %s",
            (sorter_db, *ALL_FILES_COLUMNS, len(ALL_FILES_COLUMNS)))
        names = [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()
    return sorted(names, key=_union_rank)


def _union_rank(name: str):
    if name in _PREFERRED_ORDER:
        return (0, _PREFERRED_ORDER.index(name), name)
    if name.startswith("archive_"):
        return (1, 0, name)
    if name == "archived_favorites":
        return (2, 0, name)
    return (3, 0, name)


def missing_columns(conn, sorter_db: str, table: str) -> list[str]:
    """The all_files columns a sorter table lacks."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                       "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (sorter_db, table))
        have = {row[0] for row in cursor.fetchall()}
    finally:
        cursor.close()
    return [c for c in ALL_FILES_COLUMNS if c not in have]


def all_files_sql(sorter_db: str, tables: list[str]) -> str:
    db_name = _check_name(sorter_db)
    if not tables:
        raise ValueError("no hash tables to build all_files from")
    cols = ", ".join(f"`{c}`" for c in ALL_FILES_COLUMNS)
    selects = [f"SELECT {cols}, '{_check_name(t)}' AS `table_name` FROM `{db_name}`.`{t}`" for t in tables]
    return f"CREATE OR REPLACE VIEW `{db_name}`.`all_files` AS\n" + "\nUNION ALL\n".join(selects)


def all_files_tables(conn, sorter_db: str) -> list[str] | None:
    """Tables the current all_files view covers (None if there is no view)."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT VIEW_DEFINITION FROM information_schema.VIEWS "
                       "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'all_files'", (sorter_db,))
        row = cursor.fetchone()
    finally:
        cursor.close()
    if not row:
        return None
    return re.findall(r"'(\w+)' AS `table_name`", row[0])


def refresh_all_files_view(conn, sorter_db: str | None = None) -> list[str]:
    """(Re)create all_files over every hash table; returns the tables it covers."""
    sorter_db = sorter_db or fff_config.database_name(fff_config.load(), "sorter")
    tables = hash_tables(conn, sorter_db)
    if not tables:
        logger.warning("No hash tables in %s yet; all_files not created", sorter_db)
        return []
    if all_files_tables(conn, sorter_db) == tables:
        return tables
    cursor = conn.cursor()
    try:
        cursor.execute(all_files_sql(sorter_db, tables))
        conn.commit()
    finally:
        cursor.close()
    logger.info("all_files view now covers %s tables", len(tables))
    return tables


# --- init and check --------------------------------------------------------------------

def sorter_hash_table_names(sorter_config) -> list[str]:
    """Every per-folder hash table the sorter can use with this configuration."""
    names = list(sorter_config.destination_directories)
    archive_dirs = getattr(sorter_config, "archive_directories", None) or \
        sorter_config.config.get("archive_directories", {}) or {}
    for key, value in archive_dirs.items():
        if isinstance(value, dict):
            names += [f"archive_{key}_{media}" for media, path in value.items() if path]
        else:
            names.append(f"archive_{key}")
    names += ["archived_favorites", *SORTER_SPECIAL_TABLES]
    return list(dict.fromkeys(names))


def init(progress=print) -> dict:
    """Create the three databases and every table FFF uses. Never drops anything; the only
    changes to existing tables are the sorter's own column migrations, which run on every
    sorter start anyway (DatabaseManager).

    Runs in the active environment, so 'fff --env test db init' builds the test_* databases.
    Returns {module: [tables created]}.
    """
    cfg = fff_config.load()
    names = fff_config.database_names(cfg)
    before = {m: set(existing_tables(m)) for m in MODULES}

    conn = db.connect_server(cfg)
    try:
        cursor = conn.cursor()
        for module in MODULES:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{_check_name(names[module])}` "
                           "DEFAULT CHARACTER SET utf8mb4")
        cursor.close()
    finally:
        conn.close()

    progress(f"fluffle: {names['fluffle']}")
    from fff.fluffle.search import init_database
    from fff.fluffle.sync_external_fa_sources import create_table_if_not_exists as external_fa_table
    conn = init_database(fff_config.legacy_view("fluffle")["database"])
    if conn is None:
        raise RuntimeError(f"could not create the tables in {names['fluffle']}")
    try:
        external_fa_table(conn)
    finally:
        conn.close()

    progress(f"archive: {names['archive']}")
    from fff.archive.db import create_tables_if_not_exists
    from fff.archive.fa_unfavorite import _ensure_pending_table
    conn = db.connect("archive", cfg, pooled=False)
    try:
        create_tables_if_not_exists(conn)
        _ensure_pending_table(conn)
    finally:
        conn.close()

    progress(f"sorter: {names['sorter']}")
    from fff.hashing.video_similarity import VideoSignatureCache
    from fff.sorter.config import Config
    from fff.sorter.handlers.database_manager import DatabaseManager
    sorter_config = Config()
    manager = DatabaseManager(sorter_config)  # creates the database, archive tables, file_operations
    for table in sorter_hash_table_names(sorter_config):
        manager.ensure_dest_table(table)
    VideoSignatureCache(manager.get_db_connection, names["sorter"])
    conn = db.connect("sorter", cfg, pooled=False)
    try:
        for table in DUPLICATE_TOOL_TABLES:
            ensure_duplicate_tool_table(conn, names["sorter"], table)
        covered = refresh_all_files_view(conn, names["sorter"])
    finally:
        conn.close()
    progress(f"all_files: {len(covered)} hash tables")

    return {m: sorted(set(existing_tables(m)) - before[m]) for m in MODULES}


def existing_tables(module: str) -> list[str]:
    """Base tables in a module's database (empty if the database does not exist)."""
    cfg = fff_config.load()
    conn = db.connect_server(cfg)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES "
                       "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'",
                       (fff_config.database_name(cfg, module),))
        return sorted(row[0] for row in cursor.fetchall())
    finally:
        conn.close()


def check() -> list[str]:
    """Problems with the schema of the active environment; empty when nothing is missing."""
    from fff.sorter.config import Config

    cfg = fff_config.load()
    problems = []
    expected_hash_tables = sorter_hash_table_names(Config())
    have_sorter = set()
    for module in MODULES:
        have = set(existing_tables(module))
        missing = [t for t in REQUIRED_TABLES[module] if t not in have]
        if module == "sorter":
            have_sorter = have
            missing += [t for t in expected_hash_tables if t not in have]
        if missing:
            problems.append(f"{module}: missing {len(missing)} table(s): {', '.join(missing)}")
    sorter_db = fff_config.database_name(cfg, "sorter")
    conn = db.connect_server(cfg)
    try:
        covered = all_files_tables(conn, sorter_db)
        tables = hash_tables(conn, sorter_db)
        # A configured hash table that lacks some all_files columns can't be in the view either.
        incomplete = {t: missing_columns(conn, sorter_db, t)
                      for t in expected_hash_tables if t in have_sorter and t not in tables}
    finally:
        conn.close()
    if covered is None:
        problems.append("sorter: the all_files view does not exist")
    elif set(tables) - set(covered):
        problems.append("sorter: all_files is missing hash table(s): "
                        + ", ".join(sorted(set(tables) - set(covered))))
    for table, cols in sorted(incomplete.items()):
        problems.append(f"sorter: {table} lacks column(s) {', '.join(cols)}, so all_files leaves it out")
    if problems:
        problems.append("run 'fff db init' to create what is missing")
    return problems


# --- snapshot --------------------------------------------------------------------------

SNAPSHOT_DIR = paths.ROOT / "sql" / "schema"


def snapshot(out_dir: Path | None = None) -> list[Path]:
    """Write each module's live schema to sql/schema/<prod name>.sql (tables, views, routines).

    Normalized for diffs: no AUTO_INCREMENT counters or DEFINER clauses, and test_* names are
    written as their production names, so a snapshot of either environment reads the same.
    """
    from fff.common.schema_copy import get_database_schema

    cfg = fff_config.load()
    out_dir = Path(out_dir or SNAPSHOT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    renames = {fff_config.database_name(cfg, m): fff_config.prod_database_name(cfg, m) for m in MODULES}
    written = []
    conn = db.connect_server(cfg)
    try:
        for module in MODULES:
            live = fff_config.database_name(cfg, module)
            schema = get_database_schema(conn, live)
            parts = [f"-- {renames[live]}: schema snapshot written by 'fff db snapshot'. Reference only;",
                     "-- 'fff db init' builds the tables FFF uses from the code.", ""]
            for kind in ("tables", "views", "procedures", "functions", "triggers"):
                for name in sorted(schema[kind]):
                    parts += [f"-- {kind[:-1]}: {name}", _normalize(schema[kind][name], renames) + ";", ""]
            path = out_dir / f"{renames[live]}.sql"
            path.write_text("\n".join(parts), encoding="utf-8", newline="\n")
            written.append(path)
    finally:
        conn.close()
    return written


def _normalize(sql: str, renames: dict) -> str:
    sql = re.sub(r"\s+AUTO_INCREMENT=\d+", "", sql)
    sql = re.sub(r"DEFINER\s*=\s*`[^`]+`@`[^`]+`\s*", "", sql, flags=re.I)
    for live, prod in renames.items():
        if live != prod:
            sql = sql.replace(f"`{live}`", f"`{prod}`")
    return sql.strip()
