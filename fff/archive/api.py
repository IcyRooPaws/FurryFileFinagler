# fff: the page cache folder comes from config (data/cache/e621_pages).
import time
import logging
import json
import os
import re
from datetime import datetime
from typing import List, Optional
from fff.archive.download import api_get
from requests.exceptions import ConnectionError
from urllib3.exceptions import ProtocolError

def get_auth(e621_config):
    login = e621_config.get("login")
    api_key = e621_config.get("api_key")
    if login and api_key:
        return (login, api_key)
    return None

def fetch_favorites(e621_config):
    favorites_username = e621_config.get("favorites_username")
    if not favorites_username:
        raise ValueError("favorites_username not provided in config.")

    # Always save responses to temp folder for later use
    temp_response_folder = e621_config.get("_temp_response_folder", "temp_responses")  # fff: data/cache/e621_pages
    os.makedirs(temp_response_folder, exist_ok=True)
    session_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Also save to user-configured folder if enabled
    save_responses = e621_config.get("save_api_responses", False)
    response_folder = e621_config.get("api_response_folder", "fav_response")
    if save_responses:
        os.makedirs(response_folder, exist_ok=True)

    search_tag = f"fav:{favorites_username} status:any"
    base_url = "https://e621.net/posts.json"
    headers = {"User-Agent": e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")}
    auth = get_auth(e621_config)
    favorites = []
    page = 1
    max_posts = e621_config.get("max_posts", "all")

    page_limit = min(e621_config.get("api_page_limit", 320), 320)

    while True:
        params = {"page": page, "tags": search_tag, "limit": page_limit, "v2": "true", "mode": "extended"}
        logging.info("Fetching favorites page %s with tag '%s'", page, search_tag)

        # Retry logic for connection issues
        max_retries = 3
        retry_count = 0
        response = None

        while retry_count < max_retries:
            try:
                response = api_get(base_url, params=params, headers=headers, auth=auth)
                response.raise_for_status()
                break  # Success, exit retry loop
            except (ConnectionError, ProtocolError) as e:
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = 5 + (retry_count * 2)  # Exponential backoff: 5s, 7s, 9s
                    logging.warning("Connection error on page %s (attempt %s/%s): %s",
                                  page, retry_count, max_retries, e)
                    logging.info("Waiting %s seconds before retry...", wait_time)
                    time.sleep(wait_time)
                else:
                    logging.error("Failed to fetch page %s after %s attempts: %s",
                                page, max_retries, e)
                    raise
            except Exception as e:
                logging.error("Unexpected error fetching page %s: %s", page, e)
                raise

        if response is None:
            logging.error("Failed to get response for page %s", page)
            break

        data = response.json()

        # Always save to temp folder for internal use
        page_response = {
            "page": page,
            "timestamp": datetime.now().isoformat(),
            "url": base_url,
            "params": params,
            "response": data
        }
        temp_response_file = os.path.join(temp_response_folder, f"temp_page_{page}_{session_timestamp}.json")
        try:
            with open(temp_response_file, 'w', encoding='utf-8') as f:
                json.dump(page_response, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error("Failed to save temp response for page %s: %s", page, e)

        # Also save to user folder if enabled
        if save_responses:
            response_file = os.path.join(response_folder, f"favorites_page_{page}_{session_timestamp}.json")
            try:
                with open(response_file, 'w', encoding='utf-8') as f:
                    json.dump(page_response, f, indent=2, ensure_ascii=False)
                logging.info("API response for page %s saved to: %s", page, response_file)
            except Exception as e:
                logging.error("Failed to save API response for page %s: %s", page, e)

        posts = data if isinstance(data, list) else data.get("posts", [])
        time.sleep(e621_config.get("rate_limit_sleep", 1))
        if not posts:
            logging.info("No more posts found on page %s", page)
            break
        favorites.extend(posts)
        if max_posts != "all" and isinstance(max_posts, int) and len(favorites) >= max_posts:
            favorites = favorites[:max_posts]
            logging.info("Reached max_posts limit of %s", max_posts)
            break
        page += 1

    logging.info("Total favorites retrieved: %s", len(favorites))
    _mark_session_complete(temp_response_folder, session_timestamp, page, len(favorites))

    # Store session info for later use
    e621_config['_temp_session_timestamp'] = session_timestamp
    e621_config['_temp_response_folder'] = temp_response_folder

    return favorites


_PAGE_FILE = re.compile(r"^temp_page_(\d+)_(\d{8}_\d{6})\.json$")
_COMPLETE_FILE = re.compile(r"^temp_page_complete_(\d{8}_\d{6})\.json$")


def _mark_session_complete(folder, session_timestamp, last_page, post_count) -> None:
    """Record that a fetch reached its end, so only a whole favorites list is ever reused.

    Named like the page files so the cache cleanups remove it with them.
    """
    path = os.path.join(folder, f"temp_page_complete_{session_timestamp}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pages": last_page, "posts": post_count, "finished": datetime.now().isoformat()}, f)
    except Exception as e:
        logging.error("Failed to mark the favorites fetch %s complete: %s", session_timestamp, e)


def load_recent_favorites_from_temp(e621_config, max_age_minutes: int = 15) -> Optional[List[dict]]:
    """Load the favorites list of a recent fetch from the page cache, or None.

    A cached fetch is reused only when it is whole: it finished (a completion marker exists),
    it started within ``max_age_minutes`` (its pages age together, so none of them was cleaned
    up), and every page it recorded is there and readable. Anything less returns None and the
    caller fetches again. A partial list must never be used: every favorite missing from it
    would be handled as unfavorited.
    """
    temp_folder = e621_config.get('_temp_response_folder', 'temp_responses')
    if not os.path.exists(temp_folder):
        return None

    names = os.listdir(temp_folder)
    sessions = sorted((m.group(1) for m in map(_COMPLETE_FILE.match, names) if m), reverse=True)
    if not sessions:
        return None
    session_id = sessions[0]         # newest finished fetch
    try:
        started = datetime.strptime(session_id, "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    if (datetime.now() - started).total_seconds() > max_age_minutes * 60:
        return None

    try:
        with open(os.path.join(temp_folder, f"temp_page_complete_{session_id}.json"), encoding="utf-8") as f:
            marker = json.load(f)
        expected_pages, expected_posts = int(marker["pages"]), int(marker["posts"])
    except Exception as exc:
        logging.warning("Not reusing cached favorites %s: unreadable completion marker (%s)", session_id, exc)
        return None

    pages = {int(m.group(1)): name for m in map(_PAGE_FILE.match, names) if m and m.group(2) == session_id
             for name in [m.group(0)]}
    if sorted(pages) != list(range(1, expected_pages + 1)):
        logging.warning("Not reusing cached favorites %s: %d of %d pages are left", session_id,
                        len(pages), expected_pages)
        return None

    posts = []
    for page in range(1, expected_pages + 1):
        try:
            with open(os.path.join(temp_folder, pages[page]), 'r', encoding='utf-8') as f:
                data = json.load(f)
            _resp = data.get('response', data)
            posts.extend(_resp if isinstance(_resp, list) else (_resp.get('posts') or []))
        except Exception as exc:
            logging.warning("Not reusing cached favorites %s: page %s is unreadable (%s)", session_id, page, exc)
            return None

    if len(posts) < expected_posts:
        logging.warning("Not reusing cached favorites %s: %d of %d posts", session_id, len(posts), expected_posts)
        return None
    if not posts:
        return None
    logging.info("Loaded %s posts from recent temp responses (session %s)", len(posts), session_id)
    return posts[:expected_posts]

def get_post_data_from_temp(post_id, e621_config):
    """Get post data from saved temp response files instead of making API calls.
    Uses caching to avoid re-reading files for better performance.
    """
    temp_folder = e621_config.get('_temp_response_folder', 'temp_responses')
    session_timestamp = e621_config.get('_temp_session_timestamp')

    if not session_timestamp:
        logging.warning("No temp response files available for post %s", post_id)
        return None

    # Initialize temp data cache if not exists
    if '_temp_data_cache' not in e621_config:
        e621_config['_temp_data_cache'] = {}
        logging.info("Loading temp response files into cache for faster lookups...")

        # Load all temp files into cache once
        import glob
        pattern = os.path.join(temp_folder, f"temp_page_*_{session_timestamp}.json")
        temp_files = glob.glob(pattern)

        for temp_file in temp_files:
            try:
                with open(temp_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    _resp = data.get('response', data)
                    posts = _resp if isinstance(_resp, list) else _resp.get('posts', [])

                    # Cache each post by ID
                    for post in posts:
                        if post.get('id'):
                            e621_config['_temp_data_cache'][post['id']] = post
            except Exception as e:
                logging.error("Error reading temp file %s: %s", temp_file, e)

        logging.info("Loaded %s posts into temp data cache", len(e621_config['_temp_data_cache']))

    # Return cached post data
    return e621_config['_temp_data_cache'].get(post_id)

def cleanup_temp_responses(e621_config, max_age_minutes: int = 15):
    """Clean up temporary response files older than max_age_minutes.

    Intended to be run at the start of a session. Removes cache files older
    than the age threshold and clears any in-memory cache if present.
    """
    temp_folder = e621_config.get('_temp_response_folder', 'temp_responses')

    # Clear the temp data cache (session-bound)
    if '_temp_data_cache' in e621_config:
        del e621_config['_temp_data_cache']
        logging.info("Cleared temp data cache")

    if not os.path.exists(temp_folder):
        return

    cutoff_ts = time.time() - (max_age_minutes * 60)
    deleted_count = 0

    for fname in os.listdir(temp_folder):
        # Only clean up our temp_page_* files
        if not fname.startswith('temp_page_') or not fname.endswith('.json'):
            continue

        path = os.path.join(temp_folder, fname)
        try:
            if not os.path.isfile(path):
                continue
            if os.path.getmtime(path) < cutoff_ts:
                os.remove(path)
                deleted_count += 1
        except Exception as e:
            logging.error("Error deleting temp file %s: %s", path, e)

    if deleted_count > 0:
        logging.info("Cleaned up %s temporary response files older than %s minutes", deleted_count, max_age_minutes)

    # Try to remove temp folder if empty
    try:
        if not os.listdir(temp_folder):
            os.rmdir(temp_folder)
    except Exception:
        pass

def check_and_cleanup_stale_responses(e621_config, max_age_minutes: int = 15):
    """Check if ANY temp files are older than threshold. If yes, delete ALL and force refresh.

    Returns True if files were cleaned up (forcing a refetch), False otherwise.
    """
    temp_folder = e621_config.get('_temp_response_folder', 'temp_responses')

    if not os.path.exists(temp_folder):
        return False

    cutoff_ts = time.time() - (max_age_minutes * 60)
    has_stale_files = False

    # Check if any temp files are older than the threshold
    for fname in os.listdir(temp_folder):
        # Only check our temp_page_* files
        if not fname.startswith('temp_page_') or not fname.endswith('.json'):
            continue

        path = os.path.join(temp_folder, fname)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff_ts:
                has_stale_files = True
                break
        except Exception:
            continue

    # If any stale files found, delete ALL temp files
    if has_stale_files:
        logging.warning(f"Found temp response files older than {max_age_minutes} minutes - cleaning up ALL temp files")
        deleted_count = 0

        for fname in os.listdir(temp_folder):
            # Only delete our temp_page_* files
            if not fname.startswith('temp_page_') or not fname.endswith('.json'):
                continue

            path = os.path.join(temp_folder, fname)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    deleted_count += 1
            except Exception as e:
                logging.error("Error deleting temp file %s: %s", path, e)

        logging.info(f"Deleted {deleted_count} temp response files - will force fresh API fetch")

        # Clear the temp data cache
        if '_temp_data_cache' in e621_config:
            del e621_config['_temp_data_cache']

        # Try to remove temp folder if empty
        try:
            if not os.listdir(temp_folder):
                os.rmdir(temp_folder)
        except Exception:
            pass

        return True

    return False


def fetch_pool_metadata(pool_ids, e621_config):
    """Fetch metadata for a list of pool IDs from the e621 pools API.

    Args:
        pool_ids: Iterable of pool IDs (ints)
        e621_config: e621 config dict (credentials, user_agent)

    Returns:
        dict mapping pool_id (int) -> pool metadata dict
    """
    pool_ids = list(pool_ids)
    if not pool_ids:
        return {}

    base_url = "https://e621.net/pools.json"
    headers = {"User-Agent": e621_config.get("user_agent", "MyE621FavoritesDownloader/1.0")}
    auth = get_auth(e621_config)

    results = {}
    batch_size = 100

    for i in range(0, len(pool_ids), batch_size):
        batch = pool_ids[i:i + batch_size]
        params = {"search[id]": ",".join(str(p) for p in batch), "limit": len(batch)}
        try:
            response = api_get(base_url, params=params, headers=headers, auth=auth)
            response.raise_for_status()
            for pool in response.json():
                results[pool["id"]] = pool
            logging.debug("Fetched metadata for %s pools (batch %s/%s)", len(batch), i // batch_size + 1, (len(pool_ids) - 1) // batch_size + 1)
        except Exception as e:
            logging.error("Failed fetching pool metadata for batch %s: %s", i // batch_size + 1, e)

    return results

