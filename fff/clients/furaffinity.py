"""FurAffinity access: Firefox cookies, retrying GETs, page scraping, favorites, and downloads."""
import http.cookiejar
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from fff.common import paths
from fff.common.naming import generate_fa_filename

FA_DEBUG_DIR = paths.CACHE_DIR / "fa_debug"

FA_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


FA_MAX_RETRIES = 4


FA_RETRY_BASE_DELAY = 2.0


class FASubmissionNotFoundError(Exception):
    """Raised when a FurAffinity submission doesn't exist in their database (System Error)."""
    pass


def _fa_get_with_retry(
    session: requests.Session,
    url: str,
    headers: Dict[str, str],
    *,
    context: str,
    timeout: int,
    allow_redirects: bool = True,
    stream: bool = False,
) -> Optional[requests.Response]:
    """Perform a GET request with retries for transient FA/CDN failures."""
    for attempt in range(1, FA_MAX_RETRIES + 1):
        try:
            response = session.get(
                url,
                headers=headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
                stream=stream,
            )
        except requests.RequestException as e:
            if attempt == FA_MAX_RETRIES:
                print(f"\n    [DEBUG] {context} request failed after {FA_MAX_RETRIES} attempts: {e}")
                return None

            delay = FA_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(
                f"\n    [WARN] {context} request error ({type(e).__name__}). "
                f"Retrying in {delay:.1f}s ({attempt}/{FA_MAX_RETRIES})"
            )
            time.sleep(delay)
            continue

        if response.status_code in FA_RETRYABLE_STATUS_CODES:
            if attempt == FA_MAX_RETRIES:
                return response

            retry_after = response.headers.get('Retry-After')
            if retry_after and retry_after.isdigit():
                delay = max(float(retry_after), FA_RETRY_BASE_DELAY)
            else:
                delay = FA_RETRY_BASE_DELAY * (2 ** (attempt - 1))

            print(
                f"\n    [WARN] {context} returned status {response.status_code}. "
                f"Retrying in {delay:.1f}s ({attempt}/{FA_MAX_RETRIES})"
            )
            time.sleep(delay)
            continue

        return response

    return None


def get_firefox_cookies() -> Optional[http.cookiejar.CookieJar]:
    """
    Extract FurAffinity cookies from Firefox.
    Looks for cookies.sqlite in common Firefox profile locations.
    """
    try:
        # Common Firefox profile locations
        if sys.platform == 'win32':
            firefox_dir = Path(os.environ.get('APPDATA', '')) / 'Mozilla' / 'Firefox' / 'Profiles'
        elif sys.platform == 'darwin':
            firefox_dir = Path.home() / 'Library' / 'Application Support' / 'Firefox' / 'Profiles'
        else:  # Linux
            firefox_dir = Path.home() / '.mozilla' / 'firefox'

        if not firefox_dir.exists():
            print(f"Firefox profile directory not found: {firefox_dir}")
            return None

        # Find all profile directories
        profiles = [p for p in firefox_dir.iterdir() if p.is_dir()]

        if not profiles:
            print("No Firefox profiles found")
            return None

        # Look for cookies.sqlite in each profile (use the most recently modified)
        cookie_files = []
        for profile in profiles:
            cookie_file = profile / 'cookies.sqlite'
            if cookie_file.exists():
                cookie_files.append(cookie_file)

        if not cookie_files:
            print("No Firefox cookie database found")
            return None

        # Use the most recently modified cookie file
        cookie_file = max(cookie_files, key=lambda p: p.stat().st_mtime)

        print(f"Using Firefox cookies from: {cookie_file.parent.name}")

        # Copy cookie file to temp location (Firefox locks the original)
        temp_cookie_file = Path(os.environ.get('TEMP', '/tmp')) / 'fa_cookies_temp.sqlite'
        shutil.copy2(cookie_file, temp_cookie_file)

        # Extract cookies from SQLite database
        jar = http.cookiejar.CookieJar()
        conn = sqlite3.connect(temp_cookie_file)
        cursor = conn.cursor()

        # Query for FurAffinity cookies
        cursor.execute("""
            SELECT host, name, value, path, expiry, isSecure
            FROM moz_cookies
            WHERE host LIKE '%furaffinity.net%'
        """)

        cookies_found = 0
        for host, name, value, path, expiry, is_secure in cursor.fetchall():
            cookie = http.cookiejar.Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=host,
                domain_specified=True,
                domain_initial_dot=host.startswith('.'),
                path=path,
                path_specified=True,
                secure=bool(is_secure),
                expires=expiry,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False
            )
            jar.set_cookie(cookie)
            cookies_found += 1

        conn.close()
        temp_cookie_file.unlink()

        if cookies_found == 0:
            print("No FurAffinity cookies found in Firefox")
            return None

        print(f"[OK] Loaded {cookies_found} FurAffinity cookie(s)")
        return jar

    except Exception as e:
        print(f"Error extracting Firefox cookies: {e}")
        return None


