# fff: implemented in reverse-image search/fluffle.py.
# fff: the reverse search/FuzzySearch clients come from fff.clients (Phase 1, identical code).
# fff: moves/copies are logged to file_operations via fff.fluffle.ops (decision 5).
# fff: fixed: step 4 overwrote responses_dir with e621.responses_directory, so reverse search's JSON
# fff: went to the e621 folder (design note section6). It now goes to search.responses_directory.
"""
reverse-image search Tool
Searches for posts matching local image files using reverse search reverse image search API.
Stores results in MySQL database. Run as 'fff fluffle search'.
"""

import re
import sys
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

import yaml
import mysql.connector
from mysql.connector import Error

from fff.clients.fluffle import resize_image_for_upload, search_image_fluffle  # noqa: F401
from fff.clients.fuzzysearch import search_image_fuzzysearch
from fff.common import config as fff_config
from fff.common.naming import sanitize_filename_component
from fff.fluffle import ops

# fff: the original imported these from scripts/ inside try/except ImportError
from fff.fluffle.e621_matches import process_single_image, get_all_favorites
from fff.fluffle.fa_matches import process_single_fa_image, get_firefox_cookies


def load_config(config_path=None):
    """fff: the reverse search config view (database = image_search, or test_image_search in test).
    An explicit path loads that YAML the way the original did."""
    if config_path is None:
        return fff_config.legacy_view("fluffle")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def move_to_not_found(image_path: Path, not_found_dir: Path,
                      base_search_dir: Path = None, preserve_structure: bool = False,
                      reason: str = "not_found") -> bool:
    """Move image to not_found (or deleted_source) directory when no matches are found (logged)."""
    return ops.move_to_directory(image_path, not_found_dir, base_search_dir, preserve_structure,
                                 reason=reason, error_label="Error moving file")


def ensure_directories_exist(config: Dict[str, Any]) -> None:
    """Create all destination directories specified in config if they don't exist."""
    directories_to_create = []

    # Search directories
    search_config = config.get('search', {})
    if 'responses_directory' in search_config:
        directories_to_create.append(search_config['responses_directory'])
    if 'not_found_directory' in search_config:
        directories_to_create.append(search_config['not_found_directory'])
    if 'deleted_source_directory' in search_config:
        directories_to_create.append(search_config['deleted_source_directory'])
    if 'source_found_directory' in search_config:
        directories_to_create.append(search_config['source_found_directory'])
    if 'other_sourced_directory' in search_config:
        directories_to_create.append(search_config['other_sourced_directory'])
    if 'other_sourced_backup_directory' in search_config:
        directories_to_create.append(search_config['other_sourced_backup_directory'])

    # e621 directories
    e621_config = config.get('e621', {})
    if 'download_directory' in e621_config:
        directories_to_create.append(e621_config['download_directory'])
    if 'source_found_directory' in e621_config:
        directories_to_create.append(e621_config['source_found_directory'])
    if 'blacklisted_directory' in e621_config:
        directories_to_create.append(e621_config['blacklisted_directory'])
    if 'responses_directory' in e621_config:
        directories_to_create.append(e621_config['responses_directory'])

    # FurAffinity directories
    fa_config = config.get('furaffinity', {})
    if 'download_directory' in fa_config:
        directories_to_create.append(fa_config['download_directory'])
    if 'source_found_directory' in fa_config:
        directories_to_create.append(fa_config['source_found_directory'])

    # Create directories
    created_count = 0
    for dir_path in directories_to_create:
        if dir_path:
            path = Path(dir_path)
            if not path.exists():
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    print(f"[OK] Created directory: {path}")
                    created_count += 1
                except Exception as e:
                    print(f"[WARNING] Warning: Could not create directory {path}: {e}")

    if created_count > 0:
        print()


def get_image_files(directory: str, extensions: List[str], recursive: bool = False,
                   exclude_dirs: List[str] = None, ignore_filename_filter: bool = False) -> List[Path]:
    """Get all image files from the specified directory."""
    directory_path = Path(directory)

    if not directory_path.exists():
        print(f"Error: Directory '{directory}' does not exist.")
        return []

    if not directory_path.is_dir():
        print(f"Error: '{directory}' is not a directory.")
        return []

    # Normalize extensions to lowercase and ensure they start with a dot
    extensions = [ext.lower() if ext.startswith('.') else f'.{ext.lower()}' for ext in extensions]

    # Normalize excluded directory names to lowercase for case-insensitive matching
    exclude_dirs_lower = [d.lower() for d in exclude_dirs] if exclude_dirs else []

    image_files = []
    glob_pattern = '**/*' if recursive else '*'

    for ext in extensions:
        if recursive:
            image_files.extend(directory_path.rglob(f'*{ext}'))
            image_files.extend(directory_path.rglob(f'*{ext.upper()}'))
        else:
            image_files.extend(directory_path.glob(f'*{ext}'))
            image_files.extend(directory_path.glob(f'*{ext.upper()}'))

    # Filter out files in excluded directories (if recursive search)
    if recursive and exclude_dirs_lower:
        filtered_by_dir = []
        for file_path in image_files:
            # Check if any parent directory name contains excluded names (substring match)
            parent_names = [p.name.lower() for p in file_path.parents]
            if not any(excluded_dir in parent_name for parent_name in parent_names for excluded_dir in exclude_dirs_lower):
                filtered_by_dir.append(file_path)
            else:
                print(f"Skipping file in excluded directory: {file_path}")
        image_files = filtered_by_dir

    # Remove duplicates
    unique_files = list(set(image_files))

    # Filter out files with rating tags or -FA- in the filename (unless disabled)
    if ignore_filename_filter:
        return sorted(unique_files)
    else:
        excluded_patterns = ['explicit', 'questionable', 'safe', '-FA-']
        filtered_files = []
        for file_path in unique_files:
            filename_lower = file_path.name.lower()
            if not any(pattern.lower() in filename_lower for pattern in excluded_patterns):
                filtered_files.append(file_path)
            else:
                print(f"Skipping file with excluded pattern: {file_path.name}")

        return sorted(filtered_files)


