#!/usr/bin/env python3
# fff: run as 'fff fa sync-external'; config and the e621_posts_db name come from fff config.
"""
External FurAffinity Source Links Monitor
Pulls FA source links from e621_posts_db.source_links table, tracks them, and auto-favorites them.
- Pulls new FA source links from e621_posts_db each run
- Checks if each post is favorited
- Automatically favorites posts that aren't favorited yet
- Tracks status in local database
"""

import sys
import time
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
import yaml
import mysql.connector
from mysql.connector import Error

# FurAffinity functions use the shared client with retry/backoff.
from fff.clients.furaffinity import (
    get_firefox_cookies,
    check_if_fa_favorited,
    add_to_fa_favorites,
    FASubmissionNotFoundError
)
from fff.common import config as fff_config
from fff.common.logging import logged_command

MAX_CHECK_ATTEMPTS = 10  # a source that keeps failing stops being retried after this many runs


def load_config(config_path: str = None) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    # fff: the reverse search config view (database = image_search or test_image_search)
    if config_path is None:
        return fff_config.legacy_view("fluffle")
    if config_path is None:
        possible_paths = [
            Path("config.yaml"),           # Running from workspace root
            Path("../config.yaml"),        # Running from scripts/ directory
            Path(__file__).parent / "config.yaml",         # Same directory as this script
            Path(__file__).parent.parent / "config.yaml"  # Relative to script location
        ]

        config_path = None
        for path in possible_paths:
            if path.exists():
                config_path = path
                break

        if config_path is None:
            print("Error: Configuration file 'config.yaml' not found.")
            print("Searched locations:")
            for path in possible_paths:
                print(f"  - {path.absolute()}")
            sys.exit(1)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        print(f"Error: Configuration file '{config_path}' not found.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: Failed to parse configuration file: {e}")
        sys.exit(1)


def connect_database(db_config: Dict[str, Any], database_name: str = None) -> Optional[mysql.connector.connection.MySQLConnection]:
    """Connect to MySQL database."""
    try:
        connection = mysql.connector.connect(
            host=db_config.get('host', 'localhost'),
            user=db_config.get('user'),
            password=db_config.get('password'),
            database=database_name or db_config.get('database')
        )
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"Error connecting to database '{database_name}': {e}")
        return None


def create_table_if_not_exists(connection: mysql.connector.connection.MySQLConnection) -> bool:
    """Create the external_fa_sources table if it doesn't exist."""
    try:
        cursor = connection.cursor()

        create_table_query = """
            CREATE TABLE IF NOT EXISTS external_fa_sources (
                id INT AUTO_INCREMENT PRIMARY KEY,
                source_link TEXT NOT NULL,
                post_id VARCHAR(100),
                checked BOOLEAN DEFAULT FALSE,
                is_favorited BOOLEAN DEFAULT FALSE,
                last_checked DATETIME,
                check_attempts INT DEFAULT 0,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_post_id (post_id),
                INDEX idx_checked (checked),
                INDEX idx_is_favorited (is_favorited),
                INDEX idx_last_checked (last_checked)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """

        cursor.execute(create_table_query)
        connection.commit()
        cursor.close()

        return True

    except Error as e:
        print(f"Error creating table: {e}")
        return False


