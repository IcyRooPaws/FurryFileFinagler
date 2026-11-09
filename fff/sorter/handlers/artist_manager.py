# fff: artists come from config/sort_artists.yaml (was configs/artists.yaml).
"""
Artist management for favorite file detection and processing.
"""
import os
import yaml
import re
import logging
import time
from fff.sorter import paths as sorter_paths
from .utils import (
    ensure_directory_exists, is_configured_file, move_file_with_logging
)

logger = logging.getLogger(__name__)


def artist_alias_in_filename(filename_lower, alias):
    """
    Check if an artist alias matches in the filename using more precise matching.
    This prevents false positives like 'benji' matching 'werebenjicom'.

    Special handling: aliases containing dots (e.g., 'fenn.perrox') also check
    with dots replaced by underscores, since platforms like e621 may normalize
    dots to underscores in filenames.
    """
    if _check_alias_patterns(filename_lower, alias):
        return True

    # Special rule: if alias contains dots, also try matching with dots as underscores
    if '.' in alias:
        if _check_alias_patterns(filename_lower, alias.replace('.', '_')):
            return True

    return False

def _check_alias_patterns(filename_lower, alias):
    """Check regex patterns for an alias match in the filename."""
    # Pattern 1: Artist name followed by underscore and parenthetical (e.g., "benji_(artist)")
    pattern1 = rf'\b{re.escape(alias)}_\('
    if re.search(pattern1, filename_lower):
        return True

    # Pattern 2: Artist name at word boundary followed by dash or underscore (e.g., "benji-explicit", "benji_commission")
    pattern2 = rf'\b{re.escape(alias)}[-_]'
    if re.search(pattern2, filename_lower):
        return True

    # Pattern 3: Artist name at the start of filename (e.g., "benji.jpg", "benji123.png")
    pattern3 = rf'^{re.escape(alias)}\b'
    if re.search(pattern3, filename_lower):
        return True

    # Pattern 4: Artist name as complete word surrounded by dots, dashes, or underscores
    # (e.g., "commission.benji.final.jpg", "art_benji_sketch.png")
    pattern4 = rf'[._-]{re.escape(alias)}[._-]'
    if re.search(pattern4, filename_lower):
        return True

    # Pattern 5: Artist name as complete word at the end (e.g., "commission_by_benji.jpg")
    pattern5 = rf'\b{re.escape(alias)}(?=\.|$)'
    if re.search(pattern5, filename_lower):
        return True

    return False


