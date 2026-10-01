# fff: renamed from test_all_files_view.py (a diagnostic, not a pytest test).
# fff: the hard-coded MySQL host/user/password are removed: connection settings and schema come
# fff: from Config() (sorter view, prod or test_); the artist/fav folder filters use the
# fff: configured artist_directory / fav_directory instead of hard-coded P:/IPL paths.
"""
Test script to diagnose why all_files view query returns 0 results.
"""
import mysql.connector
from mysql.connector import Error
import os
import sys

from fff.sorter.config import Config

def test_view():
    """Test the all_files view and query."""
    
    # Database connection details from config
    # fff: server settings and schema from the sorter config (environment-aware); the
    # original hard-coded the host, user and password here.
    sorter_config = Config()
    config = sorter_config.get_mysql_config()
    schema = config['database']
    
    try:
        print("=" * 80)
        print("TESTING all_files VIEW")
        print("=" * 80)
        
        conn = mysql.connector.connect(**config)
        cursor = conn.cursor()
        
        # Test 1: Check if view exists
        print("\n[TEST 1] Checking if all_files view exists...")
        cursor.execute("""
            SELECT TABLE_NAME, TABLE_TYPE 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = %s
            AND TABLE_NAME = 'all_files'
        """, (schema,))
        view_info = cursor.fetchone()
        if view_info:
            print(f"✓ View exists: {view_info[0]} ({view_info[1]})")
        else:
            print("✗ View does NOT exist!")
            return
        
        # Test 2: Count total rows in view
        print("\n[TEST 2] Counting total rows in all_files view...")
        try:
            cursor.execute("SELECT COUNT(*) FROM all_files")
            total = cursor.fetchone()[0]
            print(f"✓ Total rows in all_files view: {total:,}")
        except Error as e:
            print(f"✗ Error querying view: {e}")
            print("This likely means columns are missing in underlying tables!")
            
            # Check which columns are missing
            print("\n[DIAGNOSIS] Checking column existence in archive tables...")
            archive_tables = [
                'archive_explicit_images', 'archive_explicit_videos', 'archive_explicit_gifs',
                'archive_sfw_images', 'archive_sfw_videos', 'archive_sfw_gifs'
            ]
            for table in archive_tables:
                cursor.execute(f"""
                    SELECT COUNT(*) 
                    FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_SCHEMA = '{schema}' 
                    AND TABLE_NAME = '{table}'
                    AND COLUMN_NAME IN ('last_artist_check', 'artist_matched')
                """)
                col_count = cursor.fetchone()[0]
                status = "✓" if col_count == 2 else "✗"
                print(f"  {status} {table}: {col_count}/2 artist columns exist")
            return
        
        # Test 3: Get sample data
        print("\n[TEST 3] Getting sample rows from view...")
        cursor.execute("SELECT file_name, table_name, last_artist_check, artist_matched FROM all_files LIMIT 5")
        rows = cursor.fetchall()
        print(f"Sample data (first 5 rows):")
        for row in rows:
            print(f"  - {row[0][:50]:50s} | table: {row[1]:20s} | last_check: {row[2]} | matched: {row[3]}")
        
        # Test 4: Check artist_matched column statistics
        print("\n[TEST 4] Checking artist_matched column statistics...")
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(artist_matched) as has_artist_matched,
                SUM(CASE WHEN artist_matched IS NULL THEN 1 ELSE 0 END) as null_artist_matched,
                SUM(CASE WHEN artist_matched = '' THEN 1 ELSE 0 END) as empty_artist_matched
            FROM all_files
        """)
        stats = cursor.fetchone()
        print(f"  Total rows: {stats[0]:,}")
        print(f"  Has artist_matched value: {stats[1]:,}")
        print(f"  artist_matched IS NULL: {stats[2]:,}")
        print(f"  artist_matched = '': {stats[3]:,}")
        print(f"  Eligible (NULL or ''): {stats[2] + stats[3]:,}")
        
        # Test 5: Check last_artist_check column statistics
        print("\n[TEST 5] Checking last_artist_check column statistics...")
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(last_artist_check) as has_last_check,
                SUM(CASE WHEN last_artist_check IS NULL THEN 1 ELSE 0 END) as null_last_check,
                SUM(CASE WHEN last_artist_check < DATE_SUB(NOW(), INTERVAL 7 DAY) THEN 1 ELSE 0 END) as old_checks
            FROM all_files
        """)
        stats = cursor.fetchone()
        print(f"  Total rows: {stats[0]:,}")
        print(f"  Has last_artist_check value: {stats[1]:,}")
        print(f"  last_artist_check IS NULL: {stats[2]:,}")
        print(f"  Last checked > 7 days ago: {stats[3]:,}")
        print(f"  Eligible (NULL or old): {stats[2] + stats[3]:,}")
        
        # Test 6: Simulate the actual query
        print("\n[TEST 6] Testing actual query with WHERE clauses...")
        # fff: the configured folders (were hard-coded P:/IPL/- Fav/Artists and P:/IPL/- Fav)
        artist_dir = os.path.abspath(sorter_config.artist_directory).replace('\\', '/')
        fav_dir = os.path.abspath(sorter_config.fav_directory).replace('\\', '/')
        cache_days = 7
        
        print(f"  Artist directory filter: {artist_dir}")
        print(f"  Fav directory filter: {fav_dir}")
        print(f"  Cache days: {cache_days}")
        
        query = """
            SELECT file_name, file_path,
                   COALESCE(perceptual_hash_dhash, perceptual_hash_phash, perceptual_hash_average, perceptual_hash_whash, md5_hash) as hash_value,
                   table_name
            FROM all_files
            WHERE file_path NOT LIKE CONCAT(%s, '%%')
              AND file_path NOT LIKE CONCAT(%s, '%%')
              AND (artist_matched IS NULL OR artist_matched = '')
              AND (
                last_artist_check IS NULL
                OR last_artist_check < DATE_SUB(NOW(), INTERVAL %s DAY)
              )
            LIMIT 10
        """
        
        cursor.execute(query, [artist_dir, fav_dir, cache_days])
        results = cursor.fetchall()
        print(f"  Query returned {len(results)} rows")
        
        if results:
            print(f"  Sample results:")
            for row in results[:5]:
                print(f"    - {row[0][:50]:50s} | table: {row[3]:20s}")
        else:
            print("  ✗ Query returned 0 rows!")
            
            # Debug: Check each WHERE condition separately
            print("\n[DIAGNOSIS] Testing each WHERE condition separately...")
            
            # Test path filters
            cursor.execute(f"SELECT COUNT(*) FROM all_files WHERE file_path NOT LIKE CONCAT(%s, '%%')", [artist_dir])
            count1 = cursor.fetchone()[0]
            print(f"  Excluding artist dir: {count1:,} rows")
            
            cursor.execute(f"SELECT COUNT(*) FROM all_files WHERE file_path NOT LIKE CONCAT(%s, '%%') AND file_path NOT LIKE CONCAT(%s, '%%')", [artist_dir, fav_dir])
            count2 = cursor.fetchone()[0]
            print(f"  Excluding both artist & fav dirs: {count2:,} rows")
            
            cursor.execute("SELECT COUNT(*) FROM all_files WHERE (artist_matched IS NULL OR artist_matched = '')")
            count3 = cursor.fetchone()[0]
            print(f"  artist_matched filter only: {count3:,} rows")
            
            cursor.execute("SELECT COUNT(*) FROM all_files WHERE (last_artist_check IS NULL OR last_artist_check < DATE_SUB(NOW(), INTERVAL %s DAY))", [cache_days])
            count4 = cursor.fetchone()[0]
            print(f"  last_artist_check filter only: {count4:,} rows")
            
            # Combined
            cursor.execute("""
                SELECT COUNT(*) FROM all_files 
                WHERE file_path NOT LIKE CONCAT(%s, '%%')
                  AND file_path NOT LIKE CONCAT(%s, '%%')
                  AND (artist_matched IS NULL OR artist_matched = '')
                  AND (last_artist_check IS NULL OR last_artist_check < DATE_SUB(NOW(), INTERVAL %s DAY))
            """, [artist_dir, fav_dir, cache_days])
            count_combined = cursor.fetchone()[0]
            print(f"  ALL filters combined: {count_combined:,} rows")
            
            if count_combined == 0:
                print("\n  Possible reasons:")
                print("  1. All files have been checked within the last 7 days")
                print("  2. All eligible files are in artist or fav directories")
                print("  3. All files have artist_matched values set")
        
        # Test 7: Check path format in database
        print("\n[TEST 7] Checking file_path format in database...")
        cursor.execute("SELECT DISTINCT SUBSTRING(file_path, 1, 20) as path_prefix FROM all_files LIMIT 10")
        path_samples = cursor.fetchall()
        print("  Sample path prefixes from database:")
        for path in path_samples:
            print(f"    {path[0]}")
        
        print("\n" + "=" * 80)
        print("TEST COMPLETE")
        print("=" * 80)
        
        cursor.close()
        conn.close()
        
    except Error as e:
        print(f"\n✗ Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def main(argv=None):  # fff: entry point (no options)
    test_view()


if __name__ == "__main__":
    main()
