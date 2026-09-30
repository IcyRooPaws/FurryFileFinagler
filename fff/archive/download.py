# fff: implemented in e621 archive/modules/download.py.
# fff: MediaHasher is the archive hash profile (fff.hashing.archive_hasher).
import os
import re
import time
import requests
import logging
from requests.exceptions import Timeout, ConnectionError
from urllib3.exceptions import ProtocolError

from fff.archive.db import strip_flag_tokens
from fff.archive.config import normalize_file_path

# Optional import for media hashing
try:
    from fff.hashing import archive_hasher as MediaHasher  # fff: archive hash profile
    HAS_MEDIA_HASHER = True
except ImportError:
    HAS_MEDIA_HASHER = False

API_CALL_LIMIT = 5000
api_call_count = 0
last_request_time = 0

def api_get(url, params=None, headers=None, auth=None, stream=False):
    global api_call_count, API_CALL_LIMIT, last_request_time

    # Rate limiting: ensure at least 1 second between requests
    current_time = time.time()
    time_since_last_request = current_time - last_request_time
    if time_since_last_request < 1.0:
        sleep_time = 1.0 - time_since_last_request
        logging.debug(f"Rate limiting: sleeping for {sleep_time:.2f} seconds")
        time.sleep(sleep_time)

    while True:
        try:
            last_request_time = time.time()  # Update timestamp before making request
            response = requests.get(url, params=params, headers=headers, auth=auth, stream=stream, timeout=30)
            api_call_count += 1
            if api_call_count % API_CALL_LIMIT == 0:
                logging.info("Reached 5000 API calls. Sleeping for 60 seconds...")
                time.sleep(60)
            return response
        except Timeout as e:
            logging.error("Timeout occurred for URL %s: %s", url, e)
            logging.info("Sleeping for 60 seconds before retrying...")
            time.sleep(60)

def ensure_download_folder(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)


def _collect_search_dirs(download_config):
    """Return a unique list of download directories to scan for existing files."""
    dirs = set()

    def add_dir(path):
        if path and isinstance(path, str):
            dirs.add(path)

    for value in download_config.values():
        if isinstance(value, str):
            add_dir(value)
        elif isinstance(value, dict):
            for sub in value.values():
                add_dir(sub)

    # Allow explicit extra search directories (list of paths)
    extra_dirs = download_config.get("extra_search_dirs") or []
    if isinstance(extra_dirs, str):  # in case someone sets a single path string
        extra_dirs = [extra_dirs]
    for path in extra_dirs:
        add_dir(path)

    add_dir(download_config.get("missing_files_directory"))
    return list(dirs)


def _filename_has_exact_post_id(fname, post_id):
    """Return True when fname encodes post_id in the canonical e621 filename pattern."""
    base_name = os.path.splitext(os.path.basename(fname))[0]
    match = re.search(r"-(?:safe|questionable|explicit)-(\d+)(?:-\d+)?$", base_name, re.IGNORECASE)
    if not match:
        return False

    return match.group(1) == str(post_id)


def find_file_with_post_id(post_id, search_dirs):
    """Search for any file whose name contains post_id as a complete token."""
    for root_dir in search_dirs:
        if not root_dir or not os.path.exists(root_dir):
            continue

        try:
            for dirpath, _, filenames in os.walk(root_dir):
                for fname in filenames:
                    if _filename_has_exact_post_id(fname, post_id):
                        return os.path.join(dirpath, fname)
        except Exception as exc:
            logging.debug("Skipping directory %s due to error: %s", root_dir, exc)

    return None

def generate_media_hash(file_path, mysql_config=None):
    """Generate media hash for a file if media_hasher is available.

    First checks image_sorting_hashes database if available, then generates new hash.
    """
    if not HAS_MEDIA_HASHER:
        return None

    try:
        # Extract file_name for database lookup
        file_name = os.path.basename(file_path)

        # Check image_sorting_hashes database first if mysql_config provided
        if mysql_config:
            try:
                from fff.archive.db import check_media_hash_in_image_sorting_db
                existing_hash = check_media_hash_in_image_sorting_db(mysql_config, file_name)
                if existing_hash:
                    logging.info(f"Using existing hash for {file_name} from image_sorting_hashes database")
                    return existing_hash
            except Exception as e:
                logging.debug(f"Could not check image_sorting_hashes database: {e}")

        # Generate new hash if not found in external database
        hasher = MediaHasher()
        new_hash = hasher.hash_file(file_path)
        if new_hash:
            logging.debug(f"Generated new media hash for {file_name}")
        return new_hash

    except Exception as e:
        logging.warning(f"Could not generate media hash for {file_path}: {e}")
        return None

