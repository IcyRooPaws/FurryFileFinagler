# fff: implemented in media sorter/_handlers/archive_manager.py.
"""
Archive management for old files in the media sorter system.

This module provides functionality to archive old files based on age criteria,
integrated with the main database and configuration system.
"""
import os
import shutil
import datetime
import threading
import logging
from concurrent.futures import ThreadPoolExecutor

from .utils import (
    hash_file, ensure_directory_exists, move_file_with_logging,
    is_furaffinity_file, get_furaffinity_rating
)

logger = logging.getLogger(__name__)

def format_size(size_bytes):
    """Format bytes to human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"

class ArchiveManager:
    """Manages archiving of old files with database integration."""

    def __init__(self, config, db_manager):
        self.config = config
        self.db_manager = db_manager

        # Archive configuration (can be extended to read from archive_config.yaml)
        self.default_age_days = 365  # Default: archive files older than 1 year
        self.default_threads = 4

        # Supported file extensions
        self.supported_extensions = {
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp',
            '.mp4', '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm'
        }

        # Default exclusion patterns for directories (case-insensitive)
        self.default_exclusions = ['- fav', 'favorites', 'favourite', 'favourites']

        # Get custom exclusion patterns from config
        archive_options = self.config.config.get('archive_options', {})
        self.custom_exclusions = archive_options.get('excluded_directories', [])
        if self.custom_exclusions:
            logger.info(f"Loaded {len(self.custom_exclusions)} custom directory exclusion patterns from config")

        # Collect tag-sorted directories if configured to exclude them
        self.tag_sorted_dirs = self._get_tag_sorted_directories()

    def _get_tag_sorted_directories(self):
        """Get list of tag-sorted target directories to exclude from archiving."""
        # Check if tag-sorted directories should be excluded
        archive_options = self.config.config.get('archive_options', {})
        # Default to True for safety - we want to PROTECT tag-sorted directories by default
        exclude_tag_sorted = archive_options.get('exclude_tag_sorted', True)

        if not exclude_tag_sorted:
            return []

        tag_sorted_dirs = set()

        # Try to get tag sorting rules from config
        if hasattr(self.config, 'tag_sorting_config') and self.config.tag_sorting_config:
            rules = self.config.tag_sorting_config.get('rules', [])
            for rule in rules:
                if rule.get('enabled', True):
                    target_dir = rule.get('target_directory')
                    if target_dir:
                        # Normalize path for consistent comparison
                        normalized_dir = os.path.normpath(target_dir).lower()
                        tag_sorted_dirs.add(normalized_dir)
                        logger.info(f"Will exclude tag-sorted directory from archiving: {target_dir}")

                        # Also exclude parent directories that might contain tag-sorted content
                        # For example, if target is ".../ NSFW Pics/Tag Sorted/Diaper",
                        # also exclude ".../ NSFW Pics/Tag Sorted" itself
                        parent_dir = os.path.dirname(normalized_dir)
                        parent_name = os.path.basename(parent_dir).lower()
                        # If parent directory is named "tag sorted" or similar, exclude it too
                        if 'tag sorted' in parent_name or 'tag_sorted' in parent_name:
                            if parent_dir not in tag_sorted_dirs:
                                tag_sorted_dirs.add(parent_dir)
                                logger.info(f"Will also exclude parent tag directory: {parent_dir}")

        return list(tag_sorted_dirs)

    def is_file_old_enough(self, file_path, age_days):
        """Check if file is older than specified number of days."""
        try:
            file_mtime = os.path.getmtime(file_path)
            file_date = datetime.datetime.fromtimestamp(file_mtime)
            cutoff_date = datetime.datetime.now() - datetime.timedelta(days=age_days)
            return file_date < cutoff_date
        except Exception as e:
            logger.error(f"Error checking file age for {file_path}: {e}")
            return False

    def should_exclude_directory(self, dir_path, exclusion_patterns):
        """Check if directory should be excluded based on exclusion patterns."""
        # Normalize the directory path for comparison
        dir_path_lower = dir_path.lower()
        dir_path_normalized = os.path.normpath(dir_path).lower()

        # Check pattern-based exclusions (substring matching)
        if exclusion_patterns:
            for pattern in exclusion_patterns:
                pattern_lower = pattern.lower()
                # Check if pattern appears anywhere in the path
                if pattern_lower in dir_path_lower:
                    return True

        # Check tag-sorted directory exclusions (exact and parent path matching)
        for tag_dir in self.tag_sorted_dirs:
            # Exact match
            if dir_path_normalized == tag_dir:
                return True

            # Check if dir_path is a subdirectory of tag_dir
            # Add separator to ensure we're matching directory boundaries
            tag_dir_with_sep = tag_dir + os.sep
            dir_path_with_sep = dir_path_normalized + os.sep

            if dir_path_with_sep.startswith(tag_dir_with_sep):
                return True

        return False

    def calculate_total_size(self, file_list):
        """Calculate total size of files in list.

        Args:
            file_list: List of file paths

        Returns:
            Total size in bytes
        """
        total_size = 0
        for file_path in file_list:
            try:
                if os.path.exists(file_path):
                    total_size += os.path.getsize(file_path)
            except Exception as e:
                logger.warning(f"Could not get size of {file_path}: {e}")
        return total_size

    def get_files_to_archive(self, source_dir, age_days, file_extensions=None, exclude_favorites=True):
        """Get list of files in source directory that are old enough to archive.

        Args:
            source_dir: Source directory to scan
            age_days: Files older than this many days will be archived
            file_extensions: Set of file extensions to include (default: all supported)
            exclude_favorites: If True, exclude favorite directories (default: True)

        Returns:
            List of file paths to archive
        """
        if file_extensions is None:
            file_extensions = self.supported_extensions

        # Convert to set and ensure lowercase
        file_extensions = {ext.lower() for ext in file_extensions}
        files_to_archive = []
        excluded_dirs = []

        # Set up exclusion patterns - combine default and custom exclusions
        exclusion_patterns = []
        if exclude_favorites:
            exclusion_patterns.extend(self.default_exclusions)
        if self.custom_exclusions:
            exclusion_patterns.extend(self.custom_exclusions)

        if not os.path.exists(source_dir):
            logger.warning(f"Source directory does not exist: {source_dir}")
            return files_to_archive

        logger.info(f"Scanning {source_dir} for files older than {age_days} days...")
        if exclude_favorites:
            logger.info(f"Excluding favorite directories: {', '.join(self.default_exclusions)}")
        if self.custom_exclusions:
            logger.info(f"Excluding {len(self.custom_exclusions)} custom directory patterns: {', '.join(self.custom_exclusions)}")
        if self.tag_sorted_dirs:
            logger.info(f"Excluding {len(self.tag_sorted_dirs)} tag-sorted directories from archiving")

        for root, dirs, files in os.walk(source_dir):
            # Check if current directory should be excluded
            if self.should_exclude_directory(root, exclusion_patterns):
                if root not in excluded_dirs:
                    excluded_dirs.append(root)
                    logger.info(f"Skipping excluded directory: {root}")
                # Clear dirs list to prevent os.walk from descending into subdirectories
                dirs.clear()
                continue

            for file in files:
                file_path = os.path.join(root, file)
                file_ext = os.path.splitext(file)[1].lower()

                if file_ext in file_extensions:
                    if self.is_file_old_enough(file_path, age_days):
                        files_to_archive.append(file_path)

        logger.info(f"Found {len(files_to_archive)} files to archive in {source_dir}")
        if excluded_dirs:
            logger.info(f"Excluded {len(excluded_dirs)} favorite directories from archiving")
        return files_to_archive

    def archive_single_file(self, file_info):
        """Archive a single file using database for duplicate tracking."""
        try:
            source_file, source_dir, archive_dir, lock, organize_by_year, preserve_structure, cutoff_days = file_info

            # Validate archive_dir
            if not archive_dir:
                logger.error(f"Archive directory is None for file: {source_file}")
                return False

            # Get source filename for database operations
            source_filename = os.path.basename(source_file)

            # Get source table and retrieve full metadata BEFORE moving file
            source_table = self.db_manager.table_name_for_dest(source_dir)
            file_metadata = None
            if source_table:
                file_metadata = self.db_manager.get_file_metadata(source_table, source_filename)
                if file_metadata:
                    logger.debug(f"Retrieved metadata for {source_filename} from {source_table}")

            # Calculate file hash
            file_hash = hash_file(source_file, logger=logger)
            if not file_hash:
                logger.error(f"Could not calculate hash for {source_file}")
                return False

            # Check if file already exists in archive (by hash)
            with lock:
                existing_file = self.db_manager.is_duplicate_in_dest(archive_dir, file_hash)
                if existing_file:
                    logger.info(f"File {source_file} already archived as {existing_file} (duplicate hash)")
                    # Remove the source file since it's already archived
                    try:
                        os.remove(source_file)
                        logger.info(f"Removed duplicate source file: {source_file}")

                        # Remove from source database table
                        if source_table:
                            self.db_manager.remove_file_from_table(source_table, source_filename)
                            logger.debug(f"Removed {source_filename} from {source_table} table")
                    except Exception as e:
                        logger.error(f"Could not remove duplicate source file {source_file}: {e}")
                    return True

            # Create archive path
            fa_rating = get_furaffinity_rating(source_filename) if is_furaffinity_file(source_filename) else None

            if fa_rating:
                # FurAffinity files always get their own rating-based subfolder in the
                # archive, regardless of the subdirectory structure they were found in
                if organize_by_year:
                    file_mtime = os.path.getmtime(source_file)
                    file_year = datetime.datetime.fromtimestamp(file_mtime).year
                    archive_path = os.path.join(archive_dir, str(file_year), f"Furaffinity - {fa_rating}", source_filename)
                else:
                    archive_path = os.path.join(archive_dir, f"Furaffinity - {fa_rating}", source_filename)
            elif preserve_structure:
                # Preserve the subdirectory structure from source
                rel_path = os.path.relpath(source_file, source_dir)
                if organize_by_year:
                    # Add year prefix to preserved structure
                    file_mtime = os.path.getmtime(source_file)
                    file_year = datetime.datetime.fromtimestamp(file_mtime).year
                    archive_path = os.path.join(archive_dir, str(file_year), rel_path)
                else:
                    # Just preserve the original structure
                    archive_path = os.path.join(archive_dir, rel_path)
            else:
                # Flatten files (legacy behavior)
                if organize_by_year:
                    # Organize by year
                    file_mtime = os.path.getmtime(source_file)
                    file_year = datetime.datetime.fromtimestamp(file_mtime).year
                    filename = os.path.basename(source_file)
                    archive_path = os.path.join(archive_dir, str(file_year), filename)
                else:
                    # Just use filename
                    filename = os.path.basename(source_file)
                    archive_path = os.path.join(archive_dir, filename)

            # Ensure destination directory exists
            ensure_directory_exists(os.path.dirname(archive_path))

            # Handle filename conflicts
            if os.path.exists(archive_path):
                base, ext = os.path.splitext(archive_path)
                counter = 1
                while os.path.exists(f"{base}_{counter}{ext}"):
                    counter += 1
                archive_path = f"{base}_{counter}{ext}"

            # Calculate directory types for logging
            source_dir_type = os.path.basename(source_dir) if source_table else 'unknown'
            archive_table = self.db_manager.table_name_for_dest(archive_dir) or 'unknown'

            # Move file to archive
            move_file_with_logging(
                source_file, archive_path,
                db_manager=self.db_manager, logger_obj=logger,
                source_directory=source_dir_type,
                destination_directory=archive_table,
                reason=f'archive_old_file_{cutoff_days}days'
            )

            # Update database: Add to archive table with full metadata AND remove from source table
            with lock:
                archived_filename = os.path.basename(archive_path)

                # Update metadata with actual archive path (including year folder if organized by year)
                if file_metadata and organize_by_year:
                    # Clone metadata and update file_path to include year subdirectory
                    import copy
                    archive_metadata = copy.copy(file_metadata)
                    # Don't set file_path in metadata - let the database manager construct it correctly
                    # But we need to save with the actual full path
                elif file_metadata:
                    archive_metadata = file_metadata
                else:
                    archive_metadata = None

                # Save to archive table - use archive_path to get correct relative structure
                if archive_metadata:
                    # For year-organized archives, we need to use a custom save that preserves the path
                    # Get the relative path from archive_dir
                    rel_path = os.path.relpath(archive_path, archive_dir)
                    # Use rel_path instead of just filename to preserve year structure
                    table = self.db_manager.table_name_for_dest(archive_dir)
                    if table:
                        from .utils import normalize_path
                        normalized_archive_path = normalize_path(archive_path)

                        # Directly insert with correct path
                        conn = None
                        try:
                            conn = self.db_manager.get_db_connection()
                            cursor = conn.cursor()

                            dhash_val = archive_metadata.get('perceptual_hash_dhash')
                            phash_val = archive_metadata.get('perceptual_hash_phash')
                            avg_val = archive_metadata.get('perceptual_hash_average')
                            whash_val = archive_metadata.get('perceptual_hash_whash')
                            md5_hash = archive_metadata.get('md5_hash')
                            e621_post_id = archive_metadata.get('e621_post_id')
                            file_size = archive_metadata.get('file_size')
                            file_modified_at = archive_metadata.get('file_modified_at')
                            file_created_at = archive_metadata.get('file_created_at')

                            cursor.execute(
                                f"""INSERT INTO `{table}`
                                    (file_name, perceptual_hash_dhash, perceptual_hash_phash, perceptual_hash_average, perceptual_hash_whash,
                                     md5_hash, e621_post_id, file_size, file_modified_at, file_created_at, file_path)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON DUPLICATE KEY UPDATE
                                        file_name = VALUES(file_name),
                                        perceptual_hash_dhash = VALUES(perceptual_hash_dhash),
                                        perceptual_hash_phash = VALUES(perceptual_hash_phash),
                                        perceptual_hash_average = VALUES(perceptual_hash_average),
                                        perceptual_hash_whash = VALUES(perceptual_hash_whash),
                                        md5_hash = VALUES(md5_hash),
                                        e621_post_id = VALUES(e621_post_id),
                                        file_size = VALUES(file_size),
                                        file_modified_at = VALUES(file_modified_at),
                                        file_created_at = VALUES(file_created_at)""",
                                (archived_filename, dhash_val, phash_val, avg_val, whash_val, md5_hash, e621_post_id,
                                 file_size, file_modified_at, file_created_at, normalized_archive_path)
                            )
                            conn.commit()
                            cursor.close()
                            logger.debug(f"Saved metadata for {archived_filename} to archive table with path: {normalized_archive_path}")
                        except Exception as e:
                            logger.error(f"Error saving metadata to archive table: {e}")
                        finally:
                            if conn:
                                conn.close()
                else:
                    # Fallback to basic hash save if no metadata available
                    # This also needs to use the correct path
                    from .utils import normalize_path
                    normalized_archive_path = normalize_path(archive_path)
                    table = self.db_manager.table_name_for_dest(archive_dir)
                    if table:
                        self.db_manager.ensure_dest_table(table)
                        conn = None
                        try:
                            conn = self.db_manager.get_db_connection()
                            cursor = conn.cursor()

                            # Parse hash components
                            dhash_val = None
                            phash_val = None
                            avg_val = None
                            whash_val = None

                            if file_hash and '|' in file_hash:
                                components = file_hash.split('|')
                                if len(components) == 2:
                                    dhash_val = components[0]
                                    phash_val = components[1]
                                elif len(components) == 4:
                                    avg_val = components[0]
                                    dhash_val = components[1]
                                    phash_val = components[2]
                                    whash_val = components[3]
                            elif file_hash:
                                phash_val = file_hash

                            cursor.execute(
                                f"""INSERT INTO `{table}`
                                    (file_name, perceptual_hash_dhash, perceptual_hash_phash,
                                     perceptual_hash_average, perceptual_hash_whash, file_path)
                                    VALUES (%s, %s, %s, %s, %s, %s)
                                    ON DUPLICATE KEY UPDATE
                                        perceptual_hash_dhash = VALUES(perceptual_hash_dhash),
                                        perceptual_hash_phash = VALUES(perceptual_hash_phash),
                                        perceptual_hash_average = VALUES(perceptual_hash_average),
                                        perceptual_hash_whash = VALUES(perceptual_hash_whash)""",
                                (archived_filename, dhash_val, phash_val, avg_val, whash_val, normalized_archive_path)
                            )
                            conn.commit()
                            cursor.close()
                            logger.debug(f"Saved hash for {archived_filename} to archive table with path: {normalized_archive_path}")
                        except Exception as e:
                            logger.error(f"Error saving hash to archive table: {e}")
                        finally:
                            if conn:
                                conn.close()

                # Remove from source table
                if source_table:
                    self.db_manager.remove_file_from_table(source_table, source_filename)
                    logger.debug(f"Removed {source_filename} from {source_table} table")

            logger.info(f"Archived: {source_file} -> {archive_path}")
            return True

        except Exception as e:
            logger.error(f"Error archiving {source_file}: {e}")
            return False

    def archive_directory(self, source_dir, archive_dir, age_days=None,
                         num_threads=None, file_extensions=None, organize_by_year=True,
                         exclude_favorites=True, preserve_structure=True):
        """Archive files from source directory to archive directory.

        Args:
            source_dir: Source directory to archive from
            archive_dir: Target archive directory
            age_days: Archive files older than this many days (default: 365)
            num_threads: Number of threads to use (default: 4)
            file_extensions: File extensions to archive (default: all supported)
            organize_by_year: Organize archived files by year (default: True)
            exclude_favorites: Exclude favorite directories from archiving (default: True)
            preserve_structure: Preserve subdirectory structure from source (default: True)
        """
        # Validate archive directory
        if not archive_dir:
            logger.error(f"Archive directory is not set for source: {source_dir}")
            return {'archived': 0, 'failed': 0, 'total_size': 0}

        if age_days is None:
            age_days = self.default_age_days
        if num_threads is None:
            num_threads = self.default_threads

        logger.info(f"Starting archive process for {source_dir}")
        logger.info(f"Archive age threshold: {age_days} days")
        logger.info(f"Target archive directory: {archive_dir}")
        logger.info(f"Exclude favorites: {exclude_favorites}")
        logger.info(f"Preserve directory structure: {preserve_structure}")

        # Ensure archive directory exists
        ensure_directory_exists(archive_dir)

        # Get files to archive
        files_to_archive = self.get_files_to_archive(source_dir, age_days, file_extensions, exclude_favorites)

        if not files_to_archive:
            logger.info(f"No files to archive in {source_dir}")
            return {'archived': 0, 'failed': 0}

        # Create thread lock for database updates
        lock = threading.Lock()

        # Prepare file info for threading (include source_dir for database removal)
        file_infos = [(f, source_dir, archive_dir, lock, organize_by_year, preserve_structure, age_days) for f in files_to_archive]

        # Archive files using thread pool
        successful_archives = 0
        failed_archives = 0

        logger.info(f"Archiving {len(files_to_archive)} files using {num_threads} threads...")

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            results = executor.map(self.archive_single_file, file_infos)

            for result in results:
                if result:
                    successful_archives += 1
                else:
                    failed_archives += 1

        # Calculate total size archived
        total_size = self.calculate_total_size([f for f in files_to_archive if os.path.exists(f)])

        logger.info(f"Archive complete for {source_dir}:")
        logger.info(f"  Successfully archived: {successful_archives} files ({format_size(total_size)})")
        logger.info(f"  Failed to archive: {failed_archives} files")

        return {
            'archived': successful_archives,
            'failed': failed_archives,
            'total_size': total_size
        }

    def _get_archive_path(self, archive_config, content_type):
        """Get archive path from configuration, supporting both simple and expanded formats.

        Args:
            archive_config: Archive configuration (can be string path or dict with 'images', 'videos', 'gifs' keys)
            content_type: Type of content - 'images', 'videos', or 'gifs'

        Returns:
            Archive directory path, or None if not configured
        """
        if not archive_config:
            return None

        # Simple format: archive_config is a string path
        if isinstance(archive_config, str):
            return archive_config

        # Expanded format: archive_config is a dict with media type keys
        if isinstance(archive_config, dict):
            return archive_config.get(content_type)

        return None

    def ensure_all_archive_directories_exist(self):
        """Ensure all configured archive directories exist, creating them if needed."""
        if 'archive_directories' not in self.config.config:
            logger.debug("No archive_directories configured")
            return

        archive_dirs_config = self.config.config['archive_directories']
        created_dirs = []

        # Check each archive type (premium, explicit, sfw)
        for archive_type, archive_config in archive_dirs_config.items():
            if not archive_config:
                continue

            # Handle simple format (string path)
            if isinstance(archive_config, str):
                if not os.path.exists(archive_config):
                    try:
                        ensure_directory_exists(archive_config)
                        created_dirs.append(archive_config)
                        logger.info(f"Created archive directory: {archive_config}")
                    except Exception as e:
                        logger.error(f"Failed to create archive directory {archive_config}: {e}")

            # Handle expanded format (dict with images/videos/gifs keys)
            elif isinstance(archive_config, dict):
                for media_type, path in archive_config.items():
                    if path and not os.path.exists(path):
                        try:
                            ensure_directory_exists(path)
                            created_dirs.append(path)
                            logger.info(f"Created archive directory for {archive_type}/{media_type}: {path}")
                        except Exception as e:
                            logger.error(f"Failed to create archive directory {path}: {e}")

        if created_dirs:
            logger.info(f"Created {len(created_dirs)} archive directories")
        else:
            logger.debug("All configured archive directories already exist")

    def archive_by_content_type(self, age_days=None, num_threads=None, exclude_favorites=True):
        """Archive files by content type using the main configuration.

        Args:
            age_days: Archive files older than this many days (default: 365)
            num_threads: Number of threads to use (default: 4)
            exclude_favorites: Exclude favorite directories from archiving (default: True)
        """
        results = {}

        if 'archive_directories' not in self.config.config:
            logger.warning("No archive_directories configured")
            return results

        # Ensure all configured archive directories exist
        self.ensure_all_archive_directories_exist()

        # Archive SFW content
        sfw_config = (
            self.config.config['archive_directories'].get('sfw')
            or self.config.config['archive_directories'].get('safe')  # Legacy support
        )

        if sfw_config:
            # Archive SFW images
            sfw_image_dir = self.config.destination_directories.get('sfw_image')
            archive_sfw_images = self._get_archive_path(sfw_config, 'images')
            if sfw_image_dir and archive_sfw_images:
                logger.info(f"Archiving SFW images to {archive_sfw_images}...")
                results['sfw_images'] = self.archive_directory(
                    sfw_image_dir, archive_sfw_images, age_days, num_threads,
                    exclude_favorites=exclude_favorites, preserve_structure=True
                )
            elif sfw_image_dir and not archive_sfw_images:
                logger.warning(f"SFW images archive destination not configured - skipping {sfw_image_dir}")

            # Archive SFW videos
            sfw_video_dir = self.config.destination_directories.get('sfw_video')
            archive_sfw_videos = self._get_archive_path(sfw_config, 'videos')
            if sfw_video_dir and archive_sfw_videos:
                logger.info(f"Archiving SFW videos to {archive_sfw_videos}...")
                results['sfw_videos'] = self.archive_directory(
                    sfw_video_dir, archive_sfw_videos, age_days, num_threads,
                    exclude_favorites=exclude_favorites, preserve_structure=True
                )
            elif sfw_video_dir and not archive_sfw_videos:
                logger.warning(f"SFW videos archive destination not configured - skipping {sfw_video_dir}")

            # Archive SFW gifs
            sfw_gif_dir = self.config.destination_directories.get('sfw_gif')
            archive_sfw_gifs = self._get_archive_path(sfw_config, 'gifs')
            if sfw_gif_dir and archive_sfw_gifs:
                logger.info(f"Archiving SFW gifs to {archive_sfw_gifs}...")
                results['sfw_gifs'] = self.archive_directory(
                    sfw_gif_dir, archive_sfw_gifs, age_days, num_threads,
                    exclude_favorites=exclude_favorites, preserve_structure=True
                )
            elif sfw_gif_dir and not archive_sfw_gifs:
                logger.warning(f"SFW gifs archive destination not configured - skipping {sfw_gif_dir}")

        # Archive NSFW content
        nsfw_config = self.config.config['archive_directories'].get('explicit')

        if nsfw_config:
            # Archive NSFW images
            nsfw_image_dir = self.config.destination_directories.get('nsfw_image')
            archive_nsfw_images = self._get_archive_path(nsfw_config, 'images')
            if nsfw_image_dir and archive_nsfw_images:
                logger.info(f"Archiving NSFW images to {archive_nsfw_images}...")
                results['nsfw_images'] = self.archive_directory(
                    nsfw_image_dir, archive_nsfw_images, age_days, num_threads,
                    exclude_favorites=exclude_favorites, preserve_structure=True
                )
            elif nsfw_image_dir and not archive_nsfw_images:
                logger.warning(f"NSFW images archive destination not configured - skipping {nsfw_image_dir}")

            # Archive NSFW videos
            nsfw_video_dir = self.config.destination_directories.get('nsfw_video')
            archive_nsfw_videos = self._get_archive_path(nsfw_config, 'videos')
            if nsfw_video_dir and archive_nsfw_videos:
                logger.info(f"Archiving NSFW videos to {archive_nsfw_videos}...")
                results['nsfw_videos'] = self.archive_directory(
                    nsfw_video_dir, archive_nsfw_videos, age_days, num_threads,
                    exclude_favorites=exclude_favorites, preserve_structure=True
                )
            elif nsfw_video_dir and not archive_nsfw_videos:
                logger.warning(f"NSFW videos archive destination not configured - skipping {nsfw_video_dir}")

            # Archive NSFW gifs
            nsfw_gif_dir = self.config.destination_directories.get('nsfw_gif')
            archive_nsfw_gifs = self._get_archive_path(nsfw_config, 'gifs')
            if nsfw_gif_dir and archive_nsfw_gifs:
                logger.info(f"Archiving NSFW gifs to {archive_nsfw_gifs}...")
                results['nsfw_gifs'] = self.archive_directory(
                    nsfw_gif_dir, archive_nsfw_gifs, age_days, num_threads,
                    exclude_favorites=exclude_favorites, preserve_structure=True
                )
            elif nsfw_gif_dir and not archive_nsfw_gifs:
                logger.warning(f"NSFW gifs archive destination not configured - skipping {nsfw_gif_dir}")

        return results