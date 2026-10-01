# fff: imports point at fff.sorter.*; the sys.path hack is gone; GUIs are imported lazily from fff.gui.
# fff: CWD-relative files (post_id_list.txt, move_log.log, configs/*.yaml, configs/examples/*) use
# fff: fff.sorter.paths / fff.common.paths; schema names come from the environment.
# Test database and maintenance operations use the unified configuration.
"""
CLI commands module for FFF.

Contains command implementations for the unified CLI interface.
"""
import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime

from fff.common import paths as fff_paths
from fff.sorter import paths as sorter_paths

logger = logging.getLogger('fff.sorter.cli')


class BaseCommand:
    """Base class for CLI commands."""

    @classmethod
    def add_arguments(cls, parser):
        """Add command-specific arguments to parser."""
        pass

    @classmethod
    def execute(cls, args, config):
        """Execute the command."""
        raise NotImplementedError


class SortCommand(BaseCommand):
    """Main file sorting command."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually moving files'
        )
        parser.add_argument(
            '--skip-hash',
            action='store_true',
            help='Skip post-processing hash operations'
        )
        parser.add_argument(
            '--rehash-only',
            action='store_true',
            help='Skip file sorting and only re-hash all destination directories (useful after perceptual hash updates)'
        )
        parser.add_argument(
            '--force-rehash',
            action='store_true',
            help='Force re-hashing of all files, even if they appear unchanged (use with --rehash-only to regenerate all hashes)'
        )
        parser.add_argument(
            '--verbose-hash',
            action='store_true',
            help='Show detailed output for each file being hashed (file path, table, hash values)'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute main sorting operation."""

        try:
            # Import legacy components for now
            from fff.sorter.handlers import (
                DatabaseManager, FileOperations, ArtistManager,
                HashManager, FileSorter
            )

            # Initialize components
            print("[FOLDER] Initializing database connection...")
            db_manager = DatabaseManager(config)

            # If rehash-only mode, skip sorting entirely
            if args.rehash_only:
                logger.info("Running in rehash-only mode - skipping file sorting")
                print("[Loop] Rehash-only mode: Skipping file sorting...")

                if args.force_rehash:
                    print("[FAST] Force rehash enabled - all files will be re-hashed regardless of changes")
                    logger.info("Force rehash enabled")

                print("[SEARCH] Hashing all destination directories...")

                hash_manager = HashManager(config, db_manager, force_rehash=args.force_rehash, verbose=getattr(args, 'verbose_hash', False))
                hash_start_time = time.time()
                hash_manager.hash_all_destination_dirs()
                hash_elapsed = time.time() - hash_start_time

                print(f"[OK] Rehashing completed in {hash_elapsed:.2f} seconds")
                logger.info("Rehash-only operation completed successfully")
                return 0

            # Normal sorting mode
            logger.info("Starting main file sorting operation")
            print("[Loop] Starting file sorting operation...")

            print("[CONFIG] Setting up component managers...")
            file_operations = FileOperations(config, db_manager)
            artist_manager = ArtistManager(config, db_manager)
            hash_manager = HashManager(config, db_manager, force_rehash=getattr(args, 'force_rehash', False), verbose=getattr(args, 'verbose_hash', False))
            file_sorter = FileSorter(config, db_manager, file_operations, artist_manager, hash_manager)

            # Execute sorting
            print("[LAUNCH] Executing file sorting workflow...")
            start_time = time.time()
            results = file_sorter.start_processing()
            sort_elapsed = time.time() - start_time

            print(f"[OK] Sorting completed in {sort_elapsed:.2f} seconds")

            # Post-processing hash operations (unless skipped)
            if not args.skip_hash:
                print("[SEARCH] Post-processing - Hashing destination directories...")
                hash_start_time = time.time()
                hash_manager.hash_all_destination_dirs()
                hash_elapsed = time.time() - hash_start_time
                print(f"[OK] Hashing completed in {hash_elapsed:.2f} seconds")

            logger.info("File sorting operation completed successfully")
            return 0

        except Exception as e:
            logger.error(f"Error during sorting operation: {e}")
            print(f"[ERROR] Error during sorting: {e}")
            return 1


class ArchiveCommand(BaseCommand):
    """Archive old files command."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--age-days',
            type=int,
            default=None,
            help='Archive files older than this many days (default: from config or 365)'
        )
        parser.add_argument(
            '--threads',
            type=int,
            default=4,
            help='Number of threads to use (default: 4)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be archived without actually moving files'
        )
        parser.add_argument(
            '--include-favorites',
            action='store_true',
            help='Include favorite directories in archiving (default: False, favorites are excluded)'
        )
        parser.add_argument(
            '--include-tag-sorted',
            action='store_true',
            help='Include tag-sorted directories in archiving (default: False, tag-sorted dirs are excluded)'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute archive operation."""
        # By default, exclude favorites (inverse of the flag)
        exclude_favorites = not args.include_favorites

        # Handle tag-sorted exclusion: command-line flag overrides config
        if args.include_tag_sorted:
            # User explicitly wants to include tag-sorted directories
            exclude_tag_sorted = False
        else:
            # Use config setting (default: True)
            archive_options = config.config.get('archive_options', {})
            exclude_tag_sorted = archive_options.get('exclude_tag_sorted', True)

        # Temporarily override config to pass setting to ArchiveManager
        original_exclude_setting = config.config.get('archive_options', {}).get('exclude_tag_sorted')
        if 'archive_options' not in config.config:
            config.config['archive_options'] = {}
        config.config['archive_options']['exclude_tag_sorted'] = exclude_tag_sorted

        # Get age_days from command line, config, or default
        if args.age_days is not None:
            age_days = args.age_days
        else:
            # Read from config archive_options.age_days, default to 365
            archive_options = config.config.get('archive_options', {})
            age_days = archive_options.get('age_days', 365)

        logger.info(f"Starting archive operation (age: {age_days} days, exclude_favorites: {exclude_favorites})")
        logger.info(f"Starting archive operation (age: {age_days} days, exclude_favorites: {exclude_favorites}, exclude_tag_sorted: {exclude_tag_sorted})")
        print(f"[PACKAGE] Starting archive operation for files older than {age_days} days...")

        if exclude_favorites:
            print("[PROTECTED]  Favorites directories will be EXCLUDED from archiving")
        else:
            print("[WARNING]  WARNING: Favorites directories will be INCLUDED in archiving")

        if exclude_tag_sorted:
            print("[PROTECTED]  Tag-sorted directories will be EXCLUDED from archiving")
        else:
            print("[WARNING]  WARNING: Tag-sorted directories will be INCLUDED in archiving")

        try:
            # Import legacy components
            from fff.sorter.handlers import ArchiveManager, DatabaseManager

            # Initialize components
            print("[FOLDER] Initializing database connection...")
            db_manager = DatabaseManager(config)
            archive_manager = ArchiveManager(config, db_manager)

            if args.dry_run:
                print("[SEARCH] Running in dry-run mode - no files will be moved")
                logger.info("Dry-run mode enabled")

                # Get files that would be archived
                from fff.sorter.handlers.utils import ensure_directory_exists
                from fff.sorter.handlers.archive_manager import format_size

                def get_archive_path(archive_config, content_type):
                    """Helper to get archive path from simple or expanded format."""
                    if not archive_config:
                        return None
                    if isinstance(archive_config, str):
                        return archive_config
                    if isinstance(archive_config, dict):
                        return archive_config.get(content_type)
                    return None

                total_files = 0
                total_size = 0

                # Check SFW directories
                if 'archive_directories' in config.config:
                    sfw_config = (
                        config.config['archive_directories'].get('sfw')
                        or config.config['archive_directories'].get('safe')
                    )

                    if sfw_config:
                        # Check SFW images
                        sfw_image_dir = config.destination_directories.get('sfw_image')
                        archive_sfw_images = get_archive_path(sfw_config, 'images')
                        if sfw_image_dir and archive_sfw_images:
                            print(f"\n[FOLDER] Checking SFW Images: {sfw_image_dir}")
                            files = archive_manager.get_files_to_archive(
                                sfw_image_dir, age_days, exclude_favorites=exclude_favorites
                            )
                            size = archive_manager.calculate_total_size(files)
                            print(f"   Would archive {len(files)} files ({format_size(size)}) to {archive_sfw_images}")
                            total_files += len(files)
                            total_size += size

                        # Check SFW videos
                        sfw_video_dir = config.destination_directories.get('sfw_video')
                        archive_sfw_videos = get_archive_path(sfw_config, 'videos')
                        if sfw_video_dir and archive_sfw_videos:
                            print(f"\n[FOLDER] Checking SFW Videos: {sfw_video_dir}")
                            files = archive_manager.get_files_to_archive(
                                sfw_video_dir, age_days, exclude_favorites=exclude_favorites
                            )
                            size = archive_manager.calculate_total_size(files)
                            print(f"   Would archive {len(files)} files ({format_size(size)}) to {archive_sfw_videos}")
                            total_files += len(files)
                            total_size += size

                        # Check SFW gifs
                        sfw_gif_dir = config.destination_directories.get('sfw_gif')
                        archive_sfw_gifs = get_archive_path(sfw_config, 'gifs')
                        if sfw_gif_dir and archive_sfw_gifs:
                            print(f"\n[FOLDER] Checking SFW GIFs: {sfw_gif_dir}")
                            files = archive_manager.get_files_to_archive(
                                sfw_gif_dir, age_days, exclude_favorites=exclude_favorites
                            )
                            size = archive_manager.calculate_total_size(files)
                            print(f"   Would archive {len(files)} files ({format_size(size)}) to {archive_sfw_gifs}")
                            total_files += len(files)
                            total_size += size

                    # Check NSFW directories
                    nsfw_config = config.config['archive_directories'].get('explicit')

                    if nsfw_config:
                        # Check NSFW images
                        nsfw_image_dir = config.destination_directories.get('nsfw_image')
                        archive_nsfw_images = get_archive_path(nsfw_config, 'images')
                        if nsfw_image_dir and archive_nsfw_images:
                            print(f"\n[FOLDER] Checking NSFW Images: {nsfw_image_dir}")
                            files = archive_manager.get_files_to_archive(
                                nsfw_image_dir, age_days, exclude_favorites=exclude_favorites
                            )
                            size = archive_manager.calculate_total_size(files)
                            print(f"   Would archive {len(files)} files ({format_size(size)}) to {archive_nsfw_images}")
                            total_files += len(files)
                            total_size += size

                        # Check NSFW videos
                        nsfw_video_dir = config.destination_directories.get('nsfw_video')
                        archive_nsfw_videos = get_archive_path(nsfw_config, 'videos')
                        if nsfw_video_dir and archive_nsfw_videos:
                            print(f"\n[FOLDER] Checking NSFW Videos: {nsfw_video_dir}")
                            files = archive_manager.get_files_to_archive(
                                nsfw_video_dir, age_days, exclude_favorites=exclude_favorites
                            )
                            size = archive_manager.calculate_total_size(files)
                            print(f"   Would archive {len(files)} files ({format_size(size)}) to {archive_nsfw_videos}")
                            total_files += len(files)
                            total_size += size

                        # Check NSFW gifs
                        nsfw_gif_dir = config.destination_directories.get('nsfw_gif')
                        archive_nsfw_gifs = get_archive_path(nsfw_config, 'gifs')
                        if nsfw_gif_dir and archive_nsfw_gifs:
                            print(f"\n[FOLDER] Checking NSFW GIFs: {nsfw_gif_dir}")
                            files = archive_manager.get_files_to_archive(
                                nsfw_gif_dir, age_days, exclude_favorites=exclude_favorites
                            )
                            size = archive_manager.calculate_total_size(files)
                            print(f"   Would archive {len(files)} files ({format_size(size)}) to {archive_nsfw_gifs}")
                            total_files += len(files)
                            total_size += size

                print(f"\n[CHART] Total Summary:")
                print(f"   Files: {total_files}")
                print(f"   Total Size: {format_size(total_size)}")
                print("\n[OK] Dry-run completed - no files were moved")
            else:
                # Execute actual archiving
                from fff.sorter.handlers.archive_manager import format_size

                print("[LAUNCH] Executing archive operation...")
                results = archive_manager.archive_by_content_type(
                    age_days=age_days,
                    num_threads=args.threads,
                    exclude_favorites=exclude_favorites
                )

                print("\n[CHART] Archive Results:")
                total_archived = 0
                total_failed = 0
                total_size = 0

                for content_type, result in results.items():
                    print(f"  {content_type}:")
                    print(f"    [OK] Archived: {result['archived']} files")
                    if result.get('total_size', 0) > 0:
                        print(f"    [DATA] Size: {format_size(result['total_size'])}")
                    print(f"    [ERROR] Failed: {result['failed']} files")
                    total_archived += result['archived']
                    total_failed += result['failed']
                    total_size += result.get('total_size', 0)

                print(f"\n[CHART] Total Summary:")
                print(f"   [OK] Files Archived: {total_archived}")
                print(f"   [DATA] Total Size: {format_size(total_size)}")
                print(f"   [ERROR] Files Failed: {total_failed}")
                print("\n[OK] Archive operation completed successfully")

            logger.info("Archive operation completed")

            # Restore original config setting
            if original_exclude_setting is not None:
                config.config['archive_options']['exclude_tag_sorted'] = original_exclude_setting
            return 0

        except Exception as e:
            # Restore original config setting on error too
            if original_exclude_setting is not None:
                config.config['archive_options']['exclude_tag_sorted'] = original_exclude_setting
            logger.error(f"Error during archive operation: {e}")
            print(f"[ERROR] Error during archive: {e}")
            import traceback
            traceback.print_exc()
            return 1


