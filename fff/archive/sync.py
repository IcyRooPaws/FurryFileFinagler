# fff: implemented in e621 archive/e621_main.py.
# fff: run as 'fff e621 sync'. The FA sync runs in-process instead of as a subprocess.
import sys
import os
import logging
import argparse
from send2trash import send2trash
from fff.archive import paths as archive_paths
from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql, create_tables_if_not_exists, get_existing_posts_info, insert_or_update_post, process_artists_and_tags, backfill_missing_file_names, normalize_ratings, backfill_missing_file_types, insert_post_sources, backfill_missing_sources, get_deleted_posts_count, insert_or_update_posts_bulk, process_artists_and_tags_bulk, insert_post_sources_bulk, backfill_media_hashes, backfill_missing_file_paths, verify_and_repair_file_paths, backfill_timestamps_from_api, check_and_update_file_artist_names, insert_post_relationships_bulk, insert_post_relationships, process_pools_bulk, process_pools, upsert_pools_metadata_bulk, get_pools_missing_metadata, archive_unfavorited_posts
from fff.archive.api import fetch_favorites, cleanup_temp_responses, load_recent_favorites_from_temp, check_and_cleanup_stale_responses, fetch_pool_metadata
from fff.archive.scripts.database.backfill_deletion_reasons import backfill_deletion_reasons
from fff.archive.scripts.favorites.remove_ai_tagged_posts import load_ai_tags, fetch_ai_tagged_posts, log_ai_artists, process_posts as process_ai_tagged_posts
from fff.archive.scripts.favorites.remove_ai_artist_posts import load_ai_artists, fetch_posts_by_artists, process_posts as process_ai_artist_posts
from fff.archive.download import ensure_download_folder, download_missing_files, generate_media_hash
from fff.archive.file_verification import verify_and_update_files

def get_unfavorited_posts(conn, current_favorite_ids):
    """Get detailed information about posts in DB but not in current favorites."""
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


def get_deleted_posts_missing_reason(conn):
    """Count deleted posts that have a NULL or empty deletion_reason."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM posts
            WHERE deleted = 1 AND (deletion_reason IS NULL OR deletion_reason = '')
            """
        )
        result = cursor.fetchone()
        return result[0] if result else 0
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

def handle_unfavorited_posts(conn, current_favorite_ids, e621_config):
    """Handle posts that are no longer in favorites by archiving and deleting them."""
    unfavorited_posts = get_unfavorited_posts(conn, current_favorite_ids)

    if not unfavorited_posts:
        logging.info("No unfavorited posts found. All database posts are still in current favorites.")
        return

    logging.info(f"Found {len(unfavorited_posts)} posts in database that are no longer in favorites.")

    # First archive the information before deletion
    if archive_unfavorited_posts(conn, unfavorited_posts):
        logging.info("Unfavorited posts information archived to unfavorited_posts_archive table")

        # Handle file deletion for unfavorited posts
        delete_files_flag = e621_config.get("delete_unfavorited_files", False)
        deleted_files_count = 0
        failed_deletions = 0
        already_deleted_count = 0

        for post in unfavorited_posts:
            file_path = post.get('file_path')
            if file_path:
                if os.path.exists(file_path):
                    # File exists - only delete if configured to do so
                    if delete_files_flag:
                        try:
                            send2trash(file_path)
                            deleted_files_count += 1
                            logging.info(f"Deleted file for unfavorited post {post['id']}: {file_path}")
                        except Exception as e:
                            failed_deletions += 1
                            logging.error(f"Failed to delete file for post {post['id']}: {file_path} - {e}")
                else:
                    # File already deleted from disk - just note it
                    already_deleted_count += 1
                    logging.info(f"File for unfavorited post {post['id']} already deleted from disk: {file_path}")

        if deleted_files_count > 0:
            logging.info(f"Deleted {deleted_files_count} files for unfavorited posts")
        if already_deleted_count > 0:
            logging.info(f"Found {already_deleted_count} unfavorited posts with files already deleted from disk")
        if failed_deletions > 0:
            logging.warning(f"Failed to delete {failed_deletions} files")
        if not delete_files_flag and deleted_files_count == 0 and already_deleted_count == 0:
            logging.info("File deletion for unfavorited posts is disabled (delete_unfavorited_files: false)")

        # Now delete the posts from the database
        post_ids = [post['id'] for post in unfavorited_posts]
        delete_posts_from_db(conn, post_ids)
        logging.info(f"Deleted {len(post_ids)} unfavorited posts from database")
    else:
        logging.error("Failed to archive unfavorited posts information - skipping deletion for safety")

