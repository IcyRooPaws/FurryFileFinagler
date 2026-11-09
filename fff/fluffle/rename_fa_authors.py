#!/usr/bin/env python3
# Renames are logged to file_operations.
"""
FurAffinity Author Name to ID Renamer
This script queries the database for FurAffinity author mappings and renames files
that contain author names to use author IDs instead, while preserving file modification times.
"""

import os
import sys
import re
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime
import yaml
import mysql.connector
from mysql.connector import Error

from fff.common import config as fff_config
from fff.common import db
from fff.fluffle import ops


def load_config(config_path: str = None) -> Dict:
    """Load configuration from YAML file."""
    if config_path is None:  # fff: the reverse search config view
        return fff_config.legacy_view("fluffle")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except FileNotFoundError:
        print(f"Error: Configuration file '{config_path}' not found.")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error: Failed to parse configuration file: {e}")
        sys.exit(1)



def get_author_mappings(connection: mysql.connector.connection.MySQLConnection) -> List[Tuple[str, str]]:
    """
    Query database for distinct author_id and author_name mappings from furaffinity_links.
    Returns a list of tuples: (author_id, author_name)
    """
    try:
        cursor = connection.cursor()
        query = """
            SELECT DISTINCT
                fl.author_id,
                fl.author_name
            FROM furaffinity_links AS fl
            WHERE fl.author_id IS NOT NULL
            AND fl.author_name IS NOT NULL
            ORDER BY fl.author_name
        """
        cursor.execute(query)
        results = cursor.fetchall()
        cursor.close()

        print(f"Found {len(results)} unique author mappings in database")
        return results
    except Error as e:
        print(f"Error querying database: {e}")
        return []


def find_fa_files(directory: str) -> List[Path]:
    """
    Search directory recursively for files containing '-FA-' in their name.
    Returns a list of Path objects.
    """
    directory_path = Path(directory)
    if not directory_path.exists():
        print(f"Error: Directory '{directory}' does not exist.")
        return []

    fa_files = []
    for file_path in directory_path.rglob('*-FA-*'):
        if file_path.is_file():
            fa_files.append(file_path)

    print(f"Found {len(fa_files)} files with '-FA-' in their name")
    return fa_files


def rename_file_preserve_mtime(old_path: Path, new_path: Path) -> bool:
    """
    Rename a file while preserving its modification time.
    Returns True if successful, False otherwise.
    """
    try:
        # Get the current modification time
        stat_info = old_path.stat()
        mtime = stat_info.st_mtime
        atime = stat_info.st_atime

        # Rename the file
        ops.rename(old_path, new_path, "fa_author_rename")

        # Restore the modification time
        os.utime(new_path, (atime, mtime))

        return True
    except Exception as e:
        print(f"Error renaming file: {e}")
        return False


