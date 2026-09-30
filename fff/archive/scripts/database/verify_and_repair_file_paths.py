#!/usr/bin/env python3
# fff: implemented in e621 archive/scripts/database/verify_and_repair_file_paths.py.
"""
Standalone script to verify and repair broken file paths using image_sorting_hashes database.

This script checks posts in the e621_posts_db that have a file_path recorded, verifies that
the file actually exists at that location, and if not, attempts to find the file in the
image_sorting_hashes database using the MD5 hash and updates the path.

Usage:
    python verify_and_repair_file_paths.py [--batch-limit N] [--dry-run]

Options:
    --batch-limit N    Check only N posts (default: all posts)
    --dry-run         Show what would be updated without making changes
"""

import sys
import os
import logging
import argparse
from pathlib import Path


from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import (
    connect_to_mysql,
    get_file_path_from_image_sorting_hashes,
    get_file_path_from_image_sorting_hashes_by_post_id,
    resolve_existing_file_path,
)


def verify_and_repair_standalone(conn, mysql_config, batch_limit=None, dry_run=False):
    """
    Verify file paths and repair broken ones using image_sorting_hashes database.
    
    Args:
        conn: Database connection to e621_posts_db
        mysql_config: MySQL configuration dict
        batch_limit: Optional limit on number of posts to check
        dry_run: If True, don't actually update database
        
    Returns:
        Dict with statistics
    """
    cursor = conn.cursor()
    try:
        # Get posts that have a file_path recorded, including deleted posts.
        query = """
        SELECT id, file_path, md5, file_name, deleted
        FROM posts
        WHERE file_path IS NOT NULL
        AND file_path != ''
        ORDER BY id
        """
        
        if batch_limit:
            query += f" LIMIT {batch_limit}"
        
        cursor.execute(query)
        posts_with_paths = cursor.fetchall()
        
        if not posts_with_paths:
            logging.info("No posts with file_path to verify")
            return {'checked': 0, 'missing': 0, 'repaired': 0, 'not_found': 0}
        
        logging.info(f"Verifying file paths for {len(posts_with_paths)} posts...")
        if dry_run:
            logging.info("DRY RUN MODE - No database changes will be made")
        
        stats = {
            'checked': 0,
            'missing': 0,
            'repaired': 0,
            'not_found': 0
        }
        
        update_cursor = conn.cursor()
        missing_files = []
        
        for post_id, file_path, md5_hash, file_name, is_deleted in posts_with_paths:
            stats['checked'] += 1
            
            try:
                # Check if the recorded path, or the same file with a different extension, exists.
                resolved_file_path = resolve_existing_file_path(file_path)
                if resolved_file_path == file_path:
                    # File exists, no action needed
                    if stats['checked'] % 1000 == 0:
                        logging.info(f"Progress: Verified {stats['checked']}/{len(posts_with_paths)} posts...")
                    continue

                if resolved_file_path:
                    if dry_run:
                        logging.info(f"[DRY RUN] Would update post {post_id}: {file_path} -> {resolved_file_path}")
                    else:
                        update_cursor.execute(
                            "UPDATE posts SET file_path = %s WHERE id = %s",
                            (resolved_file_path, post_id)
                        )
                        logging.info(f"Updated post {post_id} to file with different extension: {resolved_file_path}")
                    stats['repaired'] += 1
                    continue
                
                # File is missing at recorded path
                stats['missing'] += 1
                missing_files.append((post_id, file_path, md5_hash, file_name))
                
                if not md5_hash or md5_hash == '':
                    if not dry_run:
                        update_cursor.execute(
                            "UPDATE posts SET file_path = NULL WHERE id = %s",
                            (post_id,)
                        )
                    logging.debug(f"Post {post_id} has no MD5 hash, cannot search for file")
                    stats['not_found'] += 1
                    continue
                
                # Try to find it in image_sorting_hashes database.
                new_file_path = get_file_path_from_image_sorting_hashes_by_post_id(mysql_config, post_id)
                if not new_file_path:
                    new_file_path = get_file_path_from_image_sorting_hashes(mysql_config, md5_hash)
                
                if new_file_path:
                    if dry_run:
                        logging.info(f"[DRY RUN] Would repair post {post_id}: {file_path} -> {new_file_path}")
                        stats['repaired'] += 1
                    else:
                        # Update the post with the new file_path
                        update_cursor.execute(
                            "UPDATE posts SET file_path = %s WHERE id = %s",
                            (new_file_path, post_id)
                        )
                        stats['repaired'] += 1
                        logging.info(f"Repaired post {post_id} (MD5: {md5_hash[:8]}...): {new_file_path}")
                else:
                    if not dry_run:
                        update_cursor.execute(
                            "UPDATE posts SET file_path = NULL WHERE id = %s",
                            (post_id,)
                        )
                    stats['not_found'] += 1
                    deleted_marker = " [DELETED]" if is_deleted else ""
                    logging.warning(f"Could not find file for post {post_id}{deleted_marker} (MD5: {md5_hash[:8]}..., old path: {file_path})")
                
                # Progress indicator every 100 posts
                if stats['checked'] % 100 == 0:
                    logging.info(f"Progress: {stats['checked']}/{len(posts_with_paths)} posts checked "
                               f"({stats['missing']} missing, {stats['repaired']} repaired, {stats['not_found']} not found)")
                
                # Commit every 100 updates for safety (only if not dry run)
                if not dry_run and stats['repaired'] % 100 == 0 and stats['repaired'] > 0:
                    conn.commit()
                    logging.info(f"Committed {stats['repaired']} repairs so far...")
                    
            except Exception as e:
                logging.error(f"Error verifying post {post_id}: {e}")
                continue
        
        # Final commit
        if not dry_run:
            conn.commit()
        
        update_cursor.close()
        
        return stats
        
    except Exception as e:
        logging.error(f"Error in verification process: {e}")
        return {'checked': 0, 'missing': 0, 'repaired': 0, 'not_found': 0}
    finally:
        cursor.close()


