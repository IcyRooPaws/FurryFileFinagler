#!/usr/bin/env python3
# fff: main(argv=None) accepts the CLI argv but takes no options.
"""
Quick script to check relationship statistics in the database.
Shows how many posts have parents, favorited vs non-favorited parents, etc.
"""



from fff.archive.config import load_config
from fff.archive.db import connect_to_mysql, get_relationship_stats, get_posts_with_non_favorited_parents

def main(argv=None):
    config = load_config()
    conn = connect_to_mysql(config['mysql'])
    
    if not conn:
        print("Failed to connect to database")
        return
    
    try:
        print("="*80)
        print("POST RELATIONSHIPS STATISTICS")
        print("="*80)
        
        stats = get_relationship_stats(conn)
        
        print(f"\nTotal posts with relationship data: {stats['total_with_relationships']:,}")
        print("\nParent Relationships:")
        print(f"  - Posts with parents: {stats['posts_with_parent']:,}")
        print(f"  - Parents that ARE favorited: {stats['posts_with_favorited_parent']:,}")
        print(f"  - Parents that are NOT favorited: {stats['posts_with_non_favorited_parent']:,}")
        print(f"  - Orphaned relationships: {stats['orphaned_relationships']:,}")
        
        print("\nChild Relationships:")
        print(f"  - Posts with children: {stats['posts_with_children']:,}")
        print(f"  - Posts with active children: {stats['posts_with_active_children']:,}")
        
        # Show some examples of posts with non-favorited parents
        if stats['posts_with_non_favorited_parent'] > 0:
            print(f"\n{'='*80}")
            print("SAMPLE: Posts with Non-Favorited Parents (first 10)")
            print("="*80)
            
            examples = get_posts_with_non_favorited_parents(conn, limit=10)
            if examples:
                print("\nThese posts have parents that aren't in our favorites:")
                for post_id, parent_id in examples:
                    print(f"  Post {post_id} → Parent {parent_id} (https://e621.net/posts/{parent_id})")
        
        print("\n" + "="*80)
        
    finally:
        conn.close()

if __name__ == "__main__":
    main()
