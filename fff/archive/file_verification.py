# fff: process_orphan_file has a conditional download_file import;
# fff: it only runs when e621.verify_sources is true.
import os
import logging
from fff.clients import http
import time
import concurrent.futures
from datetime import datetime
from requests.exceptions import ConnectionError, Timeout
from urllib3.exceptions import ProtocolError

def verify_and_update_files(conn, download_config, e621_config):
    if not e621_config.get("verify_sources", True):
        logging.info("Source verification is disabled in the configuration. Skipping.")
        return

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for category, folder_config in download_config.items():
            if isinstance(folder_config, dict):
                for subfolder in folder_config.values():
                    if isinstance(subfolder, str):
                        futures.append(executor.submit(process_directory, conn, subfolder, e621_config))
            elif isinstance(folder_config, str):
                futures.append(executor.submit(process_directory, conn, folder_config, e621_config))

        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.error("Error in file processing: %s", e)

def process_directory(conn, directory, e621_config):
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                process_orphan_file(conn, file_path, e621_config)
            except Exception as e:
                logging.error("Error processing file %s: %s", file_path, e)

def process_orphan_file(conn, filepath, e621_config):
    post_id = extract_post_id_from_file_name(filepath)
    if not post_id:
        logging.error("Could not extract post id from file_name: %s", filepath)
        return

    post_url = f"https://e621.net/posts/{post_id}.json?v2=true&mode=extended"
    headers = {"User-Agent": e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")}
    auth = (e621_config.get("login"), e621_config.get("api_key"))
    
    # Retry logic for connection issues
    max_retries = 3
    retry_count = 0
    response = None
    
    while retry_count < max_retries:
        try:
            response = http.get("e621", post_url, headers=headers, auth=auth, timeout=30)
            response.raise_for_status()
            data = response.json()
            post = data if "id" in data else data.get("post", {})
            if not post:
                logging.error("No post data found for post id %s", post_id)
                return
            break  # Success, exit retry loop
        except (ConnectionError, ProtocolError, Timeout) as e:
            retry_count += 1
            if retry_count < max_retries:
                wait_time = 2 + retry_count  # 3s, 4s, 5s
                logging.warning("Connection error fetching post %s (attempt %s/%s): %s", 
                              post_id, retry_count, max_retries, e)
                logging.info("Waiting %s seconds before retry...", wait_time)
                time.sleep(wait_time)
            else:
                logging.error("Failed to fetch post %s after %s attempts: %s", 
                            post_id, max_retries, e)
                return
        except Exception as e:
            logging.error("Error fetching post data for post %s: %s", post_id, e)
            return

    try:
        stat_info = os.stat(filepath)
        orphan_mtime = stat_info.st_mtime
    except Exception as e:
        logging.error("Could not get modification time for %s: %s", filepath, e)
        orphan_mtime = None

    # Handle v1 file.url and v2 files.original.url
    if "files" in post:
        file_url = post["files"].get("original", {}).get("url", "")
    else:
        file_url = post.get("file", {}).get("url", "")
    if not file_url:
        logging.error("No file URL found for post %s", post_id)
        return

    try:
        from fff.archive.download import download_file
        new_file_path = download_file(file_url, e621_config, post)
    except Exception as e:
        logging.error("Error downloading file for orphan post %s: %s", post_id, e)
        return

    if orphan_mtime is not None:
        try:
            os.utime(new_file_path, (orphan_mtime, orphan_mtime))
            logging.info("Updated modification time for downloaded file %s", new_file_path)
            post["ingested_at"] = datetime.fromtimestamp(orphan_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            logging.error("Error updating modification time for %s: %s", new_file_path, e)

    try:
        os.remove(filepath)
        logging.info("Deleted orphan file %s", filepath)
    except Exception as e:
        logging.error("Error deleting orphan file %s: %s", filepath, e)

def extract_post_id_from_file_name(filepath):
    basename = os.path.basename(filepath)
    name_no_ext, _ = os.path.splitext(basename)
    # Strategy: grab the longest trailing sequence of digits in the stem as the post id.
    import re
    match = re.search(r'(\d+)$', name_no_ext)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    logging.warning("Could not extract numeric post id from file_name: %s", basename)
    return None
