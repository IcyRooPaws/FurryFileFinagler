# fff: kept as one module to preserve the existing archive database interface.
# fff: post file names are made Windows-safe (invalid characters -> _) wherever they are built.
import logging
import os
import time
from mysql.connector import Error
from datetime import datetime

# Import path normalization utility
from fff.archive.config import normalize_file_path
from fff.clients import http
from fff.common import db as fff_db
from fff.common.naming import clean_artist_names, windows_safe_filename


def _sorter_db_name():
    """fff: the image-hash (sorter) schema for the active environment."""
    from fff.archive.paths import db_names
    return db_names()["sorter"]


FLAG_TOKENS = [
    "conditional_dnp",
    "sound_warning",
    "third-party-edit",
    "third-party_edit",
]

# ---------------------------------------------------------------------------
# e621 API v1/v2 compatibility helpers
# ---------------------------------------------------------------------------
# e621 is rolling out a new v2 API format (see forum topic #63849).
# v2 changes:
#   - List endpoints return a bare array instead of {"posts": [...]}
#   - Single-post endpoints return a bare object instead of {"post": {...}}
#   - File fields move from post["file"] to post["files"]["original"] / post["files"]["meta"]
#   - Tags are flat array by default; use mode=extended to keep category grouping
# Rollout: v2 opt-in now (add v2=true), default in ~Dec 2026, v1 removed ~May 2027.
# All API requests already include v2=true&mode=extended so that tags remain grouped.
# These helpers handle both v1 and v2 response shapes for backwards compatibility
# with already-cached temp response files.

def _parse_post_list(data):
    """Return the list of posts from either a v1 or v2 API response."""
    if isinstance(data, list):
        return data  # v2: bare array
    return data.get("posts", [])  # v1: {"posts": [...]}


def _parse_single_post(data):
    """Return the post dict from either a v1 or v2 single-post API response."""
    if isinstance(data, dict):
        if "post" in data:
            return data["post"]  # v1: {"post": {...}}
        if "id" in data:
            return data  # v2: bare object
    return {}


def _get_file_url(post):
    """Return the current file URL from either v1 or v2 post dict."""
    if "files" in post:
        return post["files"].get("original", {}).get("url")
    return post.get("file", {}).get("url")


def _get_file_md5(post):
    """Return the MD5 hash from either v1 or v2 post dict."""
    if "files" in post:
        return post["files"].get("meta", {}).get("md5")
    return post.get("file", {}).get("md5")


def _get_file_ext(post):
    """Return the file extension from either v1 or v2 post dict."""
    if "files" in post:
        return post["files"].get("meta", {}).get("ext", "")
    return post.get("file", {}).get("ext", "")
# ---------------------------------------------------------------------------


def strip_flag_tokens(name: str) -> str:
    """Remove known flag tokens (e.g. 'conditional_dnp', 'sound_warning') from a name.

    Mirrors the cleanup logic in FFF so filenames stay consistent.
    """
    if not name:
        return name

    new_name = name
    for token in FLAG_TOKENS:
        patterns = [
            f"_{token}",
            f"-{token}",
            f"{token}_",
            f"{token}-",
        ]
        for pattern in patterns:
            new_name = new_name.replace(pattern, "")

        # Fallback: strip any remaining bare token
        new_name = new_name.replace(token, "")

    # Clean up repeated separators and stray punctuation
    while "__" in new_name:
        new_name = new_name.replace("__", "_")
    while "--" in new_name:
        new_name = new_name.replace("--", "-")
    new_name = new_name.strip("-_ .")

    return new_name or name


def resolve_existing_file_path(file_path: str | None) -> str | None:
    """Return a verified local file path, allowing for extension changes."""
    if not file_path:
        return None

    if os.path.exists(file_path):
        return file_path

    base_path = os.path.splitext(file_path)[0]
    dir_path = os.path.dirname(file_path)
    if not dir_path or not os.path.exists(dir_path):
        return None

    base_name = os.path.basename(base_path)
    for file_name in os.listdir(dir_path):
        if os.path.splitext(file_name)[0] == base_name:
            return os.path.join(dir_path, file_name)

    return None


def connect_to_mysql(mysql_config):
    # One connection per call. (This used to build a new 5-connection pool on every call.)
    try:
        return fff_db.connect_with(mysql_config)
    except Error as e:
        logging.error("Error connecting to MySQL: %s", e)
        return None


def record_ai_flagged_artists(conn, artist_names):
    """Record artist names flagged by AI detection and link known artist IDs."""
    names = sorted({name.strip() for name in artist_names if name and name.strip()})
    if not names:
        return 0

    cursor = conn.cursor()
    try:
        for artist_name in names:
            cursor.execute("SELECT artist_id FROM artists WHERE name = %s", (artist_name,))
            row = cursor.fetchone()
            artist_id = row[0] if row else None
            cursor.execute(
                """
                INSERT INTO ai_flagged_artists
                    (artist_id, artist_name, first_flagged_at, last_flagged_at, flag_count)
                VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
                ON DUPLICATE KEY UPDATE
                    artist_id = COALESCE(VALUES(artist_id), artist_id),
                    last_flagged_at = CURRENT_TIMESTAMP,
                    flag_count = flag_count + 1
                """,
                (artist_id, artist_name),
            )
        conn.commit()
        return len(names)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def load_ai_flagged_artists(conn):
    """Return the artist names in ai_flagged_artists (the AI artist list)."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT artist_name FROM ai_flagged_artists ORDER BY artist_name")
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()

def connect_to_image_sorting_hashes_db(mysql_config):
    """Connect to the external image hash database if configured.

    Uses mysql.image_hash_db settings if provided; otherwise defaults to the sorter database (fff: prod or test_)
    and reuses top-level mysql host/user/password.
    """
    try:
        mysql_config = mysql_config or {}
        ihd = mysql_config.get("image_hash_db") or {}
        db_name = ihd.get("database") or _sorter_db_name()  # fff: prod or test_ name
        # image_hash_db's host/port/user/password override the top-level mysql ones.
        settings = fff_db.server_overrides(mysql_config) | fff_db.server_overrides(ihd)
        return fff_db.connect_with(settings, database=db_name)
    except Error as e:
        logging.debug("image hash database not available: %s", e)
        return None

def check_media_hash_in_image_sorting_db(mysql_config, file_name):
    """Check if a media hash already exists for a file_name in image_sorting_hashes database."""
    conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not conn:
        return None

    cursor = conn.cursor()
    try:
        # Tables to search can be configured under mysql.image_hash_db.tables.
        # Defaults cover the standard schema found in image_sorting_hashes installs.
        ihd = (mysql_config or {}).get("image_hash_db", {})
        tables_to_check = ihd.get(
            "tables",
            [
                "favorites",
                "manual_sort_images",
                "manual_sort_videos",
                "sfw_image",
                "sfw_video",
                "sfw_gif",
                "nsfw_image",
                "nsfw_video",
                "nsfw_gif",
                "archive_explicit_images",
                "archive_explicit_videos",
                "archive_explicit_gifs",
                "archive_sfw_images",
                "archive_sfw_videos",
                "archive_sfw_gifs",
                "other_directories",
            ],
        )

        # Columns: file_name (VARCHAR(512)), file_hash (VARCHAR(128)) are expected

        # Try exact match first
        for table in tables_to_check:
            try:
                query = f"SELECT file_hash FROM {table} WHERE file_name = %s"
                cursor.execute(query, (file_name,))
                result = cursor.fetchone()
                if result:
                    hash_value = result[0]
                    logging.info(f"Found existing hash for {file_name} in {table} table (exact match): {hash_value[:16]}...")
                    return hash_value
            except Exception as e:
                logging.debug(f"Error checking table {table}: {e}")
                continue  # Try next table

        # Try partial match (LIKE search) for cases where filenames might be slightly different
        # This handles cases where the stored filename might be just the base name
        base_name = os.path.basename(file_name)

        for table in tables_to_check:
            try:
                # Try exact basename match
                query = f"SELECT file_hash FROM {table} WHERE file_name = %s"
                cursor.execute(query, (base_name,))
                result = cursor.fetchone()
                if result:
                    hash_value = result[0]
                    logging.info(f"Found existing hash for {base_name} in {table} table (basename match): {hash_value[:16]}...")
                    return hash_value

                # Try LIKE search as last resort (could be slower)
                query = f"SELECT file_hash FROM {table} WHERE file_name LIKE %s LIMIT 1"
                cursor.execute(query, (f"%{base_name}%",))
                result = cursor.fetchone()
                if result:
                    hash_value = result[0]
                    logging.info(f"Found existing hash for {base_name} in {table} table (partial match): {hash_value[:16]}...")
                    return hash_value

            except Exception as e:
                logging.debug(f"Error checking table {table} with partial match: {e}")
                continue  # Try next table

        logging.debug(f"No existing hash found for {file_name} in image_sorting_hashes database")
        return None

    except Exception as e:
        logging.debug(f"Error checking image_sorting_hashes database: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def get_file_path_from_image_sorting_hashes_by_post_id(mysql_config, post_id):
    """
    Get file_path from image_sorting_hashes database using e621_post_id.

    Checks the all_files view or individual tables for a matching e621_post_id
    and returns the file_path if found. Ignores file extensions when checking existence.

    Args:
        mysql_config: MySQL configuration dict
        post_id: e621 post ID to search for

    Returns:
        file_path (str) if found, None otherwise
    """
    conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not conn:
        return None

    cursor = conn.cursor()
    try:
        # First try the all_files view if it exists
        try:
            query = "SELECT file_path FROM all_files WHERE e621_post_id = %s LIMIT 1"
            cursor.execute(query, (post_id,))
            result = cursor.fetchone()
            if result and result[0]:
                file_path = result[0]
                # Verify the file exists (ignoring extension)
                base_path = os.path.splitext(file_path)[0]
                dir_path = os.path.dirname(file_path)
                if os.path.exists(file_path):
                    logging.info(f"Found file path for post ID {post_id} in image_sorting_hashes.all_files: {file_path}")
                    return file_path
                elif dir_path and os.path.exists(dir_path):
                    # Check for files with same name but different extension
                    base_name = os.path.basename(base_path)
                    for file in os.listdir(dir_path):
                        if os.path.splitext(file)[0] == base_name:
                            actual_path = os.path.join(dir_path, file)
                            logging.info(f"Found file with different extension for post ID {post_id}: {actual_path} (expected: {file_path})")
                            return actual_path
                    logging.debug(f"File path found in database but no file with matching name exists: {file_path}")
                else:
                    logging.debug(f"File path found in database but file doesn't exist: {file_path}")
        except Exception as e:
            logging.debug(f"all_files view not available or error: {e}")

        # If all_files view doesn't work, try individual tables
        ihd = (mysql_config or {}).get("image_hash_db", {})
        tables_to_check = ihd.get(
            "tables",
            [
                "favorites",
                "manual_sort_images",
                "manual_sort_videos",
                "sfw_image",
                "sfw_video",
                "sfw_gif",
                "nsfw_image",
                "nsfw_video",
                "nsfw_gif",
                "archive_explicit_images",
                "archive_explicit_videos",
                "archive_explicit_gifs",
                "archive_sfw_images",
                "archive_sfw_videos",
                "archive_sfw_gifs",
                "other_directories",
            ],
        )

        for table in tables_to_check:
            try:
                query = f"SELECT file_path FROM {table} WHERE e621_post_id = %s LIMIT 1"
                cursor.execute(query, (post_id,))
                result = cursor.fetchone()
                if result and result[0]:
                    file_path = result[0]
                    # Verify the file exists (ignoring extension)
                    base_path = os.path.splitext(file_path)[0]
                    dir_path = os.path.dirname(file_path)
                    if os.path.exists(file_path):
                        logging.info(f"Found file path for post ID {post_id} in {table}: {file_path}")
                        return file_path
                    elif dir_path and os.path.exists(dir_path):
                        # Check for files with same name but different extension
                        base_name = os.path.basename(base_path)
                        for file in os.listdir(dir_path):
                            if os.path.splitext(file)[0] == base_name:
                                actual_path = os.path.join(dir_path, file)
                                logging.info(f"Found file with different extension for post ID {post_id} in {table}: {actual_path} (expected: {file_path})")
                                return actual_path
                        logging.debug(f"File path found in {table} but no file with matching name exists: {file_path}")
                    else:
                        logging.debug(f"File path found in {table} but file doesn't exist: {file_path}")
            except Exception as e:
                logging.debug(f"Error checking table {table}: {e}")
                continue

        logging.debug(f"No file path found for post ID {post_id} in image_sorting_hashes database")
        return None

    except Exception as e:
        logging.error(f"Error checking image_sorting_hashes database for file path by post ID: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def get_file_path_from_image_sorting_hashes(mysql_config, md5_hash):
    """
    Get file_path from image_sorting_hashes database using MD5 hash.

    Checks the all_files view or individual tables for a matching MD5 hash
    and returns the file_path if found. Ignores file extensions when checking existence.

    Args:
        mysql_config: MySQL configuration dict
        md5_hash: MD5 hash of the file to search for

    Returns:
        file_path (str) if found, None otherwise
    """
    conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not conn:
        return None

    cursor = conn.cursor()
    try:
        # First try the all_files view if it exists
        try:
            query = "SELECT file_path FROM all_files WHERE md5_hash = %s LIMIT 1"
            cursor.execute(query, (md5_hash,))
            result = cursor.fetchone()
            if result and result[0]:
                file_path = result[0]
                # Verify the file exists (ignoring extension)
                base_path = os.path.splitext(file_path)[0]
                dir_path = os.path.dirname(file_path)
                if os.path.exists(file_path):
                    logging.info(f"Found file path for MD5 {md5_hash} in image_sorting_hashes.all_files: {file_path}")
                    return file_path
                elif dir_path and os.path.exists(dir_path):
                    # Check for files with same name but different extension
                    base_name = os.path.basename(base_path)
                    for file in os.listdir(dir_path):
                        if os.path.splitext(file)[0] == base_name:
                            actual_path = os.path.join(dir_path, file)
                            logging.info(f"Found file with different extension for MD5 {md5_hash}: {actual_path} (expected: {file_path})")
                            return actual_path
                    logging.debug(f"File path found in database but no file with matching name exists: {file_path}")
                else:
                    logging.debug(f"File path found in database but file doesn't exist: {file_path}")
        except Exception as e:
            logging.debug(f"all_files view not available or error: {e}")

        # If all_files view doesn't work, try individual tables
        ihd = (mysql_config or {}).get("image_hash_db", {})
        tables_to_check = ihd.get(
            "tables",
            [
                "favorites",
                "manual_sort_images",
                "manual_sort_videos",
                "sfw_image",
                "sfw_video",
                "sfw_gif",
                "nsfw_image",
                "nsfw_video",
                "nsfw_gif",
                "archive_explicit_images",
                "archive_explicit_videos",
                "archive_explicit_gifs",
                "archive_sfw_images",
                "archive_sfw_videos",
                "archive_sfw_gifs",
                "other_directories",
            ],
        )

        for table in tables_to_check:
            try:
                # Try to find file_path using md5 or md5_hash column
                for md5_column in ['md5_hash', 'md5', 'file_md5']:
                    try:
                        query = f"SELECT file_path FROM {table} WHERE {md5_column} = %s LIMIT 1"
                        cursor.execute(query, (md5_hash,))
                        result = cursor.fetchone()
                        if result and result[0]:
                            file_path = result[0]
                            # Verify the file exists (ignoring extension)
                            base_path = os.path.splitext(file_path)[0]
                            dir_path = os.path.dirname(file_path)
                            if os.path.exists(file_path):
                                logging.info(f"Found file path for MD5 {md5_hash} in {table}.{md5_column}: {file_path}")
                                return file_path
                            elif dir_path and os.path.exists(dir_path):
                                # Check for files with same name but different extension
                                base_name = os.path.basename(base_path)
                                for file in os.listdir(dir_path):
                                    if os.path.splitext(file)[0] == base_name:
                                        actual_path = os.path.join(dir_path, file)
                                        logging.info(f"Found file with different extension for MD5 {md5_hash} in {table}.{md5_column}: {actual_path} (expected: {file_path})")
                                        return actual_path
                                logging.debug(f"File path found in {table} but no file with matching name exists: {file_path}")
                            else:
                                logging.debug(f"File path found in {table} but file doesn't exist: {file_path}")
                        break  # If query succeeded, don't try other column names
                    except Exception:
                        continue  # Try next column name
            except Exception as e:
                logging.debug(f"Error checking table {table}: {e}")
                continue

        logging.debug(f"No file path found for MD5 {md5_hash} in image_sorting_hashes database")
        return None

    except Exception as e:
        logging.error(f"Error checking image_sorting_hashes database for file path: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def create_tables_if_not_exists(conn):
    cursor = conn.cursor()
    # ...existing code for CREATE TABLE statements...
    cursor.execute("""CREATE TABLE IF NOT EXISTS posts (
        id INT PRIMARY KEY,
        file_url VARCHAR(255),
        md5 VARCHAR(32),
        media_hash TEXT,
        rating VARCHAR(20),
        ingested_at DATETIME,
        file_path VARCHAR(255),
        file_type VARCHAR(20),
        file_name VARCHAR(300),
        deleted TINYINT(1) DEFAULT 0,
        deleted_at DATETIME DEFAULT NULL,
        deletion_reason TEXT DEFAULT NULL,
        lastChecked DATETIME DEFAULT NULL,
        created_at DATETIME DEFAULT NULL,
        updated_at DATETIME DEFAULT NULL
    );""")

    # Attempt to add file_name column if the table existed before this change.
    try:
        cursor.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS file_name VARCHAR(300);")
    except Exception:
        # Fallback for MySQL versions < 8.0 (no IF NOT EXISTS). Silently ignore if column exists.
        try:
            cursor.execute("ALTER TABLE posts ADD COLUMN file_name VARCHAR(300);")
        except Exception:
            pass

    # Attempt to add media_hash column if the table existed before this change.
    try:
        cursor.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS media_hash TEXT;")
    except Exception:
        # Fallback for MySQL versions < 8.0 (no IF NOT EXISTS). Silently ignore if column exists.
        try:
            cursor.execute("ALTER TABLE posts ADD COLUMN media_hash TEXT;")
        except Exception:
            pass

    # Attempt to add created_at and updated_at columns if the table existed before this change.
    try:
        cursor.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS created_at DATETIME DEFAULT NULL;")
        cursor.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS updated_at DATETIME DEFAULT NULL;")
    except Exception:
        # Fallback for MySQL versions < 8.0 (no IF NOT EXISTS). Silently ignore if columns exist.
        try:
            cursor.execute("ALTER TABLE posts ADD COLUMN created_at DATETIME DEFAULT NULL;")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE posts ADD COLUMN updated_at DATETIME DEFAULT NULL;")
        except Exception:
            pass

    # Update existing media_hash column to TEXT if it exists as VARCHAR
    try:
        cursor.execute("ALTER TABLE posts MODIFY COLUMN media_hash TEXT;")
    except Exception:
        pass

    # Attempt to add deletion_reason column if the table existed before this change.
    try:
        cursor.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS deletion_reason TEXT DEFAULT NULL;")
    except Exception:
        # Fallback for MySQL versions < 8.0 (no IF NOT EXISTS). Silently ignore if column exists.
        try:
            cursor.execute("ALTER TABLE posts ADD COLUMN deletion_reason TEXT DEFAULT NULL;")
        except Exception:
            pass
    cursor.execute("""CREATE TABLE IF NOT EXISTS tags (
        tag_id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) UNIQUE,
        tag_type VARCHAR(50)
    );""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS artists (
        artist_id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) UNIQUE
    );""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS posts_artists (
        post_id INT,
        artist_id INT,
        PRIMARY KEY(post_id, artist_id),
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
        FOREIGN KEY (artist_id) REFERENCES artists(artist_id) ON DELETE CASCADE
    );""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS posts_tags (
        post_id INT,
        tag_id INT,
        PRIMARY KEY(post_id, tag_id),
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id) REFERENCES tags(tag_id) ON DELETE CASCADE
    );""")
    # New table for source links (one row per source URL)
    cursor.execute("""CREATE TABLE IF NOT EXISTS source_links (
        post_id INT,
        source_link VARCHAR(1000),
        PRIMARY KEY(post_id, source_link(255)),
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
    );""")

    # Tables for pool support
    cursor.execute("""CREATE TABLE IF NOT EXISTS pools (
        pool_id INT PRIMARY KEY,
        name VARCHAR(255),
        description TEXT,
        created_at DATETIME DEFAULT NULL,
        updated_at DATETIME DEFAULT NULL,
        post_count INT DEFAULT 0,
        is_active TINYINT(1) DEFAULT 1,
        category VARCHAR(50) DEFAULT NULL,
        pool_url VARCHAR(255) GENERATED ALWAYS AS (CONCAT('https://e621.net/pools/', pool_id)) STORED,
        ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_name (name),
        INDEX idx_category (category),
        INDEX idx_is_active (is_active)
    );""")

    # Add pool_url generated column if table existed before this change
    try:
        cursor.execute("ALTER TABLE pools ADD COLUMN IF NOT EXISTS pool_url VARCHAR(255) GENERATED ALWAYS AS (CONCAT('https://e621.net/pools/', pool_id)) STORED;")
    except Exception:
        # Fallback for MySQL versions < 8.0 or if column exists
        try:
            cursor.execute("ALTER TABLE pools ADD COLUMN pool_url VARCHAR(255) GENERATED ALWAYS AS (CONCAT('https://e621.net/pools/', pool_id)) STORED;")
        except Exception:
            pass

    cursor.execute("""CREATE TABLE IF NOT EXISTS posts_pools (
        post_id INT,
        pool_id INT,
        PRIMARY KEY(post_id, pool_id),
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
        FOREIGN KEY (pool_id) REFERENCES pools(pool_id) ON DELETE CASCADE
    );""")

    # Table for caching perceptual hashes of e621 posts
    # This prevents needing to redownload and rehash posts repeatedly
    cursor.execute("""CREATE TABLE IF NOT EXISTS e621_perceptual_hashes (
        post_id INT PRIMARY KEY,
        perceptual_hash TEXT,
        hash_algorithm VARCHAR(64) DEFAULT 'average',
        file_url VARCHAR(255),
        cached_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
        INDEX idx_perceptual_hash (perceptual_hash(255))
    );""")

    # Table for tracking file renames due to artist name changes
    cursor.execute("""CREATE TABLE IF NOT EXISTS artist_file_renames (
        rename_id INT AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL,
        old_file_name VARCHAR(300),
        new_file_name VARCHAR(300),
        old_file_path VARCHAR(255),
        new_file_path VARCHAR(255),
        old_artist_names TEXT,
        new_artist_names TEXT,
        renamed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
        INDEX idx_post_id (post_id),
        INDEX idx_renamed_at (renamed_at)
    );""")

    # Table for tracking parent-child relationships between posts
    # Each row represents ONE parent-child relationship
    # A post can have multiple rows (one per parent and one per child)
    # Note: parent_id and child_id do NOT have foreign key constraints because we want to track
    # relationships even when the related post is not in our favorites/database
    cursor.execute("""CREATE TABLE IF NOT EXISTS post_relationships (
        id INT AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL,
        parent_id INT DEFAULT NULL,
        child_id INT DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
        INDEX idx_post_id (post_id),
        INDEX idx_parent_id (parent_id),
        INDEX idx_child_id (child_id),
        UNIQUE KEY unique_relationship (post_id, parent_id, child_id)
    );""")

    # Table for storing takedown notice details scraped from e621
    # Stores metadata about DMCA/takedown requests that led to post deletions
    cursor.execute("""CREATE TABLE IF NOT EXISTS takedowns (
        takedown_id INT PRIMARY KEY,
        reason TEXT,
        instructions TEXT,
        admin_notes TEXT,
        created_at DATETIME DEFAULT NULL,
        updated_at DATETIME DEFAULT NULL,
        finalized_at DATETIME DEFAULT NULL,
        status VARCHAR(50) DEFAULT NULL,
        scraped_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_updated DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        raw_html_snippet TEXT,
        INDEX idx_status (status),
        INDEX idx_finalized_at (finalized_at)
    );""")

    # Attempt to add admin_notes column if the table existed before this change
    try:
        cursor.execute("ALTER TABLE takedowns ADD COLUMN IF NOT EXISTS admin_notes TEXT DEFAULT NULL;")
    except Exception:
        # Fallback for MySQL versions < 8.0 (no IF NOT EXISTS). Silently ignore if column exists.
        try:
            cursor.execute("ALTER TABLE takedowns ADD COLUMN admin_notes TEXT DEFAULT NULL;")
        except Exception:
            pass

    # Junction table linking posts to takedown notices (many-to-many)
    # A single takedown can affect multiple posts
    cursor.execute("""CREATE TABLE IF NOT EXISTS post_takedowns (
        post_id INT,
        takedown_id INT,
        linked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(post_id, takedown_id),
        FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
        FOREIGN KEY (takedown_id) REFERENCES takedowns(takedown_id) ON DELETE CASCADE,
        INDEX idx_post_id (post_id),
        INDEX idx_takedown_id (takedown_id)
    );""")

    # Table for archiving posts removed from favorites before they are deleted from the posts table.
    # No FK to posts(id) since these rows outlive the post they describe.
    cursor.execute("""CREATE TABLE IF NOT EXISTS unfavorited_posts_archive (
        archive_id INT AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL,
        file_name VARCHAR(300),
        rating VARCHAR(20),
        ingested_at DATETIME,
        file_path VARCHAR(255),
        had_file TINYINT(1) DEFAULT 0,
        archived_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_post_id (post_id),
        INDEX idx_archived_at (archived_at)
    );""")

    # Archive AI-related deleted posts before they are removed from posts.
    # No FK to posts(id) since these rows intentionally outlive the post.
    cursor.execute("""CREATE TABLE IF NOT EXISTS ai_generated_posts_archive (
        archive_id INT AUTO_INCREMENT PRIMARY KEY,
        post_id INT NOT NULL UNIQUE,
        artist_id INT DEFAULT NULL,
        file_name VARCHAR(300),
        file_path VARCHAR(255),
        rating VARCHAR(20),
        deletion_reason TEXT,
        deleted_at DATETIME,
        ingested_at DATETIME,
        updated_at DATETIME,
        file_exists TINYINT(1) DEFAULT 0,
        snapshot_json LONGTEXT,
        archived_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_ai_artist_id (artist_id),
        INDEX idx_ai_archived_at (archived_at),
        INDEX idx_ai_deletion_reason (deletion_reason(255))
    );""")

    try:
        cursor.execute("ALTER TABLE ai_generated_posts_archive ADD COLUMN artist_id INT DEFAULT NULL;")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE ai_generated_posts_archive ADD COLUMN snapshot_json LONGTEXT;")
    except Exception:
        pass
    try:
        cursor.execute("CREATE INDEX idx_ai_artist_id ON ai_generated_posts_archive (artist_id);")
    except Exception:
        pass

    cursor.execute("""CREATE TABLE IF NOT EXISTS ai_flagged_artists (
        artist_flag_id INT AUTO_INCREMENT PRIMARY KEY,
        artist_id INT DEFAULT NULL,
        artist_name VARCHAR(100) NOT NULL UNIQUE,
        first_flagged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_flagged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        flag_count INT NOT NULL DEFAULT 1,
        INDEX idx_ai_flagged_artist_id (artist_id),
        INDEX idx_ai_flagged_last_seen (last_flagged_at)
    );""")

    # Migration: Drop old columns if they exist from previous schema
    try:
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'post_relationships'
            AND COLUMN_NAME IN ('has_children', 'has_active_children', 'children', 'parent_is_favorited')
        """)
        old_columns = cursor.fetchall()
        for col in old_columns:
            try:
                cursor.execute(f"ALTER TABLE post_relationships DROP COLUMN {col[0]}")
                logging.info(f"Dropped old column post_relationships.{col[0]}")
            except Exception as e:
                logging.debug(f"Could not drop column {col[0]}: {e}")
    except Exception as e:
        logging.debug(f"Could not check for old columns: {e}")

    conn.commit()
    cursor.close()
    logging.info("Database tables ensured.")