def generate_media_hash_for_post(post, existing_posts, mysql_config=None):
    """Generate media hash for a post if it has a file path."""
    post_id = post.get("id")

    # Check if we already have the file path
    if post_id in existing_posts:
        file_path = existing_posts[post_id]
        if file_path and os.path.exists(file_path):
            try:
                media_hash = generate_media_hash(file_path, mysql_config)
                if media_hash:
                    post["media_hash"] = media_hash
                    logging.debug(f"Generated media hash for post {post_id}: {media_hash}")
                return media_hash
            except Exception as e:
                logging.warning(f"Could not generate media hash for post {post_id}: {e}")

    return None


def run_external_fa_source_sync():
    """Run the external FurAffinity source sync at the end of processing.

    fff: the original ran "FA Fav Sync/sync_external_fa_sources.py" in a subprocess and
    logged its captured output; it now runs in-process with the same logging.
    """
    import contextlib
    import io

    from fff.fluffle import sync_external_fa_sources

    logging.info("Running external FA source sync")
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            sync_external_fa_sources.main()
        ok = True
    except SystemExit as e:
        ok = not e.code
    except Exception as e:
        logging.error("Failed to run external FA sync: %s", e)
        return False
    finally:
        if out.getvalue():
            logging.info("External FA sync output:\n%s", out.getvalue().strip())
        if err.getvalue():
            logging.warning("External FA sync stderr:\n%s", err.getvalue().strip())

    if not ok:
        logging.error("External FA sync failed")
        return False
    logging.info("External FA sync completed successfully")
    return True

