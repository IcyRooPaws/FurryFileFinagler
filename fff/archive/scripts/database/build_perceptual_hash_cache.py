#!/usr/bin/env python3
# fff: MediaHasher is the archive hash profile (fff.hashing.archive_hasher).
"""
Proactively build perceptual hash cache for all posts that have local files.

This script generates and caches perceptual hashes for all posts in your database
that already have local files, without needing to download anything from e621.
This is much faster than downloading and prepares the cache for future matching.

Usage:
    python build_perceptual_hash_cache.py [--batch-limit N] [--force-recompute]

Options:
    --batch-limit N      Process only N posts (default: all posts with files)
    --force-recompute    Recompute hashes even if already cached
    --skip-existing      Skip posts that already have cached hashes
"""

import sys
import os
import logging
import argparse
from pathlib import Path


from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql


def build_cache_from_local_files(conn, batch_limit=None, force_recompute=False, skip_existing=True):
    """
    Build perceptual hash cache using existing local files.

    Args:
        conn: Database connection
        batch_limit: Optional limit on posts to process
        force_recompute: If True, recompute even if hash exists
        skip_existing: If True, skip posts with cached hashes (ignored if force_recompute=True)

    Returns:
        Dict with statistics
    """
    cursor = conn.cursor()
    try:
        # Build query to get posts with local files
        if skip_existing and not force_recompute:
            # Only get posts without cached hashes
            query = """
            SELECT p.id, p.file_path, p.file_url, p.file_type
            FROM posts p
            LEFT JOIN e621_perceptual_hashes h ON p.id = h.post_id
            WHERE p.file_path IS NOT NULL
            AND p.file_path != ''
            AND p.deleted = 0
            AND p.file_type IN ('image', 'gif')
            AND h.post_id IS NULL
            ORDER BY p.id
            """
        else:
            # Get all posts with files
            query = """
            SELECT id, file_path, file_url, file_type
            FROM posts
            WHERE file_path IS NOT NULL
            AND file_path != ''
            AND deleted = 0
            AND file_type IN ('image', 'gif')
            ORDER BY id
            """

        if batch_limit:
            query += f" LIMIT {batch_limit}"

        cursor.execute(query)
        posts = cursor.fetchall()

        if not posts:
            if skip_existing and not force_recompute:
                logging.info("All posts already have cached perceptual hashes!")
            else:
                logging.info("No posts with local files found")
            return {'processed': 0, 'cached': 0, 'failed': 0, 'skipped': 0}

        logging.info(f"Building perceptual hash cache for {len(posts)} posts...")
        if force_recompute:
            logging.info("Force recompute mode: Will recompute existing hashes")
        elif skip_existing:
            logging.info("Skip existing mode: Only processing posts without cached hashes")

        # Import required modules
        try:
            from fff.hashing import archive_hasher as MediaHasher  # fff: archive hash profile
        except ImportError:
            logging.error("MediaHasher not available. Install with: pip install Pillow imagehash")
            return {'processed': 0, 'cached': 0, 'failed': 0, 'skipped': 0}

        stats = {
            'processed': 0,
            'cached': 0,
            'failed': 0,
            'skipped': 0
        }

        hasher = MediaHasher()
        insert_cursor = conn.cursor()

        for post_id, file_path, file_url, file_type in posts:
            stats['processed'] += 1

            try:
                # Verify file exists
                if not os.path.exists(file_path):
                    logging.warning(f"File not found for post {post_id}: {file_path}")
                    stats['failed'] += 1
                    continue

                # Check if we should skip this post
                if not force_recompute:
                    check_cursor = conn.cursor()
                    try:
                        check_cursor.execute(
                            "SELECT perceptual_hash FROM e621_perceptual_hashes WHERE post_id = %s",
                            (post_id,)
                        )
                        result = check_cursor.fetchone()
                        if result:
                            stats['skipped'] += 1
                            logging.debug(f"Post {post_id} already has cached hash, skipping")
                            continue
                    finally:
                        check_cursor.close()

                # Compute perceptual hash from local file
                logging.debug(f"Computing perceptual hash for post {post_id} from {file_path}")
                perceptual_hash = hasher.hash_file(file_path)

                if not perceptual_hash:
                    logging.warning(f"Failed to compute perceptual hash for post {post_id}")
                    stats['failed'] += 1
                    continue

                # Cache the hash with algorithm info
                try:
                    insert_cursor.execute(
                        """INSERT INTO e621_perceptual_hashes (post_id, perceptual_hash, hash_algorithm, file_url, cached_at)
                           VALUES (%s, %s, %s, %s, NOW())
                           ON DUPLICATE KEY UPDATE
                           perceptual_hash = VALUES(perceptual_hash),
                           hash_algorithm = VALUES(hash_algorithm),
                           file_url = VALUES(file_url),
                           cached_at = NOW()""",
                        (post_id, perceptual_hash, hasher.image_hash_mode, file_url or '')
                    )
                    stats['cached'] += 1
                    logging.debug(f"Cached perceptual hash for post {post_id}: {perceptual_hash}")
                except Exception as e:
                    logging.error(f"Failed to cache hash for post {post_id}: {e}")
                    stats['failed'] += 1
                    continue

                # Progress reporting and periodic commits
                if stats['processed'] % 100 == 0:
                    conn.commit()
                    logging.info(f"Progress: {stats['processed']}/{len(posts)} posts | "
                               f"Cached: {stats['cached']} | Failed: {stats['failed']} | Skipped: {stats['skipped']}")

            except Exception as e:
                logging.error(f"Error processing post {post_id}: {e}")
                stats['failed'] += 1
                continue

        # Final commit
        conn.commit()
        insert_cursor.close()

        return stats

    except Exception as e:
        logging.error(f"Error in cache building: {e}")
        return {'processed': 0, 'cached': 0, 'failed': 0, 'skipped': 0}
    finally:
        cursor.close()