def get_existing_posts_info(conn):
    cursor = conn.cursor()
    query = "SELECT id, file_path FROM posts"
    cursor.execute(query)
    results = cursor.fetchall()
    cursor.close()
    return {row[0]: row[1] for row in results}

def get_post_artists(conn, post_id):
    """Get all artist names for a post from the database."""
    cursor = conn.cursor()
    try:
        query = """
        SELECT a.name
        FROM artists a
        JOIN posts_artists pa ON a.artist_id = pa.artist_id
        WHERE pa.post_id = %s
        ORDER BY a.name
        """
        cursor.execute(query, (post_id,))
        results = cursor.fetchall()
        return [row[0] for row in results]
    finally:
        cursor.close()

def extract_post_id_from_filename(filename):
    """
    Extract the post ID from a filename.
    
    Expected format: {artist}-{rating}-{post_id}.{ext}
    Returns the numeric post_id part, or None if extraction fails.
    
    Args:
        filename: The filename to parse (e.g., 'artist-explicit-12345.jpg')
        
    Returns:
        Integer post_id if successfully extracted, None otherwise
    """
    if not filename:
        return None
    
    # Remove extension
    base_name = os.path.splitext(filename)[0]
    
    # Split by dash
    parts = base_name.split('-')
    
    if len(parts) < 3:
        return None
    
    # The last part should be the post ID
    try:
        post_id = int(parts[-1])
        return post_id
    except (ValueError, IndexError):
        return None

def validate_post_id_in_filename(post_id, filename):
    """
    Validate that the post ID embedded in filename matches the actual post ID.
    
    Args:
        post_id: The actual post ID from database
        filename: The filename to validate
        
    Returns:
        tuple: (is_valid: bool, extracted_id: int or None, details: str)
    """
    extracted_id = extract_post_id_from_filename(filename)
    
    if extracted_id is None:
        return False, None, f"Could not extract post ID from filename: {filename}"
    
    if extracted_id == post_id:
        return True, extracted_id, f"Post ID matches: {post_id}"
    else:
        return False, extracted_id, f"Post ID mismatch: Database={post_id}, Filename={extracted_id}"

