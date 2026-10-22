"""AI post processing: run as part of 'fff e621 sync', or on its own as 'fff e621 ai process'.

1. Posts e621 deleted as AI: archive, flag artist, unfavorite, trash file, delete from DB
2. Posts with AI tags: flag artist, unfavorite, move file, downvote, delete from DB
3. Posts by flagged AI artists: unfavorite, move file, downvote, delete from DB

Each step logs its own failure and the next one still runs.
"""
import argparse
import logging
import sys

from fff.archive import paths as archive_paths
from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, create_tables_if_not_exists, load_ai_flagged_artists
from fff.archive.logging_setup import setup_logging
from fff.archive.scripts.database.cleanup_ai_generated_posts import cleanup_ai_generated_posts
from fff.archive.scripts.favorites.remove_ai_artist_posts import fetch_posts_by_artists, process_posts as process_ai_artist_posts
from fff.archive.scripts.favorites.remove_ai_tagged_posts import load_ai_tags, fetch_ai_tagged_posts, log_ai_artists, process_posts as process_ai_tagged_posts


def run_ai_processing(conn, e621_config):
    """Run the three AI steps. Returns True if every step ran without an error."""
    ok = True

    # Clean up posts e621 deleted as AI (archive, flag artist, unfav, trash file, delete from DB)
    try:
        logging.info("Running cleanup of posts deleted from e621 as AI...")
        ai_cleanup_stats = cleanup_ai_generated_posts(conn, e621_config, delete_files=True, dry_run=False)
        if ai_cleanup_stats["total_found"]:
            logging.info("AI-deleted cleanup complete - removed: %d, files trashed: %d, file failures: %d",
                         ai_cleanup_stats["posts_deleted"], ai_cleanup_stats["files_deleted"],
                         ai_cleanup_stats["files_failed"])
    except Exception as e:
        logging.error("AI-deleted post cleanup failed: %s", e)
        ok = False

    # Remove posts with AI tags (unfav, move file, downvote, flag artist, delete from DB)
    try:
        ai_tags_path = str(archive_paths.AI_TAGS)
        ai_tags = load_ai_tags(ai_tags_path)
        if ai_tags:
            logging.info("Running AI tag removal (%d tag(s))...", len(ai_tags))
            ai_tagged_posts = fetch_ai_tagged_posts(conn, ai_tags)
            if ai_tagged_posts:
                log_ai_artists(conn, ai_tagged_posts)
                removed, failed = process_ai_tagged_posts(conn, e621_config, ai_tagged_posts)
                logging.info("AI tag removal complete - removed: %d, failed: %d", removed, failed)
            else:
                logging.info("No posts found matching AI tags")
        else:
            logging.info("No AI tags configured, skipping AI tag removal")
    except Exception as e:
        logging.error("AI tag removal failed: %s", e)
        ok = False

    # Remove posts from known AI artists (unfav, move file, downvote, delete from DB)
    try:
        ai_artists = load_ai_flagged_artists(conn)
        if ai_artists:
            logging.info("Running AI artist removal (%d artist(s))...", len(ai_artists))
            ai_artist_posts = fetch_posts_by_artists(conn, ai_artists)
            if ai_artist_posts:
                removed, failed = process_ai_artist_posts(conn, e621_config, ai_artist_posts)
                logging.info("AI artist removal complete - removed: %d, failed: %d", removed, failed)
            else:
                logging.info("No posts found for known AI artists")
        else:
            logging.info("No AI artists listed, skipping AI artist removal")
    except Exception as e:
        logging.error("AI artist removal failed: %s", e)
        ok = False

    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="AI post processing: clean up posts e621 deleted as AI, then remove AI-tagged "
                    "posts and posts by flagged AI artists")
    parser.parse_args(argv)

    setup_logging(str(archive_paths.LOG_DIR))
    config = load_config()

    conn = connect_to_mysql(config.get("mysql", {}))
    if conn is None:
        logging.error("Failed to connect to database")
        return 1
    try:
        create_tables_if_not_exists(conn)
        ok = run_ai_processing(conn, config.get("e621", {}))
    finally:
        conn.close()
    logging.info("AI post processing %s", "complete" if ok else "finished with errors")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
