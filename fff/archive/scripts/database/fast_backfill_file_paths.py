# fff: the unused ./logs fallback log directory is data/logs/archive.
"""
Fast File Path Backfill Script
================================
Optimized version of file path backfilling using bulk operations.

This script significantly speeds up file path updates by:
1. Bulk querying the image_sorting_hashes database using all_files view
2. Processing posts in batches instead of one-by-one
3. Reducing database round-trips with bulk updates
4. Using connection pooling for multiple database connections

Performance: Can process 10,000+ posts in minutes instead of hours.

Usage:
    python fast_backfill_file_paths.py                    # Process all posts with missing paths
    python fast_backfill_file_paths.py --batch-size 1000  # Custom batch size
    python fast_backfill_file_paths.py --verify           # Also verify existing paths
    python fast_backfill_file_paths.py --dry-run          # Preview without changes
"""

import sys
import os
import logging
import argparse
from collections import defaultdict
from pathlib import Path

from fff.archive import paths as archive_paths

from fff.archive.config import load_config, normalize_file_path
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql, connect_to_image_sorting_hashes_db


def rename_file_to_match_db(file_path, expected_file_name, dry_run=False):
    """
    Rename a file if its filename doesn't match the expected database file_name.

    Args:
        file_path: Current full path to the file
        expected_file_name: Expected filename from database (with extension)
        dry_run: If True, only log what would be done

    Returns:
        New file path if renamed, original path if no rename needed, or None if rename failed
    """
    if not file_path or not os.path.exists(file_path):
        return None

    current_filename = os.path.basename(file_path)

    # If filenames already match, no action needed
    if current_filename == expected_file_name:
        return file_path

    # Check if only the extension differs - preserve actual file extension
    current_base, current_ext = os.path.splitext(current_filename)
    expected_base, expected_ext = os.path.splitext(expected_file_name)

    # If extensions differ, use the actual file's extension
    if current_ext.lower() != expected_ext.lower():
        new_file_name = f"{expected_base}{current_ext}"
        logging.info(f"Adjusting filename to use actual extension: {expected_file_name} -> {new_file_name}")
    else:
        new_file_name = expected_file_name

    # Construct new path
    directory = os.path.dirname(file_path)
    new_file_path = os.path.join(directory, new_file_name)

    # Check for collision
    if os.path.exists(new_file_path) and new_file_path != file_path:
        logging.warning(f"Cannot rename {file_path} to {new_file_name} - target already exists")
        return file_path  # Return original path, don't fail

    if dry_run:
        logging.info(f"[DRY RUN] Would rename: {current_filename} -> {new_file_name}")
        return new_file_path  # Return what it would be

    try:
        os.rename(file_path, new_file_path)
        logging.info(f"Renamed file: {current_filename} -> {new_file_name}")
        return normalize_file_path(new_file_path)
    except Exception as e:
        logging.error(f"Failed to rename {file_path} to {new_file_name}: {e}")
        return file_path  # Return original path on error


def bulk_get_file_paths_by_md5(mysql_config, md5_list):
    """
    Get file paths for multiple MD5 hashes at once from image_sorting_hashes database.

    Returns:
        dict: {md5: file_path} for found files
    """
    if not md5_list:
        return {}

    conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not conn:
        logging.error("Could not connect to image_sorting_hashes database")
        return {}

    cursor = conn.cursor()
    results = {}

    try:
        ihd = (mysql_config or {}).get("image_hash_db", {})
        tables_to_check = ihd.get(
            "tables",
            [
                "favorites",
                "manual_sort_images",
                "manual_sort_videos",
                "sfw_image",
                "sfw_video",
                "sfw_gif",
                "nsfw_image",
                "nsfw_video",
                "nsfw_gif",
                "archive_explicit_images",
                "archive_explicit_videos",
                "archive_explicit_gifs",
                "archive_sfw_images",
                "archive_sfw_videos",
                "archive_sfw_gifs",
                "other_directories",
            ]
        )

        # Try using all_files view first (much faster if it exists)
        try:
            logging.info("Attempting to use all_files view for bulk MD5 lookup...")
            placeholders = ','.join(['%s'] * len(md5_list))
            query = f"""
                SELECT md5_hash, file_path
                FROM all_files
                WHERE md5_hash IN ({placeholders})
            """
            cursor.execute(query, tuple(md5_list))
            rows = cursor.fetchall()

            for md5_hash, file_path in rows:
                if file_path and os.path.exists(file_path):
                    results[md5_hash] = normalize_file_path(file_path)

            logging.info(f"Found {len(results)} file paths using all_files view")

        except Exception as view_error:
            logging.debug(f"all_files view not available: {view_error}")
            logging.info("Falling back to individual table queries...")

            # Fall back to checking each table
            for table in tables_to_check:
                try:
                    placeholders = ','.join(['%s'] * len(md5_list))
                    query = f"""
                        SELECT md5_hash, file_path
                        FROM {table}
                        WHERE md5_hash IN ({placeholders})
                    """
                    cursor.execute(query, tuple(md5_list))
                    rows = cursor.fetchall()

                    for md5_hash, file_path in rows:
                        if md5_hash not in results and file_path and os.path.exists(file_path):
                            results[md5_hash] = normalize_file_path(file_path)

                    logging.debug(f"Found {len(rows)} matches in {table}")

                except Exception as e:
                    logging.debug(f"Error querying {table}: {e}")
                    continue

        logging.info(f"Total file paths found: {len(results)}/{len(md5_list)}")

    except Exception as e:
        logging.error(f"Error in bulk MD5 lookup: {e}")
    finally:
        cursor.close()
        conn.close()

    return results