class UnarchiveCommand(BaseCommand):
    """Restore recent files from archive back to sorting directory."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--age-days',
            type=int,
            default=None,
            help='Restore files newer than this many days (default: from config or 365)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be restored without actually moving files'
        )
        parser.add_argument(
            '--target-dir',
            type=str,
            default=None,
            help='Target directory for restored files (default: from config source_directories)'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute unarchive operation to restore recent files from archives."""
        import os
        import shutil
        import datetime
        from fff.sorter.handlers import DatabaseManager
        from fff.sorter.handlers.utils import ensure_directory_exists
        from fff.sorter.handlers.archive_manager import format_size

        # Get age_days from command line, config, or default
        if args.age_days is not None:
            age_days = args.age_days
        else:
            archive_options = config.config.get('archive_options', {})
            age_days = archive_options.get('age_days', 365)

        # Get target directory from command line or config
        if args.target_dir:
            target_dir = args.target_dir
        else:
            # Get first source directory from config
            source_dirs = config.config.get('SortMobileDownloadFolder', {}).get('source_directories', '')
            source_list = [d.strip() for d in source_dirs.splitlines() if d.strip()]
            if source_list:
                target_dir = source_list[0]
            else:
                print("[ERROR] No target directory specified and none found in config")
                return 1

        logger.info(f"Starting unarchive operation (age: {age_days} days, target: {target_dir})")
        print(f"[PACKAGE] Restoring files from archives (newer than {age_days} days)")
        print(f"[FOLDER] Target directory: {target_dir}")

        if args.dry_run:
            print("[SEARCH] Running in dry-run mode - no files will be moved")

        try:
            # Ensure target directory exists
            ensure_directory_exists(target_dir)

            # Initialize database manager
            db_manager = DatabaseManager(config)

            def get_archive_directories():
                """Get all archive directory paths from config."""
                archive_dirs = []
                archive_config = config.config.get('archive_directories', {})

                for archive_type, archive_value in archive_config.items():
                    if not archive_value:
                        continue

                    # Simple format (string)
                    if isinstance(archive_value, str):
                        archive_dirs.append({
                            'path': archive_value,
                            'type': archive_type,
                            'media': 'all'
                        })
                    # Expanded format (dict)
                    elif isinstance(archive_value, dict):
                        for media_type, path in archive_value.items():
                            if path:
                                archive_dirs.append({
                                    'path': path,
                                    'type': archive_type,
                                    'media': media_type
                                })

                return archive_dirs

            def is_file_recent_enough(file_path, age_days):
                """Check if file is newer than specified age."""
                try:
                    file_mtime = os.path.getmtime(file_path)
                    file_date = datetime.datetime.fromtimestamp(file_mtime)
                    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=age_days)
                    return file_date >= cutoff_date
                except Exception as e:
                    logger.error(f"Error checking file age for {file_path}: {e}")
                    return False

            def get_recent_files(archive_path, age_days):
                """Get all files in archive newer than age_days."""
                recent_files = []
                if not os.path.exists(archive_path):
                    return recent_files

                for root, dirs, files in os.walk(archive_path):
                    for filename in files:
                        file_path = os.path.join(root, filename)
                        if is_file_recent_enough(file_path, age_days):
                            recent_files.append(file_path)

                return recent_files

            # Collect all recent files from all archives
            all_archive_dirs = get_archive_directories()
            total_files = 0
            total_size = 0
            files_by_archive = {}

            print("\n[SEARCH] Scanning archives for recent files...")
            for archive_info in all_archive_dirs:
                archive_path = archive_info['path']
                if not os.path.exists(archive_path):
                    continue

                recent_files = get_recent_files(archive_path, age_days)
                if recent_files:
                    files_by_archive[archive_path] = recent_files
                    file_sizes = sum(os.path.getsize(f) for f in recent_files if os.path.exists(f))
                    total_files += len(recent_files)
                    total_size += file_sizes
                    print(f"   [FOLDER] {archive_info['type']}/{archive_info['media']}: {len(recent_files)} files ({format_size(file_sizes)})")

            if total_files == 0:
                print(f"\n[INFO] No files found newer than {age_days} days in archives")
                return 0

            print(f"\n[CHART] Total: {total_files} files ({format_size(total_size)})")

            if args.dry_run:
                print(f"\n[INFO] Would restore {total_files} files to {target_dir}")
                print("[OK] Dry-run completed - no files were moved")
                return 0

            # Actually move files
            print(f"\n[LAUNCH] Restoring files to {target_dir}...")
            moved_count = 0
            failed_count = 0

            for archive_path, files in files_by_archive.items():
                for file_path in files:
                    try:
                        filename = os.path.basename(file_path)
                        target_path = os.path.join(target_dir, filename)

                        # Handle filename conflicts
                        if os.path.exists(target_path):
                            base, ext = os.path.splitext(target_path)
                            counter = 1
                            while os.path.exists(f"{base}_{counter}{ext}"):
                                counter += 1
                            target_path = f"{base}_{counter}{ext}"

                        # Move file
                        shutil.move(file_path, target_path)
                        moved_count += 1

                        # Update database
                        # Remove from archive table
                        archive_table = db_manager.table_name_for_dest(archive_path)
                        if archive_table:
                            db_manager.remove_file_from_table(archive_table, filename)

                        logger.debug(f"Restored {filename} from archive to {target_dir}")

                    except Exception as e:
                        logger.error(f"Failed to restore {file_path}: {e}")
                        failed_count += 1

            print(f"\n[CHART] Restore Results:")
            print(f"   [OK] Restored: {moved_count} files")
            print(f"   [ERROR] Failed: {failed_count} files")
            print(f"\n[OK] Unarchive operation completed")

            logger.info(f"Unarchive completed - restored {moved_count} files, {failed_count} failed")
            return 0

        except Exception as e:
            logger.error(f"Error during unarchive operation: {e}")
            print(f"[ERROR] Error during unarchive: {e}")
            import traceback
            traceback.print_exc()
            return 1


class StatusCommand(BaseCommand):
    """Status and statistics command."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--detailed',
            action='store_true',
            help='Show detailed statistics'
        )
        parser.add_argument(
            '--database',
            action='store_true',
            help='Include database statistics'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute status command."""
        logger.info("Generating status report")
        print("[CHART] Generating system status report...")

        try:
            cls._show_basic_status(config)

            if args.detailed:
                cls._show_detailed_status(config)

            if args.database:
                cls._show_database_status(config)

            return 0

        except Exception as e:
            logger.error(f"Error generating status: {e}")
            print(f"[ERROR] Error generating status: {e}")
            return 1

    @classmethod
    def _show_basic_status(cls, config):
        """Show basic status information."""
        print("\n" + "=" * 50)
        print("[FOLDER] DIRECTORY STATUS")
        print("=" * 50)

        # Check source directories
        print("\n[SEARCH] Source Directories:")
        for src_dir in config.source_directories:
            if os.path.exists(src_dir):
                file_count = len([f for f in os.listdir(src_dir)
                                if os.path.isfile(os.path.join(src_dir, f))])
                print(f"  [OK] {src_dir} ({file_count} files)")
            else:
                print(f"  [ERROR] {src_dir} (not found)")

        # Check destination directories
        print("\n[FOLDER] Destination Directories:")
        for key, dest_dir in config.destination_directories.items():
            if os.path.exists(dest_dir):
                file_count = len([f for f in os.listdir(dest_dir)
                                if os.path.isfile(os.path.join(dest_dir, f))])
                print(f"  [OK] {key}: {dest_dir} ({file_count} files)")
            else:
                print(f"  [ERROR] {key}: {dest_dir} (not found)")

    @classmethod
    def _show_detailed_status(cls, config):
        """Show detailed statistics."""
        print("\n" + "=" * 50)
        print("[STATS] DETAILED STATISTICS")
        print("=" * 50)
        # TODO: Implement detailed statistics
        print("[WARNING]  Detailed statistics will be implemented in next phase")

    @classmethod
    def _show_database_status(cls, config):
        """Show database statistics."""
        print("\n" + "=" * 50)
        print("[STORAGE]  DATABASE STATUS")
        print("=" * 50)
        # TODO: Implement database statistics
        print("[WARNING]  Database statistics will be implemented in next phase")