def main(argv=None):
    parser = argparse.ArgumentParser(description='E621 Favorites Archive Manager')
    parser.add_argument('--skip-favorites', action='store_true',
                       help='Skip calling the API to fetch favorite pages')
    args = parser.parse_args(argv)

    config = load_config()
    e621_config = config.get("e621", {})
    download_config = config.get("download", {})
    mysql_config = config.get("mysql", {})
    log_config = config.get("log", {})

    log_dir = log_config.get("directory", "./data/logs")
    setup_logging(log_dir)

    # Clean old temp responses at start (older than 15 minutes)
    try:
        cleanup_temp_responses(e621_config, max_age_minutes=15)
    except Exception as e:
        logging.warning("Temp response cleanup skipped due to error: %s", e)

    # Ensure response folder exists if API response saving is enabled
    if e621_config.get("save_api_responses", False):
        response_folder = e621_config.get("api_response_folder", "fav_response")
        ensure_download_folder(response_folder)

    for key, folder_config in download_config.items():
        if isinstance(folder_config, dict):
            for subfolder in folder_config.values():
                if isinstance(subfolder, str):
                    ensure_download_folder(subfolder)
        elif isinstance(folder_config, str):
            ensure_download_folder(folder_config)

    conn = connect_to_mysql(mysql_config)
    if conn is None:
        sys.exit(1)

    create_tables_if_not_exists(conn)
    # Ensure any legacy short-form ratings are normalized before further processing.
    normalize_ratings(conn)

    # Check if ingest-only mode is enabled
    ingest_only_mode = e621_config.get("ingest_only_mode", False)
    if ingest_only_mode:
        logging.info("Ingest-only mode enabled - file path updates and deletions will be skipped")

    favorite_posts = []
    if args.skip_favorites:
        logging.info("Skipping API call to fetch favorites (--skip-favorites flag set)")
    else:
        # Check if any temp files are stale - if so, delete ALL and force fresh fetch
        stale_cleaned = check_and_cleanup_stale_responses(e621_config, max_age_minutes=15)

        # Try recent cached responses first to avoid extra API calls (unless we just cleaned up)
        if not stale_cleaned:
            cached = load_recent_favorites_from_temp(e621_config, max_age_minutes=15)
            if cached:
                favorite_posts = cached
                logging.info("Using cached favorites from temp responses (<=15 minutes old)")

        # Fetch fresh if no valid cache
        if not favorite_posts:
            try:
                favorite_posts = fetch_favorites(e621_config)
            except Exception as e:
                logging.error("Error fetching favorites: %s", e)
                sys.exit(1)

    # Reverse to process older posts first.
    favorite_posts = list(reversed(favorite_posts))
    existing_posts = get_existing_posts_info(conn)
    logging.info("Existing posts in DB: %s", len(existing_posts))

    download_files_flag = download_config.get("download_files", True)

    # Process all favorites (including deleted posts with status:any)
    # This will update metadata, deletion status, etc. for all posts
    posts_to_process = []
    skipped_files_exist = 0

    for post in favorite_posts:
        post_id = post.get("id")
        post_is_deleted = bool(post.get("flags", {}).get("deleted", False))
        if post_id in existing_posts:
            if download_files_flag:
                # When downloading files, check if file already exists
                stored_path = existing_posts[post_id]
                if stored_path and os.path.exists(stored_path) and not post_is_deleted:
                    skipped_files_exist += 1
                    continue
            # Always process existing posts to update metadata (tags, deletion status, etc.)
            # regardless of download setting
        posts_to_process.append(post)

    logging.info("Skipped %s posts with existing files", skipped_files_exist)

    logging.info("Found %s posts to process (including deleted posts).", len(posts_to_process))

    # Process posts in bulk for better performance
    batch_size = e621_config.get("bulk_processing_batch_size", 200)
    total_processed = 0

    logging.info("Using bulk processing with batch size: %s", batch_size)

    for i in range(0, len(posts_to_process), batch_size):
        batch = posts_to_process[i:i + batch_size]

        # Prepare posts for bulk processing and generate media hashes
        for post in batch:
            post["local_file_path"] = None
            # Generate media hash for existing files
            generate_media_hash_for_post(post, existing_posts, mysql_config)

        try:
            # Bulk insert posts
            insert_or_update_posts_bulk(conn, batch, commit=False)

            # Bulk process artists and tags
            process_artists_and_tags_bulk(conn, batch, commit=False)

            # Bulk insert sources
            insert_post_sources_bulk(conn, batch, commit=False)

            # Bulk process pools
            process_pools_bulk(conn, batch, commit=False)

            # Bulk insert relationships
            insert_post_relationships_bulk(conn, batch, commit=False)

            # Commit the entire batch
            conn.commit()

            total_processed += len(batch)
            logging.info("Processed batch of %s posts (%s/%s total)", len(batch), total_processed, len(posts_to_process))

        except Exception as e:
            logging.error("Error processing batch starting at index %s: %s", i, e)
            # Fallback to individual processing for this batch
            logging.info("Falling back to individual processing for batch...")
            for post in batch:
                post_id = post.get("id")
                try:
                    # Generate media hash for existing files
                    generate_media_hash_for_post(post, existing_posts, mysql_config)

                    insert_or_update_post(conn, post, commit=False)
                    process_artists_and_tags(conn, post, commit=False)
                    insert_post_sources(conn, post, commit=False)
                    process_pools(conn, post, commit=False)
                    insert_post_relationships(conn, post, commit=False)
                except Exception as individual_error:
                    logging.error("Error processing post %s: %s", post_id, individual_error)
            conn.commit()
            total_processed += len(batch)
            logging.info("Completed fallback processing for batch (%s/%s total)", total_processed, len(posts_to_process))

    # Note: Deletion status is now handled automatically during post processing
    # since we include deleted posts in favorites with status:any parameter

    # Fetch and store pool metadata for all pools touched in this run
    all_ingested_pool_ids = set()
    for post in posts_to_process:
        all_ingested_pool_ids.update(post.get("pools", []))
    if all_ingested_pool_ids:
        try:
            logging.info("Fetching metadata for %s pools...", len(all_ingested_pool_ids))
            pool_metadata = fetch_pool_metadata(list(all_ingested_pool_ids), e621_config)
            if pool_metadata:
                upsert_pools_metadata_bulk(conn, list(pool_metadata.values()))
                logging.info("Updated metadata for %s pools", len(pool_metadata))
        except Exception as e:
            logging.error("Pool metadata fetch failed: %s", e)

    # Backfill timestamps for posts that have NULL values (using cached API responses)
    if not args.skip_favorites:
        try:
            logging.info("Backfilling missing timestamps from API responses...")
            backfilled_timestamps = backfill_timestamps_from_api(conn, e621_config)
            if backfilled_timestamps > 0:
                logging.info(f"Backfilled timestamps for {backfilled_timestamps} posts")
        except Exception as e:
            logging.error("Timestamp backfill failed: %s", e)
    else:
        logging.info("Timestamp backfill skipped (--skip-favorites flag set)")

    # Backfill file_name for any legacy rows missing it (after artists linked)
    backfill_missing_file_names(conn)
    backfill_missing_file_types(conn)

    # Backfill missing file paths from image_sorting_hashes database *before* any downloads
    # Skip in ingest-only mode
    if not ingest_only_mode:
        try:
            logging.info("Checking image_sorting_hashes database for missing file paths before downloading...")
            backfilled_paths = backfill_missing_file_paths(conn, mysql_config)
            if backfilled_paths > 0:
                logging.info(f"Backfilled file paths for {backfilled_paths} posts from image_sorting_hashes database")
        except Exception as e:
            logging.error("File path backfill from image_sorting_hashes failed: %s", e)

        # Verify existing file paths and repair broken ones using image_sorting_hashes
        try:
            logging.info("Verifying file paths and repairing broken paths before downloading missing files...")
            repair_stats = verify_and_repair_file_paths(conn, mysql_config)
            if repair_stats['repaired'] > 0:
                logging.info(f"Repaired {repair_stats['repaired']} broken file paths from image_sorting_hashes database")
            if repair_stats['missing'] > 0:
                if repair_stats['repaired'] > 0 and repair_stats['not_found'] > 0:
                    logging.warning(f"Found {repair_stats['missing']} posts with missing files ({repair_stats['repaired']} repaired, {repair_stats['not_found']} marked for redownload)")
                elif repair_stats['not_found'] > 0:
                    logging.warning(f"Found {repair_stats['missing']} posts with missing files ({repair_stats['not_found']} marked for redownload)")
                else:
                    logging.info(f"Found {repair_stats['missing']} posts with missing files (all repaired)")
        except Exception as e:
            logging.error("File path verification and repair failed: %s", e)
    else:
        logging.info("Skipping file path backfill and repair (ingest-only mode)")

    # Only verify / download missing files if enabled, *after* we've tried to reuse existing files
    if download_files_flag:
        verify_and_update_files(conn, download_config, e621_config)

    # Backfill media hashes for existing files that don't have them yet
    try:
        media_hash_config = config.get("media_hasher", {})
        if media_hash_config.get("backfill_enabled", True):
            batch_size = media_hash_config.get("backfill_batch_size", 100)
            backfilled_count = backfill_media_hashes(conn, batch_size, mysql_config)
            if backfilled_count > 0:
                logging.info(f"Backfilled media hashes for {backfilled_count} files")
    except Exception as e:
        logging.error("Media hash backfill failed: %s", e)

    # Backfill missing source links (only for posts without any sources stored yet) - done last
    try:
        backfill_missing_sources(conn, e621_config)
    except Exception as e:
        logging.error("Source links backfill failed: %s", e)

    # Log deleted posts statistics
    deleted_count = get_deleted_posts_count(conn)
    if deleted_count > 0:
        logging.info("Total deleted posts in database: %s", deleted_count)

    # Backfill deletion reasons for deleted posts missing them
    missing_reasons = get_deleted_posts_missing_reason(conn)
    if missing_reasons > 0:
        logging.info("Found %s deleted posts missing deletion_reason; running backfill...", missing_reasons)
        try:
            backfill_deletion_reasons(config, max_posts=missing_reasons)
        except Exception as e:
            logging.error("Deletion reason backfill failed: %s", e)

    # Check for posts that are no longer in favorites (if enabled)
    # Skip if ingest_only_mode is enabled or if API fetch was skipped
    if ingest_only_mode:
        logging.info("Skipping unfavorited posts check and deletion (ingest-only mode)")
    elif e621_config.get("check_unfavorited_posts", True) and not args.skip_favorites:
        try:
            current_favorite_ids = {post.get("id") for post in favorite_posts if post.get("id")}
            handle_unfavorited_posts(conn, current_favorite_ids, e621_config)
        except Exception as e:
            logging.error("Failed to handle unfavorited posts: %s", e)
    elif args.skip_favorites:
        logging.info("Unfavorited posts check skipped (--skip-favorites flag set)")
    else:
        logging.info("Unfavorited posts check is disabled in configuration")

    # Download missing files (after all other processes)
    if download_config.get("download_missing_files", False):
        try:
            logging.info("Starting download of missing files...")
            download_missing_files(conn, download_config, e621_config, mysql_config)
        except Exception as e:
            logging.error("Failed to download missing files: %s", e)
    else:
        logging.info("Missing files download is disabled in configuration")

    # Update artist names in file names (if enabled)
    if e621_config.get("update_artist_filenames", False):
        try:
            logging.info("Checking for files that need artist name updates...")
            artist_update_stats = check_and_update_file_artist_names(conn, batch_limit=None, dry_run=False)
            if artist_update_stats["renamed"] > 0:
                logging.info(f"Updated {artist_update_stats['renamed']} file names with correct artist names")
        except Exception as e:
            logging.error("Failed to update artist file names: %s", e)
    else:
        logging.info("Artist filename updates are disabled in configuration")

    # Remove posts with AI tags (unfav, move file, downvote, log artist, delete from DB)
    try:
        ai_tags_path = str(archive_paths.AI_TAGS)
        ai_tags = load_ai_tags(ai_tags_path)
        if ai_tags:
            logging.info("Running AI tag removal (%d tag(s))...", len(ai_tags))
            ai_tagged_posts = fetch_ai_tagged_posts(conn, ai_tags)
            if ai_tagged_posts:
                ai_artist_log = str(archive_paths.AI_ARTIST_MD)
                log_ai_artists(conn, ai_tagged_posts, ai_artist_log)
                removed, failed = process_ai_tagged_posts(conn, e621_config, ai_tagged_posts)
                logging.info("AI tag removal complete - removed: %d, failed: %d", removed, failed)
            else:
                logging.info("No posts found matching AI tags")
        else:
            logging.info("No AI tags configured, skipping AI tag removal")
    except Exception as e:
        logging.error("AI tag removal failed: %s", e)

    # Remove posts from known AI artists (unfav, move file, downvote, delete from DB)
    try:
        ai_artist_path = str(archive_paths.AI_ARTIST_MD)
        ai_artists = load_ai_artists(ai_artist_path)
        if ai_artists:
            logging.info("Running AI artist removal (%d artist(s))...", len(ai_artists))
            ai_artist_posts = fetch_posts_by_artists(conn, ai_artists)
            if ai_artist_posts:
                removed, failed = process_ai_artist_posts(conn, e621_config, ai_artist_posts)
                logging.info("AI artist removal complete - removed: %d, failed: %d", removed, failed)
            else:
                logging.info("No posts found for known AI artists")
        else:
            logging.info("No AI artists listed, skipping AI artist removal")
    except Exception as e:
        logging.error("AI artist removal failed: %s", e)

    conn.close()

    # Run external FA source sync as the final step.
    run_external_fa_source_sync()

    logging.info("Processing complete.")
    sys.exit(0)

if __name__ == "__main__":
    main()
