#!/usr/bin/env python3
# fff: cached API pages are read from data/cache/e621_pages (was ./temp_responses).
"""
Script to backfill parent-child relationships for posts from cached API responses.
This script reads relationship data from temp_responses JSON files and populates
the post_relationships table.
"""

import sys
import logging
import json
from datetime import datetime

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, insert_post_relationships_bulk

def setup_logging():
    """Setup logging."""
    log_dir = str(archive_paths.LOG_DIR)
    import os
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"{log_dir}/{timestamp}_backfill_relationships.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    return log_file

def get_posts_without_relationships(conn, limit=None):
    """Get posts that don't have relationship data yet."""
    cursor = conn.cursor()
    try:
        query = """
            SELECT p.id
            FROM posts p
            LEFT JOIN post_relationships pr ON p.id = pr.post_id
            WHERE pr.post_id IS NULL
            ORDER BY p.id DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        results = cursor.fetchall()
        return [row[0] for row in results]
    finally:
        cursor.close()

def process_temp_responses_incrementally(conn, posts_to_find, batch_size=500):
    """Process temp response files incrementally to avoid loading all into memory.
    
    Args:
        conn: Database connection
        posts_to_find: Set of post IDs we need to process
        batch_size: How many posts to batch before inserting
    
    Returns:
        Tuple of (updated_count, skipped_count)
    """
    temp_dir = archive_paths.PAGE_CACHE  # fff: was ./temp_responses (CWD)
    if not temp_dir.exists():
        logging.warning("temp_responses directory not found")
        return 0, len(posts_to_find)
    
    updated_count = 0
    skipped_count = 0
    posts_batch = []
    remaining_posts = set(posts_to_find)
    
    # Process single post files first (e621_{post_id}_*.json)
    post_files = list(temp_dir.glob("e621_*_*.json"))
    if post_files:
        logging.info(f"Checking {len(post_files)} individual post files...")
        for post_file in post_files:
            try:
                with open(post_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    post_data = data.get("post", data)
                    post_id = post_data.get("id")
                    
                    if post_id in remaining_posts and post_data.get("relationships"):
                        posts_batch.append(post_data)
                        remaining_posts.discard(post_id)
                        
                        if len(posts_batch) >= batch_size:
                            insert_post_relationships_bulk(conn, posts_batch, commit=True)
                            updated_count += len(posts_batch)
                            posts_batch = []
            except Exception as e:
                logging.debug(f"Error loading post file {post_file}: {e}")
    
    # Process page files one at a time
    page_files = sorted(temp_dir.glob("temp_page_*.json"))
    logging.info(f"Processing {len(page_files)} page files incrementally...")
    
    for idx, page_file in enumerate(page_files, 1):
        if not remaining_posts:
            logging.info("All posts found, stopping early")
            break
            
        try:
            with open(page_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _resp = data.get("response", data)
                posts = _resp if isinstance(_resp, list) else _resp.get("posts", [])
                
                for post in posts:
                    post_id = post.get("id")
                    if post_id in remaining_posts:
                        if post.get("relationships"):
                            posts_batch.append(post)
                            remaining_posts.discard(post_id)
                        else:
                            skipped_count += 1
                            remaining_posts.discard(post_id)
                        
                        # Process batch when full
                        if len(posts_batch) >= batch_size:
                            insert_post_relationships_bulk(conn, posts_batch, commit=True)
                            updated_count += len(posts_batch)
                            logging.info(f"Processed batch - {updated_count} updated, {skipped_count} skipped, {len(remaining_posts)} remaining")
                            posts_batch = []
            
            if idx % 10 == 0:
                logging.info(f"Processed {idx}/{len(page_files)} page files - {updated_count} updated, {len(remaining_posts)} remaining")
        except Exception as e:
            logging.debug(f"Error loading page file {page_file}: {e}")
    
    # Process final batch
    if posts_batch:
        insert_post_relationships_bulk(conn, posts_batch, commit=True)
        updated_count += len(posts_batch)
    
    # Count remaining posts as skipped
    skipped_count += len(remaining_posts)
    
    return updated_count, skipped_count

def backfill_relationships(config, max_posts=None, batch_size=500):
    """Backfill relationships for posts from cached API responses.
    
    Processes temp response files incrementally to avoid loading all into memory.
    """
    mysql_config = config.get('mysql', {})

    conn = connect_to_mysql(mysql_config)
    if conn is None:
        logging.error("Failed to connect to database")
        sys.exit(1)

    try:
        # Get posts without relationship data
        logging.info("Fetching posts without relationship data...")
        post_ids = get_posts_without_relationships(conn, limit=max_posts)

        if not post_ids:
            logging.info("All posts already have relationship data!")
            return 0

        logging.info(f"Found {len(post_ids)} posts without relationship data")
        
        # Process incrementally without loading everything into memory
        updated_count, skipped_count = process_temp_responses_incrementally(
            conn, 
            set(post_ids), 
            batch_size=batch_size
        )
        logging.info(f"Relationship backfill complete: Updated {updated_count} posts, skipped {skipped_count}")
        return updated_count

    except Exception as e:
        logging.error(f"Error in relationship backfill: {e}")
        return 0
    finally:
        conn.close()

def main(argv=None):
    """Main function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill parent-child relationships for posts from cached API responses"
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=None,
        help="Maximum number of posts to process (default: all)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of posts to process per batch (default: 500)"
    )

    args = parser.parse_args(argv)

    log_file = setup_logging()
    logging.info("="*80)
    logging.info("Starting relationship backfill")
    logging.info(f"Log file: {log_file}")
    logging.info("="*80)

    # Load config
    config = load_config()

    # Run backfill
    updated_count = backfill_relationships(
        config,
        max_posts=args.max_posts,
        batch_size=args.batch_size
    )

    logging.info("="*80)
    logging.info(f"Backfill complete: {updated_count} posts updated")
    logging.info("="*80)

if __name__ == "__main__":
    main()