class MaintenanceCommand(BaseCommand):
    """Maintenance tasks command."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--cleanup-logs',
            action='store_true',
            help='Clean up old log files'
        )
        parser.add_argument(
            '--log-age-days',
            type=int,
            default=30,
            help='Remove log files older than this many days (default: 30)'
        )
        parser.add_argument(
            '--verify-database',
            action='store_true',
            help='Verify database integrity'
        )
        parser.add_argument(
            '--fix-database',
            action='store_true',
            help='Fix database consistency issues (removes orphaned database entries)'
        )
        parser.add_argument(
            '--verify-and-fix',
            action='store_true',
            help='Verify database integrity and fix issues if found'
        )
        parser.add_argument(
            '--reset-database',
            action='store_true',
            help='⚠️  DANGER: Wipe all data from database tables and reset auto-increment IDs'
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            help='Skip confirmation prompts (use with --reset-database)'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Run all maintenance tasks'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute maintenance tasks."""
        logger.info("Starting maintenance tasks")
        print("[CONFIG] Starting maintenance tasks...")

        tasks_run = 0

        try:
            # Handle database reset separately (requires explicit flag, not included in --all)
            if args.reset_database:
                confirm_reset = args.yes
                if not confirm_reset:
                    print("\n⚠️  [DANGER] DATABASE RESET WARNING ⚠️")
                    print("This will permanently DELETE ALL DATA from:")
                    print("  • All destination tables (nsfw_image, nsfw_video, sfw_image, etc.)")
                    print("  • All archive tables")
                    print("  • Special tables (favorites, manual_sort_images, manual_sort_videos)")
                    print("  • Tracking tables (file_operations, possible_missing_files)")
                    print("  • Other directory tracking (other_directories)")
                    print("  • All auto-increment IDs will be reset to 1")
                    print("\n❌ THIS ACTION CANNOT BE UNDONE!")
                    print("Files on disk will NOT be deleted, only database records.\n")
                    
                    response = input("Type 'RESET DATABASE' to confirm: ").strip()
                    confirm_reset = (response == 'RESET DATABASE')
                
                if confirm_reset:
                    if cls._reset_database(config):
                        tasks_run += 1
                        print("\n[OK] Database has been completely reset.")
                    else:
                        print("\n[ERROR] Database reset failed. Check logs for details.")
                        return 1
                else:
                    print("[INFO] Database reset cancelled.")
                    return 0

            if args.cleanup_logs or args.all:
                tasks_run += cls._cleanup_logs(config, args.log_age_days)

            if args.verify_database or args.all:
                cls._verify_database(config)  # Returns issue status; task counting is handled by the caller.
                tasks_run += 1

            if args.fix_database or args.all:
                tasks_run += cls._fix_database_issues(config)

            if args.verify_and_fix:
                print("[Clipboard] Running combined verify and fix operation...")
                cls._verify_database(config)  # Returns issue status; task counting is handled by the caller.
                tasks_run += 1
                tasks_run += cls._fix_database_issues(config)

            if tasks_run == 0:
                print("[WARNING]  No maintenance tasks specified. Use --help for options.")
                return 1

            print(f"[OK] Maintenance completed. {tasks_run} tasks executed.")
            logger.info(f"Maintenance completed. {tasks_run} tasks executed.")
            return 0

        except Exception as e:
            logger.error(f"Error during maintenance: {e}")
            print(f"[ERROR] Error during maintenance: {e}")
            return 1

    @classmethod
    def _cleanup_logs(cls, config, age_days):
        """Clean up old log files."""
        print(f"🧹 Cleaning up log files older than {age_days} days...")

        from fff.sorter.logging_setup import cleanup_old_logs
        deleted_count = cleanup_old_logs(config.log_directory, age_days)

        print(f"  [OK] Deleted {deleted_count} old log files")
        return 1

    @classmethod
    def _reset_database(cls, config):
        """
        Reset database by wiping all data and resetting auto-increment IDs.
        
        ⚠️ DANGER: This is a destructive operation!
        """
        print("\n🔥 Starting database reset...")
        
        try:
            from fff.sorter.handlers.database_manager import DatabaseManager
            db_manager = DatabaseManager(config)
            
            # Get all tables to reset
            destination_tables = list(config.destination_directories.keys())
            archive_tables = db_manager.get_all_table_names(include_archives=True)
            # Filter to only archive tables
            archive_tables = [t for t in archive_tables if t.startswith('archive_')]
            
            special_tables = ['favorites', 'manual_sort_images', 'manual_sort_videos']
            tracking_tables = ['file_operations', 'possible_missing_files']
            other_tables = ['other_directories']  # For other_directories_to_hash
            
            all_tables = destination_tables + archive_tables + special_tables + tracking_tables + other_tables
            
            total_records_deleted = 0
            tables_reset = 0
            
            print(f"\n📋 Found {len(all_tables)} tables to reset:")
            print(f"   • {len(destination_tables)} destination tables")
            print(f"   • {len(archive_tables)} archive tables")
            print(f"   • {len(special_tables)} special tables")
            print(f"   • {len(tracking_tables)} tracking tables")
            print(f"   • {len(other_tables)} other directory tracking table\n")
            
            with db_manager.get_connection_context() as conn:
                cursor = conn.cursor()
                
                for table_name in all_tables:
                    try:
                        # Check if table exists first
                        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
                        if not cursor.fetchone():
                            print(f"   [SKIP] Table '{table_name}' does not exist")
                            continue
                        
                        # Get record count before deletion
                        cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
                        record_count = cursor.fetchone()[0]
                        
                        if record_count == 0:
                            print(f"   [EMPTY] '{table_name}' (already empty)")
                        else:
                            # Truncate table (faster than DELETE and resets auto-increment)
                            cursor.execute(f"TRUNCATE TABLE `{table_name}`")
                            conn.commit()
                            
                            total_records_deleted += record_count
                            tables_reset += 1
                            print(f"   [RESET] '{table_name}' ({record_count:,} records deleted)")
                    
                    except Exception as e:
                        logger.error(f"Error resetting table {table_name}: {e}")
                        print(f"   [ERROR] Failed to reset '{table_name}': {e}")
                        continue
                
                cursor.close()
            
            print(f"\n✅ Database reset complete!")
            print(f"   • {tables_reset} tables were reset")
            print(f"   • {total_records_deleted:,} total records deleted")
            print(f"   • All auto-increment IDs reset to 1\n")
            
            logger.info(f"Database reset: {tables_reset} tables, {total_records_deleted} records deleted")
            return True
            
        except Exception as e:
            logger.error(f"Database reset failed: {e}")
            print(f"\n[ERROR] Database reset failed: {e}")
            return False

    @classmethod
    def _verify_database(cls, config):
        """Verify database integrity."""
        print("[SEARCH] Verifying database integrity...")

        try:
            # Import database manager
            from fff.sorter.handlers.database_manager import DatabaseManager
            # The unused secondary database adapter is not part of this application.

            # Initialize database connection
            db_manager = DatabaseManager(config)

            # Run verification checks
            issues_found = 0

            # 1. Test database connectivity
            print("  [BROADCAST] Testing database connectivity...")
            connectivity_ok = cls._check_database_connectivity(db_manager)
            if not connectivity_ok:
                issues_found += 1

            # 2. Verify table existence and schema
            print("  [Clipboard] Verifying table schemas...")
            schema_issues = cls._verify_table_schemas(db_manager, config)
            issues_found += schema_issues

            # 3. Check data consistency
            print("  [Loop] Checking data consistency...")
            consistency_issues = cls._verify_data_consistency(db_manager, config)
            issues_found += consistency_issues

            # 4. Check index integrity
            print("  [INDEX]  Verifying database indexes...")
            index_issues = cls._verify_database_indexes(db_manager, config)
            issues_found += index_issues

            # 5. Display statistics
            print("  [CHART] Gathering database statistics...")
            cls._display_database_statistics(db_manager, config)

            # Summary
            if issues_found == 0:
                print("  [OK] Database verification completed successfully - no issues found!")
            else:
                print(f"  [WARNING]  Database verification completed with {issues_found} issue(s) found")
                print("     Check the details above for specific problems that need attention")

            return 0 if issues_found == 0 else 1

        except Exception as e:
            logger.error(f"Database verification failed: {e}")
            print(f"  [ERROR] Database verification failed: {e}")
            return 1

    @classmethod
    def _check_database_connectivity(cls, db_manager):
        """Test basic database connectivity."""
        try:
            with db_manager.get_connection_context() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                cursor.close()

                if result and result[0] == 1:
                    print("     [OK] Database connection successful")
                    return True
                else:
                    print("     [ERROR] Database connection test failed")
                    return False

        except Exception as e:
            print(f"     [ERROR] Database connection failed: {e}")
            return False

    @classmethod
    def _verify_table_schemas(cls, db_manager, config):
        """Verify that all required tables exist with correct schemas."""
        issues_found = 0

        try:
            expected_tables = set(config.destination_directories.keys())

            with db_manager.get_connection_context() as conn:
                cursor = conn.cursor()

                # Get list of existing tables
                cursor.execute("SHOW TABLES")
                existing_tables = {row[0] for row in cursor.fetchall()}

                # Check for missing tables
                missing_tables = expected_tables - existing_tables
                if missing_tables:
                    print(f"     [ERROR] Missing tables: {', '.join(missing_tables)}")
                    issues_found += len(missing_tables)
                else:
                    print(f"     [OK] All {len(expected_tables)} expected tables exist")

                # Check schema for existing tables
                required_columns = {
                    'file_name': 'VARCHAR',
                    'perceptual_hash_dhash': 'VARCHAR',
                    'perceptual_hash_phash': 'VARCHAR',
                    'perceptual_hash_average': 'VARCHAR',
                    'perceptual_hash_whash': 'VARCHAR',
                    'md5_hash': 'VARCHAR',
                    'hash_method': 'VARCHAR',
                    'hash_type': 'VARCHAR',
                    'file_size': 'BIGINT',
                    'file_path': 'TEXT'
                }

                schema_problems = 0
                for table in existing_tables & expected_tables:
                    cursor.execute(f"DESCRIBE `{table}`")
                    columns = {row[0]: row[1] for row in cursor.fetchall()}

                    for col_name, col_type in required_columns.items():
                        if col_name not in columns:
                            print(f"     [ERROR] Table {table} missing column: {col_name}")
                            schema_problems += 1
                        else:
                            actual_type = columns[col_name].upper()
                            expected_type = col_type.upper()

                            # Special handling for TEXT types (accepts TEXT, MEDIUMTEXT, LONGTEXT)
                            if expected_type == 'TEXT':
                                if 'TEXT' not in actual_type:
                                    print(f"     [WARNING]  Table {table} column {col_name} has type {columns[col_name]}, expected TEXT variant")
                            # For other types, check if expected is in actual
                            elif expected_type not in actual_type:
                                print(f"     [WARNING]  Table {table} column {col_name} has type {columns[col_name]}, expected {col_type}")

                if schema_problems == 0:
                    print(f"     [OK] All table schemas are valid")
                else:
                    issues_found += schema_problems

                cursor.close()

        except Exception as e:
            print(f"     [ERROR] Schema verification error: {e}")
            issues_found += 1

        return issues_found

    @classmethod
    def _verify_data_consistency(cls, db_manager, config):
        """Check for data consistency issues."""
        issues_found = 0

        try:
            for dest_key, dest_path in config.destination_directories.items():
                if not os.path.exists(dest_path):
                    print(f"     [WARNING]  Destination directory missing: {dest_path}")
                    continue

                # Get files from filesystem
                try:
                    # Build case-insensitive lookup: lowercase path -> actual path
                    fs_files_case_map = {}
                    for root, dirs, files in os.walk(dest_path):
                        for file in files:
                            # Ignore system files, temp files, and text/doc files
                            if (not file.startswith('.') and
                                not file.endswith('.tmp') and
                                not file.endswith('.ini') and
                                not file.endswith('.txt') and
                                not file.endswith('.md')):
                                full_path = os.path.join(root, file)
                                normalized_path = os.path.normpath(full_path).replace('/', '\\')
                                fs_files_case_map[normalized_path.lower()] = normalized_path

                    # Get files from database and check for case mismatches
                    db_files_dict = {}  # Map path to filename for reporting
                    missing_files = []
                    case_mismatches = []  # (old_path, new_path, file_name)

                    with db_manager.get_connection_context() as conn:
                        cursor = conn.cursor()

                        # Check if table exists
                        cursor.execute("SHOW TABLES LIKE %s", (dest_key,))
                        if cursor.fetchone():
                            cursor.execute(f"SELECT file_path, file_name FROM `{dest_key}`")
                            for file_path, file_name in cursor.fetchall():
                                normalized_path = os.path.normpath(file_path).replace('/', '\\')
                                normalized_lower = normalized_path.lower()

                                # Check for exact match first
                                if os.path.exists(normalized_path):
                                    db_files_dict[normalized_path] = file_name
                                # Check for case-insensitive match
                                elif normalized_lower in fs_files_case_map:
                                    actual_path = fs_files_case_map[normalized_lower]
                                    case_mismatches.append((normalized_path, actual_path, file_name))
                                    db_files_dict[actual_path] = file_name  # Use actual case
                                else:
                                    # File doesn't exist at all
                                    missing_files.append(file_name)

                        # Update database paths for case mismatches
                        if case_mismatches:
                            print(f"     [UPDATE]  {dest_key}: Fixing {len(case_mismatches)} path case mismatches...")
                            for old_path, new_path, file_name in case_mismatches:
                                try:
                                    cursor.execute(
                                        f"UPDATE `{dest_key}` SET file_path = %s WHERE file_name = %s",
                                        (new_path, file_name)
                                    )
                                except Exception as update_err:
                                    print(f"        [ERROR] Failed to update path for {file_name}: {update_err}")
                            conn.commit()
                            print(f"     [OK] {dest_key}: Updated {len(case_mismatches)} paths to match filesystem case")

                        cursor.close()

                    # Compare sets using lowercase paths for case-insensitive comparison
                    fs_files_lower = set(fs_files_case_map.keys())  # lowercase paths
                    db_files_lower = {os.path.normpath(p).replace('/', '\\').lower() for p in db_files_dict.keys()}

                    missing_from_db_lower = fs_files_lower - db_files_lower
                    missing_from_db = [fs_files_case_map[p] for p in missing_from_db_lower]
                    missing_from_fs = len(missing_files)

                    if missing_from_db:
                        print(f"     [WARNING]  {dest_key}: {len(missing_from_db)} files in filesystem not in database")
                        # Show first 10 files as examples (extract just filename from full path)
                        missing_paths = sorted(missing_from_db)[:10]
                        for file_path in missing_paths:
                            filename = os.path.basename(file_path)
                            print(f"        * {filename}")
                        if len(missing_from_db) > 10:
                            print(f"        ... and {len(missing_from_db) - 10} more")
                        issues_found += len(missing_from_db)

                    if missing_from_fs > 0:
                        print(f"     [ERROR] {dest_key}: {missing_from_fs} database entries for missing files")
                        # Show first 10 missing files as examples
                        missing_list = sorted(missing_files)[:10]
                        for filename in missing_list:
                            print(f"        * {filename}")
                        if len(missing_files) > 10:
                            print(f"        ... and {len(missing_files) - 10} more")
                        issues_found += missing_from_fs

                    if not missing_from_db and missing_from_fs == 0 and db_files_dict:
                        print(f"     [OK] {dest_key}: {len(db_files_dict)} files in sync")
                    elif not db_files_dict and not fs_files_case_map:
                        print(f"     [INFO] {dest_key}: Empty (no files)")

                except Exception as e:
                    print(f"     [ERROR] Error checking {dest_key}: {e}")
                    issues_found += 1

            # Also check favorites table
            if hasattr(config, 'fav_directory') and config.fav_directory:
                fav_path = config.fav_directory
                if os.path.exists(fav_path):
                    try:
                        # Build case-insensitive lookup: lowercase path -> actual path
                        fs_files_case_map = {}
                        for root, dirs, files in os.walk(fav_path):
                            for file in files:
                                # Ignore system files, temp files, and text/doc files
                                if (not file.startswith('.') and
                                    not file.endswith('.tmp') and
                                    not file.endswith('.ini') and
                                    not file.endswith('.txt') and
                                    not file.endswith('.md')):
                                    full_path = os.path.join(root, file)
                                    normalized_path = os.path.normpath(full_path).replace('/', '\\')
                                    fs_files_case_map[normalized_path.lower()] = normalized_path

                        # Get files from database and check for case mismatches
                        db_files_dict = {}
                        missing_files = []
                        case_mismatches = []  # (old_path, new_path, file_name)

                        with db_manager.get_connection_context() as conn:
                            cursor = conn.cursor()

                            # Check if favorites table exists
                            cursor.execute("SHOW TABLES LIKE 'favorites'")
                            if cursor.fetchone():
                                cursor.execute("SELECT file_path, file_name FROM `favorites`")
                                for file_path, file_name in cursor.fetchall():
                                    normalized_path = os.path.normpath(file_path).replace('/', '\\')
                                    normalized_lower = normalized_path.lower()

                                    # Check for exact match first
                                    if os.path.exists(normalized_path):
                                        db_files_dict[normalized_path] = file_name
                                    # Check for case-insensitive match
                                    elif normalized_lower in fs_files_case_map:
                                        actual_path = fs_files_case_map[normalized_lower]
                                        case_mismatches.append((normalized_path, actual_path, file_name))
                                        db_files_dict[actual_path] = file_name  # Use actual case
                                    else:
                                        # File doesn't exist at all
                                        missing_files.append(file_name)

                            # Update database paths for case mismatches
                            if case_mismatches:
                                print(f"     [UPDATE]  favorites: Fixing {len(case_mismatches)} path case mismatches...")
                                for old_path, new_path, file_name in case_mismatches:
                                    try:
                                        cursor.execute(
                                            "UPDATE `favorites` SET file_path = %s WHERE file_name = %s",
                                            (new_path, file_name)
                                        )
                                    except Exception as update_err:
                                        print(f"        [ERROR] Failed to update path for {file_name}: {update_err}")
                                conn.commit()
                                print(f"     [OK] favorites: Updated {len(case_mismatches)} paths to match filesystem case")

                            cursor.close()

                        # Compare sets using lowercase paths for case-insensitive comparison
                        fs_files_lower = set(fs_files_case_map.keys())  # lowercase paths
                        db_files_lower = {os.path.normpath(p).replace('/', '\\').lower() for p in db_files_dict.keys()}

                        missing_from_db_lower = fs_files_lower - db_files_lower
                        missing_from_db = [fs_files_case_map[p] for p in missing_from_db_lower]
                        missing_from_fs = len(missing_files)

                        if missing_from_db:
                            print(f"     [WARNING]  favorites: {len(missing_from_db)} files in filesystem not in database")
                            missing_paths = sorted(missing_from_db)[:10]
                            for file_path in missing_paths:
                                filename = os.path.basename(file_path)
                                print(f"        * {filename}")
                            if len(missing_from_db) > 10:
                                print(f"        ... and {len(missing_from_db) - 10} more")
                            issues_found += len(missing_from_db)

                        if missing_from_fs > 0:
                            print(f"     [ERROR] favorites: {missing_from_fs} database entries for missing files")
                            missing_list = sorted(missing_files)[:10]
                            for filename in missing_list:
                                print(f"        * {filename}")
                            if len(missing_files) > 10:
                                print(f"        ... and {len(missing_files) - 10} more")
                            issues_found += missing_from_fs

                        if not missing_from_db and missing_from_fs == 0 and db_files_dict:
                            print(f"     [OK] favorites: {len(db_files_dict)} files in sync")
                        elif not db_files_dict and not fs_files_case_map:
                            print(f"     [INFO] favorites: Empty (no files)")

                    except Exception as e:
                        print(f"     [ERROR] Error checking favorites: {e}")
                        issues_found += 1

            # Also check manual sort tables
            manual_sort_tables = [
                ("manual_sort_images", getattr(config, 'manual_sort_directory', None)),
                ("manual_sort_videos", getattr(config, 'manual_sort_video_directory', None))
            ]

            for table_name, table_path in manual_sort_tables:
                if table_path and os.path.exists(table_path):
                    try:
                        # Build case-insensitive lookup: lowercase path -> actual path
                        fs_files_case_map = {}
                        for root, dirs, files in os.walk(table_path):
                            for file in files:
                                # Ignore system files, temp files, and text/doc files
                                if (not file.startswith('.') and
                                    not file.endswith('.tmp') and
                                    not file.endswith('.ini') and
                                    not file.endswith('.txt') and
                                    not file.endswith('.md')):
                                    full_path = os.path.join(root, file)
                                    normalized_path = os.path.normpath(full_path).replace('/', '\\')
                                    fs_files_case_map[normalized_path.lower()] = normalized_path

                        # Get files from database and check for case mismatches
                        db_files_dict = {}
                        missing_files = []
                        case_mismatches = []  # (old_path, new_path, file_name)

                        with db_manager.get_connection_context() as conn:
                            cursor = conn.cursor()

                            # Check if table exists
                            cursor.execute("SHOW TABLES LIKE %s", (table_name,))
                            if cursor.fetchone():
                                cursor.execute(f"SELECT file_path, file_name FROM `{table_name}`")
                                for file_path, file_name in cursor.fetchall():
                                    normalized_path = os.path.normpath(file_path).replace('/', '\\')
                                    normalized_lower = normalized_path.lower()

                                    # Check for exact match first
                                    if os.path.exists(normalized_path):
                                        db_files_dict[normalized_path] = file_name
                                    # Check for case-insensitive match
                                    elif normalized_lower in fs_files_case_map:
                                        actual_path = fs_files_case_map[normalized_lower]
                                        case_mismatches.append((normalized_path, actual_path, file_name))
                                        db_files_dict[actual_path] = file_name  # Use actual case
                                    else:
                                        # File doesn't exist at all
                                        missing_files.append(file_name)

                            # Update database paths for case mismatches
                            if case_mismatches:
                                print(f"     [UPDATE]  {table_name}: Fixing {len(case_mismatches)} path case mismatches...")
                                for old_path, new_path, file_name in case_mismatches:
                                    try:
                                        cursor.execute(
                                            f"UPDATE `{table_name}` SET file_path = %s WHERE file_name = %s",
                                            (new_path, file_name)
                                        )
                                    except Exception as update_err:
                                        print(f"        [ERROR] Failed to update path for {file_name}: {update_err}")
                                conn.commit()
                                print(f"     [OK] {table_name}: Updated {len(case_mismatches)} paths to match filesystem case")

                            cursor.close()

                        # Compare sets using lowercase paths for case-insensitive comparison
                        fs_files_lower = set(fs_files_case_map.keys())  # lowercase paths
                        db_files_lower = {os.path.normpath(p).replace('/', '\\').lower() for p in db_files_dict.keys()}

                        missing_from_db_lower = fs_files_lower - db_files_lower
                        missing_from_db = [fs_files_case_map[p] for p in missing_from_db_lower]
                        missing_from_fs = len(missing_files)

                        if missing_from_db:
                            print(f"     [WARNING]  {table_name}: {len(missing_from_db)} files in filesystem not in database")
                            missing_paths = sorted(missing_from_db)[:10]
                            for file_path in missing_paths:
                                filename = os.path.basename(file_path)
                                print(f"        * {filename}")
                            if len(missing_from_db) > 10:
                                print(f"        ... and {len(missing_from_db) - 10} more")
                            issues_found += len(missing_from_db)

                        if missing_from_fs > 0:
                            print(f"     [ERROR] {table_name}: {missing_from_fs} database entries for missing files")
                            missing_list = sorted(missing_files)[:10]
                            for filename in missing_list:
                                print(f"        * {filename}")
                            if len(missing_files) > 10:
                                print(f"        ... and {len(missing_files) - 10} more")
                            issues_found += missing_from_fs

                        if not missing_from_db and missing_from_fs == 0 and db_files_dict:
                            print(f"     [OK] {table_name}: {len(db_files_dict)} files in sync")
                        elif not db_files_dict and not fs_files_case_map:
                            print(f"     [INFO] {table_name}: Empty (no files)")

                    except Exception as e:
                        print(f"     [ERROR] Error checking {table_name}: {e}")
                        issues_found += 1

            # Also check archive tables (if they exist)
            archive_tables = []
            archive_dirs = getattr(config, 'archive_directories', None) or config.config.get('archive_directories', {})
            if archive_dirs:
                for archive_key, archive_config in archive_dirs.items():
                    if not archive_config:
                        continue

                    if isinstance(archive_config, str):
                        archive_tables.append((f'archive_{archive_key}', archive_config))
                    elif isinstance(archive_config, dict):
                        for media_type, path in archive_config.items():
                            if path:
                                archive_tables.append((f'archive_{archive_key}_{media_type}', path))

                for table_name, archive_path in archive_tables:
                    if archive_path and os.path.exists(archive_path):
                        try:
                            # Build case-insensitive lookup: lowercase path -> actual path
                            fs_files_case_map = {}
                            for root, dirs, files in os.walk(archive_path):
                                for file in files:
                                    # Ignore system files, temp files, and text/doc files
                                    if (not file.startswith('.') and
                                        not file.endswith('.tmp') and
                                        not file.endswith('.ini') and
                                        not file.endswith('.txt') and
                                        not file.endswith('.md')):
                                        full_path = os.path.join(root, file)
                                        normalized_path = os.path.normpath(full_path).replace('/', '\\')
                                        fs_files_case_map[normalized_path.lower()] = normalized_path

                            # Get files from database and check for case mismatches
                            db_files_dict = {}
                            missing_files = []
                            case_mismatches = []  # (old_path, new_path, file_name)

                            with db_manager.get_connection_context() as conn:
                                cursor = conn.cursor()

                                # Check if table exists
                                cursor.execute("SHOW TABLES LIKE %s", (table_name,))
                                if cursor.fetchone():
                                    cursor.execute(f"SELECT file_path, file_name FROM `{table_name}`")
                                    for file_path, file_name in cursor.fetchall():
                                        normalized_path = os.path.normpath(file_path).replace('/', '\\')
                                        normalized_lower = normalized_path.lower()

                                        # Check for exact match first
                                        if os.path.exists(normalized_path):
                                            db_files_dict[normalized_path] = file_name
                                        # Check for case-insensitive match
                                        elif normalized_lower in fs_files_case_map:
                                            actual_path = fs_files_case_map[normalized_lower]
                                            case_mismatches.append((normalized_path, actual_path, file_name))
                                            db_files_dict[actual_path] = file_name  # Use actual case
                                        else:
                                            # File doesn't exist at all
                                            missing_files.append(file_name)

                                # Update database paths for case mismatches
                                if case_mismatches:
                                    print(f"     [UPDATE]  {table_name}: Fixing {len(case_mismatches)} path case mismatches...")
                                    for old_path, new_path, file_name in case_mismatches:
                                        try:
                                            cursor.execute(
                                                f"UPDATE `{table_name}` SET file_path = %s WHERE file_name = %s",
                                                (new_path, file_name)
                                            )
                                        except Exception as update_err:
                                            print(f"        [ERROR] Failed to update path for {file_name}: {update_err}")
                                    conn.commit()
                                    print(f"     [OK] {table_name}: Updated {len(case_mismatches)} paths to match filesystem case")

                                cursor.close()

                            # Compare sets using lowercase paths for case-insensitive comparison
                            fs_files_lower = set(fs_files_case_map.keys())  # lowercase paths
                            db_files_lower = {os.path.normpath(p).replace('/', '\\').lower() for p in db_files_dict.keys()}

                            missing_from_db_lower = fs_files_lower - db_files_lower
                            missing_from_db = [fs_files_case_map[p] for p in missing_from_db_lower]
                            missing_from_fs = len(missing_files)

                            if missing_from_db:
                                print(f"     [WARNING]  {table_name}: {len(missing_from_db)} files in filesystem not in database")
                                missing_paths = sorted(missing_from_db)[:10]
                                for file_path in missing_paths:
                                    filename = os.path.basename(file_path)
                                    print(f"        * {filename}")
                                if len(missing_from_db) > 10:
                                    print(f"        ... and {len(missing_from_db) - 10} more")
                                issues_found += len(missing_from_db)

                            if missing_from_fs > 0:
                                print(f"     [ERROR] {table_name}: {missing_from_fs} database entries for missing files")
                                missing_list = sorted(missing_files)[:10]
                                for filename in missing_list:
                                    print(f"        * {filename}")
                                if len(missing_files) > 10:
                                    print(f"        ... and {len(missing_files) - 10} more")
                                issues_found += missing_from_fs

                            if not missing_from_db and missing_from_fs == 0 and db_files_dict:
                                print(f"     [OK] {table_name}: {len(db_files_dict)} files in sync")
                            elif not db_files_dict and not fs_files_case_map:
                                print(f"     [INFO] {table_name}: Empty (no files)")

                        except Exception as e:
                            print(f"     [ERROR] Error checking {table_name}: {e}")
                            issues_found += 1

        except Exception as e:
            print(f"     [ERROR] Data consistency check error: {e}")
            issues_found += 1

        return issues_found

    @classmethod
    def _verify_database_indexes(cls, db_manager, config):
        """Verify database indexes exist and are healthy."""
        issues_found = 0

        try:
            expected_indexes = {
                'idx_phash': 'perceptual_hash_phash',
                'idx_dhash': 'perceptual_hash_dhash',
                'idx_avg_hash': 'perceptual_hash_average',
                'idx_whash': 'perceptual_hash_whash',
                'idx_md5_hash': 'md5_hash',
                'idx_hash_method': 'hash_method'
            }

            with db_manager.get_connection_context() as conn:
                cursor = conn.cursor()

                cursor.execute("SHOW TABLES")
                tables = [row[0] for row in cursor.fetchall()]

                index_problems = 0
                for table in tables:
                    if table in config.destination_directories.keys():
                        cursor.execute(f"SHOW INDEX FROM `{table}`")
                        existing_indexes = {row[2] for row in cursor.fetchall() if row[2] != 'PRIMARY'}

                        for idx_name in expected_indexes.keys():
                            if idx_name not in existing_indexes:
                                print(f"     [ERROR] Table {table} missing index: {idx_name}")
                                index_problems += 1

                if index_problems == 0:
                    print(f"     [OK] All required indexes are present")
                else:
                    issues_found += index_problems

                cursor.close()

        except Exception as e:
            print(f"     [ERROR] Index verification error: {e}")
            issues_found += 1

        return issues_found

    @classmethod
    def _display_database_statistics(cls, db_manager, config):
        """Display database statistics and health metrics."""
        try:
            with db_manager.get_connection_context() as conn:
                cursor = conn.cursor()

                # Database size
                cursor.execute(f"""
                    SELECT
                        ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 'Size (MB)'
                    FROM information_schema.tables
                    WHERE table_schema = '{db_manager.database}'
                """)
                db_size = cursor.fetchone()[0] or 0
                print(f"     [CHART] Database size: {db_size} MB")

                # Table statistics
                total_records = 0
                cursor.execute("SHOW TABLES")
                tables = [row[0] for row in cursor.fetchall()]

                for table in tables:
                    if table in config.destination_directories.keys():
                        cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                        count = cursor.fetchone()[0]
                        total_records += count

                        if count > 0:
                            # Get latest entry
                            try:
                                cursor.execute(f"SELECT MAX(file_created_at) FROM `{table}` WHERE file_created_at IS NOT NULL")
                                latest = cursor.fetchone()[0]
                                latest_str = latest.strftime('%Y-%m-%d') if latest else 'N/A'
                                print(f"     [STATS] {table}: {count:,} records (latest: {latest_str})")
                            except:
                                print(f"     [STATS] {table}: {count:,} records")

                print(f"     [CHART] Total records across all tables: {total_records:,}")

                cursor.close()

        except Exception as e:
            print(f"     [ERROR] Statistics error: {e}")

    @classmethod
    def _fix_database_issues(cls, config):
        """Fix common database consistency issues."""
        print("[CONFIG] Fixing database consistency issues...")

        try:
            from fff.sorter.handlers.database_manager import DatabaseManager

            db_manager = DatabaseManager(config)
            total_fixed = 0

            # Fix orphaned database entries (records for files that no longer exist)
            print("  🧹 Removing orphaned database entries...")

            for dest_key, dest_path in config.destination_directories.items():
                if not os.path.exists(dest_path):
                    print(f"     [WARNING]  Skipping {dest_key} - directory does not exist: {dest_path}")
                    continue

                try:
                    # Get files from database and check if they exist on filesystem
                    with db_manager.get_connection_context() as conn:
                        cursor = conn.cursor()

                        # Check if table exists
                        cursor.execute("SHOW TABLES LIKE %s", (dest_key,))
                        if not cursor.fetchone():
                            print(f"     [INFO] Table {dest_key} does not exist - skipping")
                            continue

                        # Get all database entries with their full paths
                        cursor.execute(f"SELECT file_path, file_name FROM `{dest_key}`")
                        db_entries = cursor.fetchall()

                        # Find orphaned entries and entries needing path updates
                        orphaned_entries = []
                        entries_to_update = []  # (file_name, new_path)

                        for file_path, file_name in db_entries:
                            if not file_path:
                                # No path stored - treat as orphaned for now
                                orphaned_entries.append(file_name)
                                continue

                            normalized_path = os.path.normpath(file_path).replace('/', '\\')

                            # Check if file exists at stored path
                            if os.path.exists(normalized_path):
                                # File found at expected location - no action needed
                                continue

                            # File not at stored path - search subdirectories of destination
                            found_in_subdir = False
                            found_path = None

                            try:
                                # Search recursively in destination directory and subdirectories
                                for root, dirs, files in os.walk(dest_path):
                                    if file_name in files:
                                        found_path = os.path.join(root, file_name)
                                        found_in_subdir = True
                                        break
                            except Exception as walk_err:
                                print(f"        [WARNING] Error searching for {file_name}: {walk_err}")

                            if found_in_subdir and found_path:
                                # File found in subdirectory - update path instead of removing
                                entries_to_update.append((file_name, found_path))
                            else:
                                # File truly missing - mark as orphaned
                                orphaned_entries.append(file_name)

                        # Update paths for files found in subdirectories
                        if entries_to_update:
                            print(f"     [UPDATE]  {dest_key}: Updating paths for {len(entries_to_update)} files found in subdirectories...")
                            updated_count = 0
                            deduped_count = 0
                            for file_name, new_path in entries_to_update:
                                try:
                                    normalized_new_path = os.path.normpath(new_path).replace('/', '\\')
                                    cursor.execute(
                                        f"UPDATE `{dest_key}` SET file_path = %s WHERE file_name = %s AND file_path != %s",
                                        (normalized_new_path, file_name, normalized_new_path)
                                    )
                                    updated_count += cursor.rowcount
                                except Exception as update_err:
                                    err_str = str(update_err)
                                    if '1062' in err_str or 'Duplicate entry' in err_str:
                                        # Target path already exists - stale record is redundant, remove it
                                        try:
                                            normalized_new_path = os.path.normpath(new_path).replace('/', '\\')
                                            cursor.execute(
                                                f"DELETE FROM `{dest_key}` WHERE file_name = %s AND file_path != %s",
                                                (file_name, normalized_new_path)
                                            )
                                            deduped_count += cursor.rowcount
                                        except Exception as del_err:
                                            print(f"        [ERROR] Failed to clean up stale entry for {file_name}: {del_err}")
                                    else:
                                        print(f"        [ERROR] Failed to update path for {file_name}: {update_err}")

                            conn.commit()
                            msg = f"     [OK] {dest_key}: Updated {updated_count} file paths"
                            if deduped_count:
                                msg += f", removed {deduped_count} stale duplicate entries"
                            print(msg)

                        if orphaned_entries:
                            print(f"     [DELETE]  {dest_key}: Removing {len(orphaned_entries)} orphaned entries...")

                            # Show first 10 files being removed as examples
                            orphaned_names = sorted(orphaned_entries)[:10]
                            for filename in orphaned_names:
                                print(f"        * {filename}")
                            if len(orphaned_entries) > 10:
                                print(f"        ... and {len(orphaned_entries) - 10} more")

                            # Remove orphaned entries by file_name in batches
                            batch_size = 100
                            removed_count = 0

                            for i in range(0, len(orphaned_entries), batch_size):
                                batch = orphaned_entries[i:i + batch_size]
                                placeholders = ','.join(['%s'] * len(batch))

                                cursor.execute(
                                    f"DELETE FROM `{dest_key}` WHERE file_name IN ({placeholders})",
                                    batch
                                )
                                removed_count += cursor.rowcount

                            conn.commit()  # Ensure changes are committed

                            print(f"     [OK] {dest_key}: Removed {removed_count} orphaned database entries")
                            total_fixed += removed_count
                        else:
                            print(f"     [OK] {dest_key}: No orphaned entries found")

                        cursor.close()

                except Exception as e:
                    print(f"     [ERROR] Error fixing {dest_key}: {e}")

            # Also check favorites table
            if hasattr(config, 'fav_directory') and config.fav_directory:
                fav_path = config.fav_directory
                if os.path.exists(fav_path):
                    try:
                        # Get files from database and check if they exist on filesystem
                        with db_manager.get_connection_context() as conn:
                            cursor = conn.cursor()

                            # Check if favorites table exists
                            cursor.execute("SHOW TABLES LIKE 'favorites'")
                            if not cursor.fetchone():
                                print(f"     [INFO] Table favorites does not exist - skipping")
                            else:
                                # Get all database entries with their full paths
                                cursor.execute("SELECT file_path, file_name FROM `favorites`")
                                db_entries = cursor.fetchall()

                                # Find orphaned entries and entries needing path updates
                                orphaned_entries = []
                                entries_to_update = []  # (file_name, new_path)

                                for file_path, file_name in db_entries:
                                    if not file_path:
                                        orphaned_entries.append(file_name)
                                        continue

                                    normalized_path = os.path.normpath(file_path).replace('/', '\\')

                                    if os.path.exists(normalized_path):
                                        continue

                                    # Search subdirectories
                                    found_in_subdir = False
                                    found_path = None

                                    try:
                                        for root, dirs, files in os.walk(fav_path):
                                            if file_name in files:
                                                found_path = os.path.join(root, file_name)
                                                found_in_subdir = True
                                                break
                                    except Exception as walk_err:
                                        print(f"        [WARNING] Error searching for {file_name}: {walk_err}")

                                    if found_in_subdir and found_path:
                                        entries_to_update.append((file_name, found_path))
                                    else:
                                        orphaned_entries.append(file_name)

                                # Update paths for files found in subdirectories
                                if entries_to_update:
                                    print(f"     [UPDATE]  favorites: Updating paths for {len(entries_to_update)} files found in subdirectories...")
                                    updated_count = 0
                                    deduped_count = 0
                                    for file_name, new_path in entries_to_update:
                                        try:
                                            normalized_new_path = os.path.normpath(new_path).replace('/', '\\')
                                            cursor.execute(
                                                "UPDATE `favorites` SET file_path = %s WHERE file_name = %s AND file_path != %s",
                                                (normalized_new_path, file_name, normalized_new_path)
                                            )
                                            updated_count += cursor.rowcount
                                        except Exception as update_err:
                                            err_str = str(update_err)
                                            if '1062' in err_str or 'Duplicate entry' in err_str:
                                                try:
                                                    normalized_new_path = os.path.normpath(new_path).replace('/', '\\')
                                                    cursor.execute(
                                                        "DELETE FROM `favorites` WHERE file_name = %s AND file_path != %s",
                                                        (file_name, normalized_new_path)
                                                    )
                                                    deduped_count += cursor.rowcount
                                                except Exception as del_err:
                                                    print(f"        [ERROR] Failed to clean up stale entry for {file_name}: {del_err}")
                                            else:
                                                print(f"        [ERROR] Failed to update path for {file_name}: {update_err}")

                                    conn.commit()
                                    msg = f"     [OK] favorites: Updated {updated_count} file paths"
                                    if deduped_count:
                                        msg += f", removed {deduped_count} stale duplicate entries"
                                    print(msg)

                                if orphaned_entries:
                                    print(f"     [DELETE]  favorites: Removing {len(orphaned_entries)} orphaned entries...")

                                    # Show first 10 files being removed as examples
                                    orphaned_names = sorted(orphaned_entries)[:10]
                                    for filename in orphaned_names:
                                        print(f"        * {filename}")
                                    if len(orphaned_entries) > 10:
                                        print(f"        ... and {len(orphaned_entries) - 10} more")

                                    # Remove orphaned entries by file_name in batches
                                    batch_size = 100
                                    removed_count = 0

                                    for i in range(0, len(orphaned_entries), batch_size):
                                        batch = orphaned_entries[i:i + batch_size]
                                        placeholders = ','.join(['%s'] * len(batch))

                                        cursor.execute(
                                            f"DELETE FROM `favorites` WHERE file_name IN ({placeholders})",
                                            batch
                                        )
                                        removed_count += cursor.rowcount

                                    conn.commit()  # Ensure changes are committed

                                    print(f"     [OK] favorites: Removed {removed_count} orphaned database entries")
                                    total_fixed += removed_count
                                else:
                                    print(f"     [OK] favorites: No orphaned entries found")

                            cursor.close()

                    except Exception as e:
                        print(f"     [ERROR] Error fixing favorites: {e}")
                else:
                    print(f"     [WARNING]  Skipping favorites - directory does not exist: {fav_path}")

            # Also check manual sort tables
            manual_sort_tables = [
                ("manual_sort_images", config.manual_sort_directory),
                ("manual_sort_videos", config.manual_sort_video_directory)
            ]

            for table_name, table_path in manual_sort_tables:
                if not os.path.exists(table_path):
                    print(f"     [WARNING]  Skipping {table_name} - directory does not exist: {table_path}")
                    continue

                try:
                    # Get files from database and check if they exist on filesystem
                    with db_manager.get_connection_context() as conn:
                        cursor = conn.cursor()

                        # Check if table exists
                        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
                        if not cursor.fetchone():
                            print(f"     [INFO] Table {table_name} does not exist - skipping")
                            continue

                        # Get all database entries with their full paths
                        cursor.execute(f"SELECT file_path, file_name FROM `{table_name}`")
                        db_entries = cursor.fetchall()

                        # Find orphaned entries and entries needing path updates
                        orphaned_entries = []
                        entries_to_update = []  # (file_name, new_path)

                        for file_path, file_name in db_entries:
                            if not file_path:
                                orphaned_entries.append(file_name)
                                continue

                            normalized_path = os.path.normpath(file_path).replace('/', '\\')

                            if os.path.exists(normalized_path):
                                continue

                            # Search subdirectories
                            found_in_subdir = False
                            found_path = None

                            try:
                                for root, dirs, files in os.walk(table_path):
                                    if file_name in files:
                                        found_path = os.path.join(root, file_name)
                                        found_in_subdir = True
                                        break
                            except Exception as walk_err:
                                print(f"        [WARNING] Error searching for {file_name}: {walk_err}")

                            if found_in_subdir and found_path:
                                entries_to_update.append((file_name, found_path))
                            else:
                                orphaned_entries.append(file_name)

                        # Update paths for files found in subdirectories
                        if entries_to_update:
                            print(f"     [UPDATE]  {table_name}: Updating paths for {len(entries_to_update)} files found in subdirectories...")
                            updated_count = 0
                            deduped_count = 0
                            for file_name, new_path in entries_to_update:
                                try:
                                    normalized_new_path = os.path.normpath(new_path).replace('/', '\\')
                                    cursor.execute(
                                        f"UPDATE `{table_name}` SET file_path = %s WHERE file_name = %s AND file_path != %s",
                                        (normalized_new_path, file_name, normalized_new_path)
                                    )
                                    updated_count += cursor.rowcount
                                except Exception as update_err:
                                    err_str = str(update_err)
                                    if '1062' in err_str or 'Duplicate entry' in err_str:
                                        try:
                                            normalized_new_path = os.path.normpath(new_path).replace('/', '\\')
                                            cursor.execute(
                                                f"DELETE FROM `{table_name}` WHERE file_name = %s AND file_path != %s",
                                                (file_name, normalized_new_path)
                                            )
                                            deduped_count += cursor.rowcount
                                        except Exception as del_err:
                                            print(f"        [ERROR] Failed to clean up stale entry for {file_name}: {del_err}")
                                    else:
                                        print(f"        [ERROR] Failed to update path for {file_name}: {update_err}")

                            conn.commit()
                            msg = f"     [OK] {table_name}: Updated {updated_count} file paths"
                            if deduped_count:
                                msg += f", removed {deduped_count} stale duplicate entries"
                            print(msg)

                        if orphaned_entries:
                            print(f"     [DELETE]  {table_name}: Removing {len(orphaned_entries)} orphaned entries...")

                            # Show first 10 files being removed as examples
                            orphaned_names = sorted(orphaned_entries)[:10]
                            for filename in orphaned_names:
                                print(f"        * {filename}")
                            if len(orphaned_entries) > 10:
                                print(f"        ... and {len(orphaned_entries) - 10} more")

                            # Remove orphaned entries by file_name in batches
                            batch_size = 100
                            removed_count = 0

                            for i in range(0, len(orphaned_entries), batch_size):
                                batch = orphaned_entries[i:i + batch_size]
                                placeholders = ','.join(['%s'] * len(batch))

                                cursor.execute(
                                    f"DELETE FROM `{table_name}` WHERE file_name IN ({placeholders})",
                                    batch
                                )
                                removed_count += cursor.rowcount

                            conn.commit()  # Ensure changes are committed

                            print(f"     [OK] {table_name}: Removed {removed_count} orphaned database entries")
                            total_fixed += removed_count
                        else:
                            print(f"     [OK] {table_name}: No orphaned entries found")

                        cursor.close()

                except Exception as e:
                    print(f"     [ERROR] Error fixing {table_name}: {e}")

            # Also check archive tables
            archive_tables = []
            if hasattr(config, 'archive_directories') and config.archive_directories:
                # Add archive directories if they're configured
                # Handle both simple format (string) and expanded format (dict)
                for archive_key in ['premium', 'explicit', 'sfw']:
                    archive_config = config.archive_directories.get(archive_key)
                    if not archive_config:
                        continue

                    if isinstance(archive_config, str):
                        # Simple format: single path
                        archive_tables.append((f'archive_{archive_key}', archive_config))
                    elif isinstance(archive_config, dict):
                        # Expanded format: separate paths for images/videos/gifs
                        for media_type, path in archive_config.items():
                            if path:
                                archive_tables.append((f'archive_{archive_key}_{media_type}', path))

            for table_name, table_path in archive_tables:
                # Skip if configured to ignore or if path is None
                if not table_path:
                    print(f"     [INFO] Skipping {table_name} - not configured")
                    continue

                # Skip archive_premium if configured to ignore
                if table_name == 'archive_premium' and hasattr(config, 'should_ignore_archive_premium'):
                    if config.should_ignore_archive_premium():
                        print(f"     [INFO] Skipping {table_name} - configured to ignore")
                        continue

                if not os.path.exists(table_path):
                    print(f"     [WARNING]  Skipping {table_name} - directory does not exist: {table_path}")
                    continue

                try:
                    # Get files from database and check if they exist on filesystem
                    with db_manager.get_connection_context() as conn:
                        cursor = conn.cursor()

                        # Check if table exists
                        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
                        if not cursor.fetchone():
                            print(f"     [INFO] Table {table_name} does not exist - skipping")
                            continue

                        # Get all database entries with their full paths
                        cursor.execute(f"SELECT file_path, file_name FROM `{table_name}`")
                        db_entries = cursor.fetchall()

                        # Find orphaned entries (files that don't exist on filesystem)
                        orphaned_entries = []
                        for file_path, file_name in db_entries:
                            normalized_path = os.path.normpath(file_path).replace('/', '\\')
                            if not os.path.exists(normalized_path):
                                orphaned_entries.append(file_name)

                        if orphaned_entries:
                            print(f"     [DELETE]  {table_name}: Removing {len(orphaned_entries)} orphaned entries...")

                            # Show first 10 files being removed as examples
                            orphaned_names = sorted(orphaned_entries)[:10]
                            for filename in orphaned_names:
                                print(f"        * {filename}")
                            if len(orphaned_entries) > 10:
                                print(f"        ... and {len(orphaned_entries) - 10} more")

                            # Remove orphaned entries by file_name in batches
                            batch_size = 100
                            removed_count = 0

                            for i in range(0, len(orphaned_entries), batch_size):
                                batch = orphaned_entries[i:i + batch_size]
                                placeholders = ','.join(['%s'] * len(batch))

                                cursor.execute(
                                    f"DELETE FROM `{table_name}` WHERE file_name IN ({placeholders})",
                                    batch
                                )
                                removed_count += cursor.rowcount

                            conn.commit()  # Ensure changes are committed

                            print(f"     [OK] {table_name}: Removed {removed_count} orphaned database entries")
                            total_fixed += removed_count
                        else:
                            print(f"     [OK] {table_name}: No orphaned entries found")

                        cursor.close()

                except Exception as e:
                    print(f"     [ERROR] Error fixing {table_name}: {e}")

            # Summary
            if total_fixed > 0:
                print(f"  [OK] Database repair completed - fixed {total_fixed} issues")
            else:
                print("  [INFO] No issues found that could be automatically fixed")

            return 1

        except Exception as e:
            logger.error(f"Database repair failed: {e}")
            print(f"  [ERROR] Database repair failed: {e}")
            return 1


