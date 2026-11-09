#!/usr/bin/env python3
"""
Remove Posts by AI Artists

Reads artist names from the ai_flagged_artists table and for each matching post:
  - Moves the local file to E:\\Image Sorter Working Folder\\AI\\AI_Artist_Trash
  - Unfavorites the post on e621
  - Downvotes the post on e621
  - Deletes the post from the local database

Usage:
    python scripts/favorites/remove_ai_artist_posts.py [--dry-run]

Options:
    --dry-run   Preview what would be removed without making any changes.
"""

import sys
import argparse
import logging
import time

from fff.clients import http

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, load_ai_flagged_artists
from fff.archive.fa_unfavorite import unfavorite_fa_sources
from fff.archive.post_files import move_post_files
from fff.archive.logging_setup import setup_logging

AI_ARTIST_DEST = archive_paths.ai_artist_directory()  # fff: config archive.ai_cleanup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_ai_artists(md_path):
    """Read artist names from data/ai_artist.md (lines starting with '- ')."""
    artists = []
    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                # Accept "- artist_name" or bare "artist_name"
                name = line.lstrip('- ').strip()
                if name:
                    artists.append(name)
    except FileNotFoundError:
        logging.error("AI artist file not found: %s", md_path)
        return []
    except Exception as e:
        logging.error("Error reading AI artist file: %s", e)
        return []

    # Deduplicate, preserve order
    seen = set()
    unique = []
    for a in artists:
        if a not in seen:
            seen.add(a)
            unique.append(a)
    return unique


def fetch_posts_by_artists(conn, artists):
    """Fetch non-deleted posts linked to any of the given artist names."""
    cursor = conn.cursor()
    placeholders = ', '.join(['%s'] * len(artists))
    query = f"""
        SELECT DISTINCT p.id, p.file_name, p.file_path, p.file_url, p.rating,
               GROUP_CONCAT(DISTINCT a.name ORDER BY a.name SEPARATOR ', ') AS artist_names
        FROM posts p
        JOIN posts_artists pa ON pa.post_id  = p.id
        JOIN artists       a  ON a.artist_id = pa.artist_id
        WHERE a.name IN ({placeholders})
          AND p.deleted = 0
        GROUP BY p.id, p.file_name, p.file_path, p.file_url, p.rating
        ORDER BY p.id
    """
    cursor.execute(query, artists)
    columns = [desc[0] for desc in cursor.description]
    posts = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    return posts


def process_posts(conn, e621_config, posts, dry_run=False):
    """Move file, unfavorite, downvote, and delete from DB for each post."""
    removed = 0
    failed = 0
    total = len(posts)

    # Move every copy of their files (not just posts.file_path); a post whose file could not be
    # moved is kept for the next run
    if not dry_run:
        moved = move_post_files(conn, [post['id'] for post in posts], AI_ARTIST_DEST, reason="ai_artists")
        failed += len(posts) - len([p for p in posts if p['id'] in moved["cleared"]])
        posts = [p for p in posts if p['id'] in moved["cleared"]]
        total = len(posts)
        # Unfavorite their FA sources while source_links still exist
        unfavorite_fa_sources(conn, [post['id'] for post in posts], e621_config)

    for idx, post in enumerate(posts, 1):
        post_id   = post['id']
        file_path = post.get('file_path')
        artists   = post.get('artist_names', '')

        logging.info("[%d/%d] Processing post %s | artists: %s", idx, total, post_id, artists)

        if dry_run:
            logging.info(
                "[DRY RUN] Would move %s -> %s, unfav+downvote post %s, delete from DB",
                file_path or '(no file)', AI_ARTIST_DEST, post_id
            )
            removed += 1
            continue
        try:
            # 1. Files were moved to AI_Artist_Trash above.
            auth = (e621_config.get("login"), e621_config.get("api_key"))
            headers = {"User-Agent": e621_config.get("user_agent", "e621 archive/1.0")}

            # 2. Unfavorite on e621
            try:
                response = http.delete(
                    "e621",
                    f"https://e621.net/favorites/{post_id}.json",
                    auth=auth, headers=headers, timeout=15
                )
                if response.status_code in [200, 204, 404, 422]:
                    logging.info("Unfavorited post %s on e621", post_id)
                else:
                    logging.warning("Unexpected status unfavoriting post %s: HTTP %s",
                                    post_id, response.status_code)
            except Exception as e:
                logging.error("Failed to unfavorite post %s: %s", post_id, e)

            # 3. Downvote on e621
            try:
                response = http.post(
                    "e621",
                    f"https://e621.net/posts/{post_id}/votes.json",
                    auth=auth, headers=headers,
                    data={"score": -1, "no_unvote": "false"}, timeout=15
                )
                if response.status_code in [200, 201]:
                    logging.info("Downvoted post %s on e621", post_id)
                else:
                    logging.warning("Unexpected status downvoting post %s: HTTP %s",
                                    post_id, response.status_code)
            except Exception as e:
                logging.error("Failed to downvote post %s: %s", post_id, e)

            # 4. Delete from database
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM source_links  WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts_artists WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts_tags    WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts         WHERE id      = %s", (post_id,))
                conn.commit()
                logging.info("Deleted post %s from database", post_id)
                removed += 1
            except Exception as e:
                conn.rollback()
                logging.error("Failed to delete post %s from database: %s", post_id, e)
                failed += 1
            finally:
                cursor.close()

        except Exception as e:
            logging.error("Unexpected error processing post %s: %s", post_id, e)
            failed += 1

        # e621 rate limit: max 2 requests/sec; sleep between posts
        time.sleep(0.6)

    return removed, failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Remove favorites by artists listed in ai_flagged_artists"
    )
    parser.add_argument('--dry-run', action='store_true',
                        help="Preview changes without applying them")
    args = parser.parse_args(argv)

    log_dir = str(archive_paths.LOG_DIR)
    setup_logging(log_dir)

    if args.dry_run:
        logging.info("=== DRY RUN MODE - no changes will be made ===")

    logging.info("Starting AI artist post removal")

    config = load_config()
    mysql_config = config.get("mysql", {})
    e621_config  = config.get("e621", {})

    conn = connect_to_mysql(mysql_config)
    if conn is None:
        logging.error("Failed to connect to database")
        sys.exit(1)

    try:
        # Load artist list
        artists = load_ai_flagged_artists(conn)
        if not artists:
            logging.error("No artists found in ai_flagged_artists - nothing to do")
            sys.exit(1)

        logging.info("Loaded %d artist(s): %s", len(artists), ', '.join(artists))

        posts = fetch_posts_by_artists(conn, artists)

        if not posts:
            logging.info("No posts found for the listed AI artists")
            return

        logging.info("Found %d post(s) to process", len(posts))

        removed, failed = process_posts(conn, e621_config, posts, dry_run=args.dry_run)

        if args.dry_run:
            logging.info("DRY RUN complete - would have removed %d post(s)", removed)
        else:
            logging.info("Done - removed: %d, failed: %d", removed, failed)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