def get_fa_post_data(post_id: str, cookies: http.cookiejar.CookieJar) -> Optional[Dict[str, Any]]:
    """Get FurAffinity post data by scraping the page."""
    try:
        url = f"https://www.furaffinity.net/view/{post_id}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        session = requests.Session()
        session.cookies = cookies

        response = _fa_get_with_retry(
            session,
            url,
            headers,
            context=f"FA post page {post_id}",
            timeout=30,
        )

        if not response:
            print(f"Failed to fetch post {post_id}: request failed after retries")
            return None

        if response.status_code == 404:
            print(f"Post {post_id} not found (404)")
            return None

        if response.status_code != 200:
            print(f"Failed to fetch post {post_id}: status {response.status_code}")
            return None

        html = response.text

        # Check if submission doesn't exist in database
        if "The submission you are trying to find is not in our database" in html:
            return {'not_found': True, 'post_id': post_id}

        # Check if account is disabled
        if "Account disabled" in html:
            return {'not_found': True, 'post_id': post_id, 'reason': 'account_disabled'}

        # Check if access to account/content is disabled
        if "Access has been disabled to the account and contents of user" in html:
            return {'not_found': True, 'post_id': post_id, 'reason': 'access_disabled'}

        # Extract download link - check multiple CDN domains and patterns
        download_match = re.search(r'<a[^>]+href="(//d\.furaffinity\.net/art/[^"]+)"[^>]*>Download</a>', html, re.IGNORECASE)
        if not download_match:
            download_match = re.search(r'<a[^>]+href="(//d\.facdn\.net/art/[^"]+)"[^>]*>Download</a>', html, re.IGNORECASE)
        if not download_match:
            download_match = re.search(r'<a[^>]+href="(//d2\.facdn\.net/art/[^"]+)"[^>]*>Download</a>', html, re.IGNORECASE)
        if not download_match:
            # Try without the Download text anchor
            download_match = re.search(r'href="(//d\.furaffinity\.net/art/[^"]+)"', html)
        if not download_match:
            download_match = re.search(r'href="(//d\.facdn\.net/art/[^"]+)"', html)
        if not download_match:
            download_match = re.search(r'href="(//d2\.facdn\.net/art/[^"]+)"', html)
        if not download_match:
            # Try data attribute on the image tag
            download_match = re.search(r'data-fullview-src="(//[^"]+)"', html)
        if not download_match:
            # Last resort: check for any CDN link in src attribute
            download_match = re.search(r'src="(//d[0-9]*\.(?:facdn|furaffinity)\.net/art/[^"]+)"', html)

        if not download_match:
            # Save HTML for debugging
            debug_dir = paths.for_env(FA_DEBUG_DIR)  # fff: data/test/... in the test environment
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_file = debug_dir / f'fa_debug_{post_id}_error.html'
            with open(debug_file, 'w', encoding='utf-8') as f:
                f.write(html)
            print(f"\n    [DEBUG] Could not find download link for post {post_id} - saved HTML to {debug_file}")
            # Return minimal data so we don't crash
            return {
                'post_id': post_id,
                'download_url': None,
                'artist': 'unknown',
                'title': f'FA Post {post_id}',
                'rating': 'Unknown',
                'extension': '.jpg',
                'error': 'no_download_link'
            }

        download_url = 'https:' + download_match.group(1)

        # Extract artist and poster information
        artist_in_title = None
        poster_username = None
        title = None

        title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if title_match:
            full_title = title_match.group(1)
            title = full_title

            # Check for [Comm. by username] or [Gift by username] pattern first (these take priority)
            comm_gift_by_bracket = re.search(r'\[(?:comm\.?|gift)\s+by\s+([a-zA-Z0-9_-]+)\]', full_title, re.IGNORECASE)
            if comm_gift_by_bracket:
                artist_in_title = comm_gift_by_bracket.group(1).strip()

            # If not found, check for [By username] pattern (standalone)
            if not artist_in_title:
                by_bracket_match = re.search(r'\[by\s+([a-zA-Z0-9_-]+)\]', full_title, re.IGNORECASE)
                if by_bracket_match:
                    artist_in_title = by_bracket_match.group(1).strip()

            # If not found, check for "comm" or "gift" followed by username in brackets: [Comm. Username] or [Gift Username]
            if not artist_in_title:
                comm_gift_bracket = re.search(r'\[(?:comm\.?|gift)\s+([a-zA-Z0-9_-]+)\]', full_title, re.IGNORECASE)
                if comm_gift_bracket:
                    artist_in_title = comm_gift_bracket.group(1).strip()

            # If not found, check for "comm" or "gift" followed by "for username"
            if not artist_in_title:
                comm_gift_match = re.search(r'\b(?:comm\.?|gift)\s+(?:for\s+)?([a-zA-Z0-9_-]+)', full_title, re.IGNORECASE)
                if comm_gift_match:
                    artist_in_title = comm_gift_match.group(1).strip()

            # If not found, check for artist in parentheses: (art by artist_name)
            if not artist_in_title:
                paren_artist_match = re.search(r'\((?:art\s+)?by\s+([^)]+)\)', full_title, re.IGNORECASE)
                if paren_artist_match:
                    artist_in_title = paren_artist_match.group(1).strip()

        # Get the poster's profile username from the user link (not the display name)
        # The profile username is found in the /user/username/ URL in the submission info section
        # IMPORTANT: Pattern order matters - submission-id-sub-container has the actual poster,
        # while earlier /user/ links in the page are the logged-in user's profile

        # Try in the submission info container FIRST (this is where the poster's info is)
        poster_match = re.search(r'submission-id-sub-container.*?<a href="/user/([^/"]+)/"', html, re.DOTALL)
        if not poster_match:
            # Try looking for username tag
            poster_match = re.search(r'<username><a href="/user/([^/"]+)/">', html)
        if not poster_match:
            # Try info section
            poster_match = re.search(r'class="(?:info|submission-id-container)".*?href="/user/([^/"]+)/"', html, re.DOTALL)
        if not poster_match:
            # Fallback: try to extract from /gallery/ links
            poster_match = re.search(r'href="/gallery/([^/"]+)/', html)
        if not poster_match:
            # Last resort: any user link (may be wrong if it's the logged-in user)
            poster_match = re.search(r'<a href="/user/([^/"]+)/"[^>]*>([^<]+)</a>', html)

        if poster_match:
            poster_username = poster_match.group(1)

        # Build the artist string based on what we found
        if artist_in_title and poster_username:
            # Format: artist_in_title-poster_username
            artist = f"{artist_in_title}-{poster_username}"
        elif artist_in_title:
            # Only artist found in title
            artist = artist_in_title
        elif poster_username:
            # Only poster found
            artist = poster_username
        else:
            # No artist info found
            artist = 'unknown'

        # Extract rating from twitter metadata - it's cleaner than the rating-box
        rating = 'Unknown'
        twitter_rating = re.search(r'<meta name="twitter:data2" content="([^"]+)"', html)
        if twitter_rating:
            rating_text = twitter_rating.group(1).strip()
            if rating_text == 'General':
                rating = 'General'
            elif rating_text == 'Mature':
                rating = 'Mature'
            elif rating_text == 'Adult':
                rating = 'Adult'
        else:
            # Fallback: look for the rating-box class
            rating_match = re.search(r'class="rating-box\s+([^"]+)"', html)
            if rating_match:
                rating_text = rating_match.group(1).lower()
                if 'general' in rating_text:
                    rating = 'General'
                elif 'mature' in rating_text:
                    rating = 'Mature'
                elif 'adult' in rating_text:
                    rating = 'Adult'

        # Get file extension from download URL
        ext = Path(urlparse(download_url).path).suffix

        # Ensure all required fields are present with defaults
        result = {
            'post_id': post_id,
            'download_url': download_url,
            'artist': artist if artist else 'unknown',
            'title': title if title else f'FA Post {post_id}',
            'rating': rating if rating else 'Unknown',
            'extension': ext if ext else '.jpg'
        }

        return result

    except Exception as e:
        print(f"\n    [DEBUG] Error fetching FA post data: {e}")
        import traceback
        traceback.print_exc()
        return None


