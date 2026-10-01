#!/usr/bin/env python3
# fff: implemented in media sorter/migrate_paths_to_forward_slashes.py.
# fff: one-off migration kept for reference; main(argv=None).
r"""
Migrate Database Paths to Forward Slashes

This script updates all file_path entries in the database to use forward slashes (/)
instead of backslashes (\) for cross-platform consistency and easier SQL queries.

This migration is necessary because the normalize_path() function was changed
to use forward slashes for database storage.

Usage:
    python migrate_paths_to_forward_slashes.py [--dry-run]
"""

import sys
import argparse
from pathlib import Path

from fff.sorter.handlers import DatabaseManager, Config


def migrate_table_paths(db_manager, table_name, dry_run=False):
    """
    Update all file_path entries in a table to use forward slashes.
    
    Args:
        db_manager: DatabaseManager instance
        table_name: Name of table to update
        dry_run: If True, show what would be updated without making changes
        
    Returns:
        int: Number of paths updated
    """
    print(f"\n{'='*80}")
    print(f"Processing table: {table_name}")
    print(f"{'='*80}")
    
    try:
        # Get all file_path entries
        query = f"SELECT id, file_path FROM `{table_name}` WHERE file_path IS NOT NULL"
        conn = db_manager.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not rows:
            print(f"  No paths found in {table_name}")
            return 0
        
        print(f"  Found {len(rows)} paths to check")
        
        # Check which paths need updating
        paths_to_update = []
        for row_id, file_path in rows:
            if file_path and '\\' in file_path:
                new_path = file_path.replace('\\', '/')
                paths_to_update.append((row_id, file_path, new_path))
        
        if not paths_to_update:
            print(f"  ✓ All paths already use forward slashes")
            return 0
        
        print(f"  Found {len(paths_to_update)} paths with backslashes")
        
        if dry_run:
            print(f"  [DRY RUN] Would update {len(paths_to_update)} paths")
            # Show first few examples
            for i, (row_id, old_path, new_path) in enumerate(paths_to_update[:3]):
                print(f"    Example {i+1}:")
                print(f"      Old: {old_path}")
                print(f"      New: {new_path}")
            if len(paths_to_update) > 3:
                print(f"    ... and {len(paths_to_update) - 3} more")
            return len(paths_to_update)
        
        # Update paths
        conn = db_manager.get_db_connection()
        cursor = conn.cursor()
        
        updated = 0
        for row_id, old_path, new_path in paths_to_update:
            try:
                cursor.execute(
                    f"UPDATE `{table_name}` SET file_path = %s WHERE id = %s",
                    (new_path, row_id)
                )
                updated += 1
            except Exception as e:
                print(f"  ✗ Error updating row {row_id}: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"  ✓ Updated {updated} paths to use forward slashes")
        return updated
        
    except Exception as e:
        print(f"  ✗ Error processing {table_name}: {e}")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Migrate database paths to use forward slashes'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be updated without making changes'
    )
    parser.add_argument(
        '--config',
        default='configs/config.yaml',
        help='Path to config file (default: configs/config.yaml)'
    )
    args = parser.parse_args(argv)
    
    print("="*80)
    print("DATABASE PATH MIGRATION - Forward Slashes")
    print("="*80)
    
    if args.dry_run:
        print("[DRY RUN MODE] No changes will be made")
    
    # Load configuration
    try:
        config = Config(args.config)
        print(f"✓ Config loaded: {args.config}")
    except Exception as e:
        print(f"✗ Failed to load config: {e}")
        return 1
    
    # Initialize database manager
    try:
        db_manager = DatabaseManager(config)
        print(f"✓ Connected to database: {config.MYSQL_HOST}")
    except Exception as e:
        print(f"✗ Failed to connect to database: {e}")
        return 1
    
    # Get all tables
    try:
        tables = db_manager.get_all_table_names()
        print(f"✓ Found {len(tables)} tables to process")
    except Exception as e:
        print(f"✗ Failed to get table names: {e}")
        return 1
    
    # Process each table
    total_updated = 0
    for table in tables:
        updated = migrate_table_paths(db_manager, table, dry_run=args.dry_run)
        total_updated += updated
    
    # Summary
    print(f"\n{'='*80}")
    print("MIGRATION COMPLETE")
    print(f"{'='*80}")
    
    if args.dry_run:
        print(f"[DRY RUN] Would update {total_updated} total paths across all tables")
        print("\nRun without --dry-run to apply changes")
    else:
        print(f"✓ Updated {total_updated} total paths across all tables")
        print("\nAll database paths now use forward slashes (/)")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
