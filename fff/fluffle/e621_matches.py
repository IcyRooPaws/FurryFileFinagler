# The e621 API helpers are imimplemented in the shared client.
# File operations are logged to file_operations; blacklist and auto_favorite settings
# apply consistently to both command entry points.
"""
E621 Post Processor
Processes images that have e621 matches, checks favorites, downloads with proper naming,
preserves timestamps, and moves original images to source_found directory.

Run as 'fff fluffle process-e621'.
"""

import sys
import time
from pathlib import Path
from typing import List, Dict, Any

import yaml
import mysql.connector
from mysql.connector import Error

from fff.clients.e621 import (  # noqa: F401  (re-exported for callers of the original module)
    get_all_favorites,
    check_if_favorited,
    add_to_favorites,
    remove_from_favorites,
    get_artist_main_name,
    get_post_data,
    generate_filename,
    download_post,
    check_blacklisted_tags,
    is_deleted_without_file,
    keep_local_file_as_post,
)
from fff.common import config as fff_config
from fff.common import db
from fff.common.logging import logged_command
from fff.common.timestamps import copy_file_times as _copy_file_times
from fff.fluffle import ops


def load_config(config_path=None):
    """fff: the reverse search config view (database = image_search, or test_image_search in test).
    An explicit path loads that YAML the way the original did."""
    if config_path is None:
        return fff_config.legacy_view("fluffle")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def copy_file_times(source_path: Path, dest_path: Path):
    """Copy the modified time from source to destination file (atime = source mtime)."""
    _copy_file_times(source_path, dest_path, keep_source_atime=False)


def move_to_blacklisted(image_path: Path, blacklisted_dir: Path,
                       base_search_dir: Path = None, preserve_structure: bool = False) -> bool:
    """Move the original image to blacklisted directory (logged)."""
    return ops.move_to_directory(image_path, blacklisted_dir, base_search_dir, preserve_structure,
                                 reason="e621_blacklisted", error_label="Error moving file to blacklisted")


def move_to_source_found(image_path: Path, source_found_dir: Path,
                         base_search_dir: Path = None, preserve_structure: bool = False) -> bool:
    """Move the original image to source_found directory (logged)."""
    return ops.move_to_directory(image_path, source_found_dir, base_search_dir, preserve_structure,
                                 reason="e621_source_found", error_label="Error moving file")



