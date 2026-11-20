#!/usr/bin/env python3
"""``fff e621 verify file-paths``: check every recorded posts.file_path and repair broken ones.

Runs the same repair as ``fff e621 sync`` (fff.archive.db.verify_and_repair_file_paths): a missing
file is looked up in the image_sorting_hashes database by post id, then by MD5. It used to be a
separate copy of that code without its safeguards; the shared one changes nothing when the
library looks absent (a drive offline, a folder renamed) instead of clearing every path.

Usage:
    fff e621 verify file-paths [--batch-limit N] [--dry-run] [--keep-missing]
"""

import argparse
import logging
import sys

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, verify_and_repair_file_paths
from fff.archive.logging_setup import setup_logging


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify and repair broken file paths using the image_sorting_hashes database")
    parser.add_argument("--batch-limit", type=int, default=None, help="Check only N posts (default: all posts)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be updated without making changes")
    parser.add_argument("--keep-missing", action="store_true",
                        help="Leave the path of a file that cannot be found (default: clear it, so the next "
                             "sync downloads the file again)")
    args = parser.parse_args(argv)

    config = load_config()
    setup_logging(config['log']['directory'])
    logging.info("Starting file path verification and repair" + (" (DRY RUN)" if args.dry_run else ""))

    conn = connect_to_mysql(config['mysql'])
    if not conn:
        logging.error("Failed to connect to database")
        sys.exit(1)
    try:
        stats = verify_and_repair_file_paths(conn, config['mysql'], batch_limit=args.batch_limit,
                                             clear_missing=not args.keep_missing, dry_run=args.dry_run)
    finally:
        conn.close()

    if args.dry_run:
        logging.info("No changes were made (DRY RUN mode)")
    if stats.get("aborted"):
        sys.exit(1)


if __name__ == "__main__":
    main()
