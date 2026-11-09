#!/usr/bin/env python3
# Run with ``fff fluffle sync-fa-favorites``.
"""
Sync FurAffinity Favorites
Finds images that have both e621 and FA matches, and ensures the FA posts are favorited.
Does not download files since e621 versions are already downloaded.
"""

import sys
import time
from pathlib import Path
from typing import Dict, Any, List
import yaml
import mysql.connector
from mysql.connector import Error
import http.cookiejar

# Use the shared FurAffinity client and unified project configuration.
from fff.clients.furaffinity import (
    get_firefox_cookies,
    check_if_fa_favorited,
    add_to_fa_favorites
)
from fff.common import config as fff_config
from fff.common import db


def load_config(config_path: str = None) -> Dict[str, Any]:
    """Load configuration from YAML file."""
    if config_path is None:  # fff: the reverse search config view
        return fff_config.legacy_view("fluffle")
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



def get_images_with_both_platforms(connection: mysql.connector.connection.MySQLConnection) -> List[Dict[str, Any]]:
    """Get all images that have both e621 and FurAffinity matches."""
    try:
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT DISTINCT
                i.id as image_id,
                i.filename,
                f.post_id as fa_post_id,
                f.post_url as fa_post_url,
                f.author_name as fa_author
            FROM images i
            INNER JOIN e621_links e ON i.id = e.image_id
            INNER JOIN furaffinity_links f ON i.id = f.image_id
            WHERE e.match_quality = 'exact'
              AND f.match_quality = 'exact'
            ORDER BY i.id
        """

        cursor.execute(query)
        results = cursor.fetchall()
        cursor.close()

        return results

    except Error as e:
        print(f"Error querying database: {e}")
        return []


def main(argv=None):  # fff: argv accepted for the CLI; no options
    """Main function."""
    print("FurAffinity Favorites Sync Tool")
    print("=" * 80)
    print("Syncs FA favorites for images that have both e621 and FA matches")
    print("(e621 versions are already downloaded, so FA posts are only favorited)\n")

    # Load configuration
    config = load_config()
    db_config = config.get('database', {})

    # Get Firefox cookies
    print("Loading Firefox cookies...")
    cookies = get_firefox_cookies()
    if not cookies:
        print("Error: Could not load Firefox cookies.")
        print("Please make sure you're logged into FurAffinity in Firefox.")
        return

    print()

    # Connect to database
    print("Connecting to database...")
    connection = db.try_connect_with(db_config)
    if not connection:
        print("Failed to connect to database. Exiting.")
        return

    # Get images with both platforms
    print("Querying images with both e621 and FA matches...")
    matches = get_images_with_both_platforms(connection)

    if not matches:
        print("No images found with both e621 and FA matches.")
        connection.close()
        return

    print(f"Found {len(matches)} image(s) with both e621 and FA matches\n")

    request_delay = 2.0  # FA is strict about rate limiting

    # Process each FA match
    favorited = 0
    already_favorited = 0
    failed = 0

    for i, match in enumerate(matches, 1):
        fa_post_id = match['fa_post_id']
        filename = match['filename']
        fa_author = match['fa_author']

        # Extract numeric FA ID
        numeric_id = fa_post_id.replace('furAffinity_', '').replace('fa_', '')

        print(f"[{i}/{len(matches)}] {filename}")
        print(f"  FA Post ID: {numeric_id}")
        print(f"  Author: {fa_author}")

        # Check if favorited
        print(f"  Checking favorites...", end=" ")
        is_fav = check_if_fa_favorited(numeric_id, cookies)

        if is_fav:
            print("[OK] Already favorited")
            already_favorited += 1
        else:
            print("[ERROR] Not favorited")
            print(f"  Adding to favorites...", end=" ")
            if add_to_fa_favorites(numeric_id, cookies):
                print("[OK] Successfully added to favorites")
                favorited += 1
                time.sleep(request_delay)
            else:
                print("[ERROR] Failed to add to favorites")
                failed += 1

        print()

        # Rate limiting between checks
        if i < len(matches):
            time.sleep(request_delay)

    # Close database connection
    connection.close()

    # Summary
    print("=" * 80)
    print("Summary:")
    print(f"  Images processed: {len(matches)}")
    print(f"  Already favorited: {already_favorited}")
    print(f"  Newly favorited: {favorited}")
    if failed > 0:
        print(f"  Failed: {failed}")
    print()


if __name__ == "__main__":
    main()
