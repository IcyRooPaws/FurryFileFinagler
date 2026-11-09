"""
Hash management for destination directories using multi-threaded operations.

IMPORTANT FIX (September 2025):
Fixed critical data corruption issue where multiple threads processing the same directory
but different file types would each call cleanup_missing_files() with only their file type,
causing deletion of database entries for other file types.

Solution: Process each directory once with all file types together instead of separately.
"""
import os
import time
import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from .utils import hash_file, compute_md5_hash, is_configured_file, is_video, is_gif, is_image

logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle Ctrl+C and other termination signals."""
    global shutdown_requested
    print(f"\n[STOP] Received signal {signum}. Requesting graceful shutdown...")
    print("[TIME] Finishing current files and stopping...")
    shutdown_requested = True

# Set up signal handlers
signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal

class HashManager:
    """Manages hashing operations for destination directories."""

    def __init__(self, config, db_manager, force_rehash=False, verbose=False):
        self.config = config
        self.db_manager = db_manager
        self.fav_directory = config.fav_directory
        self.artist_directory = config.artist_directory
        self.destination_directories = config.destination_directories
        self.force_rehash = force_rehash  # Force rehashing even if files appear unchanged
        self.verbose = verbose  # Show detailed output for each file being hashed

    def file_needs_rehashing(self, file_path, existing_metadata):
        """Check if a file needs rehashing based on metadata changes."""
        # Force rehash mode: always return True
        if self.force_rehash:
            return True

        if not existing_metadata:
            return True  # File not in database

        try:
            # First check if we have a valid hash - if not, we need to hash
            stored_hash = existing_metadata.get('hash') if isinstance(existing_metadata, dict) else None
            if not stored_hash:
                return True  # No hash stored, need to hash

            stat = os.stat(file_path)
            current_size = stat.st_size
            current_mtime = int(stat.st_mtime)

            # Extract metadata values safely
            stored_size = existing_metadata.get('size') if isinstance(existing_metadata, dict) else None
            # Accept multiple key names and normalize to integer seconds
            stored_mtime_val = None
            if isinstance(existing_metadata, dict):
                stored_mtime_val = (
                    existing_metadata.get('mtime')
                    or existing_metadata.get('file_modified_at')
                    or existing_metadata.get('modified_at')
                )

            # Convert stored_mtime to int seconds if it's a datetime
            stored_mtime = None
            if stored_mtime_val is not None:
                try:
                    # If already numeric
                    if isinstance(stored_mtime_val, (int, float)):
                        stored_mtime = int(stored_mtime_val)
                    else:
                        # Assume datetime-like with timestamp()
                        stored_mtime = int(stored_mtime_val.timestamp())
                except Exception:
                    stored_mtime = None

            # If we have both stored size and mtime, check if file has changed
            if stored_size is not None and stored_mtime is not None:
                # Quick size check first (most likely to change)
                if stored_size != current_size:
                    logger.debug(f"File {os.path.basename(file_path)} size changed: {stored_size} -> {current_size}")
                    return True

                # Then check modification time (allow 1 second tolerance for filesystem precision)
                if abs(stored_mtime - current_mtime) > 1:
                    logger.debug(f"File {os.path.basename(file_path)} mtime changed: {stored_mtime} -> {current_mtime}")
                    return True

                # File appears unchanged and we have a hash - skip it
                logger.debug(f"Skipping unchanged file: {os.path.basename(file_path)}")
                return False

            # If metadata is incomplete, we need to rehash
            logger.debug(f"Incomplete metadata for {os.path.basename(file_path)}, rehashing")
            return True

        except OSError as e:
            logger.debug(f"Error accessing {file_path}: {e}")
            return True  # If we can't stat the file, rehash it

    def hash_dest_file_worker(self, args):
        """Worker function for hashing destination files."""
        global shutdown_requested

        if shutdown_requested:
            return None, None

        dest_dir, entry, table_name, file_id = args
        file_path = os.path.join(dest_dir, entry.name) if dest_dir else entry.path

        try:
            # Verbose output: show what's being hashed
            if self.verbose:
                print(f"[HASH] {file_id}")
                print(f"       Path: {file_path}")
                print(f"       Table: {table_name}")
            
            # Compute both MediaHasher content hash and MD5 hash
            file_hash = hash_file(file_path, logger=logger)
            md5_hash = compute_md5_hash(file_path, logger=logger)

            if file_hash:
                self.db_manager.save_file_hash_to_dest_with_table(table_name, file_id, file_hash, file_path, md5_hash)
                if self.verbose:
                    print(f"       Hash: {file_hash[:16]}... MD5: {md5_hash[:16] if md5_hash else 'N/A'}...")
                logger.debug(f"[OK] Committed to DB: {table_name} - {file_id} - hash:{file_hash[:16]}... md5:{md5_hash[:16] if md5_hash else 'N/A'}...")
            else:
                if self.verbose:
                    print("       [FAILED] Could not compute hash")
                logger.warning(f"Failed to hash {entry.name}")
            return file_id, file_hash
        except Exception as e:
            logger.error(f"Error hashing {entry.name}: {e}")
            return file_id, None

    def hash_favorites_dir(self):
        """Hash all files in the favorites directory including subdirectories."""
        print(f"Hashing favorites directory (including subdirectories): {self.fav_directory}")
        print(f"  This includes artist directory: {self.artist_directory}")
        table = "favorites"
        self.db_manager.ensure_dest_table(table)

        # Import normalize_path from utils
        from .utils import normalize_path

        # Get existing metadata from database, keyed by file_path (not file_name!)
        db_metadata = {}
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT file_path,
                       file_name,
                       COALESCE(perceptual_hash_phash, perceptual_hash_dhash, perceptual_hash_average, perceptual_hash_whash, md5_hash) as hash_value,
                       file_size,
                       file_modified_at
                FROM `{table}`
            """)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            # Key by normalized file_path for accurate matching
            for row in rows:
                normalized_path = normalize_path(row[0]) if row[0] else None
                if normalized_path:
                    db_metadata[normalized_path] = {
                        'file_name': row[1],
                        'hash': row[2],
                        'size': row[3],
                        'file_modified_at': row[4]
                    }
        except Exception as e:
            logger.warning(f"Could not retrieve existing metadata: {e}")
            db_metadata = {}

        # Collect files that need hashing
        file_paths_by_name = {}  # Track duplicate filenames
        all_file_paths = []
        args = []
        skipped_count = 0

        for root, dirs, files in os.walk(self.fav_directory):
            for file in files:
                if is_configured_file(file):
                    file_path = os.path.join(root, file)
                    normalized_path = normalize_path(file_path)
                    all_file_paths.append((file, file_path, normalized_path))

                    # Track for duplicate detection
                    if file not in file_paths_by_name:
                        file_paths_by_name[file] = []
                    file_paths_by_name[file].append(file_path)

        # Report duplicate filenames if found
        duplicates_found = {name: paths for name, paths in file_paths_by_name.items() if len(paths) > 1}
        if duplicates_found:
            print(f"[WARNING]  WARNING: Found {len(duplicates_found)} duplicate filename(s) in different subdirectories")

        # Process each file by its full path
        existing_files = set()  # Track existing filenames for cleanup
        for file_name, file_path, normalized_path in all_file_paths:
            existing_files.add(file_name)

            # Check if THIS specific path needs rehashing (not just the filename)
            if self.file_needs_rehashing(file_path, db_metadata.get(normalized_path)):
                entry = type("Entry", (), {"name": file_name, "path": file_path})()
                args.append((None, entry, table, file_name))
            else:
                skipped_count += 1

        removed_count = self.db_manager.cleanup_missing_files(table, existing_files)
        if removed_count > 0:
            logger.info(f"Removed {removed_count} missing file entries from favorites table.")

        # Calculate totals correctly
        total_files = len(all_file_paths)
        files_to_hash = len(args)
        print(f"\nFavorites: {total_files} files found, {files_to_hash} to hash, {skipped_count} up-to-date")

        if args:
            self._process_files_in_batches(args, table, "FAVORITES")
        else:
            print("[OK] All favorites are already up to date!")

        logger.info("Favorites directory hashing completed.")

    def hash_archive_directory(self, archive_dir, table_name):
        """Hash all files in an archive directory including subdirectories (like favorites)."""
        print("   Hashing archive directory (including subdirectories)...")
        self.db_manager.ensure_dest_table(table_name)

        # Import normalize_path from utils
        from .utils import normalize_path

        # Get existing metadata from database, keyed by file_path (not file_name!)
        db_metadata = {}
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT file_path,
                       file_name,
                       COALESCE(perceptual_hash_phash, perceptual_hash_dhash, perceptual_hash_average, perceptual_hash_whash, md5_hash) as hash_value,
                       file_size,
                       file_modified_at
                FROM `{table_name}`
            """)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            # Key by normalized file_path for accurate matching
            for row in rows:
                normalized_path = normalize_path(row[0]) if row[0] else None
                if normalized_path:
                    db_metadata[normalized_path] = {
                        'file_name': row[1],
                        'hash': row[2],
                        'size': row[3],
                        'file_modified_at': row[4]
                    }
        except Exception as e:
            logger.warning(f"Could not retrieve existing metadata for {table_name}: {e}")
            db_metadata = {}

        # Collect files that need hashing
        file_paths_by_name = {}  # Track duplicate filenames
        all_file_paths = []
        args = []
        skipped_count = 0

        for root, dirs, files in os.walk(archive_dir):
            for file in files:
                if is_configured_file(file):
                    file_path = os.path.join(root, file)
                    normalized_path = normalize_path(file_path)
                    all_file_paths.append((file, file_path, normalized_path))

                    # Track for duplicate detection
                    if file not in file_paths_by_name:
                        file_paths_by_name[file] = []
                    file_paths_by_name[file].append(file_path)

        # Report duplicate filenames if found
        duplicates_found = {name: paths for name, paths in file_paths_by_name.items() if len(paths) > 1}
        if duplicates_found:
            print(f"   [WARNING] Found {len(duplicates_found)} duplicate filename(s) in different subdirectories")

        # Process each file by its full path
        existing_files = set()  # Track existing filenames for cleanup
        for file_name, file_path, normalized_path in all_file_paths:
            existing_files.add(file_name)

            # Check if THIS specific path needs rehashing (not just the filename)
            existing_metadata = db_metadata.get(normalized_path)

            if self.file_needs_rehashing(file_path, existing_metadata):
                args.append((os.path.dirname(file_path), type('Entry', (), {'name': file_name, 'path': file_path})(), table_name, file_name))
            else:
                skipped_count += 1

        # Cleanup missing files
        removed_count = self.db_manager.cleanup_missing_files(table_name, existing_files)
        if removed_count > 0:
            logger.info(f"Removed {removed_count} missing file entries from {table_name} table.")

        total_files = len(all_file_paths)
        files_to_hash = len(args)
        print(f"   {table_name}: {total_files} files found, {files_to_hash} to hash, {skipped_count} up-to-date")

        if args:
            self._process_files_in_batches(args, table_name, table_name.upper())
        else:
            print(f"   [OK] All {table_name} files are already up to date!")

        logger.info(f"{table_name} directory hashing completed.")

    def hash_all_files_in_directory(self, dest_dir, table_name, exclude_paths=None, skip_dot_dirs=False, skip_cleanup=False):
        """Hash all configured files in a directory recursively including subdirectories.

        Args:
            dest_dir: base directory to scan
            table_name: target table name
            exclude_paths: iterable of absolute paths to skip (any subdir that startswith an exclude path is skipped)
            skip_dot_dirs: when True, skip directories whose basename starts with '.'
            skip_cleanup: when True, skip cleanup_missing_files (useful for multi-directory tables like other_directories)
        """
        # Map questionable_* to nsfw_* tables
        if table_name.startswith("questionable_"):
            table_name = table_name.replace("questionable_", "nsfw_")

        self.db_manager.ensure_dest_table(table_name)

        # Import normalize_path from utils for accurate path tracking
        from .utils import normalize_path

        # Get existing metadata from database, keyed by file_path
        db_metadata = {}
        try:
            conn = self.db_manager.get_db_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT file_path,
                       file_name,
                       COALESCE(perceptual_hash_phash, perceptual_hash_dhash, perceptual_hash_average, perceptual_hash_whash, md5_hash) as hash_value,
                       file_size,
                       file_modified_at
                FROM `{table_name}`
            """)
            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            # Key by normalized file_path for accurate matching
            for row in rows:
                normalized_path = normalize_path(row[0]) if row[0] else None
                if normalized_path:
                    db_metadata[normalized_path] = {
                        'file_name': row[1],
                        'hash': row[2],
                        'size': row[3],
                        'file_modified_at': row[4]
                    }
        except Exception as e:
            logger.warning(f"Could not retrieve existing metadata for {table_name}: {e}")
            db_metadata = {}

        # Normalize exclude paths once
        exclude_set = set()
        if exclude_paths:
            exclude_set = {os.path.normpath(os.path.abspath(p)) for p in exclude_paths if p}

        # Collect ALL files recursively including subdirectories
        file_paths_by_name = {}  # Track duplicate filenames
        all_file_paths = []
        args = []
        skipped_count = 0

        # Recursively walk the directory to find all files
        for root, dirs, files in os.walk(dest_dir):
            # Prune directories we do not want to scan
            pruned = []
            for d in list(dirs):
                full = os.path.normpath(os.path.abspath(os.path.join(root, d)))
                if (skip_dot_dirs and d.startswith('.')) or any(full.startswith(excl) for excl in exclude_set):
                    pruned.append(d)
            for d in pruned:
                dirs.remove(d)

            for file in files:
                if is_configured_file(file):
                    file_path = os.path.join(root, file)
                    normalized_path = normalize_path(file_path)
                    all_file_paths.append((file, file_path, normalized_path))

                    # Track for duplicate detection
                    if file not in file_paths_by_name:
                        file_paths_by_name[file] = []
                    file_paths_by_name[file].append(file_path)

        # Report duplicate filenames if found
        duplicates_found = {name: paths for name, paths in file_paths_by_name.items() if len(paths) > 1}
        if duplicates_found:
            print(f"   [WARNING] Found {len(duplicates_found)} duplicate filename(s) in different subdirectories of {table_name}")

        # Process each file by its full path
        existing_files = set()  # Track existing filenames for cleanup
        for file_name, file_path, normalized_path in all_file_paths:
            existing_files.add(file_name)

            # Check if THIS specific path needs rehashing
            if self.file_needs_rehashing(file_path, db_metadata.get(normalized_path)):
                entry = type("Entry", (), {"name": file_name, "path": file_path})()
                args.append((None, entry, table_name, file_name))
            else:
                skipped_count += 1

        # Cleanup missing files from database (unless skipped for multi-directory tables)
        if not skip_cleanup:
            removed_count = self.db_manager.cleanup_missing_files(table_name, existing_files)
            if removed_count > 0:
                logger.info(f"Removed {removed_count} missing entries from {table_name}")

        total_files = len(all_file_paths)
        files_to_hash = len(args)

        if total_files > 0:
            # Count files by type for informational purposes
            image_count = sum(1 for file_name, _, _ in all_file_paths if is_image(file_name))
            gif_count = sum(1 for file_name, _, _ in all_file_paths if is_gif(file_name))
            video_count = sum(1 for file_name, _, _ in all_file_paths if is_video(file_name))

            print(f"   {table_name}: {total_files} files ({image_count} img, {gif_count} gif, {video_count} vid) - {files_to_hash} to hash, {skipped_count} skipped")

        if args:
            self._process_files_in_batches(args, table_name, table_name.upper())
        elif total_files > 0:
            print(f"   [OK] All {total_files} files up to date")
        else:
            logger.debug(f"No configured files found in {table_name}")

    def _process_files_in_batches(self, args, table_name, display_name):
        """Streamlined batch processing for file hashing."""
        total = len(args)
        batch_size = 100  # Increased batch size for efficiency
        max_workers = 8   # Optimized for new connection pool

        for i in range(0, total, batch_size):
            global shutdown_requested
            if shutdown_requested:
                print("[STOP] Shutdown requested")
                break

            batch = args[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total + batch_size - 1) // batch_size

            start_time = time.time()
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = [executor.submit(self.hash_dest_file_worker, arg) for arg in batch]

                    # Process results as they complete
                    successful = 0
                    committed = 0
                    for future in futures:
                        try:
                            result = future.result(timeout=300)
                            if result and result[1]:  # file_id, file_hash
                                successful += 1
                                committed += 1
                        except Exception as e:
                            logger.error(f"Task failed: {e}")

                elapsed_time = time.time() - start_time
                if batch_num == total_batches or batch_num % 5 == 0:  # Show progress every 5 batches or at end
                    print(f"      Batch {batch_num}/{total_batches}: {successful}/{len(batch)} files hashed ({elapsed_time:.1f}s)")

            except Exception as e:
                logger.error(f"Error processing batch {batch_num}: {e}")

            # Brief pause between batches
            if i + batch_size < total and not shutdown_requested:
                time.sleep(0.5)

        logger.info(f"{display_name} hashing completed")

    def hash_files_in_dir_by_type_with_table(self, dest_dir, file_type_func, table_name):
        """
        DEPRECATED: This method is no longer used due to data corruption issues.

        The problem was that when processing file types separately for the same directory,
        multiple calls to cleanup_missing_files() would each consider only their file type,
        causing deletion of entries for other file types.

        Use hash_all_files_in_directory() instead.
        """
        logger.warning("hash_files_in_dir_by_type_with_table called - this method is deprecated")

    def hash_all_destination_dirs(self):
        """Hash all files in destination directories using optimized threading."""
        print(f"\n{'='*60}")
        print("[LAUNCH] HASHING DESTINATION DIRECTORIES")
        print(f"{'='*60}")

        # Phase 1: Favorites
        print("\n Phase 1: Favorites Directory")
        self.hash_favorites_dir()

        # Phase 2: Destination directories
        print("\n Phase 2: Destination Directories")

        # Streamlined processing - avoid duplicate directory processing
        # Since questionable_* directories map to the same physical paths as nsfw_*,
        # we only process the nsfw_* variants to avoid duplicate work
        rating_groups = [
            ("NSFW", ["nsfw_image", "nsfw_gif", "nsfw_video"]),
            ("SFW", ["sfw_image", "sfw_gif", "sfw_video"])
        ]

        file_types = [("video", is_video), ("gif", is_gif), ("image", is_image)]
        total_processed = 0

        for rating_name, dest_keys in rating_groups:
            global shutdown_requested
            if shutdown_requested:
                break

            # Process each directory type sequentially to avoid table conflicts
            for dest_key in dest_keys:
                if shutdown_requested:
                    break

                dest_dir = self.destination_directories.get(dest_key)
                if not dest_dir or not os.path.exists(dest_dir):
                    logger.debug(f"Skipping {dest_key}: Directory not found")
                    continue

                # Process ALL file types for this directory at once to avoid multiple cleanups
                self.hash_all_files_in_directory(dest_dir, dest_key)
                print(f"   [OK] Completed all files for {dest_key}")

                total_processed += 1

            print(f"[OK] Completed {rating_name} rating group")

        # Phase 3: Manual sort directories
        print("\n[HOT] PHASE 3: Manual sort directories")

        manual_sort_dirs = [
            ("manual_sort_images", self.config.manual_sort_directory),
            ("manual_sort_videos", self.config.manual_sort_video_directory)
        ]

        # Add gif manual sort directory if configured
        if hasattr(self.config, 'manual_sort_gif_directory') and self.config.manual_sort_gif_directory:
            manual_sort_dirs.append(("manual_sort_gifs", self.config.manual_sort_gif_directory))

        # Add possible_missing_files directory if configured
        if hasattr(self.config, 'possible_missing_files_directory') and self.config.possible_missing_files_directory:
            manual_sort_dirs.append(("possible_missing_files", self.config.possible_missing_files_directory))

        for sort_key, sort_dir in manual_sort_dirs:
            if shutdown_requested:
                break

            if not sort_dir or not os.path.exists(sort_dir):
                print(f"[WARNING]  Skipping {sort_key}: Directory not found or not configured")
                continue

            print(f"[FOLDER] {sort_key} -> {sort_dir}")

            # Process ALL file types for this directory at once
            print(f"   [Loop] Processing all files for {sort_key}")
            self.hash_all_files_in_directory(sort_dir, sort_key)
            print(f"   [OK] Completed all files for {sort_key}")

            total_processed += 1

        # Phase 4: Archive directories (if configured)
        print("\n[HOT] PHASE 4: Archive directories")

        archive_dirs = getattr(self.config, 'archive_directories', None) or self.config.config.get('archive_directories', {})
        if archive_dirs:
            for archive_key, archive_config in archive_dirs.items():
                if shutdown_requested:
                    break

                # Handle both simple format (string) and expanded format (dict)
                if isinstance(archive_config, str):
                    # Simple format: single path for all media types
                    archive_paths = [(archive_key, archive_config)]
                elif isinstance(archive_config, dict):
                    # Expanded format: separate paths for images/videos/gifs
                    archive_paths = [
                        (f"{archive_key}_{media_type}", path)
                        for media_type, path in archive_config.items()
                        if path
                    ]
                else:
                    # Config value is None or invalid
                    continue

                for archive_name, archive_path in archive_paths:
                    if shutdown_requested:
                        break

                    if not archive_path or not os.path.exists(archive_path):
                        print(f"[WARNING]  Skipping archive_{archive_name}: Directory not found")
                        continue

                    print(f"[PACKAGE] archive_{archive_name} -> {archive_path}")

                    # Process ALL file types for this archive directory at once
                    # Archive directories scan recursively like favorites
                    print(f"   [Loop] Processing all files for archive_{archive_name} (including subdirectories)")
                    table_name = f"archive_{archive_name}"
                    self.hash_archive_directory(archive_path, table_name)
                    print(f"   [OK] Completed all files for archive_{archive_name}")

                    total_processed += 1
        else:
            print("[WARNING]  No archive directories configured")

        # Phase 4.5: Archived favorites directory (if configured)
        archived_fav_dir = getattr(self.config, 'archived_favorites_directory', None)
        if archived_fav_dir:
            if os.path.exists(archived_fav_dir):
                print("\n[HOT] PHASE 4.5: Archived favorites directory")
                print(f"[PACKAGE] archived_favorites -> {archived_fav_dir}")
                self.hash_archive_directory(archived_fav_dir, 'archived_favorites')
                print("   [OK] Completed all files for archived_favorites")
                total_processed += 1
            else:
                print(f"\n[WARNING]  Skipping archived_favorites: Directory not found ({archived_fav_dir})")

        # Phase 5: Overall directories to hash (user-specified roots)
        overall_dirs = getattr(self.config, 'other_directories_to_hash', []) or []
        if overall_dirs:
            print("\n[GRID] PHASE 5: Overall directories to hash")

            # Build exclusion set from all known configured directories to avoid double hashing
            exclude_paths = set()

            def _add(path):
                if path and os.path.exists(path):
                    exclude_paths.add(os.path.normpath(os.path.abspath(path)))

            # Destination directories
            for d in self.destination_directories.values():
                _add(d)

            # Favorites and artist
            _add(getattr(self.config, 'fav_directory', None))
            _add(getattr(self.config, 'artist_directory', None))
            _add(getattr(self.config, 'duplicate_directory', None))

            # Manual / possible missing / deleted
            _add(getattr(self.config, 'manual_sort_directory', None))
            _add(getattr(self.config, 'manual_sort_video_directory', None))
            _add(getattr(self.config, 'manual_sort_gif_directory', None))
            _add(getattr(self.config, 'possible_missing_files_directory', None))
            _add(getattr(self.config, 'deleted_from_e621_directory', None))
            _add(getattr(self.config, 'archived_favorites_directory', None))

            # Archive directories (handle both simple and expanded formats)
            archive_config = getattr(self.config, 'archive_directories', {})
            for archive_value in archive_config.values():
                if isinstance(archive_value, str):
                    # Simple format: single path
                    _add(archive_value)
                elif isinstance(archive_value, dict):
                    # Expanded format: multiple paths
                    for path in archive_value.values():
                        _add(path)

            # Tag-sort base directories (if present)
            base_dirs = getattr(self.config, 'get_tag_sorting_base_dirs', lambda: {})() or {}
            for d in base_dirs.values():
                _add(d)

            overall_table = "other_directories"
            # Collect ALL files from ALL overall_dirs first, then cleanup once
            all_overall_files = set()
            
            for idx, overall_dir in enumerate(overall_dirs, 1):
                if shutdown_requested:
                    break

                if not overall_dir or not os.path.exists(overall_dir):
                    print(f"[WARNING]  Skipping overall_dir {idx}: not found or empty")
                    continue

                # Build per-run exclude list that omits the current root itself
                root_abs = os.path.abspath(overall_dir)
                per_exclude = {p for p in exclude_paths if not os.path.samefile(p, root_abs)}

                print(f"[FOLDER] overall_dir_{idx} -> {overall_dir}")
                self.hash_all_files_in_directory(overall_dir, overall_table, exclude_paths=per_exclude, skip_dot_dirs=True, skip_cleanup=True)
                # Collect filenames for later unified cleanup
                for root, dirs, files in os.walk(overall_dir):
                    pruned = []
                    for d in list(dirs):
                        full = os.path.normpath(os.path.abspath(os.path.join(root, d)))
                        if (True and d.startswith('.')) or any(full.startswith(excl) for excl in per_exclude):
                            pruned.append(d)
                    for d in pruned:
                        dirs.remove(d)
                    for file in files:
                        if is_configured_file(file):
                            all_overall_files.add(file)
                
                print(f"   [OK] Completed overall_dir_{idx} -> table {overall_table}")
                total_processed += 1
            
            # Now do unified cleanup for all overall_directories
            if all_overall_files and not shutdown_requested:
                removed_count = self.db_manager.cleanup_missing_files(overall_table, all_overall_files)
                if removed_count > 0:
                    logger.info(f"Removed {removed_count} missing entries from {overall_table}")

        print(f"\n{'='*80}")
        if shutdown_requested:
            print(f"[WARNING]  HASHING STOPPED BY USER - Processed {total_processed} directory types")
        else:
            print(f"[DONE] HASHING COMPLETE! - Processed {total_processed} directory types successfully")
        print(f"Finished: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}")
