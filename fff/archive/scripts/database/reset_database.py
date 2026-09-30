# fff: implemented in e621 archive/scripts/database/reset_database.py.
# fff: the target database is config mysql.database from the archive view, i.e. the active
# fff: environment's archive schema (prod or test_); prompts and --force/--dry-run unchanged.
"""
Reset Database Script
=====================
DANGER: This script will DROP ALL TABLES in the e621_posts_db database and recreate them.
All data will be permanently lost!

Usage:
    python reset_database.py              # Interactive mode with confirmation
    python reset_database.py --force      # Skip confirmation (use with caution)
    python reset_database.py --dry-run    # Show what would be done without doing it
"""

import sys
import logging
import argparse
from pathlib import Path

from fff.archive import paths as archive_paths

from fff.archive.config import load_config
from fff.archive.logging_setup import setup_logging
from fff.archive.db import connect_to_mysql, create_tables_if_not_exists


def get_all_tables(conn):
    """Get list of all tables in the database."""
    cursor = conn.cursor()
    try:
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        return tables
    finally:
        cursor.close()


def get_table_row_counts(conn, tables):
    """Get row count for each table."""
    counts = {}
    cursor = conn.cursor()
    try:
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            counts[table] = count
    finally:
        cursor.close()
    return counts


def drop_all_tables(conn, tables, dry_run=False):
    """Drop all tables in the database."""
    if not tables:
        logging.info("No tables to drop.")
        return
    
    cursor = conn.cursor()
    try:
        # Disable foreign key checks temporarily
        if not dry_run:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        
        for table in tables:
            if dry_run:
                logging.info(f"[DRY RUN] Would drop table: {table}")
            else:
                logging.info(f"Dropping table: {table}")
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
        
        if not dry_run:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
            conn.commit()
            logging.info(f"Successfully dropped {len(tables)} tables")
        else:
            logging.info(f"[DRY RUN] Would drop {len(tables)} tables")
    except Exception as e:
        logging.error(f"Error dropping tables: {e}")
        if not dry_run:
            conn.rollback()
        raise
    finally:
        cursor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Reset the e621_posts_db database by dropping and recreating all tables',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
WARNING: This operation is IRREVERSIBLE and will DELETE ALL DATA!

Examples:
  python reset_database.py              # Interactive mode with confirmation
  python reset_database.py --force      # Skip confirmation (dangerous!)
  python reset_database.py --dry-run    # Show what would be done
        """
    )
    parser.add_argument('--force', action='store_true',
                       help='Skip confirmation prompt (USE WITH EXTREME CAUTION)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be done without actually doing it')
    args = parser.parse_args(argv)

    # Load configuration
    config = load_config()
    mysql_config = config.get("mysql", {})
    log_config = config.get("log", {})
    
    log_dir = log_config.get("directory", str(archive_paths.LOG_DIR))  # fff: was ./logs
    setup_logging(log_dir)
    
    logging.info("=" * 80)
    logging.info("DATABASE RESET SCRIPT")
    logging.info("=" * 80)
    
    if args.dry_run:
        logging.info("DRY RUN MODE - No changes will be made")
    
    # Connect to database
    conn = connect_to_mysql(mysql_config)
    if conn is None:
        logging.error("Failed to connect to database")
        sys.exit(1)
    
    database_name = mysql_config.get("database", archive_paths.db_names()["archive"])  # fff: prod or test_
    logging.info(f"Connected to database: {database_name}")
    
    # Get current tables and their row counts
    tables = get_all_tables(conn)
    
    if not tables:
        logging.info("Database is empty (no tables exist)")
        if not args.dry_run:
            logging.info("Creating tables...")
            create_tables_if_not_exists(conn)
            logging.info("Tables created successfully")
        else:
            logging.info("[DRY RUN] Would create tables")
        conn.close()
        sys.exit(0)
    
    logging.info(f"\nFound {len(tables)} tables in database:")
    
    # Get row counts
    table_counts = get_table_row_counts(conn, tables)
    total_rows = sum(table_counts.values())
    
    for table in sorted(tables):
        count = table_counts.get(table, 0)
        logging.info(f"  - {table}: {count:,} rows")
    
    logging.info(f"\nTotal rows across all tables: {total_rows:,}")
    
    # Confirmation prompt (unless --force or --dry-run)
    if not args.force and not args.dry_run:
        logging.warning("\n" + "!" * 80)
        logging.warning("WARNING: This will PERMANENTLY DELETE all data in the database!")
        logging.warning("!" * 80)
        print("\n" + "=" * 80)
        print("DATABASE RESET CONFIRMATION")
        print("=" * 80)
        print(f"\nDatabase: {database_name}")
        print(f"Tables to drop: {len(tables)}")
        print(f"Total rows to delete: {total_rows:,}")
        print("\nThis action CANNOT be undone!")
        print("\nType 'DELETE ALL DATA' (in ALL CAPS) to confirm:")
        
        confirmation = input("> ").strip()
        
        if confirmation != "DELETE ALL DATA":
            logging.info("Reset cancelled by user")
            print("\nReset cancelled. No changes made.")
            conn.close()
            sys.exit(0)
        
        print("\nConfirmation received. Proceeding with reset...")
        logging.info("User confirmed database reset")
    
    # Drop all tables
    logging.info("\nDropping all tables...")
    try:
        drop_all_tables(conn, tables, dry_run=args.dry_run)
    except Exception as e:
        logging.error(f"Failed to drop tables: {e}")
        conn.close()
        sys.exit(1)
    
    # Recreate tables
    if not args.dry_run:
        logging.info("\nRecreating tables...")
        try:
            create_tables_if_not_exists(conn)
            logging.info("Tables recreated successfully")
        except Exception as e:
            logging.error(f"Failed to recreate tables: {e}")
            conn.close()
            sys.exit(1)
    else:
        logging.info("[DRY RUN] Would recreate tables")
    
    # Verify
    if not args.dry_run:
        new_tables = get_all_tables(conn)
        logging.info(f"\nDatabase reset complete. Created {len(new_tables)} empty tables:")
        for table in sorted(new_tables):
            logging.info(f"  - {table}")
    
    conn.close()
    
    if args.dry_run:
        logging.info("\n[DRY RUN] No changes were made to the database")
    else:
        logging.info("\nDatabase reset completed successfully!")
        logging.info("The database is now empty and ready for fresh data ingestion.")


if __name__ == "__main__":
    main()