class PostIDCommand(BaseCommand):
    """Move or revert files based on post IDs in their names."""

    @classmethod
    def _matches_post_id_pattern(cls, filename, post_id):
        """
        Check if filename matches the expected pattern: contains rating word and ends with post ID.
        Pattern: *-(explicit|safe|questionable)-{post_id}.extension
        """
        import re

        # Remove file extension for pattern matching
        name_without_ext = os.path.splitext(filename)[0]

        # Create pattern that matches rating followed by post ID at the end
        # Example: artist-explicit-12345, site-safe-67890, etc.
        pattern = rf'-(explicit|safe|questionable)-{re.escape(post_id)}$'

        return bool(re.search(pattern, name_without_ext, re.IGNORECASE))

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--action',
            choices=['move', 'revert'],
            required=True,
            help='Action to perform: move files with post IDs or revert moved files'
        )
        parser.add_argument(
            '--ids-file',
            default=str(sorter_paths.POST_ID_LIST),  # fff: was ./post_id_list.txt
            help='File containing list of post IDs (default: data/sorter/post_id_list.txt)'
        )
        parser.add_argument(
            '--target-dir',
            help='Target directory for moving files (required for move action)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually moving files'
        )
        parser.add_argument(
            '--log-file',
            default=str(sorter_paths.MOVE_LOG),  # fff: was ./move_log.log
            help='Log file to track movements (default: data/sorter/move_log.log)'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute post ID operations."""
        if args.action == 'move':
            return cls._move_files_with_post_ids(args, config)
        elif args.action == 'revert':
            return cls._revert_moved_files(args, config)

    @classmethod
    def _move_files_with_post_ids(cls, args, config):
        """Move files that contain post IDs in their names."""
        import re
        import shutil
        from datetime import datetime

        # Validate inputs
        if not args.target_dir:
            print("[ERROR] --target-dir is required for move action")
            return 1

        if not os.path.exists(args.ids_file):
            print(f"[ERROR] Post ID file not found: {args.ids_file}")
            return 1

        # Create target directory if it doesn't exist
        if not args.dry_run:
            os.makedirs(args.target_dir, exist_ok=True)

        print(f"[SEARCH] Moving files with post IDs to: {args.target_dir}")
        print(f"[Clipboard] Using post ID list: {args.ids_file}")

        # Load post IDs
        with open(args.ids_file, 'r') as f:
            post_ids = [line.strip() for line in f if line.strip()]

        print(f"[CHART] Loaded {len(post_ids)} post IDs")

        # Search through source directories
        source_dirs = []

        # Handle the source_directories configuration from config.yaml
        if hasattr(config, 'source_directories'):
            if isinstance(config.source_directories, str):
                # Handle multi-line string format
                source_dirs.extend([dir.strip() for dir in config.source_directories.strip().split('\n') if dir.strip()])
            elif isinstance(config.source_directories, list):
                source_dirs.extend(config.source_directories)

        # Also check specific source directory configs
        if hasattr(config, 'source_directory') and config.source_directory:
            source_dirs.append(config.source_directory)

        # Add any destination directories as potential sources too
        if hasattr(config, 'destination_directories'):
            for key, path in config.destination_directories.items():
                if os.path.exists(path):
                    source_dirs.append(path)

        # Add fav directory for surface-level scanning only
        fav_dir = None
        if hasattr(config, 'fav_directory') and config.fav_directory and os.path.exists(config.fav_directory):
            fav_dir = config.fav_directory
        elif hasattr(config, 'config') and 'other' in config.config and 'fav_directory' in config.config['other']:
            if os.path.exists(config.config['other']['fav_directory']):
                fav_dir = config.config['other']['fav_directory']

        # Remove duplicates and ensure directories exist
        source_dirs = list(set([d for d in source_dirs if os.path.exists(d)]))

        print(f"[FOLDER] Source directories found: {source_dirs}")

        if not source_dirs and not fav_dir:
            print("[ERROR] No valid source directories found")
            return 1

        moved_count = 0
        move_log = []

        # Get blacklist directories from config
        blacklist_dirs = []
        if hasattr(config, 'config') and 'other' in config.config and 'post_id_blacklist_directories' in config.config['other']:
            blacklist_dirs = config.config['other']['post_id_blacklist_directories']

        print(f"[BLOCKED] Blacklist directories: {blacklist_dirs}")

        # Get absolute path of destination directory for comparison
        dest_abs_path = os.path.abspath(args.target_dir)

        # First, scan regular directories (with subdirectories)
        for source_dir in source_dirs:
            print(f"[SEARCH] Scanning: {source_dir}")
            source_abs_path = os.path.abspath(source_dir)

            # Check if destination is within this source directory
            dest_in_source = dest_abs_path.startswith(source_abs_path + os.sep) or dest_abs_path == source_abs_path

            for root, dirs, files in os.walk(source_dir):
                # Skip destination directory if it's within the source directory
                if dest_in_source and os.path.abspath(root) == dest_abs_path:
                    print(f"  [NEXT]  Skipping destination directory: {root}")
                    continue

                # Filter out blacklisted directories from dirs list to prevent os.walk from entering them
                original_dirs = dirs[:]
                dirs[:] = [d for d in dirs if not any(
                    blacklist_dir.lower() in d.lower() for blacklist_dir in blacklist_dirs
                )]

                filtered_dirs = set(original_dirs) - set(dirs)
                if filtered_dirs:
                    print(f"  [BLOCKED] Filtered out subdirectories: {filtered_dirs}")

                # Also skip if current directory matches blacklist
                current_dir_name = os.path.basename(root)
                if any(blacklist_dir.lower() in current_dir_name.lower() for blacklist_dir in blacklist_dirs):
                    print(f"  [NEXT]  Skipping blacklisted directory: {root}")
                    continue

                for file in files:
                    # Check if filename matches the specific post ID pattern
                    for post_id in post_ids:
                        if cls._matches_post_id_pattern(file, post_id):
                            src_path = os.path.join(root, file)
                            dst_path = os.path.join(args.target_dir, file)

                            # Handle duplicate names
                            counter = 1
                            original_dst = dst_path
                            while os.path.exists(dst_path):
                                name, ext = os.path.splitext(original_dst)
                                dst_path = f"{name}_{counter}{ext}"
                                counter += 1

                            if args.dry_run:
                                print(f"  [DRY RUN] Would move: {src_path} -> {dst_path}")
                            else:
                                try:
                                    shutil.move(src_path, dst_path)
                                    move_log.append({
                                        'timestamp': datetime.now().isoformat(),
                                        'post_id': post_id,
                                        'source': src_path,
                                        'destination': dst_path,
                                        'action': 'move'
                                    })
                                    print(f"  [OK] Moved: {file} (post ID: {post_id})")
                                    moved_count += 1
                                except Exception as e:
                                    print(f"  [ERROR] Error moving {file}: {e}")
                            break  # Found a matching post ID, move to next file

        # Second, scan fav directory and all subdirectories
        if fav_dir:
            print(f"[SEARCH] Scanning fav directory and subdirectories: {fav_dir}")

            # Get blacklist directories from config
            blacklist_dirs = []
            if hasattr(config, 'config') and 'other' in config.config and 'post_id_blacklist_directories' in config.config['other']:
                blacklist_dirs = config.config['other']['post_id_blacklist_directories']

            print(f"  [BLOCKED] Blacklist directories: {blacklist_dirs}")

            # Get absolute path of destination directory for comparison
            dest_abs_path = os.path.abspath(args.target_dir)
            fav_abs_path = os.path.abspath(fav_dir)

            # Check if destination is within fav directory
            dest_in_fav = dest_abs_path.startswith(fav_abs_path + os.sep) or dest_abs_path == fav_abs_path

            try:
                for root, dirs, files in os.walk(fav_dir):
                    # Skip destination directory if it's within the fav directory
                    if dest_in_fav and os.path.abspath(root) == dest_abs_path:
                        print(f"  [NEXT]  Skipping destination directory: {root}")
                        continue

                    # Filter out blacklisted directories from dirs list to prevent os.walk from entering them
                    original_dirs = dirs.copy()
                    dirs[:] = [d for d in dirs if not any(
                        blacklist_dir.lower() in d.lower() for blacklist_dir in blacklist_dirs
                    )]

                    # Debug: Show what was filtered
                    filtered_dirs = [d for d in original_dirs if d not in dirs]
                    if filtered_dirs:
                        print(f"  [BLOCKED] Filtered out subdirectories: {filtered_dirs}")

                    # Also skip if current directory matches blacklist
                    current_dir_name = os.path.basename(root)
                    if any(blacklist_dir.lower() in current_dir_name.lower() for blacklist_dir in blacklist_dirs):
                        print(f"  [NEXT]  Skipping blacklisted directory: {root}")
                        continue

                    # Skip destination directory at any level if it's within scanning path
                    if dest_in_fav and os.path.abspath(root) == dest_abs_path:
                        continue

                    print(f"  [FOLDER] Scanning: {root}")

                    for file in files:
                        # Check if filename matches the specific post ID pattern
                        for post_id in post_ids:
                            if cls._matches_post_id_pattern(file, post_id):
                                src_path = os.path.join(root, file)
                                dst_path = os.path.join(args.target_dir, file)

                                # Handle duplicate names
                                counter = 1
                                original_dst = dst_path
                                while os.path.exists(dst_path):
                                    name, ext = os.path.splitext(original_dst)
                                    dst_path = f"{name}_{counter}{ext}"
                                    counter += 1

                                if args.dry_run:
                                    print(f"    [DRY RUN] Would move: {src_path} -> {dst_path}")
                                else:
                                    try:
                                        shutil.move(src_path, dst_path)
                                        move_log.append({
                                            'timestamp': datetime.now().isoformat(),
                                            'post_id': post_id,
                                            'source': src_path,
                                            'destination': dst_path,
                                            'action': 'move'
                                        })
                                        print(f"    [OK] Moved: {file} (post ID: {post_id})")
                                        moved_count += 1
                                    except Exception as e:
                                        print(f"    [ERROR] Error moving {file}: {e}")
                                break  # Found a matching post ID, move to next file
            except Exception as e:
                print(f"  [WARNING]  Error scanning fav directory: {e}")

        # Write move log
        if not args.dry_run and move_log:
            import json
            os.makedirs(os.path.dirname(os.path.abspath(args.log_file)), exist_ok=True)  # fff: default is in data/sorter/
            with open(args.log_file, 'w') as f:
                for entry in move_log:
                    f.write(json.dumps(entry) + '\n')
            print(f"[Notes] Move log written to: {args.log_file}")

        print(f"[OK] Moved {moved_count} files {'(DRY RUN)' if args.dry_run else ''}")
        return 0

    @classmethod
    def _revert_moved_files(cls, args, config):
        """Revert files that were moved by post ID operations."""
        import json
        import shutil

        if not os.path.exists(args.log_file):
            print(f"[ERROR] Move log file not found: {args.log_file}")
            return 1

        print(f"[Loop] Reverting moves from log: {args.log_file}")

        reverted_count = 0

        with open(args.log_file, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get('action') == 'move':
                        src_path = entry['destination']  # Current location
                        dst_path = entry['source']       # Original location

                        if os.path.exists(src_path):
                            # Recreate original directory structure if needed
                            os.makedirs(os.path.dirname(dst_path), exist_ok=True)

                            if args.dry_run:
                                print(f"  [DRY RUN] Would revert: {src_path} -> {dst_path}")
                            else:
                                try:
                                    shutil.move(src_path, dst_path)
                                    print(f"  [OK] Reverted: {os.path.basename(src_path)}")
                                    reverted_count += 1
                                except Exception as e:
                                    print(f"  [ERROR] Error reverting {os.path.basename(src_path)}: {e}")
                        else:
                            print(f"  [WARNING]  File not found: {src_path}")

                except json.JSONDecodeError:
                    print(f"  [WARNING]  Invalid log entry: {line}")
                except Exception as e:
                    print(f"  [ERROR] Error processing entry: {e}")

        print(f"[OK] Reverted {reverted_count} files {'(DRY RUN)' if args.dry_run else ''}")
        return 0


class SetupCommand(BaseCommand):
    """Initial setup and configuration command."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--init',
            action='store_true',
            help='Initialize configuration from examples'
        )
        parser.add_argument(
            '--check-deps',
            action='store_true',
            help='Check dependencies'
        )
        parser.add_argument(
            '--test-db',
            action='store_true',
            help='Test database connection'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute setup tasks."""
        print("[CONFIG] FFF Setup")
        print("=" * 50)

        if args.init:
            return cls._init_config()
        elif args.check_deps:
            return cls._check_dependencies()
        elif args.test_db:
            return cls._test_database(config)
        else:
            # Interactive setup
            return cls._interactive_setup()

    @classmethod
    def _init_config(cls):
        """Initialize configuration from examples."""
        print("[Notes] Initializing configuration...")

        # Copy example files if they don't exist
        # fff: the examples are config/*.example.yaml; the targets are the fff config files
        example_files = [
            (str(fff_paths.CONFIG_DIR / 'config.example.yaml'), str(fff_paths.CONFIG_DIR / 'config.yaml')),
            (str(fff_paths.CONFIG_DIR / 'sort_artists.example.yaml'), str(sorter_paths.SORT_ARTISTS)),
            (str(fff_paths.CONFIG_DIR / 'tag_sorting.example.yaml'), str(sorter_paths.TAG_SORTING))
        ]

        for example_file, target_file in example_files:
            if os.path.exists(example_file) and not os.path.exists(target_file):
                import shutil
                shutil.copy2(example_file, target_file)
                print(f"  [OK] Created {target_file} from {example_file}")
            elif os.path.exists(target_file):
                print(f"  [WARNING]  {target_file} already exists")
            else:
                print(f"  [ERROR] {example_file} not found")

        print("\n[IDEA] Next steps:")
        print("  1. Edit config.yaml with your settings")
        print("  2. Edit artists.yaml with your favorite artists")
        print("  3. Run: python main.py setup --test-db")

        return 0

    @classmethod
    def _check_dependencies(cls):
        """Check if dependencies are installed."""
        print("[SEARCH] Checking dependencies...")

        required_packages = [
            'mysql.connector',
            'yaml',
            'colorama'
        ]

        missing = []
        for package in required_packages:
            try:
                if package == 'mysql.connector':
                    import mysql.connector
                elif package == 'yaml':
                    import yaml
                elif package == 'colorama':
                    import colorama
                print(f"  [OK] {package}")
            except ImportError:
                print(f"  [ERROR] {package}")
                missing.append(package)

        if missing:
            print(f"\n[ERROR] Missing packages: {', '.join(missing)}")
            print("[IDEA] Install with: pip install -r requirements.txt")
            return 1
        else:
            print("\n[OK] All dependencies are installed")
            return 0

    @classmethod
    def _test_database(cls, config):
        """Test database connection."""
        if not config:
            print("[ERROR] No configuration provided")
            return 1

        print("[SEARCH] Testing database connection...")

        try:
            # Use the active database manager for schema checks.
            from fff.sorter.handlers import DatabaseManager
            db_manager = DatabaseManager(config)
            print("  [OK] Database connection successful")
            return 0
        except Exception as e:
            print(f"  [ERROR] Database connection failed: {e}")
            return 1

    @classmethod
    def _interactive_setup(cls):
        """Interactive setup process."""
        print("[LAUNCH] Interactive Setup")
        print("=" * 30)

        # Check if config exists
        if not os.path.exists(fff_paths.CONFIG_DIR / 'config.yaml'):  # fff: was ./config.yaml
            print("[ERROR] config.yaml not found")
            print("[IDEA] Run: python main.py setup --init")
            return 1

        # Run dependency check
        if cls._check_dependencies() != 0:
            return 1

        # Test database if config exists
        try:
            from fff.sorter.config import Config
            config = Config()
            return cls._test_database(config)
        except Exception as e:
            print(f"[ERROR] Configuration error: {e}")
            return 1


class DuplicateManagerCommand(BaseCommand):
    """Duplicate manager GUI command."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--scan-only',
            action='store_true',
            help='Only scan and report duplicates without launching GUI'
        )

    @classmethod
    def execute(cls, args, config):
        """Launch duplicate manager GUI."""
        logger.info("Launching duplicate manager")

        try:
            if args.scan_only:
                # Command line duplicate scan
                print("[SEARCH] Scanning for duplicates...")
                return cls._scan_duplicates_cli(config)
            else:
                # Launch GUI
                print("[DISPLAY]  Launching Duplicate Manager GUI...")
                return cls._launch_gui(config)

        except Exception as e:
            logger.error(f"Error in duplicate manager: {e}")
            print(f"[ERROR] Error: {e}")
            return 1

    @classmethod
    def _scan_duplicates_cli(cls, config):
        """Perform command-line duplicate scan."""
        try:
            from fff.sorter.handlers import DatabaseManager

            print("[FOLDER] Connecting to database...")
            db_manager = DatabaseManager(config)

            print("[SEARCH] Executing duplicate query...")

            # Execute the duplicate query
            conn = db_manager.get_db_connection()
            cursor = conn.cursor(dictionary=True)

            tables = [
                'archive_premium',
                'favorites',
                'manual_sort_images',
                'manual_sort_videos',
                'sfw_image',
                'sfw_video',
                'sfw_gif',
                'nsfw_image',
                'nsfw_video',
                'nsfw_gif',
                'archive_explicit',
                'archive_sfw',
            ]

            ignore_premium = False
            if hasattr(config, 'should_ignore_archive_premium'):
                try:
                    ignore_premium = bool(config.should_ignore_archive_premium())
                except Exception:
                    ignore_premium = False
            else:
                ignore_premium = bool(getattr(config, 'ignore_archive_premium', False))

            if ignore_premium:
                tables = [t for t in tables if t != 'archive_premium']

            DB = sorter_paths.db_names()  # fff: prod or test_ schema name
            select_union = " UNION ALL ".join(
                [
                    f"SELECT '{table}' AS table_name, file_hash, file_path, file_modified_at\n"
                    f"                FROM `{DB['sorter']}`.`{table}`"
                    for table in tables
                ]
            )

            hash_union = " UNION ALL ".join(
                [f"SELECT file_hash FROM `{DB['sorter']}`.`{table}`" for table in tables]
            )

            query = f"""
            SELECT *
            FROM (
                {select_union}
            ) AS combined
            WHERE `file_hash` IN (
                SELECT `file_hash`
                FROM (
                    SELECT `file_hash`
                    FROM (
                        {hash_union}
                    ) AS all_hashes
                    GROUP BY file_hash
                    HAVING COUNT(*) > 1
                ) AS duplicate_hashes
            )
            ORDER BY `file_hash`, `table_name`
            """

            cursor.execute(query)
            results = cursor.fetchall()

            cursor.close()
            conn.close()

            # Process results
            groups_by_hash = {}
            for row in results:
                hash_value = row['file_hash']
                if hash_value not in groups_by_hash:
                    groups_by_hash[hash_value] = []
                groups_by_hash[hash_value].append(row)

            duplicate_groups = [(hash_val, files) for hash_val, files in groups_by_hash.items() if len(files) > 1]

            # Report results
            total_files = sum(len(files) for _, files in duplicate_groups)

            print(f"\n[CHART] Duplicate Scan Results:")
            print(f"   Duplicate Groups: {len(duplicate_groups)}")
            print(f"   Total Files: {total_files}")

            if duplicate_groups:
                print(f"\n[SEARCH] Top 10 Duplicate Groups:")
                for i, (hash_val, files) in enumerate(duplicate_groups[:10]):
                    print(f"   {i+1:2d}. Hash: {hash_val[:12]}... ({len(files)} files)")
                    for file_info in files[:3]:  # Show first 3 files
                        path = file_info['file_path']
                        if len(path) > 60:
                            path = "..." + path[-57:]
                        print(f"       - {file_info['table_name']:15s} {path}")
                    if len(files) > 3:
                        print(f"       ... and {len(files) - 3} more files")

                if len(duplicate_groups) > 10:
                    print(f"   ... and {len(duplicate_groups) - 10} more groups")

                print(f"\n[IDEA] To manage duplicates interactively, run:")
                print(f"   python main.py duplicates")
            else:
                print("[OK] No duplicates found!")

            return 0

        except Exception as e:
            logger.error(f"Error during CLI duplicate scan: {e}")
            print(f"[ERROR] Scan failed: {e}")
            return 1

    @classmethod
    def _launch_gui(cls, config):
        """Launch the duplicate manager GUI."""
        try:
            from fff.gui import duplicate_manager_ui

            duplicate_manager_ui.main()

            print("[OK] Duplicate Manager closed")
            return 0

        except ImportError as e:
            if "tkinter" in str(e):
                print("[ERROR] GUI not available: tkinter not installed")
                print("[IDEA] Try: python main.py duplicates --scan-only")
            else:
                print(f"[ERROR] Import error: {e}")
            return 1
        except Exception as e:
            logger.error(f"Error launching GUI: {e}")
            print(f"[ERROR] GUI launch failed: {e}")
            return 1


