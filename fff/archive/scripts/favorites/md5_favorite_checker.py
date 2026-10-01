# fff: moved from search_for_posts/; the default md5 file is data/md5_search/md5.txt.
"""
Search md5 hashes on e621, verify favorite status, and favorite missing posts.

Reads md5 hashes from a file (one per line), looks up the corresponding post via
`md5:<hash>` search, checks whether the authenticated user has favorited it, and
favorites it if not already favorited. Supports dry-run to preview actions.

Usage:
    python "md5_favorite_checker.py" [--md5-file PATH] [--dry-run] [--limit N]
"""

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import requests

from fff.archive import paths as archive_paths

from fff.archive.scripts.favorites.favorite_missing_posts import check_if_favorited, favorite_post
from fff.archive.api import get_auth
from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging


def load_md5s(md5_path: Path):
    """Load unique, valid md5 hashes from the given file."""
    md5s = []
    invalid = []
    seen = set()

    try:
        lines = md5_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        logging.error("MD5 file not found: %s", md5_path)
        return md5s, invalid
    except Exception as exc:  # Safety net
        logging.error("Failed to read md5 file %s: %s", md5_path, exc)
        return md5s, invalid

    for raw in lines:
        md5 = raw.strip().lower()
        if not md5:
            continue
        if not re.fullmatch(r"[0-9a-f]{32}", md5):
            invalid.append(md5)
            continue
        if md5 in seen:
            continue
        seen.add(md5)
        md5s.append(md5)

    return md5s, invalid


def fetch_post_by_md5(md5_hash: str, e621_config):
    """Fetch the first post matching the md5 hash. Returns the post dict or None."""
    headers = {"User-Agent": e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")}
    auth = get_auth(e621_config)
    params = {"tags": f"md5:{md5_hash}", "limit": 1, "v2": "true", "mode": "extended"}
    url = "https://e621.net/posts.json"

    try:
        time.sleep(e621_config.get("rate_limit_sleep", 1))
        response = requests.get(url, headers=headers, auth=auth, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        posts = data if isinstance(data, list) else data.get("posts", [])
        if not posts:
            logging.info("No post found for md5 %s", md5_hash)
            return None
        return posts[0]
    except requests.exceptions.RequestException as exc:
        logging.error("HTTP error searching md5 %s: %s", md5_hash, exc)
    except Exception as exc:  # Safety net
        logging.error("Unexpected error searching md5 %s: %s", md5_hash, exc)

    return None


def process_md5s(md5_list, e621_config, dry_run: bool = False, limit: int | None = None):
    stats = {
        "total_checked": 0,
        "not_found": 0,
        "already_favorited": 0,
        "newly_favorited": 0,
        "favoriting_failed": 0,
        "errors": 0,
        "would_favorite": 0,
    }

    # Track hashes to keep in the file (those not confirmed favorited).
    keep_md5s: list[str] = []

    for idx, md5_hash in enumerate(md5_list, start=1):
        if limit and idx > limit:
            break

        stats["total_checked"] += 1

        logging.info("[%s/%s] Looking up md5 %s", idx, len(md5_list), md5_hash)
        post = fetch_post_by_md5(md5_hash, e621_config)
        if not post:
            stats["not_found"] += 1
            continue

        post_id = post.get("id")
        if not post_id:
            logging.warning("  Skipping md5 %s because post ID missing in response", md5_hash)
            stats["errors"] += 1
            continue

        # Prefer the search response flag; fall back to explicit check if absent
        is_favorited = post.get("is_favorited")
        if is_favorited is None:
            is_favorited = check_if_favorited(post_id, e621_config)

        if is_favorited is None:
            logging.warning("  Could not verify favorite status for post %s", post_id)
            stats["errors"] += 1
            continue

        if is_favorited:
            logging.info("  Post %s already favorited", post_id)
            stats["already_favorited"] += 1
            # Already favorited -> remove from file when not dry-run
            remove_md5 = not dry_run
        else:
            if dry_run:
                logging.info("  [DRY RUN] Would favorite post %s", post_id)
                stats["would_favorite"] += 1
                remove_md5 = False
            else:
                success = favorite_post(post_id, e621_config)
                if success:
                    stats["newly_favorited"] += 1
                    remove_md5 = True
                else:
                    stats["favoriting_failed"] += 1
                    remove_md5 = False

        if not remove_md5:
            keep_md5s.append(md5_hash)

    return stats, keep_md5s


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Search md5 list on e621 and favorite posts that are not yet favorited."
    )
    default_md5_path = archive_paths.MD5_SEARCH_DIR / "md5.txt"  # fff: was search_for_posts/md5.txt
    parser.add_argument(
        "--md5-file",
        type=Path,
        default=default_md5_path,
        help="Path to file containing md5 hashes (one per line)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report what would be favorited without making API changes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Process at most N md5 hashes",
    )

    args = parser.parse_args(argv)

    setup_logging(str(archive_paths.LOG_DIR))  # fff: was ./logs
    config = load_config()
    e621_config = config.get("e621", {})

    if not get_auth(e621_config):
        logging.error("No e621 credentials found in config (login/api_key). Aborting.")
        sys.exit(1)

    md5s, invalid = load_md5s(args.md5_file)
    if invalid:
        logging.warning("Ignored %s invalid md5 entries", len(invalid))
    if not md5s:
        logging.error("No valid md5 hashes to process. Exiting.")
        sys.exit(1)

    logging.info("Loaded %s unique md5 hashes from %s", len(md5s), args.md5_file)
    if args.limit:
        logging.info("Processing limit set to %s hashes", args.limit)

    stats, keep_md5s = process_md5s(md5s, e621_config, dry_run=args.dry_run, limit=args.limit)

    logging.info("=" * 80)
    logging.info("Processing complete")
    logging.info("MD5 hashes checked: %s", stats.get("total_checked"))
    logging.info("Not found: %s", stats.get("not_found"))
    logging.info("Already favorited: %s", stats.get("already_favorited"))
    logging.info("Newly favorited: %s", stats.get("newly_favorited"))
    logging.info("Favorite failures: %s", stats.get("favoriting_failed"))
    logging.info("Errors: %s", stats.get("errors"))
    if args.dry_run:
        logging.info("[DRY RUN] Would favorite: %s", stats.get("would_favorite"))
        logging.info("[DRY RUN] md5.txt left unchanged")
    else:
        # Rewrite file with hashes that still need attention
        try:
            args.md5_file.write_text("\n".join(keep_md5s) + ("\n" if keep_md5s else ""), encoding="utf-8")
            removed_count = len(md5s) - len(keep_md5s)
            logging.info("Updated %s (removed %s favorited hashes; remaining %s)", args.md5_file, removed_count, len(keep_md5s))
        except Exception as exc:
            logging.error("Failed to update %s: %s", args.md5_file, exc)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled by user.")
        sys.exit(0)
    except Exception as exc:  # Safety net
        logging.error("Script failed: %s", exc, exc_info=True)
        sys.exit(1)