def bulk_get_file_paths_by_post_id(mysql_config, post_ids):
    """
    Get file paths for multiple post IDs at once from image_sorting_hashes database.

    Returns:
        dict: {post_id: file_path} for found files
    """
    if not post_ids:
        return {}

    conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not conn:
        return {}

    cursor = conn.cursor()
    results = {}

    try:
        ihd = (mysql_config or {}).get("image_hash_db", {})
        tables_to_check = ihd.get(
            "tables",
            [
                "favorites",
                "manual_sort_images",
                "manual_sort_videos",
                "sfw_image",
                "sfw_video",
                "sfw_gif",
                "nsfw_image",
                "nsfw_video",
                "nsfw_gif",
                "archive_explicit_images",
                "archive_explicit_videos",
                "archive_explicit_gifs",
                "archive_sfw_images",
                "archive_sfw_videos",
                "archive_sfw_gifs",
                "other_directories",
            ]
        )

        # Try using all_files view first
        try:
            logging.info("Attempting to use all_files view for bulk post_id lookup...")
            placeholders = ','.join(['%s'] * len(post_ids))
            query = f"""
                SELECT e621_post_id, file_path
                FROM all_files
                WHERE e621_post_id IN ({placeholders})
            """
            cursor.execute(query, tuple(post_ids))
            rows = cursor.fetchall()

            for post_id, file_path in rows:
                if post_id and file_path and os.path.exists(file_path):
                    results[post_id] = normalize_file_path(file_path)

            logging.info(f"Found {len(results)} file paths by post_id using all_files view")

        except Exception as view_error:
            logging.debug(f"all_files view not available: {view_error}")
            logging.info("Falling back to individual table queries...")

            # Fall back to checking each table
            for table in tables_to_check:
                try:
                    placeholders = ','.join(['%s'] * len(post_ids))
                    query = f"""
                        SELECT e621_post_id, file_path
                        FROM {table}
                        WHERE e621_post_id IN ({placeholders})
                    """
                    cursor.execute(query, tuple(post_ids))
                    rows = cursor.fetchall()

                    for post_id, file_path in rows:
                        if post_id and post_id not in results and file_path and os.path.exists(file_path):
                            results[post_id] = normalize_file_path(file_path)

                    logging.debug(f"Found {len(rows)} matches in {table}")

                except Exception as e:
                    logging.debug(f"Error querying {table}: {e}")
                    continue

        logging.info(f"Total file paths found by post_id: {len(results)}/{len(post_ids)}")

    except Exception as e:
        logging.error(f"Error in bulk post_id lookup: {e}")
    finally:
        cursor.close()
        conn.close()

    return results