class TagSortCommand(BaseCommand):
    """Sort files based on tags from e621 database (configurable)."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--tags',
            nargs='+',
            help='Tags to search for (e.g., --tags yoshi wolf) - legacy parameter'
        )
        parser.add_argument(
            '--needed-tags',
            nargs='+',
            help='Tags that MUST all be present (AND logic) - e.g., --needed-tags cock_vore'
        )
        parser.add_argument(
            '--can-have-tags',
            nargs='+',
            help='Tags where at least ONE must be present (OR logic) - e.g., --can-have-tags fatal_vore digestion'
        )
        parser.add_argument(
            '--target-dir',
            required=True,
            help='Target directory to move matching files to'
        )
        parser.add_argument(
            '--source-dirs',
            nargs='+',
            help='Source directories to scan (defaults to config source and destination directories)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually moving files'
        )
        parser.add_argument(
            '--match-all',
            action='store_true',
            help='Require all tags to match (AND logic) instead of any tag (OR logic)'
        )
        parser.add_argument(
            '--exclude-favorite-artists',
            action='store_true',
            help='Exclude posts from artists in artists.yaml'
        )
        parser.add_argument(
            '--exclude-tags',
            nargs='+',
            help='Tags to exclude - posts with ANY of these tags will be filtered out (e.g., --exclude-tags mario luigi)'
        )
        parser.add_argument(
            '--only-fav-directory',
            action='store_true',
            help='Only search in the favorites directory (ignores all other source directories)'
        )
        parser.add_argument(
            '--set-post-date',
            action='store_true',
            help="Set each moved file's modified/created timestamp to match its e621 post's created_at date"
        )
        parser.add_argument(
            '--e621-host',
            default='localhost',
            help='e621 database host (default: localhost)'
        )
        parser.add_argument(
            '--e621-port',
            type=int,
            default=3306,
            help='e621 database port (default: 3306)'
        )
        parser.add_argument(
            '--e621-user',
            help='e621 database user (defaults to main config mysql user)'
        )
        parser.add_argument(
            '--e621-password',
            help='e621 database password (defaults to main config mysql password)'
        )
        parser.add_argument(
            '--e621-database',
            default=None,  # fff: None = the environment's e621 database (prod or test_)
            help='e621 database name (default: the environment\'s e621 database, e621_posts_db or test_e621_posts_db)'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute tag-based sorting."""
        try:
            from fff.sorter.features.tag_sorter import TagSorter

            # Validate parameters
            if not args.tags and not args.needed_tags and not args.can_have_tags:
                print("[ERROR] Error: Must specify at least one of --tags, --needed-tags, or --can-have-tags")
                return 1

            # Build e621 database config
            e621_config = {
                'host': args.e621_host,
                'port': args.e621_port,
                'user': args.e621_user if args.e621_user else config.MYSQL_USER,
                'password': args.e621_password if args.e621_password else config.MYSQL_PASSWORD,
                'database': args.e621_database or sorter_paths.db_names()['archive']  # fff: environment default
            }

            # Initialize database manager for operation logging
            from fff.sorter.handlers.database_manager import DatabaseManager
            db_manager = DatabaseManager(config)

            # Initialize tag sorter
            tag_sorter = TagSorter(config, e621_config, db_manager=db_manager)

            # Execute sorting operation (CLI doesn't use hierarchical exclusion - that's only for config-based runs)
            results = tag_sorter.sort_by_tags(
                tags=args.tags if hasattr(args, 'tags') and args.tags else None,
                needed_tags=args.needed_tags if hasattr(args, 'needed_tags') and args.needed_tags else None,
                can_have_tags=args.can_have_tags if hasattr(args, 'can_have_tags') and args.can_have_tags else None,
                destination_dir=args.target_dir,
                source_dirs=args.source_dirs,
                match_all=args.match_all,
                exclude_favorite_artists=args.exclude_favorite_artists if hasattr(args, 'exclude_favorite_artists') else False,
                exclude_tags=args.exclude_tags if hasattr(args, 'exclude_tags') and args.exclude_tags else [],
                exclude_higher_priority_dirs=None,  # CLI doesn't use hierarchical priority
                only_fav_directory=args.only_fav_directory if hasattr(args, 'only_fav_directory') else False,
                dry_run=args.dry_run,
                set_post_date=args.set_post_date if hasattr(args, 'set_post_date') else False
            )
            # Check for errors
            if 'error' in results:
                logger.error(f"Tag sort failed: {results['error']}")
                return 1

            logger.info(f"Tag sort complete - DB matches: {results['db_matches']}, "
                       f"Files found: {results['files_found']}, Moved: {results['moved']}, "
                       f"Dates set: {results.get('dates_set', 0)}")
            return 0

        except ImportError as e:
            print(f"[ERROR] Import error: {e}")
            print("[IDEA] Make sure mysql-connector-python is installed")
            return 1
        except Exception as e:
            logger.error(f"Error during tag sort: {e}")
            print(f"[ERROR] Tag sort failed: {e}")
            import traceback
            traceback.print_exc()
            return 1


