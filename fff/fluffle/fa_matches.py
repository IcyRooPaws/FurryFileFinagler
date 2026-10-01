# The FurAffinity client is shared with other entry points.
# File operations are logged to file_operations via fff.fluffle.ops.
"""
FurAffinity Post Processor
Processes images that have FurAffinity matches, adds to favorites, downloads with proper naming,
preserves timestamps, and moves original images to source_found directory.

Requires Firefox cookies for authentication. Run as 'fff fluffle process-fa'.
"""

import re
import sys
import time
import http.cookiejar
from pathlib import Path
from typing import List, Dict, Any, Optional

import yaml
import mysql.connector
from mysql.connector import Error

from fff.clients.furaffinity import (  # noqa: F401  (re-exported for callers of the original module)
    FA_RETRYABLE_STATUS_CODES,
    FA_MAX_RETRIES,
    FA_RETRY_BASE_DELAY,
    FASubmissionNotFoundError,
    _fa_get_with_retry,
    get_firefox_cookies,
    get_fa_post_data,
    add_to_fa_favorites,
    check_if_fa_favorited,
    download_fa_post,
)
from fff.common import config as fff_config
from fff.common.naming import generate_fa_filename  # noqa: F401
from fff.common.timestamps import copy_file_times as _copy_file_times
from fff.fluffle import ops


def load_config(config_path=None):
    """fff: the reverse search config view (database = image_search, or test_image_search in test).
    An explicit path loads that YAML the way the original did."""
    if config_path is None:
        return fff_config.legacy_view("fluffle")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def copy_file_times(source: Path, destination: Path):
    """Copy file creation and modification times from source to destination (atime kept)."""
    _copy_file_times(source, destination, keep_source_atime=True)


def move_to_source_found(image_path: Path, source_found_dir: Path,
                         base_search_dir: Path = None, preserve_structure: bool = False) -> bool:
    """Move the original image to source_found directory (logged)."""
    return ops.move_to_directory(image_path, source_found_dir, base_search_dir, preserve_structure,
                                 reason="fa_source_found", error_label="Error moving file")


def move_to_not_found(image_path: Path, not_found_dir: Path,
                      base_search_dir: Path = None, preserve_structure: bool = False) -> bool:
    """Move image to not_found directory (logged)."""
    return ops.move_to_directory(image_path, not_found_dir, base_search_dir, preserve_structure,
                                 reason="fa_not_found", error_label="Error moving file")


def connect_database(db_config: Dict[str, Any]) -> Optional[mysql.connector.connection.MySQLConnection]:
    """Connect to the database."""
    try:
        connection = mysql.connector.connect(
            host=db_config.get('host', 'localhost'),
            user=db_config.get('user'),
            password=db_config.get('password'),
            database=db_config.get('database')
        )
        return connection
    except Error as e:
        print(f"Error connecting to database: {e}")
        return None


def check_has_e621_match(image_id: int, connection: mysql.connector.connection.MySQLConnection) -> bool:
    """Check if an image has an e621 exact match."""
    try:
        cursor = connection.cursor()
        query = """
            SELECT COUNT(*) FROM e621_links
            WHERE image_id = %s AND match_quality = 'exact'
        """
        cursor.execute(query, (image_id,))
        count = cursor.fetchone()[0]
        cursor.close()
        return count > 0
    except Error as e:
        print(f"    Error checking e621 matches: {e}")
        return False


