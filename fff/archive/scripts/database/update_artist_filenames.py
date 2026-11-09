# fff: logs go to logs/archive (was ./logs relative to the CWD).
"""
Update file names to match current artist names in the database.

This script checks all posts with files and updates their file names if the artist
names in the file don't match the current artist names in the database. This is
useful for handling artist name changes on e621.

All changes are logged to the artist_file_renames table for audit purposes.

Usage:
    python update_artist_filenames.py [--dry-run] [--batch-limit N]

Options:
    --dry-run       Preview changes without making them
    --batch-limit N Process only N posts (useful for testing)
"""

import argparse
import logging
import sys

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql, check_and_update_file_artist_names

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Update file names to match current artist names in database"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them"
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        help="Process only N posts (useful for testing)"
    )

    args = parser.parse_args(argv)

    # Setup
    setup_logging(str(archive_paths.LOG_DIR))  # fff: was ./logs (CWD)
    config = load_config()

    logging.info("="*80)
    logging.info("Artist Filename Update Script")
    logging.info("="*80)

    if args.dry_run:
        logging.info("DRY RUN MODE - No files will be renamed")
    if args.batch_limit:
        logging.info(f"Batch limit: {args.batch_limit} posts")

    # Connect to database
    conn = connect_to_mysql(config["mysql"])
    if not conn:
        logging.error("Failed to connect to database")
        sys.exit(1)

    try:
        # Run the update
        stats = check_and_update_file_artist_names(
            conn,
            batch_limit=args.batch_limit,
            dry_run=args.dry_run
        )

        # Print summary
        logging.info("="*80)
        logging.info("SUMMARY")
        logging.info("="*80)
        logging.info(f"Posts checked: {stats['checked']}")
        logging.info(f"Files renamed: {stats['renamed']}")
        logging.info(f"Files skipped (no change needed): {stats['skipped_no_change']}")
        logging.info(f"Files skipped (file not found): {stats['skipped_no_file']}")
        logging.info(f"file_name column repaired (no rename needed): {stats['file_name_column_fixed']}")
        logging.info(f"Failed: {stats['failed']}")

        if args.dry_run:
            logging.info("\nDRY RUN - No changes were made")
        else:
            logging.info(f"\nSuccessfully renamed {stats['renamed']} files")
            if stats['renamed'] > 0:
                logging.info("Check the artist_file_renames table for full audit log")

    except Exception as e:
        logging.error(f"Error during artist filename update: {e}", exc_info=True)
        sys.exit(1)
    finally:
        conn.close()
        logging.info("Database connection closed")

if __name__ == "__main__":
    main()
