"""
Swap favorites from deleted duplicate posts to their superior versions.

For posts with deletion_reason like "Inferior version/duplicate of post #<id>",
this script will:
1) Favorite the referenced better post if it's not already favorited.
2) Unfavorite the old (deleted) post after the better one is favorited/already favored.

Usage:
    python swap_duplicate_favorites.py [--dry-run] [--limit N]

Options:
    --dry-run   Preview actions without favoriting/unfavoriting.
    --limit N   Process at most N duplicate posts (useful for testing).
"""

import argparse
import logging
import re
import sys
import time
import requests

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql
from fff.archive.api import get_auth
from fff.archive.scripts.favorites.favorite_missing_posts import check_if_favorited, favorite_post
from fff.archive.scripts.database.backfill_deletion_reasons import backfill_deletion_reasons


def extract_better_post_id(deletion_reason: str | None) -> int | None:
    """Extract the replacement post ID from the deletion_reason text."""
    if not deletion_reason:
        return None
    match = re.search(r"post\s*#?(\d+)", deletion_reason, re.IGNORECASE)
    return int(match.group(1)) if match else None


def get_duplicate_posts(conn, limit: int | None = None):
    """Fetch posts marked deleted with a duplicate/inferior deletion_reason."""
    cursor = conn.cursor(dictionary=True)
    query = (
        """
        SELECT id, deletion_reason
        FROM posts
        WHERE deleted = 1
          AND deletion_reason IS NOT NULL
          AND deletion_reason != ''
          AND deletion_reason LIKE %s
        ORDER BY deleted_at DESC, id DESC
        """
    )
    params = ["%duplicate of post%"]
    if limit:
        query += " LIMIT %s"
        params.append(limit)

    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def unfavorite_post(post_id: int, e621_config) -> bool:
    """Unfavorite a post on e621 using the API."""
    headers = {"User-Agent": e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")}
    auth = get_auth(e621_config)
    url = f"https://e621.net/favorites/{post_id}.json"

    try:
        # Rate limit: 1 second between API calls
        time.sleep(1)

        response = requests.delete(url, headers=headers, auth=auth, timeout=30)

        if response.status_code in (200, 204):
            logging.info("Unfavorited post %s", post_id)
            return True
        if response.status_code in (404, 422):
            # Already removed or not favorited
            logging.info("Post %s already not favorited (HTTP %s)", post_id, response.status_code)
            return True

        logging.error("Failed to unfavorite post %s: HTTP %s", post_id, response.status_code)
        logging.debug("Response: %s", response.text)
        return False
    except requests.exceptions.Timeout:
        logging.error("Timeout unfavoriting post %s", post_id)
        return False
    except requests.exceptions.RequestException as e:
        logging.error("Error unfavoriting post %s: %s", post_id, e)
        return False
    except Exception as e:
        logging.error("Unexpected error unfavoriting post %s: %s", post_id, e)
        return False


def process_swaps(conn, e621_config, limit: int | None = None, dry_run: bool = False):
    duplicates = get_duplicate_posts(conn, limit=limit)
    if not duplicates:
        logging.info("No duplicate/inferior posts found to process.")
        return {
            "total": 0,
            "parsed": 0,
            "already_favorited": 0,
            "newly_favorited": 0,
            "fav_failed": 0,
            "unfav_success": 0,
            "unfav_failed": 0,
            "skipped": 0,
            "errors": 0,
            "would_favorite": 0,
            "would_unfavorite": 0,
        }

    logging.info("Found %s duplicate posts to process", len(duplicates))

    stats = {
        "total": len(duplicates),
        "parsed": 0,
        "already_favorited": 0,
        "newly_favorited": 0,
        "fav_failed": 0,
        "unfav_success": 0,
        "unfav_failed": 0,
        "skipped": 0,
        "errors": 0,
        "would_favorite": 0,
        "would_unfavorite": 0,
    }

    for idx, row in enumerate(duplicates, start=1):
        old_post_id = row["id"]
        reason = row.get("deletion_reason")
        better_post_id = extract_better_post_id(reason)

        if not better_post_id:
            logging.warning("[%s/%s] Could not parse better post ID from reason for post %s: %s", idx, len(duplicates), old_post_id, reason)
            stats["skipped"] += 1
            continue

        stats["parsed"] += 1
        logging.info("[%s/%s] Old post %s -> better post %s", idx, len(duplicates), old_post_id, better_post_id)

        # Check if better post is already favorited
        is_better_favorited = check_if_favorited(better_post_id, e621_config)
        if is_better_favorited is None:
            stats["errors"] += 1
            logging.warning("  Skipping; unable to verify favorite status for better post %s", better_post_id)
            continue

        favored_or_success = False
        if is_better_favorited:
            stats["already_favorited"] += 1
            favored_or_success = True
            logging.info("  Better post %s already favorited", better_post_id)
        else:
            if dry_run:
                logging.info("  [DRY RUN] Would favorite better post %s", better_post_id)
                stats["would_favorite"] += 1
            else:
                success = favorite_post(better_post_id, e621_config)
                if success:
                    stats["newly_favorited"] += 1
                    favored_or_success = True
                else:
                    stats["fav_failed"] += 1

        # Only unfavorite old post if the better one is (or would be) favorited
        if favored_or_success or (dry_run and is_better_favorited is False):
            if dry_run:
                logging.info("  [DRY RUN] Would unfavorite old post %s", old_post_id)
                stats["would_unfavorite"] += 1
            else:
                unfav_ok = unfavorite_post(old_post_id, e621_config)
                if unfav_ok:
                    stats["unfav_success"] += 1
                else:
                    stats["unfav_failed"] += 1

    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Favorite replacement posts for deleted duplicates and unfavorite the old ones"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without making API changes",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Skip running deletion reason backfill before processing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most N duplicate posts",
    )

    args = parser.parse_args(argv)

    setup_logging(str(archive_paths.LOG_DIR))  # fff: was ./logs
    config = load_config()
    e621_config = config.get("e621", {})

    if not args.no_backfill:
        logging.info("Running deletion reason backfill before swapping duplicates...")
        try:
            backfill_deletion_reasons(config)
        except Exception as exc:
            logging.warning("Deletion reason backfill failed or was skipped due to error: %s", exc)

    conn = connect_to_mysql(config.get("mysql", {}))
    if conn is None:
        logging.error("Failed to connect to MySQL database")
        sys.exit(1)

    try:
        stats = process_swaps(conn, e621_config, limit=args.limit, dry_run=args.dry_run)
    finally:
        conn.close()

    logging.info("=" * 80)
    logging.info("Processed duplicates: %s", stats.get("total", 0))
    logging.info("Parsed replacement IDs: %s", stats.get("parsed", 0))
    logging.info("Already favorited: %s", stats.get("already_favorited", 0))
    logging.info("Newly favorited: %s", stats.get("newly_favorited", 0))
    logging.info("Favorite failures: %s", stats.get("fav_failed", 0))
    logging.info("Unfavorite success: %s", stats.get("unfav_success", 0))
    logging.info("Unfavorite failures: %s", stats.get("unfav_failed", 0))
    logging.info("Skipped (unparsable): %s", stats.get("skipped", 0))
    logging.info("Errors (status check failures): %s", stats.get("errors", 0))
    if args.dry_run:
        logging.info("[DRY RUN] Would favorite: %s", stats.get("would_favorite", 0))
        logging.info("[DRY RUN] Would unfavorite: %s", stats.get("would_unfavorite", 0))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        sys.exit(0)
    except Exception as exc:  # Safety net
        logging.error("Script failed: %s", exc, exc_info=True)
        sys.exit(1)