def process_single_fa_image(image_path: Path, post_id: str, config: Dict[str, Any],
                            download_dir: Path = None, source_found_dir: Path = None,
                            cookies: http.cookiejar.CookieJar = None, image_id: int = None,
                            connection: mysql.connector.connection.MySQLConnection = None,
                            base_search_dir: Path = None, preserve_structure: bool = False,
                            fluffle_match_data: Dict[str, Any] = None):
    """
    Process a single image with a FurAffinity match.
    If the image has an e621 match, only favorite it but don't download.

    Returns:
        - True if successfully processed
        - False if failed
        - 'deleted' if post was deleted and no rename occurred
        - ('deleted', new_path) if post was deleted and file was renamed

    Args:
        image_path: Path to the original image file
        post_id: FurAffinity post ID
        config: Configuration dictionary
        download_dir: Optional download directory
        source_found_dir: Optional directory for processed existing code
        cookies: Firefox cookies for FA authentication
        image_id: Database image ID
        connection: Database connection
        base_search_dir: Base search directory (for preserving structure)
        preserve_structure: If True, preserve subdirectory structure
        fluffle_match_data: Optional reverse search match data to use as fallback when post is deleted
    """

    fa_config = config.get('furaffinity', {})
    search_config = config.get('search', {})

    # Get directories from config if not provided
    if download_dir is None:
        download_dir = Path(fa_config.get('download_directory', './fa_downloads'))
    if source_found_dir is None:
        source_found_dir = Path(fa_config.get('source_found_directory', './source_found'))
    if base_search_dir is None:
        # Support both old (image_directory) and new (image_directories) config formats
        image_directories = search_config.get('image_directories')
        if image_directories is None:
            base_search_dir = Path(search_config.get('image_directory', ''))
        else:
            # Use first directory as base for structure preservation
            base_search_dir = Path(image_directories[0] if isinstance(image_directories, list) else image_directories)
    if not preserve_structure:
        preserve_structure = search_config.get('preserve_directory_structure', False)

    # Get replace_with_source option
    replace_with_source = search_config.get('replace_with_source', False)

    # Create directories
    download_dir.mkdir(exist_ok=True)
    source_found_dir.mkdir(exist_ok=True)

    request_delay = 2.0  # FA is stricter, use 2 second delay

    if not cookies:
        print(f"    Error: No Firefox cookies available")
        return False

    # Check if file exists
    if not image_path.exists():
        print(f"    Warning: File not found, skipping FA processing")
        return False

    # Extract numeric post ID
    numeric_id = post_id.replace('furAffinity_', '').replace('fa_', '')

    # Check if favorited
    print(f"    Checking FA favorite status...", end=" ", flush=True)
    is_fav = check_if_fa_favorited(numeric_id, cookies)

    if is_fav:
        print("[OK] Already favorited")
    else:
        print("[ERROR] Not favorited")
        print(f"    Adding to FA favorites...", end=" ", flush=True)
        if add_to_fa_favorites(numeric_id, cookies):
            print("[OK] Successfully added to favorites")
            time.sleep(request_delay)
        else:
            print("[ERROR] Failed to add to favorites")

    time.sleep(request_delay)

    # Check if image has e621 match - if so, skip download
    has_e621 = False
    if image_id and connection:
        has_e621 = check_has_e621_match(image_id, connection)
        if has_e621:
            print(f"    [i] Image has e621 match - skipping FA download")
            print(f"    [OK] FA favorite complete (no download needed)")
            return True

    # Get post data
    print(f"    Fetching FA post data...", end=" ", flush=True)
    post_data = get_fa_post_data(numeric_id, cookies)

    if not post_data:
        print("[ERROR] Failed to fetch post data")
        return False

    # Check if post was not found or deleted
    if post_data.get('not_found'):
        print(f"[WARN] Post deleted/not found")

        author_name = 'unknown'
        rating = 'Adult'  # Default to Adult for safety

        # If we have reverse search match data, use it to rename the file
        if fluffle_match_data:
            print(f"    [i] Using reverse search data to rename file with metadata")
            try:
                # Extract info from reverse search match data with robust fallback logic
                authors = fluffle_match_data.get('authors', [])
                
                if authors:
                    author_entry = authors[0] if isinstance(authors, list) else authors
                    
                    if isinstance(author_entry, dict):
                        # Try multiple fields for author name: name > handle > id
                        author_name = (author_entry.get('name') or 
                                      author_entry.get('handle') or 
                                      author_entry.get('id') or 
                                      'unknown')
                    elif isinstance(author_entry, str):
                        author_name = author_entry

                # Get rating based on isSfw flag
                is_sfw = fluffle_match_data.get('isSfw', False)
                rating = 'General' if is_sfw else 'Adult'

            except Exception as e:
                print(f"    [WARN] Error extracting reverse search data: {e}")
        else:
            # No reverse search data - try to extract artist from filename
            print(f"    [i] No reverse search data - attempting to extract artist from filename")
            try:
                # Filename format is often: timestamp.artist_title.ext
                # Example: 1302921337.doodlies_hot_lucario_on_gabumon_action.jpg
                filename = image_path.stem  # Get filename without extension
                
                # Split by first dot to remove timestamp
                parts = filename.split('.', 1)
                if len(parts) > 1:
                    # Get the part after timestamp
                    after_timestamp = parts[1]
                    # Split by underscore and take first part as artist
                    artist_parts = after_timestamp.split('_')
                    if artist_parts:
                        potential_artist = artist_parts[0]
                        # Only use if it looks reasonable (has letters, not too long)
                        if potential_artist and len(potential_artist) <= 50 and any(c.isalpha() for c in potential_artist):
                            author_name = potential_artist
                            print(f"    [i] Extracted artist from filename: {author_name}")
            except Exception as e:
                print(f"    [WARN] Could not extract artist from filename: {e}")

        # Sanitize author name
        safe_author = author_name.replace('/', '_').replace('\\', '_')
        safe_author = re.sub(r'[<>:"|?*]', '_', safe_author).strip()
        
        # If sanitization resulted in empty string, use unknown
        if not safe_author:
            safe_author = 'unknown'

        # Rename file with metadata
        try:
            # Get file extension
            ext = image_path.suffix

            # Create new filename: {artist}-{rating}-unk-FA-{postid}{ext}
            # The -unk- indicates unknown/deleted source
            new_filename = f"{safe_author}-{rating}-unk-FA-{post_id}{ext}"

            # Rename the current file in place (so it gets moved to deleted_source with proper name)
            new_path = image_path.parent / new_filename

            # Handle duplicate names
            counter = 1
            original_new_path = new_path
            while new_path.exists() and new_path != image_path:
                stem = original_new_path.stem
                suffix = original_new_path.suffix
                new_path = original_new_path.parent / f"{stem}_{counter}{suffix}"
                counter += 1

            # Rename file in place
            if new_path != image_path:
                ops.rename(image_path, new_path, "fa_deleted_renamed")
                print(f"    [OK] Renamed to: {new_path.name}")
            else:
                print(f"    [OK] File already has correct name")
            
            # Copy to download directory for record keeping (always do this for deleted posts)
            download_dir.mkdir(exist_ok=True)
            download_copy = download_dir / new_filename
            
            # Handle duplicates in download dir
            counter = 1
            original_download_copy = download_copy
            while download_copy.exists():
                stem = original_download_copy.stem
                suffix = original_download_copy.suffix
                download_copy = original_download_copy.parent / f"{stem}_{counter}{suffix}"
                counter += 1
            
            ops.copy(new_path, download_copy, "fa_deleted_record_copy")
            copy_file_times(new_path, download_copy)
            print(f"    [OK] Copied to download dir: {download_copy.name}")
            
            # Return tuple with new path
            print(f"    [i] Source was deleted - returning renamed path")
            return ('deleted', new_path)

        except Exception as e:
            print(f"    [WARN] Could not rename file: {e}")
            print(f"    [i] Source was deleted - treating as not found")
            return 'deleted'

    # Check if post has an error (like no download link found)
    if post_data.get('error'):
        print(f"[ERROR] {post_data.get('error')}")
        return False

    print("[OK]")
    time.sleep(request_delay)

    # Download post
    print(f"    Downloading from FurAffinity...", end=" ", flush=True)
    downloaded_path = download_fa_post(post_data, download_dir, cookies,
                                       image_path, base_search_dir, preserve_structure)

    if not downloaded_path:
        print("[ERROR]")
        return False

    print(f"[OK] -> {downloaded_path.name}")

    # Copy file times
    copy_file_times(image_path, downloaded_path)

    # Save the original path before moving
    original_path = image_path

    # Move original to source_found
    print(f"    Moving original to source_found...", end=" ")
    if move_to_source_found(image_path, source_found_dir, base_search_dir, preserve_structure):
        print("[OK]")

        # If replace_with_source is enabled, copy downloaded file to original location
        if replace_with_source:
            print(f"    Copying downloaded file to original location...", end=" ", flush=True)
            try:
                ops.copy(downloaded_path, original_path, "replace_with_source")
                print("[OK]")
            except Exception as e:
                print(f"[ERROR] ({e})")

        return True
    else:
        print("[ERROR]")
        return False