def add_to_fa_favorites(post_id: str, cookies: http.cookiejar.CookieJar) -> bool:
    """Add a FurAffinity post to favorites."""
    try:
        # First, get the submission page to extract the fav key
        view_url = f"https://www.furaffinity.net/view/{post_id}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        session = requests.Session()
        session.cookies = cookies

        # Get the page to find the fav key
        response = _fa_get_with_retry(
            session,
            view_url,
            headers,
            context=f"FA view page for fav {post_id}",
            timeout=30,
        )

        if not response:
            print(f"\n    [DEBUG] FA view page request failed after retries")
            return False

        if response.status_code != 200:
            print(f"\n    [DEBUG] FA view page returned status {response.status_code}")
            return False

        # Check if submission doesn't exist (System Error)
        if 'System Error' in response.text:
            raise FASubmissionNotFoundError("The submission you are trying to find is not in our database.")

        # Extract the fav key from the page
        fav_key_match = re.search(r'/fav/(\d+)/\?key=([a-f0-9]+)', response.text)

        if not fav_key_match:
            # Maybe already favorited - check for unfav link
            if '/unfav/' in response.text:
                return True  # Already favorited
            print(f"\n    [DEBUG] Could not find fav key in FA page")
            # Try to find any fav-related links for debugging
            fav_links = re.findall(r'(/(un)?fav/[^"\']+)', response.text)
            if fav_links:
                print(f"    [DEBUG] Found fav links: {fav_links[:3]}")
            return False

        post_id_from_page = fav_key_match.group(1)
        fav_key = fav_key_match.group(2)

        # Now favorite with the key
        fav_url = f"https://www.furaffinity.net/fav/{post_id_from_page}/?key={fav_key}"
        headers['Referer'] = view_url

        response = _fa_get_with_retry(
            session,
            fav_url,
            headers,
            context=f"FA favorite request {post_id}",
            timeout=30,
            allow_redirects=True,
        )

        if not response:
            print(f"\n    [DEBUG] FA fav request failed after retries")
            return None

        if response.status_code in FA_RETRYABLE_STATUS_CODES:
            print(f"\n    [DEBUG] FA fav request returned server error {response.status_code}")
            return None

        # Success if we get redirected or 200
        if response.status_code in [200, 302, 303]:
            return True

        print(f"\n    [DEBUG] FA fav request returned status {response.status_code}")
        return False

    except Exception as e:
        print(f"\n    [DEBUG] Error adding to FA favorites: {e}")
        return False