def process_files(files: List[Path], author_mappings: List[Tuple[str, str]], dry_run: bool = True) -> None:
    """
    Process files and rename those containing author names to use author IDs instead.

    Args:
        files: List of file paths to process
        author_mappings: List of (author_id, author_name) tuples
        dry_run: If True, only show what would be renamed without actually renaming
    """
    renamed_count = 0
    skipped_count = 0

    # Create a mapping dict for faster lookup
    # Sort by length of author_name (descending) to handle longer names first
    # This prevents partial matches (e.g., "bob" matching in "bobby")
    author_map = sorted(author_mappings, key=lambda x: len(x[1]), reverse=True)

    for file_path in files:
        original_name = file_path.name
        new_name = original_name
        matched_author = None

        # Check each author mapping
        for author_id, author_name in author_map:
            # First, try to match author_name (case-insensitive) and replace with author_id
            # Use word boundaries or specific patterns to avoid partial matches
            # Pattern: match author name followed by a dash (typical FA filename format)
            # or at word boundaries
            pattern_name = re.compile(r'\b' + re.escape(author_name) + r'(?=-)', re.IGNORECASE)

            if pattern_name.search(new_name):
                # Replace with author_id (case-insensitive match, but exact replacement)
                new_name = pattern_name.sub(author_id, new_name, count=1)
                matched_author = (author_id, author_name)
                break  # Only replace the first match to avoid multiple replacements

            # Also check if author_id appears with wrong case and fix it
            # Same pattern: author_id followed by a dash
            pattern_id = re.compile(r'\b' + re.escape(author_id) + r'(?=-)', re.IGNORECASE)

            if pattern_id.search(new_name):
                # Check if the case is incorrect (if it matches case-insensitively but not exactly)
                if author_id not in new_name:
                    # Replace with correct case author_id
                    new_name = pattern_id.sub(author_id, new_name, count=1)
                    matched_author = (author_id, f"{author_id} (case fix)")
                    break

        # If a match was found and the name changed
        if new_name != original_name:
            new_path = file_path.parent / new_name

            # Check if target file already exists (but not a case-only rename on Windows)
            # On Windows, file paths are case-insensitive, so we need to check if it's the same file
            is_case_only_rename = (new_path.exists() and
                                  file_path.resolve() == new_path.resolve())

            if new_path.exists() and not is_case_only_rename:
                print(f"[WARNING]  SKIP: Target already exists")
                print(f"    {file_path}")
                print(f"    -> {new_path.name}")
                skipped_count += 1
                continue

            if dry_run:
                print(f"Would rename:")
                print(f"  FROM: {file_path}")
                print(f"  TO:   {new_path.name}")
                print(f"  (Matched author: {matched_author[1]} -> {matched_author[0]})")
                renamed_count += 1
            else:
                print(f"Renaming:")
                print(f"  FROM: {file_path}")
                print(f"  TO:   {new_path.name}")
                print(f"  (Matched author: {matched_author[1]} -> {matched_author[0]})")

                if rename_file_preserve_mtime(file_path, new_path):
                    renamed_count += 1
                    print("  [OK] Success")
                else:
                    skipped_count += 1
                    print("  [ERROR] Failed")

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Summary:")
    print(f"  Files {'that would be' if dry_run else ''} renamed: {renamed_count}")
    print(f"  Files skipped: {skipped_count}")
    print(f"  Total files processed: {len(files)}")


def main(argv=None):  # fff: argv accepted for the CLI; no options
    """Main execution function."""
    print("FurAffinity Author Name to ID Renamer")
    print("=" * 60)

    # Load configuration
    config = load_config()

    # Get the directory to search from config
    search_directory = config.get('furaffinity', {}).get('rename_search_directory',
                                                          'P:/IPL/Pictures')

    print(f"\nConfiguration:")
    print(f"  Database: {config['database']['host']}")
    print(f"  Search directory: {search_directory}")

    # Ask user to confirm or override directory
    print(f"\nPress Enter to use the above directory, or type a new path:")
    user_input = input().strip()
    if user_input:
        search_directory = user_input

    # Connect to database
    print("\nConnecting to database...")
    connection = db.try_connect_with(config['database'])
    if not connection:
        print("Failed to connect to database. Exiting.")
        sys.exit(1)

    try:
        # Get author mappings from database
        print("\nQuerying database for author mappings...")
        author_mappings = get_author_mappings(connection)

        if not author_mappings:
            print("No author mappings found in database. Exiting.")
            return

        # Show sample mappings
        print(f"\nSample author mappings:")
        for author_id, author_name in author_mappings[:5]:
            print(f"  {author_name} -> {author_id}")
        if len(author_mappings) > 5:
            print(f"  ... and {len(author_mappings) - 5} more")

        # Find files with -FA- in their name
        print(f"\nSearching for files with '-FA-' in: {search_directory}")
        fa_files = find_fa_files(search_directory)

        if not fa_files:
            print("No files found with '-FA-' in their name. Exiting.")
            return

        # Ask user if they want to do a dry run first
        print(f"\nFound {len(fa_files)} files to process.")
        print("Do you want to do a DRY RUN first? (y/n): ", end='')
        response = input().strip().lower()

        if response in ['y', 'yes', '']:
            print("\n" + "=" * 60)
            print("DRY RUN - No files will be modified")
            print("=" * 60)
            process_files(fa_files, author_mappings, dry_run=True)

            print("\nDo you want to proceed with actual renaming? (y/n): ", end='')
            response = input().strip().lower()

            if response in ['y', 'yes']:
                print("\n" + "=" * 60)
                print("RENAMING FILES")
                print("=" * 60)
                process_files(fa_files, author_mappings, dry_run=False)
            else:
                print("Renaming cancelled.")
        else:
            print("\n" + "=" * 60)
            print("RENAMING FILES")
            print("=" * 60)
            process_files(fa_files, author_mappings, dry_run=False)

    finally:
        # Close database connection
        if connection and connection.is_connected():
            connection.close()
            print("\nDatabase connection closed.")

    print("\nDone!")


if __name__ == "__main__":
    main()
