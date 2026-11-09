# fff: the cross-database query uses the active environment's schema names;
# fff: the e621_main.py sync runs in-process (fff.archive.sync.main) instead of a subprocess.
"""
Script to favorite e621 posts that exist in image_sorting_hashes but not in favorites database.

Queries posts from image_sorting_hashes.all_files that have an e621_post_id but aren't
in the e621_posts_db.posts table (meaning they're not favorited). Then checks each post
on e621 and favorites it if the user hasn't already favorited it.

Usage:
    python favorite_missing_posts.py [--dry-run] [--batch-limit N] [--no-sync]

Options:
    --dry-run       Preview posts that would be favorited without making changes
    --batch-limit N Process only N posts (useful for testing)
    --no-sync       Skip running e621_main.py after favoriting posts
"""

import logging
import argparse
import time
import requests

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql, connect_to_image_sorting_hashes_db
from fff.archive.api import get_auth

def get_missing_favorited_posts(e621_conn, image_sorting_conn, batch_limit=None):
    """
    Query posts from image_sorting_hashes that have e621_post_id but aren't in favorites DB.

    Returns list of dicts with keys: e621_post_id, file_name, file_path
    """
    cursor = image_sorting_conn.cursor(dictionary=True)

    DB = archive_paths.db_names()  # fff: prod or test_ schema names
    query = f"""
        SELECT af.e621_post_id, af.file_name, af.file_path
        FROM {DB['sorter']}.all_files af
        LEFT JOIN {DB['archive']}.posts p ON p.id = af.e621_post_id
        WHERE (af.e621_post_id IS NOT NULL) AND (p.id IS NULL)
        ORDER BY af.e621_post_id
    """

    if batch_limit:
        query += f" LIMIT {batch_limit}"

    try:
        cursor.execute(query)
        results = cursor.fetchall()
        logging.info(f"Found {len(results)} posts in image_sorting_hashes not in favorites database")
        return results
    except Exception as e:
        logging.error(f"Error querying missing favorited posts: {e}")
        return []
    finally:
        cursor.close()