class PoolSortCommand(BaseCommand):
    """Sort files based on e621 pool membership from e621 database."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--pool-ids',
            nargs='+',
            required=True,
            type=int,
            help='e621 pool IDs to search for (e.g., --pool-ids 12345 67890)'
        )
        parser.add_argument(
            '--target-dir',
            required=True,
            help='Target directory to move matching files to'
        )
        parser.add_argument(
            '--source-dirs',
            nargs='+',
            help='Source directories to scan (defaults to config source and destination directories)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually moving files'
        )
        parser.add_argument(
            '--exclude-favorite-artists',
            action='store_true',
            help='Exclude posts from artists in artists.yaml'
        )
        parser.add_argument(
            '--exclude-tags',
            nargs='+',
            help='Tags to exclude - posts with ANY of these tags will be filtered out'
        )
        parser.add_argument(
            '--only-fav-directory',
            action='store_true',
            help='Only search in the favorites directory (ignores all other source directories)'
        )
        parser.add_argument(
            '--set-post-date',
            action='store_true',
            help="Set each moved file's modified/created timestamp to match its e621 post's created_at date (keeps pools/comics in upload order)"
        )
        parser.add_argument(
            '--e621-host',
            default='localhost',
            help='e621 database host (default: localhost)'
        )
        parser.add_argument(
            '--e621-port',
            type=int,
            default=3306,
            help='e621 database port (default: 3306)'
        )
        parser.add_argument(
            '--e621-user',
            help='e621 database user (defaults to main config mysql user)'
        )
        parser.add_argument(
            '--e621-password',
            help='e621 database password (defaults to main config mysql password)'
        )
        parser.add_argument(
            '--e621-database',
            default=None,  # fff: None = the environment's e621 database (prod or test_)
            help='e621 database name (default: the environment\'s e621 database, e621_posts_db or test_e621_posts_db)'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute pool-based sorting."""
        try:
            from fff.sorter.features.tag_sorter import TagSorter

            # Build e621 database config
            e621_config = {
                'host': args.e621_host,
                'port': args.e621_port,
                'user': args.e621_user if args.e621_user else config.MYSQL_USER,
                'password': args.e621_password if args.e621_password else config.MYSQL_PASSWORD,
                'database': args.e621_database or sorter_paths.db_names()['archive']  # fff: environment default
            }

            # Initialize database manager for operation logging
            from fff.sorter.handlers.database_manager import DatabaseManager
            db_manager = DatabaseManager(config)

            # Reuse TagSorter - it provides the shared file discovery/move infrastructure
            pool_sorter = TagSorter(config, e621_config, db_manager=db_manager)

            results = pool_sorter.sort_by_pools(
                pool_ids=args.pool_ids,
                destination_dir=args.target_dir,
                source_dirs=args.source_dirs,
                exclude_favorite_artists=args.exclude_favorite_artists if hasattr(args, 'exclude_favorite_artists') else False,
                exclude_tags=args.exclude_tags if hasattr(args, 'exclude_tags') and args.exclude_tags else [],
                only_fav_directory=args.only_fav_directory if hasattr(args, 'only_fav_directory') else False,
                dry_run=args.dry_run,
                set_post_date=args.set_post_date if hasattr(args, 'set_post_date') else False
            )

            if 'error' in results:
                logger.error(f"Pool sort failed: {results['error']}")
                return 1

            logger.info(f"Pool sort complete - DB matches: {results['db_matches']}, "
                       f"Files found: {results['files_found']}, Moved: {results['moved']}, "
                       f"Dates set: {results.get('dates_set', 0)}")
            return 0

        except ImportError as e:
            print(f"[ERROR] Import error: {e}")
            print("[IDEA] Make sure mysql-connector-python is installed")
            return 1
        except Exception as e:
            logger.error(f"Error during pool sort: {e}")
            print(f"[ERROR] Pool sort failed: {e}")
            import traceback
            traceback.print_exc()
            return 1


