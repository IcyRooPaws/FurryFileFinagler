"""e621 API access.

Two request styles are kept exactly as they were, because callers depend on their
error behavior:
    * ``api_get``: returns the response, raises once retries run out.
    * Search helpers (``get_post_data``, ``add_to_favorites`` ...): print and return
    None/False on failure.
Both go through fff.clients.http (shared rate limit and retries).
v1/v2 response helpers normalize the supported API response shapes.
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fff.clients import http

# --- e621 archive/modules/db.py: v1/v2 response helpers -----------------

def _parse_post_list(data):
    """Return the list of posts from either a v1 or v2 API response."""
    if isinstance(data, list):
        return data  # v2: bare array
    return data.get("posts", [])  # v1: {"posts": [...]}


def _parse_single_post(data):
    """Return the post dict from either a v1 or v2 single-post API response."""
    if isinstance(data, dict):
        if "post" in data:
            return data["post"]  # v1: {"post": {...}}
        if "id" in data:
            return data  # v2: bare object
    return {}


def _get_file_url(post):
    """Return the current file URL from either v1 or v2 post dict."""
    if "files" in post:
        return post["files"].get("original", {}).get("url")
    return post.get("file", {}).get("url")


def _get_file_md5(post):
    """Return the MD5 hash from either v1 or v2 post dict."""
    if "files" in post:
        return post["files"].get("meta", {}).get("md5")
    return post.get("file", {}).get("md5")


def _get_file_ext(post):
    """Return the file extension from either v1 or v2 post dict."""
    if "files" in post:
        return post["files"].get("meta", {}).get("ext", "")
    return post.get("file", {}).get("ext", "")


# --- e621 archive/modules/api.py + download.py -------------------------

def get_auth(e621_config):
    login = e621_config.get("login")
    api_key = e621_config.get("api_key")
    if login and api_key:
        return (login, api_key)
    return None


def api_get(url, params=None, headers=None, auth=None, stream=False, timeout=30):
    """GET through the shared e621 session (fff.clients.http): 1 s spacing, a pause every
    5000 calls, and capped retries for timeouts, connection errors, 429 and 5xx. Raises the
    requests exception once retries run out (it used to retry a timeout forever)."""
    return http.get("e621", url, params=params, headers=headers, auth=auth, stream=stream, timeout=timeout)


# --- reverse-image source search/scripts/process_e621_matches.py ------------------

def get_all_favorites(login: str, api_key: str, user_agent: str) -> set:
    """
    Fetch all favorited post IDs for the user.
    Returns a set of post IDs (as strings) for fast lookup.
    """
    try:
        print("Fetching your e621 favorites...")
        favorites = set()
        page = 1
        max_pages = 1000  # Safety limit

        headers = {"User-Agent": user_agent}
        auth = (login, api_key)

        while page <= max_pages:
            print(f"  Page {page}...", end=" ", flush=True)
            url = "https://e621.net/favorites.json"
            params = {
                "page": page,
                "limit": 320  # Max allowed by e621
            }

            response = http.get("e621", url, headers=headers, auth=auth, params=params, timeout=30)

            if response.status_code != 200:
                print(f"[ERROR] Status {response.status_code}")
                print(f"  Warning: Failed to fetch favorites page {page}")
                break

            data = response.json()
            posts = data.get('posts', [])

            if not posts:
                # No more posts, we've reached the end
                print("[Done - no more posts]")
                break

            # Add post IDs to the set
            for post in posts:
                post_id = str(post.get('id'))
                favorites.add(post_id)

            print(f"[OK] +{len(posts)} posts")
            page += 1

            # Be nice to the API - small delay between pages
            if page <= max_pages:
                time.sleep(0.5)

        print(f"\n[OK] Total favorites loaded: {len(favorites)}")
        return favorites

    except Exception as e:
        print(f"\n[ERROR] Error fetching favorites: {e}")
        return set()


def check_if_favorited(post_id: str, login: str, api_key: str, user_agent: str,
                       favorites_cache: set = None) -> bool:
    """
    Check if a post is in the user's favorites.
    If favorites_cache is provided, uses that for instant lookup.
    Otherwise falls back to API call.
    """
    try:
        # Extract just the numeric ID if it has a prefix
        if '_' in post_id:
            post_id = post_id.split('_')[-1]

        # Use cache if available
        if favorites_cache is not None:
            return post_id in favorites_cache

        # Fallback to API call if no cache
        # Get the post data - it includes is_favorited flag
        url = f"https://e621.net/posts/{post_id}.json"
        headers = {"User-Agent": user_agent}
        auth = (login, api_key)

        response = http.get("e621", url, headers=headers, auth=auth, timeout=10)

        if response.status_code == 200:
            data = response.json()
            post_data = data.get('post', {})
            # The is_favorited flag tells us if the authenticated user has favorited this post
            return post_data.get('is_favorited', False)

        return False
    except Exception as e:
        print(f"    Error checking favorites: {e}")
        return False


def add_to_favorites(post_id: str, login: str, api_key: str, user_agent: str,
                    favorites_cache: set = None) -> bool:
    """
    Add a post to the user's favorites.
    If favorites_cache is provided, updates it on success.
    """
    try:
        # Extract just the numeric ID if it has a prefix
        if '_' in post_id:
            numeric_id = post_id.split('_')[-1]
        else:
            numeric_id = post_id

        url = "https://e621.net/favorites.json"
        headers = {"User-Agent": user_agent}
        auth = (login, api_key)

        data = {
            "post_id": numeric_id
        }

        response = http.post("e621", url, headers=headers, auth=auth, json=data, timeout=10)

        if response.status_code in [200, 201, 204]:
            # Update cache if provided
            if favorites_cache is not None:
                favorites_cache.add(numeric_id)
            return True
        else:
            print(f"    Warning: Favorite response code {response.status_code}")
            return False
    except Exception as e:
        print(f"    Error adding to favorites: {e}")
        return False


def remove_from_favorites(post_id: str, login: str, api_key: str, user_agent: str,
                         favorites_cache: set = None) -> bool:
    """
    Remove a post from the user's favorites.
    If favorites_cache is provided, updates it on success.
    """
    try:
        # Extract just the numeric ID if it has a prefix
        if '_' in post_id:
            numeric_id = post_id.split('_')[-1]
        else:
            numeric_id = post_id

        url = f"https://e621.net/favorites/{numeric_id}.json"
        headers = {"User-Agent": user_agent}
        auth = (login, api_key)

        response = http.delete("e621", url, headers=headers, auth=auth, timeout=10)

        if response.status_code in [200, 201, 204]:
            # Update cache if provided
            if favorites_cache is not None:
                favorites_cache.discard(numeric_id)
            return True
        else:
            print(f"    Warning: Unfavorite response code {response.status_code}")
            return False
    except Exception as e:
        print(f"    Error removing from favorites: {e}")
        return False


def get_artist_main_name(artist_tag: str, login: str, api_key: str, user_agent: str) -> str:
    """
    Get the main/canonical name for an artist tag.
    If this is an alias, returns the tag it points to. Otherwise returns the original tag.
    """
    try:
        url = "https://e621.net/tags.json"
        headers = {"User-Agent": user_agent}
        auth = (login, api_key)
        params = {
            "search[name]": artist_tag,
            "search[category]": 1,  # 1 = artist category
            "limit": 1
        }

        response = http.get("e621", url, headers=headers, auth=auth, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            if data:
                tag_info = data[0]
                # Check if this tag has consequent tags (meaning it's an alias)
                consequent_tags = tag_info.get('consequent_tags', [])
                if consequent_tags:
                    # This is an alias, use the consequent tag (main name)
                    for consequent in consequent_tags:
                        # Return the first consequent tag that's an artist
                        return consequent
                # Otherwise return the original tag (it's already the main name)
                return artist_tag

        # If we couldn't resolve it, return the original
        return artist_tag
    except Exception as e:
        # On error, return the original tag
        return artist_tag


def get_post_data(post_id: str, login: str, api_key: str, user_agent: str,
                  save_json: bool = False, responses_dir: str = None) -> Optional[Dict[str, Any]]:
    """Get detailed post data from e621 API.

    Args:
        post_id: E621 post ID
        login: E621 username
        api_key: E621 API key
        user_agent: User agent string
        save_json: If True, save the JSON response to file
        responses_dir: Directory to save JSON responses (required if save_json=True)

    Returns:
        Post data dictionary or None if error
    """
    try:
        # Extract just the numeric ID if it has a prefix
        if '_' in post_id:
            numeric_id = post_id.split('_')[-1]
        else:
            numeric_id = post_id

        # Check for cached response files (less than 15 minutes old)
        if responses_dir:
            responses_path = Path(responses_dir)
            if responses_path.exists():
                # Look for any existing JSON files for this post ID
                cache_pattern = f"e621_{numeric_id}_*.json"
                matching_files = list(responses_path.glob(cache_pattern))

                if matching_files:
                    # Sort by modification time, newest first
                    matching_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    newest_file = matching_files[0]

                    # Check if file is less than 15 minutes old
                    file_age_seconds = time.time() - newest_file.stat().st_mtime
                    cache_max_age = 15 * 60  # 15 minutes in seconds

                    if file_age_seconds < cache_max_age:
                        # Load and return cached data
                        try:
                            with open(newest_file, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                            age_minutes = int(file_age_seconds / 60)
                            print(f"[CACHED - {age_minutes}m old]", end=" ")
                            return data
                        except Exception as e:
                            print(f"    Warning: Could not load cached response: {e}")
                            # Continue to API call if cache load fails
                    else:
                        # Cache is stale (older than 15 minutes), delete old files for this post
                        try:
                            for old_file in matching_files:
                                old_file.unlink()
                        except Exception as e:
                            print(f"    Warning: Could not delete old cache files: {e}")
                        # Continue to API call to get fresh data

        url = f"https://e621.net/posts/{numeric_id}.json"
        headers = {"User-Agent": user_agent}
        auth = (login, api_key)

        response = http.get("e621", url, headers=headers, auth=auth, timeout=10)

        if response.status_code == 200:
            data = response.json()

            # Optionally save JSON response to file
            if save_json and responses_dir:
                try:
                    responses_path = Path(responses_dir)
                    responses_path.mkdir(parents=True, exist_ok=True)

                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    json_filename = f"e621_{numeric_id}_{timestamp}.json"
                    json_path = responses_path / json_filename

                    with open(json_path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print(f"    Warning: Could not save e621 JSON response: {e}")

            return data
        else:
            print(f"    Error: API returned status {response.status_code}")
            return None
    except Exception as e:
        print(f"    Error fetching post data: {e}")
        return None


def generate_filename(post_data: Dict[str, Any], login: str = None, api_key: str = None, user_agent: str = None) -> str:
    """Generate filename based on e621 naming convention matching FA format."""
    post = post_data.get('post', {})

    # Get artists
    tags = post.get('tags', {})
    artists = tags.get('artist', [])

    # Filter out "avoid_posting"
    artists = [a for a in artists if a != 'avoid_posting']

    # Resolve all artist tags to their main names
    main_artists = []
    for artist in artists:
        if login and api_key and user_agent:
            main_name = get_artist_main_name(artist, login, api_key, user_agent)
            main_artists.append(main_name)
        else:
            main_artists.append(artist)

    # Join all main artist names with underscores
    artist_str = '_'.join(main_artists) if main_artists else 'unknown'

    # Sanitize artist name - remove invalid filename characters
    artist_str = artist_str.replace('/', '_').replace('\\', '_')
    artist_str = re.sub(r'[<>:"|?*]', '_', artist_str)
    artist_str = artist_str.strip()

    # Get rating
    rating_map = {
        's': 'safe',
        'q': 'questionable',
        'e': 'explicit'
    }
    rating = post.get('rating', 's')
    rating_str = rating_map.get(rating, 'safe')

    # Get post ID
    post_id = post.get('id', '')

    # Get extension
    file_info = post.get('file', {})
    extension = file_info.get('ext', '')

    if not extension:
        # Try to get from URL
        file_url = file_info.get('url', '')
        if file_url:
            extension = file_url.split('.')[-1].lower()

    if not extension:
        extension = 'jpg'  # Default fallback

    # Build filename
    filename = f"{artist_str}-{rating_str}-{post_id}.{extension}"

    return filename


def _post_output_path(filename: str, output_dir: Path, original_image_path: Path = None,
                      base_search_dir: Path = None, preserve_structure: bool = False) -> Path:
    """Where a post's file goes: output_dir/filename, or mirroring the source subfolder."""
    if preserve_structure and original_image_path and base_search_dir:
        try:
            # Calculate relative path from base directory
            relative_path = original_image_path.parent.relative_to(base_search_dir)
            output_path = output_dir / relative_path / filename
            # Create parent directories if needed
            output_path.parent.mkdir(parents=True, exist_ok=True)
            print(f"\n    [DEBUG] Preserving structure: {relative_path}")
            print(f"    [DEBUG] Output path: {output_path}")
            print(f"    [DEBUG] Directory created: {output_path.parent}")
            return output_path
        except ValueError as e:
            # original_image_path is not relative to base_search_dir, use flat structure
            output_dir.mkdir(exist_ok=True)
            output_path = output_dir / filename
            print(f"\n    [DEBUG] Not relative, using flat structure (error: {e})")
            print(f"    [DEBUG] Output path: {output_path}")
            return output_path
    # Flat structure (original behavior)
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / filename
    print(f"\n    [DEBUG] Flat structure (preserve={preserve_structure}, orig={original_image_path is not None}, base={base_search_dir is not None})")
    print(f"    [DEBUG] Output path: {output_path}")
    return output_path