def extract_fa_post_id(source_link: str) -> Optional[str]:
    """Extract FurAffinity post ID from a source link."""
    # Match patterns like:
    # https://www.furaffinity.net/view/12345678/
    # https://furaffinity.net/view/12345678
    # http://www.furaffinity.net/view/12345678/

    match = re.search(r'furaffinity\.net/view/(\d+)', source_link, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


def get_external_fa_sources(external_conn: mysql.connector.connection.MySQLConnection) -> List[Dict[str, Any]]:
    """Pull FA source links from e621_posts_db.source_links table."""
    try:
        cursor = external_conn.cursor(dictionary=True)

        query = """
            SELECT DISTINCT sl.source_link
            FROM source_links AS sl  -- fff: external_conn is already on the archive DB
            WHERE source_link LIKE '%furaffinity%'
            AND source_link NOT LIKE '%furaffinity.net/user/%'
            AND source_link NOT LIKE '%//d.%'
            AND source_link NOT LIKE '%/gallery/%';
        """

        cursor.execute(query)
        results = cursor.fetchall()
        cursor.close()

        return results

    except Error as e:
        print(f"Error querying external source_links: {e}")
        return []


def get_existing_post_ids(local_conn: mysql.connector.connection.MySQLConnection) -> set:
    """Get set of post IDs that already exist in local tracking table."""
    try:
        cursor = local_conn.cursor()
        query = "SELECT post_id FROM external_fa_sources WHERE post_id IS NOT NULL"
        cursor.execute(query)

        existing_ids = {row[0] for row in cursor.fetchall()}
        cursor.close()

        return existing_ids

    except Error as e:
        print(f"Error getting existing post IDs: {e}")
        return set()


def insert_new_source(local_conn: mysql.connector.connection.MySQLConnection,
                     source_link: str, post_id: Optional[str]) -> bool:
    """Insert a new source link into the tracking table."""
    try:
        cursor = local_conn.cursor()
        query = """
            INSERT INTO external_fa_sources (source_link, post_id, checked, is_favorited)
            VALUES (%s, %s, FALSE, FALSE)
        """
        cursor.execute(query, (source_link, post_id))
        local_conn.commit()
        cursor.close()

        return True

    except Error as e:
        print(f"  Error inserting source: {e}")
        return False


def get_unchecked_sources(local_conn: mysql.connector.connection.MySQLConnection,
                         limit: int = None) -> List[Dict[str, Any]]:
    """Get unchecked sources from tracking table."""
    try:
        cursor = local_conn.cursor(dictionary=True)

        query = """
            SELECT id, source_link, post_id, check_attempts
            FROM external_fa_sources
            WHERE checked = FALSE AND post_id IS NOT NULL
              AND check_attempts < %s
            ORDER BY created_at ASC
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query, (MAX_CHECK_ATTEMPTS,))
        results = cursor.fetchall()
        cursor.close()

        return results

    except Error as e:
        print(f"Error getting unchecked sources: {e}")
        return []


def update_source_check_status(local_conn: mysql.connector.connection.MySQLConnection,
                               source_id: int, is_favorited: bool,
                               error_message: Optional[str] = None) -> bool:
    """Update the check status for a source."""
    try:
        cursor = local_conn.cursor()

        query = """
            UPDATE external_fa_sources
            SET checked = TRUE,
                is_favorited = %s,
                last_checked = NOW(),
                check_attempts = check_attempts + 1,
                error_message = %s
            WHERE id = %s
        """

        cursor.execute(query, (is_favorited, error_message, source_id))
        local_conn.commit()
        cursor.close()

        return True

    except Error as e:
        print(f"  Error updating source check status: {e}")
        return False


def update_check_attempt(local_conn: mysql.connector.connection.MySQLConnection,
                        source_id: int, error_message: str) -> bool:
    """Increment check attempt counter without marking as checked."""
    try:
        cursor = local_conn.cursor()

        query = """
            UPDATE external_fa_sources
            SET check_attempts = check_attempts + 1,
                last_checked = NOW(),
                error_message = %s
            WHERE id = %s
        """

        cursor.execute(query, (error_message, source_id))
        local_conn.commit()
        cursor.close()

        return True

    except Error as e:
        print(f"  Error updating check attempt: {e}")
        return False


def get_statistics(local_conn: mysql.connector.connection.MySQLConnection) -> Dict[str, int]:
    """Get statistics about tracked sources."""
    try:
        cursor = local_conn.cursor(dictionary=True)

        query = """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN checked = TRUE THEN 1 ELSE 0 END) as checked_count,
                SUM(CASE WHEN is_favorited = TRUE THEN 1 ELSE 0 END) as favorited_count,
                SUM(CASE WHEN checked = FALSE THEN 1 ELSE 0 END) as unchecked_count,
                SUM(CASE WHEN post_id IS NULL THEN 1 ELSE 0 END) as invalid_links
            FROM external_fa_sources
        """

        cursor.execute(query)
        result = cursor.fetchone()
        cursor.close()

        return result or {}

    except Error as e:
        print(f"Error getting statistics: {e}")
        return {}


@logged_command("fluffle", "fa_sync_external", "fff.fluffle.sync_external_fa_sources")
def main():
    """Main function."""
    print("External FurAffinity Source Links Monitor")
    print("=" * 80)
    print("Pulls FA links from e621_posts_db and auto-favorites them\n")

    # Load configuration
    config = load_config()
    db_config = config.get('database', {})

    # Connect to local database (fluffle_search)
    print("Connecting to local database (fluffle_search)...")
    local_conn = connect_database(db_config)
    if not local_conn:
        print("Failed to connect to local database. Exiting.")
        return

    print("  [OK] Connected to local database")

    # Create table if it doesn't exist
    print("Ensuring external_fa_sources table exists...")
    if not create_table_if_not_exists(local_conn):
        print("Failed to create table. Exiting.")
        local_conn.close()
        return

    print("  [OK] Table ready")

    # Connect to external database (e621_posts_db)
    print("Connecting to external database (e621_posts_db)...")
    external_conn = connect_database(db_config, fff_config.database_names(fff_config.load())['archive'])
    if not external_conn:
        print("Failed to connect to external database. Exiting.")
        local_conn.close()
        return

    print("  [OK] Connected to external database\n")

    # Step 1: Pull new source links
    print("=" * 80)
    print("STEP 1: Pulling source links from e621_posts_db")
    print("=" * 80)

    print("Querying external source_links table...")
    external_sources = get_external_fa_sources(external_conn)
    print(f"Found {len(external_sources)} FA source links in external database\n")

    # Get existing post IDs to avoid duplicates
    print("Checking for existing entries in local database...")
    existing_post_ids = get_existing_post_ids(local_conn)
    print(f"Already tracking {len(existing_post_ids)} FA posts\n")

    # Process and insert new sources
    print("Processing and inserting new sources...")
    new_count = 0
    skipped_count = 0
    invalid_count = 0

    for source in external_sources:
        source_link = source.get('source_link', '')

        # Extract FA post ID
        post_id = extract_fa_post_id(source_link)

        if not post_id:
            invalid_count += 1
            continue

        # Skip if already tracked
        if post_id in existing_post_ids:
            skipped_count += 1
            continue

        # Insert new source
        if insert_new_source(local_conn, source_link, post_id):
            new_count += 1
            existing_post_ids.add(post_id)  # Add to set to avoid duplicates in this run

            if new_count % 100 == 0:
                print(f"  Inserted {new_count} new sources...")

    print(f"\n  New sources added: {new_count}")
    print(f"  Already tracked (skipped): {skipped_count}")
    print(f"  Invalid links (no post ID): {invalid_count}\n")

    # Step 2: Check unchecked sources for favorite status and favorite them
    print("=" * 80)
    print("STEP 2: Checking and favoriting unchecked sources")
    print("=" * 80)

    # Get Firefox cookies
    print("Loading Firefox cookies...")
    cookies = get_firefox_cookies()
    if not cookies:
        print("Error: Could not load Firefox cookies.")
        print("Please make sure you're logged into FurAffinity in Firefox.")
        print("\nSkipping favorite status checks.")
        external_conn.close()
        local_conn.close()
        return

    print("  [OK] Firefox cookies loaded\n")

    # Get unchecked sources
    print("Getting unchecked sources...")
    unchecked = get_unchecked_sources(local_conn)
    print(f"Found {len(unchecked)} unchecked sources\n")

    if unchecked:
        print(f"Checking and favoriting posts (with {2.0}s delay between requests)...")

        checked_count = 0
        already_favorited_count = 0
        newly_favorited_count = 0
        failed_to_favorite_count = 0
        not_found_count = 0
        error_count = 0

        for i, source in enumerate(unchecked, 1):
            source_id = source['id']
            post_id = source['post_id']

            print(f"[{i}/{len(unchecked)}] FA post {post_id}...", end=" ")

            try:
                # Check if favorited
                is_fav = check_if_fa_favorited(post_id, cookies)

                if is_fav is None:
                    # Couldn't read the page (FA down or blocking us) - leave unchecked to retry next run
                    print("[ERROR] Could not check favorite status; will retry")
                    update_check_attempt(local_conn, source_id, "favorite status check failed")
                    error_count += 1
                elif is_fav:
                    print("[OK] Already favorited")
                    already_favorited_count += 1
                    # Update database - mark as favorited
                    update_source_check_status(local_conn, source_id, True)
                    checked_count += 1
                else:
                    print("[ ] Not favorited, adding...", end=" ")
                    time.sleep(1.0)  # Small delay before favoriting

                    # Try to favorite
                    if add_to_fa_favorites(post_id, cookies):
                        print("[OK] Successfully favorited")
                        newly_favorited_count += 1
                        # Update database - mark as favorited
                        update_source_check_status(local_conn, source_id, True)
                        checked_count += 1
                    else:
                        print("[ERROR] Failed to favorite; will retry")
                        failed_to_favorite_count += 1
                        # Leave unchecked so the next run retries it
                        update_check_attempt(local_conn, source_id, "favorite request failed")

            except FASubmissionNotFoundError as e:
                print(f"[INFO] {str(e)}")
                not_found_count += 1
                # Mark as checked with specific error message
                update_source_check_status(local_conn, source_id, False, str(e))
                checked_count += 1

            except Exception as e:
                print(f"[ERROR] {str(e)}")
                update_check_attempt(local_conn, source_id, str(e))
                error_count += 1

            # Rate limiting
            if i < len(unchecked):
                time.sleep(2.0)  # FA is strict about rate limiting

        print(f"\n  Checked: {checked_count}")
        print(f"  Already favorited: {already_favorited_count}")
        print(f"  Newly favorited: {newly_favorited_count}")
        print(f"  Failed to favorite: {failed_to_favorite_count}")
        print(f"  Not found in database: {not_found_count}")
        print(f"  Errors: {error_count}\n")

    # Close external connection
    external_conn.close()

    # Step 3: Show statistics
    print("=" * 80)
    print("STATISTICS")
    print("=" * 80)

    stats = get_statistics(local_conn)

    print(f"Total tracked sources: {stats.get('total', 0)}")
    print(f"Checked sources: {stats.get('checked_count', 0)}")
    print(f"Favorited sources: {stats.get('favorited_count', 0)}")
    print(f"Unchecked sources: {stats.get('unchecked_count', 0)}")
    print(f"Invalid links: {stats.get('invalid_links', 0)}")
    print()

    # Close local connection
    local_conn.close()

    print("Done!")


if __name__ == "__main__":
    main()