def fast_backfill_missing_paths(conn, mysql_config, batch_size=5000, dry_run=False, include_deleted=False):
    """
    Fast bulk backfill of missing file paths.

    Args:
        conn: Database connection to e621_posts_db
        mysql_config: MySQL configuration dict
        batch_size: Number of posts to process per batch
        dry_run: If True, don't make changes
        include_deleted: If True, also backfill paths for deleted posts

    Returns:
        dict with statistics
    """
    cursor = conn.cursor()
    stats = {
        'checked': 0,
        'updated_by_post_id': 0,
        'updated_by_md5': 0,
        'not_found': 0
    }

    try:
        # Get all posts with missing file_path (including file_name for validation)
        query = """
            SELECT id, md5, file_name
            FROM posts
            WHERE (file_path IS NULL OR file_path = '')
        """
        if not include_deleted:
            query += " AND deleted = 0"
        query += " ORDER BY id"
        cursor.execute(query)
        posts = cursor.fetchall()

        if not posts:
            logging.info("No posts with missing file_path")
            return stats

        logging.info(f"Found {len(posts)} posts with missing file_path")

        # Process in batches
        for batch_start in range(0, len(posts), batch_size):
            batch = posts[batch_start:batch_start + batch_size]
            batch_post_ids = [p[0] for p in batch]
            batch_md5s = [p[1] for p in batch if p[1]]
            # Create map of post_id -> (md5, file_name) for this batch
            batch_info = {p[0]: (p[1], p[2]) for p in batch}

            logging.info(f"Processing batch {batch_start//batch_size + 1}/{(len(posts)-1)//batch_size + 1} ({len(batch)} posts)...")

            # First try by post_id (faster and more reliable)
            paths_by_id = bulk_get_file_paths_by_post_id(mysql_config, batch_post_ids)

            # Then try by MD5 for remaining posts
            remaining_post_ids = [pid for pid in batch_post_ids if pid not in paths_by_id]
            remaining_md5_map = {p[1]: p[0] for p in batch if p[0] in remaining_post_ids and p[1]}

            paths_by_md5 = {}
            if remaining_md5_map:
                md5_to_path = bulk_get_file_paths_by_md5(mysql_config, list(remaining_md5_map.keys()))
                paths_by_md5 = {remaining_md5_map[md5]: path for md5, path in md5_to_path.items()}

            # Rename files to match database file_name before preparing updates
            for pid in list(paths_by_id.keys()):
                if pid in batch_info:
                    expected_file_name = batch_info[pid][1]  # file_name from DB
                    if expected_file_name:
                        new_path = rename_file_to_match_db(paths_by_id[pid], expected_file_name, dry_run)
                        if new_path:
                            paths_by_id[pid] = new_path

            for pid in list(paths_by_md5.keys()):
                if pid in batch_info:
                    expected_file_name = batch_info[pid][1]  # file_name from DB
                    if expected_file_name:
                        new_path = rename_file_to_match_db(paths_by_md5[pid], expected_file_name, dry_run)
                        if new_path:
                            paths_by_md5[pid] = new_path

            # Prepare bulk updates
            # NOTE: file_name must be derived from the (possibly renamed) path here, not
            # re-read from batch_info - rename_file_to_match_db() may have adjusted the
            # name (e.g. to preserve the file's real extension), and file_name has to be
            # kept in sync with what's actually on disk at file_path.
            updates_by_id = [(paths_by_id[pid], os.path.basename(paths_by_id[pid]), pid) for pid in paths_by_id]
            updates_by_md5 = [(paths_by_md5[pid], os.path.basename(paths_by_md5[pid]), pid) for pid in paths_by_md5]

            if dry_run:
                logging.info(f"[DRY RUN] Would update {len(updates_by_id)} posts by post_id")
                logging.info(f"[DRY RUN] Would update {len(updates_by_md5)} posts by MD5")
                stats['updated_by_post_id'] += len(updates_by_id)
                stats['updated_by_md5'] += len(updates_by_md5)
            else:
                # Bulk update by post_id
                if updates_by_id:
                    update_cursor = conn.cursor()
                    update_cursor.executemany(
                        "UPDATE posts SET file_path = %s, file_name = %s WHERE id = %s",
                        updates_by_id
                    )
                    update_cursor.close()
                    stats['updated_by_post_id'] += len(updates_by_id)
                    logging.info(f"Updated {len(updates_by_id)} posts by post_id")

                # Bulk update by MD5
                if updates_by_md5:
                    update_cursor = conn.cursor()
                    update_cursor.executemany(
                        "UPDATE posts SET file_path = %s, file_name = %s WHERE id = %s",
                        updates_by_md5
                    )
                    update_cursor.close()
                    stats['updated_by_md5'] += len(updates_by_md5)
                    logging.info(f"Updated {len(updates_by_md5)} posts by MD5")

                conn.commit()

            stats['checked'] += len(batch)
            stats['not_found'] += len(batch) - len(updates_by_id) - len(updates_by_md5)

            logging.info(f"Batch progress: {stats['checked']}/{len(posts)} posts checked")

        logging.info(f"Backfill complete: {stats['updated_by_post_id']} by post_id, {stats['updated_by_md5']} by MD5, {stats['not_found']} not found")

    except Exception as e:
        logging.error(f"Error in fast_backfill_missing_paths: {e}")
        if not dry_run:
            conn.rollback()
    finally:
        cursor.close()

    return stats


