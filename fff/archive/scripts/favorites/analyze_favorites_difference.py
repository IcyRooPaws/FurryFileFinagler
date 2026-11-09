#!/usr/bin/env python3
# fff: added main(argv=None) wrapping analyze_differences() for the fff CLI.
"""
Script to find posts that are in the database but not in current favorites.
This helps identify posts that may have been unfavorited or removed from favorites.
"""

import logging


from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql
from fff.archive.api import fetch_favorites

def get_all_db_post_ids(conn):
    """Get all post IDs from the database."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM posts ORDER BY id")
        results = cursor.fetchall()
        return {row[0] for row in results}
    except Exception as e:
        logging.error("Failed to get post IDs from database: %s", e)
        return set()
    finally:
        cursor.close()

def get_current_favorites_post_ids(e621_config):
    """Get all post IDs from current favorites on e621."""
    favorites_posts = fetch_favorites(e621_config)
    if not favorites_posts:
        logging.error("Failed to fetch favorites from e621")
        return set()

    current_fav_ids = {post.get("id") for post in favorites_posts if post.get("id")}
    logging.info("Found %s posts in current favorites", len(current_fav_ids))
    return current_fav_ids

def get_post_details(conn, post_ids):
    """Get details for specific post IDs."""
    if not post_ids:
        return []

    cursor = conn.cursor()
    try:
        # Convert set to sorted list for consistent ordering
        post_ids_list = sorted(list(post_ids))
        placeholders = ','.join(['%s'] * len(post_ids_list))
        query = f"""
        SELECT id, file_name, rating, ingested_at, deleted, deleted_at, file_path
        FROM posts
        WHERE id IN ({placeholders})
        ORDER BY id
        """
        cursor.execute(query, post_ids_list)
        results = cursor.fetchall()

        posts = []
        for row in results:
            posts.append({
                'id': row[0],
                'file_name': row[1],
                'rating': row[2],
                'ingested_at': row[3],
                'deleted': bool(row[4]),
                'deleted_at': row[5],
                'file_path': row[6]
            })
        return posts
    except Exception as e:
        logging.error("Failed to get post details: %s", e)
        return []
    finally:
        cursor.close()

def analyze_differences():
    """Main function to analyze differences between DB and current favorites."""
    config = load_config()
    e621_config = config.get("e621", {})
    mysql_config = config.get("mysql", {})
    log_config = config.get("log", {})

    log_dir = log_config.get("directory", "./logs")
    setup_logging(log_dir)

    logging.info("Starting favorites difference analysis...")

    # Connect to database
    conn = connect_to_mysql(mysql_config)
    if not conn:
        logging.error("Failed to connect to MySQL database")
        return

    try:
        # Get all post IDs from database
        logging.info("Getting all post IDs from database...")
        db_post_ids = get_all_db_post_ids(conn)
        logging.info("Found %s posts in database", len(db_post_ids))

        # Get current favorites
        logging.info("Fetching current favorites from e621...")
        current_fav_ids = get_current_favorites_post_ids(e621_config)

        # Find differences
        posts_in_db_not_in_favs = db_post_ids - current_fav_ids
        posts_in_favs_not_in_db = current_fav_ids - db_post_ids

        logging.info("Analysis Results:")
        logging.info("Posts in database: %s", len(db_post_ids))
        logging.info("Posts in current favorites: %s", len(current_fav_ids))
        logging.info("Posts in DB but NOT in current favorites: %s", len(posts_in_db_not_in_favs))
        logging.info("Posts in current favorites but NOT in DB: %s", len(posts_in_favs_not_in_db))

        # Get details for posts in DB but not in favorites
        if posts_in_db_not_in_favs:
            logging.info("\n=== POSTS IN DATABASE BUT NOT IN CURRENT FAVORITES ===")
            db_only_posts = get_post_details(conn, posts_in_db_not_in_favs)

            deleted_count = 0
            active_count = 0

            for post in db_only_posts:
                status = "DELETED" if post['deleted'] else "ACTIVE"
                if post['deleted']:
                    deleted_count += 1
                else:
                    active_count += 1

                logging.info("Post ID %s: %s (%s) - %s - Ingested: %s",
                           post['id'],
                           post['file_name'] or 'No file_name',
                           post['rating'],
                           status,
                           post['ingested_at'])

                if post['deleted'] and post['deleted_at']:
                    logging.info("  L- Deleted at: %s", post['deleted_at'])

            logging.info("\nSummary of DB-only posts:")
            logging.info("  Active posts: %s", active_count)
            logging.info("  Deleted posts: %s", deleted_count)

        # Get details for posts in favorites but not in DB
        if posts_in_favs_not_in_db:
            logging.info("\n=== POSTS IN CURRENT FAVORITES BUT NOT IN DATABASE ===")
            logging.info("Post IDs: %s", sorted(list(posts_in_favs_not_in_db)))

        # Recommendations
        logging.info("\n=== RECOMMENDATIONS ===")

        if posts_in_db_not_in_favs:
            deleted_in_db_not_favs = len([p for p in db_only_posts if p['deleted']])
            active_in_db_not_favs = len([p for p in db_only_posts if not p['deleted']])

            if deleted_in_db_not_favs > 0:
                logging.info("* %s deleted posts in DB are no longer in favorites - this is normal", deleted_in_db_not_favs)

            if active_in_db_not_favs > 0:
                logging.info("* %s active posts in DB are no longer in favorites - these may have been unfavorited", active_in_db_not_favs)
                logging.info("  Consider running a cleanup or investigation on these posts")

        if posts_in_favs_not_in_db:
            logging.info("* %s posts in current favorites are missing from DB - consider running a sync", len(posts_in_favs_not_in_db))

    except Exception as e:
        logging.error("Error during analysis: %s", e)
    finally:
        conn.close()

def main(argv=None):
    """fff: CLI entry point (the original had none and takes no arguments)."""
    analyze_differences()

if __name__ == "__main__":
    main()
