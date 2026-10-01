#!/usr/bin/env python3
# fff: no argparse (as in the original): main(argv) scans argv for --dry-run / --skip-to-id-matching,
# fff: defaulting to sys.argv[1:]; unknown flags (including --help) are ignored and the script runs.
"""
Script to create download tracking tables and match local media files with posts database.

This script will:
1. Create three tables: dl_fav, dl_nsfw, dl_sfw to track downloaded files
2. Scan all image and video files in configured download directories and subdirectories
3. Calculate MD5 hashes for all found media files
4. Match files with posts in the database based on MD5 hash
5. Update the posts table file_path column when matches are found (with rating prioritization)
6. Insert file information into the appropriate download tracking table
7. Clean up missing or moved files from the database
8. Check image_sorting_hashes.all_files view for posts without file_path using MD5 matching
9. Run optimized ID-based matching for posts without file_path

Command Line Options:
- --dry-run: Run without making any database changes (for testing)
- --skip-to-id-matching: Skip file processing and go directly to ID-based matching phase

Matching Logic:
- Phase 1: Match files to posts using MD5 hash during file scanning
- Phase 2: After all files processed, check posts with NULL file_path against image_sorting_hashes.all_files
  view using MD5 hash to find file_path in the external database
- Phase 3: Find posts with NULL file_path and search for matching files by post ID in download tables
  using pattern matching (%-{post_id}.%)

Rating Prioritization:
- Files with "explicit", "safe", or "questionable" in their file_name are prioritized
- If an MD5 has multiple file matches, the one with rating words takes precedence
- Existing paths with rating words won't be overwritten by paths without rating words

Supported file types:
- Images: jpg, jpeg, png, gif, bmp, webp, tiff, tif, avif, heic, heif
- Videos: mp4, webm, mov, mkv, avi, m4v, wmv, flv, 3gp, ogv
"""

import os
import sys
import hashlib
import logging
import re
from pathlib import Path


from fff.archive.config import load_config, normalize_file_path
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql, connect_to_image_sorting_hashes_db


def create_download_tables(conn):
    """Create the download tracking tables if they don't exist."""
    cursor = conn.cursor()

    # Table schema for tracking downloaded files
    table_schema = """
        CREATE TABLE IF NOT EXISTS {} (
            id INT AUTO_INCREMENT PRIMARY KEY,
            md5 VARCHAR(32) UNIQUE NOT NULL,
            file_path VARCHAR(500) NOT NULL,
            file_size BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_md5 (md5),
            INDEX idx_file_path (file_path(255))
        )
    """

    # Create the three tables
    tables = ['dl_fav', 'dl_nsfw', 'dl_sfw']
    for table_name in tables:
        cursor.execute(table_schema.format(table_name))
        logging.info(f"Ensured table {table_name} exists")

    conn.commit()
    cursor.close()