def log_artist_file_rename(conn, post_id, old_file_name, new_file_name, old_file_path, new_file_path, old_artists, new_artists, commit=True):
    """Log a file rename due to artist name changes."""
    cursor = conn.cursor()
    try:
        old_artist_str = "_".join(sorted(old_artists)) if old_artists else "unknown"
        new_artist_str = "_".join(sorted(new_artists)) if new_artists else "unknown"

        query = """
        INSERT INTO artist_file_renames
        (post_id, old_file_name, new_file_name, old_file_path, new_file_path, old_artist_names, new_artist_names)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(query, (post_id, old_file_name, new_file_name, old_file_path, new_file_path, old_artist_str, new_artist_str))

        if commit:
            conn.commit()
        logging.info(f"Logged artist rename for post {post_id}: {old_artist_str} -> {new_artist_str}")
    except Exception as e:
        logging.error(f"Failed to log artist file rename for post {post_id}: {e}")
        if commit:
            conn.rollback()
    finally:
        cursor.close()

def check_and_update_file_artist_names(conn, batch_limit=None, dry_run=False):
    """Check all posts with file_path and update file names if artist names don't match.

    Args:
        conn: Database connection
        batch_limit: Optional limit on number of posts to process
        dry_run: If True, only report what would be changed without making changes

    Returns:
        dict with statistics about the operation
    """
    import os

    cursor = conn.cursor(dictionary=True)
    stats = {
        "checked": 0,
        "renamed": 0,
        "failed": 0,
        "skipped_no_file": 0,
        "skipped_no_change": 0,
        "post_id_mismatch_skipped": 0,
        "post_id_extraction_failed": 0,
        "file_name_column_fixed": 0
    }

    try:
        # Get posts with file paths
        query = """
        SELECT p.id, p.file_name, p.file_path, p.rating, p.file_type
        FROM posts p
        WHERE p.file_path IS NOT NULL
        AND p.file_path != ''
        AND p.deleted = 0
        ORDER BY p.id
        """
        if batch_limit:
            query += f" LIMIT {batch_limit}"

        cursor.execute(query)
        posts = cursor.fetchall()

        logging.info(f"Checking {len(posts)} posts for artist name updates...")

        for post in posts:
            stats["checked"] += 1
            post_id = post["id"]
            current_file_path = post["file_path"]
            current_file_name = post["file_name"]
            rating = post["rating"]
            file_type = post["file_type"]

            # Check if file exists
            if not os.path.exists(current_file_path):
                logging.debug(f"Post {post_id}: File not found at {current_file_path}")
                stats["skipped_no_file"] += 1
                continue

            # Skip files from other sources (FurAffinity, DeviantArt, etc.)
            # These have their own naming conventions and shouldn't be renamed to e621 format
            current_base_name = os.path.basename(current_file_path)
            if any(indicator in current_base_name for indicator in ['-FA-', '-DA-', '-IB-', '-TW-', '-pixiv-']):
                logging.debug(f"Post {post_id}: Skipping non-e621 file: {current_base_name}")
                stats["skipped_no_change"] += 1
                continue

            # Get current artists from database
            current_artists = get_post_artists(conn, post_id)
            if not current_artists:
                current_artists = ["unknown"]

            # Filter out avoid_posting, conditional_dnp, unknown, and sound_warning
            excluded_artists = {"avoid_posting", "conditional_dnp", "unknown", "sound_warning"}
            current_artists = clean_artist_names(a for a in current_artists if a not in excluded_artists)
            if not current_artists:
                current_artists = ["unknown"]

            # Extract extension from current file
            _, extension = os.path.splitext(current_file_path)
            extension = extension.lstrip('.')

            # **VALIDATION: Check if post ID in filename matches database post ID**
            current_base_name = os.path.basename(current_file_path)
            is_valid, extracted_id, validation_msg = validate_post_id_in_filename(post_id, current_base_name)
            
            if not is_valid:
                if extracted_id is not None:
                    # A mismatched post ID means this file may belong to a different post.
                    logging.warning(f"Post {post_id}: {validation_msg} - SKIPPING RENAME")
                    logging.warning(f"  Filename: {current_base_name}")
                    logging.warning("  The filename ID does not match this database row, so this file is not treated as a safe rename candidate")
                    stats["post_id_mismatch_skipped"] += 1
                    stats["skipped_no_change"] += 1
                    continue
                else:
                    # Could not extract post ID - log and skip
                    logging.warning(f"Post {post_id}: {validation_msg} - SKIPPING RENAME")
                    stats["post_id_extraction_failed"] += 1
                    stats["skipped_no_change"] += 1
                    continue

            # Build expected file name
            rating_map = {"s": "safe", "q": "questionable", "e": "explicit"}
            rating_full = rating_map.get(rating.lower(), rating.lower())
            artist_part = "_".join(sorted(current_artists))
            expected_file_name = f"{artist_part}-{rating_full}-{post_id}"
            if extension:
                expected_file_name = f"{expected_file_name}.{extension}"
            expected_file_name = windows_safe_filename(expected_file_name)  # fff: invalid chars -> _

            # Check if file name needs updating
            if current_base_name == expected_file_name:
                # The physical file already has the right name, but the file_name column
                # can still be desynced from it (e.g. an ingest cycle overwrote it, or a
                # previous rename here updated the file but the DB write failed/rolled
                # back only partially before this fix). Repair the column in that case
                # without touching the file itself.
                if current_file_name != expected_file_name:
                    if dry_run:
                        logging.info(f"[DRY RUN] Post {post_id}: file_name column out of sync - DB has '{current_file_name}', disk has '{expected_file_name}' -> would fix column only")
                    else:
                        fix_cursor = conn.cursor()
                        try:
                            fix_cursor.execute(
                                "UPDATE posts SET file_name = %s WHERE id = %s",
                                (expected_file_name, post_id)
                            )
                            conn.commit()
                            logging.info(f"Post {post_id}: fixed out-of-sync file_name column ('{current_file_name}' -> '{expected_file_name}'), no rename needed")
                        finally:
                            fix_cursor.close()
                    stats["file_name_column_fixed"] += 1
                stats["skipped_no_change"] += 1
                continue

            # Extract old artist names from current file name for logging
            old_artists = []
            if current_base_name:
                # Try to extract artist part (everything before first dash)
                parts = current_base_name.split('-')
                if parts:
                    old_artist_part = parts[0]
                    old_artists = old_artist_part.split('_')

            # Build new file path
            directory = os.path.dirname(current_file_path)
            new_file_path = os.path.join(directory, expected_file_name)

            if dry_run:
                logging.info(f"[DRY RUN] Would rename post {post_id}:")
                logging.info(f"  Old: {current_base_name}")
                logging.info(f"  New: {expected_file_name}")
                logging.info(f"  Artists: {old_artists} -> {current_artists}")
                stats["renamed"] += 1
                continue

            # Perform the rename
            try:
                # Check if target already exists
                if os.path.exists(new_file_path):
                    logging.warning(f"Post {post_id}: Target file already exists: {new_file_path}")
                    stats["failed"] += 1
                    continue

                # Rename the file
                os.rename(current_file_path, new_file_path)

                # Update database
                update_cursor = conn.cursor()
                recorded = False
                try:
                    update_cursor.execute(
                        "UPDATE posts SET file_path = %s, file_name = %s WHERE id = %s",
                        (normalize_file_path(new_file_path), expected_file_name, post_id)
                    )
                    conn.commit()

                    # Log the rename
                    log_artist_file_rename(
                        conn, post_id,
                        current_base_name, expected_file_name,
                        current_file_path, new_file_path,
                        old_artists, current_artists,
                        commit=True
                    )

                    logging.info(f"Renamed post {post_id}: {current_base_name} -> {expected_file_name}")
                    stats["renamed"] += 1
                    recorded = True

                except Exception as db_error:
                    logging.error(f"Failed to update database for post {post_id}: {db_error}")
                    # Try to rename file back
                    try:
                        os.rename(new_file_path, current_file_path)
                    except Exception:
                        pass
                    stats["failed"] += 1
                finally:
                    update_cursor.close()

                if recorded:
                    # The sorter's row and the move log follow the rename. Outside the try above:
                    # nothing here may ever trigger its rename-back.
                    from fff.archive.post_files import record_rename
                    record_rename(conn, current_file_path, new_file_path, reason="artist_rename")

            except Exception as e:
                logging.error(f"Failed to rename file for post {post_id}: {e}")
                stats["failed"] += 1

        return stats

    finally:
        cursor.close()

def scan_for_post_id_mismatches(conn, batch_limit=None):
    """
    Scan all posts and identify files where the post ID in filename doesn't match database ID.
    
    This function ONLY reports issues without making changes. Mismatches should be reviewed
    manually because the file may belong to a different post.
    
    Args:
        conn: Database connection
        batch_limit: Optional limit on number of posts to check
        
    Returns:
        dict with:
            - 'checked': Total posts checked
            - 'valid': Posts with matching IDs
            - 'mismatched': Posts with ID mismatches (list of dicts with post_id, filename, extracted_id)
            - 'unreadable': Posts where ID couldn't be extracted
    """
    import os
    
    cursor = conn.cursor(dictionary=True)
    results = {
        "checked": 0,
        "valid": 0,
        "mismatched": [],
        "unreadable": []
    }
    
    try:
        query = """
        SELECT p.id, p.file_name, p.file_path
        FROM posts p
        WHERE p.file_path IS NOT NULL
        AND p.file_path != ''
        AND p.deleted = 0
        ORDER BY p.id
        """
        if batch_limit:
            query += f" LIMIT {batch_limit}"
        
        cursor.execute(query)
        posts = cursor.fetchall()
        
        logging.info(f"Scanning {len(posts)} posts for post ID mismatches in filenames...")
        
        for post in posts:
            results["checked"] += 1
            post_id = post["id"]
            file_name = post["file_name"] or os.path.basename(post["file_path"])
            
            is_valid, extracted_id, msg = validate_post_id_in_filename(post_id, file_name)
            
            if is_valid:
                results["valid"] += 1
            elif extracted_id is not None:
                results["mismatched"].append({
                    "post_id": post_id,
                    "filename": file_name,
                    "extracted_id": extracted_id,
                    "file_path": post["file_path"]
                })
                logging.warning(f"MISMATCH: Post {post_id} has filename ID {extracted_id}: {file_name}")
            else:
                results["unreadable"].append({
                    "post_id": post_id,
                    "filename": file_name,
                    "file_path": post["file_path"]
                })
                logging.warning(f"UNREADABLE: Post {post_id} - could not extract ID from: {file_name}")
        
        return results
    
    finally:
        cursor.close()

def get_deleted_posts(conn):
    """Get all posts marked as deleted with their deletion information."""
    cursor = conn.cursor()
    query = """
        SELECT id, file_path, file_name, deleted_at, deletion_reason, rating
        FROM posts
        WHERE deleted = 1
        ORDER BY deleted_at DESC
    """
    cursor.execute(query)
    results = cursor.fetchall()
    cursor.close()
    return results

def get_deleted_posts_count(conn):
    """Get count of deleted posts from posts table."""
    cursor = conn.cursor()
    query = "SELECT COUNT(*) FROM posts WHERE deleted = 1"
    cursor.execute(query)
    result = cursor.fetchone()
    cursor.close()
    return result[0] if result else 0

def archive_unfavorited_posts(conn, posts):
    """Record posts that are being removed from favorites in the unfavorited_posts_archive table.

    Must succeed before the corresponding posts are deleted from the posts table.
    """
    if not posts:
        return True

    cursor = conn.cursor()
    try:
        query = """
        INSERT INTO unfavorited_posts_archive
            (post_id, file_name, rating, ingested_at, file_path, had_file)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        values = [
            (
                post['id'],
                post.get('file_name'),
                post.get('rating'),
                post.get('ingested_at'),
                post.get('file_path'),
                1 if post.get('has_file') else 0,
            )
            for post in posts
        ]
        cursor.executemany(query, values)
        conn.commit()
        logging.info(f"Archived {len(posts)} unfavorited posts to unfavorited_posts_archive table")
        return True
    except Exception as e:
        logging.error(f"Failed to archive unfavorited posts to database: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()

def get_database_post_stats(conn):
    """Get comprehensive statistics about posts in the database."""
    cursor = conn.cursor()
    try:
        stats = {}

        # Total posts
        cursor.execute("SELECT COUNT(*) FROM posts")
        stats['total_posts'] = cursor.fetchone()[0]

        # Deleted posts
        cursor.execute("SELECT COUNT(*) FROM posts WHERE deleted = 1")
        stats['deleted_posts'] = cursor.fetchone()[0]

        # Active posts
        stats['active_posts'] = stats['total_posts'] - stats['deleted_posts']

        # Posts with files
        cursor.execute("SELECT COUNT(*) FROM posts WHERE file_path IS NOT NULL AND file_path != ''")
        stats['posts_with_files'] = cursor.fetchone()[0]

        # Rating breakdown
        cursor.execute("SELECT rating, COUNT(*) FROM posts GROUP BY rating")
        ratings = cursor.fetchall()
        stats['by_rating'] = {rating: count for rating, count in ratings}

        return stats
    except Exception as e:
        logging.error("Failed to get database stats: %s", e)
        return {}
    finally:
        cursor.close()

def insert_or_update_post(conn, post, commit: bool = True):
    # ...existing code for insert_or_update_post...
    post_id = post.get("id")
    file_url = _get_file_url(post)
    md5_hash = _get_file_md5(post)
    rating = post.get("rating", "unknown")
    ingested_at = post.get("ingested_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    last_checked = ingested_at
    local_file_path = post.get("local_file_path")
    original_file_type = post.get("file_type", "")  # may be unused now but keep for compatibility

    # Get created_at and updated_at from e621 API
    created_at = post.get("created_at")
    updated_at = post.get("updated_at")

    # Debug: Log if timestamps are missing or present
    if created_at is None:
        logging.warning(f"Post {post_id} is missing created_at field in API response")
    if updated_at is None:
        logging.warning(f"Post {post_id} is missing updated_at field in API response")

    # Convert ISO format timestamps to MySQL datetime format (and convert to local timezone)
    if created_at:
        try:
            from datetime import datetime as dt
            if isinstance(created_at, str):
                # Parse the ISO timestamp and convert to local timezone
                parsed_dt = dt.fromisoformat(created_at.replace('Z', '+00:00'))
                local_dt = parsed_dt.astimezone()
                created_at = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                logging.debug(f"Post {post_id} created_at: {created_at} (converted to local timezone)")
        except Exception as e:
            logging.warning(f"Could not parse created_at for post {post_id}: {e}")
            created_at = None

    if updated_at:
        try:
            from datetime import datetime as dt
            if isinstance(updated_at, str):
                # Parse the ISO timestamp and convert to local timezone
                parsed_dt = dt.fromisoformat(updated_at.replace('Z', '+00:00'))
                local_dt = parsed_dt.astimezone()
                updated_at = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                logging.debug(f"Post {post_id} updated_at: {updated_at} (converted to local timezone)")
        except Exception as e:
            logging.warning(f"Could not parse updated_at for post {post_id}: {e}")
            updated_at = None

    # Handle deleted posts
    is_deleted = post.get("flags", {}).get("deleted", False)
    deleted_at = None
    deletion_reason = None

    # For deleted posts, preserve an existing file_path only if it still points to a local file.
    if is_deleted and local_file_path is None:
        check_cursor = conn.cursor()
        check_cursor.execute("SELECT file_path FROM posts WHERE id = %s", (post_id,))
        existing = check_cursor.fetchone()
        if existing and existing[0]:
            resolved_file_path = resolve_existing_file_path(existing[0])
            if resolved_file_path:
                local_file_path = resolved_file_path
                logging.debug(f"Preserving existing file_path for deleted post {post_id}: {local_file_path}")
            else:
                logging.debug(f"Clearing stale file_path for deleted post {post_id}: {existing[0]}")
        check_cursor.close()

    # For deleted posts, use e621 post link as file_url if no current file_url exists
    if is_deleted and not file_url:
        file_url = f"https://e621.net/posts/{post_id}"
    if is_deleted:
        # Use the already-parsed updated_at value for deleted_at
        # (Don't re-fetch from post to avoid overwriting the parsed value)
        if updated_at:
            deleted_at = updated_at
            logging.info("Post %s is marked as deleted (deleted_at: %s)", post_id, deleted_at)
        else:
            # Fallback to current time if no updated_at
            deleted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logging.info("Post %s is marked as deleted (deleted_at: %s - fallback)", post_id, deleted_at)

        # Note: deletion_reason is not available in the API JSON
        # It will be fetched from the HTML page by backfill_deletion_status function
        # Check if post already has a deletion_reason we want to preserve
        deletion_reason = post.get("deletion_reason")  # This preserves existing reasons during updates

    # Get extension from API response first, then fall back to URL parsing
    extension = ''
    api_ext = _get_file_ext(post)
    if api_ext:
        extension = api_ext.lower()
    elif file_url:
        last_segment = file_url.split('/')[-1].split('?')[0]
        if '.' in last_segment:
            ext_candidate = last_segment.rsplit('.', 1)[-1].lower()
            if 1 <= len(ext_candidate) <= 10:
                extension = ext_candidate
    # Fallback: if extension still empty try original_file_type if it looks like an extension
    if not extension and original_file_type and 1 <= len(original_file_type) <= 10 and '.' not in original_file_type:
        extension = original_file_type.lower()

    def categorize(ext: str) -> str:
        if not ext:
            return 'unknown'
        video_exts = {"mp4", "webm", "mov", "mkv", "avi", "m4v"}
        image_exts = {"jpg", "jpeg", "png", "bmp", "webp", "avif", "heic", "heif", "tif", "tiff"}
        if ext == "gif":
            return "gif"
        if ext in video_exts:
            return "video"
        if ext in image_exts:
            return "image"
        if ext == "swf":
            return "flash"
        # For any other file type, return the extension itself
        return ext

    file_type_category = categorize(extension)

    # Build file_name: {artists}-{rating}-{postid}.{filetype}
    # rating should be full word already per API but map single letters just in case.
    rating_map = {"s": "safe", "q": "questionable", "e": "explicit"}
    rating_full = rating_map.get(rating.lower(), rating.lower())
    # tags may be a dict (v1 or v2+mode=extended) or a flat list (v2+mode=basic)
    _tags = post.get("tags", {})
    artists = (_tags.get("artist", []) if isinstance(_tags, dict) else []) or []
    # Filter out "avoid_posting", "conditional_dnp", "unknown", and "sound_warning" from artist names
    excluded_artists = {"avoid_posting", "conditional_dnp", "unknown", "sound_warning"}
    artists = clean_artist_names(artist for artist in artists if artist not in excluded_artists)
    if artists:
        artist_part = "_".join(artists)
    else:
        artist_part = "unknown"
    # extension already determined above
    file_name = f"{artist_part}-{rating_full}-{post_id}"
    if extension:
        file_name = f"{file_name}.{extension}"
    file_name = windows_safe_filename(file_name)  # fff: invalid chars -> _

    cursor = conn.cursor()
    query = """
    INSERT INTO posts (id, file_url, md5, media_hash, rating, ingested_at, file_path, file_type, file_name, deleted, deleted_at, deletion_reason, lastChecked, created_at, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      file_url = VALUES(file_url),
      md5 = CASE
              WHEN md5 IS NOT NULL AND md5 != '' AND file_path IS NOT NULL AND file_path != '' THEN md5
              ELSE VALUES(md5)
            END,
      media_hash = CASE
                     WHEN VALUES(media_hash) IS NULL THEN media_hash
                     ELSE VALUES(media_hash)
                   END,
      rating = VALUES(rating),
      file_path = CASE
                    WHEN VALUES(file_path) IS NULL THEN file_path
                    ELSE VALUES(file_path)
                  END,
      file_type = VALUES(file_type),
      file_name = CASE
                    WHEN file_path IS NOT NULL AND file_path != '' THEN file_name
                    ELSE VALUES(file_name)
                  END,
      deleted = VALUES(deleted),
      deleted_at = CASE
                     WHEN deleted_at IS NOT NULL THEN deleted_at
                     ELSE VALUES(deleted_at)
                   END,
            deletion_reason = CASE
                                                    WHEN VALUES(deletion_reason) IS NULL OR VALUES(deletion_reason) = '' THEN deletion_reason
                                                    ELSE VALUES(deletion_reason)
                                                END,
      lastChecked = VALUES(lastChecked),
      created_at = VALUES(created_at),
      updated_at = VALUES(updated_at);
    """
    media_hash = post.get("media_hash")
    cursor.execute(query, (post_id, file_url, md5_hash, media_hash, rating_full, ingested_at, local_file_path, file_type_category, file_name, is_deleted, deleted_at, deletion_reason, last_checked, created_at, updated_at))
    if commit:
        conn.commit()
    cursor.close()
    logging.info("Inserted/Updated post %s.", post_id)

def insert_or_update_posts_bulk(conn, posts, commit: bool = True):
    """Bulk insert/update multiple posts for better performance."""
    if not posts:
        return

    cursor = conn.cursor()

    # For deleted posts with no local_file_path, fetch existing file_path from database.
    # Only preserve paths that still resolve to a local file.
    deleted_post_ids = [p.get("id") for p in posts if p.get("flags", {}).get("deleted", False) and p.get("local_file_path") is None]
    existing_file_paths = {}
    if deleted_post_ids:
        placeholders = ','.join(['%s'] * len(deleted_post_ids))
        cursor.execute(f"SELECT id, file_path FROM posts WHERE id IN ({placeholders})", tuple(deleted_post_ids))
        existing_file_paths = {
            row[0]: resolved_file_path
            for row in cursor.fetchall()
            for resolved_file_path in [resolve_existing_file_path(row[1])]
            if resolved_file_path
        }
        if existing_file_paths:
            logging.debug(f"Preserving file_path for {len(existing_file_paths)} deleted posts in bulk update")

    post_values = []

    for post in posts:
        post_id = post.get("id")
        file_url = _get_file_url(post)
        md5_hash = _get_file_md5(post)
        rating = post.get("rating", "unknown")
        ingested_at = post.get("ingested_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        last_checked = ingested_at
        local_file_path = post.get("local_file_path")
        original_file_type = post.get("file_type", "")

        # Get created_at and updated_at from e621 API
        created_at = post.get("created_at")
        updated_at = post.get("updated_at")

        # Debug: Log first post in batch for verification
        if post_id and post == posts[0]:
            logging.info(f"First post in batch {post_id}: created_at={created_at}, updated_at={updated_at}")

        # Convert ISO format timestamps to MySQL datetime format (and convert to local timezone)
        if created_at:
            try:
                from datetime import datetime as dt
                if isinstance(created_at, str):
                    # Parse the ISO timestamp and convert to local timezone
                    parsed_dt = dt.fromisoformat(created_at.replace('Z', '+00:00'))
                    local_dt = parsed_dt.astimezone()
                    created_at = local_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception as e:
                logging.warning(f"Could not parse created_at for post {post_id}: {e}")
                created_at = None

        if updated_at:
            try:
                from datetime import datetime as dt
                if isinstance(updated_at, str):
                    # Parse the ISO timestamp and convert to local timezone
                    parsed_dt = dt.fromisoformat(updated_at.replace('Z', '+00:00'))
                    local_dt = parsed_dt.astimezone()
                    updated_at = local_dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception as e:
                logging.warning(f"Could not parse updated_at for post {post_id}: {e}")
                updated_at = None

        # Handle deleted posts
        is_deleted = post.get("flags", {}).get("deleted", False)
        deleted_at = None
        deletion_reason = None

        # For deleted posts, preserve existing file_path if available
        if is_deleted and local_file_path is None and post_id in existing_file_paths:
            local_file_path = existing_file_paths[post_id]

        # For deleted posts, use e621 post link as file_url if no current file_url exists
        if is_deleted and not file_url:
            file_url = f"https://e621.net/posts/{post_id}"

        if is_deleted:
            # Use the already-parsed updated_at value for deleted_at
            # (Don't re-fetch from post to avoid overwriting the parsed value)
            if updated_at:
                deleted_at = updated_at
            else:
                deleted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Note: deletion_reason is not available in the API JSON
            # It will be fetched from the HTML page by backfill_deletion_status function
            # Check if post already has a deletion_reason we want to preserve
            deletion_reason = post.get("deletion_reason")  # This preserves existing reasons during updates

        # File extension and categorization logic - prioritize API ext field
        extension = ''
        api_ext = _get_file_ext(post)
        if api_ext:
            extension = api_ext.lower()
        elif file_url:
            last_segment = file_url.split('/')[-1].split('?')[0]
            if '.' in last_segment:
                ext_candidate = last_segment.rsplit('.', 1)[-1].lower()
                if 1 <= len(ext_candidate) <= 10:
                    extension = ext_candidate
        if not extension and original_file_type and 1 <= len(original_file_type) <= 10 and '.' not in original_file_type:
            extension = original_file_type.lower()

        def categorize(ext: str) -> str:
            if not ext:
                return 'unknown'
            video_exts = {"mp4", "webm", "mov", "mkv", "avi", "m4v"}
            image_exts = {"jpg", "jpeg", "png", "bmp", "webp", "avif", "heic", "heif", "tif", "tiff"}
            if ext == "gif":
                return "gif"
            if ext in video_exts:
                return "video"
            if ext in image_exts:
                return "image"
            if ext == "swf":
                return "flash"
            # For any other file type, return the extension itself
            return ext

        file_type_category = categorize(extension)

        # Build file_name
        rating_map = {"s": "safe", "q": "questionable", "e": "explicit"}
        rating_full = rating_map.get(rating.lower(), rating.lower())
        _tags = post.get("tags", {})
        artists = (_tags.get("artist", []) if isinstance(_tags, dict) else []) or []
        # Filter out "avoid_posting", "conditional_dnp", "unknown", and "sound_warning" from artist names
        excluded_artists = {"avoid_posting", "conditional_dnp", "unknown", "sound_warning"}
        artists = clean_artist_names(artist for artist in artists if artist not in excluded_artists)
        artist_part = "_".join(artists) if artists else "unknown"
        file_name = f"{artist_part}-{rating_full}-{post_id}"
        if extension:
            file_name = f"{file_name}.{extension}"
        file_name = windows_safe_filename(file_name)  # fff: invalid chars -> _

        media_hash = post.get("media_hash")
        post_values.append((post_id, file_url, md5_hash, media_hash, rating_full, ingested_at, local_file_path,
                          file_type_category, file_name, is_deleted, deleted_at, deletion_reason, last_checked, created_at, updated_at))

    # Bulk insert with ON DUPLICATE KEY UPDATE
    query = """
    INSERT INTO posts (id, file_url, md5, media_hash, rating, ingested_at, file_path, file_type, file_name, deleted, deleted_at, deletion_reason, lastChecked, created_at, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      file_url = VALUES(file_url),
      md5 = CASE
              WHEN md5 IS NOT NULL AND md5 != '' AND file_path IS NOT NULL AND file_path != '' THEN md5
              ELSE VALUES(md5)
            END,
      media_hash = CASE
                     WHEN VALUES(media_hash) IS NULL THEN media_hash
                     ELSE VALUES(media_hash)
                   END,
      rating = VALUES(rating),
      file_path = CASE
                    WHEN VALUES(file_path) IS NULL THEN file_path
                    ELSE VALUES(file_path)
                  END,
      file_type = VALUES(file_type),
      file_name = CASE
                    WHEN file_path IS NOT NULL AND file_path != '' THEN file_name
                    ELSE VALUES(file_name)
                  END,
      deleted = VALUES(deleted),
      deleted_at = CASE
                     WHEN deleted_at IS NOT NULL THEN deleted_at
                     ELSE VALUES(deleted_at)
                   END,
            deletion_reason = CASE
                                                    WHEN VALUES(deletion_reason) IS NULL OR VALUES(deletion_reason) = '' THEN deletion_reason
                                                    ELSE VALUES(deletion_reason)
                                                END,
      lastChecked = VALUES(lastChecked),
      created_at = VALUES(created_at),
      updated_at = VALUES(updated_at);
    """
    # md5 above: a post that has a local file keeps the md5 it was stored with, the md5 of the
    # file it has. Overwriting it with e621's current md5 here would hide a file e621 has since
    # replaced; fff.archive.metadata_sync compares the two and replaces the file. (file_path in
    # that CASE is still the old value: md5 is assigned before file_path.)

    cursor.executemany(query, post_values)
    if commit:
        conn.commit()
    cursor.close()
    logging.info("Bulk inserted/updated %s posts.", len(posts))

def insert_artist(conn, artist_name, commit: bool = True):
    cursor = conn.cursor()
    query = "INSERT IGNORE INTO artists (name) VALUES (%s)"
    cursor.execute(query, (artist_name,))
    if commit:
        conn.commit()
    query = "SELECT artist_id FROM artists WHERE name = %s"
    cursor.execute(query, (artist_name,))
    result = cursor.fetchone()
    cursor.close()
    return result[0] if result else None

def link_post_artist(conn, post_id, artist_id, commit: bool = True):
    cursor = conn.cursor()
    query = "INSERT IGNORE INTO posts_artists (post_id, artist_id) VALUES (%s, %s)"
    cursor.execute(query, (post_id, artist_id))
    if commit:
        conn.commit()
    cursor.close()

def insert_tag(conn, tag_name, tag_type, commit: bool = True):
    cursor = conn.cursor()
    query = "INSERT IGNORE INTO tags (name, tag_type) VALUES (%s, %s)"
    cursor.execute(query, (tag_name, tag_type))
    if commit:
        conn.commit()
    query = "SELECT tag_id FROM tags WHERE name = %s"
    cursor.execute(query, (tag_name,))
    result = cursor.fetchone()
    cursor.close()
    return result[0] if result else None

def link_post_tag(conn, post_id, tag_id, commit: bool = True):
    cursor = conn.cursor()
    query = "INSERT IGNORE INTO posts_tags (post_id, tag_id) VALUES (%s, %s)"
    cursor.execute(query, (post_id, tag_id))
    if commit:
        conn.commit()
    cursor.close()

def _has_tag_list(post) -> bool:
    """True when the post carries a real e621 tag dict (mode=extended).

    Stale cached responses can carry a flat list (mode=basic) or nothing; pruning links
    against those would wipe a post's artists/tags, so pruning only runs when this is True.
    """
    tags_dict = post.get("tags")
    return isinstance(tags_dict, dict) and any(tags_dict.values())


def _prune_post_links(cursor, table, column, post_id, current_ids):
    """Delete ``table`` links for ``post_id`` whose ``column`` isn't in ``current_ids``."""
    if current_ids:
        placeholders = ','.join(['%s'] * len(current_ids))
        cursor.execute(
            f"DELETE FROM {table} WHERE post_id = %s AND {column} NOT IN ({placeholders})",
            (post_id, *current_ids)
        )
    else:
        cursor.execute(f"DELETE FROM {table} WHERE post_id = %s", (post_id,))


def process_artists_and_tags(conn, post, commit: bool = True):
    post_id = post.get("id")
    tags_dict = post.get("tags", {})
    # v2 mode=basic returns tags as a flat list; mode=extended (which we request) returns a dict.
    # Guard against flat lists to avoid crashing on stale cached responses.
    prune = _has_tag_list(post)
    if not isinstance(tags_dict, dict):
        tags_dict = {}
    current_artist_ids = []
    for artist in tags_dict.get("artist", []):
        artist_id = insert_artist(conn, artist, commit=False)
        if artist_id:
            current_artist_ids.append(artist_id)
            link_post_artist(conn, post_id, artist_id, commit=False)

    current_tag_ids = []
    for category, tag_list in tags_dict.items():
        if category == "artist":
            continue
        tag_type = get_tag_type(category)
        for tag in tag_list:
            tag_id = insert_tag(conn, tag, tag_type, commit=False)
            if tag_id:
                current_tag_ids.append(tag_id)
                link_post_tag(conn, post_id, tag_id, commit=False)

    # posts_artists/posts_tags are otherwise insert-only, so artists and tags e621 has
    # since removed off this post would linger forever (stale filenames from
    # get_post_artists(), stale blacklist/tag-sort matches). Prune anything not in the
    # current tag list - but only when we actually have one.
    if prune:
        prune_cursor = conn.cursor()
        _prune_post_links(prune_cursor, "posts_artists", "artist_id", post_id, current_artist_ids)
        _prune_post_links(prune_cursor, "posts_tags", "tag_id", post_id, current_tag_ids)
        prune_cursor.close()
    if commit:
        conn.commit()

def process_artists_and_tags_bulk(conn, posts_data, commit: bool = True):
    """Bulk process artists and tags for multiple posts at once for better performance."""
    if not posts_data:
        return

    # Collect all unique artists and tags first
    all_artists = set()
    all_tags = {}  # tag_name -> tag_type

    for post in posts_data:
        tags_dict = post.get("tags", {})
        if not isinstance(tags_dict, dict):
            tags_dict = {}
        # Collect artists
        for artist in tags_dict.get("artist", []):
            all_artists.add(artist)
        # Collect tags by type
        for category, tag_list in tags_dict.items():
            if category == "artist":
                continue
            tag_type = get_tag_type(category)
            for tag in tag_list:
                all_tags[tag] = tag_type

    # Bulk insert artists
    cursor = conn.cursor()
    if all_artists:
        artist_values = [(artist,) for artist in all_artists]
        cursor.executemany("INSERT IGNORE INTO artists (name) VALUES (%s)", artist_values)

    # Bulk insert tags
    if all_tags:
        tag_values = [(tag, tag_type) for tag, tag_type in all_tags.items()]
        cursor.executemany("INSERT IGNORE INTO tags (name, tag_type) VALUES (%s, %s)", tag_values)

    # Get all artist and tag IDs in bulk
    artist_name_to_id = {}
    if all_artists:
        format_strings = ','.join(['%s'] * len(all_artists))
        cursor.execute(f"SELECT name, artist_id FROM artists WHERE name IN ({format_strings})", tuple(all_artists))
        artist_name_to_id = {row[0]: row[1] for row in cursor.fetchall()}

    tag_name_to_id = {}
    if all_tags:
        format_strings = ','.join(['%s'] * len(all_tags))
        cursor.execute(f"SELECT name, tag_id FROM tags WHERE name IN ({format_strings})", tuple(all_tags.keys()))
        tag_name_to_id = {row[0]: row[1] for row in cursor.fetchall()}

    # Bulk insert post-artist and post-tag relationships
    post_artist_links = []
    post_tag_links = []

    for post in posts_data:
        post_id = post.get("id")
        tags_dict = post.get("tags", {})
        if not isinstance(tags_dict, dict):
            tags_dict = {}

        # Prepare artist links
        for artist in tags_dict.get("artist", []):
            if artist in artist_name_to_id:
                post_artist_links.append((post_id, artist_name_to_id[artist]))

        # Prepare tag links
        for category, tag_list in tags_dict.items():
            if category == "artist":
                continue
            for tag in tag_list:
                if tag in tag_name_to_id:
                    post_tag_links.append((post_id, tag_name_to_id[tag]))

    # Execute bulk inserts for relationships
    if post_artist_links:
        cursor.executemany("INSERT IGNORE INTO posts_artists (post_id, artist_id) VALUES (%s, %s)", post_artist_links)

    if post_tag_links:
        cursor.executemany("INSERT IGNORE INTO posts_tags (post_id, tag_id) VALUES (%s, %s)", post_tag_links)

    # posts_artists/posts_tags are otherwise insert-only, so artists and tags e621 has
    # since removed off a post would linger forever (stale filenames from
    # get_post_artists(), stale blacklist/tag-sort matches). Prune anything not in each
    # post's current tag list - only for posts that came with a real tag list.
    current_artists_by_post = {}
    for post_id, artist_id in post_artist_links:
        current_artists_by_post.setdefault(post_id, set()).add(artist_id)
    current_tags_by_post = {}
    for post_id, tag_id in post_tag_links:
        current_tags_by_post.setdefault(post_id, set()).add(tag_id)

    batch_post_ids = [post.get("id") for post in posts_data if _has_tag_list(post)]
    if batch_post_ids:
        placeholders = ','.join(['%s'] * len(batch_post_ids))
        for table, column, current in (("posts_artists", "artist_id", current_artists_by_post),
                                       ("posts_tags", "tag_id", current_tags_by_post)):
            cursor.execute(
                f"SELECT post_id, {column} FROM {table} WHERE post_id IN ({placeholders})",
                tuple(batch_post_ids)
            )
            stale_links = [
                (post_id, link_id) for post_id, link_id in cursor.fetchall()
                if link_id not in current.get(post_id, set())
            ]
            if stale_links:
                cursor.executemany(
                    f"DELETE FROM {table} WHERE post_id = %s AND {column} = %s",
                    stale_links
                )
                logging.info("Removed %d stale %s link(s)", len(stale_links), table)

    if commit:
        conn.commit()
    cursor.close()


def sync_changed_post_tags(conn, posts_data, batch_size: int = 500) -> int:
    """Re-link artists/tags for posts whose e621 tags differ from the database.

    For posts the sync otherwise skips (file already downloaded): compares each post's
    e621 tag list with its stored links and runs process_artists_and_tags_bulk (which adds
    new links and prunes removed ones) only on the posts that changed. Returns that count.
    """
    posts = [p for p in posts_data if p.get("id") and _has_tag_list(p)]
    changed_total = 0
    for i in range(0, len(posts), batch_size):
        batch = posts[i:i + batch_size]
        ids = tuple(p["id"] for p in batch)
        placeholders = ','.join(['%s'] * len(ids))
        stored_tags, stored_artists = {}, {}
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"SELECT pt.post_id, t.name FROM posts_tags pt JOIN tags t ON t.tag_id = pt.tag_id "
                f"WHERE pt.post_id IN ({placeholders})", ids)
            for post_id, name in cursor.fetchall():
                stored_tags.setdefault(post_id, set()).add(name)
            cursor.execute(
                f"SELECT pa.post_id, a.name FROM posts_artists pa JOIN artists a ON a.artist_id = pa.artist_id "
                f"WHERE pa.post_id IN ({placeholders})", ids)
            for post_id, name in cursor.fetchall():
                stored_artists.setdefault(post_id, set()).add(name)
        finally:
            cursor.close()

        changed = []
        for post in batch:
            tags_dict = post["tags"]
            artists = set(tags_dict.get("artist", []))
            tags = {t for category, names in tags_dict.items() if category != "artist" for t in names}
            if tags != stored_tags.get(post["id"], set()) or artists != stored_artists.get(post["id"], set()):
                changed.append(post)
        if changed:
            process_artists_and_tags_bulk(conn, changed, commit=False)
            conn.commit()
            changed_total += len(changed)
    if changed_total:
        logging.info("Updated tags for %d already-downloaded post(s) whose e621 tags changed", changed_total)
    else:
        logging.info("No tag changes for already-downloaded posts")
    return changed_total

def insert_post_sources(conn, post, commit: bool = True):
    """Insert sources for a post. Ensures no duplicates via PK constraint.
    Expects the post JSON to contain a top-level key 'sources' that is a list of strings.
    For deleted posts, automatically adds an e621 post link.
    """
    post_id = post.get("id")
    if not post_id:
        return
    sources = post.get("sources") or []
    if not isinstance(sources, list):
        sources = []

    # Check if post is deleted and add e621 link if so
    is_deleted = post.get("flags", {}).get("deleted", False)
    if is_deleted:
        e621_link = f"https://e621.net/posts/{post_id}"
        if e621_link not in sources:
            sources.append(e621_link)

    if not sources:
        return
    cursor = conn.cursor()
    for src in sources:
        if not src or not isinstance(src, str):
            continue
        # Truncate overly long URLs just in case (schema allows 1000 chars)
        src_trim = src[:1000]
        try:
            cursor.execute("INSERT IGNORE INTO source_links (post_id, source_link) VALUES (%s, %s)", (post_id, src_trim))
        except Exception as e:
            logging.error("Failed inserting source link for post %s: %s", post_id, e)
    if commit:
        conn.commit()
    cursor.close()

def insert_post_sources_bulk(conn, posts_data, commit: bool = True):
    """Bulk insert sources for multiple posts for better performance.
    For deleted posts, automatically adds an e621 post link.
    """
    if not posts_data:
        return

    source_values = []
    for post in posts_data:
        post_id = post.get("id")
        if not post_id:
            continue
        sources = post.get("sources") or []
        if not isinstance(sources, list):
            sources = []

        # Check if post is deleted and add e621 link if so
        is_deleted = post.get("flags", {}).get("deleted", False)
        if is_deleted:
            e621_link = f"https://e621.net/posts/{post_id}"
            if e621_link not in sources:
                sources.append(e621_link)

        if not sources:
            continue

        for src in sources:
            if not src or not isinstance(src, str):
                continue
            src_trim = src[:1000]  # Truncate overly long URLs
            source_values.append((post_id, src_trim))

    if source_values:
        cursor = conn.cursor()
        try:
            cursor.executemany("INSERT IGNORE INTO source_links (post_id, source_link) VALUES (%s, %s)", source_values)
            if commit:
                conn.commit()
            logging.info("Bulk inserted %s source links.", len(source_values))
        except Exception as e:
            logging.error("Failed bulk inserting source links: %s", e)
        finally:
            cursor.close()

def insert_pool(conn, pool_id, pool_info=None, commit: bool = True):
    """Insert or update a pool record.

    Args:
        pool_id: The e621 pool ID
        pool_info: Optional dict with pool metadata (name, description, etc.)
                   If None, just inserts the pool_id with minimal info
    """
    cursor = conn.cursor()
    try:
        if pool_info:
            query = """
                INSERT INTO pools (pool_id, name, description, created_at, updated_at, post_count, is_active, category)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    name = VALUES(name),
                    description = VALUES(description),
                    updated_at = VALUES(updated_at),
                    post_count = VALUES(post_count),
                    is_active = VALUES(is_active),
                    category = VALUES(category)
            """
            cursor.execute(query, (
                pool_id,
                pool_info.get("name"),
                pool_info.get("description"),
                pool_info.get("created_at"),
                pool_info.get("updated_at"),
                pool_info.get("post_count", 0),
                pool_info.get("is_active", 1),
                pool_info.get("category")
            ))
        else:
            # Just insert the pool ID with minimal info
            query = "INSERT IGNORE INTO pools (pool_id) VALUES (%s)"
            cursor.execute(query, (pool_id,))

        if commit:
            conn.commit()
    except Exception as e:
        logging.error("Failed inserting pool %s: %s", pool_id, e)
    finally:
        cursor.close()

def link_post_pool(conn, post_id, pool_id, commit: bool = True):
    """Link a post to a pool."""
    cursor = conn.cursor()
    try:
        query = "INSERT IGNORE INTO posts_pools (post_id, pool_id) VALUES (%s, %s)"
        cursor.execute(query, (post_id, pool_id))
        if commit:
            conn.commit()
    except Exception as e:
        logging.error("Failed linking post %s to pool %s: %s", post_id, pool_id, e)
    finally:
        cursor.close()

def process_pools_bulk(conn, posts_data, commit: bool = True):
    """Bulk process pools for multiple posts at once for better performance.

    This function:
    1. Collects all unique pool IDs from posts
    2. Inserts any new pools
    3. Creates post-pool relationships
    """
    if not posts_data:
        return

    # Collect all unique pool IDs
    all_pool_ids = set()
    for post in posts_data:
        pools = post.get("pools", [])
        if isinstance(pools, list):
            all_pool_ids.update(pools)

    if not all_pool_ids:
        return

    cursor = conn.cursor()
    try:
        # Bulk insert pool IDs (minimal info - just the ID)
        pool_values = [(pool_id,) for pool_id in all_pool_ids]
        cursor.executemany("INSERT IGNORE INTO pools (pool_id) VALUES (%s)", pool_values)

        # Bulk insert post-pool relationships
        post_pool_links = []
        for post in posts_data:
            post_id = post.get("id")
            if not post_id:
                continue
            pools = post.get("pools", [])
            if isinstance(pools, list):
                for pool_id in pools:
                    post_pool_links.append((post_id, pool_id))

        if post_pool_links:
            cursor.executemany("INSERT IGNORE INTO posts_pools (post_id, pool_id) VALUES (%s, %s)", post_pool_links)

        if commit:
            conn.commit()

        logging.info("Bulk processed %s pools, created %s post-pool links", len(all_pool_ids), len(post_pool_links))
    except Exception as e:
        logging.error("Failed bulk processing pools: %s", e)
    finally:
        cursor.close()

def process_pools(conn, post, commit: bool = True):
    """Process pools for a single post.

    This function:
    1. Extracts pool IDs from the post
    2. Inserts any new pools
    3. Creates post-pool relationships
    """
    post_id = post.get("id")
    if not post_id:
        return

    pools = post.get("pools", [])
    if not isinstance(pools, list) or not pools:
        return

    cursor = conn.cursor()
    try:
        # Insert pool IDs
        for pool_id in pools:
            cursor.execute("INSERT IGNORE INTO pools (pool_id) VALUES (%s)", (pool_id,))
            cursor.execute("INSERT IGNORE INTO posts_pools (post_id, pool_id) VALUES (%s, %s)", (post_id, pool_id))

        if commit:
            conn.commit()
    except Exception as e:
        logging.error("Failed processing pools for post %s: %s", post_id, e)
    finally:
        cursor.close()


def upsert_pools_metadata_bulk(conn, pool_metadata_list, commit: bool = True):
    """Upsert pool metadata from a list of e621 pools API response dicts."""
    if not pool_metadata_list:
        return

    cursor = conn.cursor()
    try:
        query = """
            INSERT INTO pools (pool_id, name, description, created_at, updated_at, post_count, is_active, category)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name = VALUES(name),
                description = VALUES(description),
                updated_at = VALUES(updated_at),
                post_count = VALUES(post_count),
                is_active = VALUES(is_active),
                category = VALUES(category)
        """
        values = [
            (
                pool["id"],
                pool.get("name"),
                pool.get("description"),
                pool.get("created_at"),
                pool.get("updated_at"),
                pool.get("post_count", 0),
                1 if pool.get("is_active", True) else 0,
                pool.get("category"),
            )
            for pool in pool_metadata_list
        ]
        cursor.executemany(query, values)
        if commit:
            conn.commit()
        logging.info("Upserted metadata for %s pools", len(values))
    except Exception as e:
        logging.error("Failed upserting pool metadata: %s", e)
    finally:
        cursor.close()


def get_pools_missing_metadata(conn, limit=None):
    """Return pool IDs whose name has never been fetched (name IS NULL)."""
    cursor = conn.cursor()
    try:
        query = "SELECT pool_id FROM pools WHERE name IS NULL ORDER BY pool_id"
        if limit:
            query += f" LIMIT {limit}"
        cursor.execute(query)
        return [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()


def insert_post_relationships(conn, post, commit: bool = True):
    """Insert parent-child relationships for a post.
    Expects the post JSON to contain a top-level key 'relationships' with parent_id
    and children array. Creates one row per relationship.
    """
    post_id = post.get("id")
    if not post_id:
        return

    relationships = post.get("relationships")
    if not relationships:
        return

    parent_id = relationships.get("parent_id")
    children = relationships.get("children", [])

    cursor = conn.cursor()
    try:
        # First, delete existing relationships for this post to avoid duplicates
        cursor.execute("DELETE FROM post_relationships WHERE post_id = %s", (post_id,))

        # Insert parent relationship if exists
        if parent_id:
            cursor.execute("""
                INSERT IGNORE INTO post_relationships (post_id, parent_id, child_id)
                VALUES (%s, %s, NULL)
            """, (post_id, parent_id))

        # Insert child relationships
        for child_id in children:
            cursor.execute("""
                INSERT IGNORE INTO post_relationships (post_id, parent_id, child_id)
                VALUES (%s, NULL, %s)
            """, (post_id, child_id))

        if commit:
            conn.commit()
    except Exception as e:
        logging.error("Failed inserting relationships for post %s: %s", post_id, e)
    finally:
        cursor.close()

def insert_post_relationships_bulk(conn, posts_data, commit: bool = True):
    """Bulk insert relationships for multiple posts for better performance.
    Creates one row per parent-child relationship.
    """
    if not posts_data:
        return

    cursor = conn.cursor()
    try:
        # Collect all post IDs to delete existing relationships
        post_ids = [post.get("id") for post in posts_data if post.get("id")]

        if post_ids:
            # Delete existing relationships for these posts
            placeholders = ",".join(["%s"] * len(post_ids))
            cursor.execute(f"DELETE FROM post_relationships WHERE post_id IN ({placeholders})", tuple(post_ids))

        # Collect all relationship rows to insert
        relationship_values = []

        for post in posts_data:
            post_id = post.get("id")
            if not post_id:
                continue

            relationships = post.get("relationships")
            if not relationships:
                continue

            parent_id = relationships.get("parent_id")
            children = relationships.get("children", [])

            # Add parent relationship
            if parent_id:
                relationship_values.append((post_id, parent_id, None))

            # Add child relationships
            for child_id in children:
                relationship_values.append((post_id, None, child_id))

        if relationship_values:
            query = """
                INSERT IGNORE INTO post_relationships (post_id, parent_id, child_id)
                VALUES (%s, %s, %s)
            """
            cursor.executemany(query, relationship_values)
            if commit:
                conn.commit()
            logging.info("Bulk inserted %s post relationship rows.", len(relationship_values))
    except Exception as e:
        logging.error("Failed bulk inserting post relationships: %s", e)
    finally:
        cursor.close()

def backfill_missing_sources(conn, e621_config, batch_limit: int | None = None):
    """Backfill missing source links for posts that currently have none in source_links.

    For each post id in posts without any rows in source_links, fetch the post data from temp
    response files (cached from favorites API) and insert its sources. No API calls needed.
    Uses bulk processing for better performance.
    """
    # Lazy import to avoid circular dependencies
    try:
        from fff.archive.api import get_post_data_from_temp
    except Exception:
        logging.error("Could not import get_post_data_from_temp function")
        return 0

    cursor = conn.cursor()
    query = """
        SELECT p.id FROM posts p
        LEFT JOIN (
            SELECT DISTINCT post_id FROM source_links
        ) sl ON p.id = sl.post_id
        WHERE sl.post_id IS NULL
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()
    missing_ids = [r[0] for r in rows]
    if batch_limit is not None:
        missing_ids = missing_ids[:batch_limit]
    if not missing_ids:
        logging.info("No posts missing source links to backfill.")
        return 0

    logging.info("Backfilling sources for %s posts using cached response data...", len(missing_ids))

    # Get bulk processing batch size from config
    bulk_batch_size = e621_config.get("bulk_processing_batch_size", 500)

    inserted_total = 0
    skipped_no_sources = 0
    skipped_not_found = 0
    posts_with_sources = []

    for idx, post_id in enumerate(missing_ids, start=1):
        try:
            data = get_post_data_from_temp(post_id, e621_config)
            if not data:
                logging.debug("No cached data found for post %s", post_id)
                skipped_not_found += 1
                continue

            # Check if post has sources before adding to batch
            sources = data.get("sources") or []
            if not sources or not isinstance(sources, list) or len(sources) == 0:
                skipped_no_sources += 1
                continue

            posts_with_sources.append(data)

            # Process batch when it reaches the configured size
            if len(posts_with_sources) >= bulk_batch_size:
                insert_post_sources_bulk(conn, posts_with_sources, commit=True)
                inserted_total += len(posts_with_sources)
                logging.info("Backfilled sources for %s posts so far...", inserted_total)
                posts_with_sources = []  # Reset batch

        except Exception as e:
            logging.error("Error backfilling sources for post %s: %s", post_id, e)

    # Process any remaining posts in the final batch
    if posts_with_sources:
        insert_post_sources_bulk(conn, posts_with_sources, commit=True)
        inserted_total += len(posts_with_sources)

    logging.info("Completed backfilling sources for %s posts. Skipped %s posts with no sources, %s posts not found in cache.",
                 inserted_total, skipped_no_sources, skipped_not_found)
    return inserted_total

def backfill_missing_file_paths(conn, mysql_config, batch_limit: int | None = None):
    """
    Backfill missing file_path values by checking the image_sorting_hashes database.

    For posts where file_path IS NULL, attempt to find the file in the image_sorting_hashes
    database using the MD5 hash. If found and the file exists, update the file_path.

    Args:
        conn: Database connection to e621_posts_db
        mysql_config: MySQL configuration dict (for connecting to image_sorting_hashes)
        batch_limit: Optional limit on number of posts to process

    Returns:
        Number of posts updated with file paths
    """
    if not mysql_config:
        logging.warning("No mysql_config provided, cannot check image_sorting_hashes database")
        return 0

    # Check if image_sorting_hashes database is available
    test_conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not test_conn:
        logging.info("image_sorting_hashes database not available, skipping file path backfill")
        return 0
    test_conn.close()

    cursor = conn.cursor()
    try:
        # Get posts with NULL file_path but have MD5 hash.
        # Deleted posts are included so they can recover a correct local path when one exists.
        query = """
        SELECT id, md5, file_name
        FROM posts
        WHERE file_path IS NULL
        AND md5 IS NOT NULL
        AND md5 != ''
        """

        if batch_limit:
            query += f" LIMIT {batch_limit}"

        cursor.execute(query)
        posts_without_path = cursor.fetchall()

        if not posts_without_path:
            logging.info("No posts with missing file_path to backfill from image_sorting_hashes")
            return 0

        logging.info(f"Found {len(posts_without_path)} posts with missing file_path, checking image_sorting_hashes database...")

        updated_count = 0
        not_found_count = 0

        update_cursor = conn.cursor()

        for post_id, md5_hash, file_name in posts_without_path:
            try:
                # First, try to find file path by e621_post_id
                file_path = get_file_path_from_image_sorting_hashes_by_post_id(mysql_config, post_id)

                # If not found by post_id, fall back to MD5 hash lookup
                if not file_path:
                    file_path = get_file_path_from_image_sorting_hashes(mysql_config, md5_hash)

                if file_path:
                    # Update the post with the found file_path
                    update_cursor.execute(
                        "UPDATE posts SET file_path = %s WHERE id = %s",
                        (normalize_file_path(file_path), post_id)
                    )
                    updated_count += 1
                    logging.info(f"Updated file_path for post {post_id}: {file_path}")
                else:
                    not_found_count += 1
                    logging.debug(f"No file path found for post {post_id} (MD5: {md5_hash})")

                # Commit every 100 updates for safety
                if updated_count % 100 == 0 and updated_count > 0:
                    conn.commit()
                    logging.info(f"Progress: Updated {updated_count} posts so far...")

            except Exception as e:
                logging.error(f"Error processing post {post_id}: {e}")
                continue

        # Final commit
        conn.commit()
        update_cursor.close()

        logging.info(f"Completed backfilling file paths: {updated_count} updated, {not_found_count} not found")
        return updated_count

    except Exception as e:
        logging.error(f"Error in backfill_missing_file_paths: {e}")
        return 0
    finally:
        cursor.close()

# More missing files than this in one run (or 2% of the paths checked) means the library is not
# all there (a drive offline, a folder renamed), not that that many files went away.
MAX_MISSING_PATHS = 200


def _library_root(path: str) -> str:
    """The drive (or share) and first folder of a path: ``P:\\IPL`` for ``P:\\IPL\\Pictures\\a.png``."""
    drive, rest = os.path.splitdrive(os.path.normpath(path))
    first = rest.lstrip("\\/").split(os.sep, 1)[0]
    return os.path.join(drive + os.sep, first) if first else drive + os.sep


def verify_and_repair_file_paths(conn, mysql_config, batch_limit: int | None = None,
                                 clear_missing: bool = True, dry_run: bool = False):
    """Check that every recorded posts.file_path exists, and repair the ones that do not.

    A missing file is looked up in the sorter database (by post id, then by MD5). Found: the
    path is updated. Not found: with ``clear_missing`` the path is set to NULL so the sync
    downloads the file again; otherwise it is left as it is.

    Nothing is cleared when the library looks absent rather than files gone:
    - a missing file whose drive or top folder does not exist (an unplugged drive, a renamed
      folder) is left alone;
    - more than ``MAX_MISSING_PATHS`` missing files (or 2% of the paths checked) stops the
      repair before it changes anything;
    - if the sorter database cannot be reached at the end, no path is cleared (a failed lookup
      would otherwise read as "not found").

    Returns counts: checked, missing, repaired, not_found (cleared, or kept when not clearing),
    skipped (left alone because the library looked absent), and aborted (True when the
    threshold stopped it).
    """
    stats = {'checked': 0, 'missing': 0, 'repaired': 0, 'not_found': 0, 'skipped': 0, 'aborted': False}
    if not mysql_config:
        logging.warning("No mysql_config provided, cannot check image_sorting_hashes database")
        return stats

    def sorter_reachable():
        test_conn = connect_to_image_sorting_hashes_db(mysql_config)
        if not test_conn:
            return False
        test_conn.close()
        return True

    if not sorter_reachable():
        logging.info("image_sorting_hashes database not available, skipping file path verification")
        return stats

    cursor = conn.cursor()
    try:
        query = "SELECT id, file_path, md5 FROM posts WHERE file_path IS NOT NULL AND file_path != ''"
        if batch_limit:
            query += f" LIMIT {int(batch_limit)}"
        cursor.execute(query)
        posts_with_paths = cursor.fetchall()
        if not posts_with_paths:
            logging.info("No posts with file_path to verify")
            return stats
        logging.info(f"Verifying file paths for {len(posts_with_paths)} posts...")

        # Pass 1, disk only: which recorded files are there.
        updates, missing = [], []
        root_present = {}
        for post_id, file_path, md5_hash in posts_with_paths:
            stats['checked'] += 1
            try:
                resolved = resolve_existing_file_path(file_path)
            except OSError:
                resolved = None
            if resolved == file_path:
                continue
            if resolved:      # the same file with another extension
                updates.append((normalize_file_path(resolved), post_id))
                logging.info(f"Updated file_path for post {post_id} to file with different extension: "
                             f"{resolved} (was: {file_path})")
                stats['repaired'] += 1
                continue
            root = _library_root(file_path)
            if root not in root_present:
                root_present[root] = os.path.isdir(root)
                if not root_present[root]:
                    logging.error(f"Library folder {root} is not reachable; leaving the paths under it as they are")
            if not root_present[root]:
                stats['skipped'] += 1
                continue
            missing.append((post_id, file_path, md5_hash))

        stats['missing'] = len(missing) + stats['skipped']
        limit = max(MAX_MISSING_PATHS, stats['checked'] // 50)
        if len(missing) > limit:
            logging.error(f"{len(missing)} recorded files are missing, more than the {limit} a run will repair. "
                          "Nothing was changed: check that the library drives and folders are all there.")
            stats.update(aborted=True, repaired=0)
            return stats

        # Pass 2: look the missing ones up in the sorter database.
        clears = []
        for post_id, file_path, md5_hash in missing:
            logging.warning(f"File not found for post {post_id} at recorded path: {file_path}")
            try:
                new_file_path = get_file_path_from_image_sorting_hashes_by_post_id(mysql_config, post_id)
                if not new_file_path and md5_hash:
                    new_file_path = get_file_path_from_image_sorting_hashes(mysql_config, md5_hash)
            except Exception as e:
                logging.error(f"Error looking up post {post_id}: {e}")
                stats['skipped'] += 1
                continue
            if new_file_path:
                updates.append((normalize_file_path(new_file_path), post_id))
                stats['repaired'] += 1
                logging.info(f"Repaired file_path for post {post_id}: {new_file_path}")
            else:
                clears.append((post_id,))
                stats['not_found'] += 1

        if clears and clear_missing and not sorter_reachable():
            logging.error(f"image_sorting_hashes database stopped answering; not clearing the paths of "
                          f"{len(clears)} posts whose files were not found")
            stats['skipped'] += len(clears)
            stats['not_found'] = 0
            clears = []

        if dry_run:
            logging.info(f"[DRY RUN] Would repair {stats['repaired']} paths"
                         + (f" and clear {len(clears)}" if clear_missing else ""))
        else:
            update_cursor = conn.cursor()
            try:
                if updates:
                    update_cursor.executemany("UPDATE posts SET file_path = %s WHERE id = %s", updates)
                if clears and clear_missing:
                    update_cursor.executemany("UPDATE posts SET file_path = NULL WHERE id = %s", clears)
                    for (post_id,) in clears:
                        logging.warning(f"Could not find file for post {post_id} in image_sorting_hashes database "
                                        "- cleared file_path for redownload")
                conn.commit()
            finally:
                update_cursor.close()

        logging.info("File path verification complete:")
        logging.info(f"  Checked: {stats['checked']} posts")
        logging.info(f"  Missing files: {stats['missing']}")
        logging.info(f"  Repaired paths: {stats['repaired']}")
        logging.info(f"  Could not repair: {stats['not_found']}" + ("" if clear_missing else " (paths kept)"))
        if stats['skipped']:
            logging.info(f"  Left alone (library folder unreachable or lookup failed): {stats['skipped']}")
        return stats

    except Exception as e:
        conn.rollback()
        logging.error(f"Error in verify_and_repair_file_paths: {e}")
        return {'checked': 0, 'missing': 0, 'repaired': 0, 'not_found': 0, 'skipped': 0, 'aborted': False}
    finally:
        cursor.close()


def get_or_compute_e621_perceptual_hash(conn, post_id, file_url, e621_config, temp_dir="temp_downloads"):
    """
    Get perceptual hash for an e621 post from cache or compute it.

    Priority order:
    1. Check if hash is already cached in database
    2. Check if post has a local file - use that file
    3. Download from e621 temporarily

    Args:
        conn: Database connection
        post_id: E621 post ID
        file_url: URL to download the file from
        e621_config: E621 configuration for downloading
        temp_dir: Directory for temporary downloads

    Returns:
        Perceptual hash string or None if failed
    """
    import tempfile

    # Check if we have a cached perceptual hash
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT perceptual_hash FROM e621_perceptual_hashes WHERE post_id = %s",
            (post_id,)
        )
        result = cursor.fetchone()
        if result:
            perceptual_hash = result[0]
            logging.debug(f"Using cached perceptual hash for post {post_id}: {perceptual_hash}")
            return perceptual_hash
    except Exception as e:
        logging.debug(f"Error checking cache for post {post_id}: {e}")
    finally:
        cursor.close()

    # Check if post has a local file we can use
    cursor = conn.cursor()
    local_file_path = None
    try:
        cursor.execute(
            "SELECT file_path FROM posts WHERE id = %s AND file_path IS NOT NULL AND file_path != ''",
            (post_id,)
        )
        result = cursor.fetchone()
        if result and result[0]:
            potential_path = result[0]
            if os.path.exists(potential_path):
                local_file_path = potential_path
                logging.debug(f"Found local file for post {post_id}: {local_file_path}")
    except Exception as e:
        logging.debug(f"Error checking for local file: {e}")
    finally:
        cursor.close()

    # Try to generate perceptual hash using media_hasher
    try:
        from fff.hashing import archive_hasher as MediaHasher  # fff: archive hash profile
    except ImportError:
        logging.error("MediaHasher not available, cannot compute perceptual hash")
        return None

    hasher = MediaHasher()

    # If we have a local file, use it
    if local_file_path:
        try:
            logging.info(f"Computing perceptual hash for post {post_id} from local file")
            perceptual_hash = hasher.hash_file(local_file_path)

            if perceptual_hash:
                # Cache the hash with algorithm info
                cache_cursor = conn.cursor()
                try:
                    cache_cursor.execute(
                        """INSERT INTO e621_perceptual_hashes (post_id, perceptual_hash, hash_algorithm, file_url, cached_at)
                           VALUES (%s, %s, %s, %s, NOW())
                           ON DUPLICATE KEY UPDATE
                           perceptual_hash = VALUES(perceptual_hash),
                           hash_algorithm = VALUES(hash_algorithm),
                           file_url = VALUES(file_url),
                           cached_at = NOW()""",
                        (post_id, perceptual_hash, hasher.image_hash_mode, file_url or '')
                    )
                    conn.commit()
                    logging.info(f"Cached perceptual hash for post {post_id} from local file: {perceptual_hash}")
                except Exception as e:
                    logging.error(f"Failed to cache perceptual hash for post {post_id}: {e}")
                finally:
                    cache_cursor.close()

                return perceptual_hash
        except Exception as e:
            logging.warning(f"Failed to compute hash from local file for post {post_id}: {e}")
            # Fall through to download method

    # Need to download and compute hash
    if not file_url or file_url == '':
        logging.warning(f"No file URL for post {post_id}, cannot compute perceptual hash")
        return None

    # Create temp directory if needed
    os.makedirs(temp_dir, exist_ok=True)

    try:
        # Import required modules
        try:
            from fff.archive.download import api_get
        except Exception:
            logging.error("Could not import api_get for downloading")
            return None

        # Try to generate perceptual hash using media_hasher if available
        try:
            from fff.hashing import archive_hasher as MediaHasher  # fff: archive hash profile
        except ImportError:
            logging.error("MediaHasher not available, cannot compute perceptual hash")
            return None

        # Download file to temp location
        with tempfile.NamedTemporaryFile(delete=False, dir=temp_dir, suffix=os.path.splitext(file_url)[1]) as tmp_file:
            temp_path = tmp_file.name

        try:
            headers = {"User-Agent": e621_config.get("user_agent", "e621 archive/1.0")}
            logging.info(f"Downloading post {post_id} temporarily for perceptual hashing...")

            response = api_get(file_url, headers=headers, stream=True)
            if response.status_code == 200:
                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                logging.debug(f"Downloaded post {post_id} to {temp_path}")
            else:
                logging.error(f"Failed to download post {post_id}: HTTP {response.status_code}")
                return None

            # Compute perceptual hash
            hasher = MediaHasher()
            perceptual_hash = hasher.hash_file(temp_path)

            if perceptual_hash:
                # Cache the hash with algorithm info
                cache_cursor = conn.cursor()
                try:
                    cache_cursor.execute(
                        """INSERT INTO e621_perceptual_hashes (post_id, perceptual_hash, hash_algorithm, file_url, cached_at)
                           VALUES (%s, %s, %s, %s, NOW())
                           ON DUPLICATE KEY UPDATE
                           perceptual_hash = VALUES(perceptual_hash),
                           hash_algorithm = VALUES(hash_algorithm),
                           file_url = VALUES(file_url),
                           cached_at = NOW()""",
                        (post_id, perceptual_hash, hasher.image_hash_mode, file_url)
                    )
                    conn.commit()
                    logging.info(f"Cached perceptual hash for post {post_id}: {perceptual_hash}")
                except Exception as e:
                    logging.error(f"Failed to cache perceptual hash for post {post_id}: {e}")
                finally:
                    cache_cursor.close()

                return perceptual_hash
            else:
                logging.warning(f"Failed to compute perceptual hash for post {post_id}")
                return None

        finally:
            # Clean up temp file
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                    logging.debug(f"Cleaned up temp file: {temp_path}")
                except Exception as e:
                    logging.warning(f"Could not delete temp file {temp_path}: {e}")

    except Exception as e:
        logging.error(f"Error computing perceptual hash for post {post_id}: {e}")
        return None

def find_file_by_perceptual_hash(mysql_config, perceptual_hash, similarity_threshold=0.95):
    """
    Search for a file in image_sorting_hashes database by perceptual hash similarity.

    Args:
        mysql_config: MySQL configuration dict
        perceptual_hash: Perceptual hash to search for
        similarity_threshold: Minimum similarity (0.0-1.0) to consider a match

    Returns:
        Tuple of (file_path, similarity_score) or (None, None) if not found
    """
    conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not conn:
        return None, None

    cursor = conn.cursor()
    try:
        # Check if we can use imagehash for similarity comparison
        try:
            import imagehash
        except ImportError:
            logging.error("imagehash library not available for perceptual hash comparison")
            return None, None

        # Get the hash type (assuming average hash)
        try:
            query_hash = imagehash.hex_to_hash(perceptual_hash)
        except Exception as e:
            logging.error(f"Invalid perceptual hash format: {e}")
            return None, None

        # Get tables to check
        ihd = (mysql_config or {}).get("image_hash_db", {})
        tables_to_check = ihd.get(
            "tables",
            [
                "favorites",
                "manual_sort_images",
                "manual_sort_videos",
                "sfw_image",
                "sfw_video",
                "sfw_gif",
                "nsfw_image",
                "nsfw_video",
                "nsfw_gif",
                "archive_explicit_images",
                "archive_explicit_videos",
                "archive_explicit_gifs",
                "archive_sfw_images",
                "archive_sfw_videos",
                "archive_sfw_gifs",
                "other_directories",
            ],
        )

        best_match = None
        best_similarity = 0.0

        # Search each table for perceptual hashes
        for table in tables_to_check:
            try:
                # Try to find file_hash column (perceptual hash)
                query = f"SELECT file_path, file_hash FROM {table} WHERE file_hash IS NOT NULL AND file_hash != ''"
                cursor.execute(query)
                results = cursor.fetchall()

                for file_path, stored_hash in results:
                    try:
                        # Convert stored hash and compare
                        stored_hash_obj = imagehash.hex_to_hash(stored_hash)

                        # Calculate similarity (1 - normalized hamming distance)
                        hamming_distance = query_hash - stored_hash_obj
                        max_distance = len(query_hash.hash.flatten())
                        similarity = 1.0 - (hamming_distance / max_distance)

                        if similarity >= similarity_threshold and similarity > best_similarity:
                            # Verify file exists
                            if os.path.exists(file_path):
                                best_match = file_path
                                best_similarity = similarity
                                logging.debug(f"Found similar file in {table}: {file_path} (similarity: {similarity:.2%})")

                    except Exception as e:
                        logging.debug(f"Error comparing hash for {file_path}: {e}")
                        continue

            except Exception as e:
                logging.debug(f"Error checking table {table} for perceptual hashes: {e}")
                continue

        if best_match:
            logging.info(f"Found matching file by perceptual hash: {best_match} (similarity: {best_similarity:.2%})")
            return best_match, best_similarity

        return None, None

    except Exception as e:
        logging.error(f"Error searching for perceptual hash: {e}")
        return None, None
    finally:
        cursor.close()
        conn.close()

def verify_and_repair_with_perceptual_hash(conn, mysql_config, e621_config, batch_limit=None, similarity_threshold=0.95):
    """
    Verify file paths using perceptual hashing for posts where MD5 matching failed.

    This handles cases where you have a higher quality version of the file.
    Downloads e621 posts temporarily to compute perceptual hashes, then compares
    against local files.

    Args:
        conn: Database connection
        mysql_config: MySQL configuration
        e621_config: E621 configuration
        batch_limit: Optional limit on posts to check
        similarity_threshold: Minimum similarity (0.0-1.0) for match

    Returns:
        Dict with statistics
    """
    cursor = conn.cursor()
    try:
        # Get posts with NULL file_path or missing files
        query = """
        SELECT id, file_url, md5, file_name, file_path
        FROM posts
        WHERE deleted = 0
        AND file_type IN ('image', 'gif')
        AND file_url IS NOT NULL
        AND file_url != ''
        """

        if batch_limit:
            query += f" LIMIT {batch_limit}"

        cursor.execute(query)
        posts = cursor.fetchall()

        if not posts:
            logging.info("No posts to check with perceptual hashing")
            return {'checked': 0, 'computed_hashes': 0, 'repaired': 0, 'not_found': 0}

        logging.info(f"Checking {len(posts)} posts with perceptual hashing...")
        logging.info(f"Similarity threshold: {similarity_threshold:.0%}")

        stats = {
            'checked': 0,
            'computed_hashes': 0,
            'repaired': 0,
            'not_found': 0,
            'skipped_has_file': 0
        }

        update_cursor = conn.cursor()

        for post_id, file_url, md5_hash, file_name, file_path in posts:
            stats['checked'] += 1

            try:
                # Skip if file already exists at path
                if file_path and os.path.exists(file_path):
                    stats['skipped_has_file'] += 1
                    if stats['checked'] % 100 == 0:
                        logging.info(f"Progress: {stats['checked']}/{len(posts)} posts checked...")
                    continue

                # Get or compute perceptual hash for e621 post
                perceptual_hash = get_or_compute_e621_perceptual_hash(conn, post_id, file_url, e621_config)

                if not perceptual_hash:
                    stats['not_found'] += 1
                    continue

                stats['computed_hashes'] += 1

                # Search for similar file in image_sorting_hashes
                matched_path, similarity = find_file_by_perceptual_hash(mysql_config, perceptual_hash, similarity_threshold)

                if matched_path:
                    # Update the post with the found file_path
                    update_cursor.execute(
                        "UPDATE posts SET file_path = %s WHERE id = %s",
                        (matched_path, post_id)
                    )
                    stats['repaired'] += 1
                    logging.info(f"Matched post {post_id} by perceptual hash (similarity: {similarity:.2%}): {matched_path}")
                else:
                    stats['not_found'] += 1

                # Progress and commit
                if stats['checked'] % 10 == 0:
                    logging.info(f"Progress: {stats['checked']}/{len(posts)} posts checked, {stats['repaired']} matched")

                if stats['repaired'] % 50 == 0 and stats['repaired'] > 0:
                    conn.commit()
                    logging.info(f"Committed {stats['repaired']} repairs...")

            except Exception as e:
                logging.error(f"Error processing post {post_id}: {e}")
                continue

        # Final commit
        conn.commit()
        update_cursor.close()

        logging.info("Perceptual hash verification complete:")
        logging.info(f"  Posts checked: {stats['checked']}")
        logging.info(f"  Skipped (has file): {stats['skipped_has_file']}")
        logging.info(f"  Hashes computed: {stats['computed_hashes']}")
        logging.info(f"  Files matched: {stats['repaired']}")
        logging.info(f"  Not matched: {stats['not_found']}")

        return stats

    except Exception as e:
        logging.error(f"Error in perceptual hash verification: {e}")
        return {'checked': 0, 'computed_hashes': 0, 'repaired': 0, 'not_found': 0, 'skipped_has_file': 0}
    finally:
        cursor.close()

def fetch_deletion_reason_from_page(post_id, user_agent, auth=None):
    """
    Fetch deletion reason from the e621 post page HTML.

    Args:
        post_id: The e621 post ID
        user_agent: User agent string for the request
        auth: Optional tuple of (username, api_key) for authentication

    Returns:
        String containing the deletion reason, or None if not found/not deleted
    """
    import requests
    from bs4 import BeautifulSoup
    import re

    url = f"https://e621.net/posts/{post_id}"

    try:
        headers = {"User-Agent": user_agent}
        response = http.get("e621", url, headers=headers, auth=auth, timeout=30)

        if response.status_code != 200:
            logging.debug(f"Failed to fetch page for post {post_id}: HTTP {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # Look for the deletion notice: <div class="notice notice-deleted">
        notice = soup.find('div', class_='notice-deleted')

        if notice:
            # Find the list item with [DELETION]
            li_items = notice.find_all('li')

            for li in li_items:
                # Preserve anchor text with spacing so post IDs remain readable
                raw_text = li.get_text(" ", strip=True)

                if '[DELETION]' not in raw_text:
                    continue

                # Detect duplicate/inferior references that often include a linked post ID
                if 'duplicate' in raw_text.lower():
                    link = li.find('a', href=re.compile(r"/posts/\d+"))
                    post_match = re.search(r"/posts/(\d+)", link.get('href', '')) if link else None
                    linked_post_id = post_match.group(1) if post_match else None
                    if linked_post_id:
                        return f"Inferior version/duplicate of post #{linked_post_id}"

                # Extract text after the takedown link and colon when present
                reason = None
                if ':' in raw_text:
                    parts = raw_text.split(':', 1)
                    if len(parts) > 1:
                        reason = parts[1].strip()

                # Fallback: strip known prefixes and moderator suffixes
                if not reason:
                    reason = raw_text

                # Remove the leading marker and takedown metadata
                reason = re.sub(r"\[DELETION\]\s*", "", reason)
                reason = re.sub(r"takedown\s*#?\d+[:\-]?\s*", "", reason, flags=re.IGNORECASE)

                # Clean up moderator/timestamp tail
                if ' - ' in reason:
                    reason = reason.split(' - ', 1)[0].strip()

                # Collapse whitespace
                reason = ' '.join(reason.split())

                if reason:
                    logging.debug(f"Found deletion reason for post {post_id}: {reason}")
                    return reason[:500]

            # Fallback: if no structured reason found, return cleaned notice text
            text = notice.get_text(strip=True)
            # Remove the header text
            text = text.replace('This post was deleted or flagged for the following reasons:', '').strip()
            if text and len(text) > 10:
                logging.debug(f"Found deletion notice for post {post_id} (generic)")
                return text[:500]  # Limit length

        # Alternative: Look for any notice with deletion info
        notice_alt = soup.find('div', class_='notice')
        if notice_alt and 'deleted' in notice_alt.get_text().lower():
            text = notice_alt.get_text(strip=True)
            if ':' in text and 'Reason:' in text:
                reason = text.split('Reason:', 1)[1].strip()
                reason = reason.split('\n')[0].strip()
                if reason:
                    logging.debug(f"Found deletion reason for post {post_id} (alt method): {reason}")
                    return reason

        logging.debug(f"No deletion reason found for post {post_id}")
        return None

    except requests.exceptions.Timeout:
        logging.warning(f"Timeout fetching deletion reason for post {post_id}")
        return None
    except requests.exceptions.RequestException as e:
        logging.warning(f"Request error fetching deletion reason for post {post_id}: {e}")
        return None
    except Exception as e:
        logging.warning(f"Error fetching deletion reason for post {post_id}: {e}")
        return None


def fetch_takedown_details_from_page(post_id, user_agent, auth=None):
    """
    Fetch structured takedown notice details from e621 post page HTML.

    Args:
        post_id: The e621 post ID
        user_agent: User agent string for the request
        auth: Optional tuple of (username, api_key) for authentication

    Returns:
        Dictionary with takedown details:
        {
            'takedown_id': int or None,
            'reason': str or None,
            'instructions': str or None,
            'admin_notes': str or None,
            'finalized_at': datetime or None,
            'raw_html': str or None,
            'deletion_type': str ('takedown', 'duplicate', 'other')
        }
        Returns None if no deletion info found or on error.
    """
    import requests
    from bs4 import BeautifulSoup
    import re

    url = f"https://e621.net/posts/{post_id}"

    try:
        headers = {"User-Agent": user_agent}
        response = http.get("e621", url, headers=headers, auth=auth, timeout=30)

        if response.status_code != 200:
            logging.debug(f"Failed to fetch page for post {post_id}: HTTP {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # Look for the deletion notice: <div class="notice notice-deleted">
        notice = soup.find('div', class_='notice-deleted')

        if not notice:
            # Try alternative selectors
            notice = soup.find('div', class_='notice')
            if not notice or 'deleted' not in notice.get_text().lower():
                logging.debug(f"No deletion notice found for post {post_id}")
                return None

        # Store raw HTML snippet for future re-parsing
        raw_html = str(notice)[:5000]  # Limit to 5KB

        # Initialize result structure
        result = {
            'takedown_id': None,
            'reason': None,
            'instructions': None,
            'admin_notes': None,
            'finalized_at': None,
            'raw_html': raw_html,
            'deletion_type': 'other'
        }

        # Find all list items in the notice
        li_items = notice.find_all('li')

        for li in li_items:
            raw_text = li.get_text(" ", strip=True)

            # Skip non-deletion items
            if '[DELETION]' not in raw_text:
                continue

            # Extract takedown ID if present (e.g., "takedown #12345")
            takedown_match = re.search(r'takedown\s*#?(\d+)', raw_text, re.IGNORECASE)
            if takedown_match:
                result['takedown_id'] = int(takedown_match.group(1))
                result['deletion_type'] = 'takedown'

            # Extract deletion timestamp from <time> tag (preferred) or text fallback
            # The HTML contains: <time datetime="2026-01-27T13:23-05:00" title="2026-01-27 13:23:29 -0500">
            time_tag = li.find('time')
            if time_tag and not result['finalized_at']:
                # Try datetime attribute first (ISO 8601 format)
                datetime_attr = time_tag.get('datetime')
                if datetime_attr:
                    try:
                        from datetime import datetime
                        # Parse ISO 8601 with timezone (e.g., "2026-01-27T13:23-05:00")
                        # Python 3.7+ supports parsing with timezone via fromisoformat
                        result['finalized_at'] = datetime.fromisoformat(datetime_attr.replace('Z', '+00:00'))
                        logging.debug(f"Extracted deletion timestamp from <time> tag for post {post_id}: {result['finalized_at']}")
                    except (ValueError, AttributeError) as e:
                        logging.debug(f"Failed to parse datetime attribute '{datetime_attr}' for post {post_id}: {e}")
                
                # Fallback: Try title attribute (format: "2026-01-27 13:23:29 -0500")
                if not result['finalized_at']:
                    title_attr = time_tag.get('title')
                    if title_attr:
                        try:
                            from datetime import datetime
                            # Parse "YYYY-MM-DD HH:MM:SS -TZOFFSET" format
                            # Strip timezone for simpler parsing (we lose tz info but get the timestamp)
                            timestamp_part = title_attr.rsplit('-', 1)[0].strip() if '-' in title_attr else title_attr
                            timestamp_part = timestamp_part.rsplit('+', 1)[0].strip() if '+' in timestamp_part else timestamp_part
                            result['finalized_at'] = datetime.strptime(timestamp_part, '%Y-%m-%d %H:%M:%S')
                            logging.debug(f"Extracted deletion timestamp from title attribute for post {post_id}: {result['finalized_at']}")
                        except (ValueError, AttributeError) as e:
                            logging.debug(f"Failed to parse title attribute '{title_attr}' for post {post_id}: {e}")
            
            # Text fallback: Extract from "... - Username on YYYY-MM-DD HH:MM:SS" format
            if not result['finalized_at']:
                timestamp_match = re.search(
                    r'-\s+\w+\s+on\s+(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}:\d{2})?)',
                    raw_text
                )
                if timestamp_match:
                    timestamp_str = timestamp_match.group(1)
                    try:
                        from datetime import datetime
                        # Try to parse with time first
                        if ' ' in timestamp_str:
                            result['finalized_at'] = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                        else:
                            # Date only, set time to 00:00:00
                            result['finalized_at'] = datetime.strptime(timestamp_str, '%Y-%m-%d')
                        logging.debug(f"Extracted deletion timestamp from text for post {post_id}: {result['finalized_at']}")
                    except ValueError as e:
                        logging.debug(f"Failed to parse timestamp '{timestamp_str}' for post {post_id}: {e}")

            # Check for duplicate/inferior deletions
            if 'duplicate' in raw_text.lower() or 'inferior' in raw_text.lower():
                result['deletion_type'] = 'duplicate'
                # Try to find linked post
                link = li.find('a', href=re.compile(r"/posts/\d+"))
                if link:
                    post_match = re.search(r"/posts/(\d+)", link.get('href', ''))
                    if post_match:
                        linked_post_id = post_match.group(1)
                        result['reason'] = f"Inferior version/duplicate of post #{linked_post_id}"

            # Extract full reason text if not already set
            if not result['reason']:
                # Try to extract text after colon
                if ':' in raw_text:
                    parts = raw_text.split(':', 1)
                    if len(parts) > 1:
                        reason = parts[1].strip()
                    else:
                        reason = raw_text
                else:
                    reason = raw_text

                # Clean up the reason text
                reason = re.sub(r"\[DELETION\]\s*", "", reason)
                reason = re.sub(r"takedown\s*#?\d+[:\-]?\s*", "", reason, flags=re.IGNORECASE)

                # Remove moderator/timestamp suffix (format: "reason - Username on 2024-01-01")
                if ' - ' in reason:
                    reason = reason.split(' - ', 1)[0].strip()

                # Collapse whitespace
                reason = ' '.join(reason.split())

                if reason and len(reason) > 5:
                    result['reason'] = reason[:1000]  # Limit to 1000 chars

            # Look for instruction text (sometimes in nested elements)
            instruction_elem = li.find('small') or li.find('em')
            if instruction_elem:
                instructions = instruction_elem.get_text(" ", strip=True)
                if instructions and len(instructions) > 10:
                    result['instructions'] = instructions[:1000]

        # Look for admin notes (usually in separate list items)
        for li in li_items:
            raw_text = li.get_text(" ", strip=True)

            # Check for admin note markers
            if '[ADMIN' in raw_text.upper() or 'ADMIN NOTE' in raw_text.upper():
                # Extract admin note text
                admin_note = raw_text

                # Clean up markers and prefixes
                admin_note = re.sub(r"\[ADMIN[\s_]*NOTE\]\s*", "", admin_note, flags=re.IGNORECASE)
                admin_note = re.sub(r"Admin\s*Note:\s*", "", admin_note, flags=re.IGNORECASE)

                # Remove moderator/timestamp suffix
                if ' - ' in admin_note:
                    admin_note = admin_note.split(' - ', 1)[0].strip()

                # Collapse whitespace
                admin_note = ' '.join(admin_note.split())

                if admin_note and len(admin_note) > 5:
                    # Append to existing admin notes if any
                    if result['admin_notes']:
                        result['admin_notes'] += " | " + admin_note[:1000]
                    else:
                        result['admin_notes'] = admin_note[:1000]
                    logging.debug(f"Found admin note for post {post_id}: {admin_note[:50]}...")

        # If we found any useful information, return it
        if result['takedown_id'] or result['reason']:
            logging.debug(f"Found takedown details for post {post_id}: takedown_id={result['takedown_id']}, type={result['deletion_type']}")
            return result

        # Fallback: generic deletion notice
        text = notice.get_text(strip=True)
        text = text.replace('This post was deleted or flagged for the following reasons:', '').strip()
        if text and len(text) > 10:
            result['reason'] = text[:1000]
            logging.debug(f"Found generic deletion notice for post {post_id}")
            return result

        logging.debug(f"No structured takedown data found for post {post_id}")
        return None

    except requests.exceptions.Timeout:
        logging.warning(f"Timeout fetching takedown details for post {post_id}")
        return None
    except requests.exceptions.RequestException as e:
        logging.warning(f"Request error fetching takedown details for post {post_id}: {e}")
        return None
    except Exception as e:
        logging.warning(f"Error fetching takedown details for post {post_id}: {e}")
        return None


def insert_or_update_takedown(conn, takedown_data):
    """
    Insert or update a takedown record in the database.

    Args:
        conn: Database connection
        takedown_data: Dictionary with keys:
            - takedown_id (required): The e621 takedown ID
            - reason: Takedown reason text
            - instructions: Additional instructions or notes
            - admin_notes: Administrative notes from moderators
            - created_at: When takedown was created (if known)
            - updated_at: When takedown was last updated (if known)
            - finalized_at: When takedown was finalized (if known)
            - status: Takedown status (e.g., 'processed', 'finalized')
            - raw_html_snippet: Raw HTML for future re-parsing

    Returns:
        True if successful, False otherwise
    """
    if not takedown_data or 'takedown_id' not in takedown_data:
        logging.warning("Cannot insert takedown: missing takedown_id")
        return False

    cursor = conn.cursor()
    try:
        query = """
            INSERT INTO takedowns (
                takedown_id, reason, instructions, admin_notes, created_at, updated_at,
                finalized_at, status, raw_html_snippet, scraped_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                reason = COALESCE(VALUES(reason), reason),
                instructions = COALESCE(VALUES(instructions), instructions),
                admin_notes = COALESCE(VALUES(admin_notes), admin_notes),
                created_at = COALESCE(VALUES(created_at), created_at),
                updated_at = COALESCE(VALUES(updated_at), updated_at),
                finalized_at = COALESCE(VALUES(finalized_at), finalized_at),
                status = COALESCE(VALUES(status), status),
                raw_html_snippet = COALESCE(VALUES(raw_html_snippet), raw_html_snippet),
                last_updated = NOW()
        """

        values = (
            takedown_data['takedown_id'],
            takedown_data.get('reason'),
            takedown_data.get('instructions'),
            takedown_data.get('admin_notes'),
            takedown_data.get('created_at'),
            takedown_data.get('updated_at'),
            takedown_data.get('finalized_at'),
            takedown_data.get('status'),
            takedown_data.get('raw_html_snippet')
        )

        cursor.execute(query, values)
        conn.commit()
        logging.debug(f"Inserted/updated takedown {takedown_data['takedown_id']}")
        return True

    except Exception as e:
        logging.error(f"Error inserting/updating takedown {takedown_data.get('takedown_id')}: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()


def link_post_to_takedown(conn, post_id, takedown_id):
    """
    Create a link between a post and a takedown notice.

    Args:
        conn: Database connection
        post_id: The post ID
        takedown_id: The takedown ID

    Returns:
        True if successful, False otherwise
    """
    if not post_id or not takedown_id:
        logging.warning("Cannot link post to takedown: missing post_id or takedown_id")
        return False

    cursor = conn.cursor()
    try:
        query = """
            INSERT INTO post_takedowns (post_id, takedown_id, linked_at)
            VALUES (%s, %s, NOW())
            ON DUPLICATE KEY UPDATE linked_at = linked_at
        """
        cursor.execute(query, (post_id, takedown_id))
        conn.commit()
        logging.debug(f"Linked post {post_id} to takedown {takedown_id}")
        return True

    except Exception as e:
        logging.error(f"Error linking post {post_id} to takedown {takedown_id}: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()


def get_takedown_by_id(conn, takedown_id):
    """
    Retrieve takedown details by ID.

    Args:
        conn: Database connection
        takedown_id: The takedown ID

    Returns:
        Dictionary with takedown data, or None if not found
    """
    cursor = conn.cursor(dictionary=True)
    try:
        query = """
            SELECT * FROM takedowns
            WHERE takedown_id = %s
        """
        cursor.execute(query, (takedown_id,))
        result = cursor.fetchone()
        return result

    except Exception as e:
        logging.error(f"Error fetching takedown {takedown_id}: {e}")
        return None
    finally:
        cursor.close()


def get_posts_by_takedown(conn, takedown_id):
    """
    Get all posts associated with a takedown.

    Args:
        conn: Database connection
        takedown_id: The takedown ID

    Returns:
        List of post IDs
    """
    cursor = conn.cursor()
    try:
        query = """
            SELECT post_id FROM post_takedowns
            WHERE takedown_id = %s
            ORDER BY linked_at DESC
        """
        cursor.execute(query, (takedown_id,))
        results = cursor.fetchall()
        return [row[0] for row in results]

    except Exception as e:
        logging.error(f"Error fetching posts for takedown {takedown_id}: {e}")
        return []
    finally:
        cursor.close()


def get_takedowns_by_post(conn, post_id):
    """
    Get all takedowns associated with a post.

    Args:
        conn: Database connection
        post_id: The post ID

    Returns:
        List of takedown dictionaries
    """
    cursor = conn.cursor(dictionary=True)
    try:
        query = """
            SELECT t.* FROM takedowns t
            JOIN post_takedowns pt ON t.takedown_id = pt.takedown_id
            WHERE pt.post_id = %s
            ORDER BY t.finalized_at DESC, t.scraped_at DESC
        """
        cursor.execute(query, (post_id,))
        results = cursor.fetchall()
        return results

    except Exception as e:
        logging.error(f"Error fetching takedowns for post {post_id}: {e}")
        return []
    finally:
        cursor.close()


def backfill_deletion_status(conn, e621_config, batch_limit: int | None = None):
    """Backfill deletion status for existing posts by checking the API.

    Fetches post data from the API for posts that haven't been checked for deletion status
    recently (or ever) and updates their deletion status and timestamp if they've been deleted.
    """
    import requests
    import time
    from datetime import datetime, timedelta

    # Lazy import to avoid circular dependencies
    try:
        from fff.archive.api import get_auth
    except Exception:
        get_auth = None

    user_agent = e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")
    auth = get_auth(e621_config) if callable(get_auth) else (e621_config.get("login"), e621_config.get("api_key"))
    rate_sleep = e621_config.get("rate_limit_sleep", 1)

    # Get posts that haven't been checked for deletion status recently
    # Check posts that are either not marked as deleted or haven't been checked in the last 7 days
    cursor = conn.cursor()
    cutoff_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    query = """
        SELECT id FROM posts
        WHERE (deleted = 0 OR deleted IS NULL)
           OR (lastChecked IS NULL OR lastChecked < %s)
        ORDER BY lastChecked ASC, id ASC
    """
    cursor.execute(query, (cutoff_date,))
    rows = cursor.fetchall()
    cursor.close()

    post_ids = [r[0] for r in rows]
    if batch_limit is not None:
        post_ids = post_ids[:batch_limit]

    if not post_ids:
        logging.info("No posts need deletion status checking.")
        return 0

    logging.info("Checking deletion status for %s posts...", len(post_ids))
    updated_count = 0
    deleted_found = 0

    for idx, post_id in enumerate(post_ids, start=1):
        url = f"https://e621.net/posts/{post_id}.json"
        try:
            resp = http.get("e621", url, headers={"User-Agent": user_agent}, auth=auth, timeout=30)
            resp.raise_for_status()
            data = _parse_single_post(resp.json())

            if not data:
                logging.warning("No post data when checking deletion status for post %s", post_id)
                continue

            # Check deletion status
            is_deleted = data.get("flags", {}).get("deleted", False)
            updated_at = data.get("updated_at")
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if is_deleted:
                # Post is deleted, update database
                deleted_at = None
                if updated_at:
                    try:
                        if isinstance(updated_at, str):
                            # Parse ISO format and convert to MySQL datetime format
                            from datetime import datetime as dt
                            deleted_at = dt.fromisoformat(updated_at.replace('Z', '+00:00')).strftime("%Y-%m-%d %H:%M:%S")
                        else:
                            deleted_at = updated_at
                    except Exception as e:
                        logging.warning("Could not parse updated_at for deleted post %s: %s", post_id, e)
                        deleted_at = current_time
                else:
                    deleted_at = current_time

                # Try to get deletion reason from API first (though it's usually not there)
                deletion_reason = data.get("delreason") or data.get("deletion_reason")

                # If not in API, fetch from the web page
                if not deletion_reason:
                    try:
                        deletion_reason = fetch_deletion_reason_from_page(post_id, user_agent, auth)
                        # Add a small delay to avoid overwhelming the server when fetching HTML
                        if deletion_reason:
                            time.sleep(0.5)
                    except Exception as e:
                        logging.warning(f"Failed to fetch deletion reason for post {post_id}: {e}")
                        deletion_reason = None

                # Update post as deleted
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE posts
                    SET deleted = 1,
                        deleted_at = %s,
                        deletion_reason = COALESCE(%s, deletion_reason),
                        lastChecked = %s
                    WHERE id = %s
                """, (deleted_at, deletion_reason, current_time, post_id))
                conn.commit()
                cursor.close()

                deleted_found += 1
                if deletion_reason:
                    logging.info("Post %s marked as deleted (deleted_at: %s, reason: %s)", post_id, deleted_at, deletion_reason)
                else:
                    logging.info("Post %s marked as deleted (deleted_at: %s)", post_id, deleted_at)
            else:
                # Post is not deleted, just update lastChecked
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE posts
                    SET lastChecked = %s
                    WHERE id = %s
                """, (current_time, post_id))
                conn.commit()
                cursor.close()

            updated_count += 1

            # Rate limiting
            time.sleep(rate_sleep)

            # Progress logging
            if updated_count % 100 == 0:
                logging.info("Checked deletion status for %s posts so far (%s deleted found)...", updated_count, deleted_found)

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                # Post not found (likely deleted but API doesn't return it)
                # Try to fetch deletion reason from the web page
                deletion_reason = None
                try:
                    deletion_reason = fetch_deletion_reason_from_page(post_id, user_agent, auth)
                except Exception as fetch_error:
                    logging.warning(f"Failed to fetch deletion reason for post {post_id}: {fetch_error}")

                cursor = conn.cursor()
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("""
                    UPDATE posts
                    SET deleted = 1,
                        deleted_at = %s,
                        deletion_reason = COALESCE(%s, deletion_reason),
                        lastChecked = %s
                    WHERE id = %s
                """, (current_time, deletion_reason, current_time, post_id))
                conn.commit()
                cursor.close()
                deleted_found += 1
                if deletion_reason:
                    logging.info("Post %s marked as deleted (404 not found, reason: %s)", post_id, deletion_reason)
                else:
                    logging.info("Post %s marked as deleted (404 not found)", post_id)
            else:
                logging.error("HTTP error checking deletion status for post %s: %s", post_id, e)
        except Exception as e:
            logging.error("Error checking deletion status for post %s: %s", post_id, e)

    logging.info("Completed deletion status check for %s posts. Found %s newly deleted posts.", updated_count, deleted_found)
    return updated_count, deleted_found

def get_tag_type(category):
    mapping = {
        "copyright": "Copyright",
        "character": "Character",
        "species": "Species",
        "general": "General",
        "lore": "Lore",
        "meta": "Meta"
    }
    return mapping.get(category.lower(), "General")


def backfill_missing_file_names(conn):
    """Generate file_name for posts where it's NULL or empty."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, rating, file_type, file_url, file_path
        FROM posts
        WHERE file_name IS NULL OR file_name = ''
    """)
    rows = cursor.fetchall()
    if not rows:
        cursor.close()
        logging.info("No missing file_name entries to backfill.")
        return 0

    rating_map = {"s": "safe", "q": "questionable", "e": "explicit"}
    updated = 0
    for post_id, rating, file_type, file_url, file_path in rows:
        # Fetch artists
        cursor_art = conn.cursor()
        cursor_art.execute("""
            SELECT a.name FROM artists a
            JOIN posts_artists pa ON pa.artist_id = a.artist_id
            WHERE pa.post_id = %s
    """, (post_id,))
        artists = [r[0] for r in cursor_art.fetchall()]
        cursor_art.close()
        # Filter out "avoid_posting", "conditional_dnp", "unknown", and "sound_warning" and strip flag tokens from artist names
        excluded_artists = {"avoid_posting", "conditional_dnp", "unknown", "sound_warning"}
        artists = [artist for artist in artists if artist not in excluded_artists]
        artists = clean_artist_names(strip_flag_tokens(artist) for artist in artists)
        if artists:
            artist_part = "_".join(artists)
        else:
            artist_part = "unknown"
        rating_full = rating_map.get((rating or '').lower(), (rating or 'unknown').lower())
        # Determine extension independent of file_type column (which stores category now)
        extension = ''
        # Try infer from file_path then url
        source_name = None
        if file_path and '.' in os.path.basename(file_path):
            source_name = os.path.basename(file_path)
        elif file_url and '.' in file_url.rsplit('/', 1)[-1]:
            source_name = file_url.rsplit('/', 1)[-1]
        if source_name:
            ext_candidate = source_name.split('.')[-1]
            if 1 <= len(ext_candidate) <= 10:
                extension = ext_candidate.lower()
        file_name = f"{artist_part}-{rating_full}-{post_id}"
        if extension:
            file_name = f"{file_name}.{extension}"
        # Ensure no flag tokens remain in the final filename
        file_name = windows_safe_filename(strip_flag_tokens(file_name))  # fff: invalid chars -> _
        try:
            cursor_update = conn.cursor()
            cursor_update.execute("UPDATE posts SET file_name=%s WHERE id=%s", (file_name, post_id))
            cursor_update.close()
            updated += 1
        except Exception as e:
            logging.error("Failed to update file_name for post %s: %s", post_id, e)
    conn.commit()
    cursor.close()
    logging.info("Backfilled file_name for %s posts.", updated)
    return updated


def normalize_ratings(conn):
    """Normalize existing rating column values to full words (safe, questionable, explicit)."""
    cursor = conn.cursor()
    mapping = {"s": "safe", "q": "questionable", "e": "explicit"}
    # Update rows where rating is single-letter.
    for short, full in mapping.items():
        try:
            cursor.execute("UPDATE posts SET rating=%s WHERE rating=%s", (full, short))
        except Exception as e:
            logging.error("Failed updating rating %s -> %s: %s", short, full, e)
    conn.commit()
    cursor.close()
    logging.info("Ratings normalization complete.")


def backfill_missing_file_types(conn):
    """Populate/normalize file_type category (image/gif/video) based on file_url extension."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, file_url, file_type FROM posts
        WHERE file_url IS NOT NULL AND file_url != ''
    """)
    rows = cursor.fetchall()
    if not rows:
        cursor.close()
        logging.info("No rows available for file_type normalization.")
        return 0
    updated = 0
    def categorize(ext: str) -> str:
        if not ext:
            return 'unknown'
        video_exts = {"mp4", "webm", "mov", "mkv", "avi", "m4v"}
        image_exts = {"jpg", "jpeg", "png", "bmp", "webp", "avif", "heic", "heif", "tif", "tiff"}
        if ext == "gif":
            return "gif"
        if ext in video_exts:
            return "video"
        if ext in image_exts:
            return "image"
        if ext == "swf":
            return "flash"
        # For any other file type, return the extension itself
        return ext
    for post_id, file_url, existing in rows:
        last_segment = file_url.split('/')[-1].split('?')[0]
        ext = last_segment.rsplit('.', 1)[-1].lower() if '.' in last_segment else ''
        category = categorize(ext)
        if existing not in {"image", "gif", "video"} or existing != category:
            try:
                cursor_update = conn.cursor()
                cursor_update.execute("UPDATE posts SET file_type=%s WHERE id=%s", (category, post_id))
                cursor_update.close()
                updated += 1
            except Exception as e:
                logging.error("Failed to normalize file_type for post %s: %s", post_id, e)
    conn.commit()
    cursor.close()
    logging.info("Normalized file_type for %s posts.", updated)
    return updated


def backfill_conditional_dnp(conn, e621_config, batch_limit: int | None = None):
    """Backfill inclusion of 'conditional_dnp' artist where present remotely but not reflected locally.

    For each post (optionally limited), fetch remote JSON. If 'conditional_dnp' is in the remote artist tag list
    but missing from local artist links or file_name, link it and regenerate file_name including all artists.
    """
    try:
        from fff.archive.api import get_auth
    except Exception:
        get_auth = None
    user_agent = e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")
    auth = get_auth(e621_config) if callable(get_auth) else (e621_config.get("login"), e621_config.get("api_key"))
    rate_sleep = e621_config.get("rate_limit_sleep", 1)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.file_name FROM posts p
        LEFT JOIN posts_artists pa ON pa.post_id = p.id
        LEFT JOIN artists a ON a.artist_id = pa.artist_id AND a.name = 'conditional_dnp'
        WHERE a.artist_id IS NULL OR p.file_name NOT LIKE '%conditional_dnp%'
    """)
    rows = cursor.fetchall()
    cursor.close()
    targets = rows if batch_limit is None else rows[:batch_limit]
    if not targets:
        logging.info("No posts need conditional_dnp backfill.")
        return 0
    logging.info("Evaluating %s posts for conditional_dnp backfill...", len(targets))
    updated = 0
    rating_map = {"s": "safe", "q": "questionable", "e": "explicit"}
    for pid, current_name in targets:
        url = f"https://e621.net/posts/{pid}.json"
        try:
            resp = http.get("e621", url, headers={"User-Agent": user_agent}, auth=auth, timeout=30)
            resp.raise_for_status()
            post = _parse_single_post(resp.json())
            if not post:
                continue
            artists_remote = (post.get("tags", {}) or {}).get("artist", []) if isinstance(post.get("tags", {}), dict) else []
            artists_remote = artists_remote or []
            if 'conditional_dnp' not in artists_remote:
                continue
            # Ensure all remote artists linked (but keep flag tokens only as tags, not in names)
            for artist in artists_remote:
                clean_name = strip_flag_tokens(artist)
                a_id = insert_artist(conn, clean_name or artist, commit=False)
                if a_id:
                    link_post_artist(conn, pid, a_id, commit=False)
            # Rebuild file_name
            cursor_art = conn.cursor()
            cursor_art.execute("""
                SELECT a.name FROM artists a
                JOIN posts_artists pa ON pa.artist_id = a.artist_id
                WHERE pa.post_id = %s
                ORDER BY a.name
            """, (pid,))
            artist_list = clean_artist_names(strip_flag_tokens(r[0]) for r in cursor_art.fetchall())
            cursor_art.close()
            cursor_meta = conn.cursor()
            cursor_meta.execute("SELECT rating, file_url, file_path FROM posts WHERE id=%s", (pid,))
            meta_row = cursor_meta.fetchone()
            cursor_meta.close()
            if not meta_row:
                continue
            rating, file_url, file_path = meta_row
            rating_full = rating_map.get((rating or '').lower(), (rating or 'unknown').lower())
            extension = ''
            source_name = None
            if file_path and '.' in os.path.basename(file_path):
                source_name = os.path.basename(file_path)
            elif file_url and '.' in file_url.rsplit('/', 1)[-1]:
                source_name = file_url.rsplit('/', 1)[-1]
            if source_name:
                ext_candidate = source_name.split('.')[-1]
                if 1 <= len(ext_candidate) <= 10:
                    extension = ext_candidate.lower()
            artist_part = "_".join(artist_list) if artist_list else "unknown"
            new_file_name = f"{artist_part}-{rating_full}-{pid}"
            if extension:
                new_file_name = f"{new_file_name}.{extension}"
            new_file_name = windows_safe_filename(strip_flag_tokens(new_file_name))  # fff: invalid chars -> _
            if new_file_name != current_name:
                cursor_upd = conn.cursor()
                cursor_upd.execute("UPDATE posts SET file_name=%s WHERE id=%s", (new_file_name, pid))
                cursor_upd.close()
            conn.commit()
            updated += 1
            if updated % 100 == 0:
                logging.info("conditional_dnp backfill updated %s posts...", updated)
            time.sleep(rate_sleep)
        except Exception as e:
            logging.error("Error backfilling conditional_dnp for post %s: %s", pid, e)
    logging.info("Finished conditional_dnp backfill; %s posts updated.", updated)
    return updated

def check_old_favs_deletion_status(conn, e621_config, batch_limit: int | None = None):
    """Check if posts in old_favs were actually deleted rather than just unfavorited.

    Makes API requests to e621 to check the deleted flag in post data.
    Updates the deleted and deleted_at fields accordingly.
    """
    from datetime import datetime
    # Get auth and user agent
    try:
        from fff.archive.api import get_auth
    except Exception:
        get_auth = None
    user_agent = e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")
    auth = get_auth(e621_config) if callable(get_auth) else (e621_config.get("login"), e621_config.get("api_key"))
    rate_sleep = e621_config.get("rate_limit_sleep", 1)

    cursor = conn.cursor()
    # Get old_favs posts that haven't been checked for deletion yet
    query = """
        SELECT id FROM old_favs
        WHERE deleted = 0 AND (lastChecked IS NULL OR lastChecked < DATE_SUB(NOW(), INTERVAL 7 DAY))
        ORDER BY moved_to_old_favs_at DESC
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    cursor.close()

    post_ids = [r[0] for r in rows]
    if batch_limit is not None:
        post_ids = post_ids[:batch_limit]

    if not post_ids:
        logging.info("No old_favs posts need deletion status checking.")
        return 0

    logging.info("Checking deletion status for %s old_favs posts...", len(post_ids))

    deleted_count = 0
    checked_count = 0

    for post_id in post_ids:
        url = f"https://e621.net/posts/{post_id}.json"
        try:
            # Use GET request to get post data and check deleted flag
            resp = http.get("e621", url, headers={"User-Agent": user_agent}, auth=auth, timeout=30)

            cursor = conn.cursor()
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if resp.status_code == 404:
                # Post was completely removed (rare case)
                cursor.execute("""
                    UPDATE old_favs
                    SET deleted = 1, deleted_at = %s, lastChecked = %s
                    WHERE id = %s
                """, (current_time, current_time, post_id))
                deleted_count += 1
                logging.info("Marked old_favs post %s as deleted (404)", post_id)
            elif resp.status_code == 200:
                # Check the deleted flag in the API response
                data = resp.json()
                post_data = _parse_single_post(data)
                is_deleted = post_data.get("flags", {}).get("deleted", False)

                if is_deleted:
                    # Post is marked as deleted in e621
                    updated_at = post_data.get("updated_at")
                    deleted_at = current_time

                    # Try to use the updated_at timestamp as deletion time
                    if updated_at:
                        try:
                            from datetime import datetime as dt
                            deleted_at = dt.fromisoformat(updated_at.replace('Z', '+00:00')).strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass  # Fall back to current_time

                    cursor.execute("""
                        UPDATE old_favs
                        SET deleted = 1, deleted_at = %s, lastChecked = %s
                        WHERE id = %s
                    """, (deleted_at, current_time, post_id))
                    deleted_count += 1
                    logging.info("Marked old_favs post %s as deleted (flag)", post_id)
                else:
                    # Post still exists and is not deleted, just update lastChecked
                    cursor.execute("""
                        UPDATE old_favs
                        SET lastChecked = %s
                        WHERE id = %s
                    """, (current_time, post_id))
            else:
                # Unexpected status code, just update lastChecked
                cursor.execute("""
                    UPDATE old_favs
                    SET lastChecked = %s
                    WHERE id = %s
                """, (current_time, post_id))

            conn.commit()
            cursor.close()
            checked_count += 1

            # Rate limiting
            time.sleep(rate_sleep)

            if checked_count % 50 == 0:
                logging.info("Checked %s old_favs posts so far, found %s deleted...", checked_count, deleted_count)

        except Exception as e:
            logging.error("Error checking deletion status for old_favs post %s: %s", post_id, e)
            # Still update lastChecked even if there was an error
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE old_favs
                    SET lastChecked = %s
                    WHERE id = %s
                """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), post_id))
                conn.commit()
                cursor.close()
            except Exception:
                pass

    logging.info("Completed deletion status check for old_favs. Checked %s posts, found %s deleted.", checked_count, deleted_count)
    return deleted_count