class ArtistSortCommand(BaseCommand):
    """Sort files by artist names from artists.yaml into favorites subdirectories."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--search-all-subdirs',
            action='store_true',
            default=True,
            help='Search all subdirectories of favorites folder (default: True)'
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Enable debug mode with verbose output'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually moving files'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute artist-based file sorting."""
        try:
            from fff.sorter.handlers import ArtistManager, DatabaseManager

            print("[STYLE] Artist-Based File Sorting")
            print("=" * 80)
            print(f"[FOLDER] Favorites directory: {config.fav_directory}")
            print(f"[SEARCH] Search all subdirectories: {args.search_all_subdirs}")
            print(f"[BUG] Debug mode: {args.debug}")

            if args.dry_run:
                print("[ANALYZE] DRY RUN MODE - No files will be moved")

            print("=" * 80)

            # Initialize database manager
            db_manager = DatabaseManager(config)

            # Initialize artist manager
            artist_manager = ArtistManager(config, db_manager)

            if not artist_manager.favorite_artists:
                print("[ERROR] No favorite artists loaded from artists.yaml")
                return 1

            print(f"\n[OK] Loaded {len(artist_manager.favorite_artists)} favorite artists")

            if args.debug:
                print("\n[Clipboard] Artists loaded:")
                for canonical, artist_info in sorted(artist_manager.favorite_artists.items())[:10]:
                    aliases = artist_info.get('aliases', []) if isinstance(artist_info, dict) else artist_info
                    print(f"   - {canonical}: {aliases[:3]}{'...' if len(aliases) > 3 else ''}")
                if len(artist_manager.favorite_artists) > 10:
                    print(f"   ... and {len(artist_manager.favorite_artists) - 10} more")

            # Run the artist sorting
            if args.dry_run:
                print("\n[WARNING] DRY RUN: Scanning only, no files will be moved")
                # In dry run mode, we'd need to modify the check_favorites_in_destinations method
                # For now, inform the user
                print("[ERROR] Dry-run mode not yet implemented for artist sorting")
                print("[IDEA] To see what would be moved, check the logs in debug mode")
                return 1

            print("\n[Loop] Starting artist sorting...")
            files_moved = artist_manager.check_favorites_in_destinations(
                debug_mode=args.debug,
                scan_all_fav_subdirs=args.search_all_subdirs
            )

            print(f"\n{'='*80}")
            print("[OK] ARTIST SORTING COMPLETE")
            print(f"{'='*80}")
            print(f"[PACKAGE] Total files moved: {files_moved}")
            print(f"{'='*80}\n")

            logger.info(f"Artist sorting completed - {files_moved} files moved")
            return 0

        except Exception as e:
            logger.error(f"Error during artist sort: {e}")
            print(f"[ERROR] Artist sort failed: {e}")
            import traceback
            traceback.print_exc()
            return 1


