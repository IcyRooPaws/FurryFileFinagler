# Delete and rename logging use the supported log_file_operation arguments.
"""
Database operations for file hash caching using MySQL.
"""
import os
from mysql.connector import Error
import time
import logging
import threading

from fff.common import db

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Manages MySQL database operations for file hash caching."""

    def __init__(self, config):
        self.config = config  # Store full config object for archive access
        self.host = config.MYSQL_HOST
        self.port = config.MYSQL_PORT
        self.user = config.MYSQL_USER
        self.password = config.MYSQL_PASSWORD
        self.database = config.MYSQL_DATABASE
        self.destination_directories = config.destination_directories
        self.duplicate_directory = config.duplicate_directory

        # Thread lock for connection pool operations
        self._lock = threading.Lock()
        self._pool = None

        # Ensure database exists
        self._ensure_database_exists()

        # Initialize connection pool
        self._init_connection_pool()

        # Ensure archive tables exist proactively (even if directories not configured)
        self._ensure_archive_tables()

        # Ensure file operations logging table exists
        self._ensure_file_operations_table()

        # Migrate existing tables to add artist check columns
        self._ensure_artist_check_columns()

    POOL_SIZE = 25  # one connection per worker thread, with headroom

    def _server_settings(self):
        return {'host': self.host, 'port': self.port, 'user': self.user, 'password': self.password}

    def _connection_settings(self):
        """Connection options for the pool and the direct-connection fallback."""
        return db.server_overrides(self._server_settings()) | {
            'autocommit': True,
            'connection_timeout': 30,
            'sql_mode': '',
            'charset': 'utf8mb4',
            'use_unicode': True,
            'buffered': True,
        }

    def _init_connection_pool(self):
        """Initialize the connection pool for better thread management."""
        try:
            # Shared with every DatabaseManager in this process that uses the same settings.
            self._pool = db.get_pool(self.database, pool_size=self.POOL_SIZE, **self._connection_settings())
            logger.info(f"Database connection pool initialized with {self.POOL_SIZE} connections")

        except Error as e:
            logger.error(f"Failed to initialize connection pool: {e}")
            self._pool = None

    def _ensure_database_exists(self):
        """Ensure the MySQL database exists before connecting to it."""
        try:
            conn = db.connect_server(settings=self._server_settings(), autocommit=True)
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
            cursor.close()
            conn.close()
            logger.info("Database existence verified")
        except Exception as e:
            logger.error(f"Error ensuring MySQL database exists: {e}")
            raise

    def get_db_connection(self, retries=3, delay=1):
        """Get a database connection with retry logic and timeout settings."""
        for attempt in range(retries):
            try:
                # Use connection pool if available, otherwise fall back to direct connection
                if self._pool:
                    with self._lock:
                        conn = self._pool.get_connection()
                        return conn
                else:
                    # Fallback to direct connection if pool failed
                    return db.connect_database(self.database, pooled=False, **self._connection_settings())

            except Error as e:
                error_msg = str(e).lower()
                if any(keyword in error_msg for keyword in ["lock", "locked", "timeout", "connection", "10055", "pool exhausted"]):
                    if attempt < retries - 1:
                        wait_time = delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(f"Database connection attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                logger.error(f"Database connection failed after {retries} attempts: {e}")
                raise

    def get_connection_context(self):
        """Get a database connection as a context manager for proper cleanup."""
        class ConnectionContext:
            def __init__(self, db_manager):
                self.db_manager = db_manager
                self.connection = None

            def __enter__(self):
                self.connection = self.db_manager.get_db_connection()
                return self.connection

            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.connection:
                    self.connection.close()

        return ConnectionContext(self)

    def _ensure_archive_tables(self):
        """Proactively create archive tables even if directories aren't configured.

        This ensures the tables exist for:
        - Future archive operations
        - Database maintenance (orphaned entry cleanup)
        - Restore operations

        Handles both simple format (string) and expanded format (dict with images/videos/gifs).
        """
        archive_tables = []

        # Get archive configuration from config
        archive_dirs = getattr(self.config, 'archive_directories', None) or self.config.config.get('archive_directories', {})

        if archive_dirs:
            # Build table names based on actual configuration format
            for key, archive_config in archive_dirs.items():
                if isinstance(archive_config, str):
                    # Simple format: single table
                    archive_tables.append(f"archive_{key}")
                elif isinstance(archive_config, dict):
                    # Expanded format: separate tables for each media type
                    for media_type, path in archive_config.items():
                        if path:  # Only create table if path is configured
                            archive_tables.append(f"archive_{key}_{media_type}")
                elif archive_config is None:
                    # Null/disabled archive - create table anyway for future use
                    archive_tables.append(f"archive_{key}")
        else:
            # Fallback: create standard archive tables if no config
            archive_tables = ['archive_premium', 'archive_explicit', 'archive_sfw']

        # Always ensure the archived_favorites table exists
        archive_tables.append('archived_favorites')

        for table_name in archive_tables:
            try:
                self.ensure_dest_table(table_name)
                logger.debug(f"Archive table {table_name} ensured to exist")
            except Exception as e:
                logger.warning(f"Failed to create archive table {table_name}: {e}")

    def _ensure_file_operations_table(self):
        """Create file_operations table for logging all file operations."""
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # Create file_operations table for comprehensive logging
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS file_operations (
                        id BIGINT AUTO_INCREMENT PRIMARY KEY,
                        operation_type VARCHAR(32) NOT NULL,
                        file_name VARCHAR(1024) NOT NULL,
                        source_path TEXT,
                        destination_path TEXT,
                        source_directory VARCHAR(255),
                        destination_directory VARCHAR(255),
                        file_size BIGINT,
                        operation_status VARCHAR(32) DEFAULT 'success',
                        error_message TEXT,
                        reason TEXT,
                        performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                        INDEX idx_operation_type (operation_type),
                        INDEX idx_file_name (file_name(255)),
                        INDEX idx_performed_at (performed_at),
                        INDEX idx_operation_status (operation_status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)

                conn.commit()
                cursor.close()
                logger.debug("File operations table ensured to exist")
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    logger.error(f"Failed to create file_operations table: {e}")
            finally:
                if conn:
                    conn.close()

    def _ensure_artist_check_columns(self):
        """Add artist check columns to all existing destination tables."""
        tables = self.get_all_table_names(include_archives=True)

        # Also include favorites, archived_favorites, and manual_sort tables (not in destination_directories)
        additional_tables = ['favorites', 'archived_favorites', 'manual_sort_images', 'manual_sort_videos']
        tables.extend(additional_tables)

        for table_name in tables:
            for attempt in range(3):
                conn = None
                try:
                    conn = self.get_db_connection()
                    cursor = conn.cursor()

                    # Check if columns already exist
                    cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'last_artist_check'")
                    has_last_check = cursor.fetchone() is not None

                    cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'artist_matched'")
                    has_artist_matched = cursor.fetchone() is not None

                    # Add columns if missing
                    if not has_last_check:
                        cursor.execute(f"""
                            ALTER TABLE `{table_name}`
                            ADD COLUMN last_artist_check TIMESTAMP NULL DEFAULT NULL
                        """)
                        cursor.execute(f"""
                            ALTER TABLE `{table_name}`
                            ADD INDEX idx_last_artist_check (last_artist_check)
                        """)
                        logger.info(f"Added last_artist_check column to {table_name}")

                    if not has_artist_matched:
                        cursor.execute(f"""
                            ALTER TABLE `{table_name}`
                            ADD COLUMN artist_matched VARCHAR(255) NULL DEFAULT NULL
                        """)
                        cursor.execute(f"""
                            ALTER TABLE `{table_name}`
                            ADD INDEX idx_artist_matched (artist_matched)
                        """)
                        logger.info(f"Added artist_matched column to {table_name}")

                    conn.commit()
                    cursor.close()
                    break

                except Error as e:
                    if "lock" in str(e).lower() and attempt < 2:
                        time.sleep(0.5)
                    else:
                        # Don't fail on error - some tables might not support this
                        logger.debug(f"Skipped adding artist check columns to {table_name}: {e}")
                        break
                finally:
                    if conn:
                        conn.close()


    def ensure_dest_table(self, dest_key):
        """Ensure destination table exists with file metadata for optimization."""
        table = dest_key
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # Check if table exists with old schema
                cursor.execute(f"SHOW TABLES LIKE '{table}'")
                table_exists = cursor.fetchone() is not None

                if table_exists:
                    # Check if we need to migrate from old schema (file_name as PRIMARY KEY)
                    cursor.execute(f"SHOW KEYS FROM `{table}` WHERE Key_name = 'PRIMARY'")
                    primary_key_info = cursor.fetchall()
                    needs_migration = False

                    if primary_key_info:
                        # Check if PRIMARY KEY is only on file_name (old schema)
                        primary_columns = [row[4] for row in primary_key_info]  # Column_name is at index 4
                        if primary_columns == ['file_name']:
                            needs_migration = True
                            logger.info(f"Table {table} needs migration from old schema to new composite key schema")

                    if needs_migration:
                        # Create new table with modern perceptual hash schema
                        cursor.execute(f"""
                            CREATE TABLE IF NOT EXISTS `{table}_new` (
                                id INT AUTO_INCREMENT,
                                file_name VARCHAR(512) NOT NULL,
                                perceptual_hash_dhash VARCHAR(256),
                                perceptual_hash_phash VARCHAR(256),
                                perceptual_hash_average VARCHAR(256),
                                perceptual_hash_whash VARCHAR(256),
                                md5_hash VARCHAR(32),
                                e621_post_id INT NULL,
                                hash_method VARCHAR(64) DEFAULT 'md5',
                                hash_type VARCHAR(64) DEFAULT 'perceptual',
                                file_size BIGINT,
                                file_modified_at DATETIME NULL,
                                file_created_at DATETIME NULL,
                                file_path TEXT NOT NULL,
                                last_artist_check TIMESTAMP NULL DEFAULT NULL,
                                artist_matched VARCHAR(255) NULL DEFAULT NULL,
                                PRIMARY KEY (id),
                                UNIQUE KEY unique_file_path (file_path(500)),
                                INDEX idx_phash (perceptual_hash_phash(100)),
                                INDEX idx_dhash (perceptual_hash_dhash(100)),
                                INDEX idx_avg_hash (perceptual_hash_average(100)),
                                INDEX idx_whash (perceptual_hash_whash(100)),
                                INDEX idx_md5_hash (md5_hash),
                                INDEX idx_e621_post_id (e621_post_id),
                                INDEX idx_file_name (file_name),
                                INDEX idx_hash_method (hash_method),
                                INDEX idx_last_artist_check (last_artist_check),
                                INDEX idx_artist_matched (artist_matched)
                            )
                        """)

                        # Copy data from old table to new, migrating file_hash to perceptual_hash_dhash
                        cursor.execute(f"""
                            INSERT INTO `{table}_new`
                            (file_name, perceptual_hash_dhash, hash_method, hash_type, file_size, file_modified_at, file_created_at, file_path)
                            SELECT file_name, file_hash,
                                   COALESCE(hash_method, 'md5'),
                                   COALESCE(hash_type, 'perceptual'),
                                   file_size, file_modified_at, file_created_at,
                                   COALESCE(file_path, file_name)
                            FROM `{table}`
                            ON DUPLICATE KEY UPDATE
                                file_modified_at = VALUES(file_modified_at),
                                file_size = VALUES(file_size)
                        """)

                        # Drop old table and rename new one
                        cursor.execute(f"DROP TABLE `{table}`")
                        cursor.execute(f"RENAME TABLE `{table}_new` TO `{table}`")
                        logger.info(f"Successfully migrated {table} to new schema")
                else:
                    # Create new table with correct schema from the start
                    cursor.execute(f"""
                        CREATE TABLE IF NOT EXISTS `{table}` (
                            id INT AUTO_INCREMENT,
                            file_name VARCHAR(512) NOT NULL,
                            perceptual_hash_dhash VARCHAR(256),
                            perceptual_hash_phash VARCHAR(256),
                            perceptual_hash_average VARCHAR(256),
                            perceptual_hash_whash VARCHAR(256),
                            md5_hash VARCHAR(32),
                            e621_post_id INT NULL,
                            hash_method VARCHAR(64) DEFAULT 'md5',
                            hash_type VARCHAR(64) DEFAULT 'perceptual',
                            file_size BIGINT,
                            file_modified_at DATETIME NULL,
                            file_created_at DATETIME NULL,
                            file_path TEXT NOT NULL,
                            last_artist_check TIMESTAMP NULL DEFAULT NULL,
                            artist_matched VARCHAR(255) NULL DEFAULT NULL,
                            PRIMARY KEY (id),
                            UNIQUE KEY unique_file_path (file_path(500)),
                            INDEX idx_phash (perceptual_hash_phash(100)),
                            INDEX idx_dhash (perceptual_hash_dhash(100)),
                            INDEX idx_avg_hash (perceptual_hash_average(100)),
                            INDEX idx_whash (perceptual_hash_whash(100)),
                            INDEX idx_md5_hash (md5_hash),
                            INDEX idx_e621_post_id (e621_post_id),
                            INDEX idx_file_name (file_name),
                            INDEX idx_hash_method (hash_method),
                            INDEX idx_last_artist_check (last_artist_check),
                            INDEX idx_artist_matched (artist_matched)
                        )
                    """)

                # Migrate existing tables to new schema (for hash_method expansion)
                self._migrate_table_schema(cursor, table)

                conn.commit()
                cursor.close()
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def _migrate_table_schema(self, cursor, table):
        """Migrate existing table to new separate perceptual hash columns schema."""

        # Add new perceptual hash columns if they don't exist
        perceptual_hash_columns = [
            ('perceptual_hash_dhash', 'VARCHAR(256)', 'Difference hash'),
            ('perceptual_hash_phash', 'VARCHAR(256)', 'Perceptual hash'),
            ('perceptual_hash_average', 'VARCHAR(256)', 'Average hash'),
            ('perceptual_hash_whash', 'VARCHAR(256)', 'Wavelet hash')
        ]

        for col_name, col_type, comment in perceptual_hash_columns:
            cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE '{col_name}'")
            if not cursor.fetchone():
                cursor.execute(f"""
                    ALTER TABLE `{table}`
                    ADD COLUMN {col_name} {col_type} COMMENT '{comment}'
                """)
                logger.info(f"Added {col_name} column to {table}")

                # Add index for the new column
                try:
                    index_name = f"idx_{col_name.replace('perceptual_hash_', '')}"
                    cursor.execute(f"CREATE INDEX {index_name} ON `{table}` ({col_name}(100))")
                    logger.info(f"Created index {index_name} on {table}")
                except Error as e:
                    logger.warning(f"Could not create index {index_name}: {e}")

        # Check if old file_hash column exists (for backward compatibility during transition)
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'file_hash'")
        column_info = cursor.fetchone()
        if column_info:
            # Keep file_hash for now, but it's deprecated
            # Users can run cleanup_old_file_hash.py when ready to remove it
            logger.info(f"Table {table} still has deprecated file_hash column (can be removed with cleanup script)")

        # Add md5_hash column if it doesn't exist
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'md5_hash'")
        if not cursor.fetchone():
            cursor.execute(f"""
                ALTER TABLE `{table}`
                ADD COLUMN md5_hash VARCHAR(32)
            """)
            logger.info(f"Added md5_hash column to {table}")

        # Add hash_method column if it doesn't exist
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'hash_method'")
        if not cursor.fetchone():
            cursor.execute(f"""
                ALTER TABLE `{table}`
                ADD COLUMN hash_method VARCHAR(32) DEFAULT 'md5'
            """)
            logger.info(f"Added hash_method column to {table}")

        # Add hash_type column if it doesn't exist
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'hash_type'")
        if not cursor.fetchone():
            cursor.execute(f"""
                ALTER TABLE `{table}`
                ADD COLUMN hash_type VARCHAR(32) DEFAULT 'content'
            """)
            logger.info(f"Added hash_type column to {table}")

        # Add e621_post_id column if it doesn't exist
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'e621_post_id'")
        if not cursor.fetchone():
            cursor.execute(f"""
                ALTER TABLE `{table}`
                ADD COLUMN e621_post_id INT NULL COMMENT 'e621 post ID extracted from filename'
            """)
            logger.info(f"Added e621_post_id column to {table}")

            # Add index for e621_post_id
            try:
                cursor.execute(f"CREATE INDEX idx_e621_post_id ON `{table}` (e621_post_id)")
                logger.info(f"Created index idx_e621_post_id on {table}")
            except Error as e:
                logger.warning(f"Could not create index idx_e621_post_id: {e}")

        # Add missing metadata columns
        self._add_missing_metadata_columns(cursor, table)

        # Add md5_hash index if it doesn't exist
        try:
            cursor.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name = 'idx_md5_hash'")
            if not cursor.fetchone():
                cursor.execute(f"CREATE INDEX idx_md5_hash ON `{table}` (md5_hash)")
        except Error:
            pass  # Index might already exist

        # Add hash_method index if it doesn't exist
        try:
            cursor.execute(f"SHOW INDEX FROM `{table}` WHERE Key_name = 'idx_hash_method'")
            if not cursor.fetchone():
                cursor.execute(f"CREATE INDEX idx_hash_method ON `{table}` (hash_method)")
        except Error:
            pass  # Index might already exist

    def _add_missing_metadata_columns(self, cursor, table):
        """Ensure required metadata columns exist and drop legacy unix-time columns."""
        # Ensure file_size exists
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'file_size'")
        if not cursor.fetchone():
            cursor.execute(f"""
                ALTER TABLE `{table}`
                ADD COLUMN file_size BIGINT
            """)
        # Ensure new DATETIME columns exist
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'file_modified_at'")
        if not cursor.fetchone():
            cursor.execute(f"""
                ALTER TABLE `{table}`
                ADD COLUMN file_modified_at DATETIME NULL
            """)
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'file_created_at'")
        if not cursor.fetchone():
            cursor.execute(f"""
                ALTER TABLE `{table}`
                ADD COLUMN file_created_at DATETIME NULL
            """)
        # Ensure file_path exists
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'file_path'")
        if not cursor.fetchone():
            cursor.execute(f"""
                ALTER TABLE `{table}`
                ADD COLUMN file_path TEXT
            """)
        # Drop legacy columns if present (no backward compatibility required)
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'file_mtime'")
        if cursor.fetchone():
            cursor.execute(f"ALTER TABLE `{table}` DROP COLUMN file_mtime")
        cursor.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'created_at'")
        if cursor.fetchone():
            cursor.execute(f"ALTER TABLE `{table}` DROP COLUMN created_at")

    def table_name_for_dest(self, dest_dir):
        """Get table name for destination directory or archive directory."""
        def is_directory_or_child(configured_dir, candidate_dir):
            """Match a configured root and any directory created beneath it."""
            try:
                configured_path = os.path.normcase(os.path.abspath(os.path.normpath(configured_dir)))
                candidate_path = os.path.normcase(os.path.abspath(os.path.normpath(candidate_dir)))
                return os.path.commonpath([configured_path, candidate_path]) == configured_path
            except (TypeError, ValueError):
                return False

        # First check destination directories
        for key, path in self.destination_directories.items():
            if is_directory_or_child(path, dest_dir):
                return key

        # Handle dedicated deleted-from-e621 directory if configured
        deleted_dir = getattr(self.config, 'deleted_from_e621_directory', None)
        if deleted_dir and is_directory_or_child(deleted_dir, dest_dir):
            return "deleted_from_e621"

        # Handle dedicated archived-favorites directory if configured
        archived_fav_dir = getattr(self.config, 'archived_favorites_directory', None)
        if archived_fav_dir and is_directory_or_child(archived_fav_dir, dest_dir):
            return "archived_favorites"

        # Check archive directories if they exist
        archive_dirs = getattr(self.config, 'archive_directories', None) or self.config.config.get('archive_directories', {})
        if archive_dirs:
            for key, archive_config in archive_dirs.items():
                # Handle both simple format (string) and expanded format (dict)
                if isinstance(archive_config, str):
                    # Simple format: single path
                    if is_directory_or_child(archive_config, dest_dir):
                        return f"archive_{key}"
                elif isinstance(archive_config, dict):
                    # Expanded format: check each media type path
                    for media_type, path in archive_config.items():
                        if path and is_directory_or_child(path, dest_dir):
                            return f"archive_{key}_{media_type}"

        raise ValueError(f"No config key found for destination or archive directory: {dest_dir}")

    def get_all_table_names(self, include_archives=True):
        """Get all table names for destinations and optionally archives.

        Args:
            include_archives: If True, include archive tables in the list

        Returns:
            List of table names
        """
        tables = list(self.destination_directories.keys())

        if include_archives:
            archive_dirs = getattr(self.config, 'archive_directories', None) or self.config.config.get('archive_directories', {})
            if archive_dirs:
                for key, archive_config in archive_dirs.items():
                    # Handle both simple format (string) and expanded format (dict)
                    if isinstance(archive_config, str):
                        # Simple format: single table
                        tables.append(f"archive_{key}")
                    elif isinstance(archive_config, dict):
                        # Expanded format: multiple tables for each media type
                        for media_type in archive_config.keys():
                            if archive_config[media_type]:  # Only if path is configured
                                tables.append(f"archive_{key}_{media_type}")

            # Always include archived_favorites
            tables.append('archived_favorites')

        return tables

    def is_duplicate_in_any_dest(self, file_hash, check_archives=True):
        """Check if file hash is duplicate in any destination or archive table.

        Args:
            file_hash: Hash to check for duplicates
            check_archives: If True, also check archive tables

        Returns:
            Tuple of (table_name, file_name) if duplicate found, else (None, None)
        """
        tables_to_check = self.get_all_table_names(include_archives=check_archives)

        for table in tables_to_check:
            # Ensure table exists
            try:
                self.ensure_dest_table(table)
            except Exception as e:
                logger.debug(f"Could not ensure table {table}: {e}")
                continue

            # Check for duplicate in this table
            for attempt in range(5):
                conn = None
                try:
                    conn = self.get_db_connection()
                    cursor = conn.cursor()
                    # Check if hash matches any of the perceptual hash columns or md5_hash
                    cursor.execute(f"""
                        SELECT file_name, file_path
                        FROM `{table}`
                        WHERE perceptual_hash_phash = %s
                           OR perceptual_hash_dhash = %s
                           OR perceptual_hash_average = %s
                           OR perceptual_hash_whash = %s
                           OR md5_hash = %s
                        LIMIT 1
                    """, (file_hash, file_hash, file_hash, file_hash, file_hash))
                    row = cursor.fetchone()
                    cursor.close()

                    if row:
                        return (table, row[0])  # Return table name and filename
                    break  # No duplicate in this table, move to next

                except Error as e:
                    if "lock" in str(e).lower() and attempt < 4:
                        time.sleep(1)
                    else:
                        logger.warning(f"Error checking duplicates in {table}: {e}")
                        break
                finally:
                    if conn:
                        conn.close()

        return (None, None)  # No duplicate found

    def load_file_hash_from_dest(self, dest_dir, file_name):
        """Load file hash from destination table."""
        # Skip database operations for files in duplicate directory
        if os.path.abspath(dest_dir) == os.path.abspath(self.duplicate_directory):
            logger.debug(f"Skipping database load for file in duplicate directory: {file_name}")
            return None

        table = self.table_name_for_dest(dest_dir)
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT COALESCE(perceptual_hash_phash, perceptual_hash_dhash, perceptual_hash_average, perceptual_hash_whash, md5_hash)
                    FROM `{table}`
                    WHERE file_name = %s
                """, (file_name,))
                row = cursor.fetchone()
                cursor.close()
                return row[0] if row else None
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    return None
            finally:
                if conn:
                    conn.close()

    def save_file_hash_to_dest(self, dest_dir, file_name, file_hash, md5_hash=None):
        """Save file hash and metadata to destination table."""
        # Skip database operations for files in duplicate directory
        if os.path.abspath(dest_dir) == os.path.abspath(self.duplicate_directory):
            logger.debug(f"Skipping database save for file in duplicate directory: {file_name}")
            return

        table = self.table_name_for_dest(dest_dir)
        if table.startswith("questionable_"):
            table = table.replace("questionable_", "nsfw_")
        self.ensure_dest_table(table)

        # Get file metadata
        file_path = os.path.join(dest_dir, file_name)

        # Normalize file path to use forward slashes consistently
        from .utils import normalize_path, extract_e621_post_id
        normalized_path = normalize_path(file_path)

        # Extract e621 post ID from filename
        e621_post_id = extract_e621_post_id(file_name)

        # Split composite hash into separate components
        dhash_val = None
        phash_val = None
        avg_val = None
        whash_val = None

        if file_hash and '|' in file_hash:
            components = file_hash.split('|')
            if len(components) == 2:
                # Hybrid format: dhash|phash
                dhash_val = components[0]
                phash_val = components[1]
            elif len(components) == 4:
                # Multi format: avg|dhash|phash|whash
                avg_val = components[0]
                dhash_val = components[1]
                phash_val = components[2]
                whash_val = components[3]
        elif file_hash:
            # Single hash - assume it's phash (most common)
            phash_val = file_hash

        try:
            import datetime
            stat = os.stat(file_path)
            file_size = stat.st_size
            file_modified_at = datetime.datetime.fromtimestamp(stat.st_mtime)
            file_created_at = datetime.datetime.fromtimestamp(stat.st_ctime)
        except OSError:
            file_size = None
            file_modified_at = None
            file_created_at = None

        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # Smart insert/update logic:
                # 1. Check if this exact path already exists (simple update)
                # 2. If not, check if file with same hash exists at a different path that no longer exists
                # 3. If orphaned entry found, UPDATE it with new path (file was moved)
                # 4. Otherwise, INSERT new row (new file or duplicate in different location)

                # First, try simple insert/update for existing path
                cursor.execute(
                    f"""INSERT INTO `{table}`
                        (file_name, perceptual_hash_dhash, perceptual_hash_phash, perceptual_hash_average, perceptual_hash_whash,
                         md5_hash, e621_post_id, file_size, file_modified_at, file_created_at, file_path)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            file_name = VALUES(file_name),
                            perceptual_hash_dhash = VALUES(perceptual_hash_dhash),
                            perceptual_hash_phash = VALUES(perceptual_hash_phash),
                            perceptual_hash_average = VALUES(perceptual_hash_average),
                            perceptual_hash_whash = VALUES(perceptual_hash_whash),
                            md5_hash = VALUES(md5_hash),
                            e621_post_id = VALUES(e621_post_id),
                            file_size = VALUES(file_size),
                            file_modified_at = VALUES(file_modified_at),
                            file_created_at = VALUES(file_created_at)""",
                    (file_name, dhash_val, phash_val, avg_val, whash_val, md5_hash, e621_post_id,
                     file_size, file_modified_at, file_created_at, normalized_path)
                )

                conn.commit()
                cursor.close()
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def save_file_metadata_to_dest(self, dest_dir, file_name, metadata):
        """
        Save file with complete metadata to destination table.

        Args:
            dest_dir: Destination directory
            file_name: Filename
            metadata: Dict containing perceptual_hash_dhash, perceptual_hash_phash,
                     perceptual_hash_average, perceptual_hash_whash, md5_hash, e621_post_id,
                     file_size, file_modified_at, file_created_at
        """
        # Skip database operations for files in duplicate directory
        if os.path.abspath(dest_dir) == os.path.abspath(self.duplicate_directory):
            logger.debug(f"Skipping database save for file in duplicate directory: {file_name}")
            return

        table = self.table_name_for_dest(dest_dir)
        if table.startswith("questionable_"):
            table = table.replace("questionable_", "nsfw_")
        self.ensure_dest_table(table)

        # Get file path
        file_path = os.path.join(dest_dir, file_name)
        from .utils import normalize_path
        normalized_path = normalize_path(file_path)

        # Extract metadata with defaults
        dhash_val = metadata.get('perceptual_hash_dhash')
        phash_val = metadata.get('perceptual_hash_phash')
        avg_val = metadata.get('perceptual_hash_average')
        whash_val = metadata.get('perceptual_hash_whash')
        md5_hash = metadata.get('md5_hash')
        e621_post_id = metadata.get('e621_post_id')
        file_size = metadata.get('file_size')
        file_modified_at = metadata.get('file_modified_at')
        file_created_at = metadata.get('file_created_at')

        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                cursor.execute(
                    f"""INSERT INTO `{table}`
                        (file_name, perceptual_hash_dhash, perceptual_hash_phash, perceptual_hash_average, perceptual_hash_whash,
                         md5_hash, e621_post_id, file_size, file_modified_at, file_created_at, file_path)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            file_name = VALUES(file_name),
                            perceptual_hash_dhash = VALUES(perceptual_hash_dhash),
                            perceptual_hash_phash = VALUES(perceptual_hash_phash),
                            perceptual_hash_average = VALUES(perceptual_hash_average),
                            perceptual_hash_whash = VALUES(perceptual_hash_whash),
                            md5_hash = VALUES(md5_hash),
                            e621_post_id = VALUES(e621_post_id),
                            file_size = VALUES(file_size),
                            file_modified_at = VALUES(file_modified_at),
                            file_created_at = VALUES(file_created_at)""",
                    (file_name, dhash_val, phash_val, avg_val, whash_val, md5_hash, e621_post_id,
                     file_size, file_modified_at, file_created_at, normalized_path)
                )

                conn.commit()
                cursor.close()
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def get_dest_hashes(self, dest_dir):
        """Get all hashes and metadata from destination table."""
        # Skip database operations for files in duplicate directory
        if os.path.abspath(dest_dir) == os.path.abspath(self.duplicate_directory):
            logger.debug("Skipping database get_dest_hashes for duplicate directory")
            return {}

        table = self.table_name_for_dest(dest_dir)
        self.ensure_dest_table(table)
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT file_name,
                           COALESCE(perceptual_hash_phash, perceptual_hash_dhash, perceptual_hash_average, perceptual_hash_whash, md5_hash) as hash_value,
                           file_size,
                           file_modified_at,
                           file_created_at,
                           file_path
                    FROM `{table}`
                """)
                rows = cursor.fetchall()
                cursor.close()
                return {row[0]: {'hash': row[1], 'size': row[2], 'file_modified_at': row[3], 'file_created_at': row[4], 'path': row[5]} for row in rows}
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    return {}
            finally:
                if conn:
                    conn.close()

    def get_hashes_by_table(self, table_name):
        """Get all hashes and metadata from specific table."""
        self.ensure_dest_table(table_name)
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT file_name,
                           COALESCE(perceptual_hash_phash, perceptual_hash_dhash, perceptual_hash_average, perceptual_hash_whash, md5_hash) as hash_value,
                           file_size,
                           file_modified_at,
                           file_created_at,
                           file_path
                    FROM `{table_name}`
                """)
                rows = cursor.fetchall()
                cursor.close()
                return {row[0]: {'hash': row[1], 'size': row[2], 'file_modified_at': row[3], 'file_created_at': row[4], 'path': row[5]} for row in rows}
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    return {}
            finally:
                if conn:
                    conn.close()

    def update_dest_hash_cache(self, dest_dir, file_name, file_hash):
        """Update destination hash cache."""
        # Skip database operations for files in duplicate directory
        if os.path.abspath(dest_dir) == os.path.abspath(self.duplicate_directory):
            logger.debug(f"Skipping database update for file in duplicate directory: {file_name}")
            return

        table = self.table_name_for_dest(dest_dir)
        self.ensure_dest_table(table)

        # Get file path for the update
        file_path = os.path.join(dest_dir, file_name) if dest_dir and file_name else None

        # Normalize file path
        from .utils import normalize_path
        normalized_path = normalize_path(file_path) if file_path else None

        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                if file_hash is None:
                    # Delete by file_path if we have it, otherwise by file_name
                    if normalized_path:
                        cursor.execute(f"DELETE FROM `{table}` WHERE file_path = %s", (normalized_path,))
                    else:
                        cursor.execute(f"DELETE FROM `{table}` WHERE file_name = %s", (file_name,))
                else:
                    # Split composite hash into separate components
                    dhash_val = None
                    phash_val = None
                    avg_val = None
                    whash_val = None

                    if file_hash and '|' in file_hash:
                        components = file_hash.split('|')
                        if len(components) == 2:
                            # Hybrid format: dhash|phash
                            dhash_val = components[0]
                            phash_val = components[1]
                        elif len(components) == 4:
                            # Multi format: avg|dhash|phash|whash
                            avg_val = components[0]
                            dhash_val = components[1]
                            phash_val = components[2]
                            whash_val = components[3]
                    elif file_hash:
                        # Single hash - assume it's phash
                        phash_val = file_hash

                    # Insert or update
                    cursor.execute(
                        f"""INSERT INTO `{table}`
                            (file_name, perceptual_hash_dhash, perceptual_hash_phash,
                             perceptual_hash_average, perceptual_hash_whash, file_path)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON DUPLICATE KEY UPDATE
                                file_name = VALUES(file_name),
                                perceptual_hash_dhash = VALUES(perceptual_hash_dhash),
                                perceptual_hash_phash = VALUES(perceptual_hash_phash),
                                perceptual_hash_average = VALUES(perceptual_hash_average),
                                perceptual_hash_whash = VALUES(perceptual_hash_whash)""",
                        (file_name, dhash_val, phash_val, avg_val, whash_val, normalized_path)
                    )
                conn.commit()
                cursor.close()
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def save_file_hash_to_dest_with_table(self, table, file_id, file_hash, file_path=None, md5_hash=None):
        """Save file hash and metadata to specific table."""
        if table.startswith("questionable_"):
            table = table.replace("questionable_", "nsfw_")
        self.ensure_dest_table(table)

        # Normalize file path to use forward slashes consistently
        from .utils import normalize_path, extract_e621_post_id
        normalized_path = normalize_path(file_path)

        # Extract e621 post ID from filename
        e621_post_id = extract_e621_post_id(file_id) if file_id else None

        # Get file metadata if file_path is provided
        file_size = None
        file_modified_at = None
        file_created_at = None
        if file_path and os.path.exists(file_path):
            try:
                import datetime
                stat = os.stat(file_path)
                file_size = stat.st_size
                file_modified_at = datetime.datetime.fromtimestamp(stat.st_mtime)
                file_created_at = datetime.datetime.fromtimestamp(stat.st_ctime)
            except OSError:
                pass

        # Determine hash method and type from MediaHasher if available
        hash_method, hash_type = self._get_hash_metadata(file_path)

        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # Split composite hash into separate components
                dhash_val = None
                phash_val = None
                avg_val = None
                whash_val = None

                if file_hash and '|' in file_hash:
                    components = file_hash.split('|')
                    if len(components) == 2:
                        # Hybrid format: dhash|phash
                        dhash_val = components[0]
                        phash_val = components[1]
                    elif len(components) == 4:
                        # Multi format: avg|dhash|phash|whash
                        avg_val = components[0]
                        dhash_val = components[1]
                        phash_val = components[2]
                        whash_val = components[3]
                elif file_hash:
                    # Single hash - assume it's phash
                    phash_val = file_hash

                # Use INSERT ... ON DUPLICATE KEY UPDATE for batch operations too
                cursor.execute(
                    f"""INSERT INTO `{table}`
                        (file_name, perceptual_hash_dhash, perceptual_hash_phash, perceptual_hash_average, perceptual_hash_whash,
                         md5_hash, e621_post_id, hash_method, hash_type, file_size, file_modified_at, file_created_at, file_path)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            file_name = VALUES(file_name),
                            perceptual_hash_dhash = VALUES(perceptual_hash_dhash),
                            perceptual_hash_phash = VALUES(perceptual_hash_phash),
                            perceptual_hash_average = VALUES(perceptual_hash_average),
                            perceptual_hash_whash = VALUES(perceptual_hash_whash),
                            md5_hash = VALUES(md5_hash),
                            e621_post_id = VALUES(e621_post_id),
                            hash_method = VALUES(hash_method),
                            hash_type = VALUES(hash_type),
                            file_size = VALUES(file_size),
                            file_modified_at = VALUES(file_modified_at),
                            file_created_at = VALUES(file_created_at)""",
                    (file_id, dhash_val, phash_val, avg_val, whash_val, md5_hash, e621_post_id, hash_method, hash_type,
                     file_size, file_modified_at, file_created_at, normalized_path)
                )
                conn.commit()
                cursor.close()
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def _get_hash_metadata(self, file_path):
        """Get hash method and type information from MediaHasher."""
        if not file_path:
            return 'unknown', 'content'

        try:
            from .media_hasher import MediaHasher
            hasher = MediaHasher()
            file_ext = os.path.splitext(file_path)[1].lower() if file_path else ''
            file_type = hasher._get_file_type(file_ext)
            hash_method = hasher._get_hash_method(file_type)

            # Map MediaHasher types to our database types
            type_mapping = {
                'perceptual': 'perceptual',
                'audio_content': 'audio',
                'smart_content': 'content'
            }

            return hash_method, type_mapping.get(hash_method, 'content')

        except Exception as e:
            logger.warning(f"Could not determine hash metadata for {file_path}: {e}")
            return 'md5', 'content'  # Fallback to legacy values

    def is_duplicate_in_dest(self, dest_dir, file_hash, include_path=False):
        """Check if file hash is duplicate in destination.

        When ``include_path`` is true, return ``(file_name, file_path)`` so
        callers can distinguish a real duplicate from a stale database row.
        """
        # Skip database operations for files in duplicate directory
        if os.path.abspath(dest_dir) == os.path.abspath(self.duplicate_directory):
            logger.debug("Skipping duplicate check for duplicate directory")
            return False

        table = self.table_name_for_dest(dest_dir)
        if table.startswith("questionable_"):
            table = table.replace("questionable_", "nsfw_")
        self.ensure_dest_table(table)

        # Parse composite hash into components
        dhash_val = None
        phash_val = None
        avg_val = None
        whash_val = None
        md5_val = None

        if file_hash:
            if '|' in file_hash:
                components = file_hash.split('|')
                if len(components) == 2:
                    # Hybrid format: dhash|phash
                    dhash_val = components[0]
                    phash_val = components[1]
                elif len(components) == 4:
                    # Multi format: avg|dhash|phash|whash
                    avg_val = components[0]
                    dhash_val = components[1]
                    phash_val = components[2]
                    whash_val = components[3]
            else:
                # Single hash - could be any type, check all
                phash_val = file_hash
                md5_val = file_hash

        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # Build dynamic WHERE clause based on which hash components we have
                conditions = []
                params = []

                if phash_val:
                    conditions.append("perceptual_hash_phash = %s")
                    params.append(phash_val)

                if dhash_val:
                    conditions.append("perceptual_hash_dhash = %s")
                    params.append(dhash_val)

                if avg_val:
                    conditions.append("perceptual_hash_average = %s")
                    params.append(avg_val)

                if whash_val:
                    conditions.append("perceptual_hash_whash = %s")
                    params.append(whash_val)

                if md5_val:
                    conditions.append("md5_hash = %s")
                    params.append(md5_val)

                if not conditions:
                    return None

                # For composite hashes, use AND to match all components
                # For single hash, use OR to check all possible columns
                if len(params) > 1 and (dhash_val or avg_val or whash_val):
                    # Composite hash - must match all components (AND logic)
                    where_clause = " AND ".join(conditions)
                else:
                    # Single hash - match any column (OR logic)
                    where_clause = " OR ".join(conditions)

                query = f"SELECT file_name, file_path FROM `{table}` WHERE {where_clause}"
                cursor.execute(query, params)
                row = cursor.fetchone()
                cursor.close()
                if not row:
                    return None
                return (row[0], row[1]) if include_path else row[0]
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    return None
            finally:
                if conn:
                    conn.close()

    def execute_query(self, query, params=None):
        """Execute a custom SQL query and return all rows.

        Args:
            query: SQL query string
            params: Optional list of parameters for parameterized query

        Returns:
            List of tuples (rows) from the query result
        """
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                rows = cursor.fetchall()
                cursor.close()
                return rows
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    logger.error(f"Database query failed: {e}")
                    return []
            finally:
                if conn:
                    conn.close()

    def get_all_files_from_table(self, table):
        """Get all file entries from a specific table."""
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT file_name,
                           COALESCE(perceptual_hash_phash, perceptual_hash_dhash, perceptual_hash_average, perceptual_hash_whash, md5_hash) as hash_value,
                           file_created_at,
                           file_path
                    FROM `{table}`
                """)
                rows = cursor.fetchall()
                cursor.close()
                return [(row[0], row[1], row[2], row[3]) for row in rows]
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    return []
            finally:
                if conn:
                    conn.close()

    def remove_file_from_table(self, table, file_name):
        """Remove a file entry from a specific table."""
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM `{table}` WHERE file_name = %s", (file_name,))
                conn.commit()
                cursor.close()
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def remove_file_row(self, table, file_name, file_path):
        """Remove the one entry for ``file_name`` stored at ``file_path``."""
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM `{table}` WHERE file_name = %s AND file_path = %s",
                               (file_name, file_path))
                conn.commit()
                cursor.close()
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def get_file_metadata(self, table, file_name):
        """
        Retrieve full file metadata from a table.

        Returns a dict with: perceptual_hash_dhash, perceptual_hash_phash, perceptual_hash_average,
        perceptual_hash_whash, md5_hash, e621_post_id, file_size, file_modified_at, file_created_at
        """
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"""
                    SELECT perceptual_hash_dhash, perceptual_hash_phash, perceptual_hash_average,
                           perceptual_hash_whash, md5_hash, e621_post_id, file_size,
                           file_modified_at, file_created_at
                    FROM `{table}`
                    WHERE file_name = %s
                    LIMIT 1
                """, (file_name,))
                row = cursor.fetchone()
                cursor.close()

                if row:
                    return {
                        'perceptual_hash_dhash': row[0],
                        'perceptual_hash_phash': row[1],
                        'perceptual_hash_average': row[2],
                        'perceptual_hash_whash': row[3],
                        'md5_hash': row[4],
                        'e621_post_id': row[5],
                        'file_size': row[6],
                        'file_modified_at': row[7],
                        'file_created_at': row[8]
                    }
                return None
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    logger.error(f"Error getting file metadata from {table}: {e}")
                    return None
            finally:
                if conn:
                    conn.close()

    def move_entry_between_tables(self, source_table, target_table, file_name, new_path=None):
        """Move a single file record from source_table to target_table, updating the file_path.

        If the source record is not found, returns False. On success returns True.
        """
        if source_table == target_table:
            return False

        # Ensure tables exist
        self.ensure_dest_table(source_table)
        self.ensure_dest_table(target_table)

        # Normalize path for consistent storage
        from .utils import normalize_path
        normalized_path = normalize_path(new_path) if new_path else None

        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                cursor.execute(
                    f"""
                        SELECT file_name, perceptual_hash_dhash, perceptual_hash_phash,
                               perceptual_hash_average, perceptual_hash_whash, md5_hash,
                               e621_post_id, hash_method, hash_type, file_size,
                               file_modified_at, file_created_at
                        FROM `{source_table}`
                        WHERE file_name = %s
                        LIMIT 1
                    """,
                    (file_name,)
                )
                row = cursor.fetchone()

                if not row:
                    cursor.close()
                    return False

                (
                    fname,
                    dhash_val,
                    phash_val,
                    avg_val,
                    whash_val,
                    md5_hash,
                    e621_post_id,
                    hash_method,
                    hash_type,
                    file_size,
                    file_modified_at,
                    file_created_at,
                ) = row

                cursor.execute(
                    f"""
                        INSERT INTO `{target_table}`
                            (file_name, perceptual_hash_dhash, perceptual_hash_phash,
                             perceptual_hash_average, perceptual_hash_whash, md5_hash,
                             e621_post_id, hash_method, hash_type, file_size,
                             file_modified_at, file_created_at, file_path)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            file_name = VALUES(file_name),
                            perceptual_hash_dhash = VALUES(perceptual_hash_dhash),
                            perceptual_hash_phash = VALUES(perceptual_hash_phash),
                            perceptual_hash_average = VALUES(perceptual_hash_average),
                            perceptual_hash_whash = VALUES(perceptual_hash_whash),
                            md5_hash = VALUES(md5_hash),
                            e621_post_id = VALUES(e621_post_id),
                            hash_method = VALUES(hash_method),
                            hash_type = VALUES(hash_type),
                            file_size = VALUES(file_size),
                            file_modified_at = VALUES(file_modified_at),
                            file_created_at = VALUES(file_created_at),
                            file_path = VALUES(file_path)
                    """,
                    (
                        fname,
                        dhash_val,
                        phash_val,
                        avg_val,
                        whash_val,
                        md5_hash,
                        e621_post_id,
                        hash_method,
                        hash_type,
                        file_size,
                        file_modified_at,
                        file_created_at,
                        normalized_path,
                    ),
                )

                # Remove from source table after successful insert
                cursor.execute(
                    f"DELETE FROM `{source_table}` WHERE file_name = %s",
                    (file_name,),
                )

                conn.commit()
                cursor.close()
                return True
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def update_file_path(self, table, file_name, new_path):
        """Update file_path for a record in-place without changing tables.

        A table can hold more than one row for ``file_name`` (a stale row left at an old
        path, or a real copy elsewhere). Only one row is moved to ``new_path`` - preferably
        one whose file is gone - and other rows whose file no longer exists are dropped,
        so the path's unique key is never violated. Rows for copies still on disk are kept.

        Returns True if a row was updated, False if not found.
        """
        self.ensure_dest_table(table)

        from .utils import normalize_path
        normalized_path = normalize_path(new_path)

        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"SELECT id, file_path FROM `{table}` WHERE file_name = %s", (file_name,))
                rows = cursor.fetchall()
                if not rows:
                    cursor.close()
                    return False
                at_target = [row_id for row_id, path in rows if path == normalized_path]
                others = [(row_id, path) for row_id, path in rows if path != normalized_path]
                if not at_target:
                    # Move the row whose file is gone (the one that was moved), if any
                    others.sort(key=lambda row: os.path.exists(row[1]))
                    row_id, _ = others.pop(0)
                    cursor.execute(f"UPDATE `{table}` SET file_path = %s WHERE id = %s",
                                   (normalized_path, row_id))
                stale_ids = [row_id for row_id, path in others if not os.path.exists(path)]
                for row_id in stale_ids:
                    cursor.execute(f"DELETE FROM `{table}` WHERE id = %s", (row_id,))
                if stale_ids:
                    logger.info(f"Removed {len(stale_ids)} stale row(s) for {file_name} from {table}")
                conn.commit()
                cursor.close()
                return True
            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

    def copy_file_entry_between_tables(self, source_table, dest_table, file_name, new_path):
        """
        Copy a file entry from one table to another and delete from source.
        Used when files are moved between different destinations (e.g., nsfw_image -> favorites).

        Args:
            source_table: Source table name
            dest_table: Destination table name
            file_name: File name to copy
            new_path: New file path for the destination

        Returns:
            bool: True if entry was copied and deleted, False if not found
        """
        self.ensure_dest_table(source_table)
        self.ensure_dest_table(dest_table)

        from .utils import normalize_path
        normalized_path = normalize_path(new_path)

        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # Get the full entry from source table including artist check columns
                cursor.execute(f"""
                    SELECT file_name, perceptual_hash_dhash, perceptual_hash_phash,
                           perceptual_hash_average, perceptual_hash_whash, md5_hash,
                           e621_post_id, hash_method, hash_type, file_size,
                           file_modified_at, file_created_at, last_artist_check, artist_matched
                    FROM `{source_table}`
                    WHERE file_name = %s
                """, (file_name,))

                row = cursor.fetchone()
                if not row:
                    cursor.close()
                    return False

                # Insert into destination table with new path and artist check columns
                cursor.execute(f"""
                    INSERT INTO `{dest_table}`
                        (file_name, perceptual_hash_dhash, perceptual_hash_phash,
                         perceptual_hash_average, perceptual_hash_whash, md5_hash,
                         e621_post_id, hash_method, hash_type, file_size,
                         file_modified_at, file_created_at, file_path, last_artist_check, artist_matched)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        perceptual_hash_dhash = VALUES(perceptual_hash_dhash),
                        perceptual_hash_phash = VALUES(perceptual_hash_phash),
                        perceptual_hash_average = VALUES(perceptual_hash_average),
                        perceptual_hash_whash = VALUES(perceptual_hash_whash),
                        md5_hash = VALUES(md5_hash),
                        e621_post_id = VALUES(e621_post_id),
                        hash_method = VALUES(hash_method),
                        hash_type = VALUES(hash_type),
                        file_size = VALUES(file_size),
                        file_modified_at = VALUES(file_modified_at),
                        file_created_at = VALUES(file_created_at),
                        file_path = VALUES(file_path),
                        last_artist_check = VALUES(last_artist_check),
                        artist_matched = VALUES(artist_matched)
                """, (
                    row[0],  # file_name
                    row[1],  # perceptual_hash_dhash
                    row[2],  # perceptual_hash_phash
                    row[3],  # perceptual_hash_average
                    row[4],  # perceptual_hash_whash
                    row[5],  # md5_hash
                    row[6],  # e621_post_id
                    row[7],  # hash_method
                    row[8],  # hash_type
                    row[9],  # file_size
                    row[10], # file_modified_at
                    row[11], # file_created_at
                    normalized_path,  # new file_path
                    row[12], # last_artist_check
                    row[13]  # artist_matched
                ))

                # Delete from source table
                cursor.execute(f"""
                    DELETE FROM `{source_table}`
                    WHERE file_name = %s
                """, (file_name,))

                conn.commit()
                cursor.close()
                return True

            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    raise
            finally:
                if conn:
                    conn.close()

        return False

    def clear_table(self, table):
        """Clear all entries from a table. Used for temporary tables that should be cleared before each run."""
        for attempt in range(5):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                # Ensure table exists first
                self.ensure_dest_table(table)

                # Clear all entries
                cursor.execute(f"DELETE FROM `{table}`")
                deleted_count = cursor.rowcount
                conn.commit()
                cursor.close()

                logger.info(f"Cleared {deleted_count} entries from table '{table}'")
                return deleted_count

            except Error as e:
                if "lock" in str(e).lower() and attempt < 4:
                    time.sleep(1)
                else:
                    logger.error(f"Failed to clear table {table}: {e}")
                    raise
            finally:
                if conn:
                    conn.close()

    def cleanup_missing_files(self, table, existing_files_set):
        """Remove database entries for files that no longer exist in the directory.

        Also drops stale duplicates: when a file name has several rows, rows whose path no
        longer exists are removed (the file was found on disk, just somewhere else).
        """
        db_files = self.get_all_files_from_table(table)
        removed_count = 0
        name_counts = {}
        for file_name, _, _, _ in db_files:
            name_counts[file_name] = name_counts.get(file_name, 0) + 1

        for file_name, file_hash, created_at, file_path in db_files:
            if file_name not in existing_files_set:
                self.remove_file_from_table(table, file_name)
                removed_count += 1
                logger.info(f"Removed missing file from {table}: {file_name}")
            elif name_counts[file_name] > 1 and file_path and not os.path.exists(file_path):
                self.remove_file_row(table, file_name, file_path)
                name_counts[file_name] -= 1
                removed_count += 1
                logger.info(f"Removed stale duplicate row from {table}: {file_path}")

        return removed_count

    # ==================== File Operations Logging ====================

    def log_file_operation(self, operation_type, file_name, source_path=None, destination_path=None,
                          source_directory=None, destination_directory=None,
                          file_size=None, operation_status='success', error_message=None, reason=None):
        """
        Log a file operation to the database.

        Args:
            operation_type: Type of operation ('move', 'copy', 'delete', 'rename', 'create')
            file_name: Name of the file
            source_path: Full source path of the file
            destination_path: Full destination path of the file
            source_directory: Source directory name/type
            destination_directory: Destination directory name/type
            file_size: Size of the file in bytes
            operation_status: Status of the operation ('success', 'failed', 'partial')
            error_message: Error message if operation failed
            reason: Reason for the operation (e.g., 'duplicate_detected', 'artist_sort')
        """
        from .utils import normalize_path

        # Normalize paths if provided
        if source_path:
            source_path = normalize_path(source_path)
        if destination_path:
            destination_path = normalize_path(destination_path)

        for attempt in range(3):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT INTO file_operations (
                        operation_type, file_name, source_path, destination_path,
                        source_directory, destination_directory, file_size,
                        operation_status, error_message, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    operation_type, file_name, source_path, destination_path,
                    source_directory, destination_directory, file_size,
                    operation_status, error_message, reason
                ))

                conn.commit()
                cursor.close()
                logger.debug(f"Logged file operation: {operation_type} for {file_name}")
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 2:
                    time.sleep(0.5)
                else:
                    # Don't raise - logging failure shouldn't stop file operations
                    logger.warning(f"Failed to log file operation: {e}")
            finally:
                if conn:
                    conn.close()

    def log_move_operation(self, file_name, source_path, destination_path,
                          source_directory=None, destination_directory=None,
                          file_size=None, reason=None):
        """Log a file move operation."""
        self.log_file_operation(
            operation_type='move',
            file_name=file_name,
            source_path=source_path,
            destination_path=destination_path,
            source_directory=source_directory,
            destination_directory=destination_directory,
            file_size=file_size,
            reason=reason
        )

    def log_copy_operation(self, file_name, source_path, destination_path,
                          source_directory=None, destination_directory=None,
                          file_size=None, reason=None):
        """Log a file copy operation."""
        self.log_file_operation(
            operation_type='copy',
            file_name=file_name,
            source_path=source_path,
            destination_path=destination_path,
            source_directory=source_directory,
            destination_directory=destination_directory,
            file_size=file_size,
            reason=reason
        )

    def log_delete_operation(self, file_name, source_path, source_directory=None,
                            file_hash=None, file_size=None, reason=None):
        """Log a file delete operation."""
        self.log_file_operation(
            operation_type='delete',
            file_name=file_name,
            source_path=source_path,
            source_directory=source_directory,
            # file_hash is not part of the file_operations schema.
            file_size=file_size,
            reason=reason
        )

    def log_rename_operation(self, old_name, new_name, directory_path,
                            file_hash=None, file_size=None, reason=None):
        """Log a file rename operation."""
        import os
        self.log_file_operation(
            operation_type='rename',
            file_name=new_name,
            source_path=os.path.join(directory_path, old_name),
            destination_path=os.path.join(directory_path, new_name),
            source_directory=directory_path,
            destination_directory=directory_path,
            # file_hash is not part of the file_operations schema.
            file_size=file_size,
            reason=reason
        )

    def log_failed_operation(self, operation_type, file_name, source_path=None,
                            destination_path=None, error_message=None, reason=None):
        """Log a failed file operation."""
        self.log_file_operation(
            operation_type=operation_type,
            file_name=file_name,
            source_path=source_path,
            destination_path=destination_path,
            operation_status='failed',
            error_message=error_message,
            reason=reason
        )

    # ==================== Artist Check Methods ====================

    def update_artist_check(self, table_name, file_name, matched_artist=None):
        """
        Update artist check timestamp and matched artist for a file.

        Args:
            table_name: Database table containing the file
            file_name: Name of the file
            matched_artist: Canonical artist name if matched, None if no match
        """
        for attempt in range(3):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                cursor.execute(f"""
                    UPDATE `{table_name}`
                    SET last_artist_check = NOW(),
                        artist_matched = %s
                    WHERE file_name = %s
                """, (matched_artist, file_name))

                conn.commit()
                cursor.close()
                logger.debug(f"Updated artist check for {file_name} in {table_name}")
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 2:
                    time.sleep(0.5)
                else:
                    logger.warning(f"Failed to update artist check: {e}")
            finally:
                if conn:
                    conn.close()

    def update_artist_checks_batch(self, table_name, updates):
        """Batch-update artist check timestamp/match for many files in one round trip.

        Same effect as calling update_artist_check() once per file, but issues a
        single executemany() + commit instead of one connection/commit per file —
        matters when scanning tens of thousands of candidates.

        Args:
            table_name: Database table containing the files
            updates: List of (matched_artist, file_name) tuples
        """
        if not updates:
            return
        for attempt in range(3):
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()
                cursor.executemany(f"""
                    UPDATE `{table_name}`
                    SET last_artist_check = NOW(),
                        artist_matched = %s
                    WHERE file_name = %s
                """, updates)
                conn.commit()
                cursor.close()
                logger.debug(f"Batch-updated artist check for {len(updates)} file(s) in {table_name}")
                return
            except Error as e:
                if "lock" in str(e).lower() and attempt < 2:
                    time.sleep(0.5)
                else:
                    logger.warning(f"Failed to batch update artist check: {e}")
            finally:
                if conn:
                    conn.close()

    def clear_artist_checks(self, table_name=None, older_than_days=None):
        """
        Reset artist check timestamps to force re-checking.

        Args:
            table_name: Specific table to clear, or None for all tables
            older_than_days: Only clear checks older than this many days

        Returns:
            Number of records cleared
        """
        tables = [table_name] if table_name else self.get_all_table_names(include_archives=True)
        total_cleared = 0

        for table in tables:
            conn = None
            try:
                conn = self.get_db_connection()
                cursor = conn.cursor()

                if older_than_days:
                    cursor.execute(f"""
                        UPDATE `{table}`
                        SET last_artist_check = NULL,
                            artist_matched = NULL
                        WHERE last_artist_check < DATE_SUB(NOW(), INTERVAL %s DAY)
                    """, (older_than_days,))
                else:
                    cursor.execute(f"""
                        UPDATE `{table}`
                        SET last_artist_check = NULL,
                            artist_matched = NULL
                    """)

                count = cursor.rowcount
                total_cleared += count
                conn.commit()
                cursor.close()

                if count > 0:
                    logger.info(f"Cleared {count} artist checks from {table}")
            except Error as e:
                logger.warning(f"Failed to clear artist checks from {table}: {e}")
            finally:
                if conn:
                    conn.close()

        return total_cleared

    def close_connection_pool(self):
        """Close the connection pool and clean up resources."""
        try:
            if self._pool:
                # Note: MySQL connector doesn't have a direct close method for pools
                # Connections will be automatically closed when the pool is garbage collected
                logger.info("Connection pool cleanup initiated")
                self._pool = None
        except Exception as e:
            logger.error(f"Error closing connection pool: {e}")

    def __del__(self):
        """Cleanup when the DatabaseManager is destroyed."""
        self.close_connection_pool()