def remove_from_fa_favorites(post_id: str, cookies: http.cookiejar.CookieJar) -> bool:
    """Remove a FurAffinity post from favorites. True if removed or not favorited."""
    try:
        view_url = f"https://www.furaffinity.net/view/{post_id}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        session = requests.Session()
        session.cookies = cookies

        # Get the page to find the unfav key
        response = _fa_get_with_retry(
            session,
            view_url,
            headers,
            context=f"FA view page for unfav {post_id}",
            timeout=30,
        )

        if not response or response.status_code != 200:
            return False

        # Check if submission doesn't exist (System Error)
        if 'System Error' in response.text:
            raise FASubmissionNotFoundError("The submission you are trying to find is not in our database.")

        unfav_key_match = re.search(r'/unfav/(\d+)/\?key=([a-f0-9]+)', response.text)
        if not unfav_key_match:
            # A fav link means it is already not favorited
            return bool(re.search(r'/fav/\d+/\?key=', response.text))

        unfav_url = f"https://www.furaffinity.net/unfav/{unfav_key_match.group(1)}/?key={unfav_key_match.group(2)}"
        headers['Referer'] = view_url

        response = _fa_get_with_retry(
            session,
            unfav_url,
            headers,
            context=f"FA unfavorite request {post_id}",
            timeout=30,
            allow_redirects=True,
        )

        # FA redirects back to the view page, which shows a fav link once removed
        return bool(response and response.status_code == 200 and '/unfav/' not in response.text)

    except FASubmissionNotFoundError:
        raise
    except Exception as e:
        print(f"\n    [DEBUG] Error removing from FA favorites: {e}")
        return False


