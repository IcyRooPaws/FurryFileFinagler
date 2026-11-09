#!/usr/bin/env python3
"""
Script to backfill deletion reasons and takedown details for deleted posts.
This script fetches deletion information from e621 web pages and updates the database
with both the deletion reason (in posts table) and structured takedown data (in takedowns table).

Additionally, this script extracts the actual deletion timestamp from the moderator signature
on the page (format: "- ModeratorName on YYYY-MM-DD HH:MM:SS") and updates posts.deleted_at
with this more accurate timestamp, replacing the API's generic updated_at value.
"""

import sys
import logging
import argparse
from datetime import datetime

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import (
    connect_to_mysql,
    fetch_deletion_reason_from_page,
    fetch_takedown_details_from_page,
    insert_or_update_takedown,
    link_post_to_takedown
)

def setup_logging():
    """Setup logging."""
    log_dir = str(archive_paths.LOG_DIR)
    import os
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"{log_dir}/{timestamp}_backfill_deletion_reasons.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    return log_file

def get_deleted_posts_without_reason(conn, limit=None):
    """Get deleted posts that don't have a deletion reason yet."""
    cursor = conn.cursor()
    try:
        query = """
            SELECT id, deleted_at
            FROM posts
            WHERE deleted = 1
            AND (deletion_reason IS NULL OR deletion_reason = '')
            ORDER BY deleted_at DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        results = cursor.fetchall()
        return [(row[0], row[1]) for row in results]
    finally:
        cursor.close()

def get_all_deleted_posts(conn, limit=None):
    """Get all deleted posts (including those with existing deletion reasons)."""
    cursor = conn.cursor()
    try:
        query = """
            SELECT id, deleted_at
            FROM posts
            WHERE deleted = 1
            ORDER BY deleted_at DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        results = cursor.fetchall()
        return [(row[0], row[1]) for row in results]
    finally:
        cursor.close()

def backfill_deletion_reasons(config, max_posts=None, force=False):
    """Backfill deletion reasons and takedown details for deleted posts.

    Args:
        config: Configuration dictionary
        max_posts: Maximum number of posts to process (None for all)
        force: If True, reprocess all deleted posts including those with existing deletion reasons
    """
    import time

    mysql_config = config.get('mysql', {})
    e621_config = config.get('e621', {})

    conn = connect_to_mysql(mysql_config)
    if conn is None:
        logging.error("Failed to connect to database")
        sys.exit(1)

    try:
        # Get deleted posts based on force flag
        if force:
            logging.info("Fetching ALL deleted posts (force mode)...")
            posts = get_all_deleted_posts(conn, limit=max_posts)
        else:
            logging.info("Fetching deleted posts without deletion reasons...")
            posts = get_deleted_posts_without_reason(conn, limit=max_posts)

        if not posts:
            if force:
                logging.info("No deleted posts found in database!")
            else:
                logging.info("No deleted posts need deletion reasons!")
            return 0

        logging.info(f"Found {len(posts)} deleted posts to process")

        user_agent = e621_config.get("user_agent", "e621 archive/1.0")
        login = e621_config.get("login")
        api_key = e621_config.get("api_key")
        auth = (login, api_key) if login and api_key else None

        updated_count = 0
        found_reasons = 0
        found_takedowns = 0
        linked_count = 0
        updated_timestamps = 0

        for i, (post_id, deleted_at) in enumerate(posts, 1):
            logging.info(f"Processing post {post_id} ({i}/{len(posts)})")

            try:
                # Fetch deletion reason from web page (for backwards compatibility)
                deletion_reason = fetch_deletion_reason_from_page(post_id, user_agent, auth)

                if deletion_reason:
                    found_reasons += 1
                    logging.info(f"  Found reason: {deletion_reason[:100]}...")
                else:
                    logging.info("  No deletion reason found")

                # Fetch structured takedown details
                takedown_details = fetch_takedown_details_from_page(post_id, user_agent, auth)

                # Track if we found a more accurate deletion timestamp
                deletion_timestamp = None

                if takedown_details:
                    takedown_id = takedown_details.get('takedown_id')

                    # Extract finalized_at timestamp if available
                    if takedown_details.get('finalized_at'):
                        deletion_timestamp = takedown_details.get('finalized_at')
                        logging.info(f"  Found deletion timestamp: {deletion_timestamp}")

                    if takedown_id:
                        # Store takedown data
                        takedown_data = {
                            'takedown_id': takedown_id,
                            'reason': takedown_details.get('reason'),
                            'instructions': takedown_details.get('instructions'),
                            'admin_notes': takedown_details.get('admin_notes'),
                            'finalized_at': deletion_timestamp,
                            'raw_html_snippet': takedown_details.get('raw_html'),
                            'status': 'finalized'  # Assume finalized if we found it on a deleted post
                        }

                        if insert_or_update_takedown(conn, takedown_data):
                            found_takedowns += 1

                            # Log admin notes if found
                            if takedown_details.get('admin_notes'):
                                logging.info(f"  Stored takedown #{takedown_id} details (with admin notes)")
                            else:
                                logging.info(f"  Stored takedown #{takedown_id} details")

                            # Link post to takedown
                            if link_post_to_takedown(conn, post_id, takedown_id):
                                linked_count += 1
                                logging.info(f"  Linked post {post_id} to takedown #{takedown_id}")
                    else:
                        logging.info(f"  Found deletion info but no takedown ID (type: {takedown_details.get('deletion_type')})")
                        # Still use the reason from structured data if available
                        if takedown_details.get('reason') and not deletion_reason:
                            deletion_reason = takedown_details.get('reason')

                # Update posts table with deletion reason and timestamp
                cursor = conn.cursor()
                if deletion_timestamp:
                    # Update both deletion_reason and deleted_at with the accurate timestamp from the page
                    cursor.execute("""
                        UPDATE posts
                        SET deletion_reason = %s,
                            deleted_at = %s
                        WHERE id = %s
                    """, (deletion_reason, deletion_timestamp, post_id))
                    updated_timestamps += 1
                    logging.info(f"  Updated post deletion timestamp to {deletion_timestamp}")
                else:
                    # Just update deletion reason if no timestamp found
                    cursor.execute("""
                        UPDATE posts
                        SET deletion_reason = %s
                        WHERE id = %s
                    """, (deletion_reason, post_id))
                conn.commit()
                cursor.close()

                updated_count += 1

                # Rate limiting - be nice to e621 servers
                time.sleep(1.0)

                # Progress update every 10 posts
                if updated_count % 10 == 0:
                    logging.info(f"Progress: {updated_count}/{len(posts)} posts checked, {found_reasons} reasons found, {found_takedowns} takedowns stored, {updated_timestamps} timestamps updated")

            except Exception as e:
                logging.error(f"Error processing post {post_id}: {e}")
                continue

        logging.info("=" * 80)
        logging.info("Backfill complete!")
        logging.info(f"  Posts checked: {updated_count}")
        logging.info(f"  Deletion reasons found: {found_reasons}")
        logging.info(f"  Takedowns stored: {found_takedowns}")
        logging.info(f"  Post-takedown links created: {linked_count}")
        logging.info(f"  Deletion timestamps updated: {updated_timestamps}")
        logging.info(f"  No reason found: {updated_count - found_reasons}")
        logging.info("=" * 80)

        return updated_count

    finally:
        conn.close()