def is_deleted_without_file(post_data: Dict[str, Any]) -> bool:
    """True when e621 deleted the post, so it no longer hands out the file URL."""
    post = post_data.get('post', {})
    return bool(post.get('flags', {}).get('deleted')) and not post.get('file', {}).get('url')


def keep_local_file_as_post(post_data: Dict[str, Any], image_path: Path, output_dir: Path, login: str,
                            api_key: str, user_agent: str, base_search_dir: Path = None,
                            preserve_structure: bool = False, move=None) -> Optional[Path]:
    """For a post deleted on e621: give the local file the name the download would have had
    (artist-rating-postid) and move it to where the download would have gone.

    The local file's own extension is kept, since its format is what's on disk. ``move(src,
    dest)`` does the move (the caller's logged move); returns the new path, or None.
    """
    try:
        filename = generate_filename(post_data, login, api_key, user_agent)
        filename = Path(filename).stem + image_path.suffix.lower()
        output_path = _post_output_path(filename, output_dir, image_path, base_search_dir, preserve_structure)
        if output_path.exists():
            print(f"    Error: {output_path.name} already exists in the destination; leaving the file where it is")
            return None
        move(image_path, output_path)
        return output_path
    except Exception as e:
        print(f"    Error keeping local file for deleted post: {e}")
        return None


