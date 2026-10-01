#!/usr/bin/env python3
# fff: still incompatible with the active database schema: DatabaseManager is a stub that raises a
# fff: 'known broken' RuntimeError when the finder starts; main(argv=None).
"""
Advanced Duplicate Finder - Uses multi-stage deduplication pipeline

This script integrates the deduplicator's efficient multi-stage pipeline
with FFF's database and file handling infrastructure.

Usage:
    python find_duplicates_advanced.py --mode normal --tables all
    python find_duplicates_advanced.py --mode fast --tables Furry_Explicit Furry_Safe
    python find_duplicates_advanced.py --mode perceptual --min-size 1MB
"""

import sys
import os
import argparse
import logging
from pathlib import Path
from typing import List, Set, Optional

from fff.sorter.core.advanced_deduplicator import (
    AdvancedDeduplicator, DedupeMode, DuplicateFile, DuplicateGroup
)
from fff.sorter.config import Config


# Keep this module importable and --help usable, but prevent execution because its database
# adapter and file_hashes_* tables are not supported by the current schema.
class DatabaseManager:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "find_duplicates_advanced requires an unsupported database adapter and file_hashes_* tables. "
            "This command requires an obsolete database schema."
        )

logger = logging.getLogger(__name__)


class AdvancedDuplicateFinder:
    """Integrates advanced deduplicator with FFF database"""

    def __init__(self, config_path: str = 'configs/config.yaml'):
        """Initialize with FFF configuration"""
        self.config = Config(config_path)
        self.db = DatabaseManager(self.config.get_mysql_config())
        self.deduplicator = AdvancedDeduplicator()

    def get_all_tables(self) -> List[str]:
        """Get all file hash tables from database"""
        try:
            tables = self.db.get_all_tables()
            # Filter for file_hashes_* tables
            return [t for t in tables if t.startswith('file_hashes_')]
        except Exception as e:
            logger.error(f"Failed to get tables: {e}")
            return []

    def load_files_from_database(self,
                                 tables: Optional[List[str]] = None,
                                 min_size: Optional[int] = None,
                                 max_size: Optional[int] = None,
                                 extensions: Optional[Set[str]] = None) -> List[DuplicateFile]:
        """
        Load files from FFF database tables.

        Args:
            tables: List of table names to query (None = all tables)
            min_size: Minimum file size in bytes
            max_size: Maximum file size in bytes
            extensions: Set of file extensions to include

        Returns:
            List of DuplicateFile objects
        """
        if tables is None:
            tables = self.get_all_tables()

        files = []
        favorite_dirs = set(self.config.get('favorite_directories', []))

        for table in tables:
            logger.info(f"Loading files from {table}...")

            try:
                # Get all files from table
                query = f"SELECT file_path, file_name, file_hash FROM {table}"
                results = self.db.execute_query(query)

                for row in results:
                    file_path = row[0]
                    file_name = row[1]
                    existing_hash = row[2]

                    # Check if file exists
                    if not os.path.exists(file_path):
                        logger.debug(f"Skipping missing file: {file_path}")
                        continue

                    # Get file stats
                    try:
                        stat = os.stat(file_path)
                        file_size = stat.st_size
                        mtime = int(stat.st_mtime)
                    except Exception as e:
                        logger.debug(f"Failed to stat {file_path}: {e}")
                        continue

                    # Apply filters
                    if min_size and file_size < min_size:
                        continue
                    if max_size and file_size > max_size:
                        continue

                    if extensions:
                        ext = Path(file_path).suffix.lower()
                        if ext not in extensions:
                            continue

                    # Check if in favorite directory
                    is_favorite = any(
                        file_path.startswith(fav_dir)
                        for fav_dir in favorite_dirs
                    )

                    files.append(DuplicateFile(
                        path=file_path,
                        size=file_size,
                        table_name=table,
                        existing_hash=existing_hash,
                        mtime=mtime,
                        is_favorite=is_favorite
                    ))

                logger.info(f"  Loaded {len([f for f in files if f.table_name == table])} files from {table}")

            except Exception as e:
                logger.error(f"Failed to load files from {table}: {e}")
                continue

        logger.info(f"Total files loaded: {len(files)}")
        return files

    def find_duplicates(self,
                       tables: Optional[List[str]] = None,
                       mode: DedupeMode = DedupeMode.NORMAL,
                       min_size: Optional[int] = None,
                       max_size: Optional[int] = None,
                       extensions: Optional[Set[str]] = None,
                       progress_callback=None) -> tuple[List[DuplicateGroup], object]:
        """
        Find duplicates across specified tables.

        Args:
            tables: Tables to scan (None = all tables)
            mode: Deduplication mode
            min_size: Minimum file size filter
            max_size: Maximum file size filter
            extensions: File extensions to include
            progress_callback: Progress reporting function

        Returns:
            Tuple of (duplicate_groups, statistics)
        """
        # Load files from database
        files = self.load_files_from_database(tables, min_size, max_size, extensions)

        if not files:
            logger.warning("No files found to process")
            return [], None

        # Run deduplication
        logger.info(f"Running {mode.value} deduplication on {len(files)} files...")
        groups, stats = self.deduplicator.find_duplicates(
            files=files,
            mode=mode,
            image_extensions={'.jpg', '.jpeg', '.png', '.webp', '.gif'},
            progress_callback=progress_callback
        )

        return groups, stats

    def print_results(self, groups: List[DuplicateGroup], stats):
        """Print duplicate detection results"""
        print("\n" + "="*70)
        print(str(stats))
        print("="*70)

        if not groups:
            print("\n✅ No duplicates found!")
            return

        print(f"\n📁 Found {len(groups)} duplicate groups:\n")

        for i, group in enumerate(groups[:20], 1):  # Show first 20 groups
            size_mb = group.size / (1024 * 1024)
            wasted_mb = group.total_size / (1024 * 1024)

            print(f"\nGroup {i}: {group.file_count} files × {size_mb:.2f} MB = {wasted_mb:.2f} MB wasted")
            print(f"  Detection stage: {group.detection_stage}")

            for j, file in enumerate(group.files, 1):
                fav_marker = "⭐" if file.is_favorite else "  "
                table_info = f"[{file.table_name}]" if file.table_name else ""
                print(f"  {fav_marker} {j}. {Path(file.path).name} {table_info}")
                if j == 1:
                    print(f"      → {file.path}")

        if len(groups) > 20:
            print(f"\n... and {len(groups) - 20} more groups")

    def export_to_csv(self, groups: List[DuplicateGroup], output_file: str):
        """Export duplicate groups to CSV"""
        import csv

        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Group', 'File_Number', 'Path', 'Size_MB', 'Table', 'Is_Favorite', 'Stage'])

            for group_num, group in enumerate(groups, 1):
                size_mb = group.size / (1024 * 1024)
                for file_num, file in enumerate(group.files, 1):
                    writer.writerow([
                        group_num,
                        file_num,
                        file.path,
                        f"{size_mb:.2f}",
                        file.table_name or '',
                        'Yes' if file.is_favorite else 'No',
                        group.detection_stage
                    ])

        logger.info(f"Exported results to {output_file}")