def calculate_md5(file_path):
    """Calculate MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logging.error(f"Error calculating MD5 for {file_path}: {e}")
        return None


def get_download_directories(config):
    """Extract all download directories from config."""
    download_dirs = {}
    download_config = config.get('download', {})

    # Add main directories
    if 'all_star_favs' in download_config:
        download_dirs['fav'] = download_config['all_star_favs']

    if 'default' in download_config:
        download_dirs['sfw'] = download_config['default']

    # Add categorized directories
    for media_type in ['images', 'videos', 'gifs']:
        if media_type in download_config:
            for rating, path in download_config[media_type].items():
                if rating in ['explicit', 'questionable']:
                    if 'nsfw' not in download_dirs:
                        download_dirs['nsfw'] = []
                    if isinstance(download_dirs['nsfw'], str):
                        download_dirs['nsfw'] = [download_dirs['nsfw']]
                    if isinstance(download_dirs['nsfw'], list) and path not in download_dirs['nsfw']:
                        download_dirs['nsfw'].append(path)
                elif rating == 'safe':
                    if 'sfw' not in download_dirs:
                        download_dirs['sfw'] = []
                    if isinstance(download_dirs['sfw'], str):
                        download_dirs['sfw'] = [download_dirs['sfw']]
                    if isinstance(download_dirs['sfw'], list) and path not in download_dirs['sfw']:
                        download_dirs['sfw'].append(path)

    # Add extra_search_dirs (categorize as sfw by default)
    if 'extra_search_dirs' in download_config:
        extra_dirs = download_config['extra_search_dirs']
        if extra_dirs:
            if 'sfw' not in download_dirs:
                download_dirs['sfw'] = []
            if isinstance(download_dirs['sfw'], str):
                download_dirs['sfw'] = [download_dirs['sfw']]

            # Add each extra directory if not already present
            for extra_dir in extra_dirs:
                if extra_dir not in download_dirs['sfw']:
                    download_dirs['sfw'].append(extra_dir)

    # Normalize to lists
    for key in download_dirs:
        if isinstance(download_dirs[key], str):
            download_dirs[key] = [download_dirs[key]]

    return download_dirs


def is_media_file(file_path):
    """Check if file is an image or video file based on extension."""
    # Define supported extensions
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.avif', '.heic', '.heif'}
    video_extensions = {'.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v', '.wmv', '.flv', '.3gp', '.ogv'}

    # Get file extension in lowercase
    ext = os.path.splitext(file_path)[1].lower()

    return ext in image_extensions or ext in video_extensions


def scan_directory_for_files(directory):
    """Recursively scan directory for image and video files only."""
    files = []
    try:
        for root, dirs, file_names in os.walk(directory):
            for file_name in file_names:
                file_path = os.path.join(root, file_name)
                if os.path.isfile(file_path) and is_media_file(file_path):
                    # Normalize the file path to use backslashes
                    files.append(normalize_file_path(file_path))
    except Exception as e:
        logging.error(f"Error scanning directory {directory}: {e}")

    return files


def insert_file_to_table(conn, table_name, md5_hash, file_path, file_size, dry_run=False):
    """Insert file information into the specified download table."""
    if dry_run:
        return True  # Simulate successful insertion

    # Normalize file path to use backslashes
    normalized_path = normalize_file_path(file_path)

    cursor = conn.cursor()
    try:
        query = f"""
            INSERT IGNORE INTO {table_name} (md5, file_path, file_size)
            VALUES (%s, %s, %s)
        """
        cursor.execute(query, (md5_hash, normalized_path, file_size))
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Error inserting into {table_name}: {e}")
        return False
    finally:
        cursor.close()


def has_rating_in_file_name(file_path):
    """Check if file_name contains rating words (explicit, safe, questionable)."""
    file_name = os.path.basename(file_path).lower()
    rating_words = ['explicit', 'safe', 'questionable']
    return any(word in file_name for word in rating_words)


def should_update_file_path(current_path, new_path):
    """
    Determine if we should update the file path based on rating prioritization.

    Priority rules:
    1. If current path is None/empty, always update
    2. If new path has rating words and current doesn't, update
    3. If current path has rating words and new doesn't, don't update
    4. If both or neither have rating words, don't update (keep existing)
    """
    if not current_path:
        return True

    current_has_rating = has_rating_in_file_name(current_path)
    new_has_rating = has_rating_in_file_name(new_path)

    # Update only if new file has rating words and current doesn't
    return new_has_rating and not current_has_rating


def update_posts_file_path(conn, md5_hash, file_path, dry_run=False):
    """Update the posts table with the file path for matching MD5, with rating prioritization."""
    if dry_run:
        return True  # Simulate successful update

    # Normalize file path to use backslashes
    normalized_path = normalize_file_path(file_path)

    cursor = conn.cursor()
    try:
        # First check if there's already a file_path for this MD5
        cursor.execute("SELECT file_path FROM posts WHERE md5 = %s", (md5_hash,))
        result = cursor.fetchone()

        if not result:
            return False  # MD5 not found in posts

        current_path = result[0]

        # Use prioritization logic to decide if we should update
        if should_update_file_path(current_path, file_path):
            query = """
                UPDATE posts
                SET file_path = %s
                WHERE md5 = %s
            """
            cursor.execute(query, (normalized_path, md5_hash))
            return cursor.rowcount > 0
        else:
            # Don't update - current path has priority or is equivalent
            return False

    except Exception as e:
        logging.error(f"Error updating posts table for MD5 {md5_hash}: {e}")
        return False
    finally:
        cursor.close()


def get_existing_files_from_table(conn, table_name):
    """Get all existing files from a download table with their MD5 and file paths."""
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT md5, file_path FROM {table_name}")
        return {row[0]: row[1] for row in cursor.fetchall()}
    except Exception as e:
        logging.error(f"Error getting existing files from {table_name}: {e}")
        return {}
    finally:
        cursor.close()


def update_file_path_in_table(conn, table_name, md5_hash, new_file_path, dry_run=False):
    """Update the file path for an existing MD5 in a download table, with rating prioritization."""
    if dry_run:
        return True

    normalized_path = normalize_file_path(new_file_path)
    cursor = conn.cursor()
    try:
        # First get the current file path
        cursor.execute(f"SELECT file_path FROM {table_name} WHERE md5 = %s", (md5_hash,))
        result = cursor.fetchone()

        if not result:
            return False  # MD5 not found

        current_path = result[0]

        # Use prioritization logic to decide if we should update
        if should_update_file_path(current_path, new_file_path):
            query = f"UPDATE {table_name} SET file_path = %s WHERE md5 = %s"
            cursor.execute(query, (normalized_path, md5_hash))
            return cursor.rowcount > 0
        else:
            # Don't update - current path has priority
            return False

    except Exception as e:
        logging.error(f"Error updating file path in {table_name} for MD5 {md5_hash}: {e}")
        return False
    finally:
        cursor.close()


def delete_file_from_table(conn, table_name, md5_hash, dry_run=False):
    """Delete a file entry from a download table."""
    if dry_run:
        return True

    cursor = conn.cursor()
    try:
        query = f"DELETE FROM {table_name} WHERE md5 = %s"
        cursor.execute(query, (md5_hash,))
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Error deleting from {table_name} MD5 {md5_hash}: {e}")
        return False
    finally:
        cursor.close()


def cleanup_missing_files(conn, table_name, verified_md5s, found_files_by_md5, dry_run=False):
    """Clean up files that are no longer at their recorded locations."""
    stats = {'updated': 0, 'deleted': 0}

    # Get all existing files from the table
    existing_files = get_existing_files_from_table(conn, table_name)

    for md5_hash, recorded_path in existing_files.items():
        # Skip if this file was verified during scanning
        if md5_hash in verified_md5s:
            continue

        # Check if the file still exists at the recorded location
        if os.path.exists(recorded_path):
            continue

        # File is missing from recorded location
        # Check if we found this MD5 at a different location
        if md5_hash in found_files_by_md5:
            new_path = found_files_by_md5[md5_hash]
            # Use prioritization logic for cleanup updates too
            if should_update_file_path(recorded_path, new_path):
                if update_file_path_in_table(conn, table_name, md5_hash, new_path, dry_run):
                    stats['updated'] += 1
                    logging.info(f"Updated path for MD5 {md5_hash}: {recorded_path} -> {new_path}")
            # If prioritization says don't update, we don't update but also don't delete
        else:
            # File not found anywhere, delete the entry
            if delete_file_from_table(conn, table_name, md5_hash, dry_run):
                stats['deleted'] += 1
                logging.info(f"Deleted missing file entry for MD5 {md5_hash}: {recorded_path}")

    return stats


def get_existing_md5s_from_table(conn, table_name):
    """Get all existing MD5 hashes from a download table."""
    cursor = conn.cursor()
    try:
        cursor.execute(f"SELECT md5 FROM {table_name}")
        return {row[0] for row in cursor.fetchall()}
    except Exception as e:
        logging.error(f"Error getting existing MD5s from {table_name}: {e}")
        return set()
    finally:
        cursor.close()


def check_md5_in_image_sorting_hashes(image_sorting_conn, md5_hash):
    """
    Check if an MD5 hash exists in the image_sorting_hashes.all_files view.

    Returns the file_path if found, None otherwise.
    """
    if not image_sorting_conn:
        return None

    cursor = image_sorting_conn.cursor()
    try:
        # Query the all_files view using the md5_hash column
        query = "SELECT file_path FROM all_files WHERE md5_hash = %s LIMIT 1"
        cursor.execute(query, (md5_hash,))
        result = cursor.fetchone()

        if result:
            file_path = result[0]
            logging.info(f"Found file path for MD5 {md5_hash} in image_sorting_hashes: {file_path}")
            return file_path

        return None

    except Exception as e:
        logging.debug(f"Error checking image_sorting_hashes.all_files for MD5 {md5_hash}: {e}")
        return None
    finally:
        cursor.close()


def extract_post_id_from_file_name(file_path):
    """
    Extract post ID from file_name if it matches the pattern: numbers between the last - and before the .

    Example: 'some_path/image-1234567.jpg' -> '1234567'
    """
    file_name = os.path.basename(file_path)
    # Remove the extension
    name_without_ext = os.path.splitext(file_name)[0]

    # Find the last dash
    last_dash_index = name_without_ext.rfind('-')
    if last_dash_index == -1:
        return None

    # Extract everything after the last dash
    potential_id = name_without_ext[last_dash_index + 1:]

    # Check if it's only numbers
    if potential_id.isdigit():
        return int(potential_id)

    return None


def find_post_by_id(conn, post_id):
    """Find a post by its ID and return its MD5 hash."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT md5 FROM posts WHERE id = %s", (post_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        logging.error(f"Error finding post with ID {post_id}: {e}")
        return None
    finally:
        cursor.close()


def update_posts_file_path_by_id(conn, post_id, file_path, dry_run=False):
    """Update the posts table with the file path for a specific post ID, with rating prioritization."""
    if dry_run:
        return True  # Simulate successful update

    # Normalize file path to use backslashes
    normalized_path = normalize_file_path(file_path)

    cursor = conn.cursor()
    try:
        # First check if there's already a file_path for this post ID
        cursor.execute("SELECT file_path FROM posts WHERE id = %s", (post_id,))
        result = cursor.fetchone()

        if not result:
            return False  # Post ID not found

        current_path = result[0]

        # Use prioritization logic to decide if we should update
        if should_update_file_path(current_path, file_path):
            query = """
                UPDATE posts
                SET file_path = %s
                WHERE id = %s
            """
            cursor.execute(query, (normalized_path, post_id))
            return cursor.rowcount > 0
        else:
            # Don't update - current path has priority or is equivalent
            return False

    except Exception as e:
        logging.error(f"Error updating posts table for post ID {post_id}: {e}")
        return False
    finally:
        cursor.close()


def get_posts_md5s(conn):
    """Get all MD5 hashes from the posts table."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT md5 FROM posts WHERE md5 IS NOT NULL AND md5 != ''")
        return {row[0] for row in cursor.fetchall()}
    except Exception as e:
        logging.error(f"Error getting MD5s from posts table: {e}")
        return set()
    finally:
        cursor.close()


def process_files_in_directories(conn, download_dirs, image_sorting_conn=None, dry_run=False):
    """Process all files in the download directories."""
    posts_md5s = get_posts_md5s(conn)
    logging.info(f"Found {len(posts_md5s)} MD5 hashes in posts table")

    stats = {
        'total_files': 0,
        'files_processed': 0,
        'files_skipped': 0,
        'md5_calculated': 0,
        'posts_updated': 0,
        'posts_updated_from_image_sorting': 0,
        'files_inserted': {
            'dl_fav': 0,
            'dl_nsfw': 0,
            'dl_sfw': 0
        },
        'files_updated': {
            'dl_fav': 0,
            'dl_nsfw': 0,
            'dl_sfw': 0
        },
        'cleanup': {
            'dl_fav': {'updated': 0, 'deleted': 0},
            'dl_nsfw': {'updated': 0, 'deleted': 0},
            'dl_sfw': {'updated': 0, 'deleted': 0}
        }
    }

    # Track all found files by MD5 hash across all categories for cleanup
    all_found_files_by_md5 = {}

    for category, directories in download_dirs.items():
        if not directories:
            continue

        table_name = f"dl_{category}"
        existing_files = get_existing_files_from_table(conn, table_name) if not dry_run else {}
        verified_md5s = set()  # Track which existing entries we've verified

        logging.info(f"Processing {category} directories - {len(existing_files)} files already in {table_name}")

        for directory in directories:
            if not os.path.exists(directory):
                logging.warning(f"Directory does not exist: {directory}")
                continue

            logging.info(f"Scanning directory: {directory}")
            files = scan_directory_for_files(directory)
            stats['total_files'] += len(files)
            logging.info(f"Found {len(files)} files in {directory}")

            for i, file_path in enumerate(files):
                if i % 100 == 0 and i > 0:
                    logging.info(f"Processed {i}/{len(files)} files in {directory}")

                try:
                    file_size = os.path.getsize(file_path)
                    stats['files_processed'] += 1

                    # Calculate MD5 hash
                    md5_hash = calculate_md5(file_path)
                    if not md5_hash:
                        continue

                    stats['md5_calculated'] += 1

                    # Add to global found files map for cleanup
                    all_found_files_by_md5[md5_hash] = file_path

                    # Check if this file already exists in the database
                    if md5_hash in existing_files:
                        normalized_current_path = normalize_file_path(file_path)
                        current_db_path = existing_files[md5_hash]

                        if current_db_path == normalized_current_path:
                            # File already exists in DB with correct path, skip
                            verified_md5s.add(md5_hash)
                            stats['files_skipped'] += 1
                            continue
                        else:
                            # File exists but path is different, check if we should update based on rating priority
                            if should_update_file_path(current_db_path, file_path):
                                if update_file_path_in_table(conn, table_name, md5_hash, file_path, dry_run):
                                    verified_md5s.add(md5_hash)
                                    stats['files_updated'][table_name] += 1
                                    logging.info(f"Updated path for existing MD5 {md5_hash}: {current_db_path} -> {normalize_file_path(file_path)}")
                                else:
                                    verified_md5s.add(md5_hash)  # Still mark as verified even if not updated
                            else:
                                # Don't update, but mark as verified since we found the file
                                verified_md5s.add(md5_hash)
                                stats['files_skipped'] += 1
                                continue
                    else:
                        # New file, insert into appropriate download table
                        if insert_file_to_table(conn, table_name, md5_hash, file_path, file_size, dry_run):
                            stats['files_inserted'][table_name] += 1
                            verified_md5s.add(md5_hash)

                    # Check if this MD5 exists in posts table and update file_path
                    if md5_hash in posts_md5s:
                        if update_posts_file_path(conn, md5_hash, file_path, dry_run):
                            stats['posts_updated'] += 1

                    # Commit every 50 files for better performance (skip in dry run)
                    if not dry_run and stats['files_processed'] % 50 == 0:
                        conn.commit()

                except Exception as e:
                    logging.error(f"Error processing file {file_path}: {e}")
                    continue

            # Final commit for this directory (skip in dry run)
            if not dry_run:
                conn.commit()
            logging.info(f"Completed processing {directory}")

        # Clean up missing files for this category
        logging.info(f"Cleaning up missing files in {table_name}...")
        cleanup_stats = cleanup_missing_files(conn, table_name, verified_md5s, all_found_files_by_md5, dry_run)
        stats['cleanup'][table_name] = cleanup_stats

        if not dry_run:
            conn.commit()

    return stats


def match_posts_by_id(conn, dry_run=False):
    """
    Match posts with null file_path to files in download tables using post ID from file_name.

    This runs after the main processing to match any posts that couldn't be matched by MD5.
    """
    logging.info("Starting ID-based post matching for posts with null file_path...")

    stats = {
        'posts_without_file_path': 0,
        'posts_matched_by_id': 0,
        'files_checked': 0
    }

    # Get all posts without file_path
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM posts WHERE file_path IS NULL")
        posts_without_file_path = [row[0] for row in cursor.fetchall()]
        stats['posts_without_file_path'] = len(posts_without_file_path)

        logging.info(f"Found {len(posts_without_file_path)} posts without file_path")

        if not posts_without_file_path:
            logging.info("No posts without file_path found")
            return stats

        # Check each download table for matching files
        download_tables = ['dl_fav', 'dl_nsfw', 'dl_sfw']

        for post_id in posts_without_file_path:
            matched = False

            for table_name in download_tables:
                if matched:
                    break

                # Search for files containing the post ID in the file_name
                # Pattern matches files like: artist-rating-{post_id}.ext
                # But NOT files with suffixes like: artist-rating-{post_id}_1.ext or artist-rating-{post_id}-1.ext
                search_pattern = f"%-{post_id}.%"
                cursor.execute(f"SELECT file_path FROM {table_name} WHERE file_path LIKE %s", (search_pattern,))
                all_matches = cursor.fetchall()
                
                # Filter out files with unwanted suffixes like _1, _2, -1, -2, etc.
                # Only keep files where the extension comes immediately after the post ID
                matching_files = []
                for (file_path,) in all_matches:
                    file_name = os.path.basename(file_path)
                    # Check if filename ends with: -{post_id}.ext (no extra characters between ID and extension)
                    if re.search(rf'-{re.escape(str(post_id))}\.[^.]+$', file_name):
                        matching_files.append((file_path,))

                stats['files_checked'] += len(matching_files)

                if matching_files:
                    # Use the first matching file (or could implement prioritization logic here)
                    file_path = matching_files[0][0]

                    # Update the posts table
                    if update_posts_file_path_by_id(conn, post_id, file_path, dry_run):
                        stats['posts_matched_by_id'] += 1
                        matched = True
                        logging.info(f"Matched post ID {post_id} to file: {file_path}")

                        # If multiple files match, log them for information
                        if len(matching_files) > 1:
                            other_files = [f[0] for f in matching_files[1:]]
                            logging.info(f"Post ID {post_id} had multiple matches, used: {file_path}, others: {other_files}")

        if not dry_run:
            conn.commit()

    except Exception as e:
        logging.error(f"Error during ID-based matching: {e}")
    finally:
        cursor.close()

    logging.info(f"ID-based matching complete: {stats['posts_matched_by_id']} posts matched out of {stats['posts_without_file_path']} posts without file_path")
    return stats


def match_posts_from_image_sorting_hashes(conn, image_sorting_conn, dry_run=False):
    """
    Match posts with null file_path to files in image_sorting_hashes.all_files view by MD5.

    This runs to find posts that couldn't be matched by local files but exist in the
    image_sorting_hashes database.
    """
    if not image_sorting_conn:
        logging.info("No image_sorting_hashes connection available, skipping this matching phase")
        return {'posts_without_file_path': 0, 'posts_matched_from_image_sorting': 0}

    logging.info("Starting image_sorting_hashes matching for posts with null file_path...")

    stats = {
        'posts_without_file_path': 0,
        'posts_matched_from_image_sorting': 0
    }

    # Get all posts without file_path
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, md5 FROM posts WHERE file_path IS NULL AND md5 IS NOT NULL")
        posts_without_file_path = cursor.fetchall()
        stats['posts_without_file_path'] = len(posts_without_file_path)

        logging.info(f"Found {len(posts_without_file_path)} posts without file_path to check against image_sorting_hashes")

        if not posts_without_file_path:
            logging.info("No posts without file_path found")
            return stats

        # Check each post's MD5 against the image_sorting_hashes database
        for post_id, md5_hash in posts_without_file_path:
            # Check if this MD5 exists in image_sorting_hashes.all_files
            file_path = check_md5_in_image_sorting_hashes(image_sorting_conn, md5_hash)

            if file_path:
                # Update the posts table with the found file path
                if update_posts_file_path_by_id(conn, post_id, file_path, dry_run):
                    stats['posts_matched_from_image_sorting'] += 1
                    logging.info(f"Matched post ID {post_id} (MD5: {md5_hash}) to file from image_sorting_hashes: {file_path}")

        if not dry_run:
            conn.commit()

    except Exception as e:
        logging.error(f"Error during image_sorting_hashes matching: {e}")
    finally:
        cursor.close()

    logging.info(f"image_sorting_hashes matching complete: {stats['posts_matched_from_image_sorting']} posts matched out of {stats['posts_without_file_path']} posts without file_path")
    return stats


def main(argv=None):
    """Main function."""
    # fff: flags come from argv (the fff CLI passes it); defaults to sys.argv[1:] as before
    if argv is None:
        argv = sys.argv[1:]
    # Check for command line arguments
    dry_run = '--dry-run' in argv
    skip_to_id_matching = '--skip-to-id-matching' in argv

    if dry_run:
        print("DRY RUN MODE: No database changes will be made")
    if skip_to_id_matching:
        print("SKIP MODE: Skipping file processing, going directly to ID-based matching")

    config = load_config()
    setup_logging(config['log']['directory'])

    logging.info("Starting download tables creation and file matching script")
    if dry_run:
        logging.info("Running in DRY RUN mode - no database changes will be made")
    if skip_to_id_matching:
        logging.info("SKIP MODE: Skipping file processing, going directly to ID-based matching")

    try:
        # Connect to database
        conn = connect_to_mysql(config['mysql'])
        logging.info("Connected to MySQL database")

        # Connect to image_sorting_hashes database
        image_sorting_conn = None
        try:
            image_sorting_conn = connect_to_image_sorting_hashes_db(config.get('mysql', {}))
            if image_sorting_conn:
                logging.info("Connected to image_sorting_hashes database")
            else:
                logging.info("image_sorting_hashes database not available")
        except Exception as e:
            logging.info(f"Could not connect to image_sorting_hashes database: {e}")

        # Create download tracking tables (unless skipping)
        if not skip_to_id_matching:
            logging.info("Creating download tracking tables...")
            if not dry_run:
                create_download_tables(conn)
            else:
                logging.info("DRY RUN: Would create download tracking tables")

            # Get download directories from config
            download_dirs = get_download_directories(config)
            logging.info(f"Found download directories: {download_dirs}")

            if not download_dirs:
                logging.error("No download directories found in config")
                return

            # Process files in all directories
            logging.info("Starting file processing...")
            stats = process_files_in_directories(conn, download_dirs, image_sorting_conn, dry_run)
        else:
            logging.info("Skipping file processing phase...")
            # Create empty stats for skipped processing
            stats = {
                'total_files': 0,
                'files_processed': 0,
                'files_skipped': 0,
                'md5_calculated': 0,
                'posts_updated': 0,
                'posts_updated_from_image_sorting': 0,
                'files_inserted': {'dl_fav': 0, 'dl_nsfw': 0, 'dl_sfw': 0},
                'files_updated': {'dl_fav': 0, 'dl_nsfw': 0, 'dl_sfw': 0},
                'cleanup': {
                    'dl_fav': {'updated': 0, 'deleted': 0},
                    'dl_nsfw': {'updated': 0, 'deleted': 0},
                    'dl_sfw': {'updated': 0, 'deleted': 0}
                }
            }

        # Run image_sorting_hashes matching for posts without file_path
        logging.info("Starting image_sorting_hashes matching phase...")
        image_sorting_stats = match_posts_from_image_sorting_hashes(conn, image_sorting_conn, dry_run)

        # Run ID-based matching for posts without file_path
        logging.info("Starting ID-based post matching phase...")
        id_matching_stats = match_posts_by_id(conn, dry_run)

        # Print summary statistics
        if skip_to_id_matching:
            logging.info("=== MATCHING COMPLETE ===")
            logging.info(f"Posts without file_path (before image_sorting_hashes match): {image_sorting_stats['posts_without_file_path']}")
            logging.info(f"Posts matched from image_sorting_hashes: {image_sorting_stats['posts_matched_from_image_sorting']}")
            logging.info(f"Posts without file_path (before ID match): {id_matching_stats['posts_without_file_path']}")
            logging.info(f"Posts matched by ID: {id_matching_stats['posts_matched_by_id']}")
        else:
            logging.info("=== PROCESSING COMPLETE ===")
            logging.info(f"Total files found: {stats['total_files']}")
            logging.info(f"Files processed: {stats['files_processed']}")
            logging.info(f"Files skipped (already in DB): {stats['files_skipped']}")
            logging.info(f"MD5 hashes calculated: {stats['md5_calculated']}")
            logging.info(f"Posts table file_path updated (by MD5): {stats['posts_updated']}")
            logging.info(f"Posts without file_path (before image_sorting_hashes match): {image_sorting_stats['posts_without_file_path']}")
            logging.info(f"Posts matched from image_sorting_hashes: {image_sorting_stats['posts_matched_from_image_sorting']}")
            logging.info(f"Posts without file_path (before ID match): {id_matching_stats['posts_without_file_path']}")
            logging.info(f"Posts matched by ID: {id_matching_stats['posts_matched_by_id']}")
            logging.info("Files inserted into download tables:")
            for table, count in stats['files_inserted'].items():
                logging.info(f"  {table}: {count}")
            logging.info("Files updated in download tables:")
            for table, count in stats['files_updated'].items():
                logging.info(f"  {table}: {count}")
            logging.info("Cleanup operations:")
            for table, cleanup_stats in stats['cleanup'].items():
                logging.info(f"  {table}: {cleanup_stats['updated']} paths updated, {cleanup_stats['deleted']} entries deleted")

        # Close connections
        if image_sorting_conn:
            image_sorting_conn.close()
            logging.info("image_sorting_hashes connection closed")

        conn.close()
        logging.info("Database connection closed")

    except Exception as e:
        logging.error(f"Script failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