def check_if_favorited(post_id, e621_config):
    """
    Check if a post is currently favorited by the user via e621 API.

    Returns:
        True if favorited, False if not favorited, None if error/post doesn't exist
    """
    headers = {"User-Agent": e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")}
    auth = get_auth(e621_config)

    # Get post details to check if it's favorited
    # Not ?v2=true: that response format has no is_favorited field, which read as "not favorited".
    url = f"https://e621.net/posts/{post_id}.json"

    try:
        # Rate limit: 1 second between API calls
        time.sleep(1)

        response = requests.get(url, headers=headers, auth=auth, timeout=30)

        if response.status_code == 404:
            logging.warning(f"Post {post_id} does not exist (404)")
            return None

        response.raise_for_status()
        data = response.json()
        post = data if "id" in data else data.get("post", {})

        # Check if current user has favorited it; a response without the field is "unknown"
        if "is_favorited" not in post:
            logging.error(f"No is_favorited field for post {post_id} (not logged in?)")
            return None
        is_favorited = bool(post["is_favorited"])
        logging.debug(f"Post {post_id}: is_favorited={is_favorited}")

        return is_favorited

    except requests.exceptions.Timeout:
        logging.error(f"Timeout checking if post {post_id} is favorited")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error checking if post {post_id} is favorited: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error checking post {post_id}: {e}")
        return None

def favorite_post(post_id, e621_config):
    """
    Favorite a post on e621 using the API.

    Returns:
        True if successfully favorited, False otherwise
    """
    headers = {"User-Agent": e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")}
    auth = get_auth(e621_config)

    # e621 API endpoint for favoriting
    url = "https://e621.net/favorites.json"
    data = {"post_id": post_id}

    try:
        # Rate limit: 1 second between API calls
        time.sleep(1)

        response = requests.post(url, headers=headers, auth=auth, data=data, timeout=30)

        if response.status_code in [200, 201]:
            logging.info(f"Successfully favorited post {post_id}")
            return True
        elif response.status_code == 422:
            # Already favorited (or other validation error)
            logging.warning(f"Post {post_id} may already be favorited (422 response)")
            return False
        else:
            logging.error(f"Failed to favorite post {post_id}: HTTP {response.status_code}")
            logging.debug(f"Response: {response.text}")
            return False

    except requests.exceptions.Timeout:
        logging.error(f"Timeout favoriting post {post_id}")
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error favoriting post {post_id}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error favoriting post {post_id}: {e}")
        return False

def process_missing_posts(missing_posts, e621_config, dry_run=False):
    """
    Process list of missing posts: check if favorited, and favorite if not.

    Returns:
        dict with stats: already_favorited, newly_favorited, failed, errors
    """
    stats = {
        "already_favorited": 0,
        "newly_favorited": 0,
        "failed": 0,
        "errors": 0,
        "not_found": 0
    }

    total = len(missing_posts)

    for idx, post_info in enumerate(missing_posts, 1):
        post_id = post_info["e621_post_id"]
        file_name = post_info.get("file_name", "unknown")

        logging.info(f"[{idx}/{total}] Processing post {post_id} ({file_name})")

        # Check if already favorited
        is_favorited = check_if_favorited(post_id, e621_config)

        if is_favorited is None:
            # Post doesn't exist or error checking
            stats["not_found"] += 1
            continue

        if is_favorited:
            logging.info(f"Post {post_id} is already favorited")
            stats["already_favorited"] += 1
            continue

        # Not favorited - favorite it
        if dry_run:
            logging.info(f"[DRY RUN] Would favorite post {post_id}")
            stats["newly_favorited"] += 1
        else:
            success = favorite_post(post_id, e621_config)
            if success:
                stats["newly_favorited"] += 1
            else:
                stats["failed"] += 1

    return stats

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Favorite e621 posts from image_sorting_hashes that aren't in favorites database"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview posts that would be favorited without making changes"
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        help="Process only N posts (useful for testing)"
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip running e621_main.py after favoriting posts"
    )

    args = parser.parse_args(argv)    # Setup
    setup_logging(str(archive_paths.LOG_DIR))  # fff: was ./logs
    config = load_config()

    logging.info("="*80)
    logging.info("Favorite Missing Posts Script")
    logging.info("="*80)

    if args.dry_run:
        logging.info("DRY RUN MODE - No posts will be favorited")
    if args.batch_limit:
        logging.info(f"Batch limit: {args.batch_limit} posts")

    # Connect to databases
    e621_conn = connect_to_mysql(config["mysql"])
    if not e621_conn:
        logging.error("Failed to connect to e621_posts_db database")
        return

    image_sorting_conn = connect_to_image_sorting_hashes_db(config["mysql"])
    if not image_sorting_conn:
        logging.error("Failed to connect to image_sorting_hashes database")
        e621_conn.close()
        return

    try:
        # Get missing posts
        missing_posts = get_missing_favorited_posts(
            e621_conn,
            image_sorting_conn,
            batch_limit=args.batch_limit
        )

        if not missing_posts:
            logging.info("No missing posts found - all posts in image_sorting_hashes are favorited")
            return

        # Process missing posts
        logging.info(f"Starting to process {len(missing_posts)} posts...")
        stats = process_missing_posts(missing_posts, config["e621"], dry_run=args.dry_run)

        # Print summary
        logging.info("="*80)
        logging.info("SUMMARY")
        logging.info("="*80)
        logging.info(f"Total posts processed: {len(missing_posts)}")
        logging.info(f"Already favorited: {stats['already_favorited']}")
        logging.info(f"Newly favorited: {stats['newly_favorited']}")
        logging.info(f"Failed to favorite: {stats['failed']}")
        logging.info(f"Post not found: {stats['not_found']}")

        if args.dry_run:
            logging.info("\nDRY RUN - No changes were made")
        else:
            logging.info(f"\nSuccessfully favorited {stats['newly_favorited']} posts")

            # Run e621_main.py to sync newly favorited posts to database
            if not args.no_sync and stats['newly_favorited'] > 0:
                logging.info("="*80)
                logging.info("Running e621_main.py to sync newly favorited posts to database...")
                logging.info("="*80)

                try:
                    # fff: was subprocess.run([sys.executable, "e621_main.py"]); now in-process
                    from fff.archive import sync
                    try:
                        sync.main([])
                        returncode = 0
                    except SystemExit as exit_exc:
                        code = exit_exc.code
                        returncode = code if isinstance(code, int) else (0 if code is None else 1)

                    if returncode == 0:
                        logging.info("Successfully completed e621_main.py sync")
                    else:
                        logging.warning(f"e621_main.py exited with code {returncode}")

                except Exception as e:
                    logging.error(f"Error running e621_main.py: {e}")
                    logging.info("You may want to run e621_main.py manually to sync the database")

    except Exception as e:
        logging.error(f"Error in main execution: {e}", exc_info=True)
    finally:
        e621_conn.close()
        image_sorting_conn.close()
        logging.info("Database connections closed")

if __name__ == "__main__":
    main()
