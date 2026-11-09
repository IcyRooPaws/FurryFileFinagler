#!/usr/bin/env python3
# fff: cached API pages are read from data/cache/e621_pages (was ./temp_responses).
"""
Script to backfill pool relationships for posts from cached API responses.
This script reads pool data from temp_responses JSON files and populates
the pools and posts_pools tables.
"""

import sys
import logging
import json
from datetime import datetime

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, process_pools_bulk, get_pools_missing_metadata, upsert_pools_metadata_bulk
from fff.archive.api import fetch_pool_metadata

def setup_logging():
    """Setup logging."""
    log_dir = str(archive_paths.LOG_DIR)
    import os
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"{log_dir}/{timestamp}_backfill_pools.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    return log_file

def get_posts_without_pool_data(conn, limit=None):
    """Get posts that might have pool data but aren't in posts_pools yet."""
    cursor = conn.cursor()
    try:
        query = """
            SELECT p.id
            FROM posts p
            LEFT JOIN posts_pools pp ON p.id = pp.post_id
            WHERE pp.post_id IS NULL
            ORDER BY p.id DESC
        """

        if limit:
            query += f" LIMIT {limit}"

        cursor.execute(query)
        results = cursor.fetchall()
        return [row[0] for row in results]
    finally:
        cursor.close()

def process_temp_responses_for_pools(conn, posts_to_find, batch_size=500):
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

                    if post_id in remaining_posts:
                        pools = post_data.get("pools", [])
                        if pools and isinstance(pools, list):
                            posts_batch.append(post_data)
                            remaining_posts.discard(post_id)
                        else:
                            skipped_count += 1
                            remaining_posts.discard(post_id)

                        if len(posts_batch) >= batch_size:
                            process_pools_bulk(conn, posts_batch, commit=True)
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
                        pools = post.get("pools", [])
                        if pools and isinstance(pools, list):
                            posts_batch.append(post)
                            remaining_posts.discard(post_id)
                        else:
                            skipped_count += 1
                            remaining_posts.discard(post_id)

                        # Process batch when full
                        if len(posts_batch) >= batch_size:
                            process_pools_bulk(conn, posts_batch, commit=True)
                            updated_count += len(posts_batch)
                            logging.info(f"Processed batch - {updated_count} with pools, {skipped_count} without, {len(remaining_posts)} remaining")
                            posts_batch = []

            if idx % 10 == 0:
                logging.info(f"Processed {idx}/{len(page_files)} page files - {updated_count} updated, {len(remaining_posts)} remaining")
        except Exception as e:
            logging.debug(f"Error loading page file {page_file}: {e}")

    # Process final batch
    if posts_batch:
        process_pools_bulk(conn, posts_batch, commit=True)
        updated_count += len(posts_batch)

    # Count remaining posts as skipped
    skipped_count += len(remaining_posts)

    return updated_count, skipped_count

def backfill_pools(config, max_posts=None, batch_size=500):
    """Backfill pool relationships for posts from cached API responses.

    Processes temp response files incrementally to avoid loading all into memory.
    """
    mysql_config = config.get('mysql', {})

    conn = connect_to_mysql(mysql_config)
    if conn is None:
        logging.error("Failed to connect to database")
        sys.exit(1)

    try:
        # Get posts without pool data
        logging.info("Fetching posts without pool data...")
        post_ids = get_posts_without_pool_data(conn, limit=max_posts)

        if not post_ids:
            logging.info("All posts already have pool data checked!")
            return 0

        logging.info(f"Found {len(post_ids)} posts to check for pool data")

        # Process incrementally without loading everything into memory
        updated_count, skipped_count = process_temp_responses_for_pools(
            conn,
            set(post_ids),
            batch_size=batch_size
        )

        logging.info(f"Pool backfill complete: Updated {updated_count} posts with pool data, {skipped_count} without pools")
        return updated_count

    except Exception as e:
        logging.error(f"Error in pool backfill: {e}")
        return 0
    finally:
        conn.close()


def backfill_pool_metadata(config, batch_size=100, limit=None):
    """Fetch name/description/counts from the e621 API for pools missing that info."""
    mysql_config = config.get('mysql', {})
    e621_config = config.get('e621', {})

    conn = connect_to_mysql(mysql_config)
    if conn is None:
        logging.error("Failed to connect to database")
        return 0

    try:
        pool_ids = get_pools_missing_metadata(conn, limit=limit)
        if not pool_ids:
            logging.info("All pools already have metadata")
            return 0

        logging.info("Found %s pools missing metadata", len(pool_ids))
        total_updated = 0

        for i in range(0, len(pool_ids), batch_size):
            batch = pool_ids[i:i + batch_size]
            metadata = fetch_pool_metadata(batch, e621_config)
            if metadata:
                upsert_pools_metadata_bulk(conn, list(metadata.values()))
                total_updated += len(metadata)
            logging.info("Metadata backfill progress: %s/%s pools", min(i + batch_size, len(pool_ids)), len(pool_ids))

        logging.info("Pool metadata backfill complete: %s pools updated", total_updated)
        return total_updated
    except Exception as e:
        logging.error("Error in pool metadata backfill: %s", e)
        return 0
    finally:
        conn.close()

def main(argv=None):
    """Main function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill pool relationships for posts from cached API responses"
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
    parser.add_argument(
        "--fetch-metadata",
        action="store_true",
        help="After backfilling pool links, also fetch pool metadata (name, description, etc.) from the e621 API"
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Only fetch pool metadata for pools missing it; skip the pool-relationship backfill"
    )
    parser.add_argument(
        "--metadata-limit",
        type=int,
        default=None,
        help="Limit number of pools to fetch metadata for (default: all missing)"
    )

    args = parser.parse_args(argv)

    log_file = setup_logging()
    logging.info("="*80)
    logging.info("Starting pool backfill")
    logging.info(f"Log file: {log_file}")
    logging.info("="*80)

    # Load config
    config = load_config()

    if not args.metadata_only:
        # Run pool-relationship backfill
        updated_count = backfill_pools(
            config,
            max_posts=args.max_posts,
            batch_size=args.batch_size
        )
        logging.info("="*80)
        logging.info(f"Backfill complete: {updated_count} posts updated with pool data")
        logging.info("="*80)

    if args.fetch_metadata or args.metadata_only:
        logging.info("="*80)
        logging.info("Starting pool metadata backfill")
        logging.info("="*80)
        meta_count = backfill_pool_metadata(
            config,
            batch_size=100,
            limit=args.metadata_limit
        )
        logging.info("="*80)
        logging.info(f"Metadata backfill complete: {meta_count} pools updated")
        logging.info("="*80)

if __name__ == "__main__":
    main()