def fast_verify_and_repair_paths(conn, mysql_config, batch_size=5000, dry_run=False, include_deleted=False):
    """
    Fast bulk verification and repair of existing file paths.

    Args:
        conn: Database connection to e621_posts_db
        mysql_config: MySQL configuration dict
        batch_size: Number of posts to process per batch
        dry_run: If True, don't make changes
        include_deleted: If True, also verify paths for deleted posts

    Returns:
        dict with statistics
    """
    cursor = conn.cursor()
    stats = {
        'checked': 0,
        'valid': 0,
        'repaired': 0,
        'cleared': 0
    }

    try:
        # Get all posts with file_path (including file_name for validation)
        query = """
            SELECT id, file_path, md5, file_name
            FROM posts
            WHERE file_path IS NOT NULL
            AND file_path != ''
        """
        if not include_deleted:
            query += " AND deleted = 0"
        query += " ORDER BY id"
        cursor.execute(query)
        posts = cursor.fetchall()

        if not posts:
            logging.info("No posts with file_path to verify")
            return stats

        logging.info(f"Verifying file paths for {len(posts)} posts...")

        # Check which files are missing or need renaming
        missing_posts = []
        for post_id, file_path, md5, file_name in posts:
            stats['checked'] += 1
            if not os.path.exists(file_path):
                missing_posts.append((post_id, file_path, md5))
            else:
                # File exists - check if filename matches database
                if file_name:
                    new_path = rename_file_to_match_db(file_path, file_name, dry_run)
                    if new_path and new_path != file_path:
                        # File was renamed, update in database
                        if not dry_run:
                            update_cursor = conn.cursor()
                            update_cursor.execute(
                                "UPDATE posts SET file_path = %s, file_name = %s WHERE id = %s",
                                (new_path, os.path.basename(new_path), post_id)
                            )
                            update_cursor.close()
                            conn.commit()
                        stats['repaired'] += 1
                        logging.info(f"Renamed file for post {post_id}: {os.path.basename(file_path)} -> {os.path.basename(new_path)}")
                    else:
                        stats['valid'] += 1
                else:
                    stats['valid'] += 1

            if stats['checked'] % 10000 == 0:
                logging.info(f"Progress: Verified {stats['checked']}/{len(posts)} posts...")

        if not missing_posts:
            logging.info(f"All {stats['valid']} file paths are valid!")
            return stats

        logging.info(f"Found {len(missing_posts)} posts with missing files, attempting repair...")

        # Process missing posts in batches
        for batch_start in range(0, len(missing_posts), batch_size):
            batch = missing_posts[batch_start:batch_start + batch_size]
            batch_post_ids = [p[0] for p in batch]
            batch_md5s = [p[2] for p in batch if p[2]]

            # Get file_name for each post in this batch
            batch_file_names = {}
            cursor.execute(f"SELECT id, file_name FROM posts WHERE id IN ({','.join(['%s']*len(batch_post_ids))})", tuple(batch_post_ids))
            batch_file_names = {row[0]: row[1] for row in cursor.fetchall() if row[1]}

            logging.info(f"Repairing batch {batch_start//batch_size + 1}/{(len(missing_posts)-1)//batch_size + 1} ({len(batch)} posts)...")

            # Try by post_id first
            paths_by_id = bulk_get_file_paths_by_post_id(mysql_config, batch_post_ids)

            # Then try by MD5
            remaining_post_ids = [pid for pid in batch_post_ids if pid not in paths_by_id]
            remaining_md5_map = {p[2]: p[0] for p in batch if p[0] in remaining_post_ids and p[2]}

            paths_by_md5 = {}
            if remaining_md5_map:
                md5_to_path = bulk_get_file_paths_by_md5(mysql_config, list(remaining_md5_map.keys()))
                paths_by_md5 = {remaining_md5_map[md5]: path for md5, path in md5_to_path.items()}

            # Rename files to match database file_name before preparing updates
            for pid in list(paths_by_id.keys()):
                if pid in batch_file_names:
                    new_path = rename_file_to_match_db(paths_by_id[pid], batch_file_names[pid], dry_run)
                    if new_path:
                        paths_by_id[pid] = new_path

            for pid in list(paths_by_md5.keys()):
                if pid in batch_file_names:
                    new_path = rename_file_to_match_db(paths_by_md5[pid], batch_file_names[pid], dry_run)
                    if new_path:
                        paths_by_md5[pid] = new_path

            # Prepare updates
            repairs = [(paths_by_id[pid], os.path.basename(paths_by_id[pid]), pid) for pid in paths_by_id] + \
                     [(paths_by_md5[pid], os.path.basename(paths_by_md5[pid]), pid) for pid in paths_by_md5]

            clears = [pid for pid in batch_post_ids if pid not in paths_by_id and pid not in paths_by_md5]

            if dry_run:
                logging.info(f"[DRY RUN] Would repair {len(repairs)} posts")
                logging.info(f"[DRY RUN] Would clear {len(clears)} posts for redownload")
                stats['repaired'] += len(repairs)
                stats['cleared'] += len(clears)
            else:
                if repairs:
                    update_cursor = conn.cursor()
                    update_cursor.executemany(
                        "UPDATE posts SET file_path = %s, file_name = %s WHERE id = %s",
                        repairs
                    )
                    update_cursor.close()
                    stats['repaired'] += len(repairs)
                    logging.info(f"Repaired {len(repairs)} file paths")

                if clears:
                    update_cursor = conn.cursor()
                    update_cursor.executemany(
                        "UPDATE posts SET file_path = NULL WHERE id = %s",
                        [(pid,) for pid in clears]
                    )
                    update_cursor.close()
                    stats['cleared'] += len(clears)
                    logging.info(f"Cleared {len(clears)} broken paths for redownload")

                conn.commit()

        logging.info(f"Verification complete: {stats['valid']} valid, {stats['repaired']} repaired, {stats['cleared']} cleared")

    except Exception as e:
        logging.error(f"Error in fast_verify_and_repair_paths: {e}")
        if not dry_run:
            conn.rollback()
    finally:
        cursor.close()

    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Fast bulk file path backfilling and verification',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--batch-size', type=int, default=5000,
                       help='Number of posts to process per batch (default: 5000)')
    parser.add_argument('--verify', action='store_true',
                       help='Also verify and repair existing file paths')
    parser.add_argument('--verify-only', action='store_true',
                       help='Only verify existing paths, skip backfilling missing ones')
    parser.add_argument('--dry-run', action='store_true',
                       help='Preview changes without making them')
    parser.add_argument('--include-deleted', action='store_true',
                       help='Also process deleted posts (by default, deleted posts are skipped)')
    args = parser.parse_args(argv)

    # Load configuration
    config = load_config()
    mysql_config = config.get("mysql", {})
    log_config = config.get("log", {})

    log_dir = log_config.get("directory", str(archive_paths.LOG_DIR))  # fff: was ./logs
    setup_logging(log_dir)

    logging.info("=" * 80)
    logging.info("FAST FILE PATH BACKFILL & VERIFICATION")
    logging.info("=" * 80)

    if args.dry_run:
        logging.info("DRY RUN MODE - No changes will be made")

    if args.include_deleted:
        logging.info("INCLUDING DELETED POSTS - Will process deleted posts as well")

    # Connect to database
    conn = connect_to_mysql(mysql_config)
    if conn is None:
        logging.error("Failed to connect to database")
        sys.exit(1)

    # Check image_sorting_hashes availability
    test_conn = connect_to_image_sorting_hashes_db(mysql_config)
    if not test_conn:
        logging.error("Cannot connect to image_sorting_hashes database - required for this operation")
        conn.close()
        sys.exit(1)
    test_conn.close()

    # Backfill missing paths
    if not args.verify_only:
        logging.info("\n" + "="*80)
        logging.info("PHASE 1: Backfilling missing file paths")
        logging.info("="*80)
        backfill_stats = fast_backfill_missing_paths(
            conn, mysql_config,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            include_deleted=args.include_deleted
        )
        logging.info(f"Backfill results: {backfill_stats}")

    # Verify existing paths
    if args.verify or args.verify_only:
        logging.info("\n" + "="*80)
        logging.info("PHASE 2: Verifying and repairing existing file paths")
        logging.info("="*80)
        verify_stats = fast_verify_and_repair_paths(
            conn, mysql_config,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            include_deleted=args.include_deleted
        )
        logging.info(f"Verification results: {verify_stats}")

    conn.close()

    if args.dry_run:
        logging.info("\n[DRY RUN] No changes were made to the database")
    else:
        logging.info("\nOperation completed successfully!")


if __name__ == "__main__":
    main()
