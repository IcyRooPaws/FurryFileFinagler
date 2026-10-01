#!/usr/bin/env python3
"""
Remove AI-Tagged Posts

Standalone script that processes config/ai_tags.txt and for each matching post:
  - Sends the local file to the recycle bin
  - Unfavorites the post on e621
  - Deletes the post from the local database
  - Logs the artist name(s) to data/ai_artist.md

Usage:
    python scripts/favorites/remove_ai_tagged_posts.py [--dry-run]

Options:
    --dry-run   Preview what would be removed without making any changes.
"""

import os
import sys
import argparse
import logging
from pathlib import Path

import shutil

import requests

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, create_tables_if_not_exists, record_ai_flagged_artists
from fff.archive.logging_setup import setup_logging


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_ai_tags(tags_file):
    """Load tags from a text file, ignoring blank lines and # comments."""
    tags = []
    try:
        with open(tags_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if ',' in line:
                    tags.extend(t.strip() for t in line.split(',') if t.strip())
                else:
                    tags.append(line)
    except FileNotFoundError:
        logging.error("AI tags file not found: %s", tags_file)
        return []
    except Exception as e:
        logging.error("Error reading AI tags file: %s", e)
        return []

    seen = set()
    unique = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            unique.append(tag)

    return unique


def fetch_ai_tagged_posts(conn, ai_tags):
    """Fetch non-deleted posts that match any AI tag or have an artist name matching the AI tags list."""
    cursor = conn.cursor()
    placeholders = ', '.join(['%s'] * len(ai_tags))
    query = f"""
        SELECT DISTINCT p.id, p.file_name, p.file_path, p.file_url, p.rating,
               GROUP_CONCAT(DISTINCT t.name  ORDER BY t.name  SEPARATOR ', ') AS matched_tags,
               GROUP_CONCAT(DISTINCT a.name  ORDER BY a.name  SEPARATOR ', ') AS artist_names
        FROM posts p
        LEFT JOIN posts_tags   pt ON pt.post_id   = p.id
        LEFT JOIN tags          t  ON t.tag_id     = pt.tag_id
        LEFT JOIN posts_artists pa ON pa.post_id   = p.id
        LEFT JOIN artists       a  ON a.artist_id  = pa.artist_id
        WHERE (t.name IN ({placeholders}) OR a.name IN ({placeholders}))
          AND p.deleted = 0
        GROUP BY p.id, p.file_name, p.file_path, p.file_url, p.rating
        ORDER BY p.id
    """
    cursor.execute(query, ai_tags + ai_tags)
    columns = [desc[0] for desc in cursor.description]
    posts = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    return posts


def _artist_from_filename(file_name):
    """Parse the artist portion from a file_name like '{artist}-{rating}-{id}.ext'."""
    if not file_name:
        return None
    base = os.path.splitext(file_name)[0]  # strip extension
    parts = base.rsplit('-', 2)             # split off rating + post_id from right
    if len(parts) == 3 and parts[0] and parts[0] != 'unknown':
        return parts[0]
    return None


def log_ai_artists(conn, posts, output_path, dry_run=False):
    """Append new artist names from AI-tagged posts to data/ai_artist.md."""
    artists_to_log = set()
    for post in posts:
        raw = post.get('artist_names') or ''
        db_artists = {name.strip() for name in raw.split(', ') if name.strip()}
        if db_artists:
            artists_to_log.update(db_artists)
        else:
            # Fallback: parse artist from file_name when posts_artists links are missing
            fallback = _artist_from_filename(post.get('file_name'))
            if fallback:
                artists_to_log.add(fallback)

    if not artists_to_log:
        logging.info("No artist names to log")
        return

    if not dry_run:
        record_ai_flagged_artists(conn, sorted(artists_to_log))

    # Load already-logged artists to avoid duplicates
    existing = set()
    if os.path.exists(output_path):
        with open(output_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    existing.add(line.lstrip('- ').strip())

    new_artists = sorted(artists_to_log - existing)
    if not new_artists:
        logging.info("All artist names already present in %s", output_path)
        return

    if dry_run:
        logging.info("[DRY RUN] Would log %d new artist(s) to %s: %s",
                     len(new_artists), output_path, ', '.join(new_artists))
        return

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    is_new_file = not os.path.exists(output_path)
    with open(output_path, 'a', encoding='utf-8') as f:
        if is_new_file:
            f.write("# AI Artists\n\n")
        for artist in new_artists:
            f.write(f"- {artist}\n")

    logging.info("Logged %d new AI artist(s) to %s: %s",
                 len(new_artists), output_path, ', '.join(new_artists))


def process_posts(conn, e621_config, posts, dry_run=False):
    """Trash file, unfavorite on e621, and delete from DB for each post."""
    deleted_count = 0
    failed_count = 0

    for post in posts:
        post_id = post['id']
        file_path = post.get('file_path')
        matched_tags = post.get('matched_tags', '')
        artist_names = post.get('artist_names', '')

        logging.info("Processing post %s | tags: %s | artists: %s",
                     post_id, matched_tags, artist_names)

        if dry_run:
            logging.info("[DRY RUN] Would trash %s, unfavorite %s, delete from DB",
                         file_path or '(no file)', post_id)
            deleted_count += 1
            continue

        try:
            # 1. Move file to AI folder
            if file_path and os.path.exists(file_path):
                try:
                    ai_dest_dir = archive_paths.ai_tag_directory()  # fff: config archive.ai_cleanup
                    os.makedirs(ai_dest_dir, exist_ok=True)
                    dest = os.path.join(ai_dest_dir, os.path.basename(file_path))
                    shutil.move(file_path, dest)
                    logging.info("Moved file to AI folder: %s -> %s", file_path, dest)
                except Exception as e:
                    logging.error("Failed to move file %s: %s", file_path, e)

            # 2. Unfavorite on e621
            try:
                url = f"https://e621.net/favorites/{post_id}.json"
                auth = (e621_config.get("login"), e621_config.get("api_key"))
                response = requests.delete(url, auth=auth, headers={
                    "User-Agent": e621_config.get("user_agent", "e621 archive/1.0")
                }, timeout=15)
                if response.status_code in [200, 204, 404, 422]:
                    logging.info("Unfavorited post %s on e621", post_id)
                else:
                    logging.warning("Unexpected status unfavoriting post %s: HTTP %s",
                                    post_id, response.status_code)
            except Exception as e:
                logging.error("Failed to unfavorite post %s on e621: %s", post_id, e)

            # 3. Downvote on e621
            try:
                vote_url = f"https://e621.net/posts/{post_id}/votes.json"
                response = requests.post(vote_url, auth=auth, headers={
                    "User-Agent": e621_config.get("user_agent", "e621 archive/1.0")
                }, data={"score": -1, "no_unvote": "false"}, timeout=15)
                if response.status_code in [200, 201]:
                    logging.info("Downvoted post %s on e621", post_id)
                else:
                    logging.warning("Unexpected status downvoting post %s: HTTP %s",
                                    post_id, response.status_code)
            except Exception as e:
                logging.error("Failed to downvote post %s on e621: %s", post_id, e)

            # 4. Delete from database
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM source_links  WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts_artists WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts_tags    WHERE post_id = %s", (post_id,))
                cursor.execute("DELETE FROM posts         WHERE id      = %s", (post_id,))
                conn.commit()
                logging.info("Deleted post %s from database", post_id)
                deleted_count += 1
            except Exception as e:
                conn.rollback()
                logging.error("Failed to delete post %s from database: %s", post_id, e)
                failed_count += 1
            finally:
                cursor.close()

        except Exception as e:
            logging.error("Unexpected error processing post %s: %s", post_id, e)
            failed_count += 1

    return deleted_count, failed_count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Remove posts matching AI tags from e621 favorites and local DB")
    parser.add_argument('--dry-run', action='store_true',
                        help="Preview changes without applying them")
    args = parser.parse_args(argv)

    log_dir = str(archive_paths.LOG_DIR)
    setup_logging(log_dir)

    if args.dry_run:
        logging.info("=== DRY RUN MODE - no changes will be made ===")

    logging.info("Starting AI-tagged post removal")

    # Load config
    config = load_config()
    mysql_config = config.get("mysql", {})
    e621_config = config.get("e621", {})

    # Load AI tags
    ai_tags_path = archive_paths.AI_TAGS
    ai_tags = load_ai_tags(str(ai_tags_path))
    if not ai_tags:
        logging.error("No AI tags found in %s - nothing to do", ai_tags_path)
        sys.exit(1)

    logging.info("Loaded %d AI tag(s): %s", len(ai_tags), ', '.join(ai_tags))

    # Connect to DB
    conn = connect_to_mysql(mysql_config)
    if conn is None:
        logging.error("Failed to connect to database")
        sys.exit(1)
    create_tables_if_not_exists(conn)

    try:
        posts = fetch_ai_tagged_posts(conn, ai_tags)

        if not posts:
            logging.info("No posts found matching AI tags")
            return

        logging.info("Found %d post(s) matching AI tags", len(posts))

        # Log artists first (before deletion)
        ai_artist_log = str(archive_paths.AI_ARTIST_MD)
        log_ai_artists(conn, posts, ai_artist_log, dry_run=args.dry_run)

        # Process deletions
        deleted, failed = process_posts(conn, e621_config, posts, dry_run=args.dry_run)

        if args.dry_run:
            logging.info("DRY RUN complete - would have removed %d post(s)", deleted)
        else:
            logging.info("Done - removed: %d, failed: %d", deleted, failed)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