def main(argv=None):
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Build perceptual hash cache from existing local files"
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        default=None,
        help="Process only N posts (default: all posts with files)"
    )
    parser.add_argument(
        "--force-recompute",
        action="store_true",
        help="Recompute hashes even if already cached"
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip posts that already have cached hashes (default: True)"
    )

    args = parser.parse_args(argv)

    # Load config and setup logging
    config = load_config()
    setup_logging(config['log']['directory'])

    logging.info("Starting perceptual hash cache building from local files")
    if args.force_recompute:
        logging.info("Force recompute mode enabled")
    if args.batch_limit:
        logging.info(f"Processing limit: {args.batch_limit} posts")

    try:
        # Connect to database
        conn = connect_to_mysql(config['mysql'])
        if not conn:
            logging.error("Failed to connect to database")
            sys.exit(1)

        logging.info("Connected to database")

        # Check for required libraries
        try:
            import imagehash
            from PIL import Image
        except ImportError as e:
            logging.error(f"Required library missing: {e}")
            logging.error("Install with: pip install Pillow imagehash")
            sys.exit(1)

        try:
            from fff.hashing import archive_hasher as MediaHasher  # fff: archive hash profile
        except ImportError:
            logging.error("media_hasher module is required")
            sys.exit(1)

        # Run the cache building process
        stats = build_cache_from_local_files(
            conn,
            batch_limit=args.batch_limit,
            force_recompute=args.force_recompute,
            skip_existing=args.skip_existing
        )

        # Log results
        logging.info("=" * 60)
        logging.info("CACHE BUILDING COMPLETE")
        logging.info("=" * 60)
        logging.info(f"Total posts processed: {stats['processed']}")
        logging.info(f"Hashes successfully cached: {stats['cached']}")
        logging.info(f"Posts skipped (already cached): {stats['skipped']}")
        logging.info(f"Failed to process: {stats['failed']}")

        if stats['processed'] > 0:
            success_rate = (stats['cached'] / stats['processed'] * 100) if stats['processed'] > 0 else 0
            logging.info(f"Success rate: {success_rate:.1f}%")

        # Show cache statistics
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM e621_perceptual_hashes")
            total_cached = cursor.fetchone()[0]
            logging.info(f"Total perceptual hashes in cache: {total_cached}")
        except Exception as e:
            logging.warning(f"Could not get cache statistics: {e}")
        finally:
            cursor.close()

        conn.close()
        logging.info("Database connection closed")

    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
