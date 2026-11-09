#!/usr/bin/env python3
# fff: run as 'fff e621 files orphaned'.
"""Move library files of posts that left the database without their files.

Until 2026-11, removing a post (unfavorite during sync, Blacklist review deletes, the AI
cleanups, 'files unfavorited') acted on posts.file_path only, which is often stale after the
sorter moves a file. The post row was deleted and the real file stayed in the library, linked
in the sorter database to a post id that is no longer in e621_posts_db.posts.

This finds those files and asks e621 whether each post is still a favorite:
  - not a favorite: the files move to archive.e621.unfavorited_directory;
  - still a favorite: left alone ('fff e621 favorites add-missing' brings the post back);
  - lookup failed: left alone.
Dry run unless --execute is given.
"""
import argparse
import logging
from collections import defaultdict

from fff.archive import paths as archive_paths
from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql
from fff.archive.logging_setup import setup_logging
from fff.archive.post_files import move_post_files
from fff.archive.scripts.favorites.favorite_missing_posts import check_if_favorited


def find_orphaned_files(conn):
    """``{post_id: [file_path, ...]}`` for sorter files linked to posts not in ``posts``."""
    names = archive_paths.db_names()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"SELECT a.e621_post_id, a.file_path FROM `{names['sorter']}`.all_files a "
            f"LEFT JOIN `{names['archive']}`.posts p ON p.id = a.e621_post_id "
            "WHERE a.e621_post_id > 0 AND p.id IS NULL ORDER BY a.e621_post_id")
        orphans = defaultdict(list)
        for post_id, file_path in cursor.fetchall():
            orphans[int(post_id)].append(file_path)
        return dict(orphans)
    finally:
        cursor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--execute", action="store_true",
                        help="Move the files (default: dry run, only report what would move)")
    parser.add_argument("--limit", type=int, help="Check at most N posts")
    args = parser.parse_args(argv)

    config = load_config()
    setup_logging(str(archive_paths.LOG_DIR))
    conn = connect_to_mysql(config.get("mysql", {}))
    if not conn:
        logging.error("Failed to connect to MySQL database")
        return 1

    try:
        orphans = find_orphaned_files(conn)
        post_ids = list(orphans)[:args.limit] if args.limit else list(orphans)
        logging.info("Found %s files linked to %s posts that are no longer in the database",
                     sum(len(orphans[p]) for p in post_ids), len(post_ids))

        e621_config = config.get("e621", {})
        unfavorited, favorited, unknown = [], [], []
        for n, post_id in enumerate(post_ids, 1):
            status = check_if_favorited(post_id, e621_config)  # rate-limited to 1/s
            (favorited if status else unknown if status is None else unfavorited).append(post_id)
            if n % 25 == 0:
                logging.info("Checked %s/%s posts on e621...", n, len(post_ids))

        for post_id in favorited:
            logging.info("Post %s is still a favorite; leaving %s", post_id, orphans[post_id])
        for post_id in unknown:
            logging.warning("Could not check post %s on e621; leaving %s", post_id, orphans[post_id])

        destination = archive_paths.unfavorited_directory()
        result = move_post_files(conn, unfavorited, destination, reason="unfavorited:orphaned",
                                 dry_run=not args.execute)
        verb = "Moved" if args.execute else "[DRY RUN] Would move"
        logging.info("%s %s files of %s unfavorited posts to %s", verb, result["moved"], len(unfavorited),
                     destination)
        if result["failed"]:
            logging.warning("Failed to move %s files", result["failed"])
        logging.info("Left alone: %s posts still favorited (run 'fff e621 favorites add-missing'), "
                     "%s that could not be checked", len(favorited), len(unknown))
        if not args.execute and unfavorited:
            logging.info("Run again with --execute to move them.")
        return 1 if result["failed"] else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