def get_images_with_e621_matches(connection: mysql.connector.connection.MySQLConnection) -> List[Dict[str, Any]]:
    """Get all images that have e621 matches."""
    try:
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT
                i.id as image_id,
                i.filename,
                i.filepath,
                e.id as link_id,
                e.post_id,
                e.post_url,
                e.distance,
                e.author_name
            FROM images i
            JOIN e621_links e ON i.id = e.image_id
            WHERE e.match_quality = 'exact'
            ORDER BY i.id, e.distance DESC
        """

        cursor.execute(query)
        results = cursor.fetchall()
        cursor.close()

        return results
    except Error as e:
        print(f"Error querying database: {e}")
        return []


def process_single_image(image_path: Path, post_id: str, config: Dict[str, Any],
                        download_dir: Path = None, source_found_dir: Path = None,
                        favorites_cache: set = None, base_search_dir: Path = None,
                        preserve_structure: bool = False) -> bool:
    """
    Process a single image with an e621 match.

    Args:
        image_path: Path to the original image file
        post_id: E621 post ID
        config: Configuration dictionary
        download_dir: Optional download directory
        source_found_dir: Optional directory for processed existing code
        favorites_cache: Optional set of favorited post IDs for fast lookup
        base_search_dir: Base search directory (for preserving structure)
        preserve_structure: If True, preserve subdirectory structure
    """
    e621_config = config.get('e621', {})
    search_config = config.get('search', {})

    # Get directories from config if not provided
    if download_dir is None:
        download_dir = Path(e621_config.get('download_directory', './e621_downloads'))
    if source_found_dir is None:
        source_found_dir = Path(e621_config.get('source_found_directory', './source_found'))
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

    # Get blacklist configuration
    blacklisted_dir = Path(e621_config.get('blacklisted_directory', './blacklisted'))
    blacklist = e621_config.get('blacklist', [])

    # Get auto_favorite option
    auto_favorite = e621_config.get('auto_favorite', True)

    # Get replace_with_source option
    replace_with_source = search_config.get('replace_with_source', False)

    # Create directories
    download_dir.mkdir(exist_ok=True)
    source_found_dir.mkdir(exist_ok=True)

    user_agent = e621_config.get('user_agent', 'E621PostProcessor/1.0')
    login = e621_config.get('login')
    api_key = e621_config.get('api_key')
    request_delay = 1.0

    if not login or not api_key:
        print(f"    Error: e621 credentials not configured")
        return False

    # Check if file exists
    if not image_path.exists():
        print(f"    Warning: File not found, skipping e621 processing")
        return False

    # Extract numeric post ID
    if '_' in post_id:
        numeric_id = post_id.split('_')[-1]
    else:
        numeric_id = post_id

    # Check if favorited (using cache if available)
    print(f"    Checking e621 favorite status...", end=" ", flush=True)
    is_fav = check_if_favorited(numeric_id, login, api_key, user_agent, favorites_cache)

    if is_fav:
        print("[OK] Already favorited")
    else:
        print("[ERROR] Not favorited")
        if auto_favorite:
            print(f"    Adding to e621 favorites...", end=" ", flush=True)
            if add_to_favorites(numeric_id, login, api_key, user_agent, favorites_cache):
                print("[OK] Successfully added to favorites")
                time.sleep(request_delay)
            else:
                print("[ERROR] Failed to add to favorites")
        else:
            print(f"    Skipping favorite (auto_favorite disabled)")

    time.sleep(request_delay)

    # Get post data
    print(f"    Fetching e621 post data...", end=" ", flush=True)
    save_json = e621_config.get('save_json_responses', False)
    responses_dir = e621_config.get('responses_directory', './responses/e621')
    post_data = get_post_data(numeric_id, login, api_key, user_agent, save_json, responses_dir)

    if not post_data:
        print("[ERROR]")
        return False

    print("[OK]")

    # Check for blacklisted tags
    if blacklist:
        blacklisted_tags = check_blacklisted_tags(post_data, blacklist)
        if blacklisted_tags:
            print(f"    [WARNING] Post contains blacklisted tags: {', '.join(blacklisted_tags)}")

            # Remove from favorites if it's favorited
            if is_fav:
                print(f"    Removing from e621 favorites...", end=" ", flush=True)
                if remove_from_favorites(numeric_id, login, api_key, user_agent, favorites_cache):
                    print("[OK]")
                    time.sleep(request_delay)
                else:
                    print("[ERROR]")

            print(f"    Moving to blacklisted directory...", end=" ", flush=True)
            if move_to_blacklisted(image_path, blacklisted_dir, base_search_dir, preserve_structure):
                print("[OK]")
                return True
            else:
                print("[ERROR]")
                return False

    # Deleted on e621: there's nothing to download, so our copy becomes the post's file
    if is_deleted_without_file(post_data):
        original_path = Path(str(image_path))
        print(f"    [i] Post deleted on e621 - keeping the local file under its e621 name...", end=" ", flush=True)
        kept_path = keep_local_file_as_post(post_data, image_path, download_dir, login, api_key, user_agent,
                                            base_search_dir, preserve_structure,
                                            move=lambda src, dest: ops.move(src, dest, "e621_deleted_post_kept_local"))
        if not kept_path:
            print("[ERROR]")
            return False
        print(f"[OK] -> {kept_path.name}")
        if replace_with_source:
            print(f"    Copying renamed file back to original location...", end=" ", flush=True)
            try:
                ops.copy(kept_path, original_path, "replace_with_source")
                print("[OK]")
            except Exception as e:
                print(f"[ERROR] ({e})")
        return True

    time.sleep(request_delay)

    # Download post
    print(f"    Downloading from e621...", end=" ", flush=True)
    downloaded_path = download_post(post_data, download_dir, login, api_key, user_agent,
                                   image_path, base_search_dir, preserve_structure)

    if not downloaded_path:
        print("[ERROR]")
        return False

    print(f"[OK] -> {downloaded_path.name}")

    # Save the original path before any moves
    original_path = Path(str(image_path))

    # Compare file sizes
    original_size = image_path.stat().st_size
    downloaded_size = downloaded_path.stat().st_size

    if downloaded_size < original_size:
        print(f"    [i] Downloaded file is smaller ({downloaded_size:,} vs {original_size:,} bytes)")
        print(f"    Using current file with new filename...")

        # Generate the new filename for the original
        new_filename = downloaded_path.name
        new_path = download_dir / new_filename

        # Delete the smaller downloaded file
        try:
            ops.delete(downloaded_path, "e621_smaller_download_removed")
        except Exception as e:
            print(f"    [WARNING] Could not delete downloaded file: {e}")

        # Move (not copy) current file to download dir with new name
        try:
            ops.move(image_path, new_path, "e621_original_kept_as_source")
            print(f"    [OK] Moved original to: {new_filename}")
            downloaded_path = new_path  # Update path for later operations
            # Create a copy at original location if replace_with_source is enabled
            if replace_with_source:
                print(f"    Copying renamed file back to original location...", end=" ", flush=True)
                try:
                    ops.copy(new_path, original_path, "replace_with_source")
                    print("[OK]")
                except Exception as e:
                    print(f"[ERROR] ({e})")
            return True
        except Exception as e:
            print(f"    [ERROR] Failed to move original: {e}")
            return False
    else:
        # Copy file times from original to downloaded
        copy_file_times(image_path, downloaded_path)

        # Move original to source_found
        print(f"    Moving original to source_found...", end=" ", flush=True)
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


@logged_command("fluffle", "process_e621", "fff.fluffle.e621_matches")
def main(argv=None):  # fff: argv accepted for the CLI; no options
    """Main function."""
    print("E621 Post Processor")
    print("=" * 80)

    # Load configuration
    config = load_config()
    db_config = config.get('database', {})
    e621_config = config.get('e621', {})
    search_config = config.get('search', {})

    user_agent = e621_config.get('user_agent', 'E621PostProcessor/1.0')
    login = e621_config.get('login')
    api_key = e621_config.get('api_key')

    if not login or not api_key:
        print("Error: e621 login and API key required in config.yaml")
        return

    # Get directories from config
    download_dir = Path(e621_config.get('download_directory', './e621_downloads'))
    source_found_dir = Path(e621_config.get('source_found_directory', './source_found'))

    # Support both old (image_directory) and new (image_directories) config formats
    image_directories = search_config.get('image_directories')
    if image_directories is None:
        # Fall back to old single directory format
        base_search_dir = Path(search_config.get('image_directory', ''))
    else:
        # Use first directory as base for structure preservation
        base_search_dir = Path(image_directories[0] if isinstance(image_directories, list) else image_directories)

    preserve_structure = search_config.get('preserve_directory_structure', False)
    request_delay = 1.0  # Delay between API requests (e621 rate limit)

    # Apply the shared blacklist and auto_favorite settings.
    blacklisted_dir = Path(e621_config.get('blacklisted_directory', './blacklisted'))
    blacklist = e621_config.get('blacklist', [])
    auto_favorite = e621_config.get('auto_favorite', True)

    # Create directories
    download_dir.mkdir(exist_ok=True)
    source_found_dir.mkdir(exist_ok=True)

    print(f"\nConfiguration:")
    print(f"  Base search directory: {base_search_dir}")
    print(f"  Preserve directory structure: {preserve_structure}")

    # Connect to database
    print("\nConnecting to database...")
    connection = db.try_connect_with(db_config)
    if not connection:
        print("Failed to connect to database. Exiting.")
        return

    # Get images with e621 matches
    print("Fetching images with e621 matches...")
    matches = get_images_with_e621_matches(connection)
    connection.close()

    if not matches:
        print("No images with e621 matches found.")
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

    print(f"Found {len(images_dict)} image(s) with {len(matches)} e621 match(es)\n")

    if not images_dict:
        print("No images to process. Exiting.")
        return

    # Decide whether to fetch all favorites or check individually
    # Threshold: more than 50 files = bulk fetch, 50 or fewer = individual checks
    FAVORITES_THRESHOLD = 50
    favorites_cache = None

    if len(images_dict) > FAVORITES_THRESHOLD:
        # Bulk mode: Fetch all favorites for caching
        print(f"\nBulk mode: Fetching all favorites (more than {FAVORITES_THRESHOLD} files)")
        favorites_cache = get_all_favorites(login, api_key, user_agent)
        print()
    else:
        # Individual mode: Check each post via API as needed
        print(f"\nIndividual mode: Will check favorites per-post ({len(images_dict)} files <= {FAVORITES_THRESHOLD})")
        print()

    print(f"Download directory: {download_dir}")
    print(f"Source found directory: {source_found_dir}")
    print(f"API delay: {request_delay}s between requests\n")

    # Process each image
    processed = 0
    favorited = 0
    downloaded = 0
    moved = 0

    for i, (img_id, img_data) in enumerate(images_dict.items(), 1):
        print(f"[{i}/{len(images_dict)}] {img_data['filename']}")

        image_path = Path(img_data['filepath'])

        # Check if file exists
        if not image_path.exists():
            print(f"  [WARNING] Warning: File not found, skipping")
            continue

        # Process the best match (highest distance/confidence)
        best_match = img_data['posts'][0]
        post_id = best_match['post_id']

        # Extract numeric post ID
        if '_' in post_id:
            numeric_id = post_id.split('_')[-1]
        else:
            numeric_id = post_id

        print(f"  Post ID: {numeric_id}")
        print(f"  Author: {best_match['author_name']}")

        # Check if favorited (using cache)
        print(f"  Checking favorites...", end=" ", flush=True)
        is_fav = check_if_favorited(numeric_id, login, api_key, user_agent, favorites_cache)

        if is_fav:
            print("[OK] Already favorited")
        else:
            print("[ERROR] Not favorited")
            if auto_favorite:
                print(f"  Adding to favorites...", end=" ", flush=True)
                if add_to_favorites(numeric_id, login, api_key, user_agent, favorites_cache):
                    print("[OK] Successfully added to favorites")
                    favorited += 1
                    time.sleep(request_delay)
                else:
                    print("[ERROR] Failed to add to favorites")
            else:
                print(f"  Skipping favorite (auto_favorite disabled)")

        time.sleep(request_delay)

        # Get post data
        print(f"  Fetching post data...", end=" ", flush=True)
        save_json = e621_config.get('save_json_responses', False)
        responses_dir = e621_config.get('responses_directory', './responses/e621')
        post_data = get_post_data(numeric_id, login, api_key, user_agent, save_json, responses_dir)

        if not post_data:
            print("[ERROR] Failed")
            continue

        print("[OK]")

        # Check blacklisted tags before processing the post.
        if blacklist:
            blacklisted_tags = check_blacklisted_tags(post_data, blacklist)
            if blacklisted_tags:
                print(f"  [WARNING] Post contains blacklisted tags: {', '.join(blacklisted_tags)}")
                if is_fav:
                    print(f"  Removing from e621 favorites...", end=" ", flush=True)
                    if remove_from_favorites(numeric_id, login, api_key, user_agent, favorites_cache):
                        print("[OK]")
                        time.sleep(request_delay)
                    else:
                        print("[ERROR]")
                print(f"  Moving to blacklisted directory...", end=" ", flush=True)
                if move_to_blacklisted(image_path, blacklisted_dir, base_search_dir, preserve_structure):
                    print("[OK]")
                    processed += 1
                else:
                    print("[ERROR]")
                print()
                continue

        # Deleted on e621: there's nothing to download, so our copy becomes the post's file
        if is_deleted_without_file(post_data):
            print(f"  [i] Post deleted on e621 - keeping the local file under its e621 name...", end=" ", flush=True)
            kept_path = keep_local_file_as_post(post_data, image_path, download_dir, login, api_key, user_agent,
                                                base_search_dir, preserve_structure,
                                                move=lambda src, dest: ops.move(src, dest, "e621_deleted_post_kept_local"))
            if kept_path:
                print(f"[OK] -> {kept_path.name}")
                processed += 1
                moved += 1
            else:
                print("[ERROR] Failed")
            print()
            continue

        time.sleep(request_delay)

        # Download post
        print(f"  Downloading...", end=" ", flush=True)
        downloaded_path = download_post(post_data, download_dir, login, api_key, user_agent,
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
            print(f"  Using current file with new filename...", end=" ", flush=True)

            # Generate the new filename for the original
            new_filename = downloaded_path.name
            new_path = download_dir / new_filename

            # Delete the smaller downloaded file
            try:
                ops.delete(downloaded_path, "e621_smaller_download_removed")
            except Exception as e:
                print(f"  [WARNING] Could not delete downloaded file: {e}")

            # Rename current file to the new name
            try:
                ops.copy(image_path, new_path, "e621_original_kept_as_source")  # copy2 preserves metadata
                print("[OK]")
                downloaded_path = new_path  # Update path for later operations
            except Exception as e:
                print(f"[ERROR] Failed: {e}")
                continue
        else:
            # Copy file times from original to downloaded
            print(f"  Copying timestamps...", end=" ", flush=True)
            copy_file_times(image_path, downloaded_path)
            print("[OK]")

        # Move original to source_found
        print(f"  Moving original...", end=" ", flush=True)
        if move_to_source_found(image_path, source_found_dir, base_search_dir, preserve_structure):
            print(f"[OK] -> {source_found_dir}")
            moved += 1
        else:
            print("[ERROR] Failed")

        processed += 1
        print()

        # Rate limiting
        if i < len(images_dict):
            time.sleep(request_delay)

    # Summary
    print("=" * 80)
    print("Processing Complete!")
    print(f"Images processed: {processed}/{len(images_dict)}")
    print(f"Posts favorited: {favorited}")
    print(f"Files downloaded: {downloaded}")
    print(f"Originals moved: {moved}")
    print("=" * 80)


if __name__ == "__main__":
    main()
