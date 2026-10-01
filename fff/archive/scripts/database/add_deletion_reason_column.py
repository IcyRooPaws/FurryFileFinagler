#!/usr/bin/env python3
# fff: the __main__ block (confirmation prompt + migration) moved into main(argv).
# fff: main(argv) parses an option-less argparse parser so --help works; the original took no arguments.
"""
Temporary migration script to add the deletion_reason column to the posts table.
This script can be deleted after running once.
"""

import argparse  # fff: for main(argv)
import sys
import logging
from pathlib import Path


from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql

def setup_logging():
    """Setup basic logging."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

def add_deletion_reason_column():
    """Add deletion_reason column to posts table if it doesn't exist."""
    setup_logging()
    logging.info("Starting database migration: Adding deletion_reason column")
    
    try:
        # Load configuration
        config = load_config()
        mysql_config = config["mysql"]
        
        # Connect to database
        conn = connect_to_mysql(mysql_config)
        if conn is None:
            logging.error("Failed to connect to database")
            sys.exit(1)
        
        cursor = conn.cursor()
        
        # Check if column already exists
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.COLUMNS 
            WHERE TABLE_SCHEMA = %s 
            AND TABLE_NAME = 'posts' 
            AND COLUMN_NAME = 'deletion_reason'
        """, (mysql_config['database'],))
        
        exists = cursor.fetchone()[0] > 0
        
        if exists:
            logging.info("Column 'deletion_reason' already exists in posts table")
            cursor.close()
            conn.close()
            return
        
        # Add the column
        logging.info("Adding 'deletion_reason' column to posts table...")
        cursor.execute("""
            ALTER TABLE posts 
            ADD COLUMN deletion_reason TEXT DEFAULT NULL 
            AFTER deleted_at
        """)
        conn.commit()
        
        logging.info("[OK] Successfully added 'deletion_reason' column to posts table")
        
        # Verify the column was added
        cursor.execute("""
            SELECT COUNT(*) 
            FROM information_schema.COLUMNS 
            WHERE TABLE_SCHEMA = %s 
            AND TABLE_NAME = 'posts' 
            AND COLUMN_NAME = 'deletion_reason'
        """, (mysql_config['database'],))
        
        if cursor.fetchone()[0] > 0:
            logging.info("[OK] Column verified successfully")
        else:
            logging.error("[ERROR] Column verification failed")
        
        cursor.close()
        conn.close()
        
        logging.info("Migration completed successfully!")
        
    except Exception as e:
        logging.error(f"Migration failed: {e}", exc_info=True)
        sys.exit(1)

def main(argv=None):
    # fff: this was the body of `if __name__ == "__main__":`
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)  # fff: --help only
    print("=" * 80)
    print("DATABASE MIGRATION: Add deletion_reason column to posts table")
    print("=" * 80)
    print()
    
    response = input("This will modify your database structure. Continue? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("Migration cancelled.")
        sys.exit(0)
    
    print()
    add_deletion_reason_column()
    print()
    print("=" * 80)
    print("You can now delete this script (add_deletion_reason_column.py)")
    print("=" * 80)


if __name__ == "__main__":
    main()
