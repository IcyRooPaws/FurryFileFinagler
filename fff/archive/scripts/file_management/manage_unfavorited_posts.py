#!/usr/bin/env python3
"""
Script to manage posts that are in the database but not in current favorites.
Provides options to clean up, archive, or investigate these posts.
"""

import logging


from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive import paths as archive_paths
from fff.archive.db import connect_to_mysql, create_tables_if_not_exists, archive_unfavorited_posts
from fff.archive.post_files import move_post_files

def get_unfavorited_posts(conn, config):
    """Get detailed information about posts in DB but not in current favorites."""
    from fff.archive.api import fetch_favorites

    # Fetch current favorites from e621 API
    logging.info("Fetching current favorites from e621 API...")
    try:
        current_favorites = fetch_favorites(config.get("e621", {}))
        current_favorite_ids = {post.get("id") for post in current_favorites if post.get("id")}
        logging.info(f"Found {len(current_favorite_ids)} current favorites")
    except Exception as e:
        logging.error("Failed to fetch current favorites: %s", e)
        return []

    cursor = conn.cursor()
    try:
        # Get all posts currently in the database
        query = """
        SELECT id, file_name, rating, ingested_at, file_path,
               (CASE WHEN file_path IS NOT NULL AND file_path != '' THEN 1 ELSE 0 END) as has_file
        FROM posts
        ORDER BY ingested_at DESC
        """
        cursor.execute(query)
        all_db_posts = cursor.fetchall()

        logging.info(f"Found {len(all_db_posts)} posts in database")

        # Filter out posts that are still in current favorites
        unfavorited_posts = []
        for row in all_db_posts:
            post_id = row[0]
            if post_id not in current_favorite_ids:
                unfavorited_posts.append({
                    'id': post_id,
                    'file_name': row[1],
                    'rating': row[2],
                    'ingested_at': row[3],
                    'file_path': row[4],
                    'has_file': bool(row[5])
                })

        logging.info(f"Found {len(unfavorited_posts)} posts that are no longer in favorites")
        return unfavorited_posts

    except Exception as e:
        logging.error("Failed to get unfavorited posts: %s", e)
        return []
    finally:
        cursor.close()

def delete_posts_from_db(conn, post_ids):
    """Delete posts and all related data from the database."""
    if not post_ids:
        return

    cursor = conn.cursor()
    try:
        placeholders = ','.join(['%s'] * len(post_ids))

        # Delete related data first (foreign key constraints)
        tables_to_clean = [
            'source_links',
            'posts_artists',
            'posts_tags'
        ]

        for table in tables_to_clean:
            delete_query = f"DELETE FROM {table} WHERE post_id IN ({placeholders})"
            cursor.execute(delete_query, post_ids)
            logging.info(f"Deleted {cursor.rowcount} rows from {table}")

        # Delete the posts themselves
        posts_query = f"DELETE FROM posts WHERE id IN ({placeholders})"
        cursor.execute(posts_query, post_ids)
        logging.info(f"Deleted {cursor.rowcount} posts from posts table")

        conn.commit()
        logging.info(f"Successfully deleted {len(post_ids)} posts and all related data")

    except Exception as e:
        logging.error("Failed to delete posts: %s", e)
        conn.rollback()
    finally:
        cursor.close()

def remove_posts(conn, posts):
    """Move every local copy of the posts' files to the unfavorited folder, then delete the posts.

    Posts whose files could not be moved stay in the database. Returns the number deleted.
    """
    destination = archive_paths.unfavorited_directory()
    result = move_post_files(conn, [p['id'] for p in posts], destination, reason="unfavorited:manual")
    print(f"Moved {result['moved']} files to {destination}.")
    if result['failed']:
        print(f"Failed to move {result['failed']} files; those posts stay in the database.")
    post_ids = [p['id'] for p in posts if p['id'] in result['cleared']]
    delete_posts_from_db(conn, post_ids)
    return len(post_ids)


def main(argv=None):
    """Main function with interactive options. fff: argv is accepted for the CLI but unused."""
    config = load_config()
    mysql_config = config.get("mysql", {})
    log_config = config.get("log", {})

    log_dir = log_config.get("directory", "./logs")
    setup_logging(log_dir)

    print("=== Unfavorited Posts Management ===")
    print("Analyzing posts in database vs current favorites...")
    print()

    # Connect to database first to get the count
    conn = connect_to_mysql(mysql_config)
    if not conn:
        logging.error("Failed to connect to MySQL database")
        return

    create_tables_if_not_exists(conn)

    try:
        posts = get_unfavorited_posts(conn, config)

        if not posts:
            print("No unfavorited posts found. All database posts are still in your current favorites.")
            return

        print(f"Found {len(posts)} posts in database that are no longer in your favorites.")
        print()
        print("Options:")
        print("1. Archive and delete all unfavorited posts (files move to the unfavorited folder)")
        print("2. Just archive post information (no deletion)")
        print("3. Delete without archiving")
        print("4. Show detailed post information")
        print("5. Exit")
        print()

        choice = input("Enter your choice (1-5): ").strip()

        if choice == '5':
            print("Exiting...")
            return

        if choice == '1':
            # Archive and delete
            print(f"\nArchiving information for {len(posts)} posts...")
            if archive_unfavorited_posts(conn, posts):
                print("Archive created successfully.")
                confirm = input(f"Are you sure you want to DELETE {len(posts)} posts from the database "
                                "and move their files to the unfavorited folder? (yes/no): ")
                if confirm.lower() == 'yes':
                    print(f"Deleted {remove_posts(conn, posts)} posts from database.")
                else:
                    print("Deletion cancelled.")
            else:
                print("Failed to create archive. Deletion cancelled for safety.")

        elif choice == '2':
            # Just archive
            print(f"\nArchiving information for {len(posts)} posts...")
            if archive_unfavorited_posts(conn, posts):
                print("Archive created successfully. Posts remain in database.")
            else:
                print("Failed to create archive.")

        elif choice == '3':
            # Delete without archiving
            confirm = input(f"Are you sure you want to DELETE {len(posts)} posts WITHOUT archiving "
                            "(their files move to the unfavorited folder)? (yes/no): ")
            if confirm.lower() == 'yes':
                print(f"Deleted {remove_posts(conn, posts)} posts from database.")
            else:
                print("Deletion cancelled.")

        elif choice == '4':
            # Show detailed information
            print(f"\n=== Detailed Information for {len(posts)} Unfavorited Posts ===")

            files_count = sum(1 for p in posts if p['has_file'])
            print(f"Posts with downloaded files: {files_count}")
            print(f"Posts without files: {len(posts) - files_count}")

            rating_counts = {}
            for post in posts:
                rating = post['rating']
                rating_counts[rating] = rating_counts.get(rating, 0) + 1

            print(f"By rating: {rating_counts}")
            print()

            for post in posts:
                print(f"Post {post['id']}: {post['file_name'] or 'No file_name'}")
                print(f"  Rating: {post['rating']}")
                print(f"  Ingested: {post['ingested_at']}")
                print(f"  Has file: {'Yes' if post['has_file'] else 'No'}")
                print(f"  E621: https://e621.net/posts/{post['id']}")
                print()

        else:
            print("Invalid choice.")

    except Exception as e:
        logging.error("Error during processing: %s", e)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