def update_media_hash(conn, post_id, media_hash, commit: bool = True):
    """Update the media_hash for a specific post."""
    cursor = conn.cursor()
    try:
        query = "UPDATE posts SET media_hash = %s WHERE id = %s"
        cursor.execute(query, (media_hash, post_id))
        if commit:
            conn.commit()
        logging.debug("Updated media_hash for post %s", post_id)
    except Exception as e:
        logging.error("Error updating media_hash for post %s: %s", post_id, e)
        if commit:
            conn.rollback()
    finally:
        cursor.close()

def get_posts_without_media_hash(conn, limit=None):
    """Get posts that have file_path but no media_hash."""
    cursor = conn.cursor()
    try:
        query = """
        SELECT id, file_path, file_name
        FROM posts
        WHERE file_path IS NOT NULL
        AND file_path != ''
        AND (media_hash IS NULL OR media_hash = '')
        AND deleted = 0
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        results = cursor.fetchall()
        return [{'id': row[0], 'file_path': row[1], 'file_name': row[2]} for row in results]
    except Exception as e:
        logging.error("Error getting posts without media_hash: %s", e)
        return []
    finally:
        cursor.close()

def backfill_media_hashes(conn, batch_size=100, mysql_config=None):
    """Backfill media hashes for existing files."""
    try:
        from fff.hashing import archive_hasher as MediaHasher  # fff: archive hash profile
        hasher = MediaHasher()

        posts_to_hash = get_posts_without_media_hash(conn, limit=batch_size)
        if not posts_to_hash:
            logging.info("No posts need media hash backfill")
            return 0

        logging.info(f"Starting media hash backfill for {len(posts_to_hash)} posts")

        processed = 0
        successful = 0

        for post in posts_to_hash:
            try:
                post_id = post['id']
                file_path = post['file_path']

                if not os.path.exists(file_path):
                    logging.warning(f"File not found for post {post_id}: {file_path}")
                    continue

                # Check image_sorting_hashes database first
                media_hash = None
                if mysql_config:
                    file_name = os.path.basename(file_path)
                    try:
                        media_hash = check_media_hash_in_image_sorting_db(mysql_config, file_name)
                        if media_hash:
                            logging.info(f"Using existing hash for {file_name} from image_sorting_hashes database")
                    except Exception as e:
                        logging.debug(f"Could not check image_sorting_hashes database: {e}")

                # Generate new hash if not found in external database
                if not media_hash:
                    media_hash = hasher.hash_file(file_path)
                    if media_hash:
                        logging.debug(f"Generated new media hash for post {post_id}")

                if media_hash:
                    update_media_hash(conn, post_id, media_hash, commit=False)
                    successful += 1
                    logging.debug(f"Updated media hash for post {post_id}: {media_hash}")
                else:
                    logging.warning(f"Could not generate media hash for post {post_id}")

                processed += 1

                # Commit in batches
                if processed % 50 == 0:
                    conn.commit()
                    logging.info(f"Processed {processed}/{len(posts_to_hash)} media hashes")

            except Exception as e:
                logging.error(f"Error processing media hash for post {post['id']}: {e}")

        # Final commit
        conn.commit()
        logging.info(f"Media hash backfill complete: {successful}/{processed} successful")
        return successful

    except ImportError:
        logging.error("media_hasher module not available for backfill")
        return 0
    except Exception as e:
        logging.error(f"Error in media hash backfill: {e}")
        conn.rollback()
        return 0

def backfill_timestamps_from_api(conn, e621_config):
    """Backfill created_at and updated_at timestamps for posts that have NULL values.
    Uses saved API responses from fav_response folder."""
    cursor = conn.cursor()

    try:
        # Find posts with NULL timestamps
        query = """
        SELECT id FROM posts
        WHERE created_at IS NULL OR updated_at IS NULL
        ORDER BY id
        """
        cursor.execute(query)
        posts_needing_timestamps = cursor.fetchall()

        if not posts_needing_timestamps:
            logging.info("All posts already have timestamps")
            return 0

        post_ids_needing_timestamps = {row[0] for row in posts_needing_timestamps}
        logging.info(f"Found {len(post_ids_needing_timestamps)} posts with missing timestamps")

        # Check if we have saved API responses
        response_folder = e621_config.get("api_response_folder", "fav_response")
        if not os.path.exists(response_folder):
            logging.warning("No saved API responses found - cannot backfill timestamps")
            logging.info("Enable save_api_responses in config to backfill on next run")
            return 0

        # Load all saved API responses and build a cache
        import glob
        import json

        post_cache = {}
        response_files = glob.glob(os.path.join(response_folder, "favorites_page_*.json"))

        if not response_files:
            logging.warning("No saved API response files found in %s", response_folder)
            return 0

        logging.info(f"Loading {len(response_files)} API response files...")

        for response_file in response_files:
            try:
                with open(response_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    posts = _parse_post_list(data.get('response', data))

                    for post in posts:
                        post_id = post.get('id')
                        if post_id and post_id in post_ids_needing_timestamps:
                            post_cache[post_id] = post
            except Exception as e:
                logging.error(f"Error reading response file {response_file}: {e}")

        logging.info(f"Found {len(post_cache)} posts with missing timestamps in saved responses")

        # Update timestamps from cache
        updated_count = 0
        from datetime import datetime as dt

        for post_id, post_data in post_cache.items():
            created_at = post_data.get("created_at")
            updated_at = post_data.get("updated_at")

            # Convert to local timezone
            converted_created_at = None
            converted_updated_at = None

            if created_at and isinstance(created_at, str):
                try:
                    parsed_dt = dt.fromisoformat(created_at.replace('Z', '+00:00'))
                    local_dt = parsed_dt.astimezone()
                    converted_created_at = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception as e:
                    logging.warning(f"Could not parse created_at for post {post_id}: {e}")

            if updated_at and isinstance(updated_at, str):
                try:
                    parsed_dt = dt.fromisoformat(updated_at.replace('Z', '+00:00'))
                    local_dt = parsed_dt.astimezone()
                    converted_updated_at = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception as e:
                    logging.warning(f"Could not parse updated_at for post {post_id}: {e}")

            # Update only if we have at least one timestamp
            if converted_created_at or converted_updated_at:
                update_query = """
                UPDATE posts
                SET created_at = COALESCE(%s, created_at),
                    updated_at = COALESCE(%s, updated_at)
                WHERE id = %s
                """
                cursor.execute(update_query, (converted_created_at, converted_updated_at, post_id))
                updated_count += 1

                if updated_count % 100 == 0:
                    conn.commit()
                    logging.info(f"Backfilled timestamps for {updated_count} posts...")

        conn.commit()

        posts_not_found = post_ids_needing_timestamps - set(post_cache.keys())
        if posts_not_found:
            logging.warning(f"{len(posts_not_found)} posts not found in saved API responses")
            logging.info("These posts may need newer API responses to backfill timestamps")

        logging.info(f"Timestamp backfill complete: Updated {updated_count} posts")
        return updated_count

    except Exception as e:
        logging.error(f"Error in timestamp backfill: {e}")
        conn.rollback()
        return 0
    finally:
        cursor.close()

def get_post_relationships(conn, post_id):
    """Get parent-child relationships for a specific post.

    Returns:
        dict with parent_id (if exists), children array, and favorited status
    """
    cursor = conn.cursor()
    try:
        # Get parent relationship
        cursor.execute("""
            SELECT parent_id
            FROM post_relationships
            WHERE post_id = %s AND parent_id IS NOT NULL
        """, (post_id,))
        parent_result = cursor.fetchone()
        parent_id = parent_result[0] if parent_result else None

        # Get child relationships
        cursor.execute("""
            SELECT child_id
            FROM post_relationships
            WHERE post_id = %s AND child_id IS NOT NULL
            ORDER BY child_id
        """, (post_id,))
        children_results = cursor.fetchall()
        children_list = [row[0] for row in children_results]

        # Check if parent is favorited
        parent_is_favorited = False
        if parent_id:
            cursor.execute("SELECT 1 FROM posts WHERE id = %s LIMIT 1", (parent_id,))
            parent_is_favorited = cursor.fetchone() is not None

        return {
            "post_id": post_id,
            "parent_id": parent_id,
            "parent_is_favorited": parent_is_favorited,
            "has_children": len(children_list) > 0,
            "children": children_list
        }
    finally:
        cursor.close()

def get_child_posts(conn, parent_id):
    """Get all child posts for a given parent post ID.

    Returns:
        List of post IDs that are children of the given parent
    """
    cursor = conn.cursor()
    try:
        query = """
            SELECT child_id
            FROM post_relationships
            WHERE post_id = %s AND child_id IS NOT NULL
            ORDER BY child_id
        """
        cursor.execute(query, (parent_id,))
        results = cursor.fetchall()
        return [row[0] for row in results]
    finally:
        cursor.close()

def get_parent_post(conn, child_id):
    """Get the parent post ID for a given child post.

    Returns:
        Parent post ID or None if the post has no parent
    """
    cursor = conn.cursor()
    try:
        query = """
            SELECT parent_id
            FROM post_relationships
            WHERE post_id = %s AND parent_id IS NOT NULL
        """
        cursor.execute(query, (child_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    finally:
        cursor.close()

def get_post_family_tree(conn, post_id):
    """Get the complete family tree for a post (parent and all siblings).

    Returns:
        dict with keys: parent_id, current_post_id, siblings (list of post IDs)
    """
    cursor = conn.cursor()
    try:
        # First get the parent of this post
        parent_id = get_parent_post(conn, post_id)

        if not parent_id:
            # This post has no parent, check if it has children instead
            children = get_child_posts(conn, post_id)
            return {
                "parent_id": None,
                "current_post_id": post_id,
                "siblings": [],
                "children": children
            }

        # Get all siblings (other children of the same parent)
        siblings = get_child_posts(conn, parent_id)
        # Remove current post from siblings list
        siblings = [s for s in siblings if s != post_id]

        return {
            "parent_id": parent_id,
            "current_post_id": post_id,
            "siblings": siblings,
            "children": []
        }
    finally:
        cursor.close()

def get_relationship_stats(conn):
    """Get statistics about post relationships in the database.

    Returns:
        dict with counts of posts with parents, posts with children, orphaned relationships, etc.
    """
    cursor = conn.cursor()
    try:
        stats = {}

        # Total relationship rows
        cursor.execute("SELECT COUNT(*) FROM post_relationships")
        stats["total_relationship_rows"] = cursor.fetchone()[0]

        # Distinct posts with any relationship data
        cursor.execute("SELECT COUNT(DISTINCT post_id) FROM post_relationships")
        stats["total_posts_with_relationships"] = cursor.fetchone()[0]

        # Posts with parents
        cursor.execute("SELECT COUNT(DISTINCT post_id) FROM post_relationships WHERE parent_id IS NOT NULL")
        stats["posts_with_parent"] = cursor.fetchone()[0]

        # Posts with children
        cursor.execute("SELECT COUNT(DISTINCT post_id) FROM post_relationships WHERE child_id IS NOT NULL")
        stats["posts_with_children"] = cursor.fetchone()[0]

        # Posts with favorited parents (parent exists in posts table)
        cursor.execute("""
            SELECT COUNT(DISTINCT pr.post_id)
            FROM post_relationships pr
            INNER JOIN posts p ON pr.parent_id = p.id
            WHERE pr.parent_id IS NOT NULL
        """)
        stats["posts_with_favorited_parent"] = cursor.fetchone()[0]

        # Posts with non-favorited parents (parent not in posts table)
        cursor.execute("""
            SELECT COUNT(DISTINCT pr.post_id)
            FROM post_relationships pr
            LEFT JOIN posts p ON pr.parent_id = p.id
            WHERE pr.parent_id IS NOT NULL AND p.id IS NULL
        """)
        stats["posts_with_non_favorited_parent"] = cursor.fetchone()[0]

        # Orphaned relationships (parent_id or child_id points to non-existent post)
        cursor.execute("""
            SELECT COUNT(*)
            FROM post_relationships pr
            LEFT JOIN posts p ON pr.parent_id = p.id
            WHERE pr.parent_id IS NOT NULL AND p.id IS NULL
        """)
        stats["orphaned_parent_relationships"] = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM post_relationships pr
            LEFT JOIN posts p ON pr.child_id = p.id
            WHERE pr.child_id IS NOT NULL AND p.id IS NULL
        """)
        stats["orphaned_child_relationships"] = cursor.fetchone()[0]

        return stats
    finally:
        cursor.close()

