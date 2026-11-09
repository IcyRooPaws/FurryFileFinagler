#!/usr/bin/env python3
# fff: run as 'fff fluffle verify-db'.
"""
Quick script to verify data in the new platform-specific tables.
"""

import yaml  # noqa: F401
from fff.common import config as fff_config
from fff.common import db
from mysql.connector import Error


def load_config():
    """Load configuration. fff: the reverse search config view (was ../config.yaml)."""
    return fff_config.legacy_view("fluffle")


def verify_database(argv=None):  # fff: argv accepted for the CLI
    """Verify data in database tables."""
    config = load_config()
    db_config = config.get('database', {})
    
    try:
        connection = db.connect_with(db_config)

        if connection.is_connected():
            cursor = connection.cursor(dictionary=True)
            
            print("=" * 80)
            print("DATABASE VERIFICATION")
            print("=" * 80)
            
            # Check images table
            cursor.execute("SELECT * FROM images ORDER BY search_timestamp DESC LIMIT 5")
            images = cursor.fetchall()
            
            print("\n[FOLDER] IMAGES TABLE:")
            print(f"Total images: {len(images)}")
            for img in images:
                print(f"\n  • {img['filename']}")
                print(f"    ID: {img['id']}")
                print(f"    Total matches: {img['total_matches']}")
                print(f"    Exact matches: {img['exact_matches']}")
                print(f"    File size: {img['file_size']:,} bytes" if img['file_size'] else "    File size: N/A")
                if img['width'] and img['height']:
                    print(f"    Dimensions: {img['width']}x{img['height']}")
            
            # Check platform-specific tables
            platforms = [
                ('e621_links', 'e621'),
                ('furaffinity_links', 'FurAffinity'),
                ('weasyl_links', 'Weasyl'),
                ('twitter_links', 'Twitter'),
                ('bluesky_links', 'Bluesky'),
                ('deviantart_links', 'DeviantArt'),
                ('other_links', 'Other')
            ]
            
            print("\n\n[LINK] PLATFORM-SPECIFIC LINKS:")
            
            for table_name, platform_display in platforms:
                cursor.execute(f"""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN match_quality = 'exact' THEN 1 ELSE 0 END) as exact
                    FROM {table_name}
                """)
                result = cursor.fetchone()
                total = result['total']
                exact = result['exact'] or 0
                
                if total > 0:
                    print(f"\n  {platform_display}:")
                    print(f"    Total links: {total}")
                    print(f"    Exact matches: {exact}")
                    
                    # Show sample links
                    cursor.execute(f"""
                        SELECT post_url, match_quality, author_name, distance
                        FROM {table_name}
                        ORDER BY match_quality = 'exact' DESC, distance DESC
                        LIMIT 3
                    """)
                    links = cursor.fetchall()
                    
                    for link in links:
                        quality_emoji = "[OK]" if link['match_quality'] == 'exact' else "~"
                        print(f"      {quality_emoji} [{link['match_quality']}] {link['post_url']}")
                        if link['author_name']:
                            print(f"        By: {link['author_name']} (distance: {link['distance']:.4f})")
            
            # Show exact matches summary
            print("\n\n[STAR] EXACT MATCHES SUMMARY:")
            
            for table_name, platform_display in platforms:
                cursor.execute(f"""
                    SELECT i.filename, l.post_url, l.author_name
                    FROM {table_name} l
                    JOIN images i ON l.image_id = i.id
                    WHERE l.match_quality = 'exact'
                    ORDER BY l.distance DESC
                    LIMIT 5
                """)
                exact_matches = cursor.fetchall()
                
                if exact_matches:
                    print(f"\n  {platform_display} Exact Matches:")
                    for match in exact_matches:
                        print(f"    • {match['filename']}")
                        print(f"      → {match['post_url']}")
                        if match['author_name']:
                            print(f"      By: {match['author_name']}")
            
            cursor.close()
            connection.close()
            
            print("\n" + "=" * 80)
            print("[OK] Verification complete!")
            print("=" * 80)
    
    except Error as e:
        print(f"Database error: {e}")


if __name__ == "__main__":
    verify_database()