class CleanupCommand(BaseCommand):
    """Clean up empty folders in directories."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            'directories',
            nargs='*',
            help='Directories to scan for empty folders (if not specified, uses source directories from config)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )
        parser.add_argument(
            '--no-recursive',
            action='store_true',
            help='Only scan immediate subdirectories (not recursive)'
        )
        parser.add_argument(
            '--include-destinations',
            action='store_true',
            help='Also scan destination directories from config'
        )

    @classmethod
    def execute(cls, args, config):
        """Execute empty folder cleanup."""
        try:
            print("🧹 Empty Folder Cleanup")
            print("=" * 80)

            # Determine which directories to scan
            directories_to_scan = []

            if args.directories:
                # Use directories specified on command line
                directories_to_scan.extend(args.directories)
                print(f"[FOLDER] Scanning user-specified directories")
            else:
                # Use source directories from config
                if hasattr(config, 'source_directories'):
                    directories_to_scan.extend(config.source_directories)
                    print(f"[FOLDER] Scanning source directories from config")

                # Optionally include destination directories
                if args.include_destinations and hasattr(config, 'destination_directories'):
                    for dest in config.destination_directories.values():
                        if dest and dest not in directories_to_scan:
                            directories_to_scan.append(dest)
                    print(f"[FOLDER] Including destination directories from config")

            if not directories_to_scan:
                print("[ERROR] No directories to scan. Specify directories or check config.")
                return 1

            print(f"[SEARCH] Recursive: {'No' if args.no_recursive else 'Yes'}")
            if args.dry_run:
                print("[ANALYZE] DRY RUN MODE - No folders will actually be deleted")
            print("=" * 80)
            print()

            # Process each directory
            total_deleted = 0
            total_skipped = 0

            for directory in directories_to_scan:
                if not os.path.exists(directory):
                    print(f"[WARNING]  Skipping non-existent directory: {directory}")
                    continue

                print(f"\n[FOLDER] Scanning: {directory}")
                print("-" * 80)

                deleted, skipped = cls._delete_empty_folders(
                    directory,
                    dry_run=args.dry_run,
                    recursive=not args.no_recursive
                )

                total_deleted += deleted
                total_skipped += skipped

                print(f"  {'Found' if args.dry_run else 'Deleted'}: {deleted} empty folders")
                if skipped > 0:
                    print(f"  Skipped (errors): {skipped} folders")

            # Summary
            print()
            print("=" * 80)
            print("[CHART] CLEANUP SUMMARY")
            print("=" * 80)
            print(f"Total empty folders {'found' if args.dry_run else 'deleted'}: {total_deleted}")
            if total_skipped > 0:
                print(f"Total folders skipped (errors): {total_skipped}")

            if args.dry_run:
                print("\n[IDEA] Run without --dry-run to actually delete these folders")

            print()

            logger.info(f"Cleanup completed - {total_deleted} folders {'found' if args.dry_run else 'deleted'}")
            return 0

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            print(f"[ERROR] Cleanup failed: {e}")
            import traceback
            traceback.print_exc()
            return 1

    @staticmethod
    def _is_directory_empty(path):
        """Check if a directory is empty."""
        try:
            return not any(os.scandir(path))
        except (PermissionError, OSError) as e:
            logger.warning(f"Cannot access directory {path}: {e}")
            return False

    @staticmethod
    def _delete_empty_folders(root_path, dry_run=False, recursive=True):
        """
        Delete empty folders in the given directory tree.

        Returns:
            tuple: (folders_deleted, folders_skipped)
        """
        root_path = Path(root_path).resolve()

        if not root_path.exists() or not root_path.is_dir():
            return 0, 0

        folders_deleted = 0
        folders_skipped = 0

        if recursive:
            # Walk bottom-up so we delete nested empty folders first
            for dirpath, dirnames, filenames in os.walk(root_path, topdown=False):
                # Skip the root directory itself
                if dirpath == str(root_path):
                    continue

                # Skip hidden folders (starting with .)
                folder_name = os.path.basename(dirpath)
                if folder_name.startswith('.'):
                    logger.debug(f"Skipping hidden folder: {dirpath}")
                    continue

                if CleanupCommand._is_directory_empty(dirpath):
                    if dry_run:
                        logger.info(f"[DRY RUN] Would delete: {dirpath}")
                        folders_deleted += 1
                    else:
                        try:
                            os.rmdir(dirpath)
                            logger.info(f"Deleted empty folder: {dirpath}")
                            folders_deleted += 1
                        except (PermissionError, OSError) as e:
                            logger.error(f"Failed to delete {dirpath}: {e}")
                            folders_skipped += 1
        else:
            # Non-recursive: only check immediate subdirectories
            try:
                for entry in os.scandir(root_path):
                    if entry.is_dir():
                        # Skip hidden folders (starting with .)
                        if entry.name.startswith('.'):
                            logger.debug(f"Skipping hidden folder: {entry.path}")
                            continue

                        if CleanupCommand._is_directory_empty(entry.path):
                            if dry_run:
                                logger.info(f"[DRY RUN] Would delete: {entry.path}")
                                folders_deleted += 1
                            else:
                                try:
                                    os.rmdir(entry.path)
                                    logger.info(f"Deleted empty folder: {entry.path}")
                                    folders_deleted += 1
                                except (PermissionError, OSError) as e:
                                    logger.error(f"Failed to delete {entry.path}: {e}")
                                    folders_skipped += 1
            except (PermissionError, OSError) as e:
                logger.error(f"Cannot scan directory {root_path}: {e}")

        return folders_deleted, folders_skipped


class TagSortConfigCommand(BaseCommand):
    """Launch the Tag Sort Configuration GUI."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--config',
            default=None,
            help='Path to tag_sorting.yaml (default: config/tag_sorting.yaml)'
        )

    @classmethod
    def execute(cls, args, config):
        """Launch the Tag Sort Configuration UI."""
        from pathlib import Path

        tag_config_path = None
        if hasattr(args, 'config') and args.config:
            tag_config_path = Path(args.config)
        else:
            # Try to resolve from main config
            try:
                tag_config_path = Path(getattr(config, 'tag_sorting_config',
                                               str(sorter_paths.TAG_SORTING)))  # fff: was configs/tag_sorting.yaml
            except Exception:
                tag_config_path = Path(sorter_paths.TAG_SORTING)  # fff: was configs/tag_sorting.yaml

        print(f"[DISPLAY] Launching Tag Sort Configuration UI...")
        print(f"[FOLDER] Config file: {tag_config_path}")

        try:
            from fff.gui.tag_sort_config_ui import launch  # fff: GUI package
            launch(config_path=tag_config_path)
            return 0
        except ImportError as e:
            logger.error(f"Failed to import UI module: {e}")
            print(f"[ERROR] Could not load Tag Sort Config UI: {e}")
            return 1
        except Exception as e:
            logger.error(f"Error launching Tag Sort Config UI: {e}")
            print(f"[ERROR] Error: {e}")
            return 1


class ArtistConfigCommand(BaseCommand):
    """Launch the Artist Configuration GUI."""

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            '--config',
            default=None,
            help='Path to artists.yaml (default: config/sort_artists.yaml)'
        )

    @classmethod
    def execute(cls, args, config):
        """Launch the Artist Configuration UI."""
        from pathlib import Path

        artist_config_path = Path(args.config) if hasattr(args, 'config') and args.config else Path(sorter_paths.SORT_ARTISTS)  # fff: was configs/artists.yaml

        print(f"[DISPLAY] Launching Artist Configuration UI...")
        print(f"[FOLDER] Config file: {artist_config_path}")

        try:
            from fff.gui.artist_config_ui import launch  # fff: GUI package
            launch(config_path=artist_config_path)
            return 0
        except ImportError as e:
            logger.error(f"Failed to import artist UI module: {e}")
            print(f"[ERROR] Could not load Artist Config UI: {e}")
            return 1
        except Exception as e:
            logger.error(f"Error launching Artist Config UI: {e}")
            print(f"[ERROR] Error: {e}")
            return 1