def parse_size(size_str: str) -> int:
    """Parse size string like '1MB', '500KB' to bytes"""
    size_str = size_str.upper().strip()

    multipliers = {
        'B': 1,
        'KB': 1024,
        'MB': 1024 * 1024,
        'GB': 1024 * 1024 * 1024,
    }

    for unit, multiplier in multipliers.items():
        if size_str.endswith(unit):
            try:
                value = float(size_str[:-len(unit)])
                return int(value * multiplier)
            except ValueError:
                raise ValueError(f"Invalid size format: {size_str}")

    # Try parsing as raw number
    try:
        return int(size_str)
    except ValueError:
        raise ValueError(f"Invalid size format: {size_str}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Advanced duplicate finder using multi-stage pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Find all duplicates using normal mode
  python find_duplicates_advanced.py --mode normal

  # Fast scan of specific tables
  python find_duplicates_advanced.py --mode fast --tables Furry_Explicit Furry_Safe

  # Find image duplicates using perceptual hashing
  python find_duplicates_advanced.py --mode perceptual --extensions .jpg .png

  # Find large file duplicates
  python find_duplicates_advanced.py --min-size 10MB --mode full

  # Export results to CSV
  python find_duplicates_advanced.py --mode normal --export duplicates.csv
        """
    )

    parser.add_argument('--mode',
                       choices=['fast', 'normal', 'full', 'perceptual'],
                       default='normal',
                       help='Deduplication mode (default: normal)')

    parser.add_argument('--tables',
                       nargs='+',
                       help='Specific tables to scan (default: all tables)')

    parser.add_argument('--min-size',
                       type=str,
                       help='Minimum file size (e.g., 1MB, 500KB)')

    parser.add_argument('--max-size',
                       type=str,
                       help='Maximum file size (e.g., 100MB)')

    parser.add_argument('--extensions',
                       nargs='+',
                       help='File extensions to include (e.g., .jpg .png)')

    parser.add_argument('--export',
                       type=str,
                       help='Export results to CSV file')

    parser.add_argument('--config',
                       default='configs/config.yaml',
                       help='Path to config file')

    parser.add_argument('--verbose', '-v',
                       action='store_true',
                       help='Enable verbose logging')

    args = parser.parse_args(argv)

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%H:%M:%S'
    )

    # Parse size arguments
    min_size = parse_size(args.min_size) if args.min_size else None
    max_size = parse_size(args.max_size) if args.max_size else None

    # Parse extensions
    extensions = set(args.extensions) if args.extensions else None
    if extensions:
        # Ensure extensions start with dot
        extensions = {ext if ext.startswith('.') else f'.{ext}' for ext in extensions}

    # Parse mode
    mode = DedupeMode[args.mode.upper()]

    try:
        # Initialize finder
        finder = AdvancedDuplicateFinder(args.config)

        # Find duplicates
        groups, stats = finder.find_duplicates(
            tables=args.tables,
            mode=mode,
            min_size=min_size,
            max_size=max_size,
            extensions=extensions
        )

        # Print results
        finder.print_results(groups, stats)

        # Export if requested
        if args.export and groups:
            finder.export_to_csv(groups, args.export)
            print(f"\n💾 Results exported to {args.export}")

    except KeyboardInterrupt:
        print("\n\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == '__main__':
    main()