def main(argv=None):
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Verify and repair broken file paths using image_sorting_hashes database"
    )
    parser.add_argument(
        "--batch-limit",
        type=int,
        default=None,
        help="Check only N posts (default: all posts)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be updated without making changes"
    )
    
    args = parser.parse_args(argv)
    
    # Load config and setup logging
    config = load_config()
    setup_logging(config['log']['directory'])
    
    logging.info("Starting file path verification and repair")
    if args.dry_run:
        logging.info("Running in DRY RUN mode")
    if args.batch_limit:
        logging.info(f"Processing limit: {args.batch_limit} posts")
    
    try:
        # Connect to database
        conn = connect_to_mysql(config['mysql'])
        if not conn:
            logging.error("Failed to connect to database")
            sys.exit(1)
        
        logging.info("Connected to e621_posts_db database")
        
        # Run the verification and repair process
        stats = verify_and_repair_standalone(
            conn,
            config['mysql'],
            batch_limit=args.batch_limit,
            dry_run=args.dry_run
        )
        
        # Log results
        logging.info("=" * 60)
        logging.info("VERIFICATION AND REPAIR COMPLETE")
        logging.info("=" * 60)
        logging.info(f"Total posts checked: {stats['checked']}")
        logging.info(f"Files missing from recorded paths: {stats['missing']}")
        logging.info(f"Paths successfully repaired: {stats['repaired']}")
        logging.info(f"Files not found in image_sorting_hashes: {stats['not_found']}")
        
        if stats['missing'] > 0:
            repair_rate = (stats['repaired'] / stats['missing'] * 100) if stats['missing'] > 0 else 0
            logging.info(f"Repair success rate: {repair_rate:.1f}%")
        
        if args.dry_run:
            logging.info("No changes were made (DRY RUN mode)")
        
        conn.close()
        logging.info("Database connection closed")
        
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