def get_images_with_fa_matches(connection: mysql.connector.connection.MySQLConnection) -> List[Dict[str, Any]]:
    """Get all images that have FurAffinity matches but NO e621 matches."""
    try:
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT
                i.id as image_id,
                i.filename,
                i.filepath,
                f.id as link_id,
                f.post_id,
                f.post_url,
                f.distance,
                f.author_name
            FROM images i
            JOIN furaffinity_links f ON i.id = f.image_id
            WHERE f.match_quality = 'exact'
              AND NOT EXISTS (
                SELECT 1 FROM e621_links e
                WHERE e.image_id = i.id
                  AND e.match_quality = 'exact'
              )
            ORDER BY i.id, f.distance DESC
        """

        cursor.execute(query)
        results = cursor.fetchall()
        cursor.close()

        return results
    except Error as e:
        print(f"Error querying database: {e}")
        return []


def main(argv=None):  # fff: argv accepted for the CLI; no options
    """Main function."""
    print("FurAffinity Post Processor")
    print("=" * 80)

    # Load configuration
    config = load_config()
    db_config = config.get('database', {})
    fa_config = config.get('furaffinity', {})
    search_config = config.get('search', {})

    # Get directories from config
    download_dir = Path(fa_config.get('download_directory', './fa_downloads'))
    source_found_dir = Path(fa_config.get('source_found_directory', './source_found'))
    not_found_dir = Path(search_config.get('not_found_directory', './not_found'))
    
    # Support both old (image_directory) and new (image_directories) config formats
    image_directories = search_config.get('image_directories')
    if image_directories is None:
        # Fall back to old single directory format
        base_search_dir = Path(search_config.get('image_directory', ''))
    else:
        # Use first directory as base for structure preservation
        base_search_dir = Path(image_directories[0] if isinstance(image_directories, list) else image_directories)
    
    preserve_structure = search_config.get('preserve_directory_structure', False)
    request_delay = 2.0  # FA rate limit

    # Create directories
    download_dir.mkdir(exist_ok=True)
    source_found_dir.mkdir(exist_ok=True)
    not_found_dir.mkdir(exist_ok=True)

    print(f"\nConfiguration:")
    print(f"  Base search directory: {base_search_dir}")
    print(f"  Preserve directory structure: {preserve_structure}")

    # Get Firefox cookies
    print("\nLoading Firefox cookies...")
    cookies = get_firefox_cookies()
    if not cookies:
        print("Error: Could not load Firefox cookies. Please make sure you're logged into FurAffinity in Firefox.")
        return

    print()

    # Connect to database
    print("Connecting to database...")
    connection = connect_database(db_config)
    if not connection:
        print("Failed to connect to database. Exiting.")
        return

    # Get images with FA matches
    print("Fetching images with FurAffinity matches...")
    matches = get_images_with_fa_matches(connection)

    # Don't close connection yet - we need it to check for e621 matches

    if not matches:
        print("No images with FurAffinity matches found.")
        return

    # Group by image
    images_dict = {}
    for match in matches:
        img_id = match['image_id']
        if img_id not in images_dict:
            images_dict[img_id] = {
                'filename': match['filename'],
                'filepath': match['filepath'],
                'posts': []
            }
        images_dict[img_id]['posts'].append({
            'post_id': match['post_id'],
            'post_url': match['post_url'],
            'distance': match['distance'],
            'author_name': match['author_name']
        })

    # Filter to only files that still exist (haven't been processed yet)
    remaining_images = {
        img_id: img_data
        for img_id, img_data in images_dict.items()
        if Path(img_data['filepath']).exists()
    }

    already_processed = len(images_dict) - len(remaining_images)

    print(f"Found {len(images_dict)} image(s) with {len(matches)} FurAffinity match(es)")
    if already_processed > 0:
        print(f"Already processed: {already_processed}")
        print(f"Remaining to process: {len(remaining_images)}\n")
    else:
        print()

    if not remaining_images:
        print("All images have already been processed!")
        connection.close()
        return

    print(f"Download directory: {download_dir}")
    print(f"Source found directory: {source_found_dir}")
    print(f"Not found directory: {not_found_dir}")
    print(f"API delay: {request_delay}s between requests\n")

    # Process each image
    processed = 0
    favorited = 0
    downloaded = 0
    moved_to_source = 0
    moved_to_not_found = 0

    for i, (img_id, img_data) in enumerate(remaining_images.items(), 1):
        print(f"[{i}/{len(remaining_images)}] {img_data['filename']}")

        image_path = Path(img_data['filepath'])

        # Process the best match (highest distance/confidence)
        best_match = img_data['posts'][0]
        post_id = best_match['post_id']

        # Extract numeric post ID
        numeric_id = post_id.replace('furAffinity_', '').replace('fa_', '')

        print(f"  Post ID: {numeric_id}")
        print(f"  Author: {best_match['author_name']}")

        # Check if favorited
        print(f"  Checking favorites...", end=" ", flush=True)
        is_fav = check_if_fa_favorited(numeric_id, cookies)

        if is_fav is None:
            print("[WARN] FA server error (502/5xx) - skipping file")
            print()
            continue
        elif is_fav:
            print("[OK] Already favorited")
        else:
            print("[ERROR] Not favorited")
            print(f"  Adding to favorites...", end=" ", flush=True)
            fav_result = add_to_fa_favorites(numeric_id, cookies)
            if fav_result is None:
                print("[WARN] FA server error (502/5xx) while adding favorite - skipping file")
                print()
                continue
            elif fav_result:
                print("[OK] Successfully added to favorites")
                favorited += 1
                time.sleep(request_delay)
            else:
                print("[ERROR] Failed to add to favorites")

        time.sleep(request_delay)

        # Check if image has e621 match
        has_e621 = check_has_e621_match(img_id, connection)

        if has_e621:
            print(f"  [i] Image has e621 match - skipping FA download")
            processed += 1
            print()
            continue

        # Get post data
        print(f"  Fetching post data...", end=" ", flush=True)
        post_data = get_fa_post_data(numeric_id, cookies)

        if not post_data:
            print("[ERROR] Failed")
            continue

        # Check if post not found in FA database
        if post_data.get('not_found'):
            reason = post_data.get('reason', 'not_in_database')
            if reason == 'account_disabled':
                print("[ERROR] Account disabled")
            else:
                print("[ERROR] Post not found in FA database")

            # Move to not_found folder (using configured directory)
            print(f"  Moving to not_found...", end=" ", flush=True)
            if move_to_not_found(image_path, not_found_dir, base_search_dir, preserve_structure):
                print("[OK]")
                moved_to_not_found += 1
            else:
                print("[ERROR]")

            processed += 1
            print()
            continue

        print("[OK]")
        time.sleep(request_delay)

        # Download post
        print(f"  Downloading...", end=" ", flush=True)
        downloaded_path = download_fa_post(post_data, download_dir, cookies,
                                          image_path, base_search_dir, preserve_structure)

        if not downloaded_path:
            print("[ERROR] Failed")
            continue

        print(f"[OK] -> {downloaded_path.name}")
        downloaded += 1

        # Compare file sizes
        original_size = image_path.stat().st_size
        downloaded_size = downloaded_path.stat().st_size

        if downloaded_size < original_size:
            print(f"  [i] Downloaded file is smaller ({downloaded_size:,} vs {original_size:,} bytes)")
            print(f"  Using current file with new filename...")

            # Generate the new filename for the original
            new_filename = downloaded_path.name
            new_path = download_dir / new_filename

            # Delete the smaller downloaded file
            try:
                ops.delete(downloaded_path, "fa_smaller_download_removed")
            except Exception as e:
                print(f"  [WARNING] Could not delete downloaded file: {e}")

            # Rename current file to the new name
            try:
                ops.copy(image_path, new_path, "fa_original_kept_as_source")  # copy2 preserves metadata
                print(f"  [OK] Renamed original to: {new_filename}")
                downloaded_path = new_path  # Update path for later operations
            except Exception as e:
                print(f"  [ERROR] Failed to rename original: {e}")
                continue
        else:
            # Copy file times from original to downloaded
            copy_file_times(image_path, downloaded_path)

        # Move original to source_found
        print(f"  Moving original...", end=" ", flush=True)
        if move_to_source_found(image_path, source_found_dir, base_search_dir, preserve_structure):
            print("[OK]")
            moved_to_source += 1
        else:
            print("[ERROR]")

        processed += 1
        print()

    # Close database connection
    connection.close()

    # Summary
    print("=" * 80)
    print("Summary:")
    print(f"  Images processed: {processed}")
    print(f"  Added to favorites: {favorited}")
    print(f"  Posts downloaded: {downloaded}")
    print(f"  Originals moved to source_found: {moved_to_source}")
    print(f"  Files moved to not_found: {moved_to_not_found}")
    print()


if __name__ == "__main__":
    main()