def download_missing_files(conn, download_config, e621_config, mysql_config=None):
    """Download files for posts that have no file_path but are not deleted and not flash files."""

    search_dirs = _collect_search_dirs(download_config)

    # Get the directory for missing files
    missing_files_dir = download_config.get("missing_files_directory")
    if not missing_files_dir:
        logging.error("missing_files_directory not configured in download config")
        return

    # Ensure the directory exists
    ensure_download_folder(missing_files_dir)

    # Query for missing files
    cursor = conn.cursor()
    try:
        query = """
        SELECT id, file_url, file_name, rating
        FROM posts
        WHERE file_path IS NULL
        AND file_type != 'flash'
        AND deleted = 0
        AND file_url IS NOT NULL
        AND file_url != ''
        """
        cursor.execute(query)
        missing_posts = cursor.fetchall()

        if not missing_posts:
            logging.info("No missing files found to download")
            return

        logging.info(f"Found {len(missing_posts)} posts with missing files to download")

        downloaded_count = 0
        failed_count = 0
        skipped_count = 0

        headers = {"User-Agent": e621_config.get("user_agent", "e621 archive/1.0")}

        for post_id, file_url, file_name, rating in missing_posts:
            try:
                if not file_name or file_name.strip() == '':
                    logging.warning(f"Post {post_id} has no file_name, skipping")
                    skipped_count += 1
                    continue

                # Normalize filename to remove any flag tokens before using it on disk
                clean_file_name = strip_flag_tokens(file_name)
                if clean_file_name != file_name:
                    logging.info(
                        "Normalizing filename for post %s: %s -> %s",
                        post_id,
                        file_name,
                        clean_file_name,
                    )
                    file_name = clean_file_name

                # Look for any existing file that already has this post_id in its name
                existing_path = find_file_with_post_id(post_id, search_dirs)
                if existing_path and os.path.exists(existing_path):
                    logging.info("Found existing file for post %s at %s; skipping download", post_id, existing_path)

                    update_cursor = conn.cursor()
                    try:
                        # Update DB with the discovered path and media hash if missing
                        update_cursor.execute("SELECT media_hash FROM posts WHERE id = %s", (post_id,))
                        result = update_cursor.fetchone()
                        existing_media_hash = result[0] if result else None

                        media_hash = existing_media_hash or generate_media_hash(existing_path, mysql_config)

                        if media_hash:
                            update_cursor.execute(
                                "UPDATE posts SET file_path = %s, media_hash = %s WHERE id = %s",
                                (normalize_file_path(existing_path), media_hash, post_id)
                            )
                        else:
                            update_cursor.execute(
                                "UPDATE posts SET file_path = %s WHERE id = %s",
                                (normalize_file_path(existing_path), post_id)
                            )
                        conn.commit()
                    except Exception as e:
                        logging.error("Failed to update existing path for post %s: %s", post_id, e)
                        failed_count += 1
                    finally:
                        update_cursor.close()

                    skipped_count += 1
                    continue

                # Construct the full file path
                target_path = os.path.join(missing_files_dir, file_name)

                # Skip if file already exists
                if os.path.exists(target_path):
                    logging.info(f"File already exists for post {post_id}: {file_name}")

                    # Generate media hash for existing file if not already present
                    update_cursor = conn.cursor()
                    try:
                        # Check if media_hash already exists
                        update_cursor.execute("SELECT media_hash FROM posts WHERE id = %s", (post_id,))
                        result = update_cursor.fetchone()
                        existing_media_hash = result[0] if result else None

                        if not existing_media_hash:
                            media_hash = generate_media_hash(target_path, mysql_config)
                            if media_hash:
                                update_cursor.execute(
                                    "UPDATE posts SET file_path = %s, media_hash = %s WHERE id = %s",
                                    (normalize_file_path(target_path), media_hash, post_id)
                                )
                                logging.info(f"Updated existing file with media_hash for post {post_id}")
                            else:
                                update_cursor.execute(
                                    "UPDATE posts SET file_path = %s WHERE id = %s",
                                    (normalize_file_path(target_path), post_id)
                                )
                        else:
                            # Just update file_path if media_hash already exists
                            update_cursor.execute(
                                "UPDATE posts SET file_path = %s WHERE id = %s",
                                (normalize_file_path(target_path), post_id)
                            )

                        conn.commit()
                        logging.info(f"Updated file_path for existing file: {post_id}")
                        skipped_count += 1
                    except Exception as e:
                        logging.error(f"Failed to update file_path for post {post_id}: {e}")
                        failed_count += 1
                    finally:
                        update_cursor.close()
                    continue

                # Download the file
                logging.info(f"Downloading post {post_id}: {file_name}")

                # Retry logic for connection issues during file download
                max_retries = 3
                retry_count = 0
                response = None

                while retry_count < max_retries:
                    try:
                        response = api_get(file_url, headers=headers, stream=True)
                        if response.status_code == 200:
                            break  # Success, exit retry loop
                        else:
                            logging.warning(f"HTTP {response.status_code} for post {post_id} on attempt {retry_count + 1}")
                            if retry_count < max_retries - 1:
                                time.sleep(2 + retry_count)  # Wait before retry
                            retry_count += 1
                    except (ConnectionError, ProtocolError) as e:
                        retry_count += 1
                        if retry_count < max_retries:
                            wait_time = 3 + retry_count  # 4s, 5s, 6s
                            logging.warning("Connection error downloading post %s (attempt %s/%s): %s",
                                          post_id, retry_count, max_retries, e)
                            logging.info("Waiting %s seconds before retry...", wait_time)
                            time.sleep(wait_time)
                        else:
                            logging.error("Failed to download post %s after %s attempts: %s",
                                        post_id, max_retries, e)
                            continue  # Skip this file and move to the next
                    except Exception as e:
                        logging.error("Unexpected error downloading post %s: %s", post_id, e)
                        break  # Skip this file and move to the next

                if response is None or response.status_code != 200:
                    logging.error(f"Failed to download post {post_id} after retries")
                    continue

                # Write the file
                try:
                    with open(target_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    # Verify file was written
                    if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                        # Generate media hash for the downloaded file
                        media_hash = generate_media_hash(target_path, mysql_config)

                        # Update the database with the file path and media hash
                        update_cursor = conn.cursor()
                        try:
                            if media_hash:
                                update_cursor.execute(
                                    "UPDATE posts SET file_path = %s, media_hash = %s WHERE id = %s",
                                    (normalize_file_path(target_path), media_hash, post_id)
                                )
                                logging.debug(f"Updated post {post_id} with file_path and media_hash")
                            else:
                                update_cursor.execute(
                                    "UPDATE posts SET file_path = %s WHERE id = %s",
                                    (normalize_file_path(target_path), post_id)
                                )
                                logging.debug(f"Updated post {post_id} with file_path (no media_hash)")

                            conn.commit()
                            downloaded_count += 1
                            logging.info(f"Successfully downloaded and updated post {post_id}: {file_name}")
                        except Exception as e:
                            logging.error(f"Failed to update database for post {post_id}: {e}")
                            # Remove the downloaded file if database update fails
                            try:
                                os.remove(target_path)
                            except:
                                pass
                            failed_count += 1
                        finally:
                            update_cursor.close()
                    else:
                        logging.error(f"Downloaded file for post {post_id} is empty or missing")
                        try:
                            os.remove(target_path)
                        except:
                            pass
                        failed_count += 1
                except Exception as e:
                    logging.error(f"Failed to write file for post {post_id}: {e}")
                    failed_count += 1

                # Small delay to be respectful to the server
                time.sleep(0.5)

            except Exception as e:
                logging.error(f"Error downloading post {post_id}: {e}")
                failed_count += 1

        logging.info(f"Missing files download complete: {downloaded_count} downloaded, {skipped_count} skipped (already exist), {failed_count} failed")

    except Exception as e:
        logging.error(f"Error in download_missing_files: {e}")
    finally:
        cursor.close()