def check_if_fa_favorited(post_id: str, cookies: http.cookiejar.CookieJar) -> bool:
    """Check if a FA post is already favorited by looking for the unfav link."""
    try:
        url = f"https://www.furaffinity.net/view/{post_id}/"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        session = requests.Session()
        session.cookies = cookies

        response = _fa_get_with_retry(
            session,
            url,
            headers,
            context=f"FA favorite status check {post_id}",
            timeout=30,
        )

        if not response:
            return None

        # Any non-200 (e.g. a 403 when FA blocks us) means the status is unknown, not "not favorited"
        if response.status_code != 200:
            return None

        # Check if submission doesn't exist (System Error)
        if 'System Error' in response.text:
            raise FASubmissionNotFoundError("The submission you are trying to find is not in our database.")

        # If already favorited, page will have "unfav" link instead of "fav" link
        return '/unfav/' in response.text

    except Exception as e:
        print(f"Error checking FA favorites: {e}")
        return None


def download_fa_post(post_data: Dict[str, Any], download_dir: Path, cookies: http.cookiejar.CookieJar,
                    original_image_path: Path = None, base_search_dir: Path = None,
                    preserve_structure: bool = False) -> Optional[Path]:
    """
    Download a FurAffinity post.

    Args:
        post_data: Post data dictionary
        download_dir: Base download directory
        cookies: Firefox cookies for authentication
        original_image_path: Path to original image (for preserving structure)
        base_search_dir: Base search directory (for calculating relative path)
        preserve_structure: If True, preserve subdirectory structure
    """
    try:
        # Check if we have a valid download URL
        if not post_data.get('download_url'):
            print(f"\n    [ERROR] No download URL found in post data")
            if post_data.get('error'):
                print(f"    [ERROR] Reason: {post_data.get('error')}")
            return None

        filename = generate_fa_filename(post_data)

        # Determine output path (with or without subdirectory structure)
        if preserve_structure and original_image_path and base_search_dir:
            try:
                # Calculate relative path from base directory
                relative_path = original_image_path.parent.relative_to(base_search_dir)
                filepath = download_dir / relative_path / filename
                # Create parent directories if needed
                filepath.parent.mkdir(parents=True, exist_ok=True)
                print(f"\n    [DEBUG] Preserving structure: {relative_path}")
                print(f"    [DEBUG] Output path: {filepath}")
                print(f"    [DEBUG] Directory created: {filepath.parent}")
            except ValueError as e:
                # original_image_path is not relative to base_search_dir, use flat structure
                download_dir.mkdir(exist_ok=True)
                filepath = download_dir / filename
                print(f"\n    [DEBUG] Not relative, using flat structure (error: {e})")
                print(f"    [DEBUG] Output path: {filepath}")
        else:
            # Flat structure (original behavior)
            download_dir.mkdir(exist_ok=True)
            filepath = download_dir / filename
            print(f"\n    [DEBUG] Flat structure (preserve={preserve_structure}, orig={original_image_path is not None}, base={base_search_dir is not None})")
            print(f"    [DEBUG] Output path: {filepath}")

        # Skip if already exists
        if filepath.exists():
            return filepath

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': f'https://www.furaffinity.net/view/{post_data["post_id"]}/'
        }

        session = requests.Session()
        session.cookies = cookies

        response = _fa_get_with_retry(
            session,
            post_data['download_url'],
            headers,
            context=f"FA download {post_data['post_id']}",
            timeout=60,
            stream=True,
        )

        if not response:
            print("Failed to download: request failed after retries")
            return None

        response.raise_for_status()  # Raise an exception for bad status codes

        if response.status_code != 200:
            print(f"Failed to download: status {response.status_code}")
            return None

        # Download file
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192, decode_unicode=False):
                if chunk:
                    f.write(chunk)

        return filepath

    except Exception as e:
        print(f"Error downloading FA post: {e}")
        return None
