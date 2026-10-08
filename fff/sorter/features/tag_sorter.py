# fff: artists come from config/sort_artists.yaml; the e621 schema name from the environment.
"""
Tag-based file sorter using e621_posts_db database.

This module queries the e621_posts_db to find posts with specific tags,
then moves matching files to configured destinations.
"""
import os
import re
import shutil
import logging
import yaml
from typing import List, Dict, Set, Optional, Tuple
import mysql.connector
from mysql.connector import Error

# Import database logging functions
try:
    from fff.sorter.handlers.utils import move_file_with_logging
    from fff.sorter.handlers.database_manager import DatabaseManager
    HAS_LOGGING = True
except ImportError:
    HAS_LOGGING = False

from fff.sorter import paths as sorter_paths

logger = logging.getLogger('fff.sorter.tag_sorter')


class TagSorter:
    """Sort files based on tags from e621_posts_db database."""

    VIDEO_EXTS = {".webm", ".mp4", ".mov", ".avi"}
    GIF_EXTS = {".gif"}
    MEDIA_TYPE_LABEL = {"pictures": "Pics", "videos": "Vids", "gifs": "Gifs"}

    def __init__(self, config, e621_db_config: Dict = None, db_manager=None):
        """
        Initialize TagSorter.

        Args:
            config: FFF configuration object
            e621_db_config: Optional e621 database config override
                {
                    'host': 'localhost',
                    'port': 3306,
                    'user': 'username',
                    'password': 'password',
                    'database': 'test_...'  # fff: normally from the environment
                }
            db_manager: Optional DatabaseManager instance for operation logging
        """
        self.config = config
        self.db_manager = db_manager  # Store for file operation logging

        # Use provided e621 config or get from config object (which reads from tag_sorting.yaml)
        if e621_db_config:
            self.e621_config = e621_db_config
        else:
            # Use config's e621 database settings (with proper fallbacks to main MySQL config)
            if hasattr(config, 'get_e621_database_config'):
                self.e621_config = config.get_e621_database_config()
            else:
                # Fallback for older config objects
                self.e621_config = {
                    'host': config.MYSQL_HOST,
                    'port': config.MYSQL_PORT,
                    'user': config.MYSQL_USER,
                    'password': config.MYSQL_PASSWORD,
                    'database': getattr(config, 'e621_database', None) or sorter_paths.db_names()['archive']
                }

        # Load favorite artists from artists.yaml
        self.favorite_artist_aliases = self._load_favorite_artist_aliases()

        # Cache for extended artist names (with disambiguation suffixes)
        self._extended_artists_cache = None

        # Regex pattern to match e621 filenames with ratings
        # Pattern: *-(explicit|questionable|safe)-{post_id}.extension
        self.filename_pattern = re.compile(
            r'^(.+)-(explicit|questionable|safe)-(\d+)(\.\w+)$',
            re.IGNORECASE
        )

    def _get_archive_directories(self) -> List[str]:
        """Get list of all configured archive directories.

        Returns:
            List of archive directory paths (handles both simple and expanded formats)
        """
        archive_dirs = []

        # Check if archive_directories is configured
        if not hasattr(self.config, 'config') or 'archive_directories' not in self.config.config:
            return archive_dirs

        archive_config = self.config.config['archive_directories']

        # Process each archive type (sfw, explicit, premium)
        for archive_type, config_value in archive_config.items():
            if not config_value:
                continue

            # Simple format: string path
            if isinstance(config_value, str):
                archive_dirs.append(config_value)
                logger.info(f"Found {archive_type} archive directory: {config_value}")
            # Expanded format: dict with images/videos/gifs
            elif isinstance(config_value, dict):
                for media_type, path in config_value.items():
                    if path:
                        archive_dirs.append(path)
                        logger.info(f"Found {archive_type}/{media_type} archive directory: {path}")

        # Filter to only existing directories
        existing_dirs = [d for d in archive_dirs if os.path.exists(d)]
        if len(existing_dirs) < len(archive_dirs):
            logger.warning(f"Some archive directories don't exist: {len(archive_dirs) - len(existing_dirs)} missing")

        return existing_dirs

    def _classify_media_type(self, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if ext in self.GIF_EXTS:
            return "gifs"
        if ext in self.VIDEO_EXTS:
            return "videos"
        return "pictures"

    def _is_safe_rated(self, filename: str) -> bool:
        # Also matches duplicate-suffixed names like 'artist-safe-123-1.png'
        return re.search(r'-safe-\d+', filename, re.IGNORECASE) is not None

    def _resolve_destination_for_file(self, filename: str, destination_dir: Optional[str],
                                      base_directories: Optional[Dict[str, str]],
                                      folder_name: Optional[str],
                                      append_media_type_label: bool = False) -> Optional[str]:
        """Return concrete destination directory for a file based on its type.

        Legacy behavior: if destination_dir is provided, it is used for all files.
        New behavior: use base_directories[type] + folder_name when both are supplied.
        If append_media_type_label is True, appends e.g. "- Pics" / "- Vids" / "- Gifs"
        to folder_name so each media type lands in a distinct sub-folder.
        Files rated 'safe' go to base_directories['sfw_' + type] when it is set.
        """
        if destination_dir:
            return destination_dir

        if not base_directories or not folder_name:
            return None

        media_type = self._classify_media_type(filename)
        base = None
        if self._is_safe_rated(filename):
            base = base_directories.get(f"sfw_{media_type}")
        base = base or base_directories.get(media_type)
        if not base:
            return None
        effective_folder = folder_name
        if append_media_type_label:
            # Apply the suffix to every path component so that sub-rule paths like
            # "FNAF/Freddy" become "FNAF - Pics/Freddy - Pics".
            from pathlib import PurePosixPath
            parts = PurePosixPath(folder_name.replace('\\', '/')).parts
            suffix = self.MEDIA_TYPE_LABEL.get(media_type, media_type.capitalize())
            effective_folder = os.path.join(*[f"{p} - {suffix}" for p in parts])
        return os.path.join(base, effective_folder)

    def _get_extended_artist_list(self) -> List[str]:
        """Get cached extended artist list with disambiguation suffixes.

        Returns:
            List of artist names with all disambiguation variants
        """
        if self._extended_artists_cache is not None:
            return self._extended_artists_cache

        extended_artists = list(self.favorite_artist_aliases)  # Copy the original list
        for artist in self.favorite_artist_aliases:
            # Add common e621 disambiguation suffixes
            extended_artists.append(f"{artist}_(artist)")
            extended_artists.append(f"{artist}_(character)")
            extended_artists.append(f"{artist}_(copyright)")

        self._extended_artists_cache = extended_artists
        return extended_artists

    def _load_favorite_artist_aliases(self) -> List[str]:
        """
        Load all artist names and aliases from artists.yaml.

        Supports both legacy format (list of aliases) and new format (dict with config).

        Returns:
            List of all artist names and aliases (lowercase) from artists.yaml
        """
        try:
            artists_data = sorter_paths.load_yaml_for_env(sorter_paths.SORT_ARTISTS)  # fff: config/sort_artists.yaml

            # Collect all canonical names AND aliases from all artists
            all_names = []
            for canonical_name, artist_config in artists_data.items():
                # Check if artist is enabled (new format only, default to True)
                if isinstance(artist_config, dict):
                    # New format with configuration options
                    enabled = artist_config.get('enabled', True)
                    if not enabled:
                        continue  # Skip disabled artists

                    aliases = artist_config.get('aliases', [])
                elif isinstance(artist_config, list):
                    # Legacy format - list of aliases
                    aliases = artist_config
                else:
                    logger.warning(f"Unexpected format for artist '{canonical_name}': {type(artist_config)}")
                    continue

                # Add the canonical name (e621 might use this)
                all_names.append(canonical_name.lower())

                # Add all aliases
                all_names.extend([alias.lower() for alias in aliases])

            logger.info(f"Loaded {len(all_names)} artist names/aliases from configs/artists.yaml")
            return all_names
        except Exception as e:
            logger.error(f"Error loading configs/artists.yaml: {e}")
            return []

    def connect_to_e621_db(self):
        """Connect to e621 database."""
        try:
            conn = mysql.connector.connect(
                host=self.e621_config['host'],
                port=self.e621_config['port'],
                user=self.e621_config['user'],
                password=self.e621_config['password'],
                database=self.e621_config['database'],
                charset='utf8mb4',
                use_unicode=True
            )
            logger.info(f"Connected to {self.e621_config['database']} at {self.e621_config['host']}")
            return conn
        except Error as e:
            logger.error(f"Failed to connect to {self.e621_config['database']}: {e}")
            raise

    def get_filenames_by_tags(self, tags: List[str] = None, match_all: bool = False,
                              exclude_favorite_artists: bool = False,
                              exclude_tags: List[str] = None,
                              needed_tags: List[str] = None,
                              can_have_tags: List[str] = None) -> Set[str]:
        """
        Query e621_posts_db to get filenames of posts matching tags.

        Args:
            tags: List of tags to search for (legacy parameter, uses match_all logic)
            match_all: If True, require all tags (AND logic), else any tag (OR logic)
            exclude_favorite_artists: If True, exclude posts from artists in artists.yaml
            exclude_tags: List of tags to exclude - posts with ANY of these tags will be filtered out
            needed_tags: List of tags that MUST all be present (AND logic, takes precedence over tags)
            can_have_tags: List of tags where at least one must be present (OR logic, used with needed_tags)

        Returns:
            Set of filenames matching the tag criteria (excluding favorite artists and tags if enabled)

        Note:
            If needed_tags and can_have_tags are both provided, the logic is:
            - Post MUST have ALL needed_tags
            - Post MUST have AT LEAST ONE of can_have_tags
            If only needed_tags is provided, only those tags are required.
            If only can_have_tags is provided, it works like tags with match_all=False.
        """
        conn = None

        # Determine which tag parameters to use (new format takes precedence)
        if needed_tags is not None or can_have_tags is not None:
            # New format: needed_tags (must have all) + can_have_tags (must have at least one)
            needed_tags = needed_tags or []
            can_have_tags = can_have_tags or []
            use_new_format = True
        else:
            # Legacy format: use tags parameter with match_all logic
            tags = tags or []
            use_new_format = False

        # Get artist exclusion list from artists.yaml if enabled
        exclude_artists = self._get_extended_artist_list() if exclude_favorite_artists else []

        # Ensure exclude_tags is always a list
        if exclude_tags is None:
            exclude_tags = []
        elif isinstance(exclude_tags, str):
            exclude_tags = [exclude_tags] if exclude_tags else []
        elif not isinstance(exclude_tags, list):
            exclude_tags = []

        try:
            conn = self.connect_to_e621_db()
            cursor = conn.cursor(dictionary=True)

            if use_new_format:
                # NEW FORMAT: needed_tags (AND) + can_have_tags (OR)
                query = """
                    SELECT DISTINCT p.file_name, p.id
                    FROM posts p
                """

                # Add joins for each needed tag (must have ALL)
                for i, tag in enumerate(needed_tags):
                    query += f"""
                    INNER JOIN posts_tags pt_needed{i} ON pt_needed{i}.post_id = p.id
                    INNER JOIN tags t_needed{i} ON t_needed{i}.tag_id = pt_needed{i}.tag_id AND t_needed{i}.name = %s
                    """

                # Add join for can_have tags (must have at least ONE)
                if can_have_tags:
                    query += """
                    INNER JOIN posts_tags pt_can ON pt_can.post_id = p.id
                    INNER JOIN tags t_can ON t_can.tag_id = pt_can.tag_id
                    """

                # Build WHERE clause for can_have_tags and exclusions
                where_clauses = []
                query_params = list(needed_tags)

                # Add can_have_tags to WHERE (at least one must match)
                if can_have_tags:
                    can_have_placeholders = ', '.join(['%s'] * len(can_have_tags))
                    where_clauses.append(f"t_can.name IN ({can_have_placeholders})")
                    query_params.extend(can_have_tags)

                # Add artist exclusion if specified (optimized with NOT EXISTS)
                if exclude_artists:
                    artist_placeholders = ', '.join(['%s'] * len(exclude_artists))
                    where_clauses.append(f"""
                        NOT EXISTS (
                            SELECT 1
                            FROM posts_artists pa
                            INNER JOIN artists a ON a.artist_id = pa.artist_id
                            WHERE pa.post_id = p.id AND a.name IN ({artist_placeholders})
                        )
                    """)
                    query_params.extend(exclude_artists)

                # Add tag exclusion if specified (optimized with NOT EXISTS)
                if exclude_tags:
                    tag_placeholders = ', '.join(['%s'] * len(exclude_tags))
                    where_clauses.append(f"""
                        NOT EXISTS (
                            SELECT 1
                            FROM posts_tags pt_excl
                            INNER JOIN tags t_excl ON t_excl.tag_id = pt_excl.tag_id
                            WHERE pt_excl.post_id = p.id AND t_excl.name IN ({tag_placeholders})
                        )
                    """)
                    query_params.extend(exclude_tags)

                # Add WHERE clause if we have any conditions
                if where_clauses:
                    query += " WHERE " + " AND ".join(where_clauses)

                cursor.execute(query, query_params)

            elif match_all:
                # AND logic: post must have ALL tags
                # This requires joining the tags table multiple times
                query = """
                    SELECT DISTINCT p.file_name, p.id
                    FROM posts p
                """

                # Add a join for each tag
                for i, tag in enumerate(tags):
                    query += f"""
                    INNER JOIN posts_tags pt{i} ON pt{i}.post_id = p.id
                    INNER JOIN tags t{i} ON t{i}.tag_id = pt{i}.tag_id AND t{i}.name = %s
                    """

                # Build WHERE clause for exclusions
                where_clauses = []
                query_params = list(tags)

                # Add artist exclusion if specified (optimized with NOT EXISTS)
                if exclude_artists:
                    # Use NOT EXISTS for better performance
                    artist_placeholders = ', '.join(['%s'] * len(exclude_artists))
                    where_clauses.append(f"""
                        NOT EXISTS (
                            SELECT 1
                            FROM posts_artists pa
                            INNER JOIN artists a ON a.artist_id = pa.artist_id
                            WHERE pa.post_id = p.id AND a.name IN ({artist_placeholders})
                        )
                    """)
                    query_params.extend(exclude_artists)

                # Add tag exclusion if specified (optimized with NOT EXISTS)
                if exclude_tags:
                    tag_placeholders = ', '.join(['%s'] * len(exclude_tags))
                    where_clauses.append(f"""
                        NOT EXISTS (
                            SELECT 1
                            FROM posts_tags pt_excl
                            INNER JOIN tags t_excl ON t_excl.tag_id = pt_excl.tag_id
                            WHERE pt_excl.post_id = p.id AND t_excl.name IN ({tag_placeholders})
                        )
                    """)
                    query_params.extend(exclude_tags)

                # Add WHERE clause if we have any conditions
                if where_clauses:
                    query += " WHERE " + " AND ".join(where_clauses)

                cursor.execute(query, query_params)
            else:
                # OR logic: post must have ANY of the tags
                tag_placeholders = ', '.join(['%s'] * len(tags))
                query = f"""
                    SELECT DISTINCT p.file_name, p.id
                    FROM posts p
                    LEFT JOIN posts_tags pt ON pt.post_id = p.id
                    LEFT JOIN tags t ON t.tag_id = pt.tag_id
                """

                # Build WHERE clause
                where_clauses = [f"t.name IN ({tag_placeholders})"]
                query_params = list(tags)

                # Add artist exclusion if specified (optimized with NOT EXISTS)
                if exclude_artists:
                    artist_placeholders = ', '.join(['%s'] * len(exclude_artists))
                    where_clauses.append(f"""
                        NOT EXISTS (
                            SELECT 1
                            FROM posts_artists pa
                            INNER JOIN artists a ON a.artist_id = pa.artist_id
                            WHERE pa.post_id = p.id AND a.name IN ({artist_placeholders})
                        )
                    """)
                    query_params.extend(exclude_artists)

                # Add tag exclusion if specified (optimized with NOT EXISTS)
                if exclude_tags:
                    exclude_tag_placeholders = ', '.join(['%s'] * len(exclude_tags))
                    where_clauses.append(f"""
                        NOT EXISTS (
                            SELECT 1
                            FROM posts_tags pt_excl
                            INNER JOIN tags t_excl ON t_excl.tag_id = pt_excl.tag_id
                            WHERE pt_excl.post_id = p.id AND t_excl.name IN ({exclude_tag_placeholders})
                        )
                    """)
                    query_params.extend(exclude_tags)

                query += " WHERE " + " AND ".join(where_clauses)
                cursor.execute(query, query_params)

            results = cursor.fetchall()
            filenames = {row['file_name'] for row in results if row['file_name']}

            cursor.close()
            conn.close()

            # Build log message based on format used
            if use_new_format:
                tag_description = []
                if needed_tags:
                    tag_description.append(f"needed_tags={needed_tags}")
                if can_have_tags:
                    tag_description.append(f"can_have_tags={can_have_tags}")
                tag_info = ", ".join(tag_description)
            else:
                tag_info = f"tags={tags}, match_all={match_all}"

            exclude_info = f" (excluding {len(exclude_artists)} artists)" if exclude_artists else ""
            exclude_tags_info = f" (excluding tags: {', '.join(exclude_tags)})" if exclude_tags else ""
            logger.info(f"Found {len(filenames)} posts matching {tag_info}{exclude_info}{exclude_tags_info}")
            return filenames

        except Error as e:
            logger.error(f"Database query error: {e}")
            if conn:
                conn.close()
            raise
        except Exception as e:
            logger.error(f"Unexpected error querying database: {e}")
            if conn:
                conn.close()
            raise

    def get_filenames_by_pools(self, pool_ids: List[int],
                                exclude_favorite_artists: bool = False,
                                exclude_tags: List[str] = None) -> Set[str]:
        """
        Query e621_posts_db to get filenames of posts belonging to specific pools.

        Args:
            pool_ids: List of pool IDs to search for (post must belong to ANY of these pools)
            exclude_favorite_artists: If True, exclude posts from artists in artists.yaml
            exclude_tags: List of tags to exclude - posts with ANY of these tags will be filtered out

        Returns:
            Set of filenames belonging to the specified pools
        """
        conn = None

        pool_ids = [int(pid) for pid in (pool_ids or [])]
        if not pool_ids:
            return set()

        exclude_artists = self._get_extended_artist_list() if exclude_favorite_artists else []

        if exclude_tags is None:
            exclude_tags = []
        elif isinstance(exclude_tags, str):
            exclude_tags = [exclude_tags] if exclude_tags else []
        elif not isinstance(exclude_tags, list):
            exclude_tags = []

        try:
            conn = self.connect_to_e621_db()
            cursor = conn.cursor(dictionary=True)

            pool_placeholders = ', '.join(['%s'] * len(pool_ids))
            query = f"""
                SELECT DISTINCT p.file_name, p.id
                FROM posts p
                INNER JOIN posts_pools pp ON pp.post_id = p.id
                WHERE pp.pool_id IN ({pool_placeholders})
            """
            query_params = list(pool_ids)

            where_clauses = []

            if exclude_artists:
                artist_placeholders = ', '.join(['%s'] * len(exclude_artists))
                where_clauses.append(f"""
                    NOT EXISTS (
                        SELECT 1
                        FROM posts_artists pa
                        INNER JOIN artists a ON a.artist_id = pa.artist_id
                        WHERE pa.post_id = p.id AND a.name IN ({artist_placeholders})
                    )
                """)
                query_params.extend(exclude_artists)

            if exclude_tags:
                tag_placeholders = ', '.join(['%s'] * len(exclude_tags))
                where_clauses.append(f"""
                    NOT EXISTS (
                        SELECT 1
                        FROM posts_tags pt_excl
                        INNER JOIN tags t_excl ON t_excl.tag_id = pt_excl.tag_id
                        WHERE pt_excl.post_id = p.id AND t_excl.name IN ({tag_placeholders})
                    )
                """)
                query_params.extend(exclude_tags)

            if where_clauses:
                query += " AND " + " AND ".join(where_clauses)

            cursor.execute(query, query_params)
            results = cursor.fetchall()
            filenames = {row['file_name'] for row in results if row['file_name']}

            cursor.close()
            conn.close()

            exclude_info = f" (excluding {len(exclude_artists)} artists)" if exclude_artists else ""
            exclude_tags_info = f" (excluding tags: {', '.join(exclude_tags)})" if exclude_tags else ""
            logger.info(f"Found {len(filenames)} posts in pools={pool_ids}{exclude_info}{exclude_tags_info}")
            return filenames

        except Error as e:
            logger.error(f"Database query error: {e}")
            if conn:
                conn.close()
            raise
        except Exception as e:
            logger.error(f"Unexpected error querying database: {e}")
            if conn:
                conn.close()
            raise

    def get_post_dates(self, filenames: List[str]) -> Dict[str, object]:
        """
        Query e621_posts_db for each file's post creation date (posts.created_at).

        Used to set a file's modified/created timestamp to match its e621 post
        date — e.g. so pages of a pool/comic sort into upload order in a file
        browser instead of by whenever they happened to be moved on disk.

        Args:
            filenames: Filenames to look up

        Returns:
            Dict mapping filename -> datetime.datetime (created_at). Filenames
            with no matching post or a null created_at are omitted.
        """
        filenames = list(filenames or [])
        if not filenames:
            return {}

        conn = None
        try:
            conn = self.connect_to_e621_db()
            cursor = conn.cursor()
            placeholders = ', '.join(['%s'] * len(filenames))
            cursor.execute(
                f"""
                SELECT file_name, created_at FROM posts
                WHERE file_name IN ({placeholders}) AND created_at IS NOT NULL
                """,
                filenames
            )
            return {file_name: created_at for file_name, created_at in cursor.fetchall()}
        except Error as e:
            logger.error(f"Failed to query post dates: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    def _apply_post_date(self, dst_path: str, filename: str, post_dates: Optional[Dict]) -> bool:
        """Set dst_path's modified/created timestamp from post_dates[filename], if present.

        Returns True only when a timestamp was actually written, so callers can
        report a real count instead of assuming success whenever this is called.
        """
        if not post_dates:
            return False
        dt = post_dates.get(filename)
        if not dt:
            logger.debug(f"No resolved post date for {filename} — leaving its timestamp untouched")
            return False
        try:
            from fff.sorter.handlers.utils import set_file_timestamps
            ts = dt.timestamp()
            set_file_timestamps(dst_path, mtime=ts, ctime=ts)
            return True
        except Exception as e:
            logger.warning(f"Failed to set post date on {dst_path}: {e}")
            return False

    def _get_post_id_from_filename(self, filename: str) -> Optional[str]:
        """Extract the e621 post ID, allowing local filename prefixes."""
        match = self.filename_pattern.match(os.path.basename(filename))
        return match.group(3) if match else None

    def _filename_matches(self, filename: str, requested_filenames: Set[str],
                          requested_post_ids: Optional[Dict[str, str]] = None) -> bool:
        """Match database filenames and locally prefixed variants by post ID."""
        if filename in requested_filenames:
            return True
        post_id = self._get_post_id_from_filename(filename)
        return bool(post_id and requested_post_ids and post_id in requested_post_ids)

    def find_files_in_directories(self, filenames: Set[str], source_dirs: List[str],
                                   surface_only_dirs: List[str] = None,
                                   exclude_dirs: List[str] = None,
                                   normalize_conditional_dnp: bool = False) -> Dict[str, str]:
        """
        Search for files matching the given filenames in source directories.

        Args:
            filenames: Set of filenames to search for
            source_dirs: List of directories to search recursively
            surface_only_dirs: List of directories to scan only at surface level (DEPRECATED - no longer used)
            exclude_dirs: List of directories to exclude from search (e.g., destination directory)

        Returns:
            Dictionary mapping filename to full file path
        """
        found_files = {}
        surface_only_dirs = surface_only_dirs or []
        exclude_dirs = exclude_dirs or []

        # Early exit if no files to find
        if not filenames:
            return found_files

        # Track remaining files to find for early exit
        remaining_files = set(filenames)
        requested_post_ids = {
            post_id: requested
            for requested in filenames
            if (post_id := self._get_post_id_from_filename(requested))
        }

        # Normalize exclude paths for comparison (and prepare prefix matching)
        exclude_paths = [os.path.normpath(os.path.abspath(d)) for d in exclude_dirs]

        # Path normalization cache to avoid repeated os.path calls
        path_cache = {}

        def _normalize_path(path):
            """Cached path normalization."""
            if path not in path_cache:
                path_cache[path] = os.path.normpath(os.path.abspath(path))
            return path_cache[path]

        def _is_excluded(path):
            """Return True when path matches or resides under any excluded directory."""
            normalized = _normalize_path(path)
            for excluded in exclude_paths:
                if normalized == excluded or normalized.startswith(excluded + os.sep):
                    return True
            return False

        def _prepare_filename(file_path: str) -> str:
            """Remove the non-artist conditional_dnp prefix before sorting."""
            filename = os.path.basename(file_path)
            prefix_match = re.match(r'^conditional_dnp_', filename, re.IGNORECASE)
            if not prefix_match:
                return filename

            normalized_name = filename[prefix_match.end():]
            if normalized_name not in filenames:
                return filename

            normalized_path = os.path.join(os.path.dirname(file_path), normalized_name)
            if not normalize_conditional_dnp:
                return filename
            if os.path.exists(normalized_path):
                logger.warning(
                    "Cannot rename %s because the target already exists",
                    file_path,
                )
                return filename

            try:
                os.rename(file_path, normalized_path)
                logger.info("Removed conditional_dnp prefix: %s -> %s", filename, normalized_name)
                return normalized_name
            except OSError as exc:
                logger.warning("Could not rename %s: %s", file_path, exc)
                return filename

        # First, scan surface-only directories (DEPRECATED - kept for backward compatibility)
        for surface_dir in surface_only_dirs:
            if not os.path.exists(surface_dir):
                logger.warning(f"Surface directory does not exist: {surface_dir}")
                continue

            logger.info(f"Scanning directory (surface only): {surface_dir}")

            # Only scan files in the root of this directory, not subdirectories
            try:
                for file in os.listdir(surface_dir):
                    file_path = os.path.join(surface_dir, file)
                    file = _prepare_filename(file_path)
                    file_path = os.path.join(surface_dir, file)
                    # Only process files (not directories)
                    if os.path.isfile(file_path) and self._filename_matches(
                        file, filenames, requested_post_ids
                    ):
                        found_files[file] = file_path
                        requested_name = requested_post_ids.get(
                            self._get_post_id_from_filename(file), file
                        )
                        remaining_files.discard(requested_name)
                        logger.debug(f"Found matching file (surface): {file_path}")
            except Exception as e:
                logger.error(f"Error scanning surface directory {surface_dir}: {e}")

        # Then scan regular directories recursively
        for source_dir in source_dirs:
            if not os.path.exists(source_dir):
                logger.warning(f"Source directory does not exist: {source_dir}")
                continue

            # Check if this source dir should be excluded
            normalized_source = os.path.normpath(os.path.abspath(source_dir))
            if _is_excluded(normalized_source):
                logger.info(f"Skipping excluded directory: {source_dir}")
                continue

            logger.info(f"Scanning directory: {source_dir}")

            for root, dirs, files in os.walk(source_dir):
                # Early exit if all files found
                if not remaining_files:
                    logger.debug(f"All files found, stopping search early")
                    break

                # Check if current directory is in exclude list
                normalized_root = _normalize_path(root)
                if _is_excluded(normalized_root):
                    logger.debug(f"Skipping excluded subdirectory: {root}")
                    dirs.clear()  # Don't recurse into subdirectories
                    continue

                # Skip subdirectories that match any exclude pattern
                dirs_to_skip = []
                for d in dirs:
                    subdir_path = _normalize_path(os.path.join(root, d))
                    if _is_excluded(subdir_path):
                        dirs_to_skip.append(d)

                for d in dirs_to_skip:
                    dirs.remove(d)
                    logger.debug(f"Skipping excluded directory: {os.path.join(root, d)}")

                for file in files:
                    file_path = os.path.join(root, file)
                    file = _prepare_filename(file_path)
                    file_path = os.path.join(root, file)
                    if self._filename_matches(file, remaining_files, requested_post_ids):
                        found_files[file] = file_path
                        requested_name = requested_post_ids.get(
                            self._get_post_id_from_filename(file), file
                        )
                        remaining_files.discard(requested_name)
                        logger.debug(f"Found matching file: {file_path} ({len(remaining_files)} remaining)")

        logger.info(f"Found {len(found_files)} files out of {len(filenames)} database matches")
        return found_files

    def move_files_to_destination(self, files: Dict[str, str], destination_dir: Optional[str],
                                  base_directories: Optional[Dict[str, str]], folder_name: Optional[str],
                                  dry_run: bool = False, reason_tags: Optional[List[str]] = None,
                                  append_media_type_label: bool = False,
                                  post_dates: Optional[Dict] = None) -> Tuple[int, int]:
        """
        Move files to destination directory (legacy) or to type-aware destinations using base directories.
        If a file with the same name exists, move the older file to duplicates directory.

        Args:
            files: Dictionary mapping filename to source path
            destination_dir: Legacy single destination directory
            base_directories: Mapping of media types to base directories
            folder_name: Folder name to append to base directory
            dry_run: If True, only simulate the move
            reason_tags: Tags to include in database log reason (for tracking)
            post_dates: Optional filename -> datetime.datetime (post created_at);
                when a moved file's name is a key, its modified/created timestamp
                is set to match so pool/comic pages sort into upload order.

        Returns:
            (number of files successfully moved, number of files whose
            modified/created timestamp was actually set from post_dates)
        """
        base_directories = base_directories or {}

        if destination_dir and not dry_run:
            os.makedirs(destination_dir, exist_ok=True)

        moved_count = 0
        dates_set = 0
        duplicate_dir = self.config.duplicate_directory

        for filename, src_path in files.items():
            concrete_dest = self._resolve_destination_for_file(filename, destination_dir, base_directories, folder_name, append_media_type_label)
            if not concrete_dest:
                print(f"  [SKIP] No destination resolved for: {filename}")
                logger.debug(f"Skip move; no destination resolved for {filename}")
                continue

            dst_path = os.path.join(concrete_dest, filename)

            # Skip moving if the source is already under the destination subtree
            dest_root = os.path.abspath(concrete_dest)
            src_abs = os.path.abspath(src_path)
            if os.path.commonpath([src_abs, dest_root]) == dest_root:
                logger.debug(f"Skip move; already under destination: {src_path}")
                # Still already-correct files should get their post date applied —
                # otherwise files sorted before this option was enabled never get it.
                if self._apply_post_date(src_abs, filename, post_dates):
                    dates_set += 1
                continue

            try:
                if not dry_run:
                    os.makedirs(concrete_dest, exist_ok=True)

                # Check if file already exists at destination
                if os.path.exists(dst_path):
                    # Compare modification times to determine which is older
                    src_mtime = os.path.getmtime(src_path)
                    dst_mtime = os.path.getmtime(dst_path)

                    if src_mtime < dst_mtime:
                        # Source is older - move existing destination file to duplicates
                        if dry_run:
                            print(f"  [DRY RUN] Would move existing '{filename}' to duplicates (newer)")
                            print(f"  [DRY RUN] Would move '{filename}' to destination (older)")
                        else:
                            os.makedirs(duplicate_dir, exist_ok=True)
                            dup_path = os.path.join(duplicate_dir, filename)

                            # Handle duplicate name in duplicate directory
                            counter = 1
                            original_dup = dup_path
                            while os.path.exists(dup_path):
                                name, ext = os.path.splitext(original_dup)
                                dup_path = f"{name}_{counter}{ext}"
                                counter += 1

                            # Move newer file to duplicates
                            if HAS_LOGGING and self.db_manager:
                                move_file_with_logging(
                                    dst_path, dup_path,
                                    db_manager=self.db_manager,
                                    logger_obj=logger,
                                    destination_directory='duplicates',
                                    reason='tag_sort_duplicate_newer'
                                )
                            else:
                                shutil.move(dst_path, dup_path)
                                logger.info(f"Moved newer file to duplicates: {dst_path} -> {dup_path}")

                            # Move older file to destination
                            if HAS_LOGGING and self.db_manager:
                                tags_str = ','.join(reason_tags[:3]) if reason_tags else 'unknown'
                                move_file_with_logging(
                                    src_path, dst_path,
                                    db_manager=self.db_manager,
                                    logger_obj=logger,
                                    destination_directory=os.path.basename(concrete_dest),
                                    reason=f'tag_sort_{tags_str}_older_kept'
                                )
                            else:
                                shutil.move(src_path, dst_path)
                                logger.info(f"Moved older file to destination: {src_path} -> {dst_path}")

                            if self._apply_post_date(dst_path, filename, post_dates):
                                dates_set += 1

                            print(f"  [OK] Moved (older): {filename}")
                            print(f"    Existing file moved to duplicates (newer)")
                    else:
                        # Destination is older or same age - move source to duplicates
                        if dry_run:
                            print(f"  [DRY RUN] Would move '{filename}' to duplicates (newer/same age)")
                            print(f"    Existing file kept at destination (older/same age)")
                        else:
                            os.makedirs(duplicate_dir, exist_ok=True)
                            dup_path = os.path.join(duplicate_dir, filename)

                            # Handle duplicate name in duplicate directory
                            counter = 1
                            original_dup = dup_path
                            while os.path.exists(dup_path):
                                name, ext = os.path.splitext(original_dup)
                                dup_path = f"{name}_{counter}{ext}"
                                counter += 1

                            # Move newer file to duplicates
                            if HAS_LOGGING and self.db_manager:
                                move_file_with_logging(
                                    src_path, dup_path,
                                    db_manager=self.db_manager,
                                    logger_obj=logger,
                                    destination_directory='duplicates',
                                    reason='tag_sort_duplicate_newer_source'
                                )
                            else:
                                shutil.move(src_path, dup_path)
                                logger.info(f"Moved newer file to duplicates: {src_path} -> {dup_path}")

                            print(f"  [WARNING] File exists (older): {filename}")
                            print(f"    Moved to duplicates: {os.path.basename(dup_path)}")
                else:
                    # No existing file - simple move
                    if dry_run:
                        print(f"  [DRY RUN] Would move: {filename}")
                        print(f"    From: {src_path}")
                        print(f"    To: {dst_path}")
                    else:
                        # Move file with database logging
                        if HAS_LOGGING and self.db_manager:
                            tags_str = ','.join(reason_tags[:3]) if reason_tags else 'unknown'
                            move_file_with_logging(
                                src_path, dst_path,
                                db_manager=self.db_manager,
                                logger_obj=logger,
                                destination_directory=os.path.basename(concrete_dest),
                                reason=f'tag_sort_{tags_str}'
                            )
                        else:
                            shutil.move(src_path, dst_path)
                            logger.info(f"Moved {src_path} -> {dst_path}")

                        if self._apply_post_date(dst_path, filename, post_dates):
                            dates_set += 1

                        print(f"  [OK] Moved: {filename}")

                moved_count += 1

            except Exception as e:
                logger.error(f"Failed to move {src_path}: {e}")
                print(f"  [ERROR] Failed to move {filename}: {e}")

        return moved_count, dates_set

    def _apply_sub_rules(self, files_pool: dict, destination_dir: Optional[str],
                         sub_rules: List[Dict], dry_run: bool,
                         reason_tags: list = None, indent: int = 2,
                         base_directories: Optional[Dict] = None,
                         folder_name: Optional[str] = None,
                         append_media_type_label: bool = False,
                         post_dates: Optional[Dict] = None) -> Tuple[int, int]:
        """Recursively apply sub-rules, moving matched files to sub-directories.

        Supports both fixed-destination mode (destination_dir is set) and
        split mode (base_directories + folder_name, destination_dir is None).

        Files not claimed by any sub-rule remain in *files_pool* so the caller
        can move them to the parent destination.

        Returns (files moved, files whose timestamp was set from post_dates).
        """
        prefix = ' ' * indent
        moved = 0
        dates_set = 0
        enabled = [sr for sr in (sub_rules or []) if sr.get('enabled', True)]
        base_directories = base_directories or {}

        for sr_idx, sr in enumerate(enabled, 1):
            sub_dir = (sr.get('sub_directory') or '').strip()
            if not sub_dir:
                logger.warning(f"Sub-rule {sr_idx} has no sub_directory — skipping")
                continue

            sr_needed = sr.get('needed_tags') or None
            sr_can_have = sr.get('can_have_tags') or None
            sr_exclude = sr.get('exclude_tags') or []
            sr_legacy = sr.get('tags') or []
            sr_pool_ids = sr.get('pool_ids') or None

            if not sr_needed and not sr_can_have and not sr_legacy and not sr_pool_ids:
                logger.warning(f"Sub-rule {sr_idx} ({sub_dir}) has no tags or pool IDs — skipping")
                continue

            try:
                sr_filenames = set()
                if sr_needed or sr_can_have or sr_legacy:
                    sr_filenames |= self.get_filenames_by_tags(
                        tags=sr_legacy if sr_legacy and not sr_needed and not sr_can_have else None,
                        needed_tags=sr_needed,
                        can_have_tags=sr_can_have,
                        exclude_tags=sr_exclude,
                        exclude_favorite_artists=False,
                    )
                if sr_pool_ids:
                    sr_filenames |= self.get_filenames_by_pools(
                        pool_ids=sr_pool_ids,
                        exclude_favorite_artists=False,
                        exclude_tags=sr_exclude,
                    )
            except Exception as e:
                logger.error(f"Sub-rule {sr_idx} ({sub_dir}) DB query failed: {e}")
                continue

            sr_post_ids = {
                post_id: requested
                for requested in sr_filenames
                if (post_id := self._get_post_id_from_filename(requested))
            }
            sr_files = {
                f: p for f, p in files_pool.items()
                if self._filename_matches(f, sr_filenames, sr_post_ids)
            }
            if not sr_files:
                print(f"{prefix}Sub-rule {sr_idx} ({sub_dir}): no matches")
                continue

            # Claim these files before recursing so they aren't double-moved
            for f in list(sr_files):
                del files_pool[f]

            sr_reason = sr_needed or sr_can_have or sr_legacy or (
                [f"pool{pid}" for pid in sr_pool_ids] if sr_pool_ids else None)

            # Compute the effective destination for this sub-rule:
            #   fixed mode: destination_dir/sub_dir
            #   split mode: base_directories + folder_name/sub_dir
            if destination_dir:
                sub_dest = os.path.join(destination_dir, sub_dir)
                sub_base_dirs: Dict = {}
                sub_folder: Optional[str] = None
            else:
                sub_dest = None
                sub_folder = os.path.join(folder_name, sub_dir) if folder_name else sub_dir
                sub_base_dirs = base_directories

            dest_label = sub_dest or f"{sub_folder} (split)"

            nested = [s for s in (sr.get('sub_rules') or []) if s.get('enabled', True)]
            if nested:
                print(f"{prefix}Sub-rule {sr_idx} ({sub_dir}): {len(sr_files)} file(s), "
                      f"applying {len(nested)} nested sub-rule(s)...")
                sub_moved, sub_dated = self._apply_sub_rules(
                    sr_files, sub_dest, nested, dry_run,
                    reason_tags=sr_reason, indent=indent + 2,
                    base_directories=sub_base_dirs, folder_name=sub_folder,
                    append_media_type_label=append_media_type_label,
                    post_dates=post_dates
                )
                moved += sub_moved
                dates_set += sub_dated
                # Move any files not claimed by nested sub-rules
                if sr_files:
                    print(f"{prefix}  Remainder → {dest_label}: {len(sr_files)} file(s)")
                    rem_moved, rem_dated = self.move_files_to_destination(
                        sr_files, sub_dest, sub_base_dirs, sub_folder, dry_run, reason_tags=sr_reason,
                        append_media_type_label=append_media_type_label,
                        post_dates=post_dates
                    )
                    moved += rem_moved
                    dates_set += rem_dated
            else:
                print(f"{prefix}Sub-rule {sr_idx} ({sub_dir}): {len(sr_files)} file(s) → {dest_label}")
                sr_moved, sr_dated = self.move_files_to_destination(
                    sr_files, sub_dest, sub_base_dirs, sub_folder, dry_run, reason_tags=sr_reason,
                    append_media_type_label=append_media_type_label,
                    post_dates=post_dates
                )
                moved += sr_moved
                dates_set += sr_dated

        return moved, dates_set

    def sort_by_tags(self, tags: List[str] = None, destination_dir: str = None, source_dirs: List[str] = None,
                     match_all: bool = False, exclude_favorite_artists: bool = False,
                     exclude_tags: List[str] = None,
                     exclude_higher_priority_dirs: List[str] = None,
                     only_fav_directory: bool = False,
                     needed_tags: List[str] = None,
                     can_have_tags: List[str] = None,
                     pool_ids: List[int] = None,
                     base_directories: Optional[Dict[str, str]] = None,
                     folder_name: Optional[str] = None,
                     sub_rules: Optional[List[Dict]] = None,
                     dry_run: bool = False,
                     append_media_type_label: bool = False,
                     set_post_date: bool = False) -> Dict:
        """
        Main sorting operation: query database, find files, move them.

        Args:
            tags: List of tags to filter by (legacy parameter, uses match_all logic)
            destination_dir: Where to move matching files
            source_dirs: Directories to search (uses config if not provided)
            match_all: Require all tags vs any tag (for legacy tags parameter)
            exclude_favorite_artists: If True, exclude posts from artists in artists.yaml
            exclude_tags: List of tags to exclude - posts with ANY of these tags will be filtered out
            exclude_higher_priority_dirs: Directories from higher-priority tag rules to exclude from search
            only_fav_directory: If True, only search in the favorites directory (ignores other source dirs)
            needed_tags: List of tags that MUST all be present (AND logic, takes precedence over tags)
            can_have_tags: List of tags where at least one must be present (OR logic, used with needed_tags)
            pool_ids: List of e621 pool IDs - posts belonging to ANY of these pools also match (OR'd with tags)
            base_directories: Mapping of media types to base directories (pictures/videos/gifs)
            folder_name: Folder name to append to the base directory for destination
            dry_run: Simulate without moving files
            set_post_date: If True, set each moved file's modified/created timestamp to
                match its e621 post's created_at date (keeps pools/comics in upload order)

        Returns:
            Dictionary with operation statistics
        """

        # Ensure exclude_tags is always a list
        if exclude_tags is None:
            exclude_tags = []
        elif isinstance(exclude_tags, str):
            exclude_tags = [exclude_tags] if exclude_tags else []
        elif not isinstance(exclude_tags, list):
            exclude_tags = []

        pool_ids = pool_ids or []

        # Determine which tag format is being used
        use_new_format = needed_tags is not None or can_have_tags is not None
        has_tag_criteria = bool(tags or needed_tags or can_have_tags)

        print(f"[SEARCH] Tag-based File Sorting")
        if use_new_format:
            if needed_tags:
                print(f"[Clipboard] Needed tags (ALL required): {', '.join(needed_tags)}")
            if can_have_tags:
                print(f" Can have tags (at least ONE): {', '.join(can_have_tags)}")
        elif has_tag_criteria:
            print(f"[Clipboard] Tags: {', '.join(tags or [])}")
            print(f"[CONFIG] Match mode: {'ALL tags (AND)' if match_all else 'ANY tag (OR)'}")
        if pool_ids:
            print(f"[Clipboard] Pool IDs (also matched, OR'd with tags): {', '.join(str(p) for p in pool_ids)}")
        base_directories = base_directories or {}

        if destination_dir:
            print(f"[FOLDER] Destination: {destination_dir}")
        elif folder_name and base_directories:
            print(f"[FOLDER] Destination folder: {folder_name} (per media type)")
            for media_type, base in base_directories.items():
                if base:
                    print(f"   - {media_type}: {os.path.join(base, folder_name)}")
        else:
            print("[FOLDER] Destination: (not set)")
        if exclude_favorite_artists:
            print(f"[BLOCKED] Excluding favorite artists from artists.yaml ({len(self.favorite_artist_aliases)} aliases)")
        if exclude_tags:
            print(f"[BLOCKED] Excluding posts with tags: {', '.join(exclude_tags)}")
        if only_fav_directory:
            print(f"[PIN] Searching ONLY in favorites directory")

        if dry_run:
            print("[ANALYZE] DRY RUN MODE - No files will be moved\n")

        # Step 1: Query database for matching filenames (tags and/or pools, OR'd together)
        print(f"Step 1: Querying {self.e621_config['database']} for matching posts...")
        try:
            matching_filenames = set()
            if has_tag_criteria:
                matching_filenames |= self.get_filenames_by_tags(
                    tags=tags,
                    match_all=match_all,
                    exclude_favorite_artists=exclude_favorite_artists,
                    exclude_tags=exclude_tags,
                    needed_tags=needed_tags,
                    can_have_tags=can_have_tags
                )
            if pool_ids:
                matching_filenames |= self.get_filenames_by_pools(
                    pool_ids=pool_ids,
                    exclude_favorite_artists=exclude_favorite_artists,
                    exclude_tags=exclude_tags
                )
            print(f"  [OK] Found {len(matching_filenames)} posts in database\n")
        except Exception as e:
            print(f"  [ERROR] Database query failed: {e}")
            return {'error': str(e), 'moved': 0}

        if not matching_filenames:
            print("[ERROR] No posts found with the specified tags")
            return {'db_matches': 0, 'files_found': 0, 'moved': 0}

        # Step 2: Determine source directories
        if not source_dirs:
            source_dirs = []

            if only_fav_directory:
                # Only search in favorites directory
                if hasattr(self.config, 'fav_directory') and self.config.fav_directory:
                    if os.path.exists(self.config.fav_directory):
                        source_dirs.append(self.config.fav_directory)
                        logger.info(f"Only searching in favorites directory: {self.config.fav_directory}")
                    else:
                        print(f"[ERROR] Favorites directory not found: {self.config.fav_directory}")
                        return {'db_matches': len(matching_filenames), 'files_found': 0, 'moved': 0}
                else:
                    print("[ERROR] Favorites directory not configured in config.yaml")
                    return {'db_matches': len(matching_filenames), 'files_found': 0, 'moved': 0}
            else:
                # Use config source directories
                if hasattr(self.config, 'source_directories'):
                    if isinstance(self.config.source_directories, str):
                        source_dirs.extend([d.strip() for d in self.config.source_directories.strip().split('\n') if d.strip()])
                    elif isinstance(self.config.source_directories, list):
                        source_dirs.extend(self.config.source_directories)

                # Include destination directories
                if hasattr(self.config, 'destination_directories'):
                    for key, path in self.config.destination_directories.items():
                        if os.path.exists(path):
                            source_dirs.append(path)

                # Include favorites directory for RECURSIVE scanning (including subdirectories)
                if hasattr(self.config, 'fav_directory') and self.config.fav_directory:
                    if os.path.exists(self.config.fav_directory):
                        source_dirs.append(self.config.fav_directory)
                        logger.info(f"Including favorites directory (recursive): {self.config.fav_directory}")

                # Include archived favorites directory for RECURSIVE scanning
                if hasattr(self.config, 'archived_favorites_directory') and self.config.archived_favorites_directory:
                    if os.path.exists(self.config.archived_favorites_directory):
                        source_dirs.append(self.config.archived_favorites_directory)
                        logger.info(f"Including archived favorites directory (recursive): {self.config.archived_favorites_directory}")

                # Include archive directories (allows pulling files from archive)
                archive_dirs = self._get_archive_directories()
                if archive_dirs:
                    source_dirs.extend(archive_dirs)
                    logger.info(f"Including {len(archive_dirs)} archive directories in search")
                    print(f"[ARCHIVE] Including {len(archive_dirs)} archive directories in search")

                # Include LOWER-priority tag directories (so higher-priority rules can pull files from them)
                # exclude_higher_priority_dirs contains directories from rules BEFORE this one
                # We want to include directories from rules AFTER this one (lower priority)
                if exclude_higher_priority_dirs is not None:
                    # Get all tag directories that aren't in the exclusion list (i.e., lower priority)
                    # We need to get all possible tag target directories from the config
                    try:
                        tag_config = self.config.config.get('tag_sorting', {})
                        if tag_config and isinstance(tag_config, dict):
                            all_rules = tag_config.get('rules', [])
                            for rule in all_rules:
                                if not rule.get('enabled', True):
                                    continue
                                rule_target = rule.get('target_directory')
                                if rule_target and os.path.exists(rule_target):
                                    # Include if NOT in higher priority list (i.e., it's lower or equal priority)
                                    if rule_target not in exclude_higher_priority_dirs:
                                        source_dirs.append(rule_target)
                                        logger.debug(f"Including tag directory for checking: {rule_target}")
                    except Exception as e:
                        logger.warning(f"Could not load tag directories for cross-checking: {e}")

                # Include this rule's own destination(s) so files already sorted there
                # (e.g. by a prior run) are still found — needed for operations like
                # "apply post dates now" that touch up files already in place.
                if destination_dir and os.path.exists(destination_dir):
                    source_dirs.append(destination_dir)
                if folder_name and base_directories:
                    for base in base_directories.values():
                        if base:
                            candidate = os.path.join(base, folder_name)
                            if os.path.exists(candidate):
                                source_dirs.append(candidate)

            # Remove duplicates and non-existent directories
            source_dirs = list(set([d for d in source_dirs if os.path.exists(d)]))

        # Prepare exclusion list - exclude the destination directory to avoid moving files to themselves
        # and exclude higher-priority tag directories (hierarchical exclusion)
        exclude_dirs = []

        # Note: we no longer exclude destination directories here so we can pull
        # misfiled media (e.g., videos/gifs sitting in the pictures folder) into
        # the correct per-type destination. Files already under their correct
        # destination are skipped later during move.

        # Exclude artist directory when exclude_favorite_artists is enabled
        if exclude_favorite_artists:
            if hasattr(self.config, 'artist_directory') and self.config.artist_directory:
                if os.path.exists(self.config.artist_directory):
                    exclude_dirs.append(self.config.artist_directory)
                    logger.info(f"Excluding artist directory (favorite artists): {self.config.artist_directory}")

        # Exclude higher-priority tag rule directories (hierarchical)
        if exclude_higher_priority_dirs:
            for higher_dir in exclude_higher_priority_dirs:
                if higher_dir and os.path.exists(higher_dir):
                    exclude_dirs.append(higher_dir)
                    logger.info(f"Excluding higher-priority tag directory: {higher_dir}")

        # Exclude any globally ignored sorting directories from config - unless this
        # rule matches by pool and pool_sort_search_ignored_directories opts back in
        if pool_ids and getattr(self.config, 'pool_sort_search_ignored_directories', False):
            logger.info("pool_sort_search_ignored_directories is enabled - not excluding sorting_ignore_directories")
        else:
            sorting_ignore_dirs = getattr(self.config, 'sorting_ignore_directories', None) or []
            for ignore_dir in sorting_ignore_dirs:
                if ignore_dir and os.path.exists(ignore_dir):
                    exclude_dirs.append(ignore_dir)
                    logger.info(f"Excluding globally ignored sorting directory: {ignore_dir}")

        if not source_dirs:
            print("[ERROR] No valid source directories found")
            return {'db_matches': len(matching_filenames), 'files_found': 0, 'moved': 0}

        total_scan_count = len(source_dirs)
        print(f"Step 2: Scanning {total_scan_count} directories (including favorites subdirectories)...")
        found_files = self.find_files_in_directories(matching_filenames, source_dirs,
                                                     surface_only_dirs=[],
                                                     exclude_dirs=exclude_dirs,
                                                     normalize_conditional_dnp=not dry_run)
        print(f"  [OK] Found {len(found_files)} matching files on disk\n")

        if not found_files:
            print("[ERROR] No matching files found in source directories")
            return {'db_matches': len(matching_filenames), 'files_found': 0, 'moved': 0}

        # Step 3: Move files — with optional sub-rule splitting
        # Determine reason tags for logging
        reason_tags_for_log = needed_tags if needed_tags else (can_have_tags if can_have_tags else tags)
        if not reason_tags_for_log and pool_ids:
            reason_tags_for_log = [f"pool{pid}" for pid in pool_ids]
        remaining_files = dict(found_files)
        moved_count = 0
        dates_set_count = 0

        post_dates = {}
        if set_post_date and not dry_run:
            try:
                post_dates = self.get_post_dates(list(found_files.keys()))
                print(f"[Clipboard] Resolved post dates for {len(post_dates)}/{len(found_files)} file(s)")
            except Exception as e:
                logger.warning(f"Failed to resolve post dates: {e}")

        enabled_sub_rules = [sr for sr in (sub_rules or []) if sr.get('enabled', True)]
        use_sub_rules = bool(enabled_sub_rules and (destination_dir or (base_directories and folder_name)))
        if use_sub_rules:
            dest_label = destination_dir or f"{folder_name} (split)"
            print(f"Step 3: Processing {len(enabled_sub_rules)} sub-rule(s), then remainder → {dest_label}...")
            sub_moved, sub_dated = self._apply_sub_rules(
                remaining_files, destination_dir, enabled_sub_rules, dry_run,
                reason_tags=reason_tags_for_log, indent=2,
                base_directories=base_directories, folder_name=folder_name,
                append_media_type_label=append_media_type_label,
                post_dates=post_dates
            )
            moved_count += sub_moved
            dates_set_count += sub_dated
        else:
            print(f"Step 3: Moving files to {destination_dir or folder_name}...")

        # Move remaining files to parent destination
        if remaining_files:
            if use_sub_rules:
                dest_label = destination_dir or f"{folder_name} (split)"
                print(f"  Remainder: {len(remaining_files)} file(s) → {dest_label}")
            rem_moved, rem_dated = self.move_files_to_destination(
                remaining_files, destination_dir, base_directories, folder_name, dry_run,
                reason_tags=reason_tags_for_log,
                append_media_type_label=append_media_type_label,
                post_dates=post_dates
            )
            moved_count += rem_moved
            dates_set_count += rem_dated

        # Summary
        print(f"\n{'=' * 60}")
        print("TAG SORT COMPLETE")
        print(f"{'=' * 60}")
        print(f"[CHART] Posts in database: {len(matching_filenames)}")
        print(f"[FOLDER] Files found on disk: {len(found_files)}")
        print(f"[OK] Files moved: {moved_count}")
        if set_post_date:
            print(f"[DATE] Post dates set: {dates_set_count}/{len(found_files)}")
        print(f"[UNKNOWN] Missing files: {len(matching_filenames) - len(found_files)}")
        print(f"{'=' * 60}")

        return {
            'db_matches': len(matching_filenames),
            'files_found': len(found_files),
            'moved': moved_count,
            'dates_set': dates_set_count,
            'missing': len(matching_filenames) - len(found_files)
        }

    def sort_by_pools(self, pool_ids: List[int], destination_dir: str = None, source_dirs: List[str] = None,
                      exclude_favorite_artists: bool = False,
                      exclude_tags: List[str] = None,
                      only_fav_directory: bool = False,
                      base_directories: Optional[Dict[str, str]] = None,
                      folder_name: Optional[str] = None,
                      dry_run: bool = False,
                      append_media_type_label: bool = False,
                      set_post_date: bool = False) -> Dict:
        """
        Sort files based on e621 pool membership: query database, find files, verify they
        are in the correct destination and move them there if not.

        Args:
            pool_ids: List of e621 pool IDs - files belonging to ANY of these pools match
            destination_dir: Where to move matching files
            source_dirs: Directories to search (uses config if not provided)
            exclude_favorite_artists: If True, exclude posts from artists in artists.yaml
            exclude_tags: List of tags to exclude - posts with ANY of these tags will be filtered out
            only_fav_directory: If True, only search in the favorites directory
            base_directories: Mapping of media types to base directories (pictures/videos/gifs)
            folder_name: Folder name to append to the base directory for destination
            dry_run: Simulate without moving files
            set_post_date: If True, set each moved file's modified/created timestamp to
                match its e621 post's created_at date (keeps pools/comics in upload order)

        Returns:
            Dictionary with operation statistics
        """
        if exclude_tags is None:
            exclude_tags = []
        elif isinstance(exclude_tags, str):
            exclude_tags = [exclude_tags] if exclude_tags else []
        elif not isinstance(exclude_tags, list):
            exclude_tags = []

        base_directories = base_directories or {}

        print(f"[SEARCH] Pool-based File Sorting")
        print(f"[Clipboard] Pool IDs: {', '.join(str(p) for p in pool_ids)}")

        if destination_dir:
            print(f"[FOLDER] Destination: {destination_dir}")
        elif folder_name and base_directories:
            print(f"[FOLDER] Destination folder: {folder_name} (per media type)")
            for media_type, base in base_directories.items():
                if base:
                    print(f"   - {media_type}: {os.path.join(base, folder_name)}")
        else:
            print("[FOLDER] Destination: (not set)")
        if exclude_favorite_artists:
            print(f"[BLOCKED] Excluding favorite artists from artists.yaml ({len(self.favorite_artist_aliases)} aliases)")
        if exclude_tags:
            print(f"[BLOCKED] Excluding posts with tags: {', '.join(exclude_tags)}")
        if only_fav_directory:
            print(f"[PIN] Searching ONLY in favorites directory")

        if dry_run:
            print("[ANALYZE] DRY RUN MODE - No files will be moved\n")

        # Step 1: Query database for matching filenames
        print(f"Step 1: Querying {self.e621_config['database']} for posts in pool(s)...")
        try:
            matching_filenames = self.get_filenames_by_pools(
                pool_ids=pool_ids,
                exclude_favorite_artists=exclude_favorite_artists,
                exclude_tags=exclude_tags
            )
            print(f"  [OK] Found {len(matching_filenames)} posts in database\n")
        except Exception as e:
            print(f"  [ERROR] Database query failed: {e}")
            return {'error': str(e), 'moved': 0}

        if not matching_filenames:
            print("[ERROR] No posts found for the specified pool(s)")
            return {'db_matches': 0, 'files_found': 0, 'moved': 0}

        # Step 2: Determine source directories
        if not source_dirs:
            source_dirs = []

            if only_fav_directory:
                if hasattr(self.config, 'fav_directory') and self.config.fav_directory:
                    if os.path.exists(self.config.fav_directory):
                        source_dirs.append(self.config.fav_directory)
                        logger.info(f"Only searching in favorites directory: {self.config.fav_directory}")
                    else:
                        print(f"[ERROR] Favorites directory not found: {self.config.fav_directory}")
                        return {'db_matches': len(matching_filenames), 'files_found': 0, 'moved': 0}
                else:
                    print("[ERROR] Favorites directory not configured in config.yaml")
                    return {'db_matches': len(matching_filenames), 'files_found': 0, 'moved': 0}
            else:
                if hasattr(self.config, 'source_directories'):
                    if isinstance(self.config.source_directories, str):
                        source_dirs.extend([d.strip() for d in self.config.source_directories.strip().split('\n') if d.strip()])
                    elif isinstance(self.config.source_directories, list):
                        source_dirs.extend(self.config.source_directories)

                if hasattr(self.config, 'destination_directories'):
                    for key, path in self.config.destination_directories.items():
                        if os.path.exists(path):
                            source_dirs.append(path)

                if hasattr(self.config, 'fav_directory') and self.config.fav_directory:
                    if os.path.exists(self.config.fav_directory):
                        source_dirs.append(self.config.fav_directory)
                        logger.info(f"Including favorites directory (recursive): {self.config.fav_directory}")

                if hasattr(self.config, 'archived_favorites_directory') and self.config.archived_favorites_directory:
                    if os.path.exists(self.config.archived_favorites_directory):
                        source_dirs.append(self.config.archived_favorites_directory)
                        logger.info(f"Including archived favorites directory (recursive): {self.config.archived_favorites_directory}")

                archive_dirs = self._get_archive_directories()
                if archive_dirs:
                    source_dirs.extend(archive_dirs)
                    logger.info(f"Including {len(archive_dirs)} archive directories in search")
                    print(f"[ARCHIVE] Including {len(archive_dirs)} archive directories in search")

                # Include this rule's own destination(s) so files already sorted there
                # (e.g. by a prior run) are still found — needed for operations like
                # "apply post dates now" that touch up files already in place.
                if destination_dir and os.path.exists(destination_dir):
                    source_dirs.append(destination_dir)
                if folder_name and base_directories:
                    for base in base_directories.values():
                        if base:
                            candidate = os.path.join(base, folder_name)
                            if os.path.exists(candidate):
                                source_dirs.append(candidate)

            source_dirs = list(set([d for d in source_dirs if os.path.exists(d)]))

        exclude_dirs = []
        if exclude_favorite_artists:
            if hasattr(self.config, 'artist_directory') and self.config.artist_directory:
                if os.path.exists(self.config.artist_directory):
                    exclude_dirs.append(self.config.artist_directory)
                    logger.info(f"Excluding artist directory (favorite artists): {self.config.artist_directory}")

        if getattr(self.config, 'pool_sort_search_ignored_directories', False):
            logger.info("pool_sort_search_ignored_directories is enabled - not excluding sorting_ignore_directories")
        else:
            sorting_ignore_dirs = getattr(self.config, 'sorting_ignore_directories', None) or []
            for ignore_dir in sorting_ignore_dirs:
                if ignore_dir and os.path.exists(ignore_dir):
                    exclude_dirs.append(ignore_dir)
                    logger.info(f"Excluding globally ignored sorting directory: {ignore_dir}")

        if not source_dirs:
            print("[ERROR] No valid source directories found")
            return {'db_matches': len(matching_filenames), 'files_found': 0, 'moved': 0}

        print(f"Step 2: Scanning {len(source_dirs)} directories (including favorites subdirectories)...")
        found_files = self.find_files_in_directories(matching_filenames, source_dirs,
                                                     surface_only_dirs=[],
                                                     exclude_dirs=exclude_dirs,
                                                     normalize_conditional_dnp=not dry_run)
        print(f"  [OK] Found {len(found_files)} matching files on disk\n")

        if not found_files:
            print("[ERROR] No matching files found in source directories")
            return {'db_matches': len(matching_filenames), 'files_found': 0, 'moved': 0}

        # Step 3: Move any files not already in the correct destination
        print(f"Step 3: Moving files to {destination_dir or folder_name}...")
        reason_tags_for_log = [f"pool{pid}" for pid in pool_ids]

        post_dates = {}
        if set_post_date and not dry_run:
            try:
                post_dates = self.get_post_dates(list(found_files.keys()))
                print(f"[Clipboard] Resolved post dates for {len(post_dates)}/{len(found_files)} file(s)")
            except Exception as e:
                logger.warning(f"Failed to resolve post dates: {e}")

        moved_count, dates_set_count = self.move_files_to_destination(
            found_files, destination_dir, base_directories, folder_name, dry_run,
            reason_tags=reason_tags_for_log,
            append_media_type_label=append_media_type_label,
            post_dates=post_dates
        )

        print(f"\n{'=' * 60}")
        print("POOL SORT COMPLETE")
        print(f"{'=' * 60}")
        print(f"[CHART] Posts in database: {len(matching_filenames)}")
        print(f"[FOLDER] Files found on disk: {len(found_files)}")
        print(f"[OK] Files moved: {moved_count}")
        if set_post_date:
            print(f"[DATE] Post dates set: {dates_set_count}/{len(found_files)}")
        print(f"[UNKNOWN] Missing files: {len(matching_filenames) - len(found_files)}")
        print(f"{'=' * 60}")

        return {
            'db_matches': len(matching_filenames),
            'files_found': len(found_files),
            'moved': moved_count,
            'dates_set': dates_set_count,
            'missing': len(matching_filenames) - len(found_files)
        }