class ArtistManager:
    """Manages favorite artist detection and file processing."""

    def __init__(self, config, db_manager=None):
        self.config = config
        self.db_manager = db_manager
        self.fav_directory = config.fav_directory
        self.artist_directory = config.artist_directory
        self.manual_sort_directory = config.manual_sort_directory
        self.manual_sort_video_directory = config.manual_sort_video_directory
        self.destination_directories = config.destination_directories
        # Optional: directories that should be ignored by artist/tag sorting
        self.sorting_ignore_directories = getattr(config, 'sorting_ignore_directories', []) or []
        self.favorite_artists = self._load_favorite_artists()

    def _log_artist_scan_progress(self, scan_label, files_checked, matches_found, files_moved, last_log_time, root_dir=None, force=False):
        """Emit periodic INFO logs during long-running artist scans."""
        now = time.time()
        if not force and (now - last_log_time) < 2.0:
            return last_log_time

        if root_dir:
            logger.info(
                "Artist scan progress [%s] - checked: %d, matches: %d, moved: %d, current root: %s",
                scan_label,
                files_checked,
                matches_found,
                files_moved,
                root_dir,
            )
        else:
            logger.info(
                "Artist scan progress [%s] - checked: %d, matches: %d, moved: %d",
                scan_label,
                files_checked,
                matches_found,
                files_moved,
            )
        return now

    def _get_archive_directories(self):
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
                if os.path.exists(config_value):
                    archive_dirs.append(config_value)
                    logger.info(f"Found {archive_type} archive directory: {config_value}")
            # Expanded format: dict with images/videos/gifs
            elif isinstance(config_value, dict):
                for media_type, path in config_value.items():
                    if path and os.path.exists(path):
                        archive_dirs.append(path)
                        logger.info(f"Found {archive_type}/{media_type} archive directory: {path}")

        return archive_dirs

    def _get_tag_sort_exclude_dirs(self):
        """Return tag sorting target directories that exclude favorite artists."""
        if not hasattr(self.config, "get_tag_sorting_rules"):
            return []

        try:
            rules = self.config.get_tag_sorting_rules() or []
        except Exception as e:
            logger.warning("Could not load tag sorting rules for artist scan: %s", e)
            return []

        base_dirs = {}
        try:
            if hasattr(self.config, "get_tag_sorting_base_dirs"):
                base_dirs = self.config.get_tag_sorting_base_dirs() or {}
        except Exception as e:
            logger.warning("Could not load tag sorting base directories: %s", e)
            base_dirs = {}

        paths = []
        seen = set()
        for rule in rules:
            if not isinstance(rule, dict):
                continue

            if not rule.get("exclude_favorite_artists", False):
                continue

            folder_name = rule.get("folder_name")
            target_dir = rule.get("target_directory")

            candidate_dirs = []
            if folder_name and base_dirs:
                for base in base_dirs.values():
                    if base:
                        candidate_dirs.append(os.path.join(base, folder_name))

            if not candidate_dirs and target_dir:
                candidate_dirs.append(target_dir)

            for c_dir in candidate_dirs:
                abs_dir = os.path.abspath(c_dir)
                if abs_dir in seen:
                    continue
                seen.add(abs_dir)
                paths.append(c_dir)

        return paths

    def _resolve_file_path_case_insensitive(self, file_path, file_name):
        """
        Find the actual filesystem path for a file, handling case mismatches.

        Args:
            file_path: Path from database (may have wrong case)
            file_name: File name to search for

        Returns:
            Actual filesystem path if found, or None
        """
        # First check exact path
        if os.path.exists(file_path):
            return file_path

        # Path doesn't exist - try case-insensitive search
        # Get the directory and search for the file
        parent_dir = os.path.dirname(file_path)

        # Fast path: the parent directory exists and only the filename's case
        # differs (or the file is simply gone from there) — a single-directory
        # scan covers this in O(files in that dir) instead of walking a whole
        # subtree. This is the common case, so it avoids the expensive full
        # os.walk below for the vast majority of stale/missing candidates.
        if parent_dir and os.path.isdir(parent_dir):
            file_name_lower = file_name.lower()
            try:
                for entry in os.scandir(parent_dir):
                    if entry.is_file() and entry.name.lower() == file_name_lower:
                        return entry.path
            except OSError as e:
                logger.debug(f"Error scanning {parent_dir} for {file_name}: {e}")
            return None

        # Slow path: some ancestor directory itself is missing/renamed (case
        # mismatch in a directory component) — walk up to the nearest existing
        # ancestor and search its subtree for the file.
        search_root = parent_dir
        while search_root and not os.path.exists(search_root):
            search_root = os.path.dirname(search_root)

        if not search_root or not os.path.exists(search_root):
            return None

        # Search for the file in the directory tree
        try:
            for root, dirs, files in os.walk(search_root):
                if file_name in files:
                    actual_path = os.path.join(root, file_name)
                    # Verify this is a reasonable match (same relative structure)
                    if os.path.normpath(actual_path).lower() == os.path.normpath(file_path).lower():
                        return actual_path
        except Exception as e:
            logger.debug(f"Error searching for {file_name}: {e}")

        return None

    def _update_artist_check_if_in_db(self, file_name, matched_artist):
        """
        Update artist check columns for a file if it exists in any database table.
        Useful when moving files from source directories where we're not sure which table they're in.

        Args:
            file_name: Name of the file
            matched_artist: Canonical artist name if matched, None if no match

        Returns:
            bool: True if file was found and updated, False otherwise
        """
        if not self.db_manager:
            return False

        # Try to find which table contains this file
        try:
            tables = self.db_manager.get_all_table_names()
            for table in tables:
                conn = self.db_manager.get_db_connection()
                cursor = conn.cursor()
                cursor.execute(f"SELECT COUNT(*) FROM `{table}` WHERE file_name = %s", (file_name,))
                count = cursor.fetchone()[0]
                cursor.close()
                conn.close()

                if count > 0:
                    # File found in this table - update it
                    self.db_manager.update_artist_check(table, file_name, matched_artist)
                    logger.debug(f"Updated artist check for {file_name} in {table}")
                    return True
        except Exception as e:
            logger.debug(f"Could not update artist check for {file_name}: {e}")

        return False

    def _get_tag_sort_include_dirs(self):
        """Return tag sorting target directories that do NOT exclude favorite artists.

        These should be skipped during artist scans to honor the user's preference
        to leave artist content in those tag-sort destinations.
        """
        if not hasattr(self.config, "get_tag_sorting_rules"):
            return []

        try:
            rules = self.config.get_tag_sorting_rules() or []
        except Exception as e:
            logger.warning("Could not load tag sorting rules for include list: %s", e)
            return []

        base_dirs = {}
        try:
            if hasattr(self.config, "get_tag_sorting_base_dirs"):
                base_dirs = self.config.get_tag_sorting_base_dirs() or {}
        except Exception as e:
            logger.warning("Could not load tag sorting base directories: %s", e)
            base_dirs = {}

        paths = []
        seen = set()
        for rule in rules:
            if not isinstance(rule, dict):
                continue

            if rule.get("exclude_favorite_artists", False):
                continue

            folder_name = rule.get("folder_name")
            target_dir = rule.get("target_directory")

            candidate_dirs = []
            if folder_name and base_dirs:
                for base in base_dirs.values():
                    if base:
                        candidate_dirs.append(os.path.join(base, folder_name))

            if not candidate_dirs and target_dir:
                candidate_dirs.append(target_dir)

            for c_dir in candidate_dirs:
                abs_dir = os.path.abspath(c_dir)
                if abs_dir in seen:
                    continue
                seen.add(abs_dir)
                paths.append(c_dir)

        return paths

    def _load_favorite_artists(self):
        """Load favorite artists from artists.yaml.

        Supports two formats:
        1. New format (dict with aliases, target_directory, enabled):
           ArtistName:
             aliases: [alias1, alias2]
             target_directory: P:/Path/To/Artist/Folder
             enabled: true

        2. Legacy format (list of aliases only):
           ArtistName:
             - alias1
             - alias2
        """
        try:
            # fff: config/sort_artists.yaml; target folders rerouted in the test environment
            artists_data = sorter_paths.load_yaml_for_env(sorter_paths.SORT_ARTISTS)

            favorite_artists = {}
            for canonical, artist_config in artists_data.items():
                # Check if new format (dict with 'aliases' key) or legacy format (list)
                if isinstance(artist_config, dict):
                    # New format with configuration options
                    enabled = artist_config.get('enabled', True)
                    if not enabled:
                        continue

                    aliases = artist_config.get('aliases', [])
                    target_dir = artist_config.get('target_directory', None)

                    # If no target_directory specified, use default artist_directory
                    if not target_dir:
                        target_dir = os.path.join(self.artist_directory, canonical)

                    favorite_artists[canonical] = {
                        'aliases': [alias.lower() for alias in aliases],
                        'target_directory': target_dir
                    }
                elif isinstance(artist_config, list):
                    # Legacy format - list of aliases
                    favorite_artists[canonical] = {
                        'aliases': [alias.lower() for alias in artist_config],
                        'target_directory': os.path.join(self.artist_directory, canonical)
                    }

            logger.info(f"Loaded {len(favorite_artists)} favorite artists from configs/artists.yaml")
            return favorite_artists
        except Exception as e:
            logger.error("Error loading configs/artists.yaml: %s", e)
            return {}

    def check_artist_match(self, filename_lower, alias):
        """See :func:`artist_alias_in_filename`."""
        return artist_alias_in_filename(filename_lower, alias)

    def _get_artist_candidates_from_db(self, cache_days=7):
        """Query all_files view from database and return candidates that need artist checking.

        Args:
            cache_days: Skip files checked within this many days (default: 7)

        Returns:
            List of tuples: (file_name, file_path, hash_value, table_name)
        """
        if not self.db_manager:
            logger.warning("Database manager not available, skipping DB-backed artist candidate query")
            return []

        try:
            # Query the all_files view, excluding:
            # 1. Files already in artist or fav directories
            # 2. Files checked recently (within cache_days)
            # 3. Files with matched artists (unless artist_matched is null - file was checked but no match)
            from .utils import normalize_path
            artist_dir_norm = normalize_path(os.path.abspath(self.artist_directory))
            fav_dir_norm = normalize_path(os.path.abspath(self.fav_directory))

            # Build extra exclusion clauses for any other "already sorted" directories
            extra_exclude_dirs = []
            archived_fav_dir = getattr(self.config, 'archived_favorites_directory', None)
            if archived_fav_dir:
                extra_exclude_dirs.append(normalize_path(os.path.abspath(archived_fav_dir)))

            # Also exclude tag/pool sorting destinations that explicitly do NOT
            # exclude favorite artists (exclude_favorite_artists=False) — the user
            # wants favorite-artist content to stay there rather than being pulled
            # into the artist folder. The filesystem-walk fallback already honors
            # this via _get_tag_sort_include_dirs(); the DB-backed path needs the
            # same protection so pool/tag sorting isn't undone by artist sorting.
            for include_dir in self._get_tag_sort_include_dirs():
                try:
                    extra_exclude_dirs.append(normalize_path(os.path.abspath(include_dir)))
                except Exception:
                    continue

            extra_clauses = ''.join(
                "\n                  AND file_path NOT LIKE CONCAT(%s, '%%')"
                for _ in extra_exclude_dirs
            )

            query = f"""
                SELECT file_name, file_path,
                       COALESCE(perceptual_hash_dhash, perceptual_hash_phash, perceptual_hash_average, perceptual_hash_whash, md5_hash) as hash_value,
                       table_name
                FROM all_files
                WHERE file_path NOT LIKE CONCAT(%s, '%%')
                  AND file_path NOT LIKE CONCAT(%s, '%%'){extra_clauses}
                  AND (artist_matched IS NULL OR artist_matched = '')
                  AND (
                    last_artist_check IS NULL
                    OR last_artist_check < DATE_SUB(NOW(), INTERVAL %s DAY)
                  )
                ORDER BY table_name, file_name
            """

            rows = self.db_manager.execute_query(query, [artist_dir_norm, fav_dir_norm] + extra_exclude_dirs + [cache_days])

            # All returned rows are candidates that need checking (WHERE clause already filters by cache_days)
            logger.info(f"Retrieved {len(rows)} candidate files for artist checking")
            return rows
        except Exception as e:
            logger.error(f"Error querying all_files view: {e}")
            return []

    def move_favorite_files(self, source_directories):
        """Move favorite files from source directories to favorites folder."""
        files_moved = 0
        scan_subdirs = getattr(self.config, 'scan_subdirectories', True)

        # Normalize ignore directories for comparison
        ignore_paths = [os.path.normpath(os.path.abspath(d))
                        for d in self.sorting_ignore_directories if d]

        for source_directory in source_directories:
            if not os.path.exists(source_directory):
                logger.info("Source directory does not exist: %s", source_directory)
                continue

            # Skip source directories that are configured to be ignored for sorting
            normalized_source = os.path.normpath(os.path.abspath(source_directory))
            if any(normalized_source.startswith(p) for p in ignore_paths):
                logger.info("Skipping source directory (sorting_ignore_directories): %s", source_directory)
                continue

            if scan_subdirs:
                # Walk through all subdirectories as well
                for root, dirs, files in os.walk(source_directory):
                    # Handle special child directories
                    for d in list(dirs):
                        dir_lower = d.lower()
                        dir_path = os.path.join(root, d)
                        if dir_lower in ("adult", "mature"):
                            logger.info(f"Sorting files in special directory '{dir_path}' to nsfw destinations by type")
                            print(f"Sorting files in special directory '{dir_path}' to nsfw destinations by type")
                            for entry in os.scandir(dir_path):
                                if entry.is_file() and is_configured_file(entry.name):
                                    from .utils import is_image, is_gif, is_video
                                    if is_image(entry.name):
                                        dest = self.destination_directories.get("nsfw_image")
                                    elif is_gif(entry.name):
                                        dest = self.destination_directories.get("nsfw_gif")
                                    elif is_video(entry.name):
                                        dest = self.destination_directories.get("nsfw_video")
                                    else:
                                        dest = None
                                    if dest:
                                        ensure_directory_exists(dest)
                                        destination_path = os.path.join(dest, entry.name)
                                        move_file_with_logging(
                                            entry.path, destination_path,
                                            db_manager=self.db_manager, logger_obj=logger,
                                            destination_directory=os.path.basename(dest),
                                            reason='favorite_special_category'
                                        )
                                        logger.info(f"Moved '{entry.name}' from '{dir_path}' to '{dest}'")
                            # Remove the directory from further walk
                            dirs.remove(d)

                    # Process files for favorite artist matches
                    files_in_directory = [f for f in files if is_configured_file(f)]
                    if files_in_directory:
                        logger.info(f"Processing {len(files_in_directory)} files in '{root}' for favorite artists")
                        print(f"Processing {len(files_in_directory)} files in '{root}' for favorite artists")

                    for file in files:
                        if is_configured_file(file):
                            file_path = os.path.join(root, file)
                            file_lower = file.lower()
                            for canonical, artist_info in self.favorite_artists.items():
                                aliases = artist_info['aliases']
                                target_folder = artist_info['target_directory']
                                if any(self.check_artist_match(file_lower, alias) for alias in aliases):
                                    # Skip if already in the correct target directory (or a subdirectory of it)
                                    if os.path.normcase(os.path.normpath(root)).startswith(os.path.normcase(os.path.normpath(target_folder))):
                                        logger.debug("Skipping '%s' - already in target folder '%s'", file, target_folder)
                                        break
                                    print(f"Moving favorite file '{file}' to '{canonical}' artist folder.")
                                    logger.info(f"Moving favorite file '{file}' to '{canonical}' artist folder.")

                                    # Update artist check in database if file exists there
                                    self._update_artist_check_if_in_db(file, canonical)

                                    ensure_directory_exists(target_folder)
                                    destination_path = os.path.join(target_folder, file)
                                    move_file_with_logging(
                                        file_path, destination_path,
                                        db_manager=self.db_manager, logger_obj=logger,
                                        destination_directory='artist_directory',
                                        reason=f'favorite_artist_{canonical}'
                                    )
                                    logger.info("Favorite file '%s' moved to '%s' (match: %s).",
                                                file, destination_path, canonical)
                                    files_moved += 1
                                    break
            else:
                # Only scan files in the root of source directory (no subdirectories)
                logger.info(f"Scanning only root of source directory: {source_directory}")
                print(f"Scanning only root of source directory: {source_directory}")

                for entry in os.scandir(source_directory):
                    if entry.is_file() and is_configured_file(entry.name):
                        file = entry.name
                        file_path = entry.path
                        file_lower = file.lower()
                        for canonical, artist_info in self.favorite_artists.items():
                            aliases = artist_info['aliases']
                            target_folder = artist_info['target_directory']
                            if any(self.check_artist_match(file_lower, alias) for alias in aliases):
                                # Skip if already in the correct target directory (or a subdirectory of it)
                                source_dir = os.path.dirname(file_path)
                                if os.path.normcase(os.path.normpath(source_dir)).startswith(os.path.normcase(os.path.normpath(target_folder))):
                                    logger.debug("Skipping '%s' - already in target folder '%s'", file, target_folder)
                                    break
                                print(f"Moving favorite file '{file}' to '{canonical}' artist folder.")
                                logger.info(f"Moving favorite file '{file}' to '{canonical}' artist folder.")

                                # Update artist check in database if file exists there
                                self._update_artist_check_if_in_db(file, canonical)

                                ensure_directory_exists(target_folder)
                                destination_path = os.path.join(target_folder, file)
                                move_file_with_logging(
                                    file_path, destination_path,
                                    db_manager=self.db_manager, logger_obj=logger,
                                    destination_directory='artist_directory',
                                    reason=f'favorite_artist_{canonical}'
                                )
                                logger.info("Favorite file '%s' moved to '%s' (match: %s).",
                                            file, destination_path, canonical)
                                files_moved += 1
                                break
        return files_moved

    def check_favorites_in_destinations(self, debug_mode=False, scan_all_fav_subdirs=False, skip_archives=None):
        """
        Check for favorite files already in destination directories.

        Args:
            debug_mode: Enable verbose debug output
            scan_all_fav_subdirs: If True, recursively scan ALL subdirectories of favorites folder
                                  If False, only scan favorites root + specific subdirs (default behavior)
            skip_archives: If True, skip scanning archive directories for performance.
                          If None, uses value from config (default)
        """
        # Try DB-backed approach first if database manager is available
        if self.db_manager:
            # Check if user has explicitly requested filesystem scanning
            use_filesystem = getattr(self.config, 'force_filesystem_artist_scan', False)

            if use_filesystem:
                # User explicitly requested filesystem scanning via config
                logger.info("Using filesystem scanning (force_filesystem_scan: true in config)")
                print("[CONFIG] Using filesystem scanning as requested in artist_processing.force_filesystem_scan")
                return self._check_favorites_in_destinations_filesystem(debug_mode, scan_all_fav_subdirs, skip_archives)

            # Try database-backed scanning
            try:
                result = self._check_favorites_in_destinations_db(debug_mode, skip_archives)
                # If DB scan returned 0 candidates but we expect files, fall back to filesystem
                # (This handles cases where all_files view doesn't exist or isn't populated)
                if result == 0:
                    logger.warning("DB-backed artist scan found 0 candidates - this may indicate the all_files view doesn't exist")
                    logger.info("Falling back to filesystem scanning for artist detection")
                    print("\n[WARNING] DB scan found 0 candidates - falling back to filesystem scan")
                    print("[INFO] To avoid this, either:")
                    print("        1. Create the all_files view: mysql < 'sql views/all_files_view.sql'")
                    print("        2. Set force_filesystem_scan: true in artist_processing config")
                    return self._check_favorites_in_destinations_filesystem(debug_mode, scan_all_fav_subdirs, skip_archives)
                return result
            except Exception as e:
                logger.error(f"DB-backed artist scan failed: {e}")
                logger.info("Falling back to filesystem scanning")
                print(f"\n[ERROR] DB scan failed: {e}")
                print("[INFO] Falling back to filesystem scan")
                print("[TIP] To skip DB queries, set force_filesystem_scan: true in artist_processing config")
                return self._check_favorites_in_destinations_filesystem(debug_mode, scan_all_fav_subdirs, skip_archives)

        # Fallback to filesystem walk if no DB manager
        return self._check_favorites_in_destinations_filesystem(debug_mode, scan_all_fav_subdirs, skip_archives)

    def _check_favorites_in_destinations_db(self, debug_mode=False, skip_archives=None):
        """DB-backed version of artist processing - much faster than filesystem walk."""
        if skip_archives is None:
            skip_archives = getattr(self.config, 'skip_archives_in_artist_scan', True)

        # Get cache configuration
        cache_days = getattr(self.config, 'artist_check_cache_days', 7)

        files_moved = 0
        total_files_checked = 0
        matches_found = 0
        cache_hits = 0
        cache_updates = 0
        start_time = time.time()

        print(f"\n{'='*80}")
        print("CHECKING FOR FAVORITE FILES (DB-BACKED WITH CACHE)")
        print(f"[DB] Using database query with {cache_days}-day cache")
        print(f"{'='*80}")
        print(f"[INFO] Loaded {len(self.favorite_artists)} favorite artists")

        # Build set of all artist target folders (normalized, lowercased) for pre-filtering
        artist_target_folders = set()
        for canonical, artist_info in self.favorite_artists.items():
            folder = os.path.normcase(os.path.abspath(artist_info['target_directory']))
            artist_target_folders.add(folder)

        # Get candidate files from database (includes cache filtering)
        candidates = self._get_artist_candidates_from_db(cache_days=cache_days)
        raw_count = len(candidates)

        # Track path case corrections
        path_corrections = 0

        # Pre-filter: skip files already inside any artist target folder (at any
        # depth) — a file nested in a child directory of an artist folder is
        # left in place, not flattened up to the target folder's root.
        filtered_candidates = []
        skipped_count = 0
        for row in candidates:
            file_name, file_path = row[0], row[1]
            file_abs = os.path.normcase(os.path.abspath(file_path))
            if any(file_abs.startswith(folder + os.sep) or file_abs.startswith(folder + '/') for folder in artist_target_folders):
                skipped_count += 1
                continue
            filtered_candidates.append(row)

        candidates = filtered_candidates
        total_files_checked = len(candidates)

        print(f"[DB] Retrieved {raw_count} files, skipped {skipped_count} already in artist folders")
        print(f"[DB] Processing {total_files_checked} candidate files")
        logger.info(f"DB-backed artist scan: {raw_count} from DB, {skipped_count} pre-filtered, {total_files_checked} to check")

        if not candidates:
            print("[INFO] No candidates to process")
            return 0

        # Cache-update writes are batched instead of one DB round trip (+ commit)
        # per file — with tens of thousands of candidates, that per-file commit
        # overhead dominates scan time far more than the in-memory matching does.
        pending_cache_updates = {}  # table_name -> [(matched_artist, file_name), ...]
        FLUSH_THRESHOLD = 500

        def _queue_cache_update(table_name, file_name, matched_artist):
            if not table_name or not self.db_manager:
                return
            bucket = pending_cache_updates.setdefault(table_name, [])
            bucket.append((matched_artist, file_name))
            if len(bucket) >= FLUSH_THRESHOLD:
                self.db_manager.update_artist_checks_batch(table_name, bucket)
                pending_cache_updates[table_name] = []

        def _flush_all_cache_updates():
            for t_name, bucket in pending_cache_updates.items():
                if bucket:
                    self.db_manager.update_artist_checks_batch(t_name, bucket)
            pending_cache_updates.clear()

        # Process each candidate
        progress_time = start_time
        for idx, row in enumerate(candidates):
            file_name, file_path = row[0], row[1]
            file_hash = row[2] if len(row) > 2 else None
            table_name = row[3] if len(row) > 3 else None

            # Progress indicator
            if idx > 0 and idx % 500 == 0:
                now = time.time()
                if now - progress_time >= 2.0:
                    status_parts = [f"moved: {files_moved}"]
                    if path_corrections > 0:
                        status_parts.append(f"path fixes: {path_corrections}")
                    status_parts.append(f"cache: {cache_updates}")
                    status_msg = ", ".join(status_parts)
                    print(f"[TIME] Processed: {idx}/{total_files_checked} files ({status_msg})...")
                    progress_time = now

            file_lower = file_name.lower()
            matched_artist = None

            # Resolve actual file path (handle case mismatches)
            actual_file_path = file_path
            if not os.path.exists(file_path):
                resolved_path = self._resolve_file_path_case_insensitive(file_path, file_name)
                if resolved_path:
                    actual_file_path = resolved_path
                    path_corrections += 1
                    # Update database with correct path case
                    if table_name and self.db_manager:
                        try:
                            self.db_manager.update_file_path(table_name, file_name, resolved_path)
                            logger.debug(f"Corrected path case for {file_name}: {file_path} -> {resolved_path}")
                        except Exception as e:
                            logger.debug(f"Failed to update path case: {e}")
                else:
                    # File truly doesn't exist - skip it but still update cache
                    logger.debug(f"File not found: {file_name} (expected at {file_path})")
                    if table_name and self.db_manager:
                        _queue_cache_update(table_name, file_name, None)
                        cache_updates += 1
                    continue

            # Check against favorite artists
            for canonical, artist_info in self.favorite_artists.items():
                aliases = artist_info['aliases']
                target_folder = artist_info['target_directory']

                if any(self.check_artist_match(file_lower, alias) for alias in aliases):
                    print(f"   [MATCH] '{file_name}' matches artist '{canonical}'")
                    print(f"          From: {actual_file_path}")
                    matches_found += 1
                    matched_artist = canonical

                    # Queue cache update BEFORE moving (while still in source table)
                    if table_name and self.db_manager:
                        _queue_cache_update(table_name, file_name, matched_artist)

                    # Move the file
                    try:
                        ensure_directory_exists(target_folder)
                        destination_path = os.path.join(target_folder, file_name)

                        move_file_with_logging(
                            actual_file_path, destination_path,
                            db_manager=self.db_manager, logger_obj=logger,
                            destination_directory='artist_directory',
                            reason=f'artist_post_scan_{canonical}'
                        )
                        print(f"   [OK] Moved to: {destination_path}")
                        logger.info(f"Artist file '{file_name}' moved to '{destination_path}' (match: {canonical})")
                        files_moved += 1
                    except Exception as e:
                        print(f"   [ERROR] Failed to move '{file_name}': {e}")
                        logger.error(f"Failed to move artist file '{file_name}': {e}")
                    break

            # Queue cache update after checking (only if no match - matched files already queued before move)
            if table_name and self.db_manager and matched_artist is None:
                _queue_cache_update(table_name, file_name, None)
                cache_updates += 1
            elif matched_artist:
                # File was matched and moved - count it
                cache_updates += 1

        _flush_all_cache_updates()

        elapsed = time.time() - start_time
        print(f"\n{'='*80}")
        print("DB-BACKED ARTIST SCAN COMPLETE")
        print(f"{'='*80}")
        print(f"[INFO] Total files checked: {total_files_checked}")
        if path_corrections > 0:
            print(f"[FIX] Path case corrections: {path_corrections}")
        print(f"[CACHE] Cache updates: {cache_updates}")
        print(f"[MATCH] Total matches found: {matches_found}")
        print(f"[OK] Total files moved: {files_moved}")
        print(f"[TIME] Elapsed: {elapsed:.2f}s")
        print(f"{'='*80}\n")

        logger.info(
            f"DB-backed artist scan complete - checked: {total_files_checked}, path_corrections: {path_corrections}, cache_updates: {cache_updates}, matches: {matches_found}, moved: {files_moved}, elapsed: {elapsed:.2f}s"
        )

        return files_moved

    def _check_favorites_in_destinations_filesystem(self, debug_mode=False, scan_all_fav_subdirs=False, skip_archives=None):
        """Filesystem walk version of artist processing (fallback when DB is unavailable)."""
        # Use config value if not explicitly provided
        if skip_archives is None:
            skip_archives = getattr(self.config, 'skip_archives_in_artist_scan', True)
        files_moved = 0
        total_files_checked = 0
        matches_found = 0

        print(f"\n{'='*80}")
        print("CHECKING FOR ADDITIONAL FAVORITE FILES IN DESTINATION DIRECTORIES")
        if debug_mode:
            print("[BUG] DEBUG MODE: Will show detailed matching attempts")
        if scan_all_fav_subdirs:
            print("[SEARCH] RECURSIVE MODE: Will scan ALL subdirectories of favorites folder")
        print(f"{'='*80}")
        print(f"[Notes] Loaded {len(self.favorite_artists)} favorite artists")
        logger.info(
            "Artist processing scan started - favorite artists: %d, debug_mode: %s, scan_all_fav_subdirs: %s, skip_archives: %s",
            len(self.favorite_artists),
            debug_mode,
            scan_all_fav_subdirs,
            skip_archives,
        )

        # Build scan list: destination dirs + tag-sorting dirs that exclude artists
        tag_exclude_dirs = self._get_tag_sort_exclude_dirs() or []
        tag_include_dirs = self._get_tag_sort_include_dirs() or []
        if tag_exclude_dirs:
            print(f"[TAG] Tag-sort directories marked exclude_favorite_artists: {len(tag_exclude_dirs)}")
            for d in tag_exclude_dirs:
                print(f"   [TAG] {d}")
        else:
            print("[TAG] No tag-sort directories with exclude_favorite_artists found")

        if tag_include_dirs:
            print(f"[TAG] Tag-sort directories NOT excluding artists (will be skipped): {len(tag_include_dirs)}")
            for d in tag_include_dirs:
                print(f"   [TAG-SKIP] {d}")

        directories_to_scan = list(self.destination_directories.items())
        directories_to_scan.extend((f"tag_rule_exclude_{i}", d) for i, d in enumerate(tag_exclude_dirs, 1))

        # Add favorites root directory to scan for artist sorting
        if self.fav_directory and os.path.exists(self.fav_directory):
            directories_to_scan.append(("fav_root", self.fav_directory))

        # Add manual sort directories to scan for artist sorting
        if self.manual_sort_directory and os.path.exists(self.manual_sort_directory):
            directories_to_scan.append(("manual_sort_pictures", self.manual_sort_directory))
        if self.manual_sort_video_directory and os.path.exists(self.manual_sort_video_directory):
            directories_to_scan.append(("manual_sort_videos", self.manual_sort_video_directory))

        # Archived favorites should never be re-sorted by artist (files are already curated)
        archived_fav_dir = getattr(self.config, 'archived_favorites_directory', None)
        archived_fav_abs = os.path.normcase(os.path.abspath(archived_fav_dir)) if archived_fav_dir else None

        archive_dirs = [] if skip_archives else (self._get_archive_directories() or [])
        # Strip archived_favorites from the archive scan list regardless of skip_archives setting
        if archived_fav_abs:
            archive_dirs = [d for d in archive_dirs
                            if os.path.normcase(os.path.abspath(d)) != archived_fav_abs]

        if skip_archives:
            print("[SPEED] Skipping archive directories for performance (use --scan-archives to include)")
            logger.info("Artist processing scan will skip archive directories for performance")
        elif archive_dirs:
            directories_to_scan.extend((f"archive_{i}", d) for i, d in enumerate(archive_dirs, 1))
            print(f"[ARCHIVE] Including {len(archive_dirs)} archive directories in artist scan")
            for archive_dir in archive_dirs:
                print(f"   [ARCHIVE] {archive_dir}")
        if archived_fav_abs:
            print(f"[SKIP] Excluding archived_favorites directory from artist scan: {archived_fav_dir}")

        # Show the scan plan for easier troubleshooting
        print("[PLAN] Artist scan directories (destinations + tag-sort excludes + fav root + manual sort + archives):")
        for key, path in directories_to_scan:
            print(f"   [DIR] {key}: {os.path.abspath(path)}")
        logger.info(
            "Artist processing scan plan - directories: %d, tag_exclude_dirs: %d, tag_include_dirs: %d, archive_dirs: %d",
            len(directories_to_scan),
            len(tag_exclude_dirs),
            len(tag_include_dirs),
            len(archive_dirs),
        )

        # Check all destination directories (but avoid duplicate processing)
        processed_dirs = set()

        tag_exclude_abs = {os.path.abspath(p) for p in tag_exclude_dirs}
        tag_include_abs = {os.path.abspath(p) for p in tag_include_dirs}

        # Build set of all artist target folders to skip during walk
        artist_target_abs = set()
        for artist_info in self.favorite_artists.values():
            artist_target_abs.add(os.path.normcase(os.path.abspath(artist_info['target_directory'])))

        for index, (key, dest_dir) in enumerate(directories_to_scan, 1):
            abs_dest_dir = os.path.abspath(dest_dir)

            if not os.path.exists(abs_dest_dir):
                print(f"[WARNING]  Skipping {key}: Directory '{abs_dest_dir}' does not exist")
                continue

            # Avoid processing the same directory multiple times
            if abs_dest_dir in processed_dirs:
                print(f"[NEXT]  Skipping {key}: Directory '{dest_dir}' already processed")
                continue
            processed_dirs.add(abs_dest_dir)

            print(f"\n[FOLDER] Processing directory: {key}")
            print(f"   Path: {abs_dest_dir}")
            logger.info("Artist scan directory start [%d/%d]: %s -> %s", index, len(directories_to_scan), key, abs_dest_dir)

            dir_files_checked = 0
            dir_matches_found = 0
            dir_files_moved = 0
            dir_start_time = time.time()
            progress_log_time = dir_start_time

            # Walk through the directory
            for root_dir, dirs, files in os.walk(abs_dest_dir):
                if abs_dest_dir in tag_exclude_abs and root_dir == abs_dest_dir:
                    print(f"   [TAG-SCAN] Entering tag-sort exclude directory: {abs_dest_dir}")

                # Skip tag-sort directories that are configured to include artists
                # Prune traversal early to avoid scanning their contents
                dirs[:] = [d for d in dirs if not os.path.abspath(os.path.join(root_dir, d)).startswith(tuple(tag_include_abs))]
                if any(root_dir.startswith(inc) for inc in tag_include_abs):
                    continue

                # Prune artist target folders - no need to scan files already in their correct location
                dirs[:] = [d for d in dirs
                           if os.path.normcase(os.path.abspath(os.path.join(root_dir, d))) not in artist_target_abs]

                # Skip if current directory IS an artist target folder
                if os.path.normcase(os.path.abspath(root_dir)) in artist_target_abs:
                    continue

                # If scanning fav_directory root, skip subdirectories (but allow the root itself)
                # This prevents moving files that are already in artist-specific subdirectories
                if abs_dest_dir == os.path.abspath(self.fav_directory):
                    if root_dir != abs_dest_dir:
                        # We're in a subdirectory of fav_directory, skip it
                        continue
                    # We're at the root of fav_directory, continue processing
                # For other directories, skip if we're inside the favorites/artist directories
                elif (os.path.abspath(root_dir).startswith(os.path.abspath(self.fav_directory)) or \
                     os.path.abspath(root_dir).startswith(os.path.abspath(self.artist_directory))) and abs_dest_dir not in tag_exclude_abs:
                    continue

                files_to_check = [f for f in files if is_configured_file(f)]
                if not files_to_check:
                    continue

                # Progress indicator every 100 files
                if dir_files_checked > 0 and dir_files_checked % 100 == 0:
                    print(f"   [TIME] Progress: {dir_files_checked} files checked, {dir_matches_found} matches found...")

                print(f"   [FOLDER] Checking {len(files_to_check)} files in '{root_dir}'")
                dir_files_checked += len(files_to_check)
                progress_log_time = self._log_artist_scan_progress(
                    key,
                    dir_files_checked,
                    dir_matches_found,
                    dir_files_moved,
                    progress_log_time,
                    root_dir=root_dir,
                )

                for file in files_to_check:
                    file_path = os.path.join(root_dir, file)
                    file_lower = file.lower()

                    if debug_mode and dir_files_checked <= 3:
                        print(f"      [SEARCH] Checking file: '{file}'")

                    # Check against favorite artists
                    for canonical, artist_info in self.favorite_artists.items():
                        aliases = artist_info['aliases']
                        target_folder = artist_info['target_directory']
                        if any(self.check_artist_match(file_lower, alias) for alias in aliases):
                            print(f"   [TARGET] MATCH FOUND: '{file}' matches artist '{canonical}'")
                            matches_found += 1
                            dir_matches_found += 1

                            # Move the file
                            ensure_directory_exists(target_folder)
                            destination_path = os.path.join(target_folder, file)

                            try:
                                move_file_with_logging(
                                    file_path, destination_path,
                                    db_manager=self.db_manager, logger_obj=logger,
                                    destination_directory='artist_directory',
                                    reason=f'artist_destination_scan_{canonical}'
                                )
                                print(f"   [OK] MOVED: '{file}' -> '{destination_path}'")
                                logger.info("Favorite file '%s' moved from '%s' to '%s' (match: %s).",
                                            file, root_dir, destination_path, canonical)
                                files_moved += 1
                                dir_files_moved += 1
                            except Exception as e:
                                print(f"   [ERROR] FAILED to move '{file}': {e}")
                                logger.error(f"Failed to move favorite file '{file}': {e}")
                            break
                        elif debug_mode and dir_files_checked <= 3:
                            # In debug mode, show what we're testing against
                            aliases = artist_info['aliases']
                            print(f"         No match for '{canonical}' (aliases: {aliases[:2]})")

            total_files_checked += dir_files_checked
            print(f"   [CHART] Directory summary: {dir_files_checked} files checked, {dir_matches_found} matches, {dir_files_moved} moved")
            self._log_artist_scan_progress(
                key,
                dir_files_checked,
                dir_matches_found,
                dir_files_moved,
                progress_log_time,
                root_dir=abs_dest_dir,
                force=True,
            )
            logger.info(
                "Artist scan directory complete [%d/%d]: %s - checked: %d, matches: %d, moved: %d, elapsed: %.2fs",
                index,
                len(directories_to_scan),
                key,
                dir_files_checked,
                dir_matches_found,
                dir_files_moved,
                time.time() - dir_start_time,
            )

        # Check artist directory based on scan mode
        if scan_all_fav_subdirs:
            print("\n[FOLDER] Checking ALL subdirectories of artist directory recursively...")
            logger.info("Artist processing recursive scan enabled for artist directory: %s", self.artist_directory)
            # Recursive scan of ALL artist subdirectories
            if os.path.exists(self.artist_directory):
                artist_abs_path = os.path.abspath(self.artist_directory)
                recursive_start_time = time.time()
                recursive_log_time = recursive_start_time

                for root_dir, dirs, files in os.walk(self.artist_directory):
                    # Get relative path for cleaner output
                    rel_path = os.path.relpath(root_dir, self.artist_directory)

                    # Skip files in the artist root directory itself - only process subdirectories
                    if rel_path == ".":
                        logger.debug("Skipping artist root directory files (only processing subdirectories)")
                        continue

                    display_path = f"artist/{rel_path}"

                    files_to_check = [f for f in files if is_configured_file(f)]
                    if not files_to_check:
                        continue

                    print(f"   [FOLDER] Checking {len(files_to_check)} files in '{display_path}'")
                    total_files_checked += len(files_to_check)
                    recursive_log_time = self._log_artist_scan_progress(
                        "artist_recursive",
                        total_files_checked,
                        matches_found,
                        files_moved,
                        recursive_log_time,
                        root_dir=root_dir,
                    )

                    for file in files_to_check:
                        file_path = os.path.join(root_dir, file)
                        file_lower = file.lower()

                        if debug_mode and total_files_checked <= 3:
                            print(f"      [SEARCH] Checking file: '{file}'")

                        for canonical, artist_info in self.favorite_artists.items():
                            aliases = artist_info['aliases']
                            target_folder = artist_info['target_directory']
                            if any(self.check_artist_match(file_lower, alias) for alias in aliases):
                                # Check if already in the correct artist folder
                                if os.path.normcase(os.path.abspath(root_dir)).startswith(os.path.normcase(os.path.abspath(target_folder))):
                                    if debug_mode:
                                        print(f"   [NEXT]  SKIP: '{file}' already in correct artist folder '{canonical}'")
                                    break

                                print(f"   [TARGET] MATCH FOUND: '{file}' matches artist '{canonical}'")
                                matches_found += 1

                                ensure_directory_exists(target_folder)
                                destination_path = os.path.join(target_folder, file)

                                try:
                                    move_file_with_logging(
                                        file_path, destination_path,
                                        db_manager=self.db_manager, logger_obj=logger,
                                        destination_directory='artist_directory',
                                        reason=f'artist_recursive_{canonical}'
                                    )
                                    print(f"   [OK] MOVED: '{file}' -> '{os.path.relpath(destination_path, self.artist_directory)}'")
                                    logger.info("Favorite file '%s' moved from '%s' to '%s' (match: %s).",
                                                file, root_dir, destination_path, canonical)
                                    files_moved += 1
                                except Exception as e:
                                    print(f"   [ERROR] FAILED to move '{file}': {e}")
                                    logger.error(f"Failed to move favorite file '{file}': {e}")
                                break
                            elif debug_mode and total_files_checked <= 3:
                                aliases = artist_info['aliases']
                                print(f"         No match for '{canonical}' (aliases: {aliases[:2]})")
                self._log_artist_scan_progress(
                    "artist_recursive",
                    total_files_checked,
                    matches_found,
                    files_moved,
                    recursive_log_time,
                    root_dir=artist_abs_path,
                    force=True,
                )
                logger.info(
                    "Artist processing recursive artist-directory scan complete - checked: %d, matches: %d, moved: %d, elapsed: %.2fs",
                    total_files_checked,
                    matches_found,
                    files_moved,
                    time.time() - recursive_start_time,
                )
            else:
                print(f"   [WARNING]  Artist directory does not exist: {self.artist_directory}")
                logger.info("Artist directory does not exist for recursive scan: %s", self.artist_directory)
        else:
            print("\n[FOLDER] Checking files directly in artist directory root (NOT child directories)...")
            logger.info("Artist processing will scan artist directory root only: %s", self.artist_directory)
            # Check files directly in the artist_directory root (NOT subdirectories - those are organized by artist)
            if os.path.exists(self.artist_directory):
                files_to_process = [entry for entry in os.scandir(self.artist_directory)
                                   if entry.is_file() and is_configured_file(entry.name)]

                if files_to_process:
                    print(f"   [FOLDER] Processing {len(files_to_process)} files in artist root for artist matching")
                    total_files_checked += len(files_to_process)
                    logger.info("Artist directory root scan - files to check: %d", len(files_to_process))

                    for entry in files_to_process:
                        file = entry.name
                        file_lower = file.lower()
                        file_path = entry.path

                        for canonical, artist_info in self.favorite_artists.items():
                            aliases = artist_info['aliases']
                            target_folder = artist_info['target_directory']
                            if any(self.check_artist_match(file_lower, alias) for alias in aliases):
                                print(f"   [TARGET] MATCH FOUND: '{file}' matches artist '{canonical}'")
                                matches_found += 1

                                ensure_directory_exists(target_folder)
                                destination_path = os.path.join(target_folder, file)

                                try:
                                    move_file_with_logging(
                                        file_path, destination_path,
                                        db_manager=self.db_manager, logger_obj=logger,
                                        destination_directory='artist_directory',
                                        reason=f'artist_root_{canonical}'
                                    )
                                    print(f"   [OK] MOVED: '{file}' -> '{destination_path}'")
                                    logger.info("Favorite file '%s' moved from artist_directory root to '%s' (match: %s).",
                                                file, destination_path, canonical)
                                    files_moved += 1
                                except Exception as e:
                                    print(f"   [ERROR] FAILED to move '{file}': {e}")
                                    logger.error(f"Failed to move favorite file '{file}': {e}")
                                break
                else:
                    print("   [INFO] No files found in artist directory root")
                    logger.info("Artist directory root scan found no files to process")

            # Check additional favorite directories for artist files
            # Note: These directories are at fav_directory (e.g., P:/IPL/- Fav/)
            print("\n[FOLDER] Checking additional favorites subdirectories...")
            logger.info("Artist processing will scan additional favorites subdirectories")
            fav_base_dir = self.fav_directory
            additional_fav_dirs = [
                os.path.join(fav_base_dir, "- Fav Videos"),
                os.path.join(fav_base_dir, "- Fav Gifs")
            ]

            for additional_dir in additional_fav_dirs:
                if not os.path.exists(additional_dir):
                    print(f"   [WARNING]  Additional directory does not exist: {additional_dir}")
                    logger.info("Additional favorites directory does not exist: %s", additional_dir)
                    continue

                files_to_process = [entry for entry in os.scandir(additional_dir)
                                   if entry.is_file() and is_configured_file(entry.name)]

                if files_to_process:
                    print(f"   [FOLDER] Processing {len(files_to_process)} files in '{os.path.basename(additional_dir)}'")
                    total_files_checked += len(files_to_process)
                    logger.info(
                        "Additional favorites directory scan start: %s - files to check: %d",
                        additional_dir,
                        len(files_to_process),
                    )

                    for entry in files_to_process:
                        file = entry.name
                        file_lower = file.lower()
                        file_path = entry.path

                        for canonical, artist_info in self.favorite_artists.items():
                            aliases = artist_info['aliases']
                            target_folder = artist_info['target_directory']
                            if any(self.check_artist_match(file_lower, alias) for alias in aliases):
                                print(f"   [TARGET] MATCH FOUND: '{file}' matches artist '{canonical}'")
                                matches_found += 1

                                ensure_directory_exists(target_folder)
                                destination_path = os.path.join(target_folder, file)

                                try:
                                    move_file_with_logging(
                                        file_path, destination_path,
                                        db_manager=self.db_manager, logger_obj=logger,
                                        destination_directory='artist_directory',
                                        reason=f'artist_additional_favorites_{canonical}'
                                    )
                                    print(f"   [OK] MOVED: '{file}' -> '{destination_path}'")
                                    logger.info("Favorite file '%s' moved from '%s' to '%s' (match: %s).",
                                                file, additional_dir, destination_path, canonical)
                                    files_moved += 1
                                except Exception as e:
                                    print(f"   [ERROR] FAILED to move '{file}': {e}")
                                    logger.error(f"Failed to move favorite file '{file}': {e}")
                                break
                else:
                    print(f"   [INFO] No files found in '{os.path.basename(additional_dir)}'")
                    logger.info("Additional favorites directory scan found no files: %s", additional_dir)

        # Final summary
        print(f"\n{'='*80}")
        print("ADDITIONAL FAVORITES CHECK COMPLETE")
        print(f"{'='*80}")
        print(f"[CHART] Total files checked: {total_files_checked}")
        print(f"[TARGET] Total matches found: {matches_found}")
        print(f"[PACKAGE] Total files moved: {files_moved}")
        print(f"[FOLDER] Processed directories: {len(processed_dirs)}")
        print(f"{'='*80}\n")
        logger.info(
            "Artist processing scan summary - checked: %d, matches: %d, moved: %d, processed_directories: %d",
            total_files_checked,
            matches_found,
            files_moved,
            len(processed_dirs),
        )

        return files_moved

    def sort_artists_yaml(self):
        """Sort the artists.yaml file alphabetically by artist names and save it back."""
        try:
            artists_file = str(sorter_paths.SORT_ARTISTS)  # fff: raw file (it is written back)
            print("Sorting configs/artists.yaml file alphabetically...")

            # Read the current artists data
            with open(artists_file, "r", encoding="utf-8") as f:
                artists_data = yaml.safe_load(f)

            if not artists_data:
                logger.warning("artists.yaml is empty or could not be loaded")
                return

            # Sort by artist names (keys) alphabetically
            sorted_artists = dict(sorted(artists_data.items(), key=lambda item: item[0].lower()))

            # Write back the sorted data
            with open(artists_file, "w", encoding="utf-8") as f:
                yaml.dump(sorted_artists, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

            print(f"Successfully sorted {len(sorted_artists)} artists in configs/artists.yaml")
            logger.info(f"Sorted configs/artists.yaml file with {len(sorted_artists)} artists")

        except Exception as e:
            print(f"Error sorting configs/artists.yaml: {e}")
            logger.error(f"Error sorting configs/artists.yaml: {e}")