def init_database(db_config: Dict[str, Any]) -> Optional[mysql.connector.connection.MySQLConnection]:
    """Initialize database connection and create tables if they don't exist."""
    try:
        connection = mysql.connector.connect(
            host=db_config.get('host', 'localhost'),
            user=db_config.get('user'),
            password=db_config.get('password'),
            database=db_config.get('database')
        )

        if connection.is_connected():
            cursor = connection.cursor()

            # Create base images table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS images (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    filename VARCHAR(500) NOT NULL,
                    filepath TEXT NOT NULL,
                    file_size BIGINT,
                    width INT,
                    height INT,
                    search_timestamp DATETIME NOT NULL,
                    total_matches INT DEFAULT 0,
                    exact_matches INT DEFAULT 0,
                    raw_response JSON,
                    UNIQUE KEY idx_filename (filename),
                    INDEX idx_search_timestamp (search_timestamp)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create e621 links table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS e621_links (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    image_id INT NOT NULL,
                    post_id VARCHAR(100) NOT NULL,
                    post_url TEXT NOT NULL,
                    distance FLOAT,
                    match_quality VARCHAR(20),
                    author_id VARCHAR(200),
                    author_name VARCHAR(200),
                    is_sfw BOOLEAN,
                    thumbnail_url TEXT,
                    match_data JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
                    INDEX idx_image_id (image_id),
                    INDEX idx_post_id (post_id),
                    INDEX idx_match_quality (match_quality)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create furAffinity links table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS furaffinity_links (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    image_id INT NOT NULL,
                    post_id VARCHAR(100) NOT NULL,
                    post_url TEXT NOT NULL,
                    distance FLOAT,
                    match_quality VARCHAR(20),
                    author_id VARCHAR(200),
                    author_name VARCHAR(200),
                    is_sfw BOOLEAN,
                    thumbnail_url TEXT,
                    match_data JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
                    INDEX idx_image_id (image_id),
                    INDEX idx_post_id (post_id),
                    INDEX idx_match_quality (match_quality)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create weasyl links table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS weasyl_links (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    image_id INT NOT NULL,
                    post_id VARCHAR(100) NOT NULL,
                    post_url TEXT NOT NULL,
                    distance FLOAT,
                    match_quality VARCHAR(20),
                    author_id VARCHAR(200),
                    author_name VARCHAR(200),
                    is_sfw BOOLEAN,
                    thumbnail_url TEXT,
                    match_data JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
                    INDEX idx_image_id (image_id),
                    INDEX idx_post_id (post_id),
                    INDEX idx_match_quality (match_quality)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create twitter links table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS twitter_links (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    image_id INT NOT NULL,
                    post_id VARCHAR(100) NOT NULL,
                    post_url TEXT NOT NULL,
                    distance FLOAT,
                    match_quality VARCHAR(20),
                    author_id VARCHAR(200),
                    author_name VARCHAR(200),
                    is_sfw BOOLEAN,
                    thumbnail_url TEXT,
                    match_data JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
                    INDEX idx_image_id (image_id),
                    INDEX idx_post_id (post_id),
                    INDEX idx_match_quality (match_quality)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create bluesky links table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bluesky_links (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    image_id INT NOT NULL,
                    post_id VARCHAR(100) NOT NULL,
                    post_url TEXT NOT NULL,
                    distance FLOAT,
                    match_quality VARCHAR(20),
                    author_id VARCHAR(200),
                    author_name VARCHAR(200),
                    is_sfw BOOLEAN,
                    thumbnail_url TEXT,
                    match_data JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
                    INDEX idx_image_id (image_id),
                    INDEX idx_post_id (post_id),
                    INDEX idx_match_quality (match_quality)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create deviantArt links table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS deviantart_links (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    image_id INT NOT NULL,
                    post_id VARCHAR(100) NOT NULL,
                    post_url TEXT NOT NULL,
                    distance FLOAT,
                    match_quality VARCHAR(20),
                    author_id VARCHAR(200),
                    author_name VARCHAR(200),
                    is_sfw BOOLEAN,
                    thumbnail_url TEXT,
                    match_data JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
                    INDEX idx_image_id (image_id),
                    INDEX idx_post_id (post_id),
                    INDEX idx_match_quality (match_quality)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # Create other_links table for platforms not listed above
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS other_links (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    image_id INT NOT NULL,
                    platform VARCHAR(100) NOT NULL,
                    post_id VARCHAR(100) NOT NULL,
                    post_url TEXT NOT NULL,
                    distance FLOAT,
                    match_quality VARCHAR(20),
                    author_id VARCHAR(200),
                    author_name VARCHAR(200),
                    is_sfw BOOLEAN,
                    thumbnail_url TEXT,
                    match_data JSON,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
                    INDEX idx_image_id (image_id),
                    INDEX idx_platform (platform),
                    INDEX idx_post_id (post_id),
                    INDEX idx_match_quality (match_quality)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            connection.commit()
            cursor.close()
            print("[OK] Database tables initialized successfully\n")
            return connection

    except Error as e:
        print(f"Error connecting to MySQL database: {e}")
        return None


def store_search_results(connection: mysql.connector.connection.MySQLConnection,
                        image_path: Path,
                        results: Dict[str, Any],
                        save_json: bool = True,
                        responses_dir: str = "responses") -> bool:
    """Store search results in the database and optionally save JSON response."""
    try:
        cursor = connection.cursor()

        # Extract matches from results
        matches = results.get('results', results.get('matches', []))
        if isinstance(matches, dict):
            matches = [matches]

        # Filter to ONLY exact matches - do not store unlikely or likely matches
        exact_matches = [m for m in matches if m.get('match') == 'exact']
        total_matches_found = len(matches)

        # Only store exact matches in the database
        matches_to_store = exact_matches

        # Get file info
        file_size = image_path.stat().st_size if image_path.exists() else None

        # Try to get image dimensions (optional - requires PIL/Pillow)
        width, height = None, None
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                width, height = img.size
        except:
            pass  # PIL not available or image can't be opened

        # Insert or update image record
        image_query = """
            INSERT INTO images
            (filename, filepath, file_size, width, height, search_timestamp,
             total_matches, exact_matches, raw_response)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                filepath = VALUES(filepath),
                file_size = VALUES(file_size),
                width = VALUES(width),
                height = VALUES(height),
                search_timestamp = VALUES(search_timestamp),
                total_matches = VALUES(total_matches),
                exact_matches = VALUES(exact_matches),
                raw_response = VALUES(raw_response)
        """
        image_data = (
            image_path.name,
            str(image_path),
            file_size,
            width,
            height,
            datetime.now(),
            total_matches_found,  # Total from API
            len(exact_matches),   # Exact matches count
            json.dumps(results)
        )
        cursor.execute(image_query, image_data)
        image_id = cursor.lastrowid

        # If this was an update, get the existing image_id
        if image_id == 0:
            cursor.execute("SELECT id FROM images WHERE filename = %s", (image_path.name,))
            result = cursor.fetchone()
            if result:
                image_id = result[0]

        # Platform table mapping
        platform_tables = {
            'e621': 'e621_links',
            'furAffinity': 'furaffinity_links',
            'furaffinity': 'furaffinity_links',
            'weasyl': 'weasyl_links',
            'twitter': 'twitter_links',
            'bluesky': 'bluesky_links',
            'deviantArt': 'deviantart_links',
            'deviantart': 'deviantart_links',
        }

        # Insert platform-specific link records (ONLY EXACT MATCHES)
        if matches_to_store:
            for match in matches_to_store:
                platform = match.get('platform', 'unknown')
                post_id = match.get('id', '')
                post_url = match.get('url', '')
                distance = match.get('distance', 0)
                match_quality = match.get('match', 'unknown')

                # Get thumbnail URL
                thumbnail_data = match.get('thumbnail', {})
                if isinstance(thumbnail_data, dict):
                    thumbnail_url = thumbnail_data.get('url', '')
                else:
                    thumbnail_url = str(thumbnail_data) if thumbnail_data else ''

                # Get author information with platform-aware handling
                platform_value = match.get('platform', 'unknown')
                platform_lower = str(platform_value).lower()

                authors = match.get('authors') or []
                author_entry: Any = None
                if isinstance(authors, list) and authors:
                    author_entry = authors[0]
                elif isinstance(authors, dict):
                    author_entry = authors
                elif isinstance(authors, str):
                    author_entry = authors

                author_id = ''
                author_name = ''
                if isinstance(author_entry, dict):
                    if platform_lower == 'twitter':
                        author_id = author_entry.get('id') or author_entry.get('handle') or ''
                        author_name = author_entry.get('handle') or author_entry.get('name') or author_entry.get('id') or ''
                    elif platform_lower == 'bluesky':
                        author_id = author_entry.get('id') or author_entry.get('handle') or ''
                        author_name = author_entry.get('handle') or author_entry.get('name') or author_entry.get('id') or ''
                    else:
                        author_id = author_entry.get('id') or ''
                        author_name = author_entry.get('name') or author_entry.get('handle') or author_id
                elif isinstance(author_entry, str):
                    author_name = author_entry

                if platform_lower == 'twitter' and isinstance(author_name, str):
                    author_name = author_name.lstrip('@')

                is_sfw = bool(match.get('isSfw', False))

                table_name = (
                    platform_tables.get(platform)
                    or platform_tables.get(platform_lower)
                    or 'other_links'
                )

                if table_name == 'other_links':
                    link_query = f"""
                        INSERT INTO {table_name}
                        (image_id, platform, post_id, post_url, distance, match_quality,
                         author_id, author_name, is_sfw, thumbnail_url, match_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    link_data = (
                        image_id,
                        platform,
                        str(post_id),
                        post_url,
                        float(distance) if distance else None,
                        match_quality,
                        author_id,
                        author_name,
                        is_sfw,
                        thumbnail_url,
                        json.dumps(match)
                    )
                else:
                    link_query = f"""
                        INSERT INTO {table_name}
                        (image_id, post_id, post_url, distance, match_quality,
                         author_id, author_name, is_sfw, thumbnail_url, match_data)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    link_data = (
                        image_id,
                        str(post_id),
                        post_url,
                        float(distance) if distance else None,
                        match_quality,
                        author_id,
                        author_name,
                        is_sfw,
                        thumbnail_url,
                        json.dumps(match)
                    )

                cursor.execute(link_query, link_data)

        connection.commit()
        cursor.close()

        # Optionally save JSON response to file
        if save_json and responses_dir:
            try:
                responses_path = Path(responses_dir)
                responses_path.mkdir(exist_ok=True)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                json_filename = f"{image_path.stem}_{timestamp}.json"
                json_path = responses_path / json_filename

                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"    Warning: Could not save JSON response: {e}")

        if len(exact_matches) > 0:
            print(f"    [OK] Stored {len(exact_matches)} exact match(es)")
        else:
            print(f"    [i] No exact matches found")

        # Check if there's an e621 exact match
        e621_post_id = None
        fa_post_id = None
        for match in exact_matches:
            platform = match.get('platform', '').lower()
            if platform == 'e621' and not e621_post_id:
                e621_post_id = match.get('id', '')
            elif platform in ['furaffinity', 'furaffinity'] and not fa_post_id:
                fa_post_id = match.get('id', '')

        return True, e621_post_id, fa_post_id, image_id

    except Error as e:
        print(f"    Error storing results in database: {e}")
        connection.rollback()
        return False, None, None, None


def get_processed_images(connection: mysql.connector.connection.MySQLConnection) -> set:
    """Get set of already processed image filenames from database."""
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT DISTINCT filename FROM images")
        processed = {row[0] for row in cursor.fetchall()}
        cursor.close()
        return processed
    except Error as e:
        print(f"Warning: Could not retrieve processed images: {e}")
        return set()


def delete_existing_links(connection: mysql.connector.connection.MySQLConnection,
                         image_filename: str) -> bool:
    """
    Delete existing platform link entries for a reprocessed image.

    Args:
        connection: MySQL database connection
        image_filename: Name of the image file being reprocessed

    Returns:
        True if successful, False otherwise
    """
    try:
        cursor = connection.cursor()

        # Get image_id for this filename
        cursor.execute("SELECT id FROM images WHERE filename = %s", (image_filename,))
        result = cursor.fetchone()

        if not result:
            cursor.close()
            return True  # No existing record, nothing to delete

        image_id = result[0]

        # Delete from all platform link tables
        platform_tables = [
            'e621_links',
            'furaffinity_links',
            'weasyl_links',
            'twitter_links',
            'bluesky_links',
            'deviantart_links',
            'other_links'
        ]

        for table in platform_tables:
            delete_query = f"DELETE FROM {table} WHERE image_id = %s"
            cursor.execute(delete_query, (image_id,))

        connection.commit()
        cursor.close()
        return True

    except Error as e:
        print(f"Warning: Could not delete existing links for {image_filename}: {e}")
        try:
            connection.rollback()
        except:
            pass
        return False


def rename_and_move_to_other_sourced(image_path: Path,
                                     other_sourced_dir: Path,
                                     match: Dict[str, Any],
                                     base_search_dir: Path = None,
                                     preserve_structure: bool = False,
                                     backup_dir: Path = None) -> Optional[Path]:
    """Rename and move file for other platform sources."""
    import re  # Import at function level to ensure it's always available

    try:
        # Create backup of current file if backup_dir is provided
        if backup_dir:
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_destination = backup_dir / image_path.name
            # Handle duplicate filenames in backup
            backup_counter = 1
            original_backup = backup_destination
            while backup_destination.exists():
                backup_destination = original_backup.with_name(
                    f"{original_backup.stem}_{backup_counter}{original_backup.suffix}"
                )
                backup_counter += 1
            ops.copy(image_path, backup_destination, "other_sourced_backup")

        platform_value = match.get('platform', 'unknown')
        platform_lower = str(platform_value).lower()

        authors = match.get('authors') or []
        author_entry: Any = None
        if isinstance(authors, list) and authors:
            author_entry = authors[0]
        elif isinstance(authors, dict):
            author_entry = authors
        elif isinstance(authors, str):
            author_entry = authors

        author_raw = 'unknown'
        if isinstance(author_entry, dict):
            if platform_lower == 'twitter':
                author_raw = author_entry.get('handle') or author_entry.get('id') or author_entry.get('name') or 'unknown'
            elif platform_lower == 'bluesky':
                author_raw = author_entry.get('author_name') or author_entry.get('name') or author_entry.get('handle') or 'unknown'
            else:
                author_raw = author_entry.get('name') or author_entry.get('id') or author_entry.get('handle') or 'unknown'
        elif isinstance(author_entry, str):
            author_raw = author_entry

        # Extract Twitter username from URL - prioritize URL over database values
        if platform_lower == 'twitter':
            # Check if there's a URL in the match data
            url = match.get('url') or match.get('post_url') or match.get('source_url') or ''
            if isinstance(url, str) and ('twitter.com/' in url or 'x.com/' in url):
                # Extract username from URL like https://twitter.com/username/status/...
                url_match = re.search(r'(?:twitter\.com|x\.com)/([^/]+)/status/', url)
                if url_match:
                    author_raw = url_match.group(1)

        # Clean up Twitter handles
        if platform_lower == 'twitter' and isinstance(author_raw, str):
            author_raw = author_raw.lstrip('@')

        # Clean up Bluesky handles and DIDs
        if platform_lower == 'bluesky' and isinstance(author_raw, str):
            # Remove @ prefix if present
            author_raw = author_raw.lstrip('@')
            # If it's a DID, extract just the last part or mark as unknown
            if author_raw.startswith('did:'):
                # DIDs are not user-friendly, prefer to mark as unknown if no handle available
                author_raw = 'unknown'
            # Remove .bsky.social suffix from handles for cleaner filenames
            elif author_raw.endswith('.bsky.social'):
                author_raw = author_raw[:-12]  # Remove '.bsky.social'

            # Remove emojis from username
            # Emoji ranges: https://en.wikipedia.org/wiki/Unicode_block
            emoji_pattern = re.compile(
                "["
                "\U0001F600-\U0001F64F"  # emoticons
                "\U0001F300-\U0001F5FF"  # symbols & pictographs
                "\U0001F680-\U0001F6FF"  # transport & map symbols
                "\U0001F1E0-\U0001F1FF"  # flags (iOS)
                "\U00002702-\U000027B0"  # dingbats
                "\U000024C2-\U0001F251"  # enclosed characters
                "\U0001F900-\U0001F9FF"  # supplemental symbols and pictographs
                "\U0001FA00-\U0001FA6F"  # chess symbols
                "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-A
                "\U00002600-\U000026FF"  # miscellaneous symbols
                "\U00002B50"              # star
                "]+",
                flags=re.UNICODE
            )
            author_raw = emoji_pattern.sub('', author_raw).strip()
            # If empty after emoji removal, use 'unknown'
            if not author_raw:
                author_raw = 'unknown'

        author_component = sanitize_filename_component(author_raw)
        platform_component = sanitize_filename_component(platform_lower)

        # Get post ID - use post_id field if available for Bluesky/Twitter
        post_id_raw = match.get('id', 'unknown')
        if platform_lower in ('bluesky', 'twitter'):
            # Use post_id field if available
            post_id_raw = match.get('post_id') or match.get('id', 'unknown')

        if platform_lower == 'bluesky' and isinstance(post_id_raw, str):
            # Bluesky post IDs might contain full URL path - extract just the post ID
            # Format is often: at://did:plc:.../app.bsky.feed.post/postid
            # or bluesky_did_plc_xxx-postid-number
            if '/' in post_id_raw:
                post_id_raw = post_id_raw.split('/')[-1]  # Get last part after final slash
            elif post_id_raw.startswith('bluesky_did_'):
                # Format: bluesky_did_plc_xxx-postid-number
                # Extract the postid which is the second-to-last segment
                parts = post_id_raw.split('-')
                if len(parts) >= 2:
                    # The actual post ID is typically the second-to-last part (before the image number)
                    post_id_raw = parts[-2]

        post_component = sanitize_filename_component(post_id_raw)

        # Avoid redundant platform suffixes - remove platform from author if present
        lowered_author = author_component.lower()
        lowered_platform = platform_component.lower()

        # Check for platform at the end with separator
        for sep in ('-', '_'):
            suffix = f"{sep}{lowered_platform}"
            if lowered_author.endswith(suffix):
                trimmed = author_component[:-len(suffix)]
                author_component = trimmed or 'unknown'
                lowered_author = author_component.lower()
                break

        # Check for platform at the beginning with separator
        for sep in ('-', '_'):
            prefix = f"{lowered_platform}{sep}"
            if lowered_author.startswith(prefix):
                trimmed = author_component[len(prefix):]
                author_component = trimmed or 'unknown'
                lowered_author = author_component.lower()
                break

        # If author equals platform, use 'unknown'
        if lowered_author == lowered_platform:
            author_component = 'unknown'

        # For Twitter, Bluesky, and Inkbunny, omit platform component since post_id already indicates source
        if platform_lower in ('bluesky', 'twitter', 'inkbunny'):
            new_filename = f"{author_component}-{post_component}{image_path.suffix}"
        else:
            new_filename = f"{author_component}-{platform_component}-{post_component}{image_path.suffix}"

        # Determine destination directory
        destination_dir = other_sourced_dir
        if preserve_structure and base_search_dir:
            try:
                relative_dir = image_path.parent.relative_to(base_search_dir)
                destination_dir = other_sourced_dir / relative_dir
                destination_dir.mkdir(parents=True, exist_ok=True)
            except ValueError:
                destination_dir = other_sourced_dir
                destination_dir.mkdir(parents=True, exist_ok=True)
        else:
            destination_dir.mkdir(parents=True, exist_ok=True)

        destination = destination_dir / new_filename
        original_destination = destination
        counter = 1
        while destination.exists():
            destination = original_destination.with_name(
                f"{original_destination.stem}_{counter}{original_destination.suffix}"
            )
            counter += 1

        ops.move(image_path, destination, "other_sourced")
        return destination
    except Exception as e:
        print(f"    Error moving file to other sourced: {e}")
        return None


def cleanup_old_responses(responses_dir: str, max_age_days: int = 5) -> None:
    """Delete response JSON files older than max_age_days from the responses directory."""
    responses_path = Path(responses_dir)
    if not responses_path.exists():
        return

    cutoff = datetime.now().timestamp() - (max_age_days * 86400)
    deleted_count = 0

    for json_file in responses_path.glob('*.json'):
        try:
            if json_file.stat().st_mtime < cutoff:
                json_file.unlink()
                deleted_count += 1
        except Exception as e:
            print(f"    [WARNING] Could not delete old response file {json_file.name}: {e}")

    if deleted_count > 0:
        print(f"Cleaned up {deleted_count} response file(s) older than {max_age_days} days.")


def main(argv=None):  # fff: argv accepted for the CLI; no options
    """Main function to run the image search."""
    print("reverse-image search Tool")
    print("=" * 80)

    # Load configuration
    config = load_config()

    # Ensure all destination directories exist
    ensure_directories_exist(config)

    db_config = config.get('database', {})
    search_config = config.get('search', {})
    e621_config = config.get('e621', {})
    fuzzysearch_config = config.get('fuzzysearch', {})

    # Extract configuration values
    # Support both old (image_directory) and new (image_directories) config formats
    image_directories = search_config.get('image_directories')
    if image_directories is None:
        # Fall back to old single directory format
        image_directory = search_config.get('image_directory', './images')
        image_directories = [image_directory]
    elif isinstance(image_directories, str):
        # If it's a string instead of a list, convert it
        image_directories = [image_directories]

    search_delay = search_config.get('delay_between_searches', 2.0)
    max_results = search_config.get('max_results_per_image', 10)
    save_json = search_config.get('save_json_responses', True)
    responses_dir = search_config.get('responses_directory', './responses')
    file_extensions = search_config.get('file_extensions', ['.jpg', '.png', '.gif', '.webp'])
    not_found_directory = search_config.get('not_found_directory', './not_found')
    deleted_source_directory = search_config.get('deleted_source_directory', './deleted_source')
    other_sourced_directory = search_config.get('other_sourced_directory', './other_sourced')
    other_sourced_backup_directory = search_config.get('other_sourced_backup_directory', './other_sourced_backup')
    preserve_structure = search_config.get('preserve_directory_structure', False)
    move_not_found = search_config.get('move_not_found', True)
    replace_with_source = search_config.get('replace_with_source', False)
    allow_reprocessing = search_config.get('allow_reprocessing', False)
    # Use first directory as base for structure preservation
    base_search_dir = Path(image_directories[0])
    not_found_dir = Path(not_found_directory)
    deleted_source_dir = Path(deleted_source_directory)
    other_sourced_dir = Path(other_sourced_directory)
    other_sourced_backup_dir = Path(other_sourced_backup_directory)

    # FuzzySearch config
    fuzzysearch_enabled = fuzzysearch_config.get('enabled', False)
    fuzzysearch_api_key = fuzzysearch_config.get('api_key', '')
    fuzzysearch_distance = fuzzysearch_config.get('distance', 3)

    # Initialize database
    print("\n" + "=" * 80)
    print("STEP 1: Database Connection")
    print("=" * 80)
    print("Connecting to database...")
    connection = init_database(db_config)
    if not connection:
        print("Failed to initialize database. Exiting.")
        return

    # Get image files
    print("\n" + "=" * 80)
    print("STEP 2: Scanning for Image Files")
    print("=" * 80)
    recursive_search = search_config.get('recursive_search', False)
    exclude_dirs = search_config.get('exclude_directories', [])
    ignore_filename_filter = search_config.get('ignore_filename_filter', False)

    # Scan all configured directories
    image_files = []
    for image_directory in image_directories:
        print(f"Directory: {image_directory}")
        if recursive_search:
            print("  (Recursive search enabled)")
            if exclude_dirs:
                print(f"  (Excluding directories: {', '.join(exclude_dirs)})")
        if ignore_filename_filter:
            print("  (Filename filtering disabled)")
        dir_files = get_image_files(image_directory, file_extensions, recursive_search, exclude_dirs, ignore_filename_filter)
        image_files.extend(dir_files)
        print(f"  Found {len(dir_files)} images in this directory")

    print(f"\nTotal images found across all directories: {len(image_files)}")

    if not image_files:
        print("\n[!] No image files found in directory.")
        connection.close()
        return

    # Check for already processed images
    print("\n" + "=" * 80)
    print("STEP 3: Checking Database for Already Processed Images")
    print("=" * 80)
    processed_images = get_processed_images(connection)

    if allow_reprocessing:
        # When reprocessing is enabled, process all files
        remaining_images = image_files
        reprocess_images = {img.name for img in image_files if img.name in processed_images}

        print(f"Found {len(image_files)} image file(s)")
        print(f"Reprocessing mode: ENABLED")
        if reprocess_images:
            print(f"Files to reprocess: {len(reprocess_images)}")
            print(f"New files: {len(image_files) - len(reprocess_images)}\n")
        else:
            print(f"All files are new (none previously processed)\n")
    else:
        # Normal mode: skip already processed files
        remaining_images = [img for img in image_files if img.name not in processed_images]
        reprocess_images = set()

        print(f"Found {len(image_files)} image file(s)")
        if processed_images:
            print(f"Already processed: {len(processed_images)}")
            print(f"Remaining to process: {len(remaining_images)}\n")
        else:
            print()

        if not remaining_images:
            print("\n[!] All images have already been processed!")
            print("Tip: Set 'allow_reprocessing: true' in config to reprocess files.")
            connection.close()
            return

    # Fetch e621 favorites cache if processing is available (only after confirming we have images)
    print("\n" + "=" * 80)
    print("STEP 4: Loading External Resources (if needed)")
    print("=" * 80)

    favorites_cache = None
    if process_single_image and get_all_favorites:
        user_agent = e621_config.get('user_agent', 'E621PostProcessor/1.0')
        login = e621_config.get('login')
        api_key = e621_config.get('api_key')
        e621_responses_dir = e621_config.get('responses_directory')  # fff: was responses_dir (design note section6 bug)

        if login and api_key:
            # Decide whether to fetch all favorites or check individually
            # Threshold: more than 50 files = bulk fetch, 50 or fewer = individual checks
            FAVORITES_THRESHOLD = 300
            num_images_to_process = len(remaining_images)

            if num_images_to_process > FAVORITES_THRESHOLD:
                print(f"Bulk mode: Loading e621 favorites cache (more than {FAVORITES_THRESHOLD} files)")
                favorites_cache = get_all_favorites(login, api_key, user_agent)
            else:
                print(f"Individual mode: Will check e621 favorites per-post ({num_images_to_process} files <= {FAVORITES_THRESHOLD})")
        else:
            print("[i] e621 credentials not configured - favorites caching disabled")

    # Load FA cookies if processing is available (only after confirming we have images)
    fa_cookies = None
    if process_single_fa_image and get_firefox_cookies:
        print("Loading FurAffinity cookies...")
        fa_cookies = get_firefox_cookies()
        if not fa_cookies:
            print("[WARNING] Could not load FA cookies - FA processing disabled")

    print("\n" + "=" * 80)
    print("STEP 5: Starting Image Search")
    print("=" * 80)

    print(f"Search delay: {search_delay}s between requests")
    print(f"Max results per image: {max_results}")
    print(f"Saving JSON responses: {'Yes' if save_json else 'No'}")
    print(f"Database storage: EXACT MATCHES ONLY\n")

    # Search for each image
    exact_matches_count = 0
    successful_searches = 0
    e621_processed = 0
    fa_processed = 0
    other_processed = 0
    fuzzysearch_processed = 0

    for i, image_path in enumerate(remaining_images, 1):
        # Skip if file no longer exists
        if not image_path.exists():
            print(f"[{i}/{len(remaining_images)}]")
            print(f"  Skipping: {image_path.name} (file no longer exists)\n")
            continue

        # If reprocessing this file, delete old link entries first
        if allow_reprocessing and image_path.name in reprocess_images:
            delete_existing_links(connection, image_path.name)

        print(f"[{i}/{len(remaining_images)}]")
        if allow_reprocessing and image_path.name in reprocess_images:
            print(f"  (REPROCESSING)")

        # Search using reverse search API
        results = search_image_fluffle(image_path, limit=max_results)

        if results:
            # Store results in database (only exact matches are stored)
            store_result = store_search_results(connection, image_path, results, save_json, responses_dir)

            if isinstance(store_result, tuple) and len(store_result) == 4:
                success, e621_post_id, fa_post_id, image_id = store_result
            elif isinstance(store_result, tuple) and len(store_result) == 3:
                success, e621_post_id, fa_post_id = store_result
                image_id = None
            elif isinstance(store_result, tuple) and len(store_result) == 2:
                # Backwards compatibility
                success, e621_post_id = store_result
                fa_post_id = None
                image_id = None
            else:
                # Backwards compatibility
                success = store_result
                e621_post_id = None
                fa_post_id = None
                image_id = None

            if success:
                successful_searches += 1

                # Count exact matches only
                all_matches = results.get('results', results.get('matches', []))
                if isinstance(all_matches, dict):
                    all_matches = [all_matches]

                exact_match_entries = [
                    m for m in all_matches
                    if isinstance(m, dict) and m.get('match') == 'exact'
                ]
                exact_in_result = len(exact_match_entries)
                exact_matches_count += exact_in_result

                # Process e621 match if found
                if e621_post_id and process_single_image:
                    print(f"    -> e621 match found, processing...")
                    try:
                        if process_single_image(image_path, e621_post_id, config,
                                               favorites_cache=favorites_cache,
                                               base_search_dir=base_search_dir,
                                               preserve_structure=preserve_structure):
                            e621_processed += 1
                            print(f"    [OK] e621 processing complete")
                    except Exception as e:
                        print(f"    [ERROR] e621 processing failed: {e}")
                # If no e621 match, but FA match found, process FA
                elif fa_post_id and process_single_fa_image and fa_cookies:
                    print(f"    -> FA match found (no e621), processing...")
                    try:
                        # Extract numeric ID from FA post ID (remove furAffinity_ or fa_ prefix)
                        numeric_fa_id = fa_post_id.replace('furAffinity_', '').replace('fa_', '')

                        # Get the FA match data from results for fallback info
                        fa_match_data = None
                        for match in all_matches:
                            if match.get('platform', '').lower() in ['furaffinity', 'furaffinity']:
                                if str(match.get('id', '')).replace('furAffinity_', '').replace('fa_', '') == numeric_fa_id:
                                    fa_match_data = match
                                    break

                        fa_result = process_single_fa_image(image_path, numeric_fa_id, config,
                                                   cookies=fa_cookies,
                                                   image_id=image_id,
                                                   connection=connection,
                                                   base_search_dir=base_search_dir,
                                                   preserve_structure=preserve_structure,
                                                   fluffle_match_data=fa_match_data)

                        # Handle deleted FA posts (can be string 'deleted' or tuple ('deleted', new_path))
                        if fa_result == 'deleted' or (isinstance(fa_result, tuple) and fa_result[0] == 'deleted'):
                            # Extract renamed path if available
                            file_to_move = fa_result[1] if isinstance(fa_result, tuple) else image_path

                            # FA post was deleted - move to deleted_source directory
                            if move_not_found:
                                print(f"    [i] FA source deleted - moving to deleted_source...")
                                if move_to_not_found(file_to_move, deleted_source_dir, base_search_dir, preserve_structure, reason="deleted_source"):
                                    print(f"    [OK] Moved to deleted_source")
                                else:
                                    print(f"    [ERROR] Failed to move to deleted_source")
                            else:
                                print(f"    [i] FA source deleted - leaving file in place")
                        elif fa_result:
                            fa_processed += 1
                            print(f"    [OK] FA processing complete")
                    except Exception as e:
                        print(f"    [ERROR] FA processing failed: {e}")

                # Only try FuzzySearch if reverse search found NO e621/FA sources at all
                if not e621_post_id and not fa_post_id:
                    # Try FuzzySearch if enabled and no e621/FA matches from reverse search
                    fuzzy_e621_found = False
                    fuzzy_fa_found = False
                    fuzzy_fa_deleted = False
                    fuzzy_fa_deleted_path = None

                    if not e621_post_id and not fa_post_id and fuzzysearch_enabled and fuzzysearch_api_key:
                        print(f"    -> No e621/FA match from reverse search, trying FuzzySearch...")
                        fuzzy_results = search_image_fuzzysearch(image_path, fuzzysearch_api_key, fuzzysearch_distance)

                        if fuzzy_results:
                            # First pass: scan all results to find e621 and FA matches
                            e621_matches = []
                            fa_matches = []

                            for result in fuzzy_results:
                                site = result.get('site', '').lower()
                                site_id = result.get('site_id')

                                if site == 'e621' and site_id:
                                    e621_matches.append(str(site_id))
                                elif site == 'furaffinity' and site_id:
                                    fa_matches.append(str(site_id))

                            # Process e621 first (takes precedence)
                            if e621_matches and process_single_image:
                                for e621_id in e621_matches:
                                    print(f"    -> FuzzySearch found e621 match, processing...")
                                    try:
                                        if process_single_image(image_path, e621_id, config,
                                                               favorites_cache=favorites_cache,
                                                               base_search_dir=base_search_dir,
                                                               preserve_structure=preserve_structure):
                                            e621_processed += 1
                                            fuzzysearch_processed += 1
                                            fuzzy_e621_found = True
                                            print(f"    [OK] e621 processing complete (via FuzzySearch)")
                                            break  # Stop after first successful e621 match
                                    except Exception as e:
                                        print(f"    [ERROR] e621 processing failed: {e}")

                            # Only process FA if no e621 match was found or processed
                            if not e621_matches and fa_matches and process_single_fa_image and fa_cookies:
                                for fa_id in fa_matches:
                                    print(f"    -> FuzzySearch found FA match, processing...")
                                    try:
                                        # Don't pass FuzzySearch result as fluffle_match_data - different structure without author info
                                        fa_result = process_single_fa_image(image_path, fa_id, config,
                                                                   cookies=fa_cookies,
                                                                   image_id=image_id,
                                                                   connection=connection,
                                                                   base_search_dir=base_search_dir,
                                                                   preserve_structure=preserve_structure,
                                                                   fluffle_match_data=None)

                                        # Handle deleted FA posts - don't mark as found yet, check for reverse search fallback sources first
                                        if fa_result == 'deleted' or (isinstance(fa_result, tuple) and fa_result[0] == 'deleted'):
                                            # Extract renamed path if available
                                            file_to_move = fa_result[1] if isinstance(fa_result, tuple) else image_path

                                            # Store deleted FA info but don't process yet - check if reverse search has other sources
                                            fuzzy_fa_deleted = True
                                            fuzzy_fa_deleted_path = file_to_move
                                            print(f"    [i] FA source deleted - checking for alternative sources...")
                                            break  # Stop after first FA match (even if deleted)
                                        elif fa_result:
                                            fa_processed += 1
                                            fuzzysearch_processed += 1
                                            fuzzy_fa_found = True
                                            print(f"    [OK] FA processing complete (via FuzzySearch)")
                                            break  # Stop after first successful FA match
                                    except Exception as e:
                                        print(f"    [ERROR] FA processing failed: {e}")

                    # If FuzzySearch didn't find e621/FA matches, fall back to reverse search's other sources
                    if not fuzzy_e621_found and not fuzzy_fa_found:
                        other_match = None
                        if not e621_post_id and not fa_post_id and exact_match_entries:
                            platform_priority = ['bluesky', 'twitter', 'inkbunny']
                            for platform_name in platform_priority:
                                for match in exact_match_entries:
                                    platform_lower = str(match.get('platform', '')).lower()
                                    if platform_lower == platform_name:
                                        other_match = match
                                        break
                                if other_match:
                                    break

                            if not other_match:
                                for match in exact_match_entries:
                                    platform_lower = str(match.get('platform', '')).lower()
                                    if platform_lower not in ('e621', 'furaffinity'):
                                        other_match = match
                                        break

                        if other_match:
                            # Use the potentially renamed path if FA was deleted
                            file_to_process = fuzzy_fa_deleted_path if fuzzy_fa_deleted else image_path
                            print(f"    -> {str(other_match.get('platform', 'other')).title()} match found, moving to other_sourced...")
                            destination = rename_and_move_to_other_sourced(
                                file_to_process,
                                other_sourced_dir,
                                other_match,
                                base_search_dir=base_search_dir,
                                preserve_structure=preserve_structure,
                                backup_dir=other_sourced_backup_dir
                            )
                            if destination:
                                other_processed += 1
                                try:
                                    print(f"    [OK] Moved to other sourced as {destination.name}")
                                except UnicodeEncodeError:
                                    # Fallback for filenames with characters that can't be displayed in console
                                    print(f"    [OK] Moved to other sourced as {destination.name.encode('ascii', 'replace').decode('ascii')}")
                            else:
                                print(f"    [ERROR] Failed to move to other sourced")
                        elif fuzzy_fa_deleted:
                            # FuzzySearch found deleted FA but no other reverse search sources - move to deleted_source
                            if move_not_found:
                                print(f"    [i] No alternative sources found - FA source deleted, moving to deleted_source...")
                                if move_to_not_found(fuzzy_fa_deleted_path, deleted_source_dir, base_search_dir, preserve_structure, reason="deleted_source"):
                                    print(f"    [OK] Moved to deleted_source")
                                else:
                                    print(f"    [ERROR] Failed to move to deleted_source")
                            else:
                                print(f"    [i] FA source deleted - leaving file in place")
                        elif not e621_post_id and not fa_post_id:
                            if move_not_found:
                                print(f"    [i] No exact matches - moving to not_found...")
                                if move_to_not_found(image_path, not_found_dir, base_search_dir, preserve_structure):
                                    print(f"    [OK] Moved to not_found")
                                else:
                                    print(f"    [ERROR] Failed to move to not_found")
                            else:
                                print(f"    [i] No exact matches - leaving file in place")
        else:
            print(f"    Failed to get results")

        print()

        # Rate limiting
        if i < len(remaining_images):
            time.sleep(search_delay)

    # Close database connection
    connection.close()

    # Print summary
    print("=" * 80)
    print("Search Complete!")
    print(f"Images processed: {successful_searches}/{len(remaining_images)}")
    print(f"Exact matches found: {exact_matches_count}")
    if e621_processed > 0:
        print(f"e621 posts processed: {e621_processed} (downloaded & existing code moved to source_found)")
    if fa_processed > 0:
        print(f"FA posts processed: {fa_processed} (downloaded & existing code moved to source_found)")
    if fuzzysearch_processed > 0:
        print(f"  └─ Found via FuzzySearch fallback: {fuzzysearch_processed}")
    if other_processed > 0:
        print(f"Other sources processed: {other_processed} (renamed & moved to other_sourced)")
    print(f"Results stored in database: {db_config.get('database', 'N/A')}")
    if save_json:
        print(f"JSON responses saved to: {responses_dir}")
    print("=" * 80)

    # Clean up response files older than 5 days
    if save_json:
        cleanup_old_responses(responses_dir)


if __name__ == "__main__":
    main()