def get_posts_with_non_favorited_parents(conn, limit=None):
    """Get posts that have a parent that isn't in our favorites.

    These are posts where the parent exists on e621 but isn't in our favorites.
    Useful for finding potential posts to favorite.

    Returns:
        List of tuples: (post_id, parent_id)
    """
    cursor = conn.cursor()
    try:
        query = """
            SELECT DISTINCT pr.post_id, pr.parent_id
            FROM post_relationships pr
            LEFT JOIN posts p ON pr.parent_id = p.id
            WHERE pr.parent_id IS NOT NULL AND p.id IS NULL
            ORDER BY pr.post_id DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        results = cursor.fetchall()
        return [(row[0], row[1]) for row in results]
    finally:
        cursor.close()

def get_pools_for_post(conn, post_id):
    """Get all pools that a post belongs to.

    Returns:
        List of pool IDs
    """
    cursor = conn.cursor()
    try:
        query = """
            SELECT pool_id
            FROM posts_pools
            WHERE post_id = %s
            ORDER BY pool_id
        """
        cursor.execute(query, (post_id,))
        results = cursor.fetchall()
        return [row[0] for row in results]
    finally:
        cursor.close()

def get_posts_in_pool(conn, pool_id, limit=None):
    """Get all posts in a specific pool.

    Returns:
        List of post IDs
    """
    cursor = conn.cursor()
    try:
        query = """
            SELECT post_id
            FROM posts_pools
            WHERE pool_id = %s
            ORDER BY post_id
        """
        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, (pool_id,))
        results = cursor.fetchall()
        return [row[0] for row in results]
    finally:
        cursor.close()

def get_pool_info(conn, pool_id):
    """Get information about a specific pool.

    Returns:
        dict with pool information or None if not found
    """
    cursor = conn.cursor()
    try:
        query = """
            SELECT pool_id, name, description, created_at, updated_at, post_count, is_active, category, pool_url, ingested_at
            FROM pools
            WHERE pool_id = %s
        """
        cursor.execute(query, (pool_id,))
        result = cursor.fetchone()

        if not result:
            return None

        return {
            "pool_id": result[0],
            "name": result[1],
            "description": result[2],
            "created_at": result[3],
            "updated_at": result[4],
            "post_count": result[5],
            "is_active": result[6],
            "category": result[7],
            "pool_url": result[8],
            "ingested_at": result[9]
        }
    finally:
        cursor.close()

def get_pool_stats(conn):
    """Get statistics about pools in the database.

    Returns:
        dict with pool statistics
    """
    cursor = conn.cursor()
    try:
        stats = {}

        # Total pools
        cursor.execute("SELECT COUNT(*) FROM pools")
        stats["total_pools"] = cursor.fetchone()[0]

        # Total post-pool relationships
        cursor.execute("SELECT COUNT(*) FROM posts_pools")
        stats["total_post_pool_links"] = cursor.fetchone()[0]

        # Posts in pools
        cursor.execute("SELECT COUNT(DISTINCT post_id) FROM posts_pools")
        stats["posts_in_pools"] = cursor.fetchone()[0]

        # Average posts per pool
        cursor.execute("""
            SELECT AVG(post_count)
            FROM (
                SELECT COUNT(*) as post_count
                FROM posts_pools
                GROUP BY pool_id
            ) as pool_counts
        """)
        avg_result = cursor.fetchone()[0]
        stats["avg_posts_per_pool"] = round(float(avg_result), 2) if avg_result else 0

        # Pools with metadata
        cursor.execute("SELECT COUNT(*) FROM pools WHERE name IS NOT NULL")
        stats["pools_with_metadata"] = cursor.fetchone()[0]

        return stats
    finally:
        cursor.close()

