# fff: connects to the active environment's archive schema (db_names()['archive']) with the
# fff: sorter config's server settings; logs go to logs/sorter, configured when main() runs.
# fff: main(argv=None) reads the same --dry-run / -n flags from argv (default sys.argv[1:]).
"""
Standalone script to fix mismatched filenames.
Queries e621 posts database to find files where the actual filename on disk
doesn't match the correct filename in the database, then renames them.
"""
import os
import sys
import yaml
import mysql.connector
from mysql.connector import Error
import logging
from pathlib import Path
from datetime import datetime

from fff.common import config as fff_config
from fff.sorter import paths as sorter_paths

# Fix Windows console encoding for Unicode characters
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Configure logging
# fff: logs/sorter (was ./Logs). Set up by main() rather than at import, so importing the
# module (fff CLI, tests) does not create an empty log file.
log_dir = sorter_paths.LOG_DIR
log_file = None


def _setup_logging():
    global log_file
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"fix_no_source_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )


logger = logging.getLogger(__name__)


def load_config():
    """Load database configuration from config.yaml."""
    # fff: the sorter view of the unified config (same dict shape; was ./configs/config.yaml)
    try:
        return fff_config.legacy_view("sorter")
    except fff_config.ConfigError as e:
        logger.error(f"Config file not found: {e}")
        sys.exit(1)


def get_db_connection(config):
    """Get connection to e621_posts_db database."""
    mysql_config = config.get('mysql', {})

    # Use same host/port/user/password but connect to e621_posts_db
    conn_config = {
        'host': mysql_config.get('host'),
        'port': mysql_config.get('port', 3306),
        'user': mysql_config.get('user'),
        'password': mysql_config.get('password'),
        'database': sorter_paths.db_names()['archive'],  # fff: the e621 posts database (prod or test_)
        'charset': 'utf8mb4',
        'use_unicode': True
    }

    try:
        conn = mysql.connector.connect(**conn_config)
        logger.info(f"Connected to {conn_config['database']} at {conn_config['host']}")  # fff: actual schema
        return conn
    except Error as e:
        logger.error(f"Failed to connect to database: {e}")
        sys.exit(1)


def find_no_source_files(conn):
    """Query database for files where filename doesn't match the actual file on disk."""
    query = """
        SELECT
            p.id AS post_id,
            p.file_name AS correct_file_name,
            SUBSTRING_INDEX(REPLACE(p.file_path, '\\\\', '/'), '/', -1) AS current_file_name,
            REPLACE(p.file_path, '\\\\', '/') AS file_path,
            CONCAT('https://e621.net/posts/', p.id) AS post_link,
            GROUP_CONCAT(DISTINCT a.name SEPARATOR ', ') AS artist_names,
            p.created_at,
            p.updated_at
        FROM posts p
        LEFT JOIN posts_artists pa ON p.id = pa.post_id
        LEFT JOIN artists a ON pa.artist_id = a.artist_id
        LEFT JOIN post_takedowns pt ON p.id = pt.post_id
        WHERE p.file_name != SUBSTRING_INDEX(REPLACE(p.file_path, '\\\\', '/'), '/', -1)
          AND p.file_path NOT LIKE '%/- Fav/Artists/%'
          AND p.file_path NOT LIKE '%/Tag Sorted/%'
          AND p.file_path NOT LIKE '%/Pokemon/%'
          AND p.file_path NOT LIKE '%/Characters/%'
        GROUP BY p.id, p.file_path
        ORDER BY p.created_at DESC
    """

    cursor = conn.cursor(dictionary=True)
    cursor.execute(query)
    results = cursor.fetchall()
    cursor.close()

    logger.info(f"Found {len(results)} files with mismatched filenames (excluding sorted directories)")
    return results


def rename_file(old_path, new_filename, dry_run=False):
    """
    Rename a file to the correct filename.

    Args:
        old_path: Full path to the current file
        new_filename: The correct filename (just the name, not full path)
        dry_run: If True, don't actually rename, just log what would happen

    Returns:
        True if successful (or would be successful in dry run), False otherwise
    """
    old_path = Path(old_path)

    if not old_path.exists():
        logger.warning(f"File not found: {old_path}")
        return False

    # Get directory and construct new path
    directory = old_path.parent
    new_path = directory / new_filename

    # Check if target already exists
    if new_path.exists():
        logger.warning(f"Target already exists: {new_path}")
        return False

    # Check if already correctly named
    if old_path.name == new_filename:
        logger.debug(f"Already correctly named: {old_path}")
        return True

    if dry_run:
        logger.info(f"[DRY RUN] Would rename:")
        logger.info(f"  FROM: {old_path}")
        logger.info(f"  TO:   {new_path}")
        return True

    try:
        old_path.rename(new_path)
        logger.info(f"✓ Renamed successfully:")
        logger.info(f"  FROM: {old_path}")
        logger.info(f"  TO:   {new_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to rename {old_path}: {e}")
        return False


def main(argv=None):
    """Main function."""
    # Parse command line arguments
    argv = sys.argv[1:] if argv is None else argv  # fff: same flags, argv injectable
    dry_run = '--dry-run' in argv or '-n' in argv
    _setup_logging()

    print("=" * 80)
    print("FIX MISMATCHED FILENAMES")
    print("=" * 80)
    if dry_run:
        print("[DRY RUN MODE] - No files will actually be renamed")
        print()

    # Load config
    logger.info("Loading configuration...")
    config = load_config()

    # Connect to database
    logger.info("Connecting to database...")
    conn = get_db_connection(config)

    try:
        # Query for files
        logger.info("Querying for files with mismatched filenames...")
        files = find_no_source_files(conn)

        if not files:
            logger.info("No files with mismatched filenames found")
            return

        # Display summary
        print(f"\nFound {len(files)} files with mismatched filenames:")
        print("-" * 80)

        renamed_count = 0
        skipped_count = 0
        error_count = 0

        for file_info in files:
            post_id = file_info['post_id']
            current_name = file_info['current_file_name']
            correct_name = file_info['correct_file_name']
            file_path = file_info['file_path']
            artist_names = file_info['artist_names'] or "Unknown"
            post_link = file_info['post_link']

            print(f"\nPost {post_id}: {post_link}")
            print(f"  Artists: {artist_names}")
            print(f"  Current: {current_name}")
            print(f"  Correct: {correct_name}")
            print(f"  Path: {file_path}")

            # Attempt rename
            if current_name == correct_name:
                print(f"  ✓ Already correct")
                skipped_count += 1
            else:
                success = rename_file(file_path, correct_name, dry_run=dry_run)
                if success:
                    renamed_count += 1
                else:
                    error_count += 1

        # Summary
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        action_word = "Would be renamed" if dry_run else "Renamed"
        print(f"{action_word}: {renamed_count}")
        print(f"Already correct: {skipped_count}")
        print(f"Errors: {error_count}")
        print(f"Total processed: {len(files)}")
        print(f"\nLog file: {log_file}")

        if dry_run:
            print("\nTo actually rename files, run without --dry-run flag")

    finally:
        conn.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)
