#!/usr/bin/env python3
# fff: implemented in e621 archive/scripts/database/check_deletion_timestamp_accuracy.py.
# fff: main(argv) parses an option-less argparse parser so --help works; the original took no arguments.
"""
Diagnostic script to check how many deleted posts have potentially inaccurate
deletion timestamps (from API) vs accurate timestamps (from HTML page scraping).

This script checks:
1. Total deleted posts
2. Posts with deletion reasons (likely have been backfilled)
3. Posts without deletion reasons (need backfill)
4. Compares deleted_at with updated_at to identify API-sourced timestamps
"""

import argparse  # fff: for main(argv)
import sys
import logging
from pathlib import Path


from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql

def check_deletion_timestamps(conn):
    """Check deletion timestamp accuracy across all deleted posts."""
    cursor = conn.cursor()
    
    try:
        # 1. Total deleted posts
        cursor.execute("SELECT COUNT(*) FROM posts WHERE deleted = 1")
        total_deleted = cursor.fetchone()[0]
        print(f"\n{'='*80}")
        print(f"DELETION TIMESTAMP ACCURACY REPORT")
        print(f"{'='*80}\n")
        print(f"Total deleted posts: {total_deleted}")
        
        if total_deleted == 0:
            print("No deleted posts found!")
            return
        
        # 2. Posts with deletion reasons (have been scraped)
        cursor.execute("""
            SELECT COUNT(*) FROM posts 
            WHERE deleted = 1 
            AND deletion_reason IS NOT NULL 
            AND deletion_reason != ''
        """)
        with_reasons = cursor.fetchone()[0]
        print(f"Posts with deletion reasons: {with_reasons} ({with_reasons*100//total_deleted}%)")
        
        # 3. Posts without deletion reasons (need scraping)
        without_reasons = total_deleted - with_reasons
        print(f"Posts WITHOUT deletion reasons: {without_reasons} ({without_reasons*100//total_deleted}%)")
        print(f"  → These need to be backfilled with: python scripts/database/backfill_deletion_reasons.py")
        
        # 4. Posts where deleted_at might be from API (not accurate)
        # These are posts where deleted_at equals updated_at (strong indicator of API sourcing)
        cursor.execute("""
            SELECT COUNT(*) FROM posts 
            WHERE deleted = 1 
            AND deleted_at IS NOT NULL
            AND updated_at IS NOT NULL
            AND deleted_at = updated_at
        """)
        api_timestamps = cursor.fetchone()[0]
        print(f"\n{'='*80}")
        print(f"TIMESTAMP SOURCE ANALYSIS")
        print(f"{'='*80}\n")
        print(f"Posts with deleted_at = updated_at: {api_timestamps} ({api_timestamps*100//total_deleted}%)")
        print(f"  → These likely have API timestamps (less accurate)")
        
        # 5. Posts that should have accurate timestamps from HTML scraping
        cursor.execute("""
            SELECT COUNT(*) FROM posts 
            WHERE deleted = 1 
            AND deleted_at IS NOT NULL
            AND updated_at IS NOT NULL
            AND deleted_at != updated_at
        """)
        scraped_timestamps = cursor.fetchone()[0]
        print(f"Posts with deleted_at != updated_at: {scraped_timestamps} ({scraped_timestamps*100//total_deleted if total_deleted > 0 else 0}%)")
        print(f"  → These likely have accurate timestamps from HTML scraping")
        
        # 6. Posts with NULL deleted_at
        cursor.execute("""
            SELECT COUNT(*) FROM posts 
            WHERE deleted = 1 
            AND deleted_at IS NULL
        """)
        null_timestamps = cursor.fetchone()[0]
        if null_timestamps > 0:
            print(f"Posts with NULL deleted_at: {null_timestamps}")
        
        # 7. Breakdown by deletion reason status and timestamp source
        print(f"\n{'='*80}")
        print(f"DETAILED BREAKDOWN")
        print(f"{'='*80}\n")
        
        cursor.execute("""
            SELECT 
                CASE 
                    WHEN deletion_reason IS NULL OR deletion_reason = '' THEN 'No reason'
                    ELSE 'Has reason'
                END as reason_status,
                CASE
                    WHEN deleted_at IS NULL THEN 'NULL'
                    WHEN updated_at IS NULL THEN 'deleted_at only'
                    WHEN deleted_at = updated_at THEN 'API timestamp'
                    ELSE 'HTML timestamp'
                END as timestamp_type,
                COUNT(*) as count
            FROM posts 
            WHERE deleted = 1
            GROUP BY reason_status, timestamp_type
            ORDER BY reason_status, timestamp_type
        """)
        
        results = cursor.fetchall()
        print(f"{'Deletion Reason':<20} {'Timestamp Type':<20} {'Count':<10}")
        print(f"{'-'*20} {'-'*20} {'-'*10}")
        for row in results:
            print(f"{row[0]:<20} {row[1]:<20} {row[2]:<10}")
        
        # 8. Recommendations
        print(f"\n{'='*80}")
        print(f"RECOMMENDATIONS")
        print(f"{'='*80}\n")
        
        if without_reasons > 0:
            print(f"1. Run backfill for {without_reasons} posts without deletion reasons:")
            print(f"   python scripts/database/backfill_deletion_reasons.py")
        
        if api_timestamps > 0:
            print(f"\n2. {api_timestamps} posts likely have inaccurate timestamps from the API.")
            print(f"   To get accurate timestamps from HTML pages, run with --force:")
            print(f"   python scripts/database/backfill_deletion_reasons.py --force")
            print(f"   ")
            print(f"   WARNING: This will re-scrape ALL {total_deleted} deleted posts!")
            print(f"   At 1 request/second, this will take approximately {total_deleted//60} minutes.")
            print(f"   ")
            print(f"   To test with a smaller batch first:")
            print(f"   python scripts/database/backfill_deletion_reasons.py --force --max-posts 10")
        
        if api_timestamps == 0 and without_reasons == 0:
            print("✓ All deleted posts appear to have been properly backfilled!")
            print("  Deletion timestamps should be accurate.")
        
        print()
        
    finally:
        cursor.close()

def main(argv=None):
    """Main entry point."""
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)  # fff: --help only
    print("Loading configuration...")
    config = load_config()
    mysql_config = config.get('mysql', {})
    
    print("Connecting to database...")
    conn = connect_to_mysql(mysql_config)
    if conn is None:
        print("ERROR: Failed to connect to database")
        sys.exit(1)
    
    try:
        check_deletion_timestamps(conn)
    finally:
        conn.close()

if __name__ == "__main__":
    main()