def main(argv=None):
    """Main entry point."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Backfill deletion reasons and takedown details for deleted posts"
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Reprocess ALL deleted posts, including those with existing deletion reasons'
    )
    args = parser.parse_args(argv)

    log_file = setup_logging()
    logging.info("Starting deletion reason backfill")
    logging.info(f"Log file: {log_file}")

    if args.force:
        logging.info("Force mode enabled: will reprocess all deleted posts")

    try:
        # Load configuration
        config = load_config()

        # Ask user for confirmation and options
        print("=" * 80)
        print("DELETION REASON & TAKEDOWN BACKFILL")
        print("=" * 80)
        print()
        print("This script will fetch deletion reasons and takedown details from")
        print("e621 web pages and update your database. This may take a while for many posts.")
        print()
        print("New features:")
        print("  - Stores deletion reasons in posts table (as before)")
        print("  - Stores structured takedown data in takedowns table (NEW)")
        print("  - Links posts to takedown notices (NEW)")
        print("  - Extracts and updates deletion timestamps from page (NEW)")
        print("    (Updates posts.deleted_at with actual deletion date/time)")
        print()

        if args.force:
            print("FORCE MODE: Will reprocess all deleted posts including those with existing reasons")
            print()

        # Get deleted posts count
        mysql_config = config.get('mysql', {})
        conn = connect_to_mysql(mysql_config)

        if args.force:
            posts = get_all_deleted_posts(conn, limit=None)
            posts_without = get_deleted_posts_without_reason(conn, limit=None)
            print(f"Found {len(posts)} total deleted posts")
            print(f"  ({len(posts_without)} without reasons, {len(posts) - len(posts_without)} with existing reasons)")
        else:
            posts = get_deleted_posts_without_reason(conn, limit=None)
            print(f"Found {len(posts)} deleted posts without deletion reasons")

        conn.close()
        print()

        if len(posts) == 0:
            print("Nothing to do!")
            return

        # Ask for limit
        response = input(f"Process all {len(posts)} posts? (yes/no, or enter a number to limit): ").strip().lower()

        if response in ['no', 'n']:
            print("Cancelled.")
            return

        max_posts = None
        if response not in ['yes', 'y']:
            try:
                max_posts = int(response)
                print(f"Will process up to {max_posts} posts")
            except ValueError:
                print("Invalid input. Cancelled.")
                return

        print()
        print("Starting backfill... (Rate limit: 1 request per second)")
        print()

        # Run the backfill
        updated = backfill_deletion_reasons(config, max_posts=max_posts, force=args.force)

        print()
        print(f"Backfill complete! Updated {updated} posts.")
        print(f"Log file: {log_file}")

    except KeyboardInterrupt:
        print()
        logging.info("Interrupted by user")
        print("Cancelled by user.")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Script failed: {e}", exc_info=True)
        print(f"Script failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