def download_post(post_data: Dict[str, Any], output_dir: Path, login: str, api_key: str, user_agent: str,
                  original_image_path: Path = None, base_search_dir: Path = None,
                  preserve_structure: bool = False) -> Optional[Path]:
    """
    Download the post file with proper naming.

    Args:
        post_data: Post data dictionary
        output_dir: Base output directory for downloads
        login: E621 username
        api_key: E621 API key
        user_agent: User agent string
        original_image_path: Path to original image (for preserving structure)
        base_search_dir: Base search directory (for calculating relative path)
        preserve_structure: If True, preserve subdirectory structure
    """
    try:
        post = post_data.get('post', {})
        file_info = post.get('file', {})
        file_url = file_info.get('url')

        if not file_url:
            print("    Error: No file URL found in post data")
            return None

        # Generate proper filename with artist name resolution
        filename = generate_filename(post_data, login, api_key, user_agent)
        output_path = _post_output_path(filename, output_dir, original_image_path, base_search_dir,
                                        preserve_structure)

        # Download file
        headers = {"User-Agent": user_agent}
        response = http.get("e621_files", file_url, headers=headers, timeout=30, stream=True)
        response.raise_for_status()  # Raise an exception for bad status codes

        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192, decode_unicode=False):
                    if chunk:  # Filter out keep-alive new chunks
                        f.write(chunk)

            return output_path
        else:
            print(f"    Error: Download failed with status {response.status_code}")
            return None
    except Exception as e:
        print(f"    Error downloading post: {e}")
        return None


def check_blacklisted_tags(post_data: Dict[str, Any], blacklist: List[str]) -> Optional[List[str]]:
    """
    Check if post contains any blacklisted tags.

    Args:
        post_data: Post data from e621 API
        blacklist: List of blacklisted tag strings

    Returns:
        List of matching blacklisted tags if any are found, None otherwise
    """
    if not blacklist:
        return None

    post = post_data.get('post', {})
    tags = post.get('tags', {})

    # Flatten all tag categories into a single list
    all_tags = []
    for tag_category in tags.values():
        if isinstance(tag_category, list):
            all_tags.extend(tag_category)

    # Convert to lowercase for case-insensitive matching
    all_tags_lower = [tag.lower() for tag in all_tags]
    blacklist_lower = [tag.lower() for tag in blacklist]

    # Find matching blacklisted tags
    matches = [tag for tag in blacklist_lower if tag in all_tags_lower]

    return matches if matches else None
