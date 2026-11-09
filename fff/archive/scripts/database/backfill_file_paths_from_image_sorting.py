#!/usr/bin/env python3
"""
Standalone script to backfill missing file_path values from image_sorting_hashes database.

This script finds posts in the e621_posts_db that have NULL file_path but have an MD5 hash,
then searches the image_sorting_hashes database for matching files and updates the file_path
if found. This includes both active and deleted posts - deleted posts can still have file_path
values to track where the local file is stored.

Usage:
    python backfill_file_paths_from_image_sorting.py [--batch-limit N] [--dry-run]

Options:
    --batch-limit N    Process only N posts (default: all posts)
    --dry-run         Show what would be updated without making changes
"""

import sys
import logging
import argparse


from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql, get_file_path_from_image_sorting_hashes


def backfill_file_paths_standalone(conn, mysql_config, batch_limit=None, dry_run=False):
    """
    Backfill missing file_path values by checking the image_sorting_hashes database.

    Processes both active and deleted posts - even deleted posts should have file_path
    values if the local file exists, to maintain accurate database state.

    Args:
        conn: Database connection to e621_posts_db
        mysql_config: MySQL configuration dict
        batch_limit: Optional limit on number of posts to process
        dry_run: If True, don't actually update database

    Returns:
        Tuple of (updated_count, not_found_count)
    """
    cursor = conn.cursor()
    try:
        # Get posts with NULL file_path but have MD5 hash (includes deleted posts)
        query = """
        SELECT id, md5, file_name, deleted
        FROM posts
        WHERE file_path IS NULL
        AND md5 IS NOT NULL
        AND md5 != ''
        ORDER BY deleted ASC, id
        """

        if batch_limit:
            query += f" LIMIT {batch_limit}"

        cursor.execute(query)
        posts_without_path = cursor.fetchall()

        if not posts_without_path:
            logging.info("No posts with missing file_path found")
            return 0, 0

        # Count deleted vs active posts
        deleted_posts_count = sum(1 for p in posts_without_path if p[3])
        active_posts_count = len(posts_without_path) - deleted_posts_count

        logging.info(f"Found {len(posts_without_path)} posts with missing file_path")
        logging.info(f"  - {active_posts_count} active posts")
        logging.info(f"  - {deleted_posts_count} deleted posts")
        if dry_run:
            logging.info("DRY RUN MODE - No database changes will be made")

        updated_count = 0
        not_found_count = 0
        deleted_updated = 0

        update_cursor = conn.cursor()

        for idx, (post_id, md5_hash, file_name, is_deleted) in enumerate(posts_without_path, 1):
            try:
                # Check if file path exists in image_sorting_hashes database
                file_path = get_file_path_from_image_sorting_hashes(mysql_config, md5_hash)

                if file_path:
                    if dry_run:
                        deleted_marker = " [DELETED]" if is_deleted else ""
                        logging.info(f"[DRY RUN] Would update post {post_id}{deleted_marker} with file_path: {file_path}")
                        updated_count += 1
                        if is_deleted:
                            deleted_updated += 1
                    else:
                        # Update the post with the found file_path
                        update_cursor.execute(
                            "UPDATE posts SET file_path = %s WHERE id = %s",
                            (file_path, post_id)
                        )
                        updated_count += 1
                        if is_deleted:
                            deleted_updated += 1

                        deleted_marker = " [DELETED]" if is_deleted else ""
                        logging.info(f"Updated post {post_id}{deleted_marker} (MD5: {md5_hash[:8]}...): {file_path}")
                else:
                    not_found_count += 1
                    deleted_marker = " [DELETED]" if is_deleted else ""
                    logging.debug(f"No file path found for post {post_id}{deleted_marker} (MD5: {md5_hash[:8]}...)")

                # Progress indicator every 50 posts
                if idx % 50 == 0:
                    logging.info(f"Progress: {idx}/{len(posts_without_path)} posts checked ({updated_count} updated, {not_found_count} not found)")

                # Commit every 100 updates for safety (only if not dry run)
                if not dry_run and updated_count % 100 == 0 and updated_count > 0:
                    conn.commit()
                    logging.info(f"Committed {updated_count} updates so far...")

            except Exception as e:
                logging.error(f"Error processing post {post_id}: {e}")
                continue

        # Final commit
        if not dry_run:
            conn.commit()

        update_cursor.close()

        # Log deleted post statistics if any were updated
        if deleted_updated > 0:
            logging.info(f"Updated {deleted_updated} deleted posts with file paths")

        return updated_count, not_found_count

    except Exception as e:
        logging.error(f"Error in backfill process: {e}")
        return 0, 0
    finally:
        cursor.close()


def main(argv=None):
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Backfill missing file_path values from image_sorting_hashes database (includes deleted posts)"
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        default=None,
        help="Process only N posts (default: all posts)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes"
    )

    args = parser.parse_args(argv)

    # Load config and setup logging
    config = load_config()
    setup_logging(config['log']['directory'])

    logging.info("Starting file path backfill from image_sorting_hashes database")
    logging.info("This will process both active and deleted posts")
    if args.dry_run:
        logging.info("Running in DRY RUN mode")
    if args.batch_limit:
        logging.info(f"Processing limit: {args.batch_limit} posts")

    try:
        # Connect to database
        conn = connect_to_mysql(config['mysql'])
        if not conn:
            logging.error("Failed to connect to database")
            sys.exit(1)

        logging.info("Connected to e621_posts_db database")

        # Run the backfill process
        updated_count, not_found_count = backfill_file_paths_standalone(
            conn,
            config['mysql'],
            batch_limit=args.batch_limit,
            dry_run=args.dry_run
        )

        # Log results
        total_checked = updated_count + not_found_count
        logging.info("=" * 60)
        logging.info("BACKFILL COMPLETE")
        logging.info("=" * 60)
        logging.info(f"Total posts checked: {total_checked}")
        logging.info(f"File paths found and updated: {updated_count}")
        logging.info(f"File paths not found: {not_found_count}")

        if updated_count > 0:
            success_rate = (updated_count / total_checked * 100) if total_checked > 0 else 0
            logging.info(f"Success rate: {success_rate:.1f}%")

        if args.dry_run:
            logging.info("No changes were made (DRY RUN mode)")

        conn.close()
        logging.info("Database connection closed")

    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
