#!/usr/bin/env python3
# fff: one-off migration kept for reference; main(argv=None) (no options).
"""
Migration script to add artist check columns to all tables.

This adds the last_artist_check and artist_matched columns to all
destination and archive tables in the database.

Usage:
    python migrate_artist_columns.py
"""

import sys
from pathlib import Path

from fff.sorter.config import Config
from fff.sorter.handlers import DatabaseManager


def main(argv=None):
    print("=" * 80)
    print("Artist Check Columns Migration")
    print("=" * 80)
    print()
    print("This script will add the following columns to all tables:")
    print("  - last_artist_check (TIMESTAMP NULL)")
    print("  - artist_matched (VARCHAR(255) NULL)")
    print()
    
    # Load config
    try:
        config = Config('configs/config.yaml')
        print(f"✓ Config loaded successfully")
    except Exception as e:
        print(f"✗ Failed to load config: {e}")
        return 1
    
    # Initialize database manager (this will run the migration)
    try:
        print(f"✓ Connecting to database...")
        db_manager = DatabaseManager(config)
        print(f"✓ Database manager initialized")
        print()
        print("The _ensure_artist_check_columns() method runs automatically")
        print("during initialization and adds columns to all tables.")
        print()
        
        # Verify columns were added
        tables = db_manager.get_all_table_names(include_archives=True)
        print(f"Verifying {len(tables)} tables...")
        print()
        
        missing_columns = []
        for table_name in tables:
            conn = db_manager.get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'last_artist_check'")
            has_last_check = cursor.fetchone() is not None
            
            cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE 'artist_matched'")
            has_artist_matched = cursor.fetchone() is not None
            
            cursor.close()
            conn.close()
            
            if has_last_check and has_artist_matched:
                print(f"  ✓ {table_name}")
            else:
                missing = []
                if not has_last_check:
                    missing.append("last_artist_check")
                if not has_artist_matched:
                    missing.append("artist_matched")
                print(f"  ✗ {table_name} (missing: {', '.join(missing)})")
                missing_columns.append((table_name, missing))
        
        print()
        print("=" * 80)
        if missing_columns:
            print(f"⚠ Warning: {len(missing_columns)} table(s) missing columns")
            print()
            print("This might be due to permissions or table types.")
            print("Check the logs for more details.")
        else:
            print("✓ All tables have artist check columns!")
            print()
            print("You can now execute the all_files_view.sql to update the view.")
        print("=" * 80)
        
        return 0 if not missing_columns else 1
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
