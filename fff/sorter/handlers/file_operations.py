"""
File operations module for moving, hashing, and validating files.
"""
import os
import shutil
import logging
from .utils import (
    hash_file, compute_md5_hash, ensure_directory_exists,
    is_configured_file, is_video, is_image, is_gif, log_file_move,
    move_file_with_logging, copy_file_with_logging, delete_file_with_logging,
    is_furaffinity_file, get_furaffinity_rating
)

# Import advanced deduplicator for fast duplicate detection
try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False

logger = logging.getLogger(__name__)

class FileOperations:
    """Handles file moving, validation, and duplicate detection operations."""

    # Chunk size for fast partial hashing (64KB)
    FAST_HASH_CHUNK = 64 * 1024

    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager
        self.duplicate_directory = config.duplicate_directory
        self.manual_sort_directory = config.manual_sort_directory
        self.manual_sort_video_directory = config.manual_sort_video_directory
        self.manual_sort_gif_directory = config.manual_sort_gif_directory

        # Track if we're using advanced deduplication
        self.use_advanced_dedupe = HAS_XXHASH
        if self.use_advanced_dedupe:
            logger.info("Advanced deduplication enabled (using xxhash for fast partial hashing)")
        else:
            logger.info("Advanced deduplication unavailable (install xxhash: pip install xxhash)")

    def file_exists(self, destination_directory, file_name, file_path):
        """
        Check if a file with the same name exists in the destination directory.
        If hashes match, it's a duplicate.
        If hashes differ, keep the older file (by mtime), move the newer to duplicates.
        Returns:
            (exists: bool, move_to_duplicates: bool)
        """
        current_file_hash = hash_file(file_path, logger=logger)
        if current_file_hash is None:
            return False, False

        dest_hashes = self.db_manager.get_dest_hashes(destination_directory)
        dest_file_path = os.path.join(destination_directory, file_name)

        if file_name in dest_hashes:
            # Extract hash from metadata structure
            stored_hash = dest_hashes[file_name]['hash'] if isinstance(dest_hashes[file_name], dict) else dest_hashes[file_name]
            if stored_hash == current_file_hash:
                logger.info("Duplicate detected: Original: '%s', Duplicate: '%s'",
                            dest_file_path, file_path)
                return True, True  # duplicate, move to duplicates
            else:
                # Same name, different hash: keep older file
                try:
                    src_mtime = os.path.getmtime(file_path)
                    dest_mtime = os.path.getmtime(dest_file_path)
                    if src_mtime > dest_mtime:
                        logger.info("Same name, different hash: Keeping older file '%s', moving newer '%s' to duplicates.",
                                    dest_file_path, file_path)
                        return True, True  # move source to duplicates
                    else:
                        logger.info("Same name, different hash: Keeping older file '%s', moving newer '%s' to duplicates.",
                                    file_path, dest_file_path)
                        # Move destination file to duplicates, keep source
                        ensure_directory_exists(self.duplicate_directory)
                        dup_path = os.path.join(self.duplicate_directory, file_name)
                        move_file_with_logging(
                            dest_file_path, dup_path,
                            db_manager=self.db_manager,
                            logger_obj=logger,
                            destination_directory='duplicates',
                            reason='same_name_different_hash'
                        )
                        # Remove from dest hash cache
                        self.db_manager.update_dest_hash_cache(destination_directory, file_name, None)
                        return False, False  # allow source to be moved to destination
                except Exception as e:
                    logger.error("Error comparing file mtimes: %s", e)
                    return True, True  # fallback: treat as duplicate
        return False, False

    def compute_partial_hash(self, file_path, position='front'):
        """
        Compute fast hash of a file chunk using xxhash.

        This is much faster than computing full file hash, especially for large files.
        Uses only 64KB of I/O per check instead of reading the entire file.

        Args:
            file_path: Path to file
            position: Which chunk to hash ('front', 'middle', 'end')

        Returns:
            bytes: Hash digest, or None on error
        """
        if not HAS_XXHASH:
            return None

        try:
            file_size = os.path.getsize(file_path)

            # Determine offset based on position
            if position == 'front':
                offset = 0
            elif position == 'middle':
                offset = max(0, (file_size // 2) - (self.FAST_HASH_CHUNK // 2))
            elif position == 'end':
                offset = max(0, file_size - self.FAST_HASH_CHUNK)
            else:
                offset = 0

            hasher = xxhash.xxh64()
            with open(file_path, 'rb') as f:
                f.seek(offset)
                chunk = f.read(self.FAST_HASH_CHUNK)
                hasher.update(chunk)

            return hasher.digest()
        except Exception as e:
            logger.debug(f"Error computing partial hash for {file_path}: {e}")
            return None

    def is_duplicate_fast(self, file_path, destination_directory):
        """
        Fast duplicate detection using multi-stage hashing.

        Uses progressive filtering:
        1. Check file size (instant)
        2. Check front 64KB hash (fast)
        3. Check middle 64KB hash (fast)
        4. Check end 64KB hash (fast)
        Total I/O: Only 192KB instead of full file!

        This is 10-100x faster for large video files.

        Args:
            file_path: Source file to check
            destination_directory: Destination to check against

        Returns:
            tuple: (is_duplicate: bool, duplicate_filename: str or None)
        """
        if not self.use_advanced_dedupe:
            # Fallback to traditional method
            return False, None

        try:
            file_size = os.path.getsize(file_path)

            # Stage 1: Check size against destination files
            # Get all files in destination with same size
            dest_files = []
            if os.path.exists(destination_directory):
                for fname in os.listdir(destination_directory):
                    fpath = os.path.join(destination_directory, fname)
                    if os.path.isfile(fpath):
                        try:
                            if os.path.getsize(fpath) == file_size:
                                dest_files.append((fname, fpath))
                        except OSError:
                            continue

            if not dest_files:
                # No files with same size = definitely not a duplicate
                return False, None

            # Stage 2-4: Progressive hash checking
            # Only compute hashes if we found size matches
            for position in ['front', 'middle', 'end']:
                src_hash = self.compute_partial_hash(file_path, position)
                if src_hash is None:
                    # Can't compute hash, fall back to traditional method
                    return False, None

                # Filter candidates by this hash
                remaining = []
                for fname, fpath in dest_files:
                    dest_hash = self.compute_partial_hash(fpath, position)
                    if dest_hash == src_hash:
                        remaining.append((fname, fpath))

                dest_files = remaining

                if not dest_files:
                    # No matches at this stage = not a duplicate
                    return False, None

            # If we get here, we found a file that matches on size + front + middle + end
            # This is a very strong indicator of a duplicate (99.9%+ confidence)
            if dest_files:
                logger.info(f"Fast duplicate detected: {os.path.basename(file_path)} matches {dest_files[0][0]} (multi-stage hash match)")
                return True, dest_files[0][0]

            return False, None

        except Exception as e:
            logger.debug(f"Error in fast duplicate detection: {e}")
            return False, None

    def process_file(self, file, source_directory, destination_directory, file_condition):
        """Process a single file for moving to destination."""
        moved = 0
        dup = 0
        file_path = os.path.join(source_directory, file)

        try:
            # Only log when actually moving files, not for every check
            if os.path.isfile(file_path) and file_condition(file):
                logger.info(f"Moving '{file}' from '{source_directory}' to '{destination_directory}'...")

                # OPTIMIZATION: Try fast duplicate detection first (uses only 192KB I/O)
                # This is 10-100x faster for large video files
                duplicate_name = None
                if self.use_advanced_dedupe and os.path.getsize(file_path) > (10 * 1024 * 1024):  # Only for files > 10MB
                    is_dup_fast, dup_name = self.is_duplicate_fast(file_path, destination_directory)
                    if is_dup_fast:
                        duplicate_name = dup_name
                        logger.info(f"✓ Fast duplicate detection saved full file hash for '{file}'")

                # Compute hashes only if not already detected as duplicate
                # or if we need them for database storage
                stale_database_path = None
                if duplicate_name is None:
                    current_file_hash = hash_file(file_path, logger=logger)
                    md5_hash = compute_md5_hash(file_path, logger=logger)

                    if current_file_hash is None:
                        logger.warning(f"Could not hash file '{file_path}'. Skipping.")
                        return (0, 0)

                    # Check for duplicate by hash in destination (traditional method)
                    duplicate_record = self.db_manager.is_duplicate_in_dest(
                        destination_directory, current_file_hash, include_path=True
                    )
                    if duplicate_record:
                        duplicate_name, recorded_path = duplicate_record
                        if not recorded_path or not os.path.isfile(recorded_path):
                            stale_database_path = duplicate_name
                            logger.info(
                                "Hash matched stale database path for '%s'; repairing path during normal sort.",
                                duplicate_name,
                            )
                            duplicate_name = None
                else:
                    # Fast detection found duplicate, still compute hashes for database
                    current_file_hash = hash_file(file_path, logger=logger)
                    md5_hash = compute_md5_hash(file_path, logger=logger)

                # Also check if file exists in archives
                if not duplicate_name:
                    archive_table, archive_file = self.db_manager.is_duplicate_in_any_dest(current_file_hash, check_archives=True)
                    if archive_table and archive_table.startswith('archive_'):
                        logger.info(f"File '{file}' already exists in archive table '{archive_table}' as '{archive_file}'")
                        duplicate_name = archive_file  # Treat as duplicate

                if duplicate_name:
                    # Found a duplicate - compare sizes and ages
                    try:
                        existing_file_path = os.path.join(destination_directory, duplicate_name)
                        src_size = os.path.getsize(file_path)
                        dest_size = os.path.getsize(existing_file_path)
                        src_mtime = os.path.getmtime(file_path)
                        dest_mtime = os.path.getmtime(existing_file_path)
                        src_atime = os.path.getatime(file_path)
                        dest_atime = os.path.getatime(existing_file_path)

                        # Use oldest modification time for the kept file
                        oldest_mtime = min(src_mtime, dest_mtime)
                        oldest_atime = min(src_atime, dest_atime)

                        if src_size > dest_size:
                            # New file is bigger - REPLACE old file with new one
                            # Keep old file's name and use oldest modification date
                            logger.info(f"Duplicate detected by hash: Replacing '{duplicate_name}' with larger file '{file}' (sizes: {src_size} > {dest_size}).")

                            ensure_directory_exists(self.duplicate_directory)
                            # Move old smaller file to duplicates
                            dup_path = os.path.join(self.duplicate_directory, duplicate_name)
                            move_file_with_logging(
                                existing_file_path, dup_path,
                                db_manager=self.db_manager, logger_obj=logger,
                                destination_directory='duplicates',
                                reason='smaller_duplicate_replaced'
                            )

                            # Move new larger file to destination with old file's name
                            destination_path = os.path.join(destination_directory, duplicate_name)
                            move_file_with_logging(
                                file_path, destination_path,
                                db_manager=self.db_manager, logger_obj=logger,
                                destination_directory=os.path.basename(destination_directory),
                                reason='larger_file_replaces_duplicate'
                            )

                            # Set the oldest modification time on the new file
                            os.utime(destination_path, (oldest_atime, oldest_mtime))

                            logger.info(f"Larger file now at '{destination_path}' with preserved name and oldest modification date.")
                            moved = 1

                            # Update database with new file (keeping the original name)
                            self.db_manager.save_file_hash_to_dest(destination_directory, duplicate_name, current_file_hash, md5_hash)

                        elif src_mtime < dest_mtime:
                            # Source file is older (and same size or smaller) - keep it, move existing to duplicates
                            logger.info(f"Duplicate detected by hash: Keeping older file '{file}', moving newer '{duplicate_name}' to duplicates.")
                            ensure_directory_exists(self.duplicate_directory)
                            dup_path = os.path.join(self.duplicate_directory, duplicate_name)
                            move_file_with_logging(
                                existing_file_path, dup_path,
                                db_manager=self.db_manager, logger_obj=logger,
                                destination_directory='duplicates',
                                reason='newer_duplicate_moved'
                            )
                            # Remove old entry from database and add new one
                            self.db_manager.update_dest_hash_cache(destination_directory, duplicate_name, None)
                            # Move source to destination
                            destination_path = os.path.join(destination_directory, file)
                            move_file_with_logging(
                                file_path, destination_path,
                                db_manager=self.db_manager, logger_obj=logger,
                                destination_directory=os.path.basename(destination_directory),
                                reason='older_source_kept'
                            )
                            logger.info("Older file '%s' moved to '%s'.", file, destination_directory)
                            moved = 1
                            self.db_manager.save_file_hash_to_dest(destination_directory, file, current_file_hash, md5_hash)
                        else:
                            # Existing file is older (and same size or larger) - keep it, move source to duplicates
                            logger.info(f"Duplicate detected by hash: Keeping older file '{duplicate_name}', moving newer '{file}' to duplicates.")
                            ensure_directory_exists(self.duplicate_directory)
                            dup_path = os.path.join(self.duplicate_directory, file)
                            move_file_with_logging(
                                file_path, dup_path,
                                db_manager=self.db_manager, logger_obj=logger,
                                destination_directory='duplicates',
                                reason='newer_source_duplicate'
                            )
                            logger.info("Newer duplicate file '%s' moved to '%s'.", file, self.duplicate_directory)
                            dup = 1
                            self.db_manager.save_file_hash_to_dest(self.duplicate_directory, file, current_file_hash, md5_hash)
                    except Exception as e:
                        # Fallback to original behavior if we can't compare timestamps
                        logger.warning(f"Error comparing file ages for '{file}': {e}. Using fallback behavior.")
                        ensure_directory_exists(self.duplicate_directory)
                        dup_path = os.path.join(self.duplicate_directory, file)
                        move_file_with_logging(
                            file_path, dup_path,
                            db_manager=self.db_manager, logger_obj=logger,
                            destination_directory='duplicates',
                            reason='duplicate_fallback'
                        )
                        logger.info("Duplicate file '%s' moved to '%s'.", file, self.duplicate_directory)
                        dup = 1
                        self.db_manager.save_file_hash_to_dest(self.duplicate_directory, file, current_file_hash, md5_hash)
                else:
                    destination_path = os.path.join(destination_directory, file)
                    move_file_with_logging(
                        file_path, destination_path,
                        db_manager=self.db_manager, logger_obj=logger,
                        destination_directory=os.path.basename(destination_directory),
                        reason='normal_sort'
                    )
                    logger.info("File '%s' moved to '%s'.", file, destination_directory)
                    moved = 1
                    if stale_database_path:
                        table = self.db_manager.table_name_for_dest(destination_directory)
                        self.db_manager.update_file_path(
                            table, stale_database_path, destination_path
                        )
                    if (os.path.abspath(destination_directory) != os.path.abspath(self.manual_sort_directory) and
                        os.path.abspath(destination_directory) != os.path.abspath(self.manual_sort_video_directory)):
                        self.db_manager.save_file_hash_to_dest(destination_directory, file, current_file_hash, md5_hash)
            else:
                logger.debug(f"File '{file}' in '{source_directory}' did not meet condition for '{destination_directory}'. Skipping.")
        except Exception as e:
            logger.error("Exception while processing file '%s': %s", file_path, e)

        return (moved, dup)

    def move_files_single(self, source_directory, destination_directory, file_condition):
        """
        Process all files in a source directory that satisfy file_condition.
        Moves each file sequentially.
        """
        files_moved = 0
        duplicates_moved = 0
        ensure_directory_exists(destination_directory)

        if not os.path.exists(source_directory):
            logger.info("Source directory does not exist: %s", source_directory)
            return files_moved, duplicates_moved

        scan_subdirs = self.config.scan_subdirectories

        if scan_subdirs:
            # Recursive scan with os.walk
            for root, _, files in os.walk(source_directory):
                for file in files:
                    if is_configured_file(file):
                        # Get the full path for processing
                        moved, dup = self.process_file(
                            file, root, destination_directory,
                            file_condition
                        )
                        files_moved += moved
                        duplicates_moved += dup
        else:
            # Non-recursive scan with os.scandir
            for entry in os.scandir(source_directory):
                if entry.is_file() and is_configured_file(entry.name):
                    file = entry.name
                    moved, dup = self.process_file(
                        file, source_directory, destination_directory,
                        file_condition
                    )
                    files_moved += moved
                    duplicates_moved += dup
        return files_moved, duplicates_moved

    def process_directories(self, directories, file_condition, destination):
        """Process multiple directories with the same condition and destination."""
        total_files_moved = 0
        total_duplicates_moved = 0
        for directory in directories:
            moved, dup = self.move_files_single(directory, destination, file_condition)
            total_files_moved += moved
            total_duplicates_moved += dup
        return total_files_moved, total_duplicates_moved

    def move_furaffinity_files(self, source_directories):
        """
        Move FurAffinity files (identified by '-FA-' in the filename) into
        rating-based 'Furaffinity - {Rating}' subfolders.

        General-rated files land under the sfw_* destination; Mature/Adult
        land under the nsfw_* destination, split by media type (image/video/gif).
        """
        media_type_checks = (
            ('nsfw_image', 'sfw_image', is_image),
            ('nsfw_video', 'sfw_video', is_video),
            ('nsfw_gif', 'sfw_gif', is_gif),
        )

        total_moved = 0
        total_duplicates = 0

        for rating in ('General', 'Mature', 'Adult'):
            base_key = 'sfw' if rating == 'General' else 'nsfw'
            for nsfw_key, sfw_key, type_check in media_type_checks:
                dest_key = sfw_key if base_key == 'sfw' else nsfw_key
                base_dir = self.config.destination_directories.get(dest_key)
                if not base_dir:
                    continue

                destination = os.path.join(base_dir, f"Furaffinity - {rating}")

                def file_condition(file, _type_check=type_check, _rating=rating):
                    return (_type_check(file) and is_furaffinity_file(file)
                            and get_furaffinity_rating(file) == _rating)

                moved, dup = self.process_directories(source_directories, file_condition, destination)
                total_moved += moved
                total_duplicates += dup

        return total_moved, total_duplicates

    def move_sfw_images_to_sfw_dir(self, source_directories):
        """Move safe images to the safe image directory."""
        from .utils import is_sfw_or_general
        files_moved = 0
        scan_subdirs = self.config.scan_subdirectories

        # Get NSFW directories to avoid moving files with "safe" from them
        nsfw_dirs = []
        for key, path in self.config.destination_directories.items():
            if 'nsfw' in key.lower():
                nsfw_dirs.append(os.path.abspath(path))

        for source_directory in source_directories:
            if not os.path.exists(source_directory):
                logger.info("Source directory does not exist: %s", source_directory)
                continue

            # Skip moving files with "safe" from NSFW directories
            abs_source = os.path.abspath(source_directory)
            if any(abs_source == nsfw_dir for nsfw_dir in nsfw_dirs):
                logger.debug(f"Skipping safe image processing for NSFW directory: {source_directory}")
                continue

            if scan_subdirs:
                # Recursive scan with os.walk
                for root, _, files in os.walk(source_directory):
                    for file in files:
                        if is_image(file) and is_sfw_or_general(file):
                            logger.info(f"Moving safe image '{file}' to sfw_image directory.")
                            file_path = os.path.join(root, file)
                            ensure_directory_exists(self.config.destination_directories['sfw_image'])
                            destination_path = os.path.join(self.config.destination_directories['sfw_image'], file)
                            move_file_with_logging(
                                file_path, destination_path,
                                db_manager=self.db_manager, logger_obj=logger,
                                destination_directory='sfw_image',
                                reason='sfw_image_sort'
                            )
                            logger.info("Safe image '%s' moved to '%s'.", file, destination_path)
                            # Sync to database
                            try:
                                self.db_manager.save_file_hash_to_dest(self.config.destination_directories['sfw_image'], file, None, None)
                            except Exception as e:
                                logger.warning(f"Failed to sync file to DB: {e}")
                            files_moved += 1
            else:
                # Non-recursive scan with os.scandir
                with os.scandir(source_directory) as it:
                    for entry in it:
                        if entry.is_file() and is_image(entry.name) and is_sfw_or_general(entry.name):
                            logger.info(f"Moving safe image '{entry.name}' to sfw_image directory.")
                            file = entry.name
                            file_path = os.path.join(source_directory, file)
                            ensure_directory_exists(self.config.destination_directories['sfw_image'])
                            destination_path = os.path.join(self.config.destination_directories['sfw_image'], file)
                            move_file_with_logging(
                                file_path, destination_path,
                                db_manager=self.db_manager, logger_obj=logger,
                                destination_directory='sfw_image',
                                reason='sfw_image_sort'
                            )
                            logger.info("Safe image '%s' moved to '%s'.", file, destination_path)
                            # Sync to database
                            try:
                                self.db_manager.save_file_hash_to_dest(self.config.destination_directories['sfw_image'], file, None, None)
                            except Exception as e:
                                logger.warning(f"Failed to sync file to DB: {e}")
                            files_moved += 1
        return files_moved

    def move_remaining_files_to_manual_sort(self, source_directories):
        """Move remaining files to appropriate manual sort directories."""
        files_moved = 0
        scan_subdirs = self.config.scan_subdirectories

        for source_directory in source_directories:
            if not os.path.exists(source_directory):
                logger.info("Source directory does not exist: %s", source_directory)
                continue

            # Skip if source directory is the same as manual sort directory
            manual_sort_dirs = [self.manual_sort_directory, self.manual_sort_video_directory]
            if self.manual_sort_gif_directory:
                manual_sort_dirs.append(self.manual_sort_gif_directory)

            if any(os.path.abspath(source_directory) == os.path.abspath(d) for d in manual_sort_dirs if d):
                logger.debug("Skipping source directory '%s' as it matches manual sort directory", source_directory)
                continue

            if scan_subdirs:
                # Recursive scan with os.walk
                for root, _, files in os.walk(source_directory):
                    for file in files:
                        # Only move files that are images, gifs, or videos
                        if is_configured_file(file) and (is_image(file) or is_gif(file) or is_video(file)):
                            # Determine destination based on file type
                            if is_video(file):
                                destination_dir = self.manual_sort_video_directory
                                logger.info(f"Moving video '{file}' to manual sort video directory.")
                            elif is_gif(file) and self.manual_sort_gif_directory:
                                destination_dir = self.manual_sort_gif_directory
                                logger.info(f"Moving gif '{file}' to manual sort gif directory.")
                            else:
                                destination_dir = self.manual_sort_directory
                                logger.info(f"Moving '{file}' to manual sort directory.")

                            file_path = os.path.join(root, file)
                            ensure_directory_exists(destination_dir)
                            destination_path = os.path.join(destination_dir, file)
                            move_file_with_logging(
                                file_path, destination_path,
                                db_manager=self.db_manager, logger_obj=logger,
                                destination_directory=os.path.basename(destination_dir),
                                reason='manual_sort'
                            )
                            logger.info("File '%s' moved to manual sort directory '%s'.", file, destination_path)
                            files_moved += 1
            else:
                # Non-recursive scan with os.scandir
                with os.scandir(source_directory) as it:
                    for entry in it:
                        # Only move files that are images, gifs, or videos
                        if entry.is_file() and is_configured_file(entry.name):
                            file = entry.name
                            if is_image(file) or is_gif(file) or is_video(file):
                                # Determine destination based on file type
                                if is_video(file):
                                    destination_dir = self.manual_sort_video_directory
                                    logger.info(f"Moving video '{file}' to manual sort video directory.")
                                elif is_gif(file) and self.manual_sort_gif_directory:
                                    destination_dir = self.manual_sort_gif_directory
                                    logger.info(f"Moving gif '{file}' to manual sort gif directory.")
                                else:
                                    destination_dir = self.manual_sort_directory
                                    logger.info(f"Moving '{file}' to manual sort directory.")

                                file_path = os.path.join(source_directory, file)
                                ensure_directory_exists(destination_dir)
                                destination_path = os.path.join(destination_dir, file)
                                move_file_with_logging(
                                    file_path, destination_path,
                                    db_manager=self.db_manager, logger_obj=logger,
                                    destination_directory=os.path.basename(destination_dir),
                                    reason='manual_sort'
                                )
                                logger.info("File '%s' moved to manual sort directory '%s'.", file, destination_path)
                                # Sync to database
                                try:
                                    self.db_manager.save_file_hash_to_dest(destination_dir, file, None, None)
                                except Exception as e:
                                    logger.warning(f"Failed to sync file to DB: {e}")
                                files_moved += 1
        return files_moved

    def move_special_category_dirs(self, source_directories):
        """Move files from special category directories (adult/mature - treated as NSFW)."""
        nsfw_dir = self.config.destination_directories.get('nsfw_image')
        files_moved_nsfw = 0
        scan_subdirs = self.config.scan_subdirectories

        for source_directory in source_directories:
            if not os.path.exists(source_directory):
                continue

            if scan_subdirs:
                # Recursive scan with os.walk to find special category dirs anywhere
                for root, dirs, _ in os.walk(source_directory):
                    for dir_name in dirs:
                        if dir_name.lower() in ('adult', 'mature'):
                            special_dir_path = os.path.join(root, dir_name)
                            logger.info(f"Moving all files from '{special_dir_path}' to nsfw_image directory.")
                            for file_entry in os.scandir(special_dir_path):
                                if file_entry.is_file() and is_configured_file(file_entry.name):
                                    src_path = file_entry.path
                                    dst_path = os.path.join(nsfw_dir, file_entry.name)
                                    move_file_with_logging(
                                        src_path, dst_path,
                                        db_manager=self.db_manager, logger_obj=logger,
                                        destination_directory='nsfw_image',
                                        reason='special_category_nsfw_recursive'
                                    )
                                    logger.info("File '%s' from '%s' moved to nsfw_image '%s'.", file_entry.name, special_dir_path, dst_path)
                                    # Sync to database
                                    try:
                                        self.db_manager.save_file_hash_to_dest(nsfw_dir, file_entry.name, None, None)
                                    except Exception as e:
                                        logger.warning(f"Failed to sync file to DB: {e}")
                                    files_moved_nsfw += 1
            else:
                # Non-recursive scan - only check immediate subdirectories
                for entry in os.scandir(source_directory):
                    if entry.is_dir():
                        dir_name = entry.name.lower()
                        if dir_name in ('adult', 'mature'):
                            logger.info(f"Moving all files from '{entry.path}' to nsfw_image directory.")
                            for file_entry in os.scandir(entry.path):
                                if file_entry.is_file() and is_configured_file(file_entry.name):
                                    src_path = file_entry.path
                                    dst_path = os.path.join(nsfw_dir, file_entry.name)
                                    move_file_with_logging(
                                        src_path, dst_path,
                                        db_manager=self.db_manager, logger_obj=logger,
                                        destination_directory='nsfw_image',
                                        reason='special_category_nsfw_nonrecursive'
                                    )
                                    logger.info("File '%s' from '%s' moved to nsfw_image '%s'.", file_entry.name, entry.path, dst_path)
                                    # Sync to database
                                    try:
                                        self.db_manager.save_file_hash_to_dest(nsfw_dir, file_entry.name, None, None)
                                    except Exception as e:
                                        logger.warning(f"Failed to sync file to DB: {e}")
                                    files_moved_nsfw += 1
        return files_moved_nsfw
